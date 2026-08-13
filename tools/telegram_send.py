#!/usr/bin/env python3
"""telegram_send — amele tool (subprocess).

stdin: text to send
stdout: "ok" or an error message
--test: sends a fixed test message (install verification)

Env vars: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


def send(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        return ("ERROR: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars are "
                "missing — see secrets.env")
    # HTML escaping (we use parse_mode=HTML)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=30):
            return "ok"
    except urllib.error.HTTPError as e:
        return f"ERROR: Telegram {e.code}: {e.read().decode(errors='replace')[:200]}"


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print(send("🧪 Patrol test message — connection works."))
    else:
        print(send(sys.stdin.read().strip()))
