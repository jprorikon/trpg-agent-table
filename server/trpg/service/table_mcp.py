#!/usr/bin/env python3
"""stdio ↔ HTTP 的 MCP 桥：给只支持 stdio 的 MCP 客户端用。

本机 agent 的两种挂法：
  A) HTTP（推荐，一条 url）  ： mcp_servers.table: {url: "http://<nas>:8971/mcp"}
  B) stdio（本文件）        ： mcp_servers.table: {command: "python3", args: [".../table_mcp.py"]}

本文件不做任何业务逻辑——纯转发到桌服务的 /mcp。桌子仍只有一份状态。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

BASE = os.environ.get("TRPG_TABLE_URL", "http://127.0.0.1:8971/mcp")
SESSION: dict = {}


def _post(msg: dict):
    data = json.dumps(msg).encode("utf-8")
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    if SESSION.get("id"):
        headers["Mcp-Session-Id"] = SESSION["id"]
    req = urllib.request.Request(BASE, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            sid = resp.headers.get("Mcp-Session-Id")
            if sid:
                SESSION["id"] = sid
            raw = resp.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return {"jsonrpc": "2.0", "id": msg.get("id"),
                "error": {"code": -32000, "message": f"table unreachable: {exc}"}}
    if not raw.strip():
        return None
    # 服务端也可能回 SSE（event: message / data: {...}）
    if raw.lstrip().startswith("event:") or raw.lstrip().startswith("data:"):
        for line in raw.splitlines():
            if line.startswith("data:"):
                raw = line[5:].strip()
                break
    return json.loads(raw)


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        out = _post(msg)
        if out is not None:
            sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
