#!/usr/bin/env python3
"""telegram_photo — amele aracı (subprocess tool).

stdin: fotoğrafın alt yazısı (caption)
stdout: "ok" veya hata metni
snapshots/last.jpg dosyasını (son çekilen kareyi) Telegram'dan gönderir.

Ortam değişkenleri: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
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
        return ("HATA: TELEGRAM_BOT_TOKEN ve TELEGRAM_CHAT_ID ortam değişkenleri "
                "eksik — secrets.env'e bak")
    photo = ROOT / "snapshots" / "last.jpg"
    if not photo.exists():
        return "HATA: snapshots/last.jpg yok — önce camera_status çalışmalı"

    boundary = "----devriye"
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
        return f"HATA: Telegram {e.code}: {e.read().decode(errors='replace')[:200]}"


if __name__ == "__main__":
    print(send_photo(sys.stdin.read().strip()))
