#!/usr/bin/env python3
"""camera_status — amele tool (subprocess).

stdin: camera name (the 'name' in cameras.json)
stdout: analysis of that camera's live view (or an error message)

Captures a single frame from the RTSP stream with ffmpeg, sends it to the
vision model, and returns a short description of what is visible. The last
frame is kept at snapshots/last.jpg (used by the send_telegram_photo tool).

Vision follows the same single provider switch as the agent (secrets.env):
  PROVIDER_TYPE=openai (default) — OpenAI-compatible endpoint: local Ollama
                                   (BASE_URL on localhost) or online (OpenAI,
                                   OpenRouter, vLLM, ...)
  PROVIDER_TYPE=anthropic        — native Anthropic Messages API
  VISION_MODE  auto|ollama|openai|anthropic  (auto: localhost → Ollama native)
  VISION_MODEL (optional) — separate model for image analysis
                            (defaults to AMELE_MODEL)

Env vars: PROVIDER_TYPE, BASE_URL, API_KEY, AMELE_MODEL, VISION_MODEL,
          VISION_MODE, RTSP_USER, RTSP_PASS
"""
import base64
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.request

# macOS + Homebrew: /opt/homebrew/bin is missing from minimal PATHs (launchd/cron/SSH)
if sys.platform == "darwin" and os.path.isdir("/opt/homebrew/bin"):
    os.environ["PATH"] = "/opt/homebrew/bin:" + os.environ.get("PATH", "")

from urllib.parse import quote

ROOT = pathlib.Path(__file__).resolve().parent.parent
SNAP_DIR = ROOT / "snapshots"
SNAP_DIR.mkdir(exist_ok=True)

PROMPT = (
    "This is a home security camera frame. Report ONLY on people, vehicles "
    "and animals — do not describe the scene (no driveways, trees, fences, "
    "weather or buildings).\n"
    "Start with exactly one word: 'Clear' if no person, vehicle or animal is "
    "visible; 'ALERT' if any is visible; 'Unclear' if you cannot tell.\n"
    "Then give details only for what is present:\n"
    "- people: count, gender, age range (e.g. '2 people: adult male 30-40, "
    "child 5-10')\n"
    "- vehicles: type and color, license plate if readable (e.g. 'white "
    "sedan, plate 34ABC123')\n"
    "- animals: cat or dog, breed if identifiable (e.g. 'orange cat, breed "
    "unclear')\n"
    "Examples: 'Clear' | 'ALERT — one adult female, 25-35' | 'ALERT — black "
    "pickup truck, plate unreadable'"
)


def load_cameras():
    with open(ROOT / "cameras.json", encoding="utf-8") as f:
        return json.load(f)


def rtsp_url(cam):
    """Inject RTSP_USER/RTSP_PASS into the URL (unless already embedded)."""
    url = cam["url"]
    user = os.environ.get("RTSP_USER", "")
    pw = os.environ.get("RTSP_PASS", "")
    if user and url.startswith("rtsp://") and "@" not in url:
        # percent-encode so special chars in passwords (#, !, @, ...) survive
        url = url.replace("rtsp://", f"rtsp://{quote(user, safe='')}:{quote(pw, safe='')}@", 1)
    return url


