#!/usr/bin/env python3
"""telegram_photo — amele tool (subprocess).

stdin: camera name on the FIRST line, then the caption (photo text) on the
following line(s). Sends snapshots/{camera_name}.jpg (fallback: last.jpg)
via Telegram with that caption.

Example stdin:
    Main Entry New
    Main Entry New: clear — empty driveway

Env vars: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent


def send_photo(caption, photo):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        return ("ERROR: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars are "
                "missing — see secrets.env")
    if not photo.exists():
        return f"ERROR: {photo} missing — run camera_status first"

    boundary = "----vigil"
    body = b""
    for field, value in [("chat_id", chat), ("caption", caption)]:
        body += (f"--{boundary}\r\n"
                 f"Content-Disposition: form-data; name=\"{field}\"\r\n\r\n"
                 f"{value}\r\n").encode()
    body += (f"--{boundary}\r\n"
             f"Content-Disposition: form-data; name=\"photo\"; "
             f"filename=\"{photo.name}\"\r\n"
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
    text = sys.stdin.read().strip()
    lines = [ln for ln in text.splitlines() if ln.strip()]
    photo = None
    caption = text
    if lines:
        cam_name = lines[0].strip()
        cand = ROOT / "snapshots" / f"{cam_name.replace('/', '_')}.jpg"
        if not cand.exists():
            # never fall back to another camera's (possibly stale) frame
            print(f"ERROR: no snapshot for '{cam_name}' — camera_status failed for it")
            sys.exit(1)
        photo = cand
        status = "\n".join(lines[1:]).strip()
        if status:
            # guard: never let a literal "camera name:" template slip through
            status = re.sub(r"^camera\s+name\s*:\s*", "", status, flags=re.I).strip()
            caption = f"{cam_name}: {status}"
        else:
            caption = cam_name
    if photo is None:
        print("ERROR: expected the camera name on stdin")
        sys.exit(1)
    print(send_photo(caption, photo))
