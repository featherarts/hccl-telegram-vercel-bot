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
import random
import re
import sys
import traceback
import time
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
    normalize_text,
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
📊 /rank Hasitha — quick ranks
🔥 /form Hasitha — recent form
🎖️ /badges Hasitha — player badges/titles
🧬 /dna Hasitha — player DNA card
🔥 /hot — hottest recent-form players
🥶 /cold — coldest recent-form players
🚨 /expose — worst recent form expose list
⚔️ /compare Hasitha Yasitha — compare players
🎲 /battle — random player battle
🏟 /teamprofile TITANS — team profile
🏆 /power — team power rankings

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
    "teamprofile": "Clean team profile, e.g. /teamprofile TITANS",
    "power": "Team power rankings",
    "rank": "Quick player ranks, e.g. /rank Hasitha",
    "form": "Player recent form, e.g. /form Hasitha",
    "badges": "Player badges and titles, e.g. /badges Hasitha",
    "dna": "Player DNA card, e.g. /dna Hasitha",
    "hot": "Hottest recent-form players",
    "cold": "Coldest recent-form players",
    "expose": "Top 3 worst recent-form performers",
    "compare": "Compare two players, e.g. /compare Hasitha Yasitha",
    "battle": "Random player battle",
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


# ---------------------------------------------------------------------------
# Player badges / titles — fast, no extra DB calls when used inside profile.
# ---------------------------------------------------------------------------

BADGE_PRIORITY = [
    "👑 Elite Batter",
    "🎯 Strike Bowler",
    "⚔️ All-Round Warrior",
    "🔥 In-Form Beast",
    "💣 Run Machine",
    "🧨 Wicket Hunter",
    "🏏 Batting Star",
    "🛡️ Bowling Asset",
    "🚀 Fast Climber",
    "📉 Form Drop",
    "🥶 Cold Streak",
]


def _badge_rank_int(value: Any, default: int = 9999) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(str(value).replace("#", "").strip()))
    except Exception:
        return default


def _badge_rating(row: Optional[Dict[str, Any]], details: Dict[str, Any], *detail_keys: str) -> float:
    if row and row.get("rating") not in (None, ""):
        return as_number(row.get("rating"), default=0)
    return as_number(detail_value(details, *detail_keys), default=0)


def player_badges(rows_by_category: Dict[str, Dict[str, Any]], details: Dict[str, Any], max_badges: int = 4) -> List[str]:
    bat_row = rows_by_category.get("Batting")
    bowl_row = rows_by_category.get("Bowling")
    ar_row = rows_by_category.get("All-Rounder")

    bat_rank = _badge_rank_int((bat_row or {}).get("rank"))
    bowl_rank = _badge_rank_int((bowl_row or {}).get("rank"))
    ar_rank = _badge_rank_int((ar_row or {}).get("rank"))

    bat_rating = _badge_rating(bat_row, details, "batting_rating", "Batting Rating")
    bowl_rating = _badge_rating(bowl_row, details, "bowling_rating", "Bowling Rating")
    ar_rating = _badge_rating(ar_row, details, "all_rounder_rating", "All-Rounder Rating", "All Rounder Rating")

    runs = as_number(detail_value(details, "runs", "RUNS", "career_runs", "Career Runs"), default=0)
    wickets = as_number(detail_value(details, "wickets", "WICKETS", "career_wickets", "Career Wickets"), default=0)
    bat_form = as_number(detail_value(details, "batting_recent_form", "Batting Recent Form", "bat_recent_form"), default=0)
    bowl_form = as_number(detail_value(details, "bowling_recent_form", "Bowling Recent Form", "bowl_recent_form"), default=0)
    overall_form = bat_form + bowl_form

    changes = [as_number(row.get("rating_change"), default=0) for row in [bat_row, bowl_row, ar_row] if row]
    best_change = max(changes) if changes else 0
    worst_change = min(changes) if changes else 0

    badges: List[str] = []
    if bat_rank <= 3 or bat_rating >= 750:
        badges.append("👑 Elite Batter")
    if bowl_rank <= 3 or bowl_rating >= 750:
        badges.append("🎯 Strike Bowler")
    if ar_rank <= 5 or ar_rating >= 650:
        badges.append("⚔️ All-Round Warrior")
    if overall_form >= 60:
        badges.append("🔥 In-Form Beast")
    if runs >= 1000:
        badges.append("💣 Run Machine")
    if wickets >= 75:
        badges.append("🧨 Wicket Hunter")
    if bat_rating >= 650 and "👑 Elite Batter" not in badges:
        badges.append("🏏 Batting Star")
    if bowl_rating >= 650 and "🎯 Strike Bowler" not in badges:
        badges.append("🛡️ Bowling Asset")
    if best_change >= 30:
        badges.append("🚀 Fast Climber")
    if worst_change <= -30:
        badges.append("📉 Form Drop")
    if (runs >= 100 or wickets >= 10) and overall_form <= 10:
        badges.append("🥶 Cold Streak")

    ordered = [badge for badge in BADGE_PRIORITY if badge in badges]
    return ordered[:max_badges] or ["🧱 Squad Contributor"]


def badges_text(rows_by_category: Dict[str, Dict[str, Any]], details: Dict[str, Any], max_badges: int = 4) -> str:
    return " | ".join(player_badges(rows_by_category, details, max_badges=max_badges))


def badge_role(badges: List[str]) -> str:
    text = " ".join(badges)
    if "All-Round" in text:
        return "⚔️ All-round core"
    if "Elite Batter" in text or "Batting Star" in text or "Run Machine" in text:
        return "🏏 Batting weapon"
    if "Strike Bowler" in text or "Bowling Asset" in text or "Wicket Hunter" in text:
        return "🎯 Bowling weapon"
    if "In-Form" in text:
        return "🔥 Form player"
    if "Cold" in text:
        return "🥶 Needs comeback"
    return "🧱 Squad contributor"



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
        lines = [f"⚡ {bold(player_name)} {italic(f'({team})')}", f"🗓 {h(snapshot.week_label)}", f"🎖️ {h(badges_text(rows_by_category, details))}", ""]
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
        "🎖️ <b>Badges / Titles</b>",
        h(badges_text(rows_by_category, details)),
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




