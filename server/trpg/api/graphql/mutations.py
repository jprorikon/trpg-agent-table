"""mutations（自 __init__.py 拆出）"""

from __future__ import annotations
import json
from urllib.parse import quote
import os
import sys
from typing import Any, Dict, List, Optional, Tuple
from trpg.paths import ROOT as HERE  # repo 根（資料／vendor／docs 都在這裡）
from graphql import GraphQLError, build_schema, graphql_sync          # noqa: E402
from trpg.service import assets as module_assets                                         # noqa: E402
from trpg.core import layout as layout                                                          # noqa: E402
from trpg.engine import projection as projection                                                      # noqa: E402
from trpg.core import store as store                                                           # noqa: E402
from trpg.accounts import auth as auth
from trpg.service import table_directory as tdir                                         # noqa: E402

from .common import ARG_ALIASES, GM_OPS, REST_OPS, _tid
from .context import Ctx, _body_of, _need_session, _who_id, call
from .queries import _seats_of

def _mut(name: str):
    """做一個 mutation resolver：**同一個入口**（`/api/...` 或 `/api/gm`）＋ session 閘。"""

    def _f(_root: Any, info: Any, **args: Any) -> Any:
        ctx: Ctx = info.context
        if ctx.watch:
            raise GraphQLError("觀戰桌沒有寫入面（唯讀前門）")
        tid = str(args.get("tableId") or "")
        svc = ctx.need(tid) if tid else ctx.svc
        if svc is None and name == "login":
            # 大廳的根端點（2026-10-19 改版）：沒指定桌 ⇒ 依序問每一張桌的帳號層，第一個認得的就是
            #   （帳號層是跨桌共用的一份；憑證本來就「一把通用」）。都不認得＝帳密不對。
            body = _body_of(args, skip=("tableId", "session"))
            last = "帳號或密碼不對"
            for row in tdir.discover(ctx.root):
                _svc = ctx.table(row["id"])
                if _svc is None:
                    continue
                code, out = call(_svc, "POST", "/api/login", body=body, headers=ctx.headers)
                if code == 200 and isinstance(out, dict) and out.get("token"):
                    return out
                last = str((out or {}).get("error") or last) if isinstance(out, dict) else last
            raise GraphQLError(last)
        if svc is None:
            raise GraphQLError("這個操作要指定 tableId")
        if name == "login":
            body = _body_of(args, skip=("tableId", "session"))
            code, out = call(svc, "POST", "/api/login", body=body, headers=ctx.headers)
            if code != 200 or not isinstance(out, dict):
                raise GraphQLError(str((out or {}).get("error") or f"/api/login 回了 {code}"))
            return out
        _need_session(svc, str(args.get("session") or ""), name=name)
        body = _body_of(args, skip=("tableId", "session"))
        for _g, _r in ARG_ALIASES.get(name, {}).items():          # GraphQL 名 → handler 讀的名
            if _g in args and args[_g] is not None:
                body[_r] = args[_g]
        if name in REST_OPS:
            # ⚠ 2026-10-10 修：`say` 這格 **GM 也會用**（`tools/gm.py <桌> say`／`/api/say` 的 GM 聲道閘），
            #   而 handler 的認證閘讀的是 **body 裡的 token**（`body.get("gm_token") or body.get("token")`）。
            #   只有 `/api/gm` 那條把標頭轉進 body ⇒ `gm.py say` 在 require_account 的桌上一直 403。
            #   只補「seat 已經是 gm」的呼叫 ⇒ 不會把「GM 面板開著、用自己帳號發言」的頁面誤判成 GM。
            if name == "say" and str(body.get("seat") or "") == "gm":
                _gmtok = ctx.headers.get("X-Table-GM-Token")
                if _gmtok:
                    body["token"] = _gmtok
            code, out = call(svc, "POST", REST_OPS[name], body=body, headers=ctx.headers)
        else:
            # GM：一律走 `/api/gm` 的 op（同一套語意）。**控制鍵走標頭**
            # （`X-Table-GM-Token`——契約就寫標頭：不進 GraphQL 參數 ⇒ 不進 prompt／日誌）；
            # 這裡把它轉成 handler 讀的那個欄位（`body["token"]`），沒帶就原樣傳（handler 自然 403）。
            _gmtok = ctx.headers.get("X-Table-GM-Token")
            if _gmtok:
                body["token"] = _gmtok
            body["op"] = GM_OPS[name]
            code, out = call(svc, "POST", "/api/gm", body=body, headers=ctx.headers)
        if code != 200:
            raise GraphQLError(str((out or {}).get("error") or f"{name} 回了 {code}"))
        return out

    _f.__name__ = name
    return _f

