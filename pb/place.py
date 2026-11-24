"""选址：用公共画面的「写入频次热力图」找一块安静的 128×128 区域。

公共画面晚 public_delay（正式服 10 分钟 / 测试服 1 分钟），但用来判断
「哪片区域长期没人动」足够了；再加上颜色均匀度做次级排序。
"""
from __future__ import annotations

import numpy as np

from . import target


class Heatmap:
    def __init__(self, w: int, h: int):
        self.w, self.h = w, h
        self.count = np.zeros(w * h, dtype=np.int64)
        self.messages = 0

    def add_indices(self, idx: np.ndarray) -> None:
        if len(idx):
            np.add.at(self.count, idx, 1)

    def window_sums(self, size: int) -> np.ndarray:
        """返回 (h-size+1, w-size+1) 的窗口写入总数。"""
        a = self.count.reshape(self.h, self.w).astype(np.int64)
        ii = np.zeros((self.h + 1, self.w + 1), dtype=np.int64)
        np.cumsum(np.cumsum(a, axis=0), axis=1, out=ii[1:, 1:])
        k = size
        return (ii[k:, k:] - ii[:-k, k:] - ii[k:, :-k] + ii[:-k, :-k])

    def _window_sum_pix(self, a: np.ndarray, size: int) -> np.ndarray:
        h, w = a.shape
        ii = np.zeros((h + 1, w + 1), dtype=np.float64)
        np.cumsum(np.cumsum(a, axis=0), axis=1, out=ii[1:, 1:])
        k = size
        return ii[k:, k:] - ii[:-k, k:] - ii[k:, :-k] + ii[:-k, :-k]

    def best(self, size: int, board: np.ndarray, margin: int = 0,
             prefer_center: float = 0.15) -> dict:
        """综合「活跃度低 + 颜色平滑 + 靠中间」选一个落点。"""
        sums = self.window_sums(size).astype(np.float64)
        b = board.reshape(self.h, self.w)
        # 颜色平滑度：相邻像素不同的比例（边缘密度），边缘密度低的区域更像纯色背景
        edge = np.zeros((self.h, self.w), dtype=np.float64)
        edge[:, :-1] += (b[:, :-1] != b[:, 1:])
        edge[:-1, :] += (b[:-1, :] != b[1:, :])
        edges = self._window_sum_pix(edge, size)

        act = (sums - sums.min()) / (np.ptp(sums) + 1e-9)
        edg = (edges - edges.min()) / (np.ptp(edges) + 1e-9)
        hh, ww = act.shape
        yy, xx = np.mgrid[0:hh, 0:ww]
        cx, cy = (self.w - size) / 2, (self.h - size) / 2
        dist = (np.abs(xx - cx) + np.abs(yy - cy)) / (self.w + self.h)
        score = act + 0.35 * edg + prefer_center * dist

        if margin:
            score[:margin, :] += 10
            score[-margin:, :] += 10
            score[:, :margin] += 10
            score[:, -margin:] += 10

        flat = int(np.argmin(score))
        y, x = divmod(flat, ww)
        order = np.argsort(score, axis=None)[:8]
        cands = []
        for f in order:
            yy2, xx2 = divmod(int(f), ww)
            cands.append({
                "x": int(xx2), "y": int(yy2),
                "activity": int(sums[yy2, xx2]),
                "edge": float(edges[yy2, xx2]),
                "score": float(score[yy2, xx2]),
            })
        return {"x": int(x), "y": int(y), "activity": int(sums[y, x]),
                "candidates": cands}

    def save_png(self, path: str, size: int, scale: int = 4) -> None:
        a = self.count.reshape(self.h, self.w).astype(np.float64)
        a = np.log1p(a)
        if a.max() > 0:
            a = a / a.max()
        rgb = np.stack([a * 255, a * 120, a * 30], axis=-1).astype(np.uint8)
        from PIL import Image
        img = Image.fromarray(rgb, "RGB")
        if scale > 1:
            img = img.resize((self.w * scale // 2, self.h * scale // 2), Image.BILINEAR)
        img.save(path)
