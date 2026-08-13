#!/usr/bin/env python3
"""vigil Telegram botu — "kontrol et" deyince kamera devriyesini tetikler.

Uzun süreli çalışır (long-polling). Yalnızca TELEGRAM_CHAT_ID sahibine yanıt
verir. Tetiklenince bin/amele run agent.yaml ... çalıştırır; ajan raporu
kendisi send_telegram ile gönderir, bot sadece başlangıç/bitiş bilgisini verir.

Çalıştırma:  python3 bot/telegram_bot.py
Zamanlayıcı: deploy/kur-launchd.sh (sürekli ayakta tutar)

Ortam değişkenleri: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (secrets.env)
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
    """secrets.env'i ortama yükle (varsa)."""
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
        print(f"Telegram API hatası {e.code}: {e.read().decode(errors='replace')[:200]}")
        return None
    except Exception as e:  # noqa: BLE001
        print(f"Telegram API hatası: {e}")
        return None


def send(chat_id, text):
    api("sendMessage", chat_id=chat_id, text=text)


def run_patrol():
    """amele ajanını çalıştır; (çıkış kodu, özet) döndür."""
    amele = ROOT / "bin" / "amele"
    cmd = [str(amele), "run", "agent.yaml",
           "Kamera devriyesi yap ve raporu Telegram'dan gönder."]
    try:
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=600)
        ozet = (p.stdout or "").strip().splitlines()[-1:] + (p.stderr or "").strip().splitlines()[-1:]
        return p.returncode, " | ".join(ozet)[:300]
    except FileNotFoundError:
        return -1, "bin/amele bulunamadı — önce deploy/install.sh çalıştır"
    except subprocess.TimeoutExpired:
        return -2, "devriye zaman aşımı (600s)"


def main():
    load_env()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token:
        print("HATA: TELEGRAM_BOT_TOKEN eksik — secrets.env'i doldur.")
        sys.exit(1)
    if not chat:
        print("HATA: TELEGRAM_CHAT_ID eksik. Botu doldur, kendine bir mesaj at, "
              "secrets.env.example'daki komutla ID'yi bul, sonra tekrar başlat.")
        sys.exit(1)

    print("Vigil botu dinliyor... (Ctrl+C ile durdurur)")
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
                continue  # yalnızca sahip
            text = (msg.get("text") or "").strip().lower()
            if text in ("/kontrol", "/devriye", "/start") or "kontrol" in text or "devriye" in text:
                send(cid, "🕐 Devriye başladı — 14 kamerayı gezmem birkaç dakika sürebilir.")
                code, ozet = run_patrol()
                if code == 0:
                    send(cid, f"✅ Devriye tamamlandı — ayrıntılı rapor yukarıda. {ozet}")
                else:
                    send(cid, f"❌ Devriye hatası (çıkış {code}): {ozet}")
            elif text in ("/yardim", "/help"):
                send(cid, "Vigil botu:\n/kontrol — kameraları kontrol et ve rapor gönder\n"
                          "Not: 'kontrol et' yazman da yeterli.")
        time.sleep(1)


if __name__ == "__main__":
    main()
