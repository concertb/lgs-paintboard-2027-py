"""ikapb 2027 二进制协议编解码（协议文档 docs/protocol.md §3）。

约定：
  * 一条 WebSocket 消息 = 恰好一个 zstd frame；
  * 载荷 = 定长头部 + 若干列，整数小端，列按 4 字节对齐；
  * 颜色 u32，内存序 R,G,B,A，A 必须 255。
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

import numpy as np
from compression import zstd

# ---------------------------------------------------------------- 基础工具


def pad4(n: int) -> int:
    return (n + 3) & ~3


def compress(payload: bytes, level: int = 1) -> bytes:
    return zstd.compress(payload, level)


def decompress(frame: bytes) -> bytes:
    return zstd.decompress(frame)


def rgba(r: int, g: int, b: int) -> int:
    return r | (g << 8) | (b << 16) | 0xFF000000


def color_from_hex(s: str) -> int:
    s = s.lstrip("#")
    v = int(s, 16)
    return rgba((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)


def u32_le(arr) -> bytes:
    return np.asarray(arr, dtype="<u4").tobytes()


def u16_le(arr) -> bytes:
    return np.asarray(arr, dtype="<u2").tobytes()


# ---------------------------------------------------------------- 上行 BATCH

BATCH = 0x01


def encode_batch(binds=(), paints=()) -> bytes:
    """binds: [(slot, 16B token)]；paints: [(slot, idx, color)]。

    一条消息里可以同时绑定多个 token 并落多笔；每笔 10 字节。
    """
    nb = len(binds)
    npz = len(paints)
    out = bytearray(struct.pack("<BBHIII", BATCH, 1, 0, nb, npz, 0))
    if nb:
        out += u16_le([s for s, _ in binds])
        out += b"\0" * (pad4(2 * nb) - 2 * nb)
        out += b"".join(t for _, t in binds)
    if npz:
        out += u16_le([s for s, _, _ in paints])
        out += b"\0" * (pad4(2 * npz) - 2 * npz)
        out += u32_le([i for _, i, _ in paints])
        out += u32_le([c for _, _, c in paints])
    return compress(bytes(out))


def encode_batch_arrays(slots: np.ndarray, idxs: np.ndarray, colors: np.ndarray,
                        binds=()) -> bytes:
    """批量版本：绘图列已经是 numpy 数组时走这里，避免 Python 循环。"""
    nb = len(binds)
    npz = len(idxs)
    out = bytearray(struct.pack("<BBHIII", BATCH, 1, 0, nb, npz, 0))
    if nb:
        out += u16_le([s for s, _ in binds])
        out += b"\0" * (pad4(2 * nb) - 2 * nb)
        out += b"".join(t for _, t in binds)
    if npz:
        out += u16_le(slots)
        out += b"\0" * (pad4(2 * npz) - 2 * npz)
        out += u32_le(idxs)
        out += u32_le(colors)
    return compress(bytes(out))


# ---------------------------------------------------------------- 下行消息

RULES = 0x84
SNAPSHOT = 0x83
PUBLIC = 0x82
TICK = 0x81

STATUS = {
    0: "OK",
    1: "COOLING",
    2: "NO_PAINT_TOKEN",
    3: "BAD_PAINT_TOKEN",
    4: "BANNED",
    5: "CLOSED",
    6: "IP_LIMIT",
}


@dataclass
class Rules:
    w: int
    h: int
    sonar_radius: int
    tps: int
    cooldown_base_us: int
    cooldown_per_px_us: int
    public_delay_ms: int
    public_period_ms: int
    event_start_unix_ms: int
    event_end_unix_ms: int
    max_slots: int
    max_paints_per_message: int
    msg_rate: int
    msg_burst: int
    max_message_bytes: int
    max_payload_bytes: int
    server_time_us: int


def decode_rules(p: bytes) -> Rules:
    if p[0] != RULES:
        raise ValueError(f"not RULES: {p[0]:#x}")
    w, h = struct.unpack_from("<HH", p, 4)
    (radius,) = struct.unpack_from("<H", p, 8)
    (tps,) = struct.unpack_from("<I", p, 12)
    c0, k, delay, period, start, end = struct.unpack_from("<QQQQQQ", p, 16)
    max_slots, max_paints, msg_rate, msg_burst, max_msg, max_payload = \
        struct.unpack_from("<IIIIII", p, 64)
    (server_us,) = struct.unpack_from("<Q", p, 88)
    return Rules(w, h, radius, tps, c0, k, delay, period, start, end,
                 max_slots, max_paints, msg_rate, msg_burst, max_msg,
                 max_payload, server_us)


@dataclass
class Snapshot:
    w: int
    h: int
    version: int
    colors: np.ndarray
    l1: np.ndarray | None = None
    l2: np.ndarray | None = None


def decode_snapshot(p: bytes) -> Snapshot:
    if p[0] != SNAPSHOT:
        raise ValueError(f"not SNAPSHOT: {p[0]:#x}")
    codec = p[2]
    w, h = struct.unpack_from("<HH", p, 4)
    (version,) = struct.unpack_from("<Q", p, 8)
    n = w * h
    colors = np.frombuffer(p, dtype="<u4", count=n, offset=16).copy()
    if codec == 1:
        l1 = np.frombuffer(p, dtype="<u4", count=n, offset=16 + 4 * n).copy()
        l2 = np.frombuffer(p, dtype="<u4", count=n, offset=16 + 8 * n).copy()
    else:
        l1 = l2 = None
    return Snapshot(w, h, version, colors, l1, l2)


@dataclass
class PublicMsg:
    sample_rate: int
    seg_tick: np.ndarray   # u32 × m（相对 from 的偏移）
    seg_n: np.ndarray      # u32 × m
    dsmall: np.ndarray     # u8  × n
    dbig: np.ndarray       # u32 × e
    colors: np.ndarray     # u32 × n
    frm: int
    to: int
    order: int


def decode_public(p: bytes) -> PublicMsg:
    if p[0] != PUBLIC:
        raise ValueError(f"not PUBLIC: {p[0]:#x}")
    rate, n, frm, to, m, e, order = struct.unpack_from("<HIQQIII", p, 2)
    off = 36
    seg_tick = np.frombuffer(p, dtype="<u4", count=m, offset=off).copy()
    off += 4 * m
    seg_n = np.frombuffer(p, dtype="<u4", count=m, offset=off).copy()
    off += 4 * m
    dsmall = np.frombuffer(p, dtype=np.uint8, count=n, offset=off).copy()
    off += pad4(n)
    dbig = np.frombuffer(p, dtype="<u4", count=e, offset=off).copy()
    off += 4 * e
    colors = np.frombuffer(p, dtype="<u4", count=n, offset=off).copy()
    off += 4 * n
    if off != len(p):
        raise ValueError(f"PUBLIC length mismatch: {off} != {len(p)}")
    return PublicMsg(rate, seg_tick, seg_n, dsmall, dbig, colors, frm, to, order)


@dataclass
class Tick:
    tick: int
    radius: int
    results: list[tuple[int, int]] = field(default_factory=list)  # (status, value)
    brushes: list[tuple[int, int, int]] = field(default_factory=list)  # (slot, idx, t_us)
    sonars: list[tuple[int, np.ndarray]] = field(default_factory=list)  # (idx, A colors)
    lost: np.ndarray = field(default_factory=lambda: np.zeros(0, "u2"))


def decode_tick(p: bytes) -> Tick:
    if p[0] != TICK:
        raise ValueError(f"not TICK: {p[0]:#x}")
    radius, nr = struct.unpack_from("<HI", p, 2)
    (tick,) = struct.unpack_from("<Q", p, 8)
    nb, nk, ns = struct.unpack_from("<III", p, 16)
    a = 2 * radius * radius + 2 * radius + 1
    off = 32
    brush_t = np.frombuffer(p, dtype="<u8", count=nb, offset=off).copy()
    off += 8 * nb
    brush_idx = np.frombuffer(p, dtype="<u4", count=nb, offset=off).copy()
    off += 4 * nb
    brush_slot = np.frombuffer(p, dtype="<u2", count=nb, offset=off).copy()
    off += pad4(2 * nb)
    status = np.frombuffer(p, dtype=np.uint8, count=nr, offset=off).copy()
    off += pad4(nr)
    value = np.frombuffer(p, dtype="<u4", count=nr, offset=off).copy()
    off += 4 * nr
    sonar_idx = np.frombuffer(p, dtype="<u4", count=nk, offset=off).copy()
    off += 4 * nk
    sonar_color = np.frombuffer(p, dtype="<u4", count=nk * a, offset=off).copy()
    off += 4 * nk * a
    lost = np.frombuffer(p, dtype="<u2", count=ns, offset=off).copy()
    off += pad4(2 * ns)
    if off != len(p):
        raise ValueError(f"TICK length mismatch: {off} != {len(p)}")
    t = Tick(tick=tick, radius=radius, lost=lost,
             results=list(zip(status.tolist(), value.tolist())),
             brushes=[(int(brush_slot[k]), int(brush_idx[k]), int(brush_t[k]))
                      for k in range(nb)],
             sonars=[(int(sonar_idx[k]), sonar_color[k * a:(k + 1) * a])
                     for k in range(nk)])
    return t


def message_kind(p: bytes) -> int:
    return p[0] if p else -1


def encode_rules_like(server_us: int, w=1000, h=600, radius=2, tps=128,
                      c0=1_000_000, k=100_000, delay=60_000, period=1000,
                      start=0, end=0, max_slots=32768, max_paints=1 << 20,
                      msg_rate=128, msg_burst=128, max_msg=1 << 20,
                      max_payload=1 << 20) -> bytes:
    """离线测试用：造一条 RULES。"""
    return struct.pack("<BBHHHHHIQQQQQQIIIIIIQ", RULES, 1, 0, w, h, radius, 0,
                       tps, c0, k, delay, period, start, end,
                       max_slots, max_paints, msg_rate, msg_burst,
                       max_msg, max_payload, server_us)


def public_segments(m: PublicMsg, w: int, h: int):
    """按段还原像素下标（段内已做前缀和），yield (idx, colors)。"""
    d = m.dsmall.astype(np.int64)
    pos = 0
    big = 0
    for k in range(len(m.seg_n)):
        cnt = int(m.seg_n[k])
        s = d[pos:pos + cnt]
        mask = s == 255
        nbig = int(mask.sum())
        if nbig:
            s[mask] = m.dbig[big:big + nbig]
            big += nbig
        keys = np.cumsum(s)
        if m.order == 0:
            idx = keys
        else:
            idx = (keys % h) * w + keys // h
        yield idx.astype(np.int64), m.colors[pos:pos + cnt]
        pos += cnt


def sonar_offsets(radius: int) -> list[tuple[int, int]]:
    """声呐菱形顺序：(dy 从 -R..R，每行 dx 从 -(R-|dy|)..R-|dy|)。"""
    out = []
    for dy in range(-radius, radius + 1):
        span = radius - abs(dy)
        for dx in range(-span, span + 1):
            out.append((dx, dy))
    return out


def sonar_pixels(center: int, radius: int, w: int, h: int) -> np.ndarray:
    """返回长度 A 的像素下标数组，越界为 -1。"""
    x, y = center % w, center // w
    offsets = sonar_offsets(radius)
    dx = np.array([o[0] for o in offsets])
    dy = np.array([o[1] for o in offsets])
    xs = x + dx
    ys = y + dy
    ok = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
    out = np.full(len(offsets), -1, dtype=np.int64)
    out[ok] = ys[ok] * w + xs[ok]
    return out
