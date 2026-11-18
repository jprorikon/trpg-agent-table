#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**MCP 適配器**：agent ←MCP→ 這一支 →GraphQL→ 前台（`hub_server`）。

設計者 2026-10-07 拍板：「① 全替換，走 GraphQL」＋「**適配器的位置你和她商量後決定**」。

## 兩件紀律（照設計者的規矩）

1. **不 import 引擎**：這一支只認 `docs/schema.graphql` 與 `POST /graphql`——分層上它是 L5（橋／App
   那一層），引擎不 import 它，它也不 import 引擎（設計者：「保持 trpg 應用本身乾淨」）。
2. **工具描述逐字相同**：目錄**不抄第二份**，是問伺服器要的（`table(id){ tools { … } }` ← `mcp_api.TOOLS`
   那一份，按這一桌的 config 調整過）⇒ 不可能和舊 MCP 面漂移。輸出＝`View.raw`（＝`/api/state` 原樣）。

## 怎麼跑（協作端 挑掛法；兩種都現成）

```sh
# ① 她本機 stdio（最省事：agent 直接 spawn 這支）
TRPG_HUB=http://<前台>/hyakki_tower TRPG_TOKEN=<憑證> python3 mcp_adapter.py

# ② 我們跑 HTTP（她那邊只要連一條 URL）
TRPG_HUB=http://<前台>/hyakki_tower python3 mcp_adapter.py --http 0.0.0.0:8999
```
連線標頭：`Authorization: Bearer <account token>`（人／agent 的身分）、
`X-Table-GM-Token: <gm token>`（GM 面）。憑證也可以由每次呼叫的 `args.token` 帶（相容舊 MCP）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Callable, Dict, List, Optional

PROTOCOL = "2024-11-05"


# ─────────────────────────── GraphQL 傳輸（可注入，測試用）───────────────────────────

