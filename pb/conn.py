"""WebSocket 连接：zstd 帧收发、断线重连、下行积压保护。"""
from __future__ import annotations

import asyncio
import ssl

import websockets

from . import codec
from .http_api import UA


class Conn:
    def __init__(self, base: str, path: str, on_payload, on_open=None,
                 name: str = "ws", public: bool = False):
        base = base.replace("https://", "wss://").replace("http://", "ws://")
        self.base = base.rstrip("/")
        self.path = path
        self.on_payload = on_payload
        self.on_open = on_open
        self.name = name
        self.public = public
        self.ws = None
        self.closed = False
        self.reconnects = 0
        self.frames = 0
        self.bytes = 0

    def url(self, since: int | None = None) -> str:
        q = []
        if self.public:
            q.append("public=1")
        else:
            q.append("public=0")
        if since:
            q.append(f"since={since}")
        return f"{self.base}/ws?{'&'.join(q)}"

    async def run(self, since=None):
        delay = 0.5
        while not self.closed:
            try:
                async with websockets.connect(
                        self.url(since() if callable(since) else since),
                        additional_headers={"User-Agent": UA},
                        max_size=None, open_timeout=20, ping_interval=20,
                        ping_timeout=20) as ws:
                    self.ws = ws
                    delay = 0.5
                    if self.on_open:
                        r = self.on_open()
                        if asyncio.iscoroutine(r):
                            await r
                    async for frame in ws:
                        if isinstance(frame, bytes):
                            self.frames += 1
                            self.bytes += len(frame)
                            self.on_payload(codec.decompress(frame))
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - 任何断开都重连
                if self.closed:
                    break
                self.reconnects += 1
                print(f"[{self.name}] 断开: {type(e).__name__}: {e}，{delay:.1f}s 后重连")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 10.0)

    async def send(self, frame: bytes):
        if self.ws is None:
            return False
        await self.ws.send(frame)
        return True

    async def close(self):
        self.closed = True
        if self.ws is not None:
            await self.ws.close()
