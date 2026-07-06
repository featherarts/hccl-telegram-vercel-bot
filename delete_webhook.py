"""Delete Telegram webhook, useful if you want to return to local polling."""

from __future__ import annotations

import json
import os
import urllib.request

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Missing TELEGRAM_BOT_TOKEN")
    url = f"https://api.telegram.org/bot{token}/deleteWebhook"
    data = json.dumps({"drop_pending_updates": False}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as response:
        print(response.read().decode("utf-8"))


if __name__ == "__main__":
    main()
