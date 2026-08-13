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
    "The camera is wide-angle: people, vehicles and animals may appear small "
    "or distant — inspect the whole frame carefully before concluding "
    "'Clear'.\n"
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


def _frame_ok(path):
    """Reject near-uniform (corrupted) JPEGs. Solid pink/gray decodes from a
    broken bitstream have almost no luma variation; a real scene always does."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(path),
             "-vf", "signalstats,metadata=print:file=-",
             "-frames:v", "1", "-f", "null", "-"],
            capture_output=True, text=True, timeout=20)
        lo = hi = None
        for line in (r.stdout or "").splitlines():
            if "YMIN=" in line:
                lo = float(line.split("YMIN=")[1].split()[0])
            elif "YMAX=" in line:
                hi = float(line.split("YMAX=")[1].split()[0])
        return lo is not None and hi is not None and (hi - lo) >= 12
    except Exception:
        return False


def _rtsp_capture(url, out, timeout=15):
    """Pure-python RTSP snapshot.

    A network extension (e.g. a VPN/firewall filter, NECP policy) can drop
    local-network connects from third-party binaries (ffmpeg) when they are
    spawned by launchd — 'No route to host'. Apple-signed processes (python3,
    nc) are not filtered, so we do the RTSP handshake + RTP receive in Python
    and decode the captured H.264/H.265 access unit LOCALLY with ffmpeg from
    a pipe (no network socket involved)."""
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
        challenge = None   # parsed digest challenge, once received

        def _parse_challenge(head):
            chall = "".join(
                ln.split(b":", 1)[1].decode("latin-1") + "\n"
                for ln in head.split(b"\r\n")
                if ln.lower().startswith(b"www-authenticate:"))
            if not chall:
                return None
            import re as _re
            realm = _re.search(r'realm="([^"]*)"', chall)
            nonce = _re.search(r'nonce="([^"]*)"', chall)
            if not realm or not nonce:
                return None
            qop_m = _re.search(r'qop="([^"]*)"', chall)
            opaque = _re.search(r'opaque="([^"]*)"', chall)
            return {
                "digest": "Digest" in chall,
                "realm": realm.group(1),
                "nonce": nonce.group(1),
                "qop": qop_m.group(1).split(",")[0] if qop_m else None,
                "opaque": opaque.group(1) if opaque else None,
            }

        def _auth_header(method, uri):
            if challenge is None or not user:
                return b""
            if not challenge["digest"]:
                return b"Authorization: Basic " + _b64.b64encode(
                    f"{user}:{pw}".encode()) + b"\r\n"
            import hashlib as _hl
            realm, nonce = challenge["realm"], challenge["nonce"]
            ha1 = _hl.md5(f"{user}:{realm}:{pw}".encode()).hexdigest()
            ha2 = _hl.md5(f"{method}:{uri}".encode()).hexdigest()
            if challenge["qop"]:
                cnonce = _hl.md5(os.urandom(8)).hexdigest()[:16]
                resp = _hl.md5(
                    f"{ha1}:{nonce}:00000001:{cnonce}:{challenge['qop']}:{ha2}"
                    .encode()).hexdigest()
                hdr = (f'Authorization: Digest username="{user}", '
                       f'realm="{realm}", nonce="{nonce}", uri="{uri}", '
                       f'qop={challenge["qop"]}, nc=00000001, '
                       f'cnonce="{cnonce}", response="{resp}"')
            else:
                resp = _hl.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()
                hdr = (f'Authorization: Digest username="{user}", '
                       f'realm="{realm}", nonce="{nonce}", uri="{uri}", '
                       f'response="{resp}"')
            if challenge["opaque"]:
                hdr += f', opaque="{challenge["opaque"]}"'
            return (hdr + "\r\n").encode()

        def req(method, cseq, extra=b"", ruri=None):
            nonlocal challenge
            uri = ruri or base
            # header order matters on this camera: extra headers (Accept,
            # Transport, Session) must come BEFORE CSeq/User-Agent, auth last;
            # the UA mimics ffmpeg's — this camera ignores/refuses others
            r = (f"{method} {uri} RTSP/1.0\r\n").encode() + extra + \
                f"CSeq: {cseq}\r\nUser-Agent: Lavf60.3.100\r\n".encode() + \
                _auth_header(method, uri) + b"\r\n"
            s.sendall(r)
            buf = b""
            while b"\r\n\r\n" not in buf:
                c = s.recv(4096)
                if not c:
                    break
                buf += c
            head, _, body = buf.partition(b"\r\n\r\n")
            try:
                status = int(head.split(b" ", 2)[1])
            except Exception:
                return head, body
            if status == 401 and user and challenge is None:
                challenge = _parse_challenge(head)
                return req(method, cseq + 1, extra, uri)
            return head, body

        h, _ = req("OPTIONS", 1)
        h, body = req("DESCRIBE", 2, b"Accept: application/sdp\r\n")
        if not h.startswith(b"RTSP/1.0 200"):
            return False
        sdp = body.decode("latin-1")
        # locate the video media block: its a=control URL and codec
        codec = None
        control = "streamid=0"
        in_video = False
        for line in sdp.splitlines():
            if line.startswith("m="):
                in_video = line.startswith("m=video")
            elif in_video and line.startswith("a=control:"):
                control = line.split(":", 1)[1].strip()
            elif in_video and line.startswith("a=rtpmap:"):
                cname = line.split(":", 1)[1].strip().split(" ")[1].split("/")[0].upper()
                if cname in ("H264", "H265", "HEVC"):
                    codec = "h264" if cname == "H264" else "h265"
        if codec is None:
            return False
        ctrl = control if control.startswith("rtsp://") else (
            base.rstrip("/") + "/" + control.lstrip("/"))
        h, _ = req("SETUP", 3,
                   b"Transport: RTP/AVP/TCP;unicast;interleaved=0-1\r\n",
                   ruri=ctrl)
        if not h.startswith(b"RTSP/1.0 200"):
            return False
        # video channel = first interleaved id from the SETUP response
        vch = 0
        session = None
        for line in h.split(b"\r\n"):
            low = line.lower()
            if low.startswith(b"transport:") and b"interleaved=" in line:
                try:
                    vch = int(line.split(b"interleaved=")[1].split(b"-")[0])
                except Exception:
                    pass
            elif low.startswith(b"session:"):
                session = line.split(b":", 1)[1].strip().split(b";")[0]
        play_extra = (b"Session: " + session + b"\r\n") if session else b""
        h, _ = req("PLAY", 4, play_extra, ruri=base + "/")
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

        es = bytearray()
        params_seen = False
        idr_seen = False
        fu_hdr = None   # H.265 FU header format: 1 (H.264-style) or 2 (RFC 7798)
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
            # proper RTP header: skip CSRCs and any extension
            cc = pkt[0] & 0x0F
            off = 12 + 4 * cc
            if pkt[0] & 0x10 and len(pkt) >= off + 4:
                ext_len = _struct.unpack(">H", pkt[off + 2:off + 4])[0]
                off += 4 + 4 * ext_len
            if len(pkt) <= off:
                continue
            rtp = pkt[off:]
            m_bit = pkt[1] & 0x80
            if codec == "h264":
                nal_type = rtp[0] & 0x1F
                if nal_type == 28:  # FU-A fragmentation
                    fu = rtp[1]
                    start, end = fu & 0x80, fu & 0x40
                    nalu_type = fu & 0x1F
                    if start:
                        es += b"\x00\x00\x00\x01" + bytes(
                            [(rtp[0] & 0xE0) | nalu_type]) + rtp[2:]
                    else:
                        es += rtp[2:]
                    if nalu_type in (7, 8):
                        params_seen = True
                    if nalu_type == 5:
                        idr_seen = True
                    if end and m_bit and nalu_type == 5:
                        break  # IDR frame complete (end of access unit)
                elif nal_type in (1, 5, 6, 7, 8):
                    es += b"\x00\x00\x00\x01" + rtp
                    if nal_type in (7, 8):
                        params_seen = True
                    if nal_type == 5:
                        idr_seen = True
                    if nal_type == 5 and m_bit:
                        break
            else:  # h265 / HEVC
                if len(rtp) < 4:
                    continue
                nal_type = (rtp[0] >> 1) & 0x3F
                if nal_type == 49:  # FU
                    # some cameras (Tapo) send H.265 FU packets with an
                    # H.264-style 1-byte FU header instead of the RFC 7798
                    # 2-byte one — detect which format this stream uses
                    if fu_hdr is None:
                        t1 = rtp[2] & 0x1F
                        t2 = (rtp[2] >> 1) & 0x3F
                        v1 = t1 in (19, 20, 32, 33, 34)
                        v2 = t2 in (19, 20, 32, 33, 34)
                        fu_hdr = 2 if (v2 and not v1) else 1
                    if fu_hdr == 1:  # 1-byte FU header (H.264 style)
                        fu_type = rtp[2] & 0x1F
                        start, end = rtp[2] & 0x80, rtp[2] & 0x40
                        payload = rtp[3:]
                    else:  # 2-byte FU header (RFC 7798)
                        fu_type = (rtp[2] >> 1) & 0x3F
                        start, end = rtp[3] & 0x80, rtp[3] & 0x40
                        payload = rtp[4:]
                    if start:
                        nalu = bytes([(rtp[0] & 0x81) | (fu_type << 1), rtp[1]])
                        es += b"\x00\x00\x00\x01" + nalu + payload
                    else:
                        es += payload
                    if fu_type in (32, 33, 34):
                        params_seen = True
                    if fu_type in (19, 20):
                        idr_seen = True
                    if end and m_bit and fu_type in (19, 20):
                        break  # IDR frame complete (end of access unit)
                elif nal_type in (19, 20, 32, 33, 34, 39, 40):
                    es += b"\x00\x00\x00\x01" + rtp
                    if nal_type in (32, 33, 34):
                        params_seen = True
                    if nal_type in (19, 20):
                        idr_seen = True
                    if nal_type in (19, 20) and m_bit:
                        break
        s.close()
        if not es or not params_seen or not idr_seen:
            return False
        demux = "h264" if codec == "h264" else "hevc"
        out.unlink(missing_ok=True)   # never let a stale file pass the check
        subprocess.run(
            ["ffmpeg", "-y", "-f", demux, "-i", "pipe:0",
             "-frames:v", "1", "-q:v", "2", str(out)],
            input=bytes(es), capture_output=True, timeout=30)
        # ffmpeg may exit non-zero when the bitstream ends at a frame
        # boundary — the decoded frame is still valid; sanity-check the
        # pixels so a corrupted decode is never reported as a live view
        if out.exists() and out.stat().st_size > 5000 and _frame_ok(out):
            return True
        out.unlink(missing_ok=True)
        return False
    except Exception:
        try:
            s.close()
        except Exception:
            pass
        return False


def _capture_via_agent(name, out, timeout=18):
    """Ask the com.vigil.capture LaunchAgent for a frame. That agent is
    spawned directly by launchd (Apple python, outside amele's process
    tree), so its socket is NOT subject to the NECP filter that drops
    amele's children from the local network."""
    try:
        req = SNAP_DIR / "request.txt"
        old = out.stat().st_mtime if out.exists() else 0
        req.write_text(name)
        subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.vigil.capture"],
            capture_output=True, timeout=10,
        )
        deadline = time.time() + timeout
        while time.time() < deadline:
            if out.exists() and out.stat().st_mtime > old:
                return True
            time.sleep(0.5)
        return False
    except Exception:
        return False


