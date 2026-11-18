#!/usr/bin/env python3
"""**L6｜POST 面**（寫入 REST／GraphQL 入口／MCP over HTTP）——`HandlerPostMixin`。

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

from .base import gm_token_ok, HandlerBaseMixin, _watch_token_ok, _auth_info, _is_api_path, _legacy_rest_ok, _watch_json, _maybe_writeback, _chars_available, _pick_char, _busy_tables, _busy_msg, _accounts_of, asset_response, carry_over_wallet


class HandlerPostMixin(HandlerBaseMixin):
    def _post_auth(self, body: Dict[str, Any], svc: Any) -> bool:
        """account 認證閘（實作在 `post_auth.py`；GraphQL 的 `_Shim` 也借這一份）。"""
        from .post_auth import post_auth as _policy
        return _policy(self, body, svc)

    def do_DELETE(self) -> None:  # noqa: N802
        if self.path.split("?")[0] == "/mcp":
            self._send(204, b"")
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
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
        # **觀戰模式**：沒有寫入面（唯讀前門）——**`POST /graphql` 例外**。
        #   前端整支搬 GraphQL（2026-10-08）之後，觀戰頁的「讀」也走同一個端點：mutation 由
        #   `graphql_api._mut` 依 `ctx.watch` 一律拒（「觀戰桌沒有寫入面」），Query 只走唯讀白名單。
        #   ⚠ 不放它進來 ⇒ 觀戰頁每一次查詢都 404、整頁空白（owner 2026-10-08 實測：
        #   hub log 連發 `POST /<桌>/watch/graphql` 404）。
        if getattr(self, "_watch_mode", False) and path != "/graphql":
            self._json(404, {"error": "觀戰桌沒有寫入面（唯讀前門）"})
            return
        body = self._body()
        svc = self.service
        _maybe_writeback(svc, _accounts_of(svc))        # 收局寫回（冪等；沒 accounts.json＝no-op）
        # ★ 2026-10-20（review P2）：REST 寫入口也補 Archive 閘（GraphQL／MCP 各有自己的閘；
        #   這幾條路不經過 GraphQL）。封存後只允許讀；`/api/enter` 仍可進來「看」，但不再寫。
        if path in ("/api/join", "/api/act", "/api/tell_gm", "/api/tell", "/api/say", "/api/gm"):
            try:
                from trpg.service import run_archive as _ra                        # noqa: PLC0415
                _run = str(svc.table.session_id() or "")
                if _run and _ra.is_archived(svc.home, _run):
                    self._json(403, {"error": "這一局已封存（Archive）：不能再發言／行動。"
                                              "要繼續請 owner 解除封存，或開新的一局。"})
                    return
            except Exception as exc:                                               # noqa: BLE001
                self._json(403, {"error": f"讀不到封存狀態，拒絕寫入：{type(exc).__name__}: {exc}"})
                return
        try:
            if path == "/mcp":
                self._handle_mcp(body)
                return
            if path == "/graphql":
                # **GraphQL 客戶端面**（設計者 2026-10-07：一個端點、桌是欄位）——契約＝docs/schema.graphql。
                # 內部一律「換入口叫同一支 handler」⇒ 與 REST 同語意（政策只有一份）。
                from trpg.api.graphql import dispatch as graphql_api     # noqa: PLC0415
                _st, _payload = graphql_api.handle(
                    body, svc=svc, headers=self.headers,
                    watch=getattr(self, "_watch_mode", False))
                self._json(_st, _payload)
                return
            if path == "/api/join":
                seat = svc.table.join(seat=body.get("seat"), name=body.get("name", "无名"),
                                      kind=body.get("kind", "human"), attrs=body.get("attrs"),
                                      wake_url=body.get("wake_url"))
                if body.get("wake_url"):
                    svc.wake_urls[seat] = body["wake_url"]
                self._json(200, {"seat": seat, "view": svc.table.view(seat=seat)})
                return
            # ── account 認證（★account 層）：政策住 `post_auth.py`（GraphQL 也借同一份）。
            if not self._post_auth(body, svc):
                return
            if path == "/api/enter":
                # 人類（login UI）與 agent 的**進場**：token ⇒ 帳號 ⇒ 這一局的席位 ⇒ 帶入局外成長。
                _acct = _accounts_of(svc)
                _tok = auth.token_from_request(headers=self.headers, body=body)
                _who = _acct.resolve(_tok)
                if _who is None:
                    self._json(403, {"error": "token 不認得（或用的是舊憑證）"})
                    return
                _seat = _who.get("seat")
                if not _seat:
                    self._json(409, {"error": "這個 account 在這一局還沒有席位——先選角色（/api/pick_char）",
                                     "chars_available": _chars_available(svc, _acct)})
                    return
                _char = _acct.char_of(_seat) or _who.get("display")
                # ★ 2026-10-20（review P2）：封存的局仍可進來「看」，但**不 join／不帶錢／不 join_run**
                #   （事件流與座次都是凍結的）。以前 `/api/enter` 不看 Archive ⇒ 封存後還能寫 seat_joined。
                try:
                    from trpg.service import run_archive as _ra                        # noqa: PLC0415
                    _archived = _ra.is_archived(svc.home, str(svc.table.session_id() or ""))
                except Exception:                                           # noqa: BLE001
                    _archived = False
                # ★ 同一個帳號的同一個角色不能同時在兩張桌（設計者 2026-10-07 定案）：
                #   別的桌**還開著且未收局**時，他的那個角色還在演 ⇒ 當場擋（409）。
                _busy = _busy_tables(svc, _who["account"], str(_char or ""))
                if _busy:
                    self._json(409, {"error": _busy_msg(str(_char), _busy), "busy_in": _busy})
                    return
                # 席位名：**劇本/模組給的顯示名**優先（`seats.json` 的 name → 模組角色表），
                # 最後才退回 charId。以前直接拿 `_char`（charId）當名字 ⇒ 畫面上席位名變成 "char_b"
                # （2026-10-08 進桌選人實測：她坐 p2 卻顯示 char_b，GM 敘事也叫不出「シズク」）。
                _spec = (getattr(svc.table, "seats_spec", {}) or {}).get(_seat) or {}
                _nm = str(_spec.get("name") or "").strip()
                if not _nm:
                    _ch = str(_spec.get("character") or _char or "")
                    _nm = str(((getattr(svc.table, "roster", {}) or {}).get(_ch) or {}).get("name") or "").strip()
                if not _archived:
                    try:
                        svc.table.join(seat=_seat, name=(_nm or _char), kind=_who.get("kind") or "human")
                    except Exception:                                       # noqa: BLE001
                        pass                                                # 已入座＝正常
                # ⚠ test_director A4：**L6 不准直讀 state**（禁繞過 L2）⇒ 走讀出口 `adapter_view()`。
                #    這是先前 account 那輪留下的違規（`test_director` 一直是紅的），順手修。
                _ops = svc.table.adapter_view()
                _ended = bool(_ops.get("ended"))
                _inactive = bool(_ended or _archived)          # 局已收／已封存：只讀
                _runid = str(_ops.get("run_id") or "")
                # 錢包（通用貨幣）：**同一局只帶一次**。冪等鍵＝這一局的事件流（`_carry_over_done`），
                # 不再信 `seat_assignment.wallet_carried`（DB 走 run_seat 時那格寫死 False ⇒ 每次進場
                # 都重帶；JSON 後端則新局不帶）。見 `web/base.py › carry_over_wallet`。
                # 說法 2：錢包是 **(帳號 × 模組)**；`_acct.wallet` 預設就是這一桌（模組）。
                _w = _acct.wallet(_who["account"])
                if not _inactive:
                    try:
                        carry_over_wallet(svc, _acct, _seat, _w)
                    except Exception:                                   # noqa: BLE001
                        pass
                _inst = _acct.instance(_who["account"], _char)           # 說法 3：(帳號, 模組, 角色)
                # 說法 4：把「(帳號, 模組, 角色, 這一副本) → 局內行為實例」記一列
                if not _archived:
                    try:
                        _acct.join_run(_who["account"], str(_char or ""), _runid, _seat)
                    except Exception:                                       # noqa: BLE001
                        pass
                _carry = {}
                if _inst and not _inactive and hasattr(svc.table, "carry_over"):
                    try:
                        _carry = svc.table.carry_over(seat=_seat, level=int(_inst.get("level") or 1),
                                                      exp=int(_inst.get("exp") or 0))
                    except Exception as exc:                            # noqa: BLE001
                        _carry = {"error": f"{type(exc).__name__}: {exc}"}
                self._json(200, {"account": _who["account"], "display": _who.get("display"),
                                 "kind": _who.get("kind"), "seat": _seat, "char": _char,
                                 "module": _acct.table, "run_id": _runid,
                                 "instance": _inst, "wallet": _acct.wallet(_who["account"]),
                                 "carried": bool((_carry or {}).get("carried")),
                                 "archived": bool(_archived),
                                 "ended": _ended,
                                 "view": svc.table.view(seat=_seat)})
                return
            if path == "/api/login":
                # 人類入口：**帳號＋密碼** ⇒ 換一把 session token（前端存起來，之後照舊自動帶）。
                # agent 側仍用自己的帳號 token；兩者同一條 `resolve`。壞 = 一次不算是誰、也不說哪個欄位錯。
                _acct = _accounts_of(svc)
                _who = _acct.login(str(body.get("username") or ""), str(body.get("password") or ""))
                if _who is None:
                    self._json(401, {"error": "帳號或密碼不對"})
                    return
                self._json(200, {"token": _who.pop("token"), "account": _who.get("account"),
                                 "display": _who.get("display"), "kind": _who.get("kind"),
                                 "seat": _who.get("seat"), "char": _acct.char_of(_who.get("seat") or ""),
                                 "instance": _who.get("instance"), "wallet": _who.get("wallet"),
                                 "chars_available": _chars_available(svc, _acct)})
                return
            if path == "/api/pick_char":
                _who = _accounts_of(svc).resolve(auth.token_from_request(headers=self.headers, body=body))
                if _who is None:
                    self._json(403, {"error": "token 不認得"})
                    return
                _out = _pick_char(svc, _accounts_of(svc), _who, str(body.get("char") or ""))
                self._json(200 if _out.get("ok") else 409, _out)
                return
            if path == "/api/act":
                out = svc.table.act(seat=body["seat"], action=body["action"],
                                    target=body.get("target"), attr=body.get("attr"),
                                    dc=body.get("dc"), note=body.get("note"),
                                    skill=body.get("skill"), item=body.get("item"),
                                    req=body.get("req"))          # 請求 id（重試不重複結算）
                # ★ 2026-10-21 拆景：GM 的喚醒點只剩「輪」——玩家動作**不逐句**叫 GM。
                #   `wake.gm_on_turn_end: true`（每席結束就叫）那條路由 tick_loop 負責，這裡不重複踢。
                if not bool((svc.cfg.get("wake") or {}).get("gm_on_turn_end")):
                    maybe_kick_round(svc)
                self._json(200, {"result": out, "view": svc.table.view(seat=body["seat"])})
                return
            if path == "/api/tell_gm":
                # 玩家 → GM 的場外對話（只有 GM 看得到；走 secret 管線，進 state["secrets"]["gm"]）
                seat = body.get("seat")
                text = (body.get("text") or "").strip()
                if not seat or not text:
                    self._json(400, {"error": "seat 與 text 都是必填"})
                    return
                if seat not in svc.table.adapter_view()["seats"]:
                    self._json(409, {"error": "還沒入座"})
                    return
                ev = svc.table.secret(to="gm", text=text, frm=seat)
                # 回顯給自己（peer＝對話對象，前端據此分對話串）
                svc.table.secret(to=seat, text=text, frm=seat, peer="gm")
                svc.wake_gm(seat, text, "私訊", ev["seq"])
                self._json(200, {"ok": True, "seq": ev["seq"]})
                return
            if path == "/api/tell":
                # 私訊：玩家 ⇄ 玩家／GM（只有雙方看得到）。
                # **實作只有一本**＝`TableService.tell`（MCP 的 table_tell 走同一支），
                # 驗證訊息與喚醒契約都不養第二份。
                try:
                    self._json(200, svc.tell(body.get("seat"), body.get("to"), body.get("text")))
                except TellError as exc:
                    self._json(exc.status, {"error": str(exc)})
                return
            if path == "/api/typing":
                # 打字中（純記憶體，不落地）：前端在私訊框邊打邊 ping，讓對方看到「正在輸入…」
                seat = body.get("seat")
                if seat and seat in svc.table.adapter_view()["seats"]:
                    svc.table.mark_typing(seat=seat, to=body.get("to") or "")
                self._json(200, {"ok": True})
                return
            if path == "/api/say":
                # GM 聲道閘（協作端 20261006-0013 裁定 (c)）：只有持 config.auth.gm_token 的人
                # 能聲明「我是 gm」。同一把 token、同義同碼＝403（照 /api/gm，別讓同一個引擎給兩種）。
                # 玩家照舊帶自己的 SEAT ⇒ 走無 token 那條，現況零變更。
                if body["seat"] == "gm" and not gm_token_ok(svc.cfg, body.get("token")):
                    self._json(403, {"error": "bad gm token"})
                    return
                ev = svc.table.say(seat=body["seat"], text=body["text"])
                _kind = (svc.table.adapter_view()["seats"].get(body["seat"], {}) or {}).get("kind")
                if _kind == "human" and svc.pacing_mode != "turn":
                    # 喚醒收斂（協作端 2026-10-05 §3）：同一個席位在同一景裡只喚第一次。
                    # 冪等鍵＝坐席＋景，不用 `:序號` —— 否則每說一句都是新鍵 ⇒ 一句一喚。
                    # 回合制：不逐句叫 GM（喚醒點是「輪」；這一輪的話會進輪末的 digest）。
                    tid = svc.cfg.get("table_id") or "table"
                    key = svc.wake_key(body["seat"]) or f"{tid}:公開發言:{body['seat']}:seq{ev['seq']}"
                    if key not in svc.rio_kick_keys:
                        svc.rio_kick_keys.add(key)
                        svc.wake_gm(body["seat"], body["text"], "公開發言", ev["seq"],
                                    turn_key=key)
                # 玩家的发言不会自动推给 agent——只有**点名**才推（每景限量）。
                # GM/NPC 的敘事不點名喚醒：gm_say 回 {"said","speaker"}（沒有 seq），
                # 且點名喚醒的語意只認玩家席位（2026-10-06：seat=gm 走這條會 KeyError 'seq'）。
                mentions = ([] if _kind in ("gm", "npc")
                            else svc.kick_on_mention(speaker=body["seat"], text=body["text"],
                                                     seq=int(ev["seq"])))
                self._json(200, {"view": svc.table.view(seat=body["seat"]),
                                 "mentions_pushed": [m["seat"] for m in mentions]})
                return
            if path == "/api/gm":
                if not gm_token_ok(svc.cfg, body.get("token")):          # R1：`token: ""` 不再放行
                    self._json(403, {"error": "bad gm token"})
                    return
                if body.get("op") == "semi":
                    # 半合作 GM 工具面：全部走 gm_op（只有叙述/投放/开收景/结算/改判）
                    self._json(200, svc.table.gm_op(**body.get("args", {})))
                    return
                op = body.get("op")
                # GM 席位：没入座就自动补一个（带 token 就够），且不进行动顺序
                gseat = body.get("seat") or "gm"
                if gseat not in svc.table.adapter_view()["seats"]:
                    gseat = svc.table.join(seat=gseat, name="GM", kind="gm")
                if op == "scene":
                    raise RuleError("景已停用（2026-10-21）：GM 每輪用 say 說一句就好")
                elif op == "secret":
                    svc.table.secret(to=body["to"], text=body["text"], frm=gseat)
                elif op == "start_turn":
                    svc.table.start_turn(seat=body.get("seat"))
                elif op == "end_turn":
                    svc.table.end_turn(reason=body.get("reason", "gm"))
                elif op == "heal":
                    svc.table.heal(seat=body["seat_target"], amount=int(body["amount"]))
                elif op == "pause":
                    self._json(200, {"ok": True, "view": svc.table.view(gm=True),
                                     "pause": svc.table.pause(reason=body.get("reason", "gm"),
                                                              by=gseat)})
                    return
                elif op == "resume":
                    self._json(200, {"ok": True, "view": svc.table.view(gm=True),
                                     "pause": svc.table.resume(reason=body.get("reason", "gm"),
                                                               by=gseat)})
                    return
                elif op == "kick":
                    # 手動補踢：**總閘的例外**（人有意識地要這一腳）
                    self._json(200, svc.kick(body["seat"], int(body["turn_seq"]),
                                             force=True, manual=True))
                    return
                elif op == "condition":
                    # 部位受傷／異常狀態：GM 當場投／治。**與劇本 trigger 的 `condition_set`
                    # effect 走同一條路**（`Table.set_condition`）——不養第二份實作。
                    try:
                        ev = svc.table.set_condition(
                            target=str(body.get("target") or ""), id=str(body.get("id") or ""),
                            value=bool(body.get("value", True)),
                            turns=(int(body["turns"]) if body.get("turns") is not None else None),
                            note=body.get("note"), by=gseat)
                        self._json(200, {"ok": True, "seq": ev.get("seq"),
                                         "conditions": svc.table.conditions_view(
                                             str(body.get("target") or "")),
                                         "known": svc.table.condition_ids()})
                    except ValueError as exc:
                        self._json(409, {"error": f"{type(exc).__name__}: {exc}"})
                    return
                elif op == "exp":
                    # M4：GM 當場給經驗（升級由引擎判）
                    try:
                        res = svc.table.exp_gain(
                            seat=str(body.get("target") or body.get("seat") or ""),
                            amount=int(body.get("amount") or 0), reason=body.get("note"),
                            by=gseat)
                        self._json(200, {"ok": True, **res})
                    except ValueError as exc:
                        self._json(409, {"error": f"{type(exc).__name__}: {exc}"})
                    return
                elif op == "star":
                    # 升星的第二條路：當場給星（劇情自己判的也能直接給）
                    try:
                        res = svc.table.star_set(
                            seat=str(body.get("target") or body.get("seat") or ""),
                            star=int(body.get("star") or 0), by=gseat, note=body.get("note"))
                        self._json(200, {"ok": True, "seat": res["seat"], "star": res["star"]})
                    except ValueError as exc:
                        self._json(409, {"error": f"{type(exc).__name__}: {exc}"})
                    return
                elif op == "item":
                    # ★2：GM 當場給／拿道具（與劇本的 item_grant／item_take 同一條路）
                    try:
                        cnt = int(body.get("count") or 1)
                        if str(body.get("mode") or "add") == "take":
                            res = svc.table.item_take(
                                target=str(body.get("target") or body.get("seat") or ""),
                                id=str(body.get("id") or ""), count=cnt,
                                note=body.get("note"), by=gseat)
                        else:
                            res = svc.table.item_add(
                                target=str(body.get("target") or body.get("seat") or ""),
                                id=str(body.get("id") or ""), count=cnt,
                                note=body.get("note"), by=gseat)
                        self._json(200, {"ok": True, "seat": res["seat"], "id": res["id"],
                                         "count": res["count"], "delta": res["delta"]})
                    except ValueError as exc:
                        self._json(409, {"error": f"{type(exc).__name__}: {exc}"})
                    return
                elif op in ("attr_mod", "resource"):
                    # M2 原語（dsl.py）：GM 當場裁定與劇本 trigger 走同一條路（不養第二份實作）。
                    try:
                        tgt = str(body.get("target") or "")
                        amt = int(body.get("amount") or 0)
                        if op == "attr_mod":
                            ev = svc.table.set_attr_mod(target=tgt, attr=str(body.get("attr") or ""),
                                                        value=amt, note=body.get("note"), by=gseat)
                            self._json(200, {"ok": True, "seq": ev.get("seq"),
                                             "attr_mods": svc.table.attr_mods_of(tgt),
                                             "attrs": svc.table.attrs_of(tgt)})
                        else:
                            ev = svc.table.add_resource(target=tgt,
                                                        name=str(body.get("name") or ""),
                                                        value=amt,
                                                        mode=str(body.get("mode") or "add"),
                                                        note=body.get("note"), by=gseat)
                            self._json(200, {"ok": True, "seq": ev.get("seq"),
                                             "after": ev.get("after"),
                                             "resources": svc.table.resources_of(tgt)})
                    except (ValueError, TypeError) as exc:
                        self._json(409, {"error": f"{type(exc).__name__}: {exc}"})
                    return
                elif op == "replay":
                    self._json(200, svc.table.replay_check())
                    return
                else:
                    self._json(400, {"error": f"unknown op {op}"})
                    return
                self._json(200, {"ok": True, "view": svc.table.view(gm=True)})
                return
            if path == "/hook/inbox":
                # 不挂 MCP 的席位（或"拉"模式脚本）把动作 POST 进来
                seat = body.get("seat")
                if not seat:
                    self._json(400, {"error": "seat required"})
                    return
                kind = body.get("op", "act")
                if kind == "act":
                    out = svc.table.act(seat=seat, action=body.get("action", "other"),
                                        target=body.get("target"), attr=body.get("attr"),
                                        dc=body.get("dc"), note=body.get("note"),
                                        # ★ 2026-10-21：技能／道具走同一支 `act`；hook 這條路
                                        #   以前不轉發 skill／item ⇒ 只有這條路的席位放不了技能。
                                        skill=body.get("skill"), item=body.get("item"),
                                        req=body.get("req"))      # 請求 id（重試不重複結算）
                elif kind == "say":
                    out = svc.table.say(seat=seat, text=body.get("text", ""))
                else:
                    self._json(400, {"error": f"unknown op {kind}"})
                    return
                self._json(200, {"result": out})
                return
        except RuleError as exc:
            self._json(409, {"error": str(exc), "kind": "rule"})
            return
        except KeyError as exc:
            self._json(400, {"error": f"missing field {exc}"})
            return
        except Exception as exc:  # noqa: BLE001
            self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
            return
        self._json(404, {"error": "not found"})


    def _handle_mcp(self, body: Dict[str, Any]) -> None:
        svc = self.service
        sid = self.headers.get("Mcp-Session-Id")
        extra: Dict[str, str] = {}
        # 连线身份（2026-10-06）：GM 那一侧没有 token 参数（凭证不进 prompt／会话），
        # 改由标头证明——写在 GM profile 的 `mcp_servers.table.headers`。
        _hdr = (self.headers.get("X-Table-GM-Token") or "").strip()
        gm_ok = gm_token_ok(svc.cfg, _hdr)          # R1：原本就有 bool(_hdr) 擋著，改成同一支（語意不變）
        # ★ 多桌共用前門（`/mcp`）：hub 已經驗過 `X-Hub-GM-Token`（最小權限那把），
        #   它蓋在這一格上 ⇒ 那條路來的請求就是「這一張桌的 GM」，不必逐桌帶各自的 gm_token
        #   （GM profile 的設定是靜態的，一桌一個值它帶不動——2026-10-08）。
        if not gm_ok and getattr(self, "_hub_gm_ok", False):
            gm_ok = True
        if not sid:
            sid = uuid.uuid4().hex
            svc.mcp_sessions[sid] = {"gm": gm_ok}
            extra["Mcp-Session-Id"] = sid
        sess = svc.mcp_sessions.get(sid) or {}
        if gm_ok:
            # 每个请求都重认一次：客户端可能在重连后才带上标头
            sess["gm"] = True
            svc.mcp_sessions[sid] = sess
        resp, is_notif = svc.mcp_handle(body, sess)
        if is_notif or resp is None:
            self._send(202, b"", "application/json", extra)
            return
        self._json(200, resp, extra)


