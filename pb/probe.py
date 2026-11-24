"""只读探测：连公共流，积累画面与热力图，给出候选落点。"""
from __future__ import annotations

import asyncio
import json
import os
import time

from . import codec, target
from .conn import Conn
from .http_api import Server
from .place import Heatmap
from .world import World


class ProbeResult:
    def __init__(self, meta, world, hm, rules):
        self.meta = meta
        self.world = world
        self.hm = hm
        self.rules = rules


async def collect(server: str, seconds: float, size: int, outdir: str) -> ProbeResult:
    meta = Server(server).fetch_meta()
    w, h = int(meta["board"]["width"]), int(meta["board"]["height"])
    world = World(w, h)
    hm = Heatmap(w, h)
    state = {"rules": None, "snap": 0}

    def on_payload(p: bytes) -> None:
        if not p:
            return
        kind = p[0]
        if kind == codec.RULES:
            r = codec.decode_rules(p)
            state["rules"] = r
            world.clock_offset_us = r.server_time_us - time.time() * 1e6
            print(f"[probe] rules: {w}x{h} c0={r.cooldown_base_us/1e6:.3f}s "
                  f"k={r.cooldown_per_px_us/1e6:.4f}s/px "
                  f"delay={r.public_delay_ms/1000:.0f}s tps={r.tps}")
        elif kind == codec.SNAPSHOT:
            snap = codec.decode_snapshot(p)
            world.apply_snapshot(snap)
            state["snap"] += 1
            print(f"[probe] snapshot v{snap.version} (#{state['snap']})")
        elif kind == codec.PUBLIC:
            m = codec.decode_public(p)
            for idx, _c in codec.public_segments(m, w, h):
                hm.add_indices(idx)
            world.apply_public(m)

    conn = Conn(server, "/ws", on_payload, public=True, name="probe")
    task = asyncio.create_task(conn.run(since=lambda: world.V or None))
    await asyncio.sleep(seconds)
    await conn.close()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    os.makedirs(outdir, exist_ok=True)
    target.save_png(world.board, os.path.join(outdir, "board.png"), w, h)
    hm.save_png(os.path.join(outdir, "heat.png"), size)

    info = {
        "board": [w, h],
        "public_version": int(world.V),
        "public_messages": hm.messages,
        "writes_observed": int(hm.count.sum()),
        "seconds": seconds,
        "placement": None,
    }
    if state["rules"] is not None:
        r = state["rules"]
        info["rules"] = {
            "tps": r.tps,
            "cooldown_base_us": r.cooldown_base_us,
            "cooldown_per_px_us": r.cooldown_per_px_us,
            "sonar_radius_px": r.sonar_radius,
            "public_delay_ms": r.public_delay_ms,
            "public_period_ms": r.public_period_ms,
        }
    with open(os.path.join(outdir, "probe.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    print(f"[probe] 收 PUBLIC {hm.messages} 条，观测写入 {int(hm.count.sum())} 次")
    return ProbeResult(meta, world, hm, state["rules"])
