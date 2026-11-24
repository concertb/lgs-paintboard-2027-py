"""绘图/守图执行器：一条 WS 连接 + 多槽位 + 每 tick 攒一条 BATCH。"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass

import numpy as np

from . import codec
from .conn import Conn
from .world import World


@dataclass
class Token:
    uid: int
    raw: bytes
    slot: int
    bound: bool = False
    brush: tuple[int, int] | None = None
    due_us: float = 0
    inflight: bool = False
    pos: int = 0
    start: int = 0
    end: int = 0
    dir: int = 1
    painted: int = 0
    cooling: int = 0
    errors: int = 0
    sent_at: float = 0.0


class Painter:
    def __init__(self, server: str, meta: dict, colors: np.ndarray | None,
                 x0: int, y0: int, tokens: list[Token], once: bool = False,
                 limit: int = 0, dry: bool = False,
                 route: list[tuple[int, int]] | None = None,
                 start_phase: str = "paint"):
        self.server = server
        self.meta = meta
        self.size = int(colors.shape[0]) if colors is not None else 0
        self.target = colors.reshape(-1) if colors is not None else np.zeros(0, "<u4")
        self.x0 = x0
        self.y0 = y0
        self.tokens = tokens
        self.once = once
        self.limit = limit
        self.dry = dry

        self.w = int(meta["board"]["width"])
        self.h = int(meta["board"]["height"])
        self.world = World(self.w, self.h)
        self.rules: codec.Rules | None = None
        self.clock_off = 0.0
        self.pending: deque = deque()
        self.conn: Conn | None = None
        self.stop = False
        self.last_tick = 0.0
        self.ticks = 0
        self.ok_total = 0
        self.t0 = time.time()
        self.cpu0 = time.process_time()
        self.route = list(route) if route is not None else self._build_route()
        self.paint_route = list(self.route)
        self.phase = "paint"
        self.guard_cycles = 0
        self.cycle_t0 = time.time()
        self.guard_since = 0.0
        self.total = len(self.route)
        self._build_desired()
        self._assign_slices()
        if start_phase == "guard" and route is None:
            self._enter_guard()

    def _enter_guard(self) -> None:
        """首遍画完后切到「声呐密铺巡檢」：每笔覆盖 13 像素，一轮只花 1/13 的笔数。"""
        from .tiling import build_sonar_route

        radius = self.rules.sonar_radius if self.rules else 2
        self.route = build_sonar_route(self.w, self.h, self.x0, self.y0, self.size,
                                       self.target.reshape(self.size, self.size),
                                       max(1, radius))
        self.total = len(self.route)
        self.phase = "guard"
        self.ok_total = 0
        self.cycle_t0 = time.time()
        self.guard_since = time.time()
        self.suspects.clear()
        self._sus_was = 0
        self._assign_slices()
        for tok in self.tokens:
            tok.pos = tok.start

    def _enter_paint(self) -> None:
        """地盘被大面积破坏时，退回全量重绘（只靠巡检中心补不回来）。"""
        self.route = list(self.paint_route)
        self.total = len(self.route)
        self.phase = "paint"
        self.ok_total = 0
        self.cycle_t0 = time.time()
        self.suspects.clear()
        self._sus_was = 0
        self._assign_slices()
        for tok in self.tokens:
            tok.pos = tok.start
            tok.dir = 1

    @property
    def guard_route(self):
        return self.route if self.phase == "guard" else None

    def _build_desired(self) -> None:
        """目标色 + 是否属于本区域，供守图时快速判断「这个像素现在对不对」。"""
        n = self.w * self.h
        self.desired = np.zeros(n, dtype="<u4")
        self.in_region = np.zeros(n, dtype=bool)
        for g, c in self.route:
            self.desired[g] = c
            self.in_region[g] = True
        self.suspects: set[int] = set()
        self._sus_was = 0
        self.repairs = 0
        self.max_jump = 96  # 守图时最多跨多少格去救火；再远就不划算，继续扫自己的带

    def _note(self, indices: np.ndarray) -> None:
        """更新「可疑像素」集合：已知颜色 != 目标色。"""
        if len(indices) == 0:
            return
        idx = indices[self.in_region[indices]]
        if len(idx) == 0:
            return
        kt = self.world.Kt[idx]
        known = np.where(kt > 0, self.world.Kc[idx], self.world.board[idx])
        bad = known != self.desired[idx]
        for g in idx[bad].tolist():
            self.suspects.add(g)
        for g in idx[~bad].tolist():
            self.suspects.discard(g)
        n = len(self.suspects)
        if n and not self._sus_was:
            print(f"[detect] {time.strftime('%H:%M:%S')} 发现 {n} 个可疑像素")
        elif not n and self._sus_was:
            print(f"[detect] {time.strftime('%H:%M:%S')} 可疑像素清零")
        self._sus_was = n

    def _assign_slices(self) -> None:
        """把蛇形路线切成连续切片，每个 token 分一段（= 若干整行），互不重叠。"""
        L = self.total
        n = max(1, len(self.tokens))
        for i, tok in enumerate(self.tokens):
            tok.start = (L * i) // n
            tok.end = (L * (i + 1)) // n
            tok.pos = tok.start
            tok.dir = 1

    # ------------------------------------------------------------ 路线
    def _build_route(self) -> list[tuple[int, int]]:
        """箱内蛇形：相邻两笔曼哈顿距离恒为 1（换行也是 1），行程开销最小。"""
        n = self.size
        out = []
        for row in range(n):
            cols = range(n) if row % 2 == 0 else range(n - 1, -1, -1)
            for c in cols:
                g = (self.y0 + row) * self.w + (self.x0 + c)
                out.append((g, int(self.target[row * n + c])))
        return out

    def now_us(self) -> float:
        return time.time() * 1e6 + self.clock_off

    # ------------------------------------------------------------ 收包
    def on_payload(self, p: bytes) -> None:
        if not p:
            return
        kind = p[0]
        if kind == codec.RULES:
            self._on_rules(codec.decode_rules(p))
        elif kind == codec.SNAPSHOT:
            self.world.apply_snapshot(codec.decode_snapshot(p))
        elif kind == codec.PUBLIC:
            self.world.apply_public(codec.decode_public(p))
            self._note(self.world.changed)
        elif kind == codec.TICK:
            self._on_tick(codec.decode_tick(p))

    def _on_rules(self, r: codec.Rules) -> None:
        first = self.rules is None
        self.rules = r
        self.clock_off = r.server_time_us - time.time() * 1e6
        if r.w != self.world.w or r.h != self.world.h:
            self.world = World(r.w, r.h)
            self.w, self.h = r.w, r.h
            self.route = self._build_route()
            self.total = len(self.route)
            self._build_desired()
            self._assign_slices()
        if first:
            print(f"[rules] {r.w}x{r.h} tps={r.tps} c0={r.cooldown_base_us/1e6:.3f}s "
                  f"k={r.cooldown_per_px_us/1e6:.4f}s/px sonar R={r.sonar_radius} "
                  f"delay={r.public_delay_ms/1000:.0f}s slots={r.max_slots}")

    def _on_tick(self, t: codec.Tick) -> None:
        self.ticks += 1
        self.last_tick = time.time()
        now = self.now_us()
        for st, val in t.results:
            if not self.pending:
                break
            rec = self.pending.popleft()
            tok = rec[0]
            tok.inflight = False
            if rec[1] == "bind":
                if st == 0:
                    tok.bound = True
                else:
                    tok.errors += 1
            else:
                if st == 0:
                    tok.painted += 1
                    tok.pos += tok.dir
                    self.ok_total += 1
                elif st == 1:  # COOLING：猜测失败，回滚画笔并按服务器给的值等
                    tok.cooling += 1
                    tok.brush = rec[3]
                    tok.due_us = max(tok.due_us, now + val)
                elif st == 2:  # NO_PAINT_TOKEN
                    tok.bound = False
                    tok.brush = rec[3]
                else:
                    tok.errors += 1
                    tok.brush = rec[3]
        for slot, idx, t_us in t.brushes:
            self.world.set_brush(slot, idx, t_us)
            for tok in self.tokens:
                if tok.slot == slot:
                    tok.brush = None if t_us == 0 else (idx, t_us)
                    break
        for center, colors in t.sonars:
            pix = self.world.apply_sonar(t.tick, t.radius, center, colors)
            self._note(pix)
        if len(t.lost):
            lost = set(int(s) for s in t.lost.tolist())
            for tok in self.tokens:
                if tok.slot in lost:
                    tok.bound = False

    # ------------------------------------------------------------ 连接
    async def start(self):
        self.conn = Conn(self.server, "/ws", self.on_payload,
                         on_open=self._on_open, name="work")
        self.reader = asyncio.create_task(self.conn.run())
        # 观察连接：单独收公共画面（晚 public_delay），用来发现「被改」的像素。
        # 工作连接保持 public=0，避免公共流把 TICK 的实时性拖下去。
        self.obs = Conn(self.server, "/ws", self.on_payload, name="obs", public=True)
        self.obs_task = asyncio.create_task(
            self.obs.run(since=lambda: self.world.V or None))
        for _ in range(500):
            if self.rules is not None:
                break
            await asyncio.sleep(0.02)
        if self.rules is None:
            raise SystemExit("没有收到 RULES，服务器不可达？")
        print(f"[draw] 目标 {self.total} 像素 @ ({self.x0},{self.y0})，"
              f"{len(self.tokens)} 个 token")
        await self.run()

    async def _on_open(self):
        self.pending.clear()
        for tok in self.tokens:
            tok.bound = False
            tok.inflight = False
        if not self.tokens or self.conn is None or self.conn.ws is None:
            return
        binds = [(tok.slot, tok.raw) for tok in self.tokens]
        for tok in self.tokens:
            self.pending.append((tok, "bind"))
        await self.conn.send(codec.encode_batch(binds, []))
        print(f"[bind] 绑定 {len(binds)} 个 token 到槽位 0..{len(binds)-1}")

    # ------------------------------------------------------------ 主循环
    def _next_record(self, tok: Token, now: float):
        # 到边界就反向：这样「上一轮结尾」和「下一轮开头」是同一个点，省掉一次长跳
        if tok.pos >= tok.end:
            tok.dir = -1
            tok.pos = tok.end - 1
        elif tok.pos < tok.start:
            tok.dir = 1
            tok.pos = tok.start
        idx, color = self.route[tok.pos]
        if tok.brush is not None:
            bx, bt = tok.brush
            d = abs(idx % self.w - bx % self.w) + abs(idx // self.w - bx // self.w)
            ready = bt + self.rules.cooldown_base_us + self.rules.cooldown_per_px_us * d
            if ready > now:
                tok.due_us = ready
                return None
        return idx, color

    def _pick_suspect(self, tok: Token, now: float):
        """有「已知被改」的像素时优先救火；太远就继续扫自己的带。"""
        if not self.suspects or len(self.suspects) > 512:
            return None  # 可疑像素太多时说明还在首遍绘制，直接扫带更划算
        if tok.brush is None:
            g = next(iter(self.suspects))
            self.suspects.discard(g)
            return g, int(self.desired[g])
        bx, bt = tok.brush
        bx_x, bx_y = bx % self.w, bx // self.w
        best = -1
        bd = 1 << 30
        for g in self.suspects:
            d = abs(g % self.w - bx_x) + abs(g // self.w - bx_y)
            if d < bd:
                bd, best = d, g
                if d == 0:
                    break
        if best < 0 or bd > self.max_jump:
            return None
        ready = bt + self.rules.cooldown_base_us + self.rules.cooldown_per_px_us * bd
        if ready > now:
            tok.due_us = ready
            return "wait"
        self.suspects.discard(best)
        self.repairs += 1
        return best, int(self.desired[best])

    async def run(self):
        next_report = time.time() + 5
        while not self.stop:
            now = self.now_us()
            paints = []
            for tok in self.tokens:
                if not tok.bound or tok.inflight or tok.due_us > now:
                    continue
                rec = self._pick_suspect(tok, now)
                if rec == "wait":
                    continue
                if rec is None:
                    rec = self._next_record(tok, now)
                if rec is None:
                    continue
                idx, color = rec
                prev = tok.brush
                # 乐观调度：把「现在」当作服务器接受时间，立刻排下一笔的冷却，
                # 不再等 TICK 回执。算早了会收到 COOLING，按它给的值回滚重排。
                tok.brush = (idx, now)
                tok.inflight = True
                tok.sent_at = time.time()
                self.pending.append((tok, "paint", idx, prev))
                paints.append((tok.slot, idx, color))
            if paints:
                slots = np.fromiter((s for s, _, _ in paints), dtype="<u2", count=len(paints))
                idxs = np.fromiter((i for _, i, _ in paints), dtype="<u4", count=len(paints))
                cols = np.fromiter((c for _, _, c in paints), dtype="<u4", count=len(paints))
                if not self.dry:
                    await self.conn.send(codec.encode_batch_arrays(slots, idxs, cols))
            if time.time() >= next_report:
                self._report()
                next_report = time.time() + 5
            if self.limit and self.ok_total >= self.limit:
                self.stop = True
            elif (self.phase == "guard"
                  and time.time() - self.guard_since > 90
                  and len(self.suspects) > max(512, self.total // 4)):
                print(f"[phase] 可疑像素 {len(self.suspects)} > {max(512, self.total // 4)}，"
                      f"判定为地盘被大面积破坏，回到全量重绘")
                self._enter_paint()
            elif self.ok_total >= self.total:
                if self.once:
                    self.stop = True
                elif self.phase == "paint":
                    print(f"[phase] 首遍绘制完成，切换声呐密铺巡检")
                    self._enter_guard()
                else:
                    dt = time.time() - self.cycle_t0
                    self.guard_cycles += 1
                    print(f"[guard] 第 {self.guard_cycles} 轮巡检完成：{dt:.1f}s，"
                          f"suspect={len(self.suspects)} fix={self.repairs}；开始下一轮")
                    self.ok_total = 0
                    self.cycle_t0 = time.time()
            await asyncio.sleep(0.002)
        self._report()
        print("[draw] 结束")

    def _report(self):
        wall = time.time() - self.t0
        rate = self.ok_total / wall * 60 if wall > 0 else 0
        bound = sum(1 for t in self.tokens if t.bound)
        cool = sum(t.cooling for t in self.tokens)
        err = sum(t.errors for t in self.tokens)
        cpu = time.process_time() - self.cpu0
        done = min(self.ok_total, self.total)
        eta = ""
        if rate > 0:
            left = max(0, self.total - done)
            eta = f"  剩余约 {left / rate:.1f} min"
        print(f"[{wall:6.1f}s] {self.phase:5s} {done}/{self.total} "
              f"({done / self.total * 100:5.1f}%) {rate:6.1f} px/min "
              f"bound={bound} cooling={cool} err={err} "
              f"suspect={len(self.suspects)} fix={self.repairs} "
              f"V={self.world.V} cpu={cpu:.1f}s{eta}")
