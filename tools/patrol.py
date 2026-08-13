#!/usr/bin/env python3
"""patrol — yedek/test devriye modu. amele ajanı OLMADAN deterministik çalışır.

Kullanım:
  python3 tools/patrol.py --all    # tüm kameraları gez, Telegram raporu gönder

Neden var: amele + Ollama tool-calling uyumu bozulursa veya ajan döngüsü
takılırsa sistem yine de çalışsın diye. Mantık, agent.yaml'ın birebir aynısı:
kamera gez → analiz et → rapor gönder → anormallik varsa fotoğraf ekle.

Ortam değişkenleri: RTSP_USER, RTSP_PASS, OLLAMA_HOST, AMELE_MODEL,
TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOLS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

from camera_status import SNAP_DIR, analyze, load_cameras, rtsp_url, snapshot  # noqa: E402
import telegram_photo  # noqa: E402
import telegram_send  # noqa: E402

ANOMALI_KELIMELER = [
    "insan", "kişi", "adam", "kadın", "çocuk", "hareket", "yabancı",
    "şüpheli", "açık", "kapı", "araç hareket",
]


def main():
    model = os.environ.get("AMELE_MODEL", "qwen3-vl")
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    cams = load_cameras()
    if not cams:
        print("HATA: cameras.json boş — önce kamera listesini doldur.")
        sys.exit(1)

    satirlar = []
    sorunlar = []
    fotolar = []

    for cam in cams:
        name = cam["name"]
        out = SNAP_DIR / f"{name.replace('/', '_')}.jpg"
        if not snapshot(rtsp_url(cam), out):
            satirlar.append(f"• {name}: 🔴 bağlanamadı")
            sorunlar.append(name)
            continue
        try:
            txt = analyze(out, model, host)
        except Exception as e:  # noqa: BLE001
            satirlar.append(f"• {name}: ⚠️ analiz hatası ({e})")
            sorunlar.append(name)
            continue
        satirlar.append(f"• {name}: {txt}")
        if any(k in txt.lower() for k in ANOMALI_KELIMELER):
            fotolar.append((name, txt))

    if sorunlar:
        baslik = "⚠️ DEVRIYE RAPORU (sorun var)"
    elif fotolar:
        baslik = "⚠️ DEVRIYE RAPORU (dikkat gerektiren görüntü var)"
    else:
        baslik = "✅ DEVRIYE RAPORU — her şey normal"

    rapor = baslik + "\n" + "\n".join(satirlar)
    print(rapor)
    print(telegram_send.send(rapor))

    for name, txt in fotolar:
        (SNAP_DIR / "last.jpg").write_bytes(
            (SNAP_DIR / f"{name.replace('/', '_')}.jpg").read_bytes()
        )
        print(telegram_photo.send_photo(f"{name}: {txt}"))


if __name__ == "__main__":
    main()
