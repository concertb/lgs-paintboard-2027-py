"""破坏测试：往图里涂一块别的颜色，然后量「守图」要多久把它改回来。

注意服务器公共画面晚 public_delay（测试服 60 s / 正式服 600 s），所以测量值 =
真实修复时间 + 公共延迟，只能算上界。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pb import target as tmod  # noqa: E402
from pb.http_api import Server  # noqa: E402
from pb.runner import Painter, Token  # noqa: E402
from tools.board import fetch_snapshot  # noqa: E402


async def paint_patch(server: str, rect: tuple[int, int, int, int], color: str,
                      uid_base: int, tokens_file: str) -> None:
    srv = Server(server)
    meta = srv.fetch_meta()
    w, h = int(meta["board"]["width"]), int(meta["board"]["height"])
    x, y, rw, rh = rect
    c = color.lstrip("#")
    col = int(c[4:6] + c[2:4] + c[0:2], 16) | 0xFF000000
    route = [(yy * w + xx, col) for yy in range(y, y + rh) for xx in range(x, x + rw)]

    cache = {}
    if os.path.exists(tokens_file):
        with open(tokens_file, encoding="utf-8") as f:
            cache = json.load(f)
    tokens = []
    for i in range(4):
        uid = uid_base + i
        if str(uid) not in cache:
            r = srv.mint(uid)
            cache[str(uid)] = r["paint_token"]
        tokens.append(Token(uid=uid, raw=uuid.UUID(cache[str(uid)]).bytes, slot=i))
    with open(tokens_file, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=1)

    painter = Painter(server, meta, None, 0, 0, tokens, once=True, route=route)
    await painter.start()


def mismatch(board: np.ndarray, target: np.ndarray, x0: int, y0: int) -> np.ndarray:
    size = target.shape[0]
    reg = board[y0:y0 + size, x0:x0 + size]
    return reg != target


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="https://pb.ika.zip")
    ap.add_argument("--image", default="assets/akie.webp")
    ap.add_argument("--size", type=int, default=120)
    ap.add_argument("--place", default="440,1")
    ap.add_argument("--rect", default="", help="破坏区域 x,y,w,h（相对图片）")
    ap.add_argument("--color", default="#000000")
    ap.add_argument("--uid-base", type=int, default=920000000)
    ap.add_argument("--tokens-file", default="out/tokens_attack.json")
    a = ap.parse_args()

    x0, y0 = (int(v) for v in a.place.split(","))
    target = tmod.load_target(a.image, a.size)
    if a.rect:
        rx, ry, rw, rh = (int(v) for v in a.rect.split(","))
        print(f"[attack] 涂黑 ({rx},{ry}) {rw}x{rh}")
        await paint_patch(a.server, (x0 + rx, y0 + ry, rw, rh), a.color,
                          a.uid_base, a.tokens_file)
        t0 = time.time()
    else:
        t0 = time.time()

    print("[watch] 轮询公共画面，直到被破坏区域恢复…")
    for _ in range(30):
        await asyncio.sleep(20)
        snap, _ = fetch_snapshot(a.server)
        board = snap.colors.reshape(snap.h, snap.w)
        d = mismatch(board, target, x0, y0)
        if a.rect:
            rx, ry, rw, rh = (int(v) for v in a.rect.split(","))
            sub = d[ry:ry + rh, rx:rx + rw]
            print(f"  +{time.time()-t0:5.0f}s  patch 不一致 {int(sub.sum())}/{rw*rh}，"
                  f"全图不一致 {int(d.sum())}")
            if sub.sum() == 0:
                print(f"[done] 修复完成，上界 {time.time()-t0:.0f}s")
                return
        else:
            print(f"  +{time.time()-t0:5.0f}s  全图不一致 {int(d.sum())}")
    print("[timeout] 5 分钟内未恢复")


if __name__ == "__main__":
    asyncio.run(main())
