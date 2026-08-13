#!/usr/bin/env python3
"""telegram_photo — amele tool (subprocess).

stdin: photo caption
stdout: "ok" or an error message
Sends snapshots/last.jpg (the most recently captured frame) via Telegram.

Env vars: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""
import os
import pathlib
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent


def send_photo(caption):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        return ("ERROR: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars are "
                "missing — see secrets.env")
    photo = ROOT / "snapshots" / "last.jpg"
    if not photo.exists():
        return "ERROR: snapshots/last.jpg missing — run camera_status first"

    boundary = "----vigil"
    body = b""
    for field, value in [("chat_id", chat), ("caption", caption)]:
        body += (f"--{boundary}\r\n"
                 f"Content-Disposition: form-data; name=\"{field}\"\r\n\r\n"
                 f"{value}\r\n").encode()
    body += (f"--{boundary}\r\n"
             f"Content-Disposition: form-data; name=\"photo\"; "
             f"filename=\"last.jpg\"\r\n"
             f"Content-Type: image/jpeg\r\n\r\n").encode()
    body += photo.read_bytes() + b"\r\n--" + boundary.encode() + b"--\r\n"

    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60):
            return "ok"
    except urllib.error.HTTPError as e:
        return f"ERROR: Telegram {e.code}: {e.read().decode(errors='replace')[:200]}"


if __name__ == "__main__":
    print(send_photo(sys.stdin.read().strip()))