def _m_create_char(_root: Any, info: Any, tableId: str, session: str,     # noqa: N803
                   charId: str, name: Optional[str] = None) -> Dict[str, Any]:
    """**建立新角色**＝在這個**模組**底下給這個帳號開一個**新實例**（設計者 2026-10-07）。

    與 `pickChar` 的分工（設計者：「需要专门的接口比较卫生」）：
      * `createChar`＝**開新實例**（還沒有 instance）⇒ 綁席位 ＋ 寫一筆 level 1 的實例
      * `pickChar` ＝**匯入已有實例**（已經有 instance）⇒ 只綁席位
    這一格對「已經有實例」**明確拒絕**（叫他用 `pickChar`）——「我到底有沒有這個角色」永遠只有一個答案。
    """
    from trpg.accounts import auth as auth                                                            # noqa: PLC0415
    from trpg.api import web as web_api                                                         # noqa: PLC0415
    ctx: Ctx = info.context
    if ctx.watch:
        raise GraphQLError("觀戰桌沒有寫入面（唯讀前門）")
    svc = ctx.need(tableId)
    _need_session(svc, session, name="createChar")
    acct = web_api._accounts_of(svc)
    if not getattr(acct, "enabled", False):
        raise GraphQLError("這張桌沒有帳號層（不能建角色實例）")
    tok = auth.token_from_request(headers=ctx.headers)
    who = acct.resolve(tok) if tok else None
    if not who:
        raise GraphQLError("要帶有效的 account token 才能建角色")
    account = str(who.get("account") or "")
    # 角色必須是**模組角色表**裡的（角色的類由模組定）＋ 這一局**有他的席位**
    mod = str(svc.cfg.get("module") or layout.where(svc.table.home, ctx.root).get("module")
              or _tid(svc))
    if layout.characters(ctx.root, mod) and charId not in layout.characters(ctx.root, mod):
        raise GraphQLError(f"模組 {mod} 的角色表沒有 {charId!r}（角色的類由模組定）")
    seat = next((str(r.get("seat")) for r in _seats_of(svc.table.home)
                 if str(r.get("character") or "") == charId
                 and str(r.get("kind")) in ("human", "agent")), "")
    if not seat:
        raise GraphQLError(f"這一局沒有 {charId!r} 的玩家席位（劇本沒排）")
    # ⚠ **順序要緊**：先判「我在這個模組已經有實例」（＝他該走 `pickChar`），再判席位被佔。
    #   顛倒的話，同一個人重複按「建立新角色」會看到「席位已經有人了」——那是別人的情境，誤導。
    if acct.instance(account, charId):
        raise GraphQLError(f"{charId!r} 你在這個模組已經有實例了——要用 `pickChar`（匯入），"
                           "不是 `createChar`（開新的）")
    _taken = acct.char_of(seat)
    if _taken and not (_taken == charId):
        raise GraphQLError(f"席位 {seat} 已經有人了")
    busy = web_api._busy_tables(svc, account, charId)
    if busy:
        raise GraphQLError(web_api._busy_msg(charId, busy))
    # ⚠ **不要再包一層 `acct._lock()`**：`assign_seat()`／`write_instance()` 自己就取鎖，
    #   而 JSON 後端的鎖是 `fcntl.flock`（**同行程再開一個 fd 也會擋**）⇒ 巢狀取鎖＝死鎖。
    #   實測踩過：整個請求卡死（線上沒炸只因為真桌走 DB 後端，DB 那條沒有 flock）。
    acct.assign_seat(seat, account, charId)
    acct.write_instance(account, charId, {"level": 1, "exp": 0, "note": str(name or "")})
    inst = acct.instance(account, charId)
    return {"ok": True, "account": account, "charId": charId, "seat": seat,
            "instance": inst}

