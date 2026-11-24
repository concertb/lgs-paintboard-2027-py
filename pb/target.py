"""目标图：读入图片、缩放到目标尺寸、输出 (h*w) 的 u32 颜色矩阵。"""
from __future__ import annotations

import numpy as np
from PIL import Image


def load_target(path: str, size: int, background: tuple[int, int, int] | None = None
                ) -> np.ndarray:
    """把图片缩放成 size×size 的 u32 颜色矩阵（行优先，A=0xFF）。

    带 alpha 的图直接叠到 background（默认取图片平均色）上。
    """
    img = Image.open(path)
    has_alpha = img.mode in ("RGBA", "LA", "P") or (
        img.mode == "P" and "transparency" in img.info)
    img = img.convert("RGBA" if has_alpha else "RGB")
    img = img.resize((size, size), Image.BOX)  # BOX = 面积平均，缩小时最忠实

    if has_alpha:
        a = np.asarray(img, dtype=np.float32)
        rgb = a[..., :3]
        alpha = a[..., 3:4] / 255.0
        if background is None:
            bg = rgb.reshape(-1, 3).mean(axis=0)
        else:
            bg = np.array(background, dtype=np.float32)
        rgb = rgb * alpha + bg * (1.0 - alpha)
        arr = np.clip(rgb + 0.5, 0, 255).astype(np.uint32)
    else:
        arr = np.asarray(img, dtype=np.uint32)

    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    return (r | (g << 8) | (b << 16) | np.uint32(0xFF000000)).astype("<u4")


def to_rgb(colors: np.ndarray) -> np.ndarray:
    c = colors.astype(np.uint32).reshape(-1)
    out = np.empty((len(c), 3), dtype=np.uint8)
    out[:, 0] = c & 0xFF
    out[:, 1] = (c >> 8) & 0xFF
    out[:, 2] = (c >> 16) & 0xFF
    return out


def save_png(colors: np.ndarray, path: str, w: int, h: int, scale: int = 1) -> None:
    rgb = to_rgb(colors)
    img = Image.fromarray(rgb.reshape(h, w, 3), "RGB")
    if scale > 1:
        img = img.resize((w * scale, h * scale), Image.NEAREST)
    img.save(path)
