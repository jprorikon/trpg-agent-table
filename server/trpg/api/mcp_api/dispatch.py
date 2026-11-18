"""dispatch（自 __init__.py 拆出）"""

from __future__ import annotations
import json
import os
from typing import Any, Dict, Optional
from trpg.accounts import auth as auth                              # L6 account 認證層（agent 與人類同一條路）
from trpg.core.errors import RuleError, TellError                                       # noqa: E402
from trpg.core.gmtoken import gm_token_ok                                               # R1（唯一一份 GM 閘）

from .catalog import TOOLS, _accounts_of_table

def tool_result(payload: Any) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=1)}],
            "isError": False}

def tool_error(msg: str) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": f"错误：{msg}"}], "isError": True}

class McpMixin:
    """`TableService` 的 MCP 那一半（另一半在 `service.py`／`wake.py`）。"""

    # -- MCP ---------------------------------------------------------------

    def _session_gate(self, name: str, args: Dict[str, Any], t: Any) -> Optional[str]:
        """★ session 規範（設計者 2026-10-07）：**一個 session 對應一個副本實例**。

        * `session` ＝ 這一副本的 id（引擎 `run_id`）；kick 訊息 與 `table_view` 都會給。
        * 讀（`table_view`）**不要求**帶；回應裡一定帶 `session`，拿去寫入時用。
        * 寫（`table_act／say／tell／secret／gm`）：`config.session.require` 下**必須帶且要對**——
          帶舊副本的 id 會被擋（那正是「換局之後舊的一腳不該生效」的閘）。
          **預設 false＝相容期**（照 `require_account` 那套：機制到位、由桌的 config 決定何時收緊；
          沒設的桌 legacy 一字不變）。現役兩張桌都寫了 `session.require: true`。
        * `table_join`：還在入座，允許不帶；有帶就必須對。
        """
        if name == "table_view":
            return None
        cur = ""
        try:
            cur = str(t.session_id() or "")
        except Exception:                                                    # noqa: BLE001
            cur = str((t.adapter_view() or {}).get("run_id") or "")
        want = str(args.get("session") or "").strip()
        if name == "table_join":
            if want and cur and want != cur:
                return f"session 不對：帶的是 {want}，這一副本是 {cur}。"
            return None
        req = bool(((self.cfg.get("session") or {}).get("require", False)))
        if not req:
            if want and cur and want != cur:
                return f"session 不對：帶的是 {want}，這一副本是 {cur}。"
            return None
        if not want:
            return ("這一桌要求帶 `session`（＝本副本的實例 id）——先 `table_view` 拿（kick 訊息裡也有），"
                    f"之後每次寫入都帶 session=\"{cur or '<id>'}\"。")
        if cur and want != cur:
            return (f"session 過期或不對：帶的是 {want}，這一副本是 {cur}"
                    "（換一局就是新的 session；請用 kick／table_view 給的那個）。")
        return None

    def mcp_call(self, name: str, args: Dict[str, Any],
                 gm_ok: bool = False) -> Dict[str, Any]:
        """`gm_ok`＝这条 MCP **连线的**身份已经由标头证明（`X-Table-GM-Token`，2026-10-06）。

        为什么要有这条：GM 换成「只给桌工具」的瘦 profile 之后**没有 terminal**，
        只能走 MCP 调 `table_gm`／`table_secret`／`table_say(seat=gm)`；而这三格原本要
        `token` **参数** ⇒ 模型得把凭证填进参数 ⇒ **凭证会进 prompt、进会话历史、进模型上下文**。
        改了之后：token 由**连线标头**带（写在 GM profile 的 `mcp_servers.table.headers` 里），
        工具参数不用填 ⇒ prompt 与会话里都没有它。玩家席位/匿名客户端仍照旧走 token 参数那条。
        """
        t = self.table
        try:
            # ── account 認證（玩家工具）：帶了 token（連線標頭或工具參數）就**以它認出的席位為準**；
            #    `config.auth.require_account` 開了 ⇒ 沒 token／替別人動 ⇒ 錯誤。agent 與人類同一條路。
            #    沒有 accounts.json 的桌 ⇒ no-op（legacy 一字不變）。
            if name in ("table_view", "table_act", "table_say", "table_tell"):
                _req = bool((self.cfg.get("auth") or {}).get("require_account"))
                _tok = args.get("token")            # 玩家工具照慣例走 token 參數（GM 才用連線標頭）
                _seat, _who, _err = auth.check_seat(_accounts_of_table(t), _tok,
                                                    args.get("seat"), require=_req)
                if _err:
                    return tool_error(_err)
                if _seat:
                    args["seat"] = _seat
                elif _who:
                    # ★有認證、但這一局沒席位（2026-10-08）：
                    #   **讀**（`table_view`）⇒ 旁觀——丟掉自報的席位（照它放行＝冒充），只給公開投影；
                    #   **寫**（act／say／tell）⇒ 擋（不能替別人動手）。
                    if name == "table_view":
                        args["seat"] = None
                    else:
                        return tool_error("你這一局還沒有席位（先在這一桌入座，再動作）")
            _serr = self._session_gate(name, args, t)      # ★ session 規範（一副本一 session）
            if _serr:
                return tool_error(_serr)
            # ★ 2026-10-20（review P2）：MCP 以前**不经过** GraphQL 的 Archive 闸（`/mcp` 直接
            #   调 `mcp_handle`）⇒ 封存后照样能写。这里是 MCP 的唯一写入口：除了 `table_view`
            #   以外一律查封存状态（读不到也拒绝＝fail-closed）。
            if name != "table_view":
                try:
                    from trpg.service import run_archive as _ra                          # noqa: PLC0415
                    _run = str(args.get("session") or t.session_id() or "")
                    if _run and _ra.is_archived(self.home, _run):
                        return tool_error("這一局已封存（Archive）：不能再發言／行動。"
                                          "要繼續請 owner 解除封存，或開新的一局。")
                except Exception as exc:                                             # noqa: BLE001
                    return tool_error(f"讀不到封存狀態，拒絕寫入：{type(exc).__name__}: {exc}")
            if name == "table_view":
                _v = t.view(seat=args["seat"], detail=args.get("detail"))
                if isinstance(_v, dict) and "session" not in _v:
                    try:
                        _v["session"] = t.session_id()             # 讀也把 session 給你（寫入時要帶）
                    except Exception:                                # noqa: BLE001
                        pass
                return tool_result(_v)
            if name == "table_join":
                seat = t.join(seat=args.get("seat"), name=args["name"],
                              kind=args.get("kind", "agent"), attrs=args.get("attrs"))
                if args.get("wake_url"):
                    self.wake_urls[seat] = args["wake_url"]
                return tool_result({"seat": seat, "view": t.view(seat=seat)})
            if name == "table_act":
                return tool_result(t.act(seat=args["seat"], action=args["action"],
                                         target=args.get("target"), attr=args.get("attr"),
                                         dc=args.get("dc"), note=args.get("note"),
                                         skill=args.get("skill"), item=args.get("item"),
                                         req=args.get("req")))    # 請求 id（重試不重複結算）
            if name == "table_say":
                # 同 /api/say 的 GM 聲道閘（協作端 20261006-0013）：seat=gm 需 config.auth.gm_token。
                if (args["seat"] == "gm" and not gm_ok
                        and not gm_token_ok(self.cfg, args.get("token"))):     # R1：空值不再放行
                    return tool_error("GM token 不对（seat=gm 的敘事要帶 token——同一把 auth.gm_token；"
                                      "用 X-Table-GM-Token 连线标头也可以）")
                return tool_result(t.say(seat=args["seat"], text=args["text"]))
            if name == "table_tell":
                # 私訊：**不帶 token 也能用**（玩家 agent 的出口）。GM 的私信「投遞」另有一格
                # table_secret（GM 專用）；這一格是「玩家 ⇄ 玩家／GM」的一般私訊。
                return tool_result(self.tell(args["seat"], args["to"], args["text"]))
            if name == "table_secret":
                if not gm_ok and not gm_token_ok(self.cfg, args.get("token")):          # R1
                    return tool_error("GM token 不对（table_secret 是 GM 专用；"
                                      "用 X-Table-GM-Token 连线标头也可以）")
                return tool_result(t.secret(to=args["to"], text=args["text"],
                                            frm=args.get("seat", "gm")))
            if name == "table_gm":
                if not gm_ok and not gm_token_ok(self.cfg, args.get("token")):          # R1
                    return tool_error("GM token 不对（用 X-Table-GM-Token 连线标头也可以）")
                op = args.get("op")
                if op in ("pause", "resume"):
                    # 整桌暫停／解除（2026-10-06）：與半合作無關 ⇒ 先處理，不必有 gm_op
                    fn = t.pause if op == "pause" else t.resume
                    return tool_result(fn(reason="gm", by=args.get("seat") or "gm"))
                if op == "exp":
                    # M4：GM 當場給經驗（升級由引擎判；屬性／上限的成長寫進事件）
                    return tool_result(t.exp_gain(
                        seat=str(args.get("target") or args.get("seat") or ""),
                        amount=int(args.get("amount") or 0), reason=args.get("note"),
                        by=args.get("seat") or "gm"))
                if op == "star":
                    # 升星：當場給星（`star` 省略＝加一星）
                    return tool_result(t.star_set(
                        seat=str(args.get("target") or args.get("seat") or ""),
                        star=int(args.get("star") or 0), by=args.get("seat") or "gm",
                        note=args.get("note")))
                if op == "item":
                    # ★2：GM 當場給／拿道具（`mode=take` 拿走；與劇本的 item_grant／item_take 同一條路）
                    _cnt = int(args.get("count") or 1)
                    if str(args.get("mode") or "add") == "take":
                        return tool_result(t.item_take(
                            target=str(args.get("target") or args.get("seat") or ""),
                            id=str(args.get("id") or ""), count=_cnt,
                            note=args.get("note"), by=args.get("seat") or "gm"))
                    return tool_result(t.item_add(
                        target=str(args.get("target") or args.get("seat") or ""),
                        id=str(args.get("id") or ""), count=_cnt,
                        note=args.get("note"), by=args.get("seat") or "gm"))
                if op in ("attr_mod", "resource"):
                    # M2 原語（dsl.py）：屬性加值／通用資源——與劇本 trigger 同一條路
                    if op == "attr_mod":
                        return tool_result(t.set_attr_mod(
                            target=str(args.get("target") or ""), attr=str(args.get("attr") or ""),
                            value=int(args.get("amount") or 0), note=args.get("note"),
                            by=args.get("seat") or "gm"))
                    return tool_result(t.add_resource(
                        target=str(args.get("target") or ""), name=str(args.get("name") or ""),
                        value=int(args.get("amount") or 0),
                        mode=str(args.get("mode") or "add"), note=args.get("note"),
                        by=args.get("seat") or "gm"))
                if op == "condition":
                    # 部位受傷／異常狀態的當場投／治（與劇本 trigger 同一條路）
                    return tool_result(t.set_condition(
                        target=str(args.get("target") or ""), id=str(args.get("id") or ""),
                        value=bool(args.get("value", True)),
                        turns=(int(args["turns"]) if args.get("turns") is not None else None),
                        note=args.get("note"), by=args.get("seat") or "gm"))
                if not hasattr(t, "gm_op"):
                    return tool_error("这张桌子没有开半合作模式（config 里没有 semi 段）")
                kw = {k: v for k, v in args.items()
                      if k in ("text", "seat", "fid", "to", "pool", "declare_seq",
                               "attr", "dc", "secret", "action", "note",
                               # ★ 動作請求冪等（2026-10-20）：GM 代打也吃同一個鍵
                               "req",
                               # ★ 音訊（2026-10-20）：播放控制＋開景帶 BGM
                               "bgm", "ch", "key", "gain", "loop", "fade") and v is not None}
                if "force" in args:
                    kw["force"] = args["force"]
                if op == "settle":
                    return tool_result(t.gm_op(op="settle", **kw))
                if op == "leak_report":
                    return tool_result(t.gm_op(op="leak_report"))
                return tool_result(t.gm_op(op=op, **kw))
            return tool_error(f"未知工具 {name}")
        except TellError as exc:
            return tool_error(str(exc))
        except RuleError as exc:
            return tool_error(str(exc))
        except KeyError as exc:
            return tool_error(f"缺少参数 {exc}")
        except Exception as exc:  # noqa: BLE001
            return tool_error(f"{type(exc).__name__}: {exc}")

    def mcp_handle(self, msg: Dict[str, Any], session: Optional[Dict[str, Any]] = None):
        """处理一条 JSON-RPC。返回 (response|None, is_notification)。"""
        method = msg.get("method")
        rid = msg.get("id")
        params = msg.get("params") or {}
        if method == "initialize":
            _pv = getattr(getattr(self, "table", None), "packs", None)
            _pk = (f" 本桌启用的机制包：{'/'.join(_pv.ids)}。"
                   if _pv is not None and _pv.enabled else "")
            result = {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                      "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": "trpg-table", "version": "0.1.0"},
                      "instructions": ("这是一个 TRPG 桌子。先 table_join 入座（kind=agent），"
                                       "轮到你时用 table_view 看局面，用 table_act / table_say 行动。"
                                       "所有骰子与数值由服务端产出，不要自己编。" + _pk)}
            return {"jsonrpc": "2.0", "id": rid, "result": result}, False
        if method in ("notifications/initialized", "notifications/cancelled"):
            return None, True
        if method == "ping":
            return {"jsonrpc": "2.0", "id": rid, "result": {}}, False
        if method == "tools/list":
            # 工具目錄＝**這一桌**那一份（`self.tools`：單一進程時 A 桌的機制包不會漏到 B 桌；
            # 沒有就先給共用底稿——舊路徑相容）
            return {"jsonrpc": "2.0", "id": rid,
                    "result": {"tools": getattr(self, "tools", None) or TOOLS}}, False
        if method == "tools/call":
            out = self.mcp_call(params.get("name"), params.get("arguments") or {},
                               gm_ok=bool((session or {}).get("gm")))
            return {"jsonrpc": "2.0", "id": rid, "result": out}, False
        if method == "resources/list":
            return {"jsonrpc": "2.0", "id": rid, "result": {"resources": []}}, False
        if method == "prompts/list":
            return {"jsonrpc": "2.0", "id": rid, "result": {"prompts": []}}, False
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32601, "message": f"method not found: {method}"}}, False
