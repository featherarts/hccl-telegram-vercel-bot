"""Vercel serverless Telegram webhook for HCCL Rankings Bot.

This file is loaded by Vercel as /api/telegram.
It receives Telegram webhook POST updates, reads latest rankings from Supabase,
and sends replies through the Telegram Bot API.
"""

from __future__ import annotations

import json
import ast
import html
import os
import re
import sys
import traceback
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Ensure root-level modules are importable from /api on Vercel and locally.
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from hccl_bot_data import (  # noqa: E402
    BENCHMARK_LABELS,
    HCCLBotError,
    HCCLSupabaseStore,
    as_bool,
    as_number,
    format_player_line,
    format_rank,
    format_rating,
    format_signed,
    parse_category,
    VALID_CATEGORIES,
)


BOT_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
MAX_MESSAGE_LENGTH = 3900


HELP_TEXT = """
🏏 <b>HCCL Rankings Bot</b>

<b>Main commands</b>
🏏 /topbat — batting rankings
🎯 /topbowl — bowling rankings
👑 /topall — all-rounder rankings

<b>Player & team</b>
👤 /player Hasitha — full profile card
⚡ /card Hasitha — short mobile card
🏟 /team DRAGONS — team rankings

<b>Weekly movement</b>
📈 /movers — biggest climbers
📉 /fallers — biggest fallers
🔥 /gains — biggest rating gains
🆕 /newentries — new entries

<b>More</b>
🗞 /report — weekly report
📊 /benchmarks — rating benchmarks
🗓 /weeks — saved ranking weeks

Tip: add a number, for example /topbat 5
""".strip()


COMMAND_DESCRIPTIONS = {
    "start": "Show welcome message",
    "help": "Show command list",
    "topbat": "Top batting rankings",
    "topbowl": "Top bowling rankings",
    "topall": "Top all-rounder rankings",
    "player": "Full player profile card, e.g. /player Hasitha",
    "profile": "Full player profile card, e.g. /profile Hasitha",
    "card": "Compact player profile card, e.g. /card Hasitha",
    "profiledebug": "Debug profile data, e.g. /profiledebug Hasitha",
    "team": "Team rankings, e.g. /team DRAGONS",
    "movers": "Top rank climbers",
    "fallers": "Top rank fallers",
    "gains": "Top rating gains",
    "newentries": "New ranking entries",
    "report": "Weekly ranking report",
    "benchmarks": "Current rating benchmarks",
    "weeks": "Saved ranking weeks",
}


def get_env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def get_token() -> str:
    token = get_env(BOT_TOKEN_ENV)
    if not token:
        raise HCCLBotError(f"Missing environment variable: {BOT_TOKEN_ENV}")
    return token


