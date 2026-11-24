"""把若干矩形涂成同一颜色（用于清理旧图残留 / 擦除）。"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pb.http_api import Server  # noqa: E402
from pb.runner import Painter, Token  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="https://pb.ika.zip")
    ap.add_argument("--rects", required=True, help='"x,y,w,h;x,y,w,h"')
    ap.add_argument("--color", default="#ffffff")
    ap.add_argument("--tokens", type=int, default=64)
    ap.add_argument("--uid-base", type=int, default=910000000)
    ap.add_argument("--tokens-file", default="out/tokens.json")
    a = ap.parse_args()

    srv = Server(a.server)
    meta = srv.fetch_meta()
    w = int(meta["board"]["width"])
    h = int(meta["board"]["height"])
    with open(a.tokens_file, encoding="utf-8") as f:
        cache = json.load(f)

    pixels: list[int] = []
    for part in a.rects.split(";"):
        x, y, rw, rh = (int(v) for v in part.split(","))
        for yy in range(y, min(y + rh, h)):
            for xx in range(x, min(x + rw, w)):
                pixels.append(yy * w + xx)
    c = a.color.lstrip("#")
    color = int(c[4:6] + c[2:4] + c[0:2], 16) | 0xFF000000
    route = [(p, color) for p in pixels]
    print(f"[fill] {len(route)} 像素 -> {a.color}")

    tokens = []
    for i in range(a.tokens):
        uid = a.uid_base + i
        if str(uid) not in cache:
            raise SystemExit(f"缺少 uid {uid} 的 token，先跑一次 draw 领取")
        tokens.append(Token(uid=uid, raw=uuid.UUID(cache[str(uid)]).bytes, slot=i))

    painter = Painter(a.server, meta, None, 0, 0, tokens, once=True, route=route)
    await painter.start()


if __name__ == "__main__":
    asyncio.run(main())