def snapshot(url, out, name=None):
    """Capture a single frame. Returns True ONLY if a NEW frame was written —
    never trusts a pre-existing file. Primary path: the capture LaunchAgent
    (its python socket is not filtered by the NECP policy that blocks
    amele-spawned processes from the local network). Fallbacks: in-process
    pure-python RTSP, then ffmpeg directly."""
    if name and _capture_via_agent(name, out):
        return True
    urls = [url]
    alt = url.replace("/stream1", "/stream2")
    if alt != url:
        urls.append(alt)
    for u in urls:
        for attempt in range(2):
            if _rtsp_capture(u, out, timeout=10):
                return True
            r = subprocess.run(
                ["ffmpeg", "-y", "-rtsp_transport", "tcp", "-i", u,
                 "-frames:v", "1", "-q:v", "2", str(out)],
                capture_output=True, timeout=20,
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


def analyze_tiled(path, model, grid=2):
    """Full-frame analysis, then — if it reports Clear/Unclear — a grid of
    upscaled tiles so small/distant objects (a car far from a wide-angle
    camera) are not missed. VISION_TILES=0 disables (full frame only),
    2 = 2x2 tiles, 3 = 3x3 tiles (default 2)."""
    import pathlib as _pl
    import subprocess as _sp

    def probe_size(p):
        try:
            r = _sp.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                         "-show_entries", "stream=width,height",
                         "-of", "csv=p=0", str(p)], capture_output=True, text=True, timeout=10)
            w, h = r.stdout.strip().split(",")[:2]
            return int(w), int(h)
        except Exception:
            return None

    def first_word(t):
        return (t or "").strip().split()[0].lower() if (t or "").strip() else ""

    full = analyze(path, model)
    fw = first_word(full)
    if fw != "clear" and fw != "unclear":
        return full  # ALERT already — tiles not needed
    try:
        tiles = int(os.environ.get("VISION_TILES", "2"))
    except ValueError:
        tiles = 2
    if tiles < 2:
        return full
    size = probe_size(path)
    if not size:
        return full
    w, h = size
    tw, th = w // tiles, h // tiles
    tmp = _pl.Path(f"/tmp/vigil_tile_{os.getpid()}.jpg")
    any_unclear = fw == "unclear"
    try:
        for ty in range(tiles):
            for tx in range(tiles):
                r = _sp.run(
                    ["ffmpeg", "-y", "-v", "error", "-i", str(path),
                     "-vf", (f"crop={tw}:{th}:{tx * tw}:{ty * th},"
                             f"scale=iw*2:ih*2"),
                     "-q:v", "2", str(tmp)],
                    capture_output=True, timeout=20)
                if r.returncode != 0 or not tmp.exists() or tmp.stat().st_size < 5000:
                    continue
                t = analyze(tmp, model)
                tword = first_word(t)
                if tword == "alert":
                    tmp.unlink(missing_ok=True)
                    return t  # first ALERT found wins; caller adds the camera name
                if tword == "unclear":
                    any_unclear = True
        tmp.unlink(missing_ok=True)
        return "Unclear — could not judge the whole frame" if any_unclear else full
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return full


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

    if not snapshot(rtsp_url(cam), out, name):
        print(f"Camera '{name}' ({cam['url']}) unreachable or no frame captured.")
        return  # not an error: the agent should report it

    try:
        text = analyze_tiled(out, model)
    except Exception as e:  # noqa: BLE001 — return text so the agent can report it
        print(f"Camera '{name}' frame captured but vision analysis failed: {e}")
        return

    (SNAP_DIR / "last.jpg").write_bytes(out.read_bytes())
    print(f"[{name}] {text}")


if __name__ == "__main__":
    main()
