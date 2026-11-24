"""在建 60 秒内统计某个矩形区域的写入次数，用来判断「这片区域有没有人在维护」。"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pb import codec  # noqa: E402
from pb.conn import Conn  # noqa: E402
from pb.http_api import Server  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="https://pb.ika.zip")
    ap.add_argument("--rect", required=True, help="x,y,w,h")
    ap.add_argument("--seconds", type=float, default=60)
    a = ap.parse_args()
    x, y, w, h = (int(v) for v in a.rect.split(","))
    meta = Server(a.server).fetch_meta()
    W, H = int(meta["board"]["width"]), int(meta["board"]["height"])
    count = np.zeros(W * H, dtype=np.int64)
    t0 = time.time()
    msgs = [0]

    def on_payload(p: bytes) -> None:
        if not p or p[0] != codec.PUBLIC:
            return
        msgs[0] += 1
        m = codec.decode_public(p)
        for idx, col in codec.public_segments(m, W, H):
            xs = idx % W
            ys = idx // W
            mask = (xs >= x) & (xs < x + w) & (ys >= y) & (ys < y + h)
            sel = idx[mask]
            if len(sel):
                np.add.at(count, sel, 1)

    conn = Conn(a.server, "/ws", on_payload, public=True, name="scan")
    task = asyncio.create_task(conn.run())
    await asyncio.sleep(a.seconds)
    await conn.close()
    task.cancel()
    sub = count.reshape(H, W)[y:y + h, x:x + w]
    print(f"窗口 ({x},{y}) {w}x{h}：{a.seconds:.0f}s 内 {msgs[0]} 条 PUBLIC，"
          f"写入 {int(sub.sum())} 次，覆盖 {int((sub > 0).sum())} 像素")
    per_row = sub.sum(axis=1)
    nz = [(i + y, int(v)) for i, v in enumerate(per_row) if v]
    print("按行：", nz[:30])
    print(f"耗时 {time.time()-t0:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
