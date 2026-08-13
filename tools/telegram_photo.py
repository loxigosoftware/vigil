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


def _resize_for_upload(src, dst, max_dim=1920):
    """Downscale large snapshots (4K cameras) before upload. A raw 4K JPEG can
    be 1MB+, which regularly exceeds the tool timeout on slower uplinks;
    resized to ~1920px it uploads in seconds. Original snapshot is untouched.
    Uses sips (macOS, always present) with ffmpeg as a fallback; returns False
    if neither works (caller then uploads the original)."""
    import shutil
    import subprocess as _sp
    if src.stat().st_size <= 300_000:
        return False  # small enough already — send as-is
    if shutil.which("sips"):
        r = _sp.run(["sips", "-Z", str(max_dim), "-s", "format", "jpeg",
                     "-s", "formatOptions", "85", str(src), "--out", str(dst)],
                    capture_output=True, timeout=30)
        if r.returncode == 0 and dst.exists() and dst.stat().st_size > 0:
            return True
    if shutil.which("ffmpeg"):
        r = _sp.run(["ffmpeg", "-y", "-v", "error", "-i", str(src),
                     "-vf", f"scale='min({max_dim},iw)':-2", "-q:v", "4", str(dst)],
                    capture_output=True, timeout=30)
        if r.returncode == 0 and dst.exists() and dst.stat().st_size > 0:
            return True
    dst.unlink(missing_ok=True)
    return False


def send_photo(caption, photo, retries=2):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        return ("ERROR: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars are "
                "missing — see secrets.env")
    if not photo.exists():
        return f"ERROR: {photo} missing — run camera_status first"

    # Downscale before upload so 4K frames never hit the tool timeout.
    upload = photo
    tmp = ROOT / "snapshots" / f".upload_{photo.stem[:40]}.jpg"
    if _resize_for_upload(photo, tmp):
        upload = tmp
    try:
        last_err = None
        for attempt in range(retries):
            boundary = "----vigil"
            body = b""
            for field, value in [("chat_id", chat), ("caption", caption)]:
                body += (f"--{boundary}\r\n"
                         f"Content-Disposition: form-data; name=\"{field}\"\r\n\r\n"
                         f"{value}\r\n").encode()
            body += (f"--{boundary}\r\n"
                     f"Content-Disposition: form-data; name=\"photo\"; "
                     f"filename=\"{upload.name}\"\r\n"
                     f"Content-Type: image/jpeg\r\n\r\n").encode()
            body += upload.read_bytes() + b"\r\n--" + boundary.encode() + b"--\r\n"
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
            except Exception as ex:  # network blip — retry
                last_err = f"{type(ex).__name__}: {ex}"
                if attempt < retries - 1:
                    import time
                    time.sleep(2)
        return f"ERROR: upload failed after {retries} attempts ({last_err})"
    finally:
        tmp.unlink(missing_ok=True)


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