def telegram_api(method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Call Telegram Bot API using only Python stdlib."""
    token = get_token()
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise HCCLBotError(f"Telegram API error {exc.code}: {error_body}") from exc


def allowed_chat_ids() -> Optional[set[int]]:
    raw = get_env("ALLOWED_CHAT_IDS")
    if not raw:
        return None
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            # Ignore invalid values rather than failing all commands.
            continue
    return ids or None


def is_chat_allowed(chat_id: int) -> bool:
    allowed = allowed_chat_ids()
    return allowed is None or chat_id in allowed


def split_message(text: str) -> List[str]:
    text = (text or "").strip() or "No data found."
    if len(text) <= MAX_MESSAGE_LENGTH:
        return [text]

    chunks: List[str] = []
    current = ""
    for line in text.splitlines():
        if len(current) + len(line) + 1 > MAX_MESSAGE_LENGTH:
            if current:
                chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


def send_message(chat_id: int, text: str, message_thread_id: Optional[int] = None) -> None:
    for chunk in split_message(text):
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if message_thread_id is not None:
            payload["message_thread_id"] = message_thread_id
        telegram_api("sendMessage", payload)


def default_limit() -> int:
    try:
        return max(1, min(25, int(get_env("DEFAULT_TOP_LIMIT", "10"))))
    except Exception:
        return 10


def parse_limit(args: List[str], fallback: int) -> int:
    if not args:
        return fallback
    try:
        return max(1, min(25, int(args[0])))
    except Exception:
        return fallback


def store() -> HCCLSupabaseStore:
    return HCCLSupabaseStore()


def h(value: Any) -> str:
    """HTML-escape dynamic text for Telegram."""
    if value is None or value == "":
        return "—"
    return html.escape(str(value), quote=False)


def bold(value: Any) -> str:
    return f"<b>{h(value)}</b>"


def italic(value: Any) -> str:
    return f"<i>{h(value)}</i>"


CATEGORY_ICONS = {
    "Batting": "🏏",
    "Bowling": "🎯",
    "All-Rounder": "👑",
}

CATEGORY_SHORT = {
    "Batting": "Bat",
    "Bowling": "Bowl",
    "All-Rounder": "AR",
}


def display_movement(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text in {"—", "-"}:
        return "→"
    return text


def display_change(value: Any) -> str:
    if value is None or value == "":
        return "—"
    try:
        number = float(value)
        if number == 0:
            return "±0"
        if number.is_integer():
            return f"{int(number):+d}"
        return f"{number:+.1f}"
    except Exception:
        text = str(value).strip()
        return text if text else "—"


def medal(index: int) -> str:
    return {1: "🥇", 2: "🥈", 3: "🥉"}.get(index, f"{index})")


def snapshot_header(snapshot: Any, title: str, subtitle: str = "") -> str:
    official = "Official only" if snapshot.official_only else "All calculated players"
    lines = [bold(title), f"🗓 {h(snapshot.week_label)} | {h(snapshot.snapshot_date)}"]
    if subtitle:
        lines.append(h(subtitle))
    lines.append(f"📌 {h(official)}")
    return "\n".join(lines)


def format_top(category: str, limit: int) -> str:
    snapshot, rows = store().top(category, limit=limit)
    if not rows:
        return f"No {h(category)} rankings found."

    icon = CATEGORY_ICONS.get(category, "🏏")
    lines = [snapshot_header(snapshot, f"{icon} HCCL {category} Top {limit}"), ""]
    for idx, row in enumerate(rows, start=1):
        rank = format_rank(row.get("rank"))
        player = row.get("player") or "Unknown"
        team = row.get("team") or "—"
        rating = format_rating(row.get("rating"))
        movement = display_movement(row.get("movement"))
        change = display_change(row.get("rating_change"))
        lines.append(f"{medal(idx)} {bold(player)} {italic(f'({team})')}")
        lines.append(f"   {h(rank)} • {h(rating)} pts • {h(movement)} • {h(change)}")
        if idx != len(rows):
            lines.append("")
    return "\n".join(lines)


def command_start(args: List[str]) -> str:
    return HELP_TEXT


def command_topbat(args: List[str]) -> str:
    return format_top("Batting", parse_limit(args, default_limit()))


def command_topbowl(args: List[str]) -> str:
    return format_top("Bowling", parse_limit(args, default_limit()))


def command_topall(args: List[str]) -> str:
    return format_top("All-Rounder", parse_limit(args, default_limit()))


def _rank_row_by_category(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(row.get("category") or ""): row for row in rows}


def _row_line(icon: str, label: str, row: Optional[Dict[str, Any]], rating_key: str, details: Dict[str, Any]) -> List[str]:
    if row:
        rank = format_rank(row.get("rank"))
        rating = format_rating(row.get("rating"))
        movement = display_movement(row.get("movement"))
        change = display_change(row.get("rating_change"))
        status = row.get("status") or "—"
        return [
            f"{icon} {bold(label)}",
            f"   {h(rank)} • {h(rating)} pts • {h(movement)} • {h(change)}",
            f"   {h(status)}",
        ]
    rating = details.get(rating_key)
    if rating is not None and rating != "":
        return [f"{icon} {bold(label)}", f"   — • {format_rating(rating)} pts • → • —", "   Provisional"]
    return [f"{icon} {bold(label)}", "   No rating yet"]


def _profile_badges(rows_by_category: Dict[str, Dict[str, Any]], details: Dict[str, Any]) -> str:
    qualified = []
    if as_bool(detail_value(details, "batting_qualified", "Batting Qualified")):
        qualified.append("🏏 Bat")
    if as_bool(detail_value(details, "bowling_qualified", "Bowling Qualified")):
        qualified.append("🎯 Bowl")
    if as_bool(detail_value(details, "all_rounder_qualified", "All-Rounder Qualified", "All Rounder Qualified")):
        qualified.append("👑 AR")

    if len(qualified) == 3:
        return "🟢 Official all-rounder profile"
    if qualified:
        return "🟢 Qualified: " + " | ".join(qualified)
    return "🟡 Provisional / building profile"


def _best_skill(rows_by_category: Dict[str, Dict[str, Any]]) -> str:
    best_label = "—"
    best_rank = 10**9
    for category, row in rows_by_category.items():
        try:
            rank = int(row.get("rank") or 0)
        except Exception:
            rank = 0
        if rank and rank < best_rank:
            best_rank = rank
            best_label = category
    return best_label


def _detail_key_variants(key: Any) -> List[str]:
    """Return forgiving key variants for JSON detail dictionaries."""
    raw = str(key or "").strip()
    compact = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
    nospace = re.sub(r"[^a-z0-9]+", "", raw.lower())
    variants = [raw, raw.lower(), compact, nospace]
    # Common hand-written aliases used across older dashboard versions.
    alias_map = {
        "allrounderqualified": "all_rounder_qualified",
        "allrounderrating": "all_rounder_rating",
        "battingrecentform": "batting_recent_form",
        "bowlingrecentform": "bowling_recent_form",
        "battingcareerscore": "batting_career_score",
        "bowlingcareerscore": "bowling_career_score",
        "achievementscorebatting": "achievement_score_batting",
        "achievementscorebowling": "achievement_score_bowling",
        "experiencescore": "experience_score",
        "playerid": "player_id",
    }
    if nospace in alias_map:
        variants.append(alias_map[nospace])
    return [v for v in dict.fromkeys(variants) if v]


def _flatten_detail_dict(value: Any) -> Dict[str, Any]:
    """Parse and flatten Supabase jsonb detail data from multiple possible shapes.

    Some snapshots returned `data` as jsonb dicts, some as JSON strings, and
    a few older saves can come back looking like a Python dict string. This
    function accepts all of those and returns a normalized dict with forgiving
    key aliases, so profile cards can always find innings/runs/wickets/etc.
    """
    if value is None or value == "":
        return {}

    parsed = value
    if isinstance(value, str):
        text = value.strip()
        try:
            parsed = json.loads(text)
        except Exception:
            try:
                parsed = ast.literal_eval(text)
            except Exception:
                parsed = {}

    if not isinstance(parsed, dict):
        return {}

    # Some data can be nested as {"data": {...}} or {"details": {...}}.
    merged: Dict[str, Any] = {}
    for k, v in parsed.items():
        if isinstance(v, dict) and str(k).strip().lower() in {"data", "details", "rating_details"}:
            merged.update(v)
        else:
            merged[k] = v

    normalized: Dict[str, Any] = {}
    for k, v in merged.items():
        for variant in _detail_key_variants(k):
            normalized.setdefault(variant, v)
    return normalized


def _safe_detail_data(detail_row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return all available player detail data safely.

    This merges the top-level Supabase row (player_id/player/team) with the
    saved JSON `data` payload and normalizes key names. It fixes profile cards
    that showed ranks but blank career values because only one JSON shape was
    supported.
    """
    if not detail_row:
        return {}

    details: Dict[str, Any] = {}
    # Include top-level values too.
    details.update(_flatten_detail_dict({k: v for k, v in detail_row.items() if k != "data"}))
    # Then include the real saved rating details.
    details.update(_flatten_detail_dict(detail_row.get("data")))
    return details


def detail_value(details: Dict[str, Any], *keys: str) -> Any:
    """Get the first non-empty detail value, supporting old/new key names."""
    for key in keys:
        for variant in _detail_key_variants(key):
            value = details.get(variant)
            if value is not None and value != "":
                return value
    return None


def format_detail(details: Dict[str, Any], *keys: str) -> str:
    return format_rating(detail_value(details, *keys))


def _player_team_id(player_name: str, team: str, player_id: Any) -> List[str]:
    return [
        bold(player_name),
        f"🏟 <b>Team:</b> {h(team)}",
        f"🆔 <b>ID:</b> {h(player_id)}",
    ]


def _movement_summary(rows_by_category: Dict[str, Dict[str, Any]]) -> str:
    bits: List[str] = []
    for category in ["Batting", "Bowling", "All-Rounder"]:
        row = rows_by_category.get(category)
        if row:
            short = CATEGORY_SHORT.get(category, category)
            bits.append(f"{short} {display_movement(row.get('movement'))} ({display_change(row.get('rating_change'))})")
    return " | ".join(bits) if bits else "—"


def format_profile_card(query: str, compact: bool = False) -> str:
    snapshot, player_name, rows, detail_row, suggestions = store().player_profile(query)
    if not player_name:
        suggestion_text = ""
        if suggestions:
            suggestion_text = "\n\n<b>Possible matches</b>\n" + "\n".join(f"• {h(name)}" for name in suggestions)
        return f"Player not found: {h(query)}{suggestion_text}"

    details = _safe_detail_data(detail_row)
    rows_by_category = _rank_row_by_category(rows)
    team = (detail_row or {}).get("team") or (rows[0].get("team") if rows else "—")
    player_id = (detail_row or {}).get("player_id") or detail_value(details, "player_id", "ID", "Player ID") or "—"

    if compact:
        lines = [f"⚡ {bold(player_name)} {italic(f'({team})')}", f"🗓 {h(snapshot.week_label)}", ""]
        for category in ["Batting", "Bowling", "All-Rounder"]:
            row = rows_by_category.get(category)
            icon = CATEGORY_ICONS.get(category, "•")
            short = CATEGORY_SHORT.get(category, category)
            if row:
                lines.append(
                    f"{icon} <b>{h(short)}</b> {h(format_rank(row.get('rank')))} | "
                    f"{h(format_rating(row.get('rating')))} pts | {h(display_movement(row.get('movement')))}"
                )
        runs = format_rating(detail_value(details, "runs", "RUNS", "Runs"))
        wickets = format_rating(detail_value(details, "wickets", "WICKETS", "Wickets"))
        bat_form = format_rating(detail_value(details, "batting_recent_form", "Batting Recent Form"))
        bowl_form = format_rating(detail_value(details, "bowling_recent_form", "Bowling Recent Form"))
        lines.extend(["", f"📌 Runs: {h(runs)} | Wkts: {h(wickets)}", f"🔥 Form: Bat {h(bat_form)} | Bowl {h(bowl_form)}"])
        return "\n".join(lines)

    lines = [
        "🏏 <b>HCCL PLAYER CARD</b>",
        "",
        *_player_team_id(player_name, team, player_id),
        f"🗓 <b>Week:</b> {h(snapshot.week_label)} | {h(snapshot.snapshot_date)}",
        "",
        "⚡ <b>Status</b>",
        _profile_badges(rows_by_category, details),
        "",
        "📊 <b>Rankings</b>",
    ]

    for category, icon, rating_key in [
        ("Batting", "🏏", "batting_rating"),
        ("Bowling", "🎯", "bowling_rating"),
        ("All-Rounder", "👑", "all_rounder_rating"),
    ]:
        lines.extend(_row_line(icon, category, rows_by_category.get(category), rating_key, details))
        lines.append("")

    innings_val = detail_value(details, "innings", "Innings", "INNINGS")
    runs_val = detail_value(details, "runs", "RUNS", "Runs")
    wickets_val = detail_value(details, "wickets", "WICKETS", "Wickets")
    bat_form = detail_value(details, "batting_recent_form", "Batting Recent Form")
    bowl_form = detail_value(details, "bowling_recent_form", "Bowling Recent Form")

    lines.extend([
        "📌 <b>Career Snapshot</b>",
        f"Innings: {h(format_rating(innings_val))}",
        f"Runs: {h(format_rating(runs_val))}",
        f"Wickets: {h(format_rating(wickets_val))}",
    ])
    if innings_val is None and runs_val is None and wickets_val is None:
        lines.append("⚠️ Career stats are missing in the latest saved snapshot. Re-save rankings from the latest Streamlit dashboard.")

    lines.extend([
        "",
        "🔥 <b>Recent Form</b>",
        f"Batting: {h(format_rating(bat_form))}",
        f"Bowling: {h(format_rating(bowl_form))}",
        "",
        "🧠 <b>Rating Components</b>",
        f"Bat career: {h(format_rating(detail_value(details, 'batting_career_score', 'Batting Career Score')))}",
        f"Bowl career: {h(format_rating(detail_value(details, 'bowling_career_score', 'Bowling Career Score')))}",
        f"Bat achievement: {h(format_rating(detail_value(details, 'achievement_score_batting', 'Achievement Score Batting')))}",
        f"Bowl achievement: {h(format_rating(detail_value(details, 'achievement_score_bowling', 'Achievement Score Bowling')))}",
        f"Experience: {h(format_rating(detail_value(details, 'experience_score', 'Experience Score')))}",
        "",
        "🏆 <b>Quick Read</b>",
        f"Best discipline: {h(_best_skill(rows_by_category))}",
        f"Movement: {h(_movement_summary(rows_by_category))}",
    ])
    return "\n".join(lines)


def command_player(args: List[str]) -> str:
    query = " ".join(args).strip()
    if not query:
        return "Use like this: /player Hasitha"
    return format_profile_card(query, compact=False)


def command_profile(args: List[str]) -> str:
    query = " ".join(args).strip()
    if not query:
        return "Use like this: /profile Hasitha"
    return format_profile_card(query, compact=False)


def command_card(args: List[str]) -> str:
    query = " ".join(args).strip()
    if not query:
        return "Use like this: /card Hasitha"
    return format_profile_card(query, compact=True)


def command_team(args: List[str]) -> str:
    if not args:
        return "Use like this: /team DRAGONS or /team DRAGONS batting"

    team_query = args[0]
    category = parse_category(args[1], default="") if len(args) > 1 else None
    snapshot, team_name, rows, suggestions = store().team_rankings(team_query, category=category, per_category_limit=7)
    if not rows or not team_name:
        suggestion_text = ""
        if suggestions:
            suggestion_text = "\n\n<b>Available teams / possible matches</b>\n" + "\n".join(f"• {h(name)}" for name in suggestions)
        return f"Team not found: {h(team_query)}{suggestion_text}"

    title = f"🏟 HCCL Team Rankings: {team_name}"
    if category:
        title += f" | {category}"
    lines = [snapshot_header(snapshot, title), ""]
    last_category = None
    idx = 0
    for row in rows:
        row_category = row.get("category") or ""
        if row_category != last_category:
            if last_category is not None:
                lines.append("")
            lines.append(f"{CATEGORY_ICONS.get(row_category, '•')} {bold(row_category)}")
            last_category = row_category
            idx = 0
        idx += 1
        lines.append(f"• {bold(row.get('player') or 'Unknown')}")
        lines.append(
            f"  Team #{h(row.get('team_rank'))} | Overall {h(format_rank(row.get('overall_rank')))} | "
            f"{h(format_rating(row.get('rating')))} pts | {h(display_movement(row.get('movement')))} | {h(display_change(row.get('rating_change')))}"
        )
    return "\n".join(lines)


def _section_rows_grouped(rows: List[Dict[str, Any]], per_category: int = 5) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {category: [] for category in VALID_CATEGORIES}
    for row in rows:
        category = str(row.get("category") or "")
        if category not in grouped:
            grouped[category] = []
        if len(grouped[category]) < per_category:
            grouped[category].append(row)
    return {k: v for k, v in grouped.items() if v}


def format_report_section(section: str, title: str, per_category: int = 5) -> str:
    snapshot, rows = store().weekly_report(section=section, limit=50)
    if not rows:
        return f"No {h(title.lower())} found for the latest saved snapshot."

    lines = [snapshot_header(snapshot, title), ""]
    grouped = _section_rows_grouped(rows, per_category=per_category)
    for category in VALID_CATEGORIES:
        cat_rows = grouped.get(category, [])
        if not cat_rows:
            continue
        lines.append(f"{CATEGORY_ICONS.get(category, '•')} {bold(category)}")
        for row in cat_rows:
            lines.append(f"• {bold(row.get('player') or 'Unknown')} {italic(f'({row.get('team') or '—'})')}")
            lines.append(
                f"  {h(format_rank(row.get('current_rank')))} • {h(format_rating(row.get('current_rating')))} pts • "
                f"{h(display_movement(row.get('movement')))} • {h(display_change(row.get('rating_change')))}"
            )
        lines.append("")
    return "\n".join(lines).strip()


def command_movers(args: List[str]) -> str:
    return format_report_section("Top Climbers", "📈 HCCL Top Climbers")


def command_fallers(args: List[str]) -> str:
    return format_report_section("Top Fallers", "📉 HCCL Top Fallers")


def command_gains(args: List[str]) -> str:
    return format_report_section("Top Rating Gains", "🔥 HCCL Top Rating Gains")


def command_newentries(args: List[str]) -> str:
    return format_report_section("New Entries", "🆕 HCCL New Entries")


def command_report(args: List[str]) -> str:
    snapshot, rows = store().weekly_report(limit=60)
    if not rows:
        return "No weekly report rows found for the latest saved snapshot."

    # Keep the weekly report mobile-friendly: show top 3 from each report section.
    sections = ["Top Climbers", "Top Fallers", "Top Rating Gains", "New Entries"]
    titles = {
        "Top Climbers": "📈 Climbers",
        "Top Fallers": "📉 Fallers",
        "Top Rating Gains": "🔥 Rating Gains",
        "New Entries": "🆕 New Entries",
    }
    lines = [snapshot_header(snapshot, "🗞 HCCL Weekly Ranking Report"), ""]
    for section in sections:
        section_rows = [r for r in rows if r.get("report_section") == section][:3]
        if not section_rows:
            continue
        lines.append(bold(titles[section]))
        for row in section_rows:
            category = row.get("category") or ""
            icon = CATEGORY_ICONS.get(category, "•")
            lines.append(f"{icon} {bold(row.get('player') or 'Unknown')} {italic(f'({row.get('team') or '—'})')}")
            lines.append(
                f"  {h(category)} {h(format_rank(row.get('current_rank')))} • "
                f"{h(format_rating(row.get('current_rating')))} pts • "
                f"{h(display_movement(row.get('movement')))} • {h(display_change(row.get('rating_change')))}"
            )
        lines.append("")
    return "\n".join(lines).strip()


def command_benchmarks(args: List[str]) -> str:
    snapshot, rows = store().benchmarks()
    if not rows:
        return "No benchmarks found for the latest saved snapshot."
    lines = [snapshot_header(snapshot, "📊 HCCL Rating Benchmarks"), ""]
    for row in rows:
        key = row.get("benchmark_key")
        label = BENCHMARK_LABELS.get(key, str(key))
        lines.append(f"• {bold(label)}: {h(format_rating(row.get('benchmark_value')))}")
    return "\n".join(lines)


def command_profiledebug(args: List[str]) -> str:
    if not args:
        return "Use like this: /profiledebug Hasitha"
    query = " ".join(args)
    snapshot, player_name, rows, detail_row, suggestions = store().player_profile(query)
    if not player_name:
        return f"Player not found: {h(query)}"
    details = _safe_detail_data(detail_row)
    raw_type = type((detail_row or {}).get('data')).__name__ if detail_row else 'None'
    keys = sorted([str(k) for k in details.keys()])[:40]
    lines = [
        "🛠 <b>Profile Debug</b>",
        f"Player: {h(player_name)}",
        f"Snapshot: {h(snapshot.week_label)}",
        f"Detail row found: {'Yes' if detail_row else 'No'}",
        f"Raw data type: {h(raw_type)}",
        f"Available detail keys: {h(', '.join(keys) if keys else 'None')}",
        "",
        "<b>Main values</b>",
        f"innings={h(detail_value(details, 'innings'))}",
        f"runs={h(detail_value(details, 'runs'))}",
        f"wickets={h(detail_value(details, 'wickets'))}",
        f"bat_recent={h(detail_value(details, 'batting_recent_form'))}",
        f"bowl_recent={h(detail_value(details, 'bowling_recent_form'))}",
    ]
    return "\n".join(lines)


def command_weeks(args: List[str]) -> str:
    snapshots = store().list_snapshots(limit=10)
    if not snapshots:
        return "No saved ranking weeks found yet."
    lines = ["🗓 <b>Saved HCCL ranking weeks</b>", ""]
    for i, snapshot in enumerate(snapshots, start=1):
        official = "Official only" if snapshot.official_only else "All players"
        lines.append(f"{i}. {bold(snapshot.week_label)}")
        lines.append(f"   {h(snapshot.snapshot_date)} • {h(official)}")
    return "\n".join(lines)


COMMANDS = {
    "start": command_start,
    "help": command_start,
    "topbat": command_topbat,
    "topbowl": command_topbowl,
    "topall": command_topall,
    "player": command_player,
    "profile": command_profile,
    "card": command_card,
    "profiledebug": command_profiledebug,
    "team": command_team,
    "movers": command_movers,
    "fallers": command_fallers,
    "gains": command_gains,
    "newentries": command_newentries,
    "report": command_report,
    "benchmarks": command_benchmarks,
    "weeks": command_weeks,
}


def extract_message(update: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return update.get("message") or update.get("edited_message")


def parse_command(text: str) -> Optional[Tuple[str, List[str]]]:
    text = (text or "").strip()
    if not text.startswith("/"):
        return None
    parts = text.split()
    command_token = parts[0][1:]
    # In groups Telegram often sends /command@BotUsername.
    command_name = command_token.split("@", 1)[0].lower()
    return command_name, parts[1:]


def handle_update(update: Dict[str, Any]) -> Optional[Tuple[int, str, Optional[int]]]:
    """Return (chat_id, reply_text, message_thread_id) or None when no reply is needed."""
    message = extract_message(update)
    if not message:
        return None

    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return None

    thread_id = message.get("message_thread_id")
    if not is_chat_allowed(int(chat_id)):
        return int(chat_id), "Sorry, this HCCL bot is private.", thread_id

    parsed = parse_command(message.get("text") or "")
    if not parsed:
        return None
    command, args = parsed

    handler_func = COMMANDS.get(command)
    if not handler_func:
        return int(chat_id), "Unknown command. Send /help to see HCCL commands.", thread_id

    try:
        reply = handler_func(args)
    except HCCLBotError as exc:
        reply = str(exc)
    except Exception:
        # Avoid leaking secrets in Telegram, but keep the traceback visible in Vercel logs.
        traceback.print_exc()
        reply = "Something went wrong while reading HCCL rankings. Check Vercel logs for details."

    return int(chat_id), reply, thread_id


def json_response(handler_obj: BaseHTTPRequestHandler, status: int, payload: Dict[str, Any]) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler_obj.send_response(status)
    handler_obj.send_header("Content-Type", "application/json")
    handler_obj.send_header("Content-Length", str(len(body)))
    handler_obj.end_headers()
    handler_obj.wfile.write(body)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        json_response(
            self,
            200,
            {
                "ok": True,
                "service": "HCCL Telegram Bot Webhook",
                "path": "/api/telegram",
                "message": "POST Telegram updates to this endpoint.",
            },
        )

    def do_POST(self):
        configured_secret = get_env("WEBHOOK_SECRET")
        if configured_secret:
            incoming_secret = self.headers.get(SECRET_HEADER, "")
            if incoming_secret != configured_secret:
                json_response(self, 403, {"ok": False, "error": "Invalid webhook secret"})
                return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(length).decode("utf-8") if length else "{}"
            update = json.loads(raw_body or "{}")
        except Exception:
            json_response(self, 400, {"ok": False, "error": "Invalid JSON"})
            return

        result = handle_update(update)
        if result:
            chat_id, reply_text, thread_id = result
            try:
                send_message(chat_id, reply_text, message_thread_id=thread_id)
            except Exception:
                traceback.print_exc()
                # Reply with 200 so Telegram does not endlessly retry a bad update.
                json_response(self, 200, {"ok": False, "handled": False, "error": "Failed to send reply"})
                return

        json_response(self, 200, {"ok": True, "handled": bool(result)})
