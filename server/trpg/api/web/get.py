#!/usr/bin/env python3
"""**L6｜GET 面**（人類網頁／唯讀 REST／觀戰唯讀白名單）——`HandlerGetMixin`。

從 `web_api.py` 拆出（2026-10-20 補齊重構漏掉的檔案）。
紀律不變：路由只做參數搬運與回碼；判斷與裁決一律在 `TableService`／引擎；
回應一律 `Cache-Control: no-store`（素材圖除外，`_send()` 自己指定）。
"""
from __future__ import annotations

import email.utils
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from trpg.accounts import account_store
from trpg.accounts import auth
from trpg.core import layout
from trpg.core.errors import RuleError, TellError
from trpg.service import assets
from trpg.service import table_directory as tdir
from trpg.service.wake import maybe_kick_round, round_kick_key

if TYPE_CHECKING:
    from trpg.service.service import TableService

from .base import gm_token_ok, HandlerBaseMixin, _watch_token_ok, _auth_info, _is_api_path, _legacy_rest_ok, _watch_json, _maybe_writeback, _chars_available, _pick_char, _busy_tables, _busy_msg, _accounts_of, asset_response


class HandlerGetMixin(HandlerBaseMixin):
    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        # ★ 素材（**模組級靜態面**，2026-10-09）：放在最前面處理——**不看身分、不進 `/api/*` 退役閘**。
        #   這樣各桌頁面與觀戰頁（匿名）都能直接吃同一條 `/assets/<模組>/…`（單桌模式與 hub 共用同一支
        #   `asset_response`，不養第二份實作）。
        if path.startswith(assets.URL_PREFIX + "/"):
            # repo 根：單桌模式用「這一桌所在的那棵樹」（臨時桌／測試也對得上）；
            # 拿不到才退回本檔所在目錄（＝正式部署時的 repo 根）。
            try:
                _root = layout.repo_root(self.service.home)
            except Exception:                                         # noqa: BLE001
                _root = HERE
            _c, _b, _t, _x = asset_response(_root, self.path, self.headers)
            return self._send(_c, _b, _t, _x)
        # ★ 2026-10-07：**`/api/*` 退役**——外部一律 410（請走 `POST /graphql`）。
        #   ⚠ **內部**的 GraphQL（`graphql_api._Shim`，`_internal=True`）不受影響：新的面就是
        #   「同一支 handler 換一個入口」實作的，政策只有一份。
        # ⚠ **觀戰模式豁免**：`_watch_json` 的唯讀白名單是**觀戰前門**（匿名公開投影），不是已退役的
        #   客戶端面——2026-10-07 退役時把它一起擋掉，觀戰頁就什麼都看不到了。
        if (_is_api_path(path) and not getattr(self, "_internal", False)
                and not getattr(self, "_watch_mode", False) and not _legacy_rest_ok()):
            self._json(410, {"error": "`/api/*` 已退役（2026-10-07）——請走 `POST /graphql`"
                                      "（契約＝docs/schema.graphql）"})
            return
        q = {}
        if "?" in self.path:
            for kv in self.path.split("?", 1)[1].split("&"):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    q[k] = urllib.parse.unquote_plus(v)
        # ── **觀戰模式**（`hub_server` 的 `/<桌>/watch/…` 用；單桌模式的 `watch_server.py` 是另一支獨立
        #    行程）：不認任何身分（連 token／gm_token 都不看）⇒ 直接是**公開投影**，並只放行唯讀白名單。
        #    以前那條路要 `watch.read_token`（因為是另一個行程、會被 require_account 擋）——同進程不需要。
        if getattr(self, "_watch_mode", False):
            if path not in self.WATCH_GET:
                self._json(404, {"error": "觀戰桌沒有這一條（唯讀前門）"})
                return
            q.pop("token", None)
            q.pop("gm_token", None)
            q.pop("seat", None)
            if path == "/health":
                self._json(200, {"status": "ok", "service": "trpg-watch",
                                 "table": self.service.cfg.get("table_id"), "readonly": True})
                return
            _watch_json(self, self.service, path, q)
            return
        # ── account 認證（讀也算）：`/api/state` 帶了有效 token ⇒ **以 token 的席位為準**
        #    （`?seat=p2&token=<p1 的>` 會被蓋成 p1）；開了 `require_account` 就要求有效 token。
        #    沒有 accounts.json 的桌 ⇒ no-op。
        # ⚠ 例外：`/api/log?meta=1` 是**刻意開的最小資訊窗**（只回筆數／尾序號、不含任何事件內容——
        #    協作端 2026-10-05 §5 的獨立複核、大廳的探活都靠它）⇒ 帳號閘不擋它。
        _meta_only = path == "/api/log" and str(q.get("meta")) in ("1", "true", "yes")
        if path in ("/api/state", "/api/log") and not _meta_only:
            _acct = _accounts_of(self.service)
            _req = bool((self.service.cfg.get("auth") or {}).get("require_account"))
            _tok = auth.token_from_request(headers=self.headers, query=q)
            # GM 那條路（`?gm_token=`＝`config.auth.gm_token`）**不受 account 認證影響**：
            # gm_token 是「桌子的控制鍵」，docs/API.md 就寫它換 GM 全視圖。
            # ⚠ 2026-10-07 修：account 層接上去之後這裡先跑 `check_seat` ⇒ `require_account=true` 的桌
            #    連 GM 都被 403（**GM 面板整個壞掉**，線上就中了——GM 明明握著 gm_token）。
            # ★ 2026-10-24（review R1）：走 `gm_token_ok()`——**配置沒設 token ⇒ 一律 False**。
            #   從前是 `_secret_eq(給的, `…or ""`)` ⇒ 沒設定的桌被「不帶參數」比成 True（GM 全視圖外洩）。
            _gm_ok = gm_token_ok(self.service.cfg, q.get("gm_token"))
            if _watch_token_ok(self.service, _tok):
                # 觀戰桌（唯讀前門）：**只給公開投影**——不認席位、不給私密、不看 gm_token。
                q.pop("seat", None)
                q.pop("gm_token", None)
            elif not _gm_ok:
                _seat, _who, _err = auth.check_seat(_acct, _tok, q.get("seat"), require=_req)
                if _err:
                    self._json(403, {"error": _err})
                    return
                if _seat:
                    q["seat"] = _seat
                elif _who:
                    # 有認證、但這一局沒席位 ⇒ 旁觀：**丟掉客戶端自己報的席位**（照它放行＝冒充），只給公開投影。
                    q.pop("seat", None)
        _maybe_writeback(self.service, _accounts_of(self.service))   # 收局寫回（讀也能順手結清）
        if path == "/api/me":
            # 「我是誰、這一局坐哪一席、我的 char 實例與錢包」——login UI 的入口（**不含 token**）
            _acct = _accounts_of(self.service)
            _who = _acct.resolve(auth.token_from_request(headers=self.headers, query=q))
            if _who is None:
                self._json(403, {"error": "token 不認得（或用的是舊憑證）"})
                return
            _seat = _who.get("seat")
            _busy_map: Dict[str, Any] = {}
            for _c in (_who.get("chars") or []):
                _b = _busy_tables(self.service, _who["account"], _c)
                if _b:
                    _busy_map[_c] = _b
            self._json(200, {"account": _who["account"], "display": _who.get("display"),
                             "kind": _who.get("kind"), "seat": _seat,
                             "char": (_acct.char_of(_seat) if _seat else None),
                             "instance": (_acct.instance(_who["account"], _acct.char_of(_seat) or "")
                                          if _seat else {}),
                             "wallet": _acct.wallet(_who["account"]),
                             # ★ 我的角色裡「還在別張活桌出場」的（同帳號同角色不能同時兩桌；
                             #   前端可據此把那個角色標灰／說明原因）
                             "chars_busy": _busy_map,
                             "chars_available": _chars_available(self.service, _acct)})
            return
        if path in ("/", "/index.html"):
            # ★ 2026-10-19（設計者定案，拆前後台）：後台不送網頁——頁面在前台容器（nginx）。
            self._json(410, {"error": "這是 TRPG 後台：不送網頁",
                             "hint": "客戶端面只有 POST /graphql 與 POST /mcp；"
                                     "頁面由前台容器（trpg/frontend-nuxt）提供"})
            return
        if path == "/health":
            _ops = self.service.table.adapter_view()      # L6 的讀出口（L2）；不直接摸 state
            # ⚠ 一定要露 `ended`：`/hold-trpg`（tools/hold_trpg.py）就是靠它挑「**還沒收局**的那張桌」——
            #   從前只回 seats/turn，於是多桌時它會挑「事件流最大的」＝挑到**已收局**的那張
            #   （2026-10-07 實測：暫停指令打去 示範模組，真正在玩的 hyakki 沒被停）。純附加。
            # `session`＝這一局的副本 id：`/hold-trpg` 用它**選局**（設計者 2026-10-07：
            # 「hold-trpg 现在需要选取到session才可用」）。純附加。
            # 2026-10-19（設計者）：「GM 和 NPC 都不應該算席，只有玩家（agent 和人類）才算席」
            # ⇒ `seats`＝**玩家席**（human／agent）；`seats_total`＝含 GM／NPC 的總格數（純附加，給要全量的地方）。
            # ⚠ `adapter_view()` 的 seats 可能是**字典**（帶 kind）也可能是**席位 id 字串**
            #   （測試夾具／舊投影）⇒ 兩種都認：字串就回頭查席位表的 kind。
            _raw_seats = _ops.get("seats") or []
            _state_seats = (getattr(self.service.table, "state", None) or {}).get("seats") or {}

            def _kind_of(_x: Any) -> str:
                if isinstance(_x, dict):
                    return str(_x.get("kind") or "")
                _b = _state_seats.get(str(_x)) or {}
                return str((_b or {}).get("kind") or "") if isinstance(_b, dict) else ""

            _pl = [x for x in _raw_seats if _kind_of(x) in ("human", "agent")]
            _cap = len(getattr(self.service.table, "seat_plan", ()) or ()) or None
            # ★ 音訊（2026-10-20；純附加）：音訊表在不在、幾首、cue 有沒有指到不存在的鍵。
            #   模組沒放 audio.json ⇒ 這一格不出現（legacy／沒聲音的模組一字不變）。
            _au = getattr(self.service.table, "audio", None)
            try:
                _auh = _au.health() if _au is not None else {}
            except Exception:                                          # noqa: BLE001
                _auh = {}
            self._json(200, {"status": "ok", "table_id": self.service.cfg.get("table_id"),
                             "seats": len(_pl), "seats_total": len(_raw_seats),
                             # 劇本設計的玩家席數（大廳顯示 `n/m 席` 的分母；純附加）
                             "seat_cap": _cap,
                             "ended": bool(_ops.get("ended")),
                             "session": str(_ops.get("run_id") or ""),
                             "turn": (_ops["turn"] or {}).get("seat"),
                             # 大廳（2026-10-19 改版）要的「第幾景／開著沒／暫停／還在等誰」——全是公開投影
                             # 本來就有的事（view 的 stage／scene_waiting／paused），這裡只是讓桌外也問得到。純附加。
                             "round_no": int(_ops.get("round_no") or 0),
                             "paused": bool(_ops.get("paused")),
                             "waiting": list(_ops.get("pending_actors") or []),
                             **({"audio": _auh} if (_auh.get("present") or _auh.get("warnings"))
                                else {})})
            return
        if path == "/api/state":
            seat = q.get("seat")
            # ★ 2026-10-24（review R1）：`?gm_token=`（空值）從前與「沒設定」相等 ⇒ GM 全視圖。
            gm = gm_token_ok(self.service.cfg, q.get("gm_token"))
            # `detail=compact`（2026-10-06）：投影的省 token 版——以前只有 MCP 那條路吃得到，
            # 現在兩個入口同一支（投影的參數面一致）。省略＝full，舊客戶端一字不動。
            try:
                _v = self.service.table.view(seat=seat, gm=gm, detail=q.get("detail"))
            except Exception as exc:  # noqa: BLE001
                # 席位还不存在（例如带 ?seat=p1 的入席链接先被打开）→ 给公开视图 + 提示，不报错
                try:
                    _v = self.service.table.view(seat=None, gm=gm)
                    _v["seat_unknown"] = seat
                except Exception:  # noqa: BLE001
                    self._json(400, {"error": str(exc)})
                    return
            _v["auth"] = _auth_info(self.service)      # 前端據此決定要不要顯示登入卡（L6 的事，不進投影）
            self._json(200, _v)
            return
        if path == "/api/log":
            # 唯讀 meta（協作端 2026-10-05 §5 提議，家側 2026-10-05 拍板落地）：
            #   只回「事件筆數 ＋ 尾筆 seq」，**不帶任何事件內容**——讓只有公開投影可看的
            #   一方也能對「我沒動資料」的聲明做獨立複核。有意開的最小資訊窗：泄露的只有
            #   筆數／尾序號，不含內容；帶不帶 gm_token 都回同一組原始（raw）數字。
            if q.get("meta") in ("1", "true", "yes"):
                raw = self.service.table.events
                self._json(200, {"count": len(raw),
                                 "last_seq": (raw[-1].get("seq") if raw else None)})
                return
            # 兩段式閘（協作端 2026-10-05 §2）：
            #   沒帶 gm_token ⇒ 只回公開投影 = **view 白名單 ∩ public:true**
            #                    （＝ view.log 看得到的那些；`acted` 這類內部記帳被排除）
            #   帶 gm_token   ⇒ 回原始全量（維持「GM／除錯用的原始流」本來用途）
            if gm_token_ok(self.service.cfg, q.get("gm_token")):          # R1：空值／未設定 ⇒ 公開投影
                evs = self.service.table.events
            else:
                evs = self.service.table.public_events()
            self._json(200, {"events": evs})
            return
        if path == "/api/roll_attrs":
            # 初期屬性只能用骰的（協作端 2026-10-05 §2325；桌端我方落地）：
            #   唯讀、可重問——值＝seed＋席位＋屬性（同 seed＋席位＝同結果，重放不必額外存檔、
            #   也不能一直重擲到滿意）。沒開 rules.attr_roll 的桌回 409（前端據此決定是否顯示擲骰）。
            t = self.service.table
            seat = q.get("seat") or "p1"
            if not t.rules.get("attr_roll"):
                self._json(409, {"error": "這張桌的初期屬性不是用骰的（rules.attr_roll 沒開）"})
                return
            self._json(200, {"seat": seat, "attrs": t.roll_attrs(seat),
                             "range": list(t.rules["attr_range"]), "rolled": True})
            return
        if path == "/api/map":
            # 静态地图（公开知识）：节点坐标 + 边；换作品只改 board.json
            t = self.service.table
            self._json(200, t.board_map() if hasattr(t, "board_map") else {})
            return
        if path == "/mcp":
            self._send(405, b'{"error":"use POST /mcp"}', "application/json",
                       {"Allow": "POST, DELETE"})
            return
        self._json(404, {"error": "not found"})