def http_transport(hub: str) -> Callable[..., Dict[str, Any]]:
    """回一個 `(query, variables, token, gm) -> data` 的傳輸：POST `<hub>/graphql`。"""
    import urllib.request

    def _t(query: str, variables: Dict[str, Any], token: str, gm: str) -> Dict[str, Any]:
        body = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        if gm:
            headers["X-Table-GM-Token"] = gm
        req = urllib.request.Request(hub.rstrip("/") + "/graphql", data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as fh:      # noqa: S310（我們自己的前台）
            out = json.loads(fh.read().decode("utf-8"))
        if out.get("errors"):
            raise RuntimeError(str((out["errors"][0] or {}).get("message") or "GraphQL 錯誤"))
        return out.get("data") or {}
    return _t


# ─────────────────────────────── 工具目錄／呼叫對映 ───────────────────────────────

_TOOLS_Q = "query($t: ID!) { table(id: $t) { tools { name description inputSchema } } }"


def list_tools(table: str, *, transport: Callable[..., Dict[str, Any]], token: str = "",
               gm: str = "") -> List[Dict[str, Any]]:
    """工具目錄：**問伺服器要**（`mcp_api.TOOLS` 那一份，按桌調整過）。"""
    data = transport(_TOOLS_Q, {"t": table}, token, gm)
    return list(((data.get("table") or {}).get("tools") or []))


def _camel(op: str) -> str:
    return "".join(p.capitalize() if i else p for i, p in enumerate(str(op).split("_"))) or op


def call_tool(name: str, args: Dict[str, Any], *, table: str,
              transport: Callable[..., Dict[str, Any]], token: str = "",
              gm: str = "") -> Dict[str, Any]:
    """一格工具的呼叫 → 對應的 GraphQL 欄位（**與 `mcp_api.mcp_call` 同語意**）。

    * `session` 一律**原樣帶過去**（規範：一副本一 session；帶錯要讓伺服器擋，不要在適配器裡「幫忙改」）。
    * 憑證：標頭優先；也吃 `args.token`（相容舊 MCP 客戶端）。
    """
    tk = str(token or args.get("token") or "")
    gmk = str(gm or args.get("gm_token") or "")
    sess = str(args.get("session") or "")
    base = {"t": table, "s": sess}

    if name == "table_view":
        q = ("query($t: ID!, $s: ID!, $seat: ID, $d: Detail) { table(id: $t) { session { id } "
             "view(session: $s, seat: $seat, detail: $d) { raw } } }")
        d = transport(q, dict(base, seat=args.get("seat"), d=args.get("detail")), tk, gmk)
        t = d.get("table") or {}
        out = dict((t.get("view") or {}).get("raw") or {})
        out.setdefault("session", ((t.get("session") or {}).get("id") or sess))   # 讀也把 session 給你
        return out

    if name == "table_join":
        q = ("mutation($t: ID!, $s: ID!, $seat: ID, $name: String!, $kind: JoinKind, $attrs: JSON) "
             "{ join(tableId: $t, session: $s, seat: $seat, name: $name, kind: $kind, attrs: $attrs) }")
        d = transport(q, dict(base, seat=args.get("seat"), name=args.get("name"),
                              kind=args.get("kind") or "agent", attrs=args.get("attrs")), tk, gmk)
        return d["join"]

    if name == "table_act":
        q = ("mutation($t: ID!, $s: ID!, $seat: ID!, $action: String!, $target: ID, $attr: String, "
             "$dc: Int, $note: String, $skill: ID, $item: ID) "
             "{ act(tableId: $t, session: $s, seat: $seat, action: $action, target: $target, attr: $attr, "
             "dc: $dc, note: $note, skill: $skill, item: $item) }")
        d = transport(q, dict(base, seat=args.get("seat"), action=args.get("action"),
                              target=args.get("target"), attr=args.get("attr"), dc=args.get("dc"),
                              note=args.get("note"), skill=args.get("skill"), item=args.get("item")), tk, gmk)
        return d["act"]

    if name == "table_say":
        q = "mutation($t: ID!, $s: ID!, $seat: ID!, $text: String!) { say(tableId: $t, session: $s, seat: $seat, text: $text) }"
        return transport(q, dict(base, seat=args.get("seat"), text=args.get("text")), tk, gmk)["say"]

    if name == "table_tell":
        q = ("mutation($t: ID!, $s: ID!, $seat: ID!, $to: ID!, $text: String!) "
             "{ tell(tableId: $t, session: $s, seat: $seat, to: $to, text: $text) }")
        return transport(q, dict(base, seat=args.get("seat"), to=args.get("to"),
                                 text=args.get("text")), tk, gmk)["tell"]

    if name == "table_secret":
        # ⚠ 備註：**沒有 `from`**——投遞者的身分由 session／GM 標頭決定（不讓參數冒充別人）。
        q = ("mutation($t: ID!, $s: ID!, $to: ID!, $text: String) "
             "{ gmSecret(tableId: $t, session: $s, to: $to, text: $text) }")
        return transport(q, dict(base, to=args.get("to"), text=args.get("text")), tk, gmk)["gmSecret"]

    if name == "table_gm":
        # op 直通（`gmRun` ← 舊 `/api/gm` 同一支 handler）：**不做 op→欄位名對映表**（那種表一定會漂）。
        _skip = ("session", "table", "tableId", "token", "gm_token")
        opts = {k: v for k, v in args.items() if k not in _skip}
        q = ("mutation($t: ID!, $s: ID!, $op: String!, $a: JSON) "
             "{ gmRun(tableId: $t, session: $s, op: $op, args: $a) }")
        return transport(q, {"t": table, "s": sess, "op": str(args.get("op") or ""),
                             "a": opts}, tk, gmk)["gmRun"]

    raise RuntimeError(f"不認得的工具：{name}")


# ───────────────────────────────── MCP 協議面 ─────────────────────────────────

def handle(req: Dict[str, Any], *, table: str, transport: Callable[..., Dict[str, Any]],
           token: str = "", gm: str = "") -> Optional[Dict[str, Any]]:
    """一格 JSON-RPC 請求 → 回應（`None`＝通知，不回）。"""
    meth = str(req.get("method") or "")
    rid = req.get("id")
    if meth == "initialize":
        res: Any = {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                    "serverInfo": {"name": "trpg-mcp-adapter", "version": "1.0"}}
    elif meth == "tools/list":
        res = {"tools": list_tools(table, transport=transport, token=token, gm=gm)}
    elif meth == "tools/call":
        p = req.get("params") or {}
        try:
            payload = call_tool(str(p.get("name") or ""), dict(p.get("arguments") or {}),
                                table=table, transport=transport, token=token, gm=gm)
            res = {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
                   "isError": False}
        except Exception as exc:                                      # noqa: BLE001
            res = {"content": [{"type": "text", "text": f"⚠ {exc}"}], "isError": True}
    elif meth.startswith("notifications/"):
        return None
    else:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"沒有 {meth}"}}
    return {"jsonrpc": "2.0", "id": rid, "result": res}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="TRPG MCP 適配器（stdio／HTTP）")
    ap.add_argument("--hub", default=os.environ.get("TRPG_HUB", ""), help="前台 URL（含桌前綴）")
    ap.add_argument("--table", default=os.environ.get("TRPG_TABLE", ""), help="桌 id（hub 根端點時要給）")
    ap.add_argument("--http", default="", help="跑 HTTP（例 0.0.0.0:8999）而不是 stdio")
    a = ap.parse_args(argv)
    if not a.hub:
        print("要 --hub 或 TRPG_HUB（例 http://127.0.0.1:8971/<桌>）", file=sys.stderr)
        return 2
    table = a.table or a.hub.rstrip("/").rsplit("/", 1)[-1]
    tk = os.environ.get("TRPG_TOKEN", "")
    gmk = os.environ.get("TRPG_GM_TOKEN", "")
    tr = http_transport(a.hub)

    if a.http:
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class H(BaseHTTPRequestHandler):
            def do_POST(self) -> None:                                # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                req = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
                out = handle(req, table=table, transport=tr, token=tk, gm=gmk)
                body = json.dumps(out or {}, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: Any) -> None:
                pass

        host, _, port = a.http.partition(":")
        print(f"[adapter] HTTP 模式：http://{host or '0.0.0.0'}:{port or 8999}/（桌 {table}）", file=sys.stderr)
        HTTPServer((host or "0.0.0.0", int(port or 8999)), H).serve_forever()
        return 0

    for line in sys.stdin:                     # stdio：一行一格 JSON-RPC
        line = line.strip()
        if not line:
            continue
        try:
            resp = handle(json.loads(line), table=table, transport=tr, token=tk, gm=gmk)
        except Exception as exc:                                      # noqa: BLE001
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