# ---------------------------------------------------------------------------
# New mobile-first interactive commands: /rank, /form, /compare, /battle,
# /teamprofile. These intentionally keep lines short for Telegram mobile.
# ---------------------------------------------------------------------------

def _profile_lookup(query: str):
    """Return snapshot, player name, rows, details, suggestions safely."""
    snapshot, player_name, rows, detail_row, suggestions = store().player_profile(query)
    details = _safe_detail_data(detail_row)
    rows_by_category = _rank_row_by_category(rows)
    team = (detail_row or {}).get("team") or (rows[0].get("team") if rows else "—")
    return snapshot, player_name, rows, rows_by_category, detail_row, details, team, suggestions


def _not_found_msg(kind: str, query: str, suggestions: List[str]) -> str:
    suggestion_text = ""
    if suggestions:
        suggestion_text = "\n\n<b>Possible matches</b>\n" + "\n".join(f"• {h(name)}" for name in suggestions[:8])
    return f"{h(kind)} not found: {h(query)}{suggestion_text}"


def _rank_mini_line(category: str, row: Optional[Dict[str, Any]]) -> str:
    icon = CATEGORY_ICONS.get(category, "•")
    short = CATEGORY_SHORT.get(category, category)
    if not row:
        return f"{icon} <b>{h(short)}</b> — not ranked"
    return (
        f"{icon} <b>{h(short)}</b> "
        f"{h(format_rank(row.get('rank')))} | "
        f"{h(format_rating(row.get('rating')))} pts | "
        f"{h(display_movement(row.get('movement')))}"
    )


def command_rank(args: List[str]) -> str:
    query = " ".join(args).strip()
    if not query:
        return "Use like this: /rank Hasitha"
    snapshot, player_name, rows, rows_by_category, detail_row, details, team, suggestions = _profile_lookup(query)
    if not player_name:
        return _not_found_msg("Player", query, suggestions)

    lines = [
        "📊 <b>HCCL QUICK RANKS</b>",
        "",
        f"{bold(player_name)} {italic(f'({team})')}",
        f"🗓 {h(snapshot.week_label)}",
        "",
    ]
    for category in ["Batting", "Bowling", "All-Rounder"]:
        lines.append(_rank_mini_line(category, rows_by_category.get(category)))
    lines.extend([
        "",
        f"🎖️ Badges: {h(badges_text(rows_by_category, details))}",
        f"🏆 Best: {h(_best_skill(rows_by_category))}",
        f"📈 Move: {h(_movement_summary(rows_by_category))}",
    ])
    return "\n".join(lines)


def _form_mood(score: Any) -> str:
    n = as_number(score, default=-999)
    if n == -999:
        return "—"
    if n >= 60:
        return "🔥 Excellent"
    if n >= 35:
        return "🟢 Good"
    if n >= 15:
        return "🟡 Steady"
    if n >= 0:
        return "🔵 Low"
    return "🔴 Needs boost"


def command_form(args: List[str]) -> str:
    query = " ".join(args).strip()
    if not query:
        return "Use like this: /form Hasitha"
    snapshot, player_name, rows, rows_by_category, detail_row, details, team, suggestions = _profile_lookup(query)
    if not player_name:
        return _not_found_msg("Player", query, suggestions)

    bat_form = detail_value(details, "batting_recent_form", "Batting Recent Form")
    bowl_form = detail_value(details, "bowling_recent_form", "Bowling Recent Form")
    bat_rating = detail_value(details, "batting_rating", "Batting Rating")
    bowl_rating = detail_value(details, "bowling_rating", "Bowling Rating")
    ar_rating = detail_value(details, "all_rounder_rating", "All-Rounder Rating", "All Rounder Rating")

    lines = [
        "🔥 <b>HCCL FORM CHECK</b>",
        "",
        f"{bold(player_name)} {italic(f'({team})')}",
        f"🗓 {h(snapshot.week_label)}",
        "",
        "🏏 <b>Batting Form</b>",
        f"{h(format_rating(bat_form))} pts • {h(_form_mood(bat_form))}",
        "",
        "🎯 <b>Bowling Form</b>",
        f"{h(format_rating(bowl_form))} pts • {h(_form_mood(bowl_form))}",
        "",
        "🎖️ <b>Badges</b>",
        h(badges_text(rows_by_category, details)),
        "",
        "📊 <b>Current Ratings</b>",
        f"🏏 Bat: {h(format_rating(bat_rating))}",
        f"🎯 Bowl: {h(format_rating(bowl_rating))}",
        f"👑 AR: {h(format_rating(ar_rating))}",
    ]
    return "\n".join(lines)




def command_badges(args: List[str]) -> str:
    query = " ".join(args).strip()
    if not query:
        return "Use like this: /badges Hasitha"
    snapshot, player_name, rows, rows_by_category, detail_row, details, team, suggestions = _profile_lookup(query)
    if not player_name:
        return _not_found_msg("Player", query, suggestions)

    badge_list = player_badges(rows_by_category, details, max_badges=6)
    runs = detail_value(details, "runs", "RUNS", "Career Runs")
    wickets = detail_value(details, "wickets", "WICKETS", "Career Wickets")
    bat_form = detail_value(details, "batting_recent_form", "Batting Recent Form")
    bowl_form = detail_value(details, "bowling_recent_form", "Bowling Recent Form")

    lines = [
        "🎖️ <b>HCCL PLAYER BADGES</b>",
        "",
        f"{bold(player_name)} {italic(f'({team})')}",
        f"🗓 {h(snapshot.week_label)}",
        "",
        "🏷️ <b>Titles</b>",
    ]
    for badge in badge_list:
        lines.append(f"• {h(badge)}")
    lines.extend([
        "",
        f"🧬 <b>Role:</b> {h(badge_role(badge_list))}",
        f"🏆 <b>Best discipline:</b> {h(_best_skill(rows_by_category))}",
        f"📌 <b>Career:</b> {h(format_rating(runs))} runs | {h(format_rating(wickets))} wkts",
        f"🔥 <b>Form:</b> Bat {h(format_rating(bat_form))} | Bowl {h(format_rating(bowl_form))}",
    ])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Player DNA Card — fast profile-style analysis using the same loaded player data.