def _m_create_table(_root: Any, info: Any, module: str, scenario: str,
                    name: Optional[str] = None,
                    watchable: Optional[bool] = None,
                    difficulty: Optional[str] = None) -> Dict[str, Any]:
    """**建立新桌**（設計者 2026-10-07 第 1 條）：同一個（模組×劇本）開多張桌。

    前台層的操作（要動 registry ⇒ 只有 hub 能做；單桌模式明確拒絕，不要偷偷生一個
    沒人服務的目錄卻回「建好了」）。
    """
    ctx: Ctx = info.context
    if ctx.watch:
        raise GraphQLError("觀戰桌沒有寫入面（唯讀前門）")
    if ctx.create_table is None:
        raise GraphQLError("建立新桌要在前台上做（單桌模式／開發行程沒有 registry）")
    # **建桌要有憑證**：不然同一個網段上誰都能生桌子。用的是「任何一張桌的帳號憑證」——
    # 誰建的看得出來；建出來的桌屬於（模組×劇本），不屬於某個人。
    _who = ctx.who()
    if not _who:
        raise GraphQLError("建立新桌要帶帳號憑證（`Authorization: Bearer …`）")
    # 2026-10-19：建桌時就能選「允不允許觀戰」（不給＝config 預設）
    # 2026-10-20：建桌時就能指定**開局難度**（`difficulty`＝劇本 `starting_difficulty.levels` 的檔名，
    #   例 easy／normal／hard；不給＝用劇本的 default）。三層回退是為了讓「舊 hook 不吃 kwargs」
    #   的部署仍能建桌（TypeError ⇒ 少一個 kwarg 再試一次）。
    try:
        out = ctx.create_table(str(module), str(scenario), str(name or ""),
                               watchable=watchable, difficulty=difficulty)
    except TypeError:
        try:
            out = ctx.create_table(str(module), str(scenario), str(name or ""), watchable=watchable)
        except TypeError:                 # 舊 hook 不吃 kwargs ⇒ 退回原呼叫
            out = ctx.create_table(str(module), str(scenario), str(name or ""))
    if not out.get("ok"):
        raise GraphQLError(str(out.get("error") or "建立新桌失敗"))
    # 2026-10-19：開桌的人是**這一局的 owner**（只有 owner 能喚 GM／調可見性）
    try:
        from trpg.core import layout as _ly                              # noqa: PLC0415
        from trpg.service import run_owner as _ro                       # noqa: PLC0415
        _tb = (out.get("table") or {})
        _home = _ly.table_home(ctx.root, str(module), str(scenario), str(_tb.get("id") or ""))
        _ro.set_owner(_home, str(_tb.get("session") or ""), str(_who.get("account") or ""))
    except Exception:                                                   # noqa: BLE001
        pass
    return out

