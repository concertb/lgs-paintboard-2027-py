"""HTTP 接口：/api/meta、/api/paint-token。

注意 pb.ika.zip 前面有 Cloudflare，裸 urllib 的默认 UA 会被 403，必须带浏览器 UA。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0")


class HttpError(RuntimeError):
    def __init__(self, code: int, body: str):
        super().__init__(f"HTTP {code}: {body[:200]}")
        self.code = code
        self.body = body


def _request(url: str, data: bytes | None = None, timeout: float = 30.0):
    headers = {
        "User-Agent": UA,
        "Accept": "application/json, */*",
        "Origin": "https://pb.ika.zip",
        "Referer": "https://pb.ika.zip/",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        raise HttpError(e.code, e.read().decode("utf-8", "replace")) from None


def get_json(url: str, timeout: float = 30.0):
    status, body = _request(url, timeout=timeout)
    if status != 200:
        raise HttpError(status, body.decode("utf-8", "replace"))
    return json.loads(body)


def post_json(url: str, obj, timeout: float = 30.0):
    return get_json(url, timeout=timeout) if False else _post(url, obj, timeout)


def _post(url: str, obj, timeout: float):
    status, body = _request(url, json.dumps(obj).encode(), timeout=timeout)
    if status != 200:
        raise HttpError(status, body.decode("utf-8", "replace"))
    return json.loads(body)


class Server:
    def __init__(self, base: str = "https://pb.ika.zip"):
        self.base = base.rstrip("/")
        self.meta: dict = {}

    def fetch_meta(self) -> dict:
        self.meta = get_json(self.base + "/api/meta")
        return self.meta

    def mint(self, uid: int) -> dict:
        """测试服（access_mode=none）用任意 uid 换 paint_token。"""
        return _post(self.base + "/api/paint-token", {"uid": int(uid)}, 30.0)

    def mint_access_key(self, access_key: str) -> dict:
        return _post(self.base + "/api/paint-token",
                     {"access_key": access_key}, 60.0)