# ---------------------------------------------------------------------------

def _dna_rank(row: Optional[Dict[str, Any]]) -> int:
    return _badge_rank_int((row or {}).get("rank"))


def _dna_type(bat_rating: float, bowl_rating: float, ar_rating: float, bat_rank: int, bowl_rank: int, ar_rank: int, runs: float, wickets: float) -> str:
    if ar_rating >= 650 or ar_rank <= 5:
        return "⚔️ Elite All-Rounder"
    if ar_rating >= 520 or (runs >= 100 and wickets >= 10 and abs(bat_rating - bowl_rating) <= 90):
        return "🛡️ Balanced All-Rounder"
    if bat_rating >= bowl_rating + 90 or (bat_rank <= bowl_rank and bat_rating >= 600):
        return "🏏 Batting Specialist"
    if bowl_rating >= bat_rating + 90 or (bowl_rank <= bat_rank and bowl_rating >= 600):
        return "🎯 Bowling Specialist"
    if runs >= 100 or wickets >= 10:
        return "🧱 Squad Contributor"
    return "🌱 Developing Player"


def _batting_dna(bat_rating: float, bat_form: float, runs: float, bat_rank: int) -> str:
    if bat_rank <= 3 or bat_rating >= 750:
        return "👑 Elite run creator"
    if runs >= 1000:
        return "💣 Proven run machine"
    if bat_form >= 35:
        return "🔥 In-form batting threat"
    if bat_rating >= 600:
        return "🏏 Reliable scoring option"
    if runs >= 100:
        return "🧱 Support batter"
    return "🌱 Still building batting impact"


def _bowling_dna(bowl_rating: float, bowl_form: float, wickets: float, bowl_rank: int) -> str:
    if bowl_rank <= 3 or bowl_rating >= 750:
        return "🎯 Strike bowling weapon"
    if wickets >= 75:
        return "🧨 Proven wicket hunter"
    if bowl_form >= 50:
        return "🔥 Hot wicket-taking form"
    if bowl_rating >= 600:
        return "🛡️ Strong bowling asset"
    if wickets >= 10:
        return "🎯 Useful bowling option"
    return "🌱 Limited bowling impact"


def _dna_form(overall_form: float) -> str:
    if overall_form >= 75:
        return "🔥 Red-hot form"
    if overall_form >= 45:
        return "🟢 Strong current form"
    if overall_form >= 20:
        return "🟡 Steady form"
    if overall_form >= 0:
        return "🔵 Quiet recent form"
    return "🥶 Cold spell"


def _dna_strength(bat_rating: float, bowl_rating: float, ar_rating: float, bat_form: float, bowl_form: float, runs: float, wickets: float) -> str:
    options = [
        (bat_rating, "🏏 Batting rating"),
        (bowl_rating, "🎯 Bowling rating"),
        (ar_rating, "⚔️ All-round value"),
        (bat_form * 8, "🔥 Batting form"),
        (bowl_form * 6, "🔥 Bowling form"),
        (min(runs / 2, 700), "💣 Career runs"),
        (min(wickets * 8, 700), "🧨 Career wickets"),
    ]
    return max(options, key=lambda x: x[0])[1]


def _dna_improve(bat_rating: float, bowl_rating: float, bat_form: float, bowl_form: float, runs: float, wickets: float) -> str:
    if runs >= 100 and bat_form <= 10:
        return "🏏 Batting recent form"
    if wickets >= 10 and bowl_form <= 10:
        return "🎯 Bowling recent form"
    if bat_rating < bowl_rating - 100:
        return "🏏 Batting contribution"
    if bowl_rating < bat_rating - 100:
        return "🎯 Bowling contribution"
    if runs < 100:
        return "🏏 More career runs"
    if wickets < 10:
        return "🎯 More wickets"
    return "📈 Keep consistency high"


def command_dna(args: List[str]) -> str:
    query = " ".join(args).strip()
    if not query:
        return "Use like this: /dna Hasitha"
    snapshot, player_name, rows, rows_by_category, detail_row, details, team, suggestions = _profile_lookup(query)
    if not player_name:
        return _not_found_msg("Player", query, suggestions)

    bat_row = rows_by_category.get("Batting")
    bowl_row = rows_by_category.get("Bowling")
    ar_row = rows_by_category.get("All-Rounder")

    bat_rating = _badge_rating(bat_row, details, "batting_rating", "Batting Rating")
    bowl_rating = _badge_rating(bowl_row, details, "bowling_rating", "Bowling Rating")
    ar_rating = _badge_rating(ar_row, details, "all_rounder_rating", "All-Rounder Rating", "All Rounder Rating")
    bat_rank = _dna_rank(bat_row)
    bowl_rank = _dna_rank(bowl_row)
    ar_rank = _dna_rank(ar_row)

    runs = as_number(detail_value(details, "runs", "RUNS", "career_runs", "Career Runs"), default=0)
    wickets = as_number(detail_value(details, "wickets", "WICKETS", "career_wickets", "Career Wickets"), default=0)
    innings = as_number(detail_value(details, "innings", "INNINGS", "career_innings", "Career Innings"), default=0)
    bat_form = as_number(detail_value(details, "batting_recent_form", "Batting Recent Form", "bat_recent_form"), default=0)
    bowl_form = as_number(detail_value(details, "bowling_recent_form", "Bowling Recent Form", "bowl_recent_form"), default=0)
    overall_form = max(-25.0, min(100.0, bat_form + bowl_form))
    badge_list = player_badges(rows_by_category, details, max_badges=4)

    player_type = _dna_type(bat_rating, bowl_rating, ar_rating, bat_rank, bowl_rank, ar_rank, runs, wickets)
    batting = _batting_dna(bat_rating, bat_form, runs, bat_rank)
    bowling = _bowling_dna(bowl_rating, bowl_form, wickets, bowl_rank)
    form = _dna_form(overall_form)
    strength = _dna_strength(bat_rating, bowl_rating, ar_rating, bat_form, bowl_form, runs, wickets)
    improve = _dna_improve(bat_rating, bowl_rating, bat_form, bowl_form, runs, wickets)

    lines = [
        "🧬 <b>HCCL PLAYER DNA</b>",
        "",
        f"{bold(player_name)} {italic(f'({team})')}",
        f"🗓 {h(snapshot.week_label)}",
        "",
        f"🧬 <b>Type:</b> {h(player_type)}",
        f"🎖️ <b>Badges:</b> {h(' | '.join(badge_list))}",
        "",
        "📊 <b>DNA Scores</b>",
        f"🏏 Bat: {h(format_rating(bat_rating))} | 🎯 Bowl: {h(format_rating(bowl_rating))} | 👑 AR: {h(format_rating(ar_rating))}",
        f"🔥 Form: {h(format_rating(overall_form))} | Bat {h(format_rating(bat_form))} | Bowl {h(format_rating(bowl_form))}",
        "",
        "🧠 <b>Profile Read</b>",
        f"🏏 Batting DNA: {h(batting)}",
        f"🎯 Bowling DNA: {h(bowling)}",
        f"🔥 Form DNA: {h(form)}",
        f"💎 Main strength: {h(strength)}",
        f"🪜 Improve next: {h(improve)}",
        "",
        f"📌 Career: {h(format_rating(innings))} inns • {h(format_rating(runs))} runs • {h(format_rating(wickets))} wkts",
    ]
    return "\n".join(lines)

