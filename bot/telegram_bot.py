#!/usr/bin/env python3
"""vigil Telegram bot — triggers a camera patrol when the owner says "check".

Runs long-lived (long-polling). Only responds to the owner's TELEGRAM_CHAT_ID.
On trigger it runs `bin/amele run agent.yaml ...`; the agent sends the report
itself via send_telegram, the bot only reports start/finish.

Run:        python3 bot/telegram_bot.py
Scheduler:  macOS: deploy/install-launchd.sh (keeps it alive) | Linux: systemd unit (see README)

Env vars: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (secrets.env)
"""
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load_env():
    """Load secrets.env into the environment (if present)."""
    env_file = ROOT / "secrets.env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def api(method, **params):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(params).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=65) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"Telegram API error {e.code}: {e.read().decode(errors='replace')[:200]}")
        return None
    except Exception as e:  # noqa: BLE001
        print(f"Telegram API error: {e}")
        return None


def send(chat_id, text):
    api("sendMessage", chat_id=chat_id, text=text)


def run_patrol():
    """Run the amele agent; return (exit code, summary)."""
    amele = ROOT / "bin" / "amele"
    cmd = [str(amele), "run", "agent.yaml",
           "Check all cameras and send the patrol report to Telegram."]
    try:
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=600)
        summary = (p.stdout or "").strip().splitlines()[-1:] + (p.stderr or "").strip().splitlines()[-1:]
        return p.returncode, " | ".join(summary)[:300]
    except FileNotFoundError:
        return -1, "bin/amele not found — run ./deploy/install.sh first"
    except subprocess.TimeoutExpired:
        return -2, "patrol timed out (600s)"


def main():
    load_env()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token:
        print("ERROR: TELEGRAM_BOT_TOKEN missing — fill in secrets.env.")
        sys.exit(1)
    if not chat:
        print("ERROR: TELEGRAM_CHAT_ID missing. Message the bot, find your ID "
              "with the one-liner in secrets.env.example, then restart.")
        sys.exit(1)

    print("Vigil bot listening... (Ctrl+C to stop)")
    offset = 0
    while True:
        r = api("getUpdates", offset=offset, timeout=50)
        if not r or not r.get("ok"):
            time.sleep(5)
            continue
        for upd in r.get("result", []):
            offset = upd["update_id"] + 1
            msg = upd.get("message") or {}
            cid = str(msg.get("chat", {}).get("id", ""))
            if cid != chat:
                continue  # owner only
            text = (msg.get("text") or "").strip()
            lowered = text.lower()
            if (text in ("/check", "/patrol", "/kontrol", "/devriye", "/start")
                    or any(t in lowered for t in ("check", "patrol", "kontrol", "devriye"))):
                send(cid, "🕐 Patrol started — checking all cameras; this may take a few minutes.")
                code, summary = run_patrol()
                if code == 0:
                    send(cid, f"✅ Patrol complete — full report above. {summary}")
                else:
                    send(cid, f"❌ Patrol failed (exit {code}): {summary}")
            elif text in ("/yardim", "/help"):
                send(cid, "Vigil bot:\n/check — check all cameras and send the report\n"
                          "You can also just type \"check\" (or \"kontrol et\").")
        time.sleep(1)


if __name__ == "__main__":
    main()
