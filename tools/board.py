"""直接用 HTTP 拉公共画面快照（晚 public_delay），存成 PNG。"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pb import codec, target as tmod  # noqa: E402
from pb.http_api import UA  # noqa: E402


def fetch_snapshot(server: str):
    req = urllib.request.Request(server.rstrip("/") + "/api/board",
                                 headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
        version = r.headers.get("X-Board-Version")
    p = codec.decompress(raw)
    if p[0] != codec.SNAPSHOT:
        raise SystemExit(f"不是 SNAPSHOT：{p[0]:#x}")
    return codec.decode_snapshot(p), version


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="https://pb.ika.zip")
    ap.add_argument("--out", default="out/board_http.png")
    ap.add_argument("--crop", default="", help="x,y,w,h")
    ap.add_argument("--scale", type=int, default=1)
    a = ap.parse_args()
    snap, ver = fetch_snapshot(a.server)
    print(f"snapshot v{snap.version} (header {ver}) {snap.w}x{snap.h}")
    colors = snap.colors.reshape(snap.h, snap.w)
    if a.crop:
        x, y, w, h = (int(v) for v in a.crop.split(","))
        colors = colors[y:y + h, x:x + w]
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    tmod.save_png(colors.reshape(-1), a.out, colors.shape[1], colors.shape[0],
                  scale=a.scale)
    print("->", a.out)


if __name__ == "__main__":
    main()
