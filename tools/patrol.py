#!/usr/bin/env python3
"""patrol — fallback/test patrol mode. Runs deterministically WITHOUT the amele agent.

Usage:
  python3 tools/patrol.py --all    # walk every camera, send the report via Telegram

Why it exists: if amele + Ollama tool-calling compatibility breaks, or the
agent loop gets stuck, the system should still work. The logic mirrors
agent.yaml exactly: walk cameras → analyze → send report → attach photos for
anomalies.

Env vars: RTSP_USER, RTSP_PASS, OLLAMA_HOST, AMELE_MODEL,
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

ANOMALY_KEYWORDS = [
    "person", "man", "woman", "child", "motion", "movement", "stranger",
    "suspicious", "open", "door", "vehicle",
]


def main():
    model = os.environ.get("AMELE_MODEL", "qwen3-vl")
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    cams = load_cameras()
    if not cams:
        print("ERROR: cameras.json is empty — add your cameras first.")
        sys.exit(1)

    lines = []
    issues = []
    photos = []

    for cam in cams:
        name = cam["name"]
        out = SNAP_DIR / f"{name.replace('/', '_')}.jpg"
        if not snapshot(rtsp_url(cam), out):
            lines.append(f"• {name}: 🔴 unreachable")
            issues.append(name)
            continue
        try:
            txt = analyze(out, model)
        except Exception as e:  # noqa: BLE001
            lines.append(f"• {name}: ⚠️ analysis error ({e})")
            issues.append(name)
            continue
        lines.append(f"• {name}: {txt}")
        if any(k in txt.lower() for k in ANOMALY_KEYWORDS):
            photos.append((name, txt))

    if issues:
        heading = "⚠️ PATROL REPORT (issues found)"
    elif photos:
        heading = "⚠️ PATROL REPORT (something needs attention)"
    else:
        heading = "✅ PATROL REPORT — all normal"

    report = heading + "\n" + "\n".join(lines)
    print(report)
    print(telegram_send.send(report))

    for name, txt in photos:
        photo = SNAP_DIR / f"{name.replace('/', '_')}.jpg"
        (SNAP_DIR / "last.jpg").write_bytes(photo.read_bytes())
        print(telegram_photo.send_photo(f"{name}: {txt}", photo))


if __name__ == "__main__":
    main()