def _m_gm_run(_root: Any, info: Any, tableId: str, session: str, op: str, args: Optional[Any] = None) -> Any:
    """**適配器專用**的 GM op 直通：把 `op／args` 原樣交給 `/api/gm`（＝舊 MCP `table_gm` 的同一支）。

    與具名格（`gmExp`…）**同一支 handler**，所以兩條路的語意不可能不同；留它是為了讓 MCP 適配器
    不必維護「op → 欄位名 → 參數名」的對映表（那種表一定會漂）。
    """
    ctx: Ctx = info.context
    if ctx.watch:
        raise GraphQLError("觀戰桌沒有寫入面（唯讀前門）")
    svc = ctx.need(str(tableId))
    _need_session(svc, str(session), name="gmRun")
    body: Dict[str, Any] = dict(args or {})
    _op = str(op)
    if _op == "semi":
        # ⚠ 2026-10-10 修：`semi` 這個形狀裡**真正的動作名在 `args["op"]`**（`tools/gm.py`／
        #   `gql_client.gm_op` 送的形狀；`/api/gm` 的 semi 分支是 `gm_op(**args)`）。
        #   攤平會讓外層的 `op="semi"` 蓋掉它 ⇒ `gm_op()` 少必要參數直接 TypeError
        #   （2026-10-07 `24f82f0` 起；`tools/gm.py` 除 `say`／`state` 外全壞）。
        body = {"op": "semi", "args": dict(args or {})}
    else:
        body["op"] = _op
    if "token" not in body:
        _gmk = str(ctx.headers.get("X-Table-GM-Token") or "")
        if _gmk:
            body["token"] = _gmk
    code, out = call(svc, "POST", "/api/gm", body=body, headers=ctx.headers, watch=ctx.watch)
    if code != 200:
        raise GraphQLError(str((out or {}).get("error") or f"/api/gm 回了 {code}"))
    return out

def _m_new_run(_root: Any, info: Any, tableId: str, watchable: Optional[bool] = None) -> Any:
    """**換局**：這一張桌開一局新的（新 session ＋ 新 `runs/<session>/`；舊局檔原地留著可回放）。

    ⚠ 這是**前台層**的操作（要換掉 registry 那一格＝`Hub.services[tid]`）：單桌模式／開發行程
    （沒有 hook）**明確拒絕**——不要在表內偷偷重建實例，那會變成「回應說換了、請求還打到舊實例」。
    """
    ctx: Ctx = info.context
    if ctx.watch:
        raise GraphQLError("觀戰桌沒有寫入面（唯讀前門）")
    svc = ctx.need(tableId)
    # ⚠ 「沒有前台 hook」先判：單桌／開發行程本來就不支援換局，錯誤訊息要講這件事，
    #   不要先被身分閘攔成「要帶憑證」（測試 G13／G17b 咬著）。
    if ctx.new_run is None:
        raise GraphQLError("換局要在前台上做：這個端點沒有 registry（單桌模式／開發行程）")
    # ★ 2026-10-20（review P3）：以前這裡兩層都不驗身分 ⇒ 匿名就能讓任意一桌換局
    #   （正在進行的局等於被踢掉）。判據與 setPaused／setArchived 一致：要有效帳號，
    #   而且是**現役那一局**的 owner；沒有 owner 的舊局至少要有效帳號（沒有 owner 可繼承）。
    _me = _who_id(ctx)
    if not _me:
        raise GraphQLError("換局要帶帳號憑證（`Authorization: Bearer …`）")
    try:
        from trpg.service import run_owner as _ro                                         # noqa: PLC0415
        _home = str(getattr(svc, "home", "") or "")
        _cur = str(svc.table.session_id() or "")
        if _home and _cur:
            _own = _ro.owner_of(_home, _cur)
            if _own and str(_own) != str(_me):
                raise GraphQLError("只有這一局的 owner 能換局")
    except GraphQLError:
        raise
    except Exception:                                                   # noqa: BLE001
        pass
    try:
        out = ctx.new_run(str(tableId), watchable=watchable) or {}
    except TypeError:                     # 舊 hook 不吃 kwargs（單桌／舊前台）⇒ 退回原呼叫
        out = ctx.new_run(str(tableId)) or {}
    if not out.get("ok"):
        raise GraphQLError(str(out.get("error") or "開新局失敗"))
    # 2026-10-19：新局也記 owner——換局的人若是誰的已知身分就用他，否則沿用上一局的 owner
    try:
        from trpg.service import run_owner as _ro                                         # noqa: PLC0415
        # 新局是**新的 run 目錄**（table_id＝run_id），不要在舊 home 寫 owner。
        _new = str(out.get("session") or "")
        _home = str(out.get("home") or "")
        if not _home and _new:
            _new_svc = ctx.table(str(out.get("table") or "")) if hasattr(ctx, "table") else None
            _home = str(getattr(_new_svc, "home", "") or "")
        if not _home:
            _home = str(getattr(ctx.need(tableId), "home", "") or "")
        if _home and _new:
            _ro.set_owner(_home, _new, _me)
    except Exception:                                                   # noqa: BLE001
        pass
    return out

