"""Vercel serverless Telegram webhook for HCCL Rankings Bot.

This file is loaded by Vercel as /api/telegram.
It receives Telegram webhook POST updates, reads latest rankings from Supabase,
and sends replies through the Telegram Bot API.
"""

from __future__ import annotations

import json
import os
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
    format_player_line,
    format_rank,
    format_rating,
    parse_category,
)


BOT_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
MAX_MESSAGE_LENGTH = 3900


HELP_TEXT = """
🏏 Welcome to the HCCL Rankings Bot

Use these commands:
/topbat - top batting rankings
/topbowl - top bowling rankings
/topall - top all-rounder rankings
/player Hasitha - player profile
/team DRAGONS - team-wise rankings
/movers - biggest rank climbers
/fallers - biggest rank fallers
/gains - biggest rating gains
/newentries - new ranking entries
/report - weekly ranking report
/benchmarks - current rating benchmarks
/weeks - saved ranking weeks
/help - command list

Tip: You can add a number, for example /topbat 5
""".strip()


COMMAND_DESCRIPTIONS = {
    "start": "Show welcome message",
    "help": "Show command list",
    "topbat": "Top batting rankings",
    "topbowl": "Top bowling rankings",
    "topall": "Top all-rounder rankings",
    "player": "Player profile, e.g. /player Hasitha",
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


def snapshot_header(snapshot: Any, title: str) -> str:
    official = "Official only" if snapshot.official_only else "All calculated players"
    return f"{title}\n{snapshot.week_label} | {snapshot.snapshot_date} | {official}\n"


def format_top(category: str, limit: int) -> str:
    snapshot, rows = store().top(category, limit=limit)
    if not rows:
        return f"No {category} rankings found."
    icon = "🏏" if category == "Batting" else "🎯" if category == "Bowling" else "👑"
    lines = [snapshot_header(snapshot, f"{icon} HCCL {category} Top {limit}")]
    for row in rows:
        lines.append(f"{icon} {format_player_line(row)}")
    return "\n".join(lines)


def command_start(args: List[str]) -> str:
    return HELP_TEXT


def command_topbat(args: List[str]) -> str:
    return format_top("Batting", parse_limit(args, default_limit()))


def command_topbowl(args: List[str]) -> str:
    return format_top("Bowling", parse_limit(args, default_limit()))


def command_topall(args: List[str]) -> str:
    return format_top("All-Rounder", parse_limit(args, default_limit()))


def command_player(args: List[str]) -> str:
    query = " ".join(args).strip()
    if not query:
        return "Use like this: /player Hasitha"

    snapshot, player_name, rows, suggestions = store().find_player(query)
    if not rows or not player_name:
        suggestion_text = ""
        if suggestions:
            suggestion_text = "\n\nPossible matches:\n" + "\n".join(f"- {name}" for name in suggestions)
        return f"Player not found: {query}{suggestion_text}"

    lines = [snapshot_header(snapshot, f"👤 HCCL Player Profile: {player_name}")]
    for row in rows:
        lines.append(
            f"{row.get('category')}: {format_rank(row.get('rank'))}\n"
            f"Rating: {format_rating(row.get('rating'))}\n"
            f"Previous: {format_rating(row.get('previous_rating'))}\n"
            f"Change: {row.get('rating_change') or '—'}\n"
            f"Movement: {row.get('movement') or '—'}\n"
            f"Status: {row.get('status') or '—'}\n"
        )
    return "\n".join(lines)


def command_team(args: List[str]) -> str:
    if not args:
        return "Use like this: /team DRAGONS or /team DRAGONS batting"

    team_query = args[0]
    category = parse_category(args[1], default="") if len(args) > 1 else None
    snapshot, team_name, rows, suggestions = store().team_rankings(team_query, category=category, per_category_limit=7)
    if not rows or not team_name:
        suggestion_text = ""
        if suggestions:
            suggestion_text = "\n\nAvailable teams / possible matches:\n" + "\n".join(f"- {name}" for name in suggestions)
        return f"Team not found: {team_query}{suggestion_text}"

    title = f"🏟️ HCCL Team Rankings: {team_name}"
    if category:
        title += f" | {category}"
    lines = [snapshot_header(snapshot, title)]
    last_category = None
    for row in rows:
        row_category = row.get("category") or ""
        if row_category != last_category:
            lines.append(f"\n{row_category}")
            last_category = row_category
        lines.append(
            f"Team #{row.get('team_rank')} | Overall {format_rank(row.get('overall_rank'))}: "
            f"{row.get('player')} — {format_rating(row.get('rating'))} pts "
            f"{row.get('movement') or '—'} | {row.get('rating_change') or '—'}"
        )
    return "\n".join(lines)


def format_report_section(section: str, title: str) -> str:
    snapshot, rows = store().weekly_report(section=section, limit=25)
    if not rows:
        return f"No {title.lower()} found for the latest saved snapshot."
    lines = [snapshot_header(snapshot, title)]
    for row in rows:
        lines.append(
            f"{row.get('category')} #{row.get('current_rank')}: {row.get('player')} ({row.get('team')}) — "
            f"{format_rating(row.get('current_rating'))} pts | {row.get('movement') or '—'} | {row.get('rating_change') or '—'}"
        )
    return "\n".join(lines)


def command_movers(args: List[str]) -> str:
    return format_report_section("Top Climbers", "🚀 HCCL Top Climbers")


def command_fallers(args: List[str]) -> str:
    return format_report_section("Top Fallers", "📉 HCCL Top Fallers")


def command_gains(args: List[str]) -> str:
    return format_report_section("Top Rating Gains", "🔥 HCCL Top Rating Gains")


def command_newentries(args: List[str]) -> str:
    return format_report_section("New Entries", "🆕 HCCL New Entries")


def command_report(args: List[str]) -> str:
    snapshot, rows = store().weekly_report(limit=40)
    if not rows:
        return "No weekly report rows found for the latest saved snapshot."
    lines = [snapshot_header(snapshot, "🗞️ HCCL Weekly Ranking Report")]
    last_group = None
    for row in rows:
        group = f"{row.get('category')} | {row.get('report_section')}"
        if group != last_group:
            lines.append(f"\n{group}")
            last_group = group
        lines.append(
            f"#{row.get('current_rank')}: {row.get('player')} ({row.get('team')}) — "
            f"{format_rating(row.get('current_rating'))} pts | {row.get('movement') or '—'} | {row.get('rating_change') or '—'}"
        )
    return "\n".join(lines)


def command_benchmarks(args: List[str]) -> str:
    snapshot, rows = store().benchmarks()
    if not rows:
        return "No benchmarks found for the latest saved snapshot."
    lines = [snapshot_header(snapshot, "📊 HCCL Rating Benchmarks")]
    for row in rows:
        key = row.get("benchmark_key")
        label = BENCHMARK_LABELS.get(key, str(key))
        lines.append(f"{label}: {format_rating(row.get('benchmark_value'))}")
    return "\n".join(lines)


def command_weeks(args: List[str]) -> str:
    snapshots = store().list_snapshots(limit=10)
    if not snapshots:
        return "No saved ranking weeks found yet."
    lines = ["🗓️ Saved HCCL ranking weeks\n"]
    for i, snapshot in enumerate(snapshots, start=1):
        official = "Official only" if snapshot.official_only else "All players"
        lines.append(f"{i}. {snapshot.week_label} — {snapshot.snapshot_date} — {official}")
    return "\n".join(lines)


COMMANDS = {
    "start": command_start,
    "help": command_start,
    "topbat": command_topbat,
    "topbowl": command_topbowl,
    "topall": command_topall,
    "player": command_player,
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
