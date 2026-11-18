"""http（自 __init__.py 拆出）"""

from __future__ import annotations
import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List
from trpg.core import layout as layout                                  # 桌的位置／模組命名（與桌子共用同一支）
from trpg.service import table_directory as tdir                 # L6：掃桌／探活／收局判定（與桌子共用同一支）
from trpg.paths import ROOT as HERE  # repo 根（資料／vendor／docs 都在這裡）

from .common import LOBBY
from .render import _char_index, _tables, render

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "trpg-lobby"

    def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:               # noqa: A002,A003
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            host = (self.headers.get("Host") or "127.0.0.1").split(":")[0]
            self._send(200, render(host, _tables()))
            return
        if path == "/api/tables":
            ts = _tables()
            self._send(200, json.dumps({"tables": ts, "chars": _char_index(ts)},
                                       ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
            return
        if path == "/health":
            ts = _tables()
            self._send(200, json.dumps({"status": "ok", "service": "trpg-lobby",
                                        "tables": len(ts),
                                        "online": sum(1 for t in ts if t.get("online"))},
                                       ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
            return
        self._send(404, b'{"error":"not found"}', "application/json; charset=utf-8")

def main() -> int:
    ap = argparse.ArgumentParser(description="TRPG 大廳（入口頁；只讀）")
    ap.add_argument("--root", default=HERE, help="引擎倉目錄（掃 <root>/campaigns/*/config.json）")
    ap.add_argument("--port", type=int, default=8998)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--pidfile", default=None)
    ap.add_argument("--timeout", type=float, default=1.5, help="每張桌的探活逾時（秒）")
    a = ap.parse_args()
    LOBBY["root"], LOBBY["timeout"] = os.path.abspath(a.root), a.timeout
    if a.pidfile:
        try:
            with open(a.pidfile, "w", encoding="utf-8") as fh:
                fh.write(str(os.getpid()))
        except OSError as exc:
            print(f"⚠ 寫 pidfile 失敗：{exc}", file=sys.stderr)
    srv = ThreadingHTTPServer((a.bind, a.port), Handler)
    srv.daemon_threads = True
    ts = tdir.discover(LOBBY["root"])
    print(f"[lobby] 大廳已起： http://{a.bind}:{a.port}/  "
          f"（掃到 {len(ts)} 張桌：{', '.join(t['id'] for t in ts) or '（無）'}）", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0