def _debug(msg):
    """TEMP: append diagnostics to logs/cs_debug.log (removed after diagnosis)."""
    try:
        (ROOT / "logs").mkdir(exist_ok=True)
        with open(ROOT / "logs" / "cs_debug.log", "a") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def _rtsp_capture(url, out, timeout=15):
    """Pure-python RTSP snapshot.

    A network extension (Tailscale's, NECP policy) drops local-network
    connects from third-party binaries (ffmpeg) when they are spawned by
    launchd — 'No route to host'. Apple-signed processes (python3, nc) are
    not filtered, so we do the RTSP handshake + RTP receive in Python and
    decode the captured H.264 access unit LOCALLY with ffmpeg from a pipe
    (no network socket involved)."""
    import base64 as _b64
    import socket as _socket
    import struct as _struct
    from urllib.parse import unquote as _unquote
    from urllib.parse import urlparse as _urlparse
    try:
        u = _urlparse(url)
        host, port = u.hostname, (u.port or 554)
        path = u.path or "/"
        user, pw = _unquote(u.username or ""), _unquote(u.password or "")
        base = f"rtsp://{host}:{port}{path}"
        s = _socket.create_connection((host, port), timeout=timeout)
        s.settimeout(timeout)
        auth = b""

        def req(method, cseq, extra=b""):
            nonlocal auth
            r = (f"{method} {base} RTSP/1.0\r\nCSeq: {cseq}\r\n"
                 f"User-Agent: vigil\r\n").encode() + auth + extra + b"\r\n"
            s.sendall(r)
            buf = b""
            while b"\r\n\r\n" not in buf:
                c = s.recv(4096)
                if not c:
                    break
                buf += c
            head, _, body = buf.partition(b"\r\n\r\n")
            status = int(head.split(b" ", 2)[1])
            if status == 401 and user and not auth:
                auth = (b"Authorization: Basic "
                        + _b64.b64encode(f"{user}:{pw}".encode()) + b"\r\n")
                return req(method, cseq, extra)
            return head, body

        h, _ = req("OPTIONS", 1)
        h, body = req("DESCRIBE", 2, b"Accept: application/sdp\r\n")
        if not h.startswith(b"RTSP/1.0 200"):
            return False
        sdp = body.decode("latin-1")
        control = "streamid=0"
        for line in sdp.splitlines():
            if line.startswith("a=control:"):
                control = line.split(":", 1)[1].strip()
        ctrl = control if control.startswith("rtsp://") else (
            base.rstrip("/") + "/" + control.lstrip("/"))
        h, _ = req("SETUP", 3,
                   b"Transport: RTP/AVP/TCP;unicast;interleaved=0-1\r\n")
        if not h.startswith(b"RTSP/1.0 200"):
            return False
        # video channel = first interleaved id from the SETUP response
        vch = 0
        for line in h.split(b"\r\n"):
            if line.lower().startswith(b"transport:") and b"interleaved=" in line:
                try:
                    vch = int(line.split(b"interleaved=")[1].split(b"-")[0])
                except Exception:
                    pass
        h, _ = req("PLAY", 4)
        if not h.startswith(b"RTSP/1.0 200"):
            return False

        def recv_exact(n):
            buf = b""
            while len(buf) < n:
                c = s.recv(n - len(buf))
                if not c:
                    raise EOFError
                buf += c
            return buf

        h264 = bytearray()
        seen_sps = False
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                hdr = recv_exact(4)
            except (EOFError, _socket.timeout, OSError):
                break
            if hdr[0] != 0x24:
                continue
            ln = _struct.unpack(">H", hdr[2:4])[0]
            pkt = recv_exact(ln)
            if hdr[1] != vch or len(pkt) < 12:
                continue
            rtp = pkt[12:]
            nal_type = rtp[0] & 0x1F
            m_bit = pkt[1] & 0x80
            if nal_type == 28:  # FU-A fragmentation
                fu = rtp[1]
                start, end = fu & 0x80, fu & 0x40
                nalu_type = fu & 0x1F
                if start:
                    h264 += b"\x00\x00\x00\x01" + bytes(
                        [(rtp[0] & 0xE0) | nalu_type]) + rtp[2:]
                else:
                    h264 += rtp[2:]
                if nalu_type == 7:
                    seen_sps = True
                if end and nalu_type == 5:
                    break
            elif nal_type in (1, 5, 6, 7, 8):
                h264 += b"\x00\x00\x00\x01" + rtp
                if nal_type == 7:
                    seen_sps = True
                if nal_type == 5 and m_bit:
                    break
        s.close()
        if not h264 or not seen_sps:
            return False
        p = subprocess.run(
            ["ffmpeg", "-y", "-f", "h264", "-i", "pipe:0",
             "-frames:v", "1", "-q:v", "2", str(out)],
            input=bytes(h264), capture_output=True, timeout=30)
        return p.returncode == 0 and out.exists()
    except Exception as e:
        try:
            s.close()
        except Exception:
            pass
        _debug(f"[rtsp_capture] {url[:40]}... err={e!r}")
        return False


