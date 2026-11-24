"""命令行入口：python -m pb {meta,probe,draw}"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import uuid

from . import target as tmod
from .http_api import Server
from .probe import collect
from .runner import Painter, Token

DEFAULT_SERVER = "https://pb.ika.zip"


def load_tokens(path: str) -> dict:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_tokens(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


async def cmd_meta(args):
    srv = Server(args.server)
    meta = srv.fetch_meta()
    print(json.dumps(meta, ensure_ascii=False, indent=1))


async def cmd_probe(args):
    res = await collect(args.server, args.seconds, args.size, args.out)
    best = res.hm.best(args.size, res.world.board)
    print(f"\n[place] 建议落点 ({best['x']},{best['y']})，窗口写入 {best['activity']} 次")
    for c in best["candidates"][:5]:
        print(f"   ({c['x']:4d},{c['y']:3d})  activity={c['activity']:6d}  "
              f"edge={c['edge']:8.0f}  score={c['score']:.3f}")
    with open(os.path.join(args.out, "probe.json"), encoding="utf-8") as f:
        info = json.load(f)
    info["placement"] = {"x": best["x"], "y": best["y"], "size": args.size,
                         "activity": best["activity"]}
    with open(os.path.join(args.out, "probe.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    print(f"[probe] 快照/热力图已写到 {args.out}")


async def cmd_draw(args):
    srv = Server(args.server)
    meta = srv.fetch_meta()
    colors = tmod.load_target(args.image, args.size)
    tmod.save_png(colors.reshape(-1), os.path.join(args.out, "target.png"),
                  args.size, args.size, scale=4)

    if args.place:
        x0, y0 = (int(v) for v in args.place.split(","))
    else:
        res = await collect(args.server, args.probe_seconds, args.size, args.out)
        best = res.hm.best(args.size, res.world.board)
        x0, y0 = best["x"], best["y"]
        print(f"[place] 自动落点 ({x0},{y0})，窗口写入 {best['activity']} 次")
    if x0 + args.size > int(meta["board"]["width"]) or \
       y0 + args.size > int(meta["board"]["height"]):
        raise SystemExit(f"落点越界: ({x0},{y0}) size={args.size}")

    cache = load_tokens(args.tokens_file)
    tokens = []
    for i in range(args.tokens):
        uid = args.uid_base + i
        key = str(uid)
        if key not in cache:
            r = srv.mint(uid)
            cache[key] = r["paint_token"]
            print(f"[token] uid={uid} -> {r['paint_token'][:8]}…")
        tokens.append(Token(uid=uid, raw=uuid.UUID(cache[key]).bytes, slot=i))
    save_tokens(args.tokens_file, cache)

    painter = Painter(args.server, meta, colors, x0, y0, tokens,
                      once=args.once, limit=args.limit, dry=args.dry,
                      start_phase="guard" if args.guard_only else "paint")
    await painter.start()


def main() -> None:
    ap = argparse.ArgumentParser(prog="pb")
    ap.add_argument("--server", default=DEFAULT_SERVER)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("meta")
    p.set_defaults(func=cmd_meta)

    p = sub.add_parser("probe")
    p.add_argument("--seconds", type=float, default=60)
    p.add_argument("--size", type=int, default=128)
    p.add_argument("--out", default="out")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("draw")
    p.add_argument("--image", default="assets/akie.webp")
    p.add_argument("--size", type=int, default=128)
    p.add_argument("--place", default="", help="x,y；留空则自动探测")
    p.add_argument("--probe-seconds", type=float, default=45)
    p.add_argument("--tokens", type=int, default=16)
    p.add_argument("--uid-base", type=int, default=910000000)
    p.add_argument("--tokens-file", default="out/tokens.json")
    p.add_argument("--out", default="out")
    p.add_argument("--once", action="store_true", help="画完一遍就停")
    p.add_argument("--limit", type=int, default=0, help="最多画多少笔后停")
    p.add_argument("--dry", action="store_true", help="只调度不发包")
    p.add_argument("--guard-only", action="store_true",
                   help="跳过首遍绘制，直接开始声呐巡检守图")
    p.set_defaults(func=cmd_draw)

    args = ap.parse_args()
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
