"""声呐密铺：用最少的落笔覆盖整个矩形区域。

菱形（曼哈顿半径 R）的面积是 2R²+2R+1（R=2 时 13），而格基
    e1 = (R+1, R), e2 = (-R, R+1)
的行列式恰好也是 2R²+2R+1，所以这组格可以把平面用菱形无重叠地铺满：
每个中心只负责 13 个像素，一整轮「巡检」的落笔数 ≈ 面积 / 13。

对比逐像素重扫：120×120 需要 14400 笔；密铺只要 ~1108 笔，快 13 倍。

路线顺序也很重要：格点在「行」内的间距是 13（差一个 e1+2e2），
按 y 排序会让每一笔都跨 13 格（冷却 2.3 s）。沿格基 (a,b) 蛇形走，
相邻两笔距离恒为 |e1|=|e2|=5，冷却只有 1.5 s。
"""
from __future__ import annotations

import numpy as np


def _diamond(cx: int, cy: int, r: int, x0: int, y0: int, size: int,
             cov: np.ndarray) -> None:
    for dy in range(-r, r + 1):
        span = r - abs(dy)
        y = cy + dy
        if y < y0 or y >= y0 + size:
            continue
        for dx in range(-span, span + 1):
            x = cx + dx
            if x0 <= x < x0 + size:
                cov[y - y0, x - x0] = True


def build_sonar_route(w: int, h: int, x0: int, y0: int, size: int,
                      colors: np.ndarray, radius: int = 2) -> list[tuple[int, int]]:
    """返回 [(全局像素下标, 该像素的目标颜色)]，覆盖整个 size×size 区域。"""
    r = radius
    k = size // max(1, r) + 6
    flat = colors.reshape(-1) if colors.ndim == 2 else colors

    best_cov = -1
    best_pts: list[tuple[int, int, int, int]] = []  # (a, b, x, y)
    best_mask = None
    for dy in range(-r, r + 1):
        span = r - abs(dy)
        for dx in range(-span, span + 1):
            pts = []
            mask = np.zeros((size, size), dtype=bool)
            for a in range(-k, k + 1):
                for b in range(-k, k + 1):
                    x = x0 + dx + (r + 1) * a - r * b
                    y = y0 + dy + r * a + (r + 1) * b
                    if x0 <= x < x0 + size and y0 <= y < y0 + size:
                        pts.append((a, b, x, y))
                        _diamond(x, y, r, x0, y0, size, mask)
            covered = int(mask.sum())
            if covered > best_cov or (covered == best_cov and len(pts) < len(best_pts)):
                best_cov, best_pts, best_mask = covered, pts, mask
    assert best_mask is not None

    # 沿格基蛇形：a 递增，b 在奇数 a 上反向，相邻两笔恒为 e1 或 e2（d=5）
    asc = sorted(best_pts, key=lambda p: (p[0], p[1] if p[0] % 2 == 0 else -p[1]))
    ordered = [(x, y) for _, _, x, y in asc]

    # 边缘补射：没被任何菱形覆盖的像素，以自身为中心补一笔
    extra = []
    for yy in range(size):
        for xx in range(size):
            if not best_mask[yy, xx]:
                cx, cy = x0 + xx, y0 + yy
                extra.append((cx, cy))
                _diamond(cx, cy, r, x0, y0, size, best_mask)
    ordered.extend(extra)

    route = []
    for x, y in ordered:
        local = (y - y0) * size + (x - x0)
        route.append((y * w + x, int(flat[local])))
    print(f"[tiling] R={r} 密铺中心 {len(best_pts)} + 补射 {len(extra)} = "
          f"{len(route)} 笔覆盖 {size * size} 像素（逐像素需 {size * size} 笔，省 "
          f"{size * size / len(route):.1f}×）")
    return route
