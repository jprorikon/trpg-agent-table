"""common（自 __init__.py 拆出）"""

from __future__ import annotations
import argparse
import atexit
import json
import os
import secrets
import socket
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from trpg.paths import ROOT as HERE  # repo 根（資料／vendor／docs 都在這裡）
from trpg.kick import receiver as kick_receiver                                                      # noqa: E402
from trpg.core import layout as layout                                                             # noqa: E402
from trpg.service import table_directory as tdir                                            # noqa: E402
from trpg.service import table_factory as table_factory                                                      # noqa: E402
from trpg.api import web as web_api                                                            # noqa: E402
from trpg.api.web import Handler as WebHandler                                 # noqa: E402

_API_ONLY: Dict[str, Any] = {
    "error": "這是 TRPG 後台：不送網頁",
    "hint": "客戶端面只有 POST /graphql（桌是欄位）與 POST /mcp（agent 面）；"
            "另有 /assets/<模組>/…（圖片）與 /health（運維）。"
            "頁面（大廳／桌面／觀戰）由前台容器（trpg/frontend-nuxt，nginx）提供。",
    "endpoints": ["POST /graphql", "POST /mcp", "GET /assets/<模組>/…", "GET /health"],
}

def _pick_port(root: str, start: int = 18971) -> int:
    """挑一個沒被現有桌佔、而且**真的 bind 得起來**的埠。"""
    used = set()
    for r in tdir.discover(root):
        try:
            used.add(int(r.get("port") or 0))
        except (TypeError, ValueError):
            continue
    p = max([start] + [x for x in used if x]) + 1
    while p < 65535:
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                p += 1
    return start + 1

def _seat_counts(svc: Any) -> tuple:
    """(玩家席數, 全部席數)——2026-10-19（設計者）：「GM 和 NPC 都不應該算席，只有玩家才算」。

    席位資料可能來自 `adapter_view()`（字典，帶 kind）或舊投影（席位 id 字串）⇒ 兩種都認；
    字串就回頭查 `table.state["seats"]` 的 kind。
    """
    try:
        ops = svc.table.adapter_view()
        raw = list(ops.get("seats") or [])
        st = (getattr(svc.table, "state", None) or {}).get("seats") or {}
    except Exception:                                                 # noqa: BLE001
        return (0, 0)

    def kind_of(x: Any) -> str:
        if isinstance(x, dict):
            return str(x.get("kind") or "")
        b = st.get(str(x)) or {}
        return str(b.get("kind") or "") if isinstance(b, dict) else ""

    return (sum(1 for x in raw if kind_of(x) in ("human", "agent")), len(raw))

def _seat_cap(svc: Any) -> Optional[int]:
    """劇本**設計的玩家席數**（`seats.json` 的 human／agent 列數）——大廳顯示 `n/m 席` 的分母。

    讀定義層（`svc.def_dir`）；讀不到回 None（大廳就只顯示 n，不編一個假分母）。
    """
    try:
        with open(os.path.join(getattr(svc, "def_dir", "") or "", "seats.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    rows = [r for r in (doc.get("seats") or []) if isinstance(r, dict)]
    return sum(1 for r in rows if str(r.get("kind")) in ("human", "agent"))

def _rewrite_prefix(cfg: Dict[str, Any], tid: str) -> None:
    """模板可能帶著「指回大廳」的欄位（`public_prefix`／`hub_base`）⇒ 改指到新桌。"""
    if str(cfg.get("public_prefix") or ""):
        cfg["public_prefix"] = "/" + str(tid)
    hb = str(cfg.get("hub_base") or "")
    if hb:
        cfg["hub_base"] = hb.rstrip("/").rsplit("/", 1)[0] + "/" + str(tid)

def _cur_session(svc: Any) -> str:
    """這張桌**現役副本**的 id（＝引擎 `run_id`／session）。取不到就空字串。

    用在 `/mcp` 的選桌：一副本一 session（UUID4）⇒ 「session 對到哪張桌」是唯一的。
    """
    try:
        fn = getattr(svc, "_session_id", None)
        return str(fn() if callable(fn) else (svc.table.adapter_view().get("run_id") or ""))
    except Exception:                                                    # noqa: BLE001
        return ""
