#!/usr/bin/env python3
"""camera_status — amele aracı (subprocess tool).

stdin: kamera adı (cameras.json'daki 'name')
stdout: o kameranın canlı görüntüsünün Türkçe analizi (veya hata metni)

RTSP akışından ffmpeg ile tek kare çeker, Ollama'daki görüntü modeline
gönderir, ne görüldüğünü kısaca döndürür. Son kare snapshots/last.jpg
olarak saklanır (send_telegram_photo aracı onu gönderir).

Ortam değişkenleri: RTSP_USER, RTSP_PASS, OLLAMA_HOST, AMELE_MODEL
"""
import base64
import json
import os
import pathlib
import subprocess
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
SNAP_DIR = ROOT / "snapshots"
SNAP_DIR.mkdir(exist_ok=True)


def load_cameras():
    with open(ROOT / "cameras.json", encoding="utf-8") as f:
        return json.load(f)


def rtsp_url(cam):
    """cameras.json'daki url'ye RTSP_USER/RTSP_PASS'i ekler (url'de yoksa)."""
    url = cam["url"]
    user = os.environ.get("RTSP_USER", "")
    pw = os.environ.get("RTSP_PASS", "")
    if user and url.startswith("rtsp://") and "@" not in url:
        url = url.replace("rtsp://", f"rtsp://{user}:{pw}@", 1)
    return url


def snapshot(url, out):
    """ffmpeg ile tek kare. TCP taşıması (NAT/firewall'da UDP başarısız olur)."""
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-rtsp_transport", "tcp", "-i", url,
             "-frames:v", "1", "-q:v", "2", str(out)],
            capture_output=True, timeout=45,
        )
    except subprocess.TimeoutExpired:
        return False
    return out.exists()


def analyze(path, model, host):
    """Görüntüyü Ollama /api/chat'e gönder, metin döndür."""
    b64 = base64.b64encode(path.read_bytes()).decode()
    prompt = (
        "Bu bir ev güvenlik kamerası karesi. Türkçe olarak 1-2 cümlede kısaca "
        "anlat: insan var mı, hayvan var mı, hareket veya anormallik var mı, "
        "araç var mı? Emin değilsen 'belirsiz' de."
    )
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt, "images": [b64]}],
        "stream": False,
        "options": {"temperature": 0.1},
    }
    req = urllib.request.Request(
        f"{host}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read())
    return data["message"]["content"].strip()


def main():
    name = sys.stdin.read().strip()
    if not name:
        print("HATA: stdin'den kamera adı bekleniyor (cameras.json'daki 'name')")
        sys.exit(1)

    cams = {c["name"]: c for c in load_cameras()}
    cam = cams.get(name)
    if not cam:
        names = ", ".join(cams) or "(liste boş)"
        print(f"HATA: '{name}' cameras.json'da yok. Mevcut kameralar: {names}")
        sys.exit(1)

    model = os.environ.get("AMELE_MODEL", "qwen3-vl")
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    out = SNAP_DIR / f"{name.replace('/', '_')}.jpg"

    if not snapshot(rtsp_url(cam), out):
        print(f"Kamera '{name}' ({cam['url']}) bağlanamadı veya kare alınamadı.")
        return  # hata değil: ajan bunu rapora işlesin

    try:
        text = analyze(out, model, host)
    except Exception as e:  # noqa: BLE001 — ajanın raporlayabilmesi için metin dön
        print(f"Kamera '{name}' karesi alındı ama görüntü analizi başarısız: {e}")
        return

    (SNAP_DIR / "last.jpg").write_bytes(out.read_bytes())
    print(f"[{name}] {text}")


if __name__ == "__main__":
    main()
