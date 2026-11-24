"""把目标图贴到当前画板快照上，检查落点是否压到别人的作品。"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pb import target as tmod  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--board", default="out/board.png")
    ap.add_argument("--image", default="assets/akie.webp")
    ap.add_argument("--size", type=int, default=128)
    ap.add_argument("--place", required=True, help="x,y")
    ap.add_argument("--out", default="out/preview.png")
    a = ap.parse_args()

    x0, y0 = (int(v) for v in a.place.split(","))
    board = np.asarray(Image.open(a.board).convert("RGB"))
    h, w = board.shape[:2]
    colors = tmod.load_target(a.image, a.size)
    rgb = tmod.to_rgb(colors).reshape(a.size, a.size, 3)
    canvas = board.copy()
    canvas[y0:y0 + a.size, x0:x0 + a.size] = rgb
    img = Image.fromarray(canvas)
    d = ImageDraw.Draw(img)
    d.rectangle([x0 - 1, y0 - 1, x0 + a.size, y0 + a.size], outline=(0, 255, 0))
    img.save(a.out)
    print(f"preview -> {a.out}  ({x0},{y0}) size={a.size}")
    # 统计落点区域原有颜色复杂度
    region = board[y0:y0 + a.size, x0:x0 + a.size]
    uniq = len(np.unique(region.reshape(-1, 3), axis=0))
    print(f"落点区域内原有颜色数: {uniq}")


if __name__ == "__main__":
    sys.path.insert(0, ".")
    main()