def _parse_two_players(args: List[str]) -> Optional[Tuple[str, str, str]]:
    raw = " ".join(args).strip()
    if not raw:
        return None
    # Best format for full names: /compare Player One vs Player Two
    lowered = raw.lower()
    for sep in [" vs ", " v ", " | "]:
        if sep in lowered:
            idx = lowered.index(sep)
            return raw[:idx].strip(), raw[idx + len(sep):].strip(), raw
    if "," in raw:
        parts = [p.strip() for p in raw.split(",", 1)]
        if len(parts) == 2 and parts[0] and parts[1]:
            return parts[0], parts[1], raw
    if len(args) >= 2:
        # Convenient short-name mode: /compare Pasindu Yasitha
        return args[0], " ".join(args[1:]).strip(), raw
    return None


def _comparison_metric(label: str, left: Any, right: Any, suffix: str = "") -> str:
    return f"{h(label)}: <b>{h(format_rating(left))}{h(suffix)}</b> vs <b>{h(format_rating(right))}{h(suffix)}</b>"


def _get_rating_row(rows_by_category: Dict[str, Dict[str, Any]], category: str) -> Optional[Dict[str, Any]]:
    return rows_by_category.get(category)


def format_compare_card(left_query: str, right_query: str) -> str:
    left = _profile_lookup(left_query)
    right = _profile_lookup(right_query)

    lsnapshot, lname, lrows, lbycat, ldetail_row, ldetails, lteam, lsuggestions = left
    rsnapshot, rname, rrows, rbycat, rdetail_row, rdetails, rteam, rsuggestions = right

    if not lname:
        return _not_found_msg("Player", left_query, lsuggestions)
    if not rname:
        return _not_found_msg("Player", right_query, rsuggestions)

    lines = [
        "⚔️ <b>HCCL PLAYER BATTLE</b>",
        "",
        f"🅰️ {bold(lname)} {italic(f'({lteam})')}",
        f"🅱️ {bold(rname)} {italic(f'({rteam})')}",
        f"🗓 {h(lsnapshot.week_label)}",
        "",
        "📊 <b>Ratings</b>",
    ]

    category_winners: List[str] = []
    for category in ["Batting", "Bowling", "All-Rounder"]:
        icon = CATEGORY_ICONS.get(category, "•")
        short = CATEGORY_SHORT.get(category, category)
        lrow = _get_rating_row(lbycat, category)
        rrow = _get_rating_row(rbycat, category)
        lr = lrow.get("rating") if lrow else detail_value(ldetails, f"{category.lower()}_rating")
        rr = rrow.get("rating") if rrow else detail_value(rdetails, f"{category.lower()}_rating")
        lrank = format_rank(lrow.get("rank")) if lrow else "—"
        rrank = format_rank(rrow.get("rank")) if rrow else "—"
        lines.append(f"{icon} <b>{h(short)}</b>")
        lines.append(f"A {h(lrank)} | {h(format_rating(lr))} pts")
        lines.append(f"B {h(rrank)} | {h(format_rating(rr))} pts")
        if as_number(lr) > as_number(rr):
            category_winners.append(f"{short}: A")
        elif as_number(rr) > as_number(lr):
            category_winners.append(f"{short}: B")
        else:
            category_winners.append(f"{short}: Tie")
        lines.append("")

    lines.extend([
        "📌 <b>Career Snapshot</b>",
        _comparison_metric("Innings", detail_value(ldetails, "innings"), detail_value(rdetails, "innings")),
        _comparison_metric("Runs", detail_value(ldetails, "runs"), detail_value(rdetails, "runs")),
        _comparison_metric("Wickets", detail_value(ldetails, "wickets"), detail_value(rdetails, "wickets")),
        "",
        "🔥 <b>Recent Form</b>",
        _comparison_metric("Bat form", detail_value(ldetails, "batting_recent_form"), detail_value(rdetails, "batting_recent_form")),
        _comparison_metric("Bowl form", detail_value(ldetails, "bowling_recent_form"), detail_value(rdetails, "bowling_recent_form")),
        "",
        "🏆 <b>Quick Result</b>",
    ])

    # Decide overall winner by all-rounder rating if available, otherwise sum ratings.
    lar = as_number(detail_value(ldetails, "all_rounder_rating"), default=0)
    rar = as_number(detail_value(rdetails, "all_rounder_rating"), default=0)
    if lar == 0:
        lar = sum(as_number((_get_rating_row(lbycat, c) or {}).get("rating"), default=0) for c in VALID_CATEGORIES)
    if rar == 0:
        rar = sum(as_number((_get_rating_row(rbycat, c) or {}).get("rating"), default=0) for c in VALID_CATEGORIES)
    if lar > rar:
        winner = f"🅰️ {lname}"
    elif rar > lar:
        winner = f"🅱️ {rname}"
    else:
        winner = "🤝 Too close to call"
    lines.append(f"Winner: {h(winner)}")
    lines.append(f"Breakdown: {h(' | '.join(category_winners))}")
    return "\n".join(lines).strip()


