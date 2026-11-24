"""离线自测：编解码的字节布局、PUBLIC 历史层语义、声呐几何。"""
from __future__ import annotations

import struct

import numpy as np

from pb import codec
from pb.world import World


def build_public(w, h, frm, to, segments, order=0, rate=32):
    """segments: [(tick_offset, [(idx, color_code), ...]), ...]（段内按 idx 升序）"""
    seg_tick = [s[0] for s in segments]
    seg_n = [len(s[1]) for s in segments]
    ds, db, cols = [], [], []
    for _, entries in segments:
        prev = None
        for idx, col in entries:
            key = idx if order == 0 else (idx % w) * h + idx // w
            d = key if prev is None else key - prev
            prev = key
            if d >= 255:
                ds.append(255)
                db.append(d)
            else:
                ds.append(d)
            cols.append(col)
    n, m, e = len(ds), len(segments), len(db)
    out = bytearray(struct.pack("<BBHIQQIII", codec.PUBLIC, 1, rate, n,
                                frm, to, m, e, order))
    out += np.asarray(seg_tick, dtype="<u4").tobytes()
    out += np.asarray(seg_n, dtype="<u4").tobytes()
    out += bytes(ds) + b"\0" * (codec.pad4(n) - n)
    out += np.asarray(db, dtype="<u4").tobytes()
    out += np.asarray(cols, dtype="<u4").tobytes()
    return bytes(out)


def test_batch():
    tok = bytes(range(16))
    frame = codec.encode_batch([(3, tok)], [(7, 12345, 0xFF112233)])
    p = codec.decompress(frame)
    assert p[0] == 1 and p[1] == 1
    nb, npz = struct.unpack_from("<II", p, 4)
    assert (nb, npz) == (1, 1), (nb, npz)
    assert len(p) == 16 + codec.pad4(2) + 16 + codec.pad4(2) + 8, len(p)
    assert struct.unpack_from("<H", p, 16)[0] == 3
    assert p[20:36] == tok
    assert struct.unpack_from("<H", p, 36)[0] == 7
    assert struct.unpack_from("<I", p, 40)[0] == 12345
    assert struct.unpack_from("<I", p, 44)[0] == 0xFF112233
    print("batch ok", len(p))


def test_rules():
    frame = codec.encode_rules_like(123456789)
    assert len(frame) == 96, len(frame)
    r = codec.decode_rules(frame)
    assert (r.w, r.h, r.tps, r.sonar_radius) == (1000, 600, 128, 2)
    assert r.cooldown_base_us == 1_000_000 and r.server_time_us == 123456789
    print("rules ok")


def test_public():
    w = h = 8
    world = World(w, h)
    world.apply_snapshot(codec.Snapshot(w, h, 10, np.zeros(64, "u4")))
    C1 = codec.rgba(10, 20, 30)
    C2 = codec.rgba(200, 100, 50)

    def apply(frm, to, segs, order=0):
        m = codec.decode_public(build_public(w, h, frm, to, segs, order=order))
        world.apply_public(m)

    # 1) 首次落色：l1 = 旧色(0)，l2 = 当前色（空）
    apply(10, 12, [(2, [(5, C1), (9, C2)])])
    assert world.board[5] == C1 and world.board[9] == C2
    assert world.l1[5] == 0 and world.l1[9] == 0
    # 2) idx5 换色：l1 记 C1，l2 记更早的 0
    apply(12, 16, [(4, [(5, C2)])])
    assert world.board[5] == C2 and world.l1[5] == C1 and world.l2[5] == 0, \
        (hex(int(world.board[5])), hex(int(world.l1[5])), hex(int(world.l2[5])))
    # 3) 颜色码 0 = 取 l1，回到 C1
    apply(16, 20, [(4, [(5, 0)])])
    assert world.board[5] == C1, hex(int(world.board[5]))
    # 4) 颜色码 1 = 取 l2，变回 0
    apply(20, 24, [(4, [(5, 1)])])
    assert world.board[5] == 0 and world.l1[5] == C1
    # 5) 颜色码 2 = 原地重绘，不改历史层
    apply(24, 28, [(4, [(5, 2)])])
    assert world.board[5] == 0 and world.l1[5] == C1
    # 6) 列优先
    w2 = World(w, h)
    w2.apply_snapshot(codec.Snapshot(w, h, 0, np.zeros(64, "u4")))
    m4 = codec.decode_public(build_public(w, h, 0, 5, [(5, [(9, C2)])], order=1))
    w2.apply_public(m4)
    assert w2.board[9] == C2 and w2.V == 5
    print("public ok")


def test_sonar():
    offs = codec.sonar_offsets(2)
    assert len(offs) == 13
    assert offs[0] == (0, -2) and offs[6] == (0, 0) and offs[-1] == (0, 2)
    px = codec.sonar_pixels(0, 2, 100, 100)  # 左上角，越界为 -1
    assert (px < 0).sum() == 7, (px < 0).sum()  # 角落 13 格里有 7 格越界
    px2 = codec.sonar_pixels(100 * 50 + 50, 2, 100, 100)
    assert (px2 >= 0).all() and len(set(px2.tolist())) == 13
    print("sonar ok")


def test_tiling():
    from pb.tiling import build_sonar_route
    size = 120
    colors = np.zeros((size, size), dtype="<u4")
    route = build_sonar_route(1000, 600, 440, 1, size, colors, radius=2)
    cov = np.zeros((size, size), dtype=bool)
    for g, _c in route:
        x, y = g % 1000 - 440, g // 1000 - 1
        for dy in range(-2, 3):
            span = 2 - abs(dy)
            for dx in range(-span, span + 1):
                xx, yy = x + dx, y + dy
                if 0 <= xx < size and 0 <= yy < size:
                    cov[yy, xx] = True
    assert cov.all(), cov.sum()
    ratio = size * size / len(route)
    assert ratio > 8, ratio
    print(f"tiling ok: {len(route)} 笔覆盖 {size*size} 像素，省 {ratio:.1f}x")


if __name__ == "__main__":
    test_batch()
    test_rules()
    test_public()
    test_sonar()
    test_tiling()
    print("ALL OK")