def snapshot(url, out):
    """Capture a single frame. Returns True ONLY if a NEW frame was written —
    never trusts a pre-existing file. Primary path: pure-python RTSP (works
    under launchd; the NECP filter drops ffmpeg's own local connects).
    Fallback: ffmpeg directly (works in contexts without the filter)."""
    urls = [url]
    alt = url.replace("/stream1", "/stream2")
    if alt != url:
        urls.append(alt)
    for u in urls:
        for attempt in range(2):
            if _rtsp_capture(u, out):
                return True
            r = subprocess.run(
                ["ffmpeg", "-y", "-rtsp_transport", "tcp", "-i", u,
                 "-frames:v", "1", "-q:v", "2", str(out)],
                capture_output=True, timeout=30,
            )
            if r.returncode == 0 and out.exists():
                return True
            # failed: remove any stale leftover so it is never mistaken for
            # a fresh frame
            out.unlink(missing_ok=True)
            time.sleep(2)
    return False


def base_url():
    return os.environ.get("BASE_URL", "").strip() or "http://localhost:11434/v1"


def is_local(url):
    return any(h in url for h in ("localhost", "127.0.0.1", "0.0.0.0", "::1"))


def ollama_host():
    """Ollama's native API root: BASE_URL minus a trailing /v1."""
    url = base_url()
    if url.endswith("/v1"):
        url = url[:-3]
    return url.rstrip("/")


def analyze(path, model):
    """Send the image to the vision provider; return the description text."""
    b64 = base64.b64encode(path.read_bytes()).decode()
    mode = os.environ.get("VISION_MODE", "auto").strip().lower()
    ptype = os.environ.get("PROVIDER_TYPE", "openai").strip().lower()
    if mode == "auto":
        mode = "ollama" if (ptype != "anthropic" and is_local(base_url())) else ptype
    if mode == "ollama":
        return _ollama(b64, model)
    if mode == "anthropic":
        return _anthropic(b64, model)
    return _openai(b64, model)


def _post(url, payload, headers, timeout=90):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _ollama(b64, model):
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT, "images": [b64]}],
        "stream": False,
        "keep_alive": -1,  # keep the vision model resident in memory
        "options": {"temperature": 0.1},
    }
    data = _post(f"{ollama_host()}/api/chat", payload, {})
    return data["message"]["content"].strip()


def _openai(b64, model):
    api_key = os.environ.get("API_KEY", "")
    if not api_key:
        return "ERROR: API_KEY missing — set it in secrets.env for online vision"
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        }],
        "max_tokens": 1024,
        "temperature": 0.1,
    }
    data = _post(f"{base_url()}/chat/completions", payload,
                 {"Authorization": f"Bearer {api_key}"})
    return data["choices"][0]["message"]["content"].strip()


def _anthropic(b64, model):
    api_key = os.environ.get("API_KEY", "")
    if not api_key:
        return "ERROR: API_KEY missing — set it in secrets.env for Anthropic vision"
    base = base_url().rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    payload = {
        "model": model,
        "max_tokens": 1024,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/jpeg", "data": b64}},
            ],
        }],
    }
    data = _post(f"{base}/v1/messages", payload, {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    })
    return " ".join(b.get("text", "") for b in data.get("content", [])).strip()


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

    model = os.environ.get("VISION_MODEL", "") or os.environ.get("AMELE_MODEL", "qwen3-vl")
    out = SNAP_DIR / f"{name.replace('/', '_')}.jpg"

    if not snapshot(rtsp_url(cam), out):
        print(f"Camera '{name}' ({cam['url']}) unreachable or no frame captured.")
        return  # not an error: the agent should report it

    try:
        text = analyze(out, model)
    except Exception as e:  # noqa: BLE001 — return text so the agent can report it
        print(f"Camera '{name}' frame captured but vision analysis failed: {e}")
        return

    (SNAP_DIR / "last.jpg").write_bytes(out.read_bytes())
    print(f"[{name}] {text}")


if __name__ == "__main__":
    main()
