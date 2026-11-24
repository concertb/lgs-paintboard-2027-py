"""世界模型：公共画面 + 两个历史层 + 声呐表 + 画笔表。

这是整个客户端唯一可信的状态镜像。所有上层决策只读这里，不自己猜。
规则见 protocol.md §1.5、§3.7.2、§3.7.3、§3.8。
"""
from __future__ import annotations

from collections import deque

import numpy as np

from . import codec


class World:
    def __init__(self, w: int, h: int):
        self.w = w
        self.h = h
        n = w * h
        # 公共画面（永久晚 public_delay）
        self.board = np.zeros(n, dtype="<u4")
        # 历史层：l1 = 上一次变色前的颜色，l2 = 更早的另一个颜色；等于当前色表示空
        self.l1 = np.zeros(n, dtype="<u4")
        self.l2 = np.zeros(n, dtype="<u4")
        self.V = 0
        # 声呐表：像素 -> (实时颜色, tick)。Kt 为 0 表示没有声呐数据。
        self.Kc = np.zeros(n, dtype="<u4")
        self.Kt = np.zeros(n, dtype="<u8")
        self.kq: deque[tuple[int, int]] = deque()
        self.radius = 2
        # 槽位 -> 画笔 (idx, t_us) / None
        self.brushes: dict[int, tuple[int, int] | None] = {}
        self.clock_offset_us = 0
        self.public_msgs = 0
        self.ticks_seen = 0
        # 最近一条 PUBLIC 里被写过的像素（用于守图时更新「可疑像素」）
        self.changed = np.zeros(0, dtype=np.int64)

    # ------------------------------------------------------------ 快照
    def apply_snapshot(self, snap: codec.Snapshot) -> None:
        self.board = snap.colors
        if snap.l1 is not None:
            self.l1 = snap.l1
            self.l2 = snap.l2
        else:  # codec 0：没有历史层，全部视为空
            self.l1 = np.zeros_like(self.board)
            self.l2 = np.zeros_like(self.board)
        self.V = snap.version

    # ------------------------------------------------------------ 公共增量
    def apply_public(self, m: codec.PublicMsg) -> None:
        parts = []
        for idx, col in codec.public_segments(m, self.w, self.h):
            self._apply_entries(idx, col)
            parts.append(idx)
        self.changed = np.concatenate(parts) if parts else np.zeros(0, np.int64)
        self.V = m.to
        self.public_msgs += 1
        self._expire()

    def _apply_entries(self, idx: np.ndarray, col: np.ndarray) -> None:
        """一条 PUBLIC 段内每个像素最多出现一次，所以可以整段向量化。"""
        if len(idx) == 0:
            return
        old = self.board[idx]
        l1 = self.l1[idx]
        l2 = self.l2[idx]
        new = np.where(col == 0, l1,
                       np.where(col == 1, l2,
                                np.where(col == 2, old, col)))
        chg = new != old
        if not chg.any():
            return
        idx, old, new, l1, l2 = idx[chg], old[chg], new[chg], l1[chg], l2[chg]
        pick = np.where((l1 != old) & (l1 != new), l1,
                        np.where((l2 != old) & (l2 != new), l2, new))
        self.l2[idx] = pick
        self.l1[idx] = old
        self.board[idx] = new

    # ------------------------------------------------------------ 声呐
    def apply_sonar(self, tick: int, radius: int, center: int,
                    colors: np.ndarray) -> np.ndarray:
        pix = codec.sonar_pixels(center, radius, self.w, self.h)
        ok = pix >= 0
        if not ok.any():
            return np.zeros(0, dtype=np.int64)
        pix = pix[ok]
        self.Kc[pix] = colors[ok]
        self.Kt[pix] = tick
        for p in pix.tolist():
            self.kq.append((tick, p))
        return pix

    def _expire(self) -> None:
        """公共版本 V 追上某条声呐的 tick 后，这条就不比公共画面新了。"""
        V = self.V
        kt = self.Kt
        while self.kq and self.kq[0][0] <= V:
            t, p = self.kq.popleft()
            if kt[p] == t:
                kt[p] = 0

    def known(self) -> np.ndarray:
        """当前已知的最新颜色（声呐优先，其余用公共画面）。"""
        return np.where(self.Kt > 0, self.Kc, self.board)

    def sonar_age(self, px: np.ndarray) -> np.ndarray:
        return self.Kt[px]

    def clear_sonar(self, px: np.ndarray) -> None:
        self.Kt[px] = 0

    def brush(self, slot: int):
        return self.brushes.get(slot)

    def set_brush(self, slot: int, idx: int, t_us: int) -> None:
        self.brushes[slot] = None if t_us == 0 else (idx, t_us)
