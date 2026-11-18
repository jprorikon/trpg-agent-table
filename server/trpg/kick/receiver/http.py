"""http（自 __init__.py 拆出）"""

import argparse
import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import quote
from trpg.paths import ROOT as HERE  # repo 根（資料／vendor／docs 都在這裡）

from .common import log_line
from .state import State

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    state: State = None  # type: ignore

    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # 太吵的存取日誌不進 stdout
        pass

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?")[0] == "/health":
            self._json(200, {"status": "ok", "service": "trpg-gm-kick",
                             "session_template": self.state.session_tpl,
                             "kicks": self.state.kick_count,
                             "seen_keys": len(self.state.seen)})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?")[0] != "/trpg/kick":
            self._json(404, {"error": "not found"})
            return
        got = self.headers.get("X-Table-Kick-Token") or ""
        want = self.state.token()
        if not got or got != want:
            log_line(self.state.log_file, "kick 被拒：token 不符")
            self._json(401, {"error": "unauthorized"})
            return
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            self._json(400, {"error": "bad json"})
            return
        # `dry_run` 是通道自測：缺 kind／turn_seq 直接 400（協作端 那側同語意；真 kick 不受這條管）
        if self.state.is_dry(payload) and not (payload.get("kind") and payload.get("turn_seq")):
            log_line(self.state.log_file, "kick dry_run 被拒：缺 kind／turn_seq")
            self._json(400, {"error": "missing kind/turn_seq"})
            return
        self._json(200, self.state.handle_kick(payload))