def command_compare(args: List[str]) -> str:
    parsed = _parse_two_players(args)
    if not parsed:
        return "Use like this:\n/compare Pasindu Yasitha\n/compare Pasindu Dilshan vs Yasitha Nawod"
    left, right, _ = parsed
    if not left or not right:
        return "Use like this: /compare Pasindu Yasitha"
    return format_compare_card(left, right)


def command_battle(args: List[str]) -> str:
    snapshot, rows = store().all_rankings_latest()
    names: Dict[str, str] = {}
    for row in rows:
        name = str(row.get("player") or "").strip()
        if name:
            names[normalize_text(name)] = name
    player_names = sorted(set(names.values()))
    if len(player_names) < 2:
        return "Not enough players found for a battle yet."
    left, right = random.sample(player_names, 2)
    return "🎲 <b>Random Battle</b>\n\n" + format_compare_card(left, right)


# -----------------------------
# Team power rankings
# -----------------------------

def _team_norm(value: Any) -> str:
    text = normalize_text(value)
    aliases = {
        "auradynasty": "aura", "aura": "aura",
        "velocityreapers": "reapers", "reapers": "reapers",
        "superfiredragons": "dragons", "dragons": "dragons",
        "matrix": "matrix",
        "silenttearz": "tearz", "silenttears": "tearz", "tearz": "tearz", "tears": "tearz",
        "invinciblelords": "lords", "lords": "lords",
        "mindgamers": "gamers", "gamers": "gamers",
        "wizardtitans": "titans", "titans": "titans",
    }
    return aliases.get(text, text)


def _team_equal(a: Any, b: Any) -> bool:
    return _team_norm(a) == _team_norm(b)


def _team_avg_top(rows: List[Dict[str, Any]], team: str, category: str, n: int = 3) -> float:
    vals = [as_number(r.get("rating"), default=0) for r in rows if r.get("category") == category and _team_equal(r.get("team"), team)]
    vals = sorted([v for v in vals if v > 0], reverse=True)[:n]
    return sum(vals) / len(vals) if vals else 0.0


def _team_top_player(rows: List[Dict[str, Any]], team: str, category: str) -> Optional[Dict[str, Any]]:
    matches = [r for r in rows if r.get("category") == category and _team_equal(r.get("team"), team)]
    matches.sort(key=lambda r: int(as_number(r.get("rank"), default=9999)))
    return matches[0] if matches else None


def _power_detail_name(row: Dict[str, Any]) -> str:
    details = _safe_detail_data(row)
    return str(detail_value(details, "player", "name", "NAME") or row.get("player") or "")


def _power_detail_team(row: Dict[str, Any]) -> str:
    details = _safe_detail_data(row)
    return str(detail_value(details, "team", "TEAM") or row.get("team") or "")


