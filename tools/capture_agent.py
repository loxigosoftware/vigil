#!/usr/bin/env python3
"""capture_agent — on-demand frame capture, spawned DIRECTLY by launchd.

The patrol/bot run camera_status inside amele's process tree, and a network
extension (NECP policy) drops local-network connects from those processes
("No route to host"). This agent is launched straight from launchd with
Apple's /usr/bin/python3, so its socket is not filtered.

Protocol (one shot per kickstart):
  - camera_status writes the camera name to snapshots/request.txt
  - camera_status runs: launchctl kickstart gui/<uid>/com.vigil.capture
  - this script reads request.txt, captures a fresh frame to
    snapshots/<name>.jpg, deletes request.txt, exits 0/1.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SNAP = ROOT / "snapshots"
sys.path.insert(0, str(ROOT / "tools"))

import camera_status as cs  # noqa: E402  (reuses _rtsp_capture / rtsp_url)


def main():
    req = SNAP / "request.txt"
    try:
        name = req.read_text().strip()
    except Exception:
        return 2
    req.unlink(missing_ok=True)
    cams = {c["name"]: c for c in cs.load_cameras()}
    cam = cams.get(name)
    if not cam:
        return 3
    out = SNAP / f"{name.replace('/', '_')}.jpg"
    url = cs.rtsp_url(cam)
    urls = [url]
    alt = url.replace("/stream1", "/stream2")
    if alt != url:
        urls.append(alt)
    ok = False
    for u in urls:
        for attempt in range(2):
            if cs._rtsp_capture(u, out, timeout=12):
                ok = True
                break
        if ok:
            break
    print("ok" if ok else "failed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
