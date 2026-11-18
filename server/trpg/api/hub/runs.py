"""runs（自 Hub 拆出）。"""

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
from .common import _pick_port, _rewrite_prefix, _seat_cap, _seat_counts


class HubRunsMixin:
    @classmethod
    def index_runs(cls) -> Dict[str, Dict[str, Any]]:
        """掃「一局一夾」的權威（`<桌 home>/runs/<run>/`）⇒ `run → 桌`。

        2026-10-19 URL 正規化：對外路徑是 `/tables/<run>`、`/watchtables/<run>`（**不含模組與劇本**），
        所以 hub 要能由 run 反查桌子。**不新增檔案格式**——盤上本來就有一局一夾
        （旁有 `current` 記著現役那一局）。★ 2026-10-20「桌和 run 等价」之後，新的 play 實例
        各自一個 home（`table_id`＝`run_id`）；舊 home 仍可能有多個歷史 run，這裡照樣索引。

        ⚠ 只掃**已載入的桌**（`services`／`rows` 給的 home），不寫死 `state/tables/<模組>/<劇本>/<桌>`
        這個深度：測試夾具（`campaigns/<名>`）與舊桌的層級不一定一樣，寫死會讓 fixture 全都查不到 run。
        """
        out: Dict[str, Dict[str, Any]] = {}
        homes: Dict[str, str] = {}
        for tid in list(cls.services) + [t for t in cls.rows if t not in cls.services]:
            home = ""
            svc = cls.services.get(tid)
            if svc is not None:
                home = str(getattr(svc, "home", "") or "")
            if not home:
                home = str((cls.rows.get(tid) or {}).get("home") or "")
            if home:
                homes[tid] = home
        for tid, home in homes.items():
            rdir = os.path.join(home, "runs")
            if not os.path.isdir(rdir):
                continue
            row = cls.rows.get(tid) or {}
            cur_run = ""
            try:
                with open(os.path.join(rdir, "current"), encoding="utf-8") as fh:
                    cur_run = fh.read().strip()
            except OSError:
                cur_run = ""
            for run in sorted(os.listdir(rdir)):
                if run == "current" or not os.path.isdir(os.path.join(rdir, run)):
                    continue
                out[run] = {"tid": tid, "module": str(row.get("module") or ""),
                            "scenario": str(row.get("scenario") or ""), "home": home,
                            "current": bool(cur_run and run == cur_run)}
        cls.runs = out
        return out


    @classmethod
    def run_info(cls, run: str) -> Dict[str, Any]:
        if not cls.runs:
            cls.index_runs()
        hit = cls.runs.get(str(run or ""))
        if hit is None:                       # 可能剛換局／剛建桌 ⇒ 重建一次再查（便宜：一個 listdir）
            cls.index_runs()
            hit = cls.runs.get(str(run or ""))
        return hit or {}


    @classmethod
    def row(cls, tid: str) -> Dict[str, Any]:
        """大廳／health 要的那一列（**直接問記憶體**，不比照 HTTP 探活）。"""
        t: Dict[str, Any] = dict(cls.rows.get(tid) or {"id": tid})
        svc = cls.services.get(tid)
        if svc is None:
            t.update({"online": False, "ended": False, "turn": None, "seats": None,
                      "events": None, "last_seq": None, "config_error": False,
                      "error": cls.errors.get(tid)})
            return t
        try:
            ops = svc.table.adapter_view()
            raw = svc.table.events
            _pl, _all = _seat_counts(svc)
            t.update({"online": True, "ended": bool(ops.get("ended")),
                      "turn": (ops.get("turn") or {}).get("seat"),
                      "seats": _pl, "seats_total": _all, "seatCap": _seat_cap(svc),
                      "events": len(raw),
                      "last_seq": (raw[-1].get("seq") if raw else None)})
        except Exception as exc:                                          # noqa: BLE001
            t.update({"online": False, "error": f"{type(exc).__name__}: {exc}"})
        # 2026-10-19 URL 正規化：對外前綴是 `/tables/<run>`（不含模組／劇本）；
        # `legacy_prefix` 留著給舊連結與大廳的相容判斷。
        run = ""
        try:
            run = str(getattr(getattr(svc, "table", None), "session_id", lambda: "")() or "")
        except Exception:                                                 # noqa: BLE001
            run = ""
        if run:
            t["run"] = run
        t["prefix"] = ("/tables/" + run) if run else ("/" + tid + "/")
        t["watch_prefix"] = ("/watchtables/" + run) if run else ("/" + tid + "/watch/")
        t["legacy_prefix"] = "/" + tid + "/"
        return t


    @classmethod
    def view_all(cls) -> List[Dict[str, Any]]:
        return [cls.row(tid) for tid in sorted(cls.rows)]