def _team_power_rows() -> Tuple[Any, List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    store_obj = store()
    snapshot, ranking_rows = store_obj.all_rankings_latest()
    detail_rows = store_obj.rating_details(snapshot_id=snapshot.id)

    teams: Dict[str, str] = {}
    for r in ranking_rows:
        team = str(r.get("team") or "").strip()
        if team:
            teams.setdefault(_team_norm(team), team)
    for d in detail_rows:
        team = _power_detail_team(d)
        if team:
            teams.setdefault(_team_norm(team), team)

    out: List[Dict[str, Any]] = []
    for team_key, team_name in teams.items():
        bat = _team_avg_top(ranking_rows, team_name, "Batting") / 10.0
        bowl = _team_avg_top(ranking_rows, team_name, "Bowling") / 10.0
        ar = _team_avg_top(ranking_rows, team_name, "All-Rounder") / 10.0

        form_scores: List[Tuple[float, str]] = []
        player_count = 0
        for d in detail_rows:
            if not _team_equal(_power_detail_team(d), team_name):
                continue
            player_count += 1
            details = _safe_detail_data(d)
            name = _power_detail_name(d)
            combined = as_number(detail_value(details, "batting_recent_form"), default=0) + as_number(detail_value(details, "bowling_recent_form"), default=0)
            if combined > 0:
                form_scores.append((max(0.0, min(100.0, combined)), name))
        form_scores.sort(reverse=True, key=lambda x: x[0])
        top_form = form_scores[:3]
        form = sum(v for v, _ in top_form) / len(top_form) if top_form else 0.0
        form_player = top_form[0][1] if top_form else "—"

        pieces = [(bat, 0.35), (bowl, 0.35), (ar, 0.20), (form, 0.10)]
        available = [(v, w) for v, w in pieces if v > 0]
        power = sum(v * w for v, w in available) / sum(w for _, w in available) if available else 0.0

        top10 = set()
        for r in ranking_rows:
            if _team_equal(r.get("team"), team_name) and as_number(r.get("rank"), 9999) <= 10:
                top10.add(str(r.get("player") or ""))

        out.append({
            "team": team_name,
            "power": round(power, 1),
            "bat": round(bat, 1),
            "bowl": round(bowl, 1),
            "ar": round(ar, 1),
            "form": round(form, 1),
            "top_batter": (_team_top_player(ranking_rows, team_name, "Batting") or {}).get("player") or "—",
            "top_bowler": (_team_top_player(ranking_rows, team_name, "Bowling") or {}).get("player") or "—",
            "top_ar": (_team_top_player(ranking_rows, team_name, "All-Rounder") or {}).get("player") or "—",
            "form_player": form_player,
            "top10": len([p for p in top10 if p]),
            "players": player_count,
        })
    out.sort(key=lambda r: r.get("power") or 0, reverse=True)
    for i, r in enumerate(out, start=1):
        r["rank"] = i
    return snapshot, out, ranking_rows, detail_rows


def command_power(args: List[str]) -> str:
    snapshot, rows, _, _ = _team_power_rows()
    if not rows:
        return "No team power data found yet."
    limit = parse_limit(args, 8)
    lines = [
        "🏆 <b>HCCL TEAM POWER RANKINGS</b>",
        f"🗓 {h(snapshot.week_label)}",
        "📊 Formula: Bat 35% • Bowl 35% • AR 20% • Form 10%",
        "",
    ]
    for row in rows[:limit]:
        lines.append(f"{medal(int(row['rank']))} {bold(row['team'])}")
        lines.append(f"   ⚡ Power: <b>{h(row['power'])}</b>/100")
        lines.append(f"   🏏 {h(row['bat'])} | 🎯 {h(row['bowl'])} | 👑 {h(row['ar'])} | 🔥 {h(row['form'])}")
        lines.append(f"   Top 10 players: {h(row['top10'])} | Squad: {h(row['players'])}")
        if row is not rows[:limit][-1]:
            lines.append("")
    return "\n".join(lines).strip()


def command_teamprofile(args: List[str]) -> str:
    team_query = " ".join(args).strip()
    if not team_query:
        return "Use like this: /teamprofile TITANS"

    snapshot, team_name, team_rows, suggestions = store().team_rankings(team_query, category=None, per_category_limit=10)
    if not team_name or not team_rows:
        return _not_found_msg("Team", team_query, suggestions)

    by_category: Dict[str, List[Dict[str, Any]]] = {c: [] for c in VALID_CATEGORIES}
    for row in team_rows:
        cat = row.get("category")
        if cat in by_category:
            by_category[cat].append(row)

    # Team power summary if available.
    power_row = None
    try:
        _, power_rows, _, _ = _team_power_rows()
        for prow in power_rows:
            if _team_equal(prow.get("team"), team_name):
                power_row = prow
                break
    except Exception:
        power_row = None

    # Find best recent-form player from rating details if available.
    form_player = "—"
    form_score = None
    try:
        detail_rows = store().rating_details(snapshot_id=snapshot.id)
        team_key = normalize_text(team_name)
        best = None
        best_score = -10**9
        for row in detail_rows:
            if normalize_text(row.get("team")) != team_key:
                continue
            details = _safe_detail_data(row)
            score = as_number(detail_value(details, "batting_recent_form"), default=0) + as_number(detail_value(details, "bowling_recent_form"), default=0)
            if score > best_score:
                best_score = score
                best = row.get("player") or row.get("name")
        if best:
            form_player = str(best)
            form_score = best_score
    except Exception:
        # Keep team profile working even if details are missing.
        pass

    def best_line(category: str, label: str) -> str:
        rows = by_category.get(category) or []
        if not rows:
            return f"{label}: —"
        row = rows[0]
        return (
            f"{label}: {bold(row.get('player') or 'Unknown')}\n"
            f"   Overall {h(format_rank(row.get('overall_rank')))} | {h(format_rating(row.get('rating')))} pts"
        )

    lines = [
        "🏟 <b>HCCL TEAM PROFILE</b>",
        "",
        bold(team_name),
        f"🗓 {h(snapshot.week_label)}",
        "",
        "⚡ <b>Power Summary</b>",
        f"Power Score: <b>{h(power_row.get('power') if power_row else '—')}</b>/100",
        f"Top 10 players: {h(power_row.get('top10') if power_row else '—')} | Squad: {h(power_row.get('players') if power_row else '—')}",
        f"🏏 {h(power_row.get('bat') if power_row else '—')} | 🎯 {h(power_row.get('bowl') if power_row else '—')} | 👑 {h(power_row.get('ar') if power_row else '—')} | 🔥 {h(power_row.get('form') if power_row else '—')}",
        "",
        "⭐ <b>Team Leaders</b>",
        best_line("Batting", "🏏 Batter"),
        "",
        best_line("Bowling", "🎯 Bowler"),
        "",
        best_line("All-Rounder", "👑 All-Rounder"),
        "",
        "🔥 <b>In-form Player</b>",
        f"{h(form_player)}" + (f" | {h(format_rating(form_score))} form pts" if form_score is not None else ""),
        "",
        "📋 <b>Quick Top 3</b>",
    ]
    for category in VALID_CATEGORIES:
        rows = by_category.get(category, [])[:3]
        if not rows:
            continue
        lines.append(f"{CATEGORY_ICONS.get(category, '•')} {bold(category)}")
        for i, row in enumerate(rows, start=1):
            lines.append(f"{i}) {bold(row.get('player') or 'Unknown')}")
            lines.append(f"   {h(format_rating(row.get('rating')))} pts | Overall {h(format_rank(row.get('overall_rank')))}")
        lines.append("")
    return "\n".join(lines).strip()



# -----------------------------
# Hot / cold recent-form tracker
# -----------------------------

_FORM_TRACKER_CACHE: Dict[str, Any] = {"expires": 0.0, "snapshot": None, "rows": []}
FORM_CACHE_TTL_SECONDS = 45


def _form_mood(score: float, hot: bool = True) -> str:
    if score >= 75:
        return "🔥 Elite"
    if score >= 50:
        return "🟢 Hot"
    if score >= 25:
        return "🟡 Steady"
    if score >= 0:
        return "🔵 Low"
    return "🥶 Cold"


def _build_form_rows(detail_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for row in detail_rows:
        details = _safe_detail_data(row)
        player = str(row.get("player") or detail_value(details, "player", "name", "NAME") or "").strip()
        team = str(row.get("team") or detail_value(details, "team", "TEAM") or "—").strip()
        if not player:
            continue
        key = normalize_text(player)
        if key in seen:
            continue
        seen.add(key)

        runs = as_number(detail_value(details, "runs", "RUNS", "career_runs", "Career Runs"), default=0)
        wickets = as_number(detail_value(details, "wickets", "WICKETS", "career_wickets", "Career Wickets"), default=0)
        if runs < 100 and wickets < 10:
            continue

        bat_raw = detail_value(details, "batting_recent_form", "Batting Recent Form", "bat_recent_form")
        bowl_raw = detail_value(details, "bowling_recent_form", "Bowling Recent Form", "bowl_recent_form")
        if bat_raw is None and bowl_raw is None:
            continue

        bat = as_number(bat_raw, default=0)
        bowl = as_number(bowl_raw, default=0)
        overall = max(-25.0, min(100.0, bat + bowl))
        rows.append({
            "player": player,
            "team": team or "—",
            "overall": round(overall, 1),
            "bat": round(bat, 1),
            "bowl": round(bowl, 1),
            "runs": int(runs) if float(runs).is_integer() else round(runs, 1),
            "wickets": int(wickets) if float(wickets).is_integer() else round(wickets, 1),
            "mood": _form_mood(overall),
        })
    return rows


def _form_tracker_data() -> Tuple[Any, List[Dict[str, Any]]]:
    now = time.time()
    cached_snapshot = _FORM_TRACKER_CACHE.get("snapshot")
    if cached_snapshot is not None and now < float(_FORM_TRACKER_CACHE.get("expires") or 0):
        return cached_snapshot, list(_FORM_TRACKER_CACHE.get("rows") or [])

    store_obj = store()
    snapshot = store_obj.latest_snapshot()
    detail_rows = store_obj.rating_details(snapshot_id=snapshot.id)
    rows = _build_form_rows(detail_rows)
    _FORM_TRACKER_CACHE.update({"expires": now + FORM_CACHE_TTL_SECONDS, "snapshot": snapshot, "rows": rows})
    return snapshot, rows


def _format_form_list(snapshot: Any, rows: List[Dict[str, Any]], *, title: str, sort_key: str, reverse: bool, limit: int, cold: bool = False) -> str:
    if not rows:
        return (
            f"{title}\n\n"
            "No eligible recent-form data found yet.\n"
            "Eligibility: 100+ career runs or 10+ wickets."
        )

    rows = sorted(rows, key=lambda r: (as_number(r.get(sort_key), default=0), str(r.get("player") or "")), reverse=reverse)[:limit]
    lines = [
        title,
        f"🗓 {h(snapshot.week_label)}",
        "✅ Eligible: 100+ runs or 10+ wickets",
        "",
    ]
    for i, row in enumerate(rows, start=1):
        lines.append(f"{medal(i)} {bold(row.get('player'))} <i>({h(row.get('team'))})</i>")
        if sort_key == "bat":
            lines.append(f"   🏏 Bat Form: <b>{h(format_rating(row.get('bat')))}</b> | Overall {h(format_rating(row.get('overall')))}")
        elif sort_key == "bowl":
            lines.append(f"   🎯 Bowl Form: <b>{h(format_rating(row.get('bowl')))}</b> | Overall {h(format_rating(row.get('overall')))}")
        else:
            lines.append(f"   {'🥶' if cold else '🔥'} Overall: <b>{h(format_rating(row.get('overall')))}</b> | {h(row.get('mood'))}")
        lines.append(f"   🏏 {h(format_rating(row.get('bat')))} | 🎯 {h(format_rating(row.get('bowl')))}")
        lines.append(f"   Runs: {h(format_rating(row.get('runs')))} | Wkts: {h(format_rating(row.get('wickets')))}")
        if i != len(rows):
            lines.append("")
    return "\n".join(lines).strip()


def _form_sort_mode(args: List[str], default_limit: int = 5) -> Tuple[str, int]:
    mode = "overall"
    limit = default_limit
    if args:
        first = normalize_text(args[0])
        if first in {"bat", "batting", "batter"}:
            mode = "bat"
            limit = parse_limit(args[1:], default_limit)
        elif first in {"bowl", "bowling", "bowler"}:
            mode = "bowl"
            limit = parse_limit(args[1:], default_limit)
        else:
            limit = parse_limit(args, default_limit)
    return mode, max(1, min(10, limit))


def command_hot(args: List[str]) -> str:
    snapshot, rows = _form_tracker_data()
    mode, limit = _form_sort_mode(args, default_limit=5)
    title = "🔥 <b>HCCL HOT FORM PLAYERS</b>"
    if mode == "bat":
        title = "🏏 <b>HCCL HOT BATTING FORM</b>"
    elif mode == "bowl":
        title = "🎯 <b>HCCL HOT BOWLING FORM</b>"
    return _format_form_list(snapshot, rows, title=title, sort_key=mode if mode != "overall" else "overall", reverse=True, limit=limit)


def command_cold(args: List[str]) -> str:
    snapshot, rows = _form_tracker_data()
    mode, limit = _form_sort_mode(args, default_limit=5)
    title = "🥶 <b>HCCL COLD FORM PLAYERS</b>"
    if mode == "bat":
        title = "🥶🏏 <b>HCCL COLD BATTING FORM</b>"
    elif mode == "bowl":
        title = "🥶🎯 <b>HCCL COLD BOWLING FORM</b>"
    return _format_form_list(snapshot, rows, title=title, sort_key=mode if mode != "overall" else "overall", reverse=False, limit=limit, cold=True)



# ---------------------------------------------------------------------------
# /expose - mobile-first worst recent form report
# ---------------------------------------------------------------------------

EXPOSE_LABELS = [
    "සුපිරිම ලොන්තයා",
    "දෙවෙනි ලොන්තයා",
    "තුන්වෙනි ලොන්තයා",
]


def _recent_lines(raw: Any) -> List[str]:
    """Split saved recent-5 text into individual match lines.

    Supports normal multiline CSV text and older accidentally-joined text such as
    "... POTM No2. 10 runs ...".
    """
    text = str(raw or "").strip()
    if not text:
        return []
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Repair old joined lines before splitting.
    text = re.sub(r"(?i)(POTM\s*(?:Yes|No|YES|NO))\s*(?=\d+\.)", r"\1\n", text)
    text = re.sub(r"(?m)^\s*\d+\.\s*", "", text)
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    return lines[:5]


def _bat_recent_performances(raw: Any) -> List[str]:
    output: List[str] = []
    for line in _recent_lines(raw):
        if "DNB" in line.upper():
            output.append("DNB")
            continue
        runs_match = re.search(r"(\d+)\s*runs?", line, flags=re.IGNORECASE)
        if not runs_match:
            output.append("—")
            continue
        runs = runs_match.group(1)
        not_out = bool(re.search(r"Not\s*out\s*Yes", line, flags=re.IGNORECASE))
        output.append(f"{runs}{'*' if not_out else ''}")
    return output[:5]


def _bowl_recent_performances(raw: Any) -> List[str]:
    output: List[str] = []
    for line in _recent_lines(raw):
        if "DNB" in line.upper():
            output.append("DNB")
            continue
        w_match = re.search(r"(\d+)\s*Wickets?", line, flags=re.IGNORECASE)
        # New dashboard saves "Runs Conceded" for new scorecard updates.
        r_match = re.search(r"(\d+)\s*Runs?\s*Conceded", line, flags=re.IGNORECASE)
        eco_match = re.search(r"([0-9.]+)\s*Eco", line, flags=re.IGNORECASE)
        wickets = w_match.group(1) if w_match else "0"
        if r_match:
            output.append(f"{wickets}/{r_match.group(1)}")
        elif eco_match:
            output.append(f"{wickets}W @ {format_rating(eco_match.group(1))} eco")
        else:
            output.append(f"{wickets}W")
    return output[:5]


def _points_list(raw_points: Any) -> List[str]:
    text = str(raw_points or "").strip()
    if not text:
        return []
    return [p.strip() for p in re.split(r"[,|;/]+", text) if p.strip()][:5]


def _perf_line(values: List[str]) -> str:
    return ", ".join(values) if values else "Recent innings not saved yet"


def _expose_candidates(snapshot_id: str) -> List[Dict[str, Any]]:
    """Return top 3 worst batting recent-form players.

    Rules requested by HCCL:
    - Only batting performances are considered.
    - Only players with 100+ career runs are eligible.
    - Recent-form score is used to rank worst first.
    - Inning-by-inning batting scores are displayed from saved recent raw data.
    """
    rows = store().rating_details(snapshot_id=snapshot_id)
    candidates: List[Dict[str, Any]] = []
    seen_players: set[str] = set()

    for row in rows:
        details = _safe_detail_data(row)
        player = row.get("player") or detail_value(details, "name", "NAME") or "Unknown"
        team = row.get("team") or detail_value(details, "team", "TEAM") or "—"
        player_key = normalize_text(player)
        if player_key in seen_players:
            continue

        career_runs = as_number(detail_value(details, "runs", "RUNS", "career_runs", "Career Runs"), default=0)
        if career_runs < 100:
            continue

        bat_score_raw = detail_value(details, "batting_recent_form", "Batting Recent Form")
        if bat_score_raw is None:
            continue

        bat_raw = detail_value(details, "batting_recent_raw", "Bat Recent 5 Matches", "Batting Recent Raw")
        bat_points = detail_value(details, "batting_recent_points", "Batting Recent Points")

        seen_players.add(player_key)
        candidates.append({
            "player": player,
            "team": team,
            "runs": career_runs,
            "score": as_number(bat_score_raw, default=9999),
            "score_display": format_rating(bat_score_raw),
            "performances": _bat_recent_performances(bat_raw),
            "points": _points_list(bat_points),
            "raw_available": bool(str(bat_raw or "").strip()),
        })

    candidates.sort(key=lambda c: (c["score"], c["player"]))
    return candidates[:3]


def command_expose(args: List[str]) -> str:
    snapshot = store().latest_snapshot()
    exposed = _expose_candidates(snapshot.id)
    if not exposed:
        return (
            "🚨 <b>HCCL EXPOSE LIST</b>\n\n"
            "No eligible players found yet.\n"
            "Eligible players must have 100+ career runs and saved batting recent-form data."
        )

    lines = [
        "🚨 <b>HCCL EXPOSE LIST</b> 🚨",
        f"🗓 {h(snapshot.week_label)} | {h(snapshot.snapshot_date)}",
        "🏏 Worst batting recent-form performers",
        "✅ Eligibility: 100+ career runs",
        "",
    ]

    raw_missing = False
    for idx, item in enumerate(exposed, start=1):
        label = EXPOSE_LABELS[idx - 1] if idx <= len(EXPOSE_LABELS) else f"#{idx}"
        if not item.get("raw_available"):
            raw_missing = True

        performances = _perf_line(item.get("performances") or [])
        lines.extend([
            f"{medal(idx)} <b>{h(label)}</b>",
            f"👤 {bold(item['player'])}",
            f"🏟 {h(item['team'])} | 🏏 {h(format_rating(item.get('runs')))} career runs",
            "",
            "🏏 <b>With the bat</b>",
            f"{h(performances)}",
            "",
            f"📉 <b>Recent form score:</b> {h(item['score_display'])}",
        ])
        points = item.get("points") or []
        if points:
            lines.append(f"🧮 Points: {h(', '.join(points))}")
        if idx != len(exposed):
            lines.append("━━━━━━━━━━━━")
            lines.append("")

    if raw_missing:
        lines.extend([
            "",
            "ℹ️ Inning-by-inning data is missing for some players.",
            "Update the dashboard to v5.0 and save a fresh Supabase snapshot.",
        ])
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
    "teamprofile": command_teamprofile,
    "power": command_power,
    "rank": command_rank,
    "form": command_form,
    "badges": command_badges,
    "dna": command_dna,
    "hot": command_hot,
    "cold": command_cold,
    "expose": command_expose,
    "compare": command_compare,
    "battle": command_battle,
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
