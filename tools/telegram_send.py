#!/usr/bin/env python3
"""telegram_send — amele aracı (subprocess tool).

stdin: gönderilecek metin
stdout: "ok" veya hata metni
--test: sabit bir test mesajı gönderir (kurulum doğrulaması için)

Ortam değişkenleri: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
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
        return ("HATA: TELEGRAM_BOT_TOKEN ve TELEGRAM_CHAT_ID ortam değişkenleri "
                "eksik — secrets.env'e bak")
    # HTML kaçışı (parse_mode=HTML kullanıyoruz)
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
        return f"HATA: Telegram {e.code}: {e.read().decode(errors='replace')[:200]}"


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print(send("🧪 Devriye test mesajı — bağlantı çalışıyor."))
    else:
        print(send(sys.stdin.read().strip()))