def _m_set_paused(_root: Any, info: Any, tableId: str, session: str,
                  paused: bool, reason: str = "") -> Any:
    """**整桌暫停／繼續**——2026-10-19（設計者）：「owner 還能直接在頁面上暫停，不通過 agent 那側的工具」。

    與 `gmRun(op=pause|resume)` 是**同一個引擎動作**（`table.pause()`／`resume()`，事件流一樣留
    `paused`／`resumed`），差別只在權限：這裡認的是**這一局的 owner**，不需要 GM token。
    """
    ctx: Ctx = info.context
    if ctx.watch:
        raise GraphQLError("觀戰桌沒有寫入面（唯讀前門）")
    svc = ctx.need(tableId)
    _need_session(svc, session, name="setPaused")
    from trpg.service import run_owner as _ro                                            # noqa: PLC0415
    home = str(getattr(svc, "home", "") or "")
    own = _ro.owner_of(home, str(session))
    if not own:
        raise GraphQLError("這一局沒有 owner（舊局）⇒ 不能暫停／繼續")
    if own != _who_id(ctx):
        raise GraphQLError("只有這一局的 owner 能暫停／繼續")
    why = str(reason or "owner")
    try:
        out = (svc.table.pause(reason=why, by=_who_id(ctx)) if paused
               else svc.table.resume(reason=why, by=_who_id(ctx)))
    except Exception as exc:                                           # noqa: BLE001
        raise GraphQLError(f"{'暫停' if paused else '繼續'}失敗：{type(exc).__name__}: {exc}")
    return {"ok": True, "paused": bool(paused), "detail": out or {}}

def _m_set_archived(_root: Any, info: Any, tableId: str, session: str,
                     archived: bool) -> Any:
    """**封存這一局**（Archive）——2026-10-19（設計者）：owner 才能設；封存後不進選桌、也不能再寫。

    封存是**局**的事（`runs/<run>/archived.json`），不是桌的；解除封存也是 owner 的權限。
    """
    ctx: Ctx = info.context
    if ctx.watch:
        raise GraphQLError("觀戰桌沒有寫入面（唯讀前門）")
    svc = ctx.need(tableId)
    _need_session(svc, session, name="setArchived")
    from trpg.service import run_owner as _ro                          # noqa: PLC0415
    from trpg.service import run_archive as _ra                        # noqa: PLC0415
    home = str(getattr(svc, "home", "") or "")
    own = _ro.owner_of(home, str(session))
    if not own:
        raise GraphQLError("這一局沒有 owner（舊局）⇒ 不能改封存狀態")
    if own != _who_id(ctx):
        raise GraphQLError("只有這一局的 owner 能封存／解除封存")
    if not _ra.set_archived(home, str(session), bool(archived), by=_who_id(ctx)):
        raise GraphQLError("寫入封存狀態失敗")
    return {"ok": True, "archived": bool(archived)}

