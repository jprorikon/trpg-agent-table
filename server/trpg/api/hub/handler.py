"""handler（自 __init__.py 拆出）"""

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

from .common import _API_ONLY, _cur_session
from .hub import Hub

class Handler(WebHandler):
    """把 `web_api` 的路由**按子路徑分派到那一張桌**；大廳／health／管理面自己處理。

    ⚠ 刻意**不覆寫** `_json`／`log_message`／`_body`：那幾個都在 `web_api.Handler` 上，
    照用同一支（同一個行為不要兩份實作）。
    """

    server_version = "trpg-hub"

    def _dispatch(self, method: str) -> None:
        raw = self.path
        path = raw.split("?", 1)[0]
        try:
            if path in ("/", "/index.html"):
                # ★ 2026-10-19（設計者定案，拆前後台）：**後台不再送任何網頁**。
                #   頁面（大廳／桌面／觀戰）是前台容器（trpg/frontend-nuxt，nginx）的事；
                #   後台只留兩個客戶端端點：`POST /graphql`、`POST /mcp`
                #   （另留 `/assets/<模組>/…` 圖片資料面與 `/health` 運維面）。
                return self._json(410, _API_ONLY)
            if path == "/health":
                return self._json(200, {
                    "status": "ok", "service": "trpg-hub",
                    "tables": [{"id": t["id"], "run": t.get("run"), "prefix": t.get("prefix"),
                                "watch_prefix": t.get("watch_prefix"),
                                "legacy_prefix": t.get("legacy_prefix"), "online": t.get("online"),
                                "ended": t.get("ended"), "seats": t.get("seats"),
                                "seats_total": t.get("seats_total"), "seat_cap": t.get("seatCap"),
                                "events": t.get("events"), "error": t.get("error")}
                               for t in Hub.view_all()],
                    "note": "純後台（2026-10-19 拆前後台）：客戶端面只有 /graphql（桌是欄位）"
                            "＋ /mcp（agent 面）；頁面由前台容器（nginx）負責，後台不再送 HTML；"
                            "另留 /assets/<模組>/…（圖片）與 /health（運維）"})
            if path == "/graphql":
                # **一個端點**（設計者 2026-10-07：「一個端點，桌作為 graphql 的欄位」）：
                # 大廳根端點服務**所有桌**——`table(id:)` 用 `Hub.services` 查（registry 那一份），
                # `tables` 走 `layout.scan`（`graphql_api` 內）。契約＝`docs/schema.graphql`。
                from trpg.api import graphql as graphql_api                                            # noqa: PLC0415
                _st, _payload = graphql_api.handle(
                    self._body(), svc=None, lookup=lambda _t: Hub.services.get(_t),
                    root=Hub.root, headers=self.headers,
                    # 前台層：換局要換 registry 那一格（2026-10-19 起可帶 watchable）
                    new_run=lambda _t, watchable=None: Hub.new_run(_t, watchable=watchable),
                    create_table=lambda m, s, nm="", watchable=None, difficulty=None: Hub.create_table(
                        m, s, nm, watchable=watchable, difficulty=difficulty),
                    set_watchable=lambda t, on, session="": Hub.set_watchable(t, on, session=session))
                return self._json(_st, _payload)
            if path == "/mcp":
                # ★ **多桌共用的 MCP 前門**（owner 2026-10-08 選 (b)；見檔頭）——
                #   哪張桌由呼叫自己決定（`arguments.session`／`arguments.table`／標頭 X-Table-Id），
                #   GM 身分由 `X-Hub-GM-Token` 帶 ⇒ GM profile 的設定不必逐桌改。
                return self._hub_mcp(method)
            if path.startswith("/__hub/"):
                return self._admin(method, path)
            # ★ 素材（模組級靜態面，2026-10-09）：hub 根底下直接服務 `/assets/<模組>/…`
            #   （模組級＝與桌 id 解耦 ⇒ 同模組的多張桌共用一份；觀戰頁匿名也吃得到）。
            #   實作只有一份（`web_api.asset_response`），這裡只是拿對的 repo 根去叫它。
            if path.startswith(web_api.assets.URL_PREFIX + "/"):
                _c, _b, _t, _x = web_api.asset_response(Hub.root, raw, self.headers)
                return self._send(_c, _b, _t, _x)
            # ★ 2026-10-19（設計者定案，拆前後台）：**桌級子路徑全部收掉**。
            #   從前 `/tables/<run>`（桌面頁）、`/watchtables/<run>`（觀戰頁）、`/<桌>/…`（舊前綴）
            #   是「後端順便送頁面」的產物；頁面搬去前台容器之後這些路徑只剩兩種用途：
            #   （a）送 HTML（已經不是後台的事）、（b）當桌級 API／觀戰前門（改由 `/graphql`
            #   按 query 做訪問控制，見 `graphql_api._anon_watch`）。所以一律 404，並講清楚去哪裡。
            return self._json(404, {
                "error": f"後台沒有這條路：{path}",
                "hint": "純後台只有 POST /graphql（桌是欄位）、POST /mcp（agent 面）、"
                        "/assets/<模組>/…（圖片）、/health（運維）；"
                        "頁面（/、/tables/<run>、/watchtables/<run>）由前台容器負責",
                "tables": sorted(Hub.services)})
        except BrokenPipeError:
            return
        except Exception as exc:                          # noqa: BLE001
            # **一桌的請求炸掉，不帶走別桌**（單一進程最要緊的紀律）
            import traceback
            traceback.print_exc()
            try:
                self._json(500, {"error": f"這一桌的請求失敗（不影響別桌）：{type(exc).__name__}: {exc}"})
            except Exception:                                                 # noqa: BLE001
                pass

    # ------------------------------------------------------------------ 管理面
    def _admin(self, method: str, path: str) -> None:
        token = self.headers.get("X-Hub-Token") or ""
        if method != "POST" and path not in ("/__hub/status",):
            return self._json(405, {"error": "管理面只吃 POST（/__hub/status 例外）"})
        if not Hub.token_ok(token):
            return self._json(403, {"error": "要 X-Hub-Token（值在 logs/hub.token，600）"})
        body: Dict[str, Any] = {}
        if method == "POST":
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n else {}
            except Exception:                                                 # noqa: BLE001
                body = {}
        if path == "/__hub/status":
            return self._json(200, {"tables": sorted(Hub.services), "errors": Hub.errors,
                                    "rows": [{k: v for k, v in t.items() if k != "cast"}
                                             for t in Hub.view_all()]})
        if path == "/__hub/reload":
            tid = str(body.get("table") or body.get("table_id") or "")
            if not tid:
                tid = str(body.get("id") or "")
            return self._json(200, Hub.reload(tid))
        if path == "/__hub/rescan":
            return self._json(200, Hub.rescan())
        return self._json(404, {"error": f"沒有這條管理路：{path}"})

    # ------------------------------------------------------------------ 多桌共用 MCP
    def _hub_mcp_table(self, body: Dict[str, Any]) -> tuple:
        """這一發 MCP 打**哪一張桌**：回 `(tid, err)`。

        判據順序（誰在前面誰說了算）：
          ① 標頭 `X-Table-Id`（運維手動指定最直接）；
          ② `tools/call` 的 `arguments.table`；
          ③ `tools/call` 的 `arguments.session`——**這一局是哪張桌的現役副本**（一副本一 session，
              id 是 UUID4 ⇒ 唯一）⇒ GM 只要照喚醒訊息帶 session 就會落到自己那張桌；
          ④ 都沒有（`initialize`／`tools/list` 這種「還沒開始叫工具」的請求）：用**唯一在線**那張桌
             ——工具目錄各桌同形（`mcp_api.TOOLS`＋按桌微調），實際呼叫時才用 ①–③ 選桌。
        """
        hdr = (self.headers.get("X-Table-Id") or "").strip()
        if hdr:
            if hdr in Hub.services:
                return hdr, ""
            return "", f"標頭 X-Table-Id 指到不存在的桌：{hdr}"
        params = body.get("params") if isinstance(body.get("params"), dict) else {}
        args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        want_t = str(args.get("table") or "").strip()
        if want_t:
            if want_t in Hub.services:
                return want_t, ""
            return "", f"參數 table 指到不存在的桌：{want_t}"
        want_s = str(args.get("session") or "").strip()
        if want_s:
            hit = [t for t, s in Hub.services.items() if _cur_session(s) == want_s]
            if len(hit) == 1:
                return hit[0], ""
            if not hit:
                return "", (f"沒有哪張桌的現役 session 是 {want_s}（換局之後要帶**新的** session；"
                            f"`table_view` 的回應裡有）")
            return "", f"session 對到多張桌（{'、'.join(sorted(hit))}）⇒ 請帶標頭 X-Table-Id"
        if Hub.services:
            return sorted(Hub.services)[0], ""
        return "", "hub 上一張桌都沒有"

    def _hub_mcp(self, method: str) -> None:
        """`/mcp`：剝到「哪張桌」之後，**同一條路**（`web_api.Handler.do_POST` 的 `/mcp`）。"""
        if method != "POST":
            # 桌端 `/mcp` 也只有 POST（`/<桌>/mcp` 同契約）；GET/DELETE 一律 405。
            return self._json(405, {"error": f"{method} 不在 MCP 介面上（只有 POST）"})
        body: Dict[str, Any] = {}
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n:
                body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception:                                                  # noqa: BLE001
            return self._json(400, {"error": "MCP 的 body 不是 JSON"})
        _gm = (self.headers.get("X-Hub-GM-Token") or "").strip()
        if _gm and not Hub.gm_token_ok(_gm):
            # 留下「有帶但帶錯」的痕跡（**不印值**）——沒有這行，外部只看到工具層的
            # 「GM token 不对」，查不出是「沒帶」還是「帶錯」（2026-10-08 實測卡在這裡）。
            print(f"[hub] /mcp：X-Hub-GM-Token 帶了但**不對**（長度 {len(_gm)}）⇒ 403", flush=True)
            return self._json(403, {"error": "X-Hub-GM-Token 不對（值在 logs/hub_gm.token，600）"})
        tid, err = self._hub_mcp_table(body)
        if err:
            return self._json(400, {"error": err, "tables": sorted(Hub.services)})
        svc = Hub.services.get(tid)
        if svc is None:
            return self._json(404, {"error": f"沒有這張桌：{tid}", "tables": sorted(Hub.services)})
        _meth = str((body.get("params") or {}).get("name") or body.get("method") or "")
        print(f"[hub] /mcp → 桌={tid} method={_meth} GM 憑證={'有' if _gm else '沒有'}"
              f"（X-Table-GM-Token={'有' if self.headers.get('X-Table-GM-Token') else '沒有'}）",
              flush=True)
        self.service = svc                    # ★ 同 `/<桌>/mcp`：實例屬性蓋掉類別屬性
        self.tid = tid
        self.path = "/mcp"
        self._watch_mode = False
        self._hub_gm_ok = bool(_gm)           # ★ `web_api._handle_mcp` 認這一格（hub 級 GM 身分）
        self._prebody = body                  # ★ body 已經被我讀走了（`web_api._body()` 直接用這一格）
        return super().do_POST()

    # ------------------------------------------------------------------ HTTP verbs
    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch("HEAD")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")
