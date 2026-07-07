"""Set Telegram webhook for the HCCL Vercel bot.

Usage:
    python set_webhook.py https://your-vercel-app.vercel.app/api/telegram

Environment variables required:
    TELEGRAM_BOT_TOKEN

Optional:
    WEBHOOK_SECRET   Must match the WEBHOOK_SECRET in Vercel env vars.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from typing import Any, Dict

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

COMMANDS = [
    {"command": "start", "description": "Show welcome message"},
    {"command": "help", "description": "Show command list"},
    {"command": "topbat", "description": "Top batting rankings"},
    {"command": "topbowl", "description": "Top bowling rankings"},
    {"command": "topall", "description": "Top all-rounder rankings"},
    {"command": "player", "description": "Player profile, e.g. /player Hasitha"},
    {"command": "profile", "description": "Full profile card, e.g. /profile Hasitha"},
    {"command": "card", "description": "Compact profile card, e.g. /card Hasitha"},
    {"command": "team", "description": "Team rankings, e.g. /team DRAGONS"},
    {"command": "movers", "description": "Top rank climbers"},
    {"command": "fallers", "description": "Top rank fallers"},
    {"command": "gains", "description": "Top rating gains"},
    {"command": "newentries", "description": "New ranking entries"},
    {"command": "report", "description": "Weekly ranking report"},
    {"command": "benchmarks", "description": "Current rating benchmarks"},
    {"command": "weeks", "description": "Saved ranking weeks"},
]


def bot_api(method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Missing TELEGRAM_BOT_TOKEN. Add it to .env or your environment.")
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python set_webhook.py https://your-vercel-app.vercel.app/api/telegram")

    webhook_url = sys.argv[1].strip()
    if not webhook_url.startswith("https://"):
        raise SystemExit("Webhook URL must start with https://")

    secret = os.getenv("WEBHOOK_SECRET", "").strip()

    print("Setting bot command menu...")
    print(json.dumps(bot_api("setMyCommands", {"commands": COMMANDS}), indent=2))

    payload: Dict[str, Any] = {
        "url": webhook_url,
        "drop_pending_updates": True,
        "allowed_updates": ["message", "edited_message"],
    }
    if secret:
        payload["secret_token"] = secret

    print("Setting webhook...")
    print(json.dumps(bot_api("setWebhook", payload), indent=2))

    print("Checking webhook info...")
    print(json.dumps(bot_api("getWebhookInfo", {}), indent=2))


if __name__ == "__main__":
    main()