def _m_set_watchable(_root: Any, info: Any, tableId: str, session: str,
                      watchable: bool) -> Any:
    """**調整可見性**（這一局給不給人觀戰）——2026-10-19，**只有 owner 能做**。

    寫的是**這一局**的 `runs/<run>/watch.json`（`/watchtables/<run>` 的閘就是看它），
    由前台 hook 落檔＋即時生效（`Hub.set_watchable`）；單桌模式沒有 hook ⇒ 明確拒絕。
    ⚠ 2026-10-20 以前寫桌的 `config.json` ⇒ 關一局會影響下一局；現在局與局互不影響。
    """
    ctx: Ctx = info.context
    if ctx.watch:
        raise GraphQLError("觀戰桌沒有寫入面（唯讀前門）")
    svc = ctx.need(tableId)
    _need_session(svc, session, name="setWatchable")
    from trpg.service import run_owner as _ro                                            # noqa: PLC0415
    _home = str(getattr(svc, "home", "") or "")
    _own = _ro.owner_of(_home, str(session))
    if not _own:
        raise GraphQLError("這一局沒有 owner（舊局）⇒ 不能改可見性")
    if _own != _who_id(ctx):
        raise GraphQLError("只有這一局的 owner 能調整可見性")
    hook = getattr(ctx, "set_watchable", None)
    if hook is None:
        raise GraphQLError("調整可見性要在前台上做（單桌模式／開發行程沒有 registry）")
    ok = bool(hook(str(tableId), bool(watchable), str(session)))
    if not ok:
        raise GraphQLError("寫入這一局的 watch.json 失敗")
    return {"ok": True, "watchable": bool(watchable)}

def _m_request_gm(_root: Any, info: Any, tableId: str, session: str) -> Any:
    """**「要求 GM 就位」**（owner 2026-10-08：「全員落座之後，web 畫面上提供一個按鈕，要求 GM 就位」）。

    作用＝把「這一輪請 GM 說一句」那一腳交給**人按**（2026-10-21 拆景：沒有開景了）。
    判據、幂等鍵（同一輪只叫一次）、kick 事件都留在桌端那一支（`wake.maybe_kick_round`）——
    這裡只是入口，不養第二份條件（以免「按鈕說可以、桌端說不行」）。
    回 `{ok, sent, reason, detail}`：**不該叫／叫過了不是錯誤**（按鈕可以多按一下；`reason` 說明為什麼）。
    """
    from trpg.service import wake as _wake                                                 # noqa: PLC0415
    ctx = info.context
    if getattr(ctx, "watch", False):
        raise GraphQLError("觀戰桌沒有寫入面（唯讀前門）")
    svc = ctx.need(tableId)
    _need_session(svc, session, name="requestGm")
    # 2026-10-19（設計者）：「只有 owner 能呼喚 GM」——不是 owner 的人按了要講清楚，別靜默。
    try:
        from trpg.service import run_owner as _ro                                        # noqa: PLC0415
        _own = _ro.owner_of(str(getattr(svc, "home", "") or ""), str(session))
        if _own and _own != _who_id(ctx):
            raise GraphQLError("只有這一局的 owner 能呼喚 GM")
    except GraphQLError:
        raise
    except Exception:                                                  # noqa: BLE001
        pass
    if str(getattr(svc, "pacing_mode", "")) != "turn":
        raise GraphQLError("這張桌不是回合制——場景制本來就會自己叫 GM，不需要這個按鈕")
    if (svc.table.state or {}).get("ended"):
        raise GraphQLError("這一局已經結束了")
    # 這一段刻意**不**做「人齊了沒」的判斷：那是桌端的事（`min_seats_to_start`）。
    res = _wake.maybe_kick_round(svc) or {}
    _skip = str(res.get("skipped") or "")
    _why = {"not_all_seated": "人還沒齊（要等全部席位都落座）",
            "already_kicked": "這一輪已經叫過 GM 了（同一輪只叫一次）",
            "ended": "這一局已經結束了"}.get(_skip, "")
    return {"ok": bool(res.get("sent")), "sent": bool(res.get("sent")), "skipped": _skip,
            "reason": _why, "detail": {k: v for k, v in res.items() if k != "detail"}}
