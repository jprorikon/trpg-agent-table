"""core（自 Hub 拆出）。"""

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


class HubCoreMixin:
    @classmethod
    def load(cls, root: Optional[str] = None, log=print) -> None:
        if root:
            cls.root = os.path.abspath(root)
        for t in tdir.discover(cls.root):
            cls.add(t, log=log)
        # 一進程時「別張桌還在不在演」不必探 HTTP——直接查記憶體（見 web_api._busy_tables 的 hook）
        os.environ["TRPG_HUB_ROOT"] = cls.root
        cls.index_runs()                      # 2026-10-19：/tables/<run> 要靠這份索引反查桌子
        return None


    @classmethod
    def add(cls, t: Dict[str, Any], log=print) -> bool:
        tid = str(t.get("id") or "")
        if not tid or t.get("config_error") or tid in cls.services:
            return False
        cfgp = os.path.join(str(t.get("home")), "config.json")
        try:
            cfg = table_factory.load_cfg(cfgp)
            # ★ 2026-10-20（「桌和 run 等价」）：新桌的 config 带 `run_id`（＝table_id），
            #   開機就接**那一局**——table_id／session／run_id 三者同一個值。舊桌沒有這個鍵
            #   ⇒ 照舊接 `runs/current`。
            _rid = str(cfg.get("run_id") or "")
            # `def_dir`（劇本層定義目錄）由 discover 那一列帶著——同一劇本多桌時大家共用它
            svc = table_factory.build(str(t.get("home")), cfg, log=log,
                                      def_dir=str(t.get("def_dir") or "") or None,
                                      session=_rid or None, run_id=_rid or None)
        except Exception as exc:                                          # noqa: BLE001
            cls.errors[tid] = f"{type(exc).__name__}: {exc}"
            log(f"[hub] {tid} 組裝失敗：{cls.errors[tid]}")
            return False
        stop = threading.Event()
        with cls.lock:
            cls.services[tid] = svc
            cls.rows[tid] = t
            cls._stops[tid] = stop
            cls._ticks[tid] = table_factory.start_tick(svc, stop)
        cls.errors.pop(tid, None)
        log(f"[hub] {tid} 已上線（{len(cls.services)} 張桌）")
        return True


    @classmethod
    def set_watchable(cls, tid: str, on: bool, session: str = "", log=print) -> bool:
        """把「**這一局**允不允許觀戰」寫進 `runs/<run>/watch.json`。

        2026-10-20（設計者：「现在没有桌设计了，所有的都是局」）：以前寫桌的 `config.watch.enabled`，
        會讓「這一局關觀戰」影響到下一局，也讓換局後的新 owner 改到整桌。現在只動**指定的那一局**
        （`session` 省略 ⇒ 現役局）；config 的 `watch.enabled` 只當「舊局沒有檔」的預設。
        """
        row = cls.rows.get(tid) or {}
        home = str(row.get("home") or "")
        svc = cls.services.get(tid)
        if not home and svc is not None:
            home = str(getattr(svc, "home", "") or "")
        if not home:
            return False
        run = str(session or "")
        if not run and svc is not None:
            try:
                run = str(svc.table.session_id() or "")
            except Exception:                                                 # noqa: BLE001
                run = ""
        if not run:
            from trpg.core import store as _store                              # noqa: PLC0415
            run = str(_store.current_run(home) or "")
        if not run:
            return False
        try:
            from trpg.service import run_watch as _rw                          # noqa: PLC0415
            ok = _rw.set_enabled(home, run, bool(on), by="hub")
        except Exception as exc:                                              # noqa: BLE001
            log(f"[hub] {tid} 寫 {run}/watch.json 失敗：{exc}")
            return False
        if not ok:
            return False
        # 同進程的舊讀取（health／舊 watch_server）仍會看 cfg；把它同步成「這一局的值」。
        if svc is not None:
            try:
                w = dict(getattr(svc, "cfg", {}).get("watch") or {})
                w["enabled"] = bool(on)
                svc.cfg["watch"] = w
            except Exception:                                                 # noqa: BLE001
                pass
        if tid in cls.rows:
            cls.rows[tid]["watch_enabled"] = bool(on)
            cls.rows[tid]["watch_port"] = (cls.rows[tid].get("watch_port") if on else None)
        return True


    @classmethod
    def new_run(cls, tid: str, watchable: Optional[bool] = None,
                log=print) -> Dict[str, Any]:
        """**開一個新的 play 實例（run／桌）**——2026-10-20：「桌和 run 等价」。

        舊 run **原地不動**（它自己就是一個 run／桌，留在大廳）；這裡在**同一個劇本**下
        再建一個 run（新的目錄、新的 `table_id == run_id`、新的 GM token／觀戰開關），
        回 `{ok, table, parent, old_session, session}`。

        ⚠ 與舊語意不同：以前是「換掉 registry 那一格、舊局留在同一個桌目錄的 runs/ 裡」；
        現在「一局＝一張桌」⇒ 新局是新的 registry 條目，舊的照舊可玩／可回放。
        """
        svc = cls.services.get(tid)
        row = cls.rows.get(tid) or {}
        if svc is None:
            return {"ok": False, "error": f"沒有這一局：{tid}"}
        home = str(row.get("home") or getattr(svc, "home", "") or "")
        old = str(svc.table.session_id() or "")
        try:
            cfg = table_factory.load_cfg(os.path.join(home, "config.json"))
        except Exception:                                                  # noqa: BLE001
            cfg = json.loads(json.dumps(getattr(svc, "cfg", {}) or {}))
        cfg.pop("hidden", None)
        out = cls.create_table(
            str(row.get("module") or cfg.get("module") or ""),
            str(row.get("scenario") or cfg.get("scenario") or ""),
            str(cfg.get("name") or ""),
            watchable=watchable, template_cfg=cfg, log=log)
        if not out.get("ok"):
            return {"ok": False, "error": out.get("error") or "建立新局失敗",
                    "old_session": old}
        table = out.get("table") or {}
        cls.index_runs()
        log(f"[hub] {tid} 開新局：{old or '（無）'} → {table.get('id')}（舊局保留）")
        return {"ok": True, "table": str(table.get("id") or ""), "parent": tid,
                "home": str(table.get("home") or ""),
                "old_session": old, "session": str(table.get("session") or "")}

    @classmethod
    def create_table(cls, module: str, scenario: str, name: str = "",
                     watchable: Optional[bool] = None,
                     difficulty: Optional[str] = None, log=print,
                     template_cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """**建立一個 play 實例（run／桌）**（2026-10-20：「桌和 run 等价」）。

        模組 › 劇本 › run（桌）：`state/tables/<模組>/<劇本>/<run_id>/`；`run_id`＝`table_id`。
        同一個劇本可以有**多個 run**（各自獨立、互不影響）；`newRun` 就是再建一個 run。

        桌住 `modules/<模組>/scenarios/<劇本>/tables/<桌ID>/`——**定義留在劇本層共享**，
        桌只有自己的 `config.json` 與 `runs/`。桌 ID 自動取 `<模組>_<劇本>_<n>`；埠自動挑
        （避開現有桌、而且真的 bind 得起來）；`config.json` 從劇本層的 `config.json`
        （第一張桌的旋鈕）或 `config.example.json` 抄一份起步。

        回 `{ok, table:{id,module,scenario,name,port,session}}`——回來時**已經上線**（registry＋節拍都起了）。
        ⚠ 只有**前台**能做（要動 registry）。
        """
        with cls.lock:
            sdir = os.path.join(cls.root, layout.MODULES_DIR, str(module),
                                layout.SCENARIOS_DIR, str(scenario))
            if not os.path.isdir(sdir):
                return {"ok": False, "error": f"沒有這個劇本：{module}×{scenario}"}
            if template_cfg is not None:
                tpl: Optional[Dict[str, Any]] = json.loads(json.dumps(template_cfg))
            else:
                tpl = None
                for cand in ("config.json", "config.example.json"):
                    p = os.path.join(sdir, cand)
                    if os.path.isfile(p):
                        tpl = table_factory.load_cfg(p)
                        break
            if not tpl:
                return {"ok": False, "error": "劇本目錄沒有 config.json／config.example.json 可當底稿"}
            same = [r for r in cls.rows.values()
                    if str(r.get("module")) == str(module) and str(r.get("scenario")) == str(scenario)]
            n = len(same) + 1
            tid = f"{module}_{scenario}_{n}"
            while tid in cls.services or os.path.exists(layout.table_home(cls.root, module, scenario, tid)):
                n += 1
                tid = f"{module}_{scenario}_{n}"
            thome = layout.table_home(cls.root, module, scenario, tid)
            os.makedirs(thome, exist_ok=True)
            cfg = dict(tpl)
            # ★ 2026-10-20（實測 scenario_b_2）：新桌不能只抄 example 模板——
            #   example 帶 `auth.gm_token="REPLACE_ME_*"`、而且**沒有帳號層**（auth.db_env）⇒
            #   ① 帳號開 `/tables/<run>` 進來不會自動落座（DB 裡這張桌沒有任何 seat_assignment）
            #   ② GM 面板／kick 也認不了憑證。部署級鍵一律從**同模組現有的桌**繼承。
            donors = [r for r in cls.rows.values() if str(r.get("module")) == str(module)]
            donors.sort(key=lambda r: (0 if str(r.get("scenario")) == str(scenario) else 1,
                                       str(r.get("id"))))
            if donors:
                try:
                    _dcfg = table_factory.load_cfg(os.path.join(str(donors[0]["home"]), "config.json"))
                except Exception:                                                 # noqa: BLE001
                    _dcfg = {}
                for _k in ("auth", "session", "kick", "wake"):
                    if isinstance(_dcfg.get(_k), dict):
                        cfg[_k] = json.loads(json.dumps(_dcfg[_k]))
            # ★ 2026-10-20（設計者：「模組編輯器理論上就不該管這些事情」）：
            #   `auth`（帳號層＋GM 憑證）是**部署端**的事，不該由模組／編輯器決定。
            #   ⇒ 沒有可繼承的桌（全新模組的第一張桌）時，這裡自己長出一個能用的 auth：
            #   GM 憑證由 hub 生成、帳號層指向同一份 DB（有設定就沿用 default env）。
            _a = cfg.get("auth")
            if not isinstance(_a, dict):
                _a = {}
            # ★ 2026-10-20（review P4）：**每桌一把新的 GM 控制鍵**。以前從同模組的桌連
            #   `gm_token` 一起繼承 ⇒ 同模組所有桌共用一把，外流一把全模組失守。現在只繼承
            #   帳號層／部署鍵（`db_env`／`db`／`require_account`／`kick`／`wake`），GM 憑證重生成。
            _a["gm_token"] = secrets.token_urlsafe(24)
            log(f"[hub] 新桌 {tid} 的 GM 憑證由 hub 生成（auth.gm_token；每桌一把、別外流）")
            if not (_a.get("db_env") or _a.get("db")):
                try:
                    from trpg.core import db as _db                              # noqa: PLC0415
                    if os.path.exists(_db.DEFAULT_ENV_FILE):
                        _a["db_env"] = _db.DEFAULT_ENV_FILE
                        log(f"[hub] 新桌 {tid} 的帳號層指向預設 DB env")
                except Exception:                                                 # noqa: BLE001
                    pass
            _a.pop("_doc", None)
            cfg["auth"] = _a
            cfg["table_id"] = tid
            # ★ 2026-10-20（「桌和 run 等价」）：第一局就是這張桌本身。
            cfg["run_id"] = tid
            cfg.pop("hidden", None)
            # ★ 2026-10-20（P2）：把身分寫進 config——`module`／`scenario` 是帳號層（角色實例／錢包）
            #   與副本紀錄的維度，寫下來就不必每次從路徑反推。
            cfg["module"] = str(module)
            if scenario:
                cfg["scenario"] = str(scenario)
            if name:
                cfg["name"] = str(name)
            cfg["port"] = _pick_port(cls.root)
            if watchable is not None:          # 2026-10-19：建桌時就能決定給不給觀戰
                w = dict(cfg.get("watch") or {})
                w["enabled"] = bool(watchable)
                cfg["watch"] = w
            # ★ 2026-10-20（設計者：「在建房间的时候可以指定开局难度」）：寫進 config，
            #   由 table_factory.apply_difficulty 套 rules（death／levels）與 difficulty_mods；
            #   不給＝留空 ⇒ 用劇本 starting_difficulty 的 default（沒那段＝legacy 不變）。
            if difficulty:
                cfg["difficulty"] = str(difficulty)
            _rewrite_prefix(cfg, tid)
            with open(os.path.join(thome, "config.json"), "w", encoding="utf-8") as fh:
                json.dump(cfg, fh, ensure_ascii=False, indent=1)
            row = {"id": tid, "home": thome, "module": str(module), "scenario": str(scenario),
                   "module_name": layout.module_name(cls.root, str(module)),
                   "scenario_name": layout.scenario_name(cls.root, str(module), str(scenario)),
                   "def_dir": sdir, "port": cfg["port"], "legacy_layout": False}
            if not cls.add(row, log=log):
                return {"ok": False, "error": f"組裝失敗：{cls.errors.get(tid, '未知')}"}
            svc = cls.services.get(tid)
            # 觀戰開關是**局級**：第一局的檔案寫在它自己的 `runs/<run>/watch.json`。
            # config 的 `watch.enabled` 只當「舊局沒有檔」的預設，不再代表這一桌。
            if watchable is not None and svc is not None:
                try:
                    from trpg.service import run_watch as _rw                              # noqa: PLC0415
                    _rw.set_enabled(thome, str(svc.table.session_id() or ""),
                                    bool(watchable), by="hub:create")
                except Exception as exc:                                                  # noqa: BLE001
                    log(f"[hub] 寫第一局 watch.json 失敗（{type(exc).__name__}: {exc}）")
            # ★ 2026-10-20（設計者：「现在没有桌设计了，所有的都是局」）：
            #   不再把「上一張桌」的座次搬進新桌。座次是**局**的（`run_seat`），新局從空桌開始；
            #   玩家進場時自己選角色（`/api/pick_char`）。這也修掉「建桌者被舊帳號的座次鎖在門外」。
            log(f"[hub] 建立新 run／桌：{tid}（{module}×{scenario}，埠 {cfg['port']}）")
            return {"ok": True, "table": {"id": tid, "module": str(module), "scenario": str(scenario),
                                          "name": str(cfg.get("name") or tid), "port": cfg["port"],
                                          "home": thome,
                                          "session": str(svc.table.session_id() or "") if svc else ""}}


    @classmethod
    def drop(cls, tid: str) -> bool:
        with cls.lock:
            if tid not in cls.services:
                return False
            st = cls._stops.pop(tid, None)
            if st:
                st.set()                          # 這條節拍執行緒會在 stop.wait() 之後退出
            cls._ticks.pop(tid, None)
            cls.services.pop(tid, None)
            cls.rows.pop(tid, None)
        return True


    @classmethod
    def reload(cls, tid: str, log=print) -> Dict[str, Any]:
        """只重建**那一張桌**：停它的節拍 → 重新組裝（Table 建構時會從 `events.jsonl` 重播 ⇒ 無損）。

        ⚠ 這是「一進程」相對「一桌一 process」唯一少掉的能力（單獨重啟一張桌）的補償。
        """
        if tid not in cls.rows:
            _t = next((t for t in tdir.discover(cls.root) if t.get("id") == tid), None)
            if _t is None:
                return {"ok": False, "error": f"沒有這張桌：{tid}"}
            cls.rows[tid] = _t
        row = cls.rows[tid]
        before = 0
        try:
            before = len(cls.services[tid].table.events) if tid in cls.services else 0
        except Exception:                                                 # noqa: BLE001
            pass
        cls.drop(tid)
        ok = cls.add(row, log=log)
        after = 0
        try:
            after = len(cls.services[tid].table.events)
        except Exception:                                                 # noqa: BLE001
            pass
        return {"ok": bool(ok), "table": tid, "events_before": before, "events_after": after,
                "lossless": bool(ok) and before == after, "error": cls.errors.get(tid)}


    @classmethod
    def rescan(cls, log=print) -> Dict[str, Any]:
        """收新桌／移除已刪的桌（**不動**還在一起的桌 ⇒ 不會打斷對局）。"""
        found = {str(t.get("id")): t for t in tdir.discover(cls.root) if not t.get("config_error")}
        added = [tid for tid in found if tid not in cls.services and cls.add(found[tid], log=log)]
        removed = [tid for tid in list(cls.services) if tid not in found]
        for tid in removed:
            cls.drop(tid)
        cls.index_runs()
        return {"ok": True, "added": added, "removed": removed,
                "tables": sorted(cls.services)}

