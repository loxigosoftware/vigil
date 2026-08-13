#!/usr/bin/env python3
"""camera_status — amele tool (subprocess).

stdin: camera name (the 'name' in cameras.json)
stdout: analysis of that camera's live view (or an error message)

Captures a single frame from the RTSP stream with ffmpeg, sends it to the
vision model in Ollama, and returns a short description of what is visible.
The last frame is kept at snapshots/last.jpg (used by the send_telegram_photo
tool).

Env vars: RTSP_USER, RTSP_PASS, OLLAMA_HOST, AMELE_MODEL
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
    """Inject RTSP_USER/RTSP_PASS into the URL (unless already embedded)."""
    url = cam["url"]
    user = os.environ.get("RTSP_USER", "")
    pw = os.environ.get("RTSP_PASS", "")
    if user and url.startswith("rtsp://") and "@" not in url:
        url = url.replace("rtsp://", f"rtsp://{user}:{pw}@", 1)
    return url


def snapshot(url, out):
    """Capture a single frame with ffmpeg. TCP transport (UDP fails behind NAT/firewalls)."""
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
    """Send the image to Ollama /api/chat and return the description text."""
    b64 = base64.b64encode(path.read_bytes()).decode()
    prompt = (
        "This is a home security camera frame. Describe it briefly in 1-2 "
        "sentences: is there a person, an animal, motion, an anomaly, or a "
        "vehicle? If unsure, say 'unclear'."
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
        print("ERROR: expected a camera name on stdin (a 'name' from cameras.json)")
        sys.exit(1)

    cams = {c["name"]: c for c in load_cameras()}
    cam = cams.get(name)
    if not cam:
        names = ", ".join(cams) or "(list is empty)"
        print(f"ERROR: '{name}' not found in cameras.json. Known cameras: {names}")
        sys.exit(1)

    model = os.environ.get("AMELE_MODEL", "qwen3-vl")
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    out = SNAP_DIR / f"{name.replace('/', '_')}.jpg"

    if not snapshot(rtsp_url(cam), out):
        print(f"Camera '{name}' ({cam['url']}) unreachable or no frame captured.")
        return  # not an error: the agent should report it

    try:
        text = analyze(out, model, host)
    except Exception as e:  # noqa: BLE001 — return text so the agent can report it
        print(f"Camera '{name}' frame captured but vision analysis failed: {e}")
        return

    (SNAP_DIR / "last.jpg").write_bytes(out.read_bytes())
    print(f"[{name}] {text}")


if __name__ == "__main__":
    main()
