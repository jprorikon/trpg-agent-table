"""queries（自 __init__.py 拆出）"""

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

from .common import _tid
from .context import Ctx, _anon_watch, _need_session, _table_info, _watch_of, call

from .view_shape import _view_shape

def _r_tables(_root: Any, info: Any) -> List[Dict[str, Any]]:
    """所有桌（**已封存的局不列**：設計者 2026-10-19「Archive 就不再出現在選桌裡面」）。

    封存是「這一局」的事 ⇒ 只濾掉 archived 的那一列；桌子本身與其他局照列。
    要找回封存的局：直接開它的 URL（`/tables/<run>`）——頁面照樣讀得到，只是不能再寫。
    """
    ctx: Ctx = info.context
    out = []
    for row in tdir.discover(ctx.root):
        info_row = _table_info(row, ctx)
        if info_row.get("archived"):
            continue
        out.append(info_row)
    return out

def _r_modules(_root: Any, info: Any) -> List[Dict[str, Any]]:
    """**按模組分組**的桌清單（大廳頁／app／建桌嚮導用）。

    模組 id 一律用 `layout.scan` 給的 `module`（新制＝`modules/<模組>/` 的目錄名；舊制＝桌名）。
    沒掃到模組的桌（config 讀不動）歸到 `（未分類）`——**寧可醜，也不要靜默吞掉一張桌**。
    """
    ctx: Ctx = info.context
    rows_all = tdir.discover(ctx.root)
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows_all:
        groups.setdefault(str(row.get("module") or "（未分類）"), []).append(row)
    # ★ 2026-10-20：模組與劇本的選單要看**定義層**（`modules/<模組>/scenarios/*/`），
    #   不能只看「現在有幾張桌」——某劇本的最後一張桌被關掉／hidden 之後，它會從建桌選單消失，
    #   再也開不回來（實測：示範劇本關桌後選不到）。桌數照實算，可以是 0。
    for _mid in layout.modules(ctx.root):
        groups.setdefault(_mid, [])
    out = []
    for mid in sorted(groups):
        rows = groups[mid]
        # **劇本層**：同一劇本底下的桌歸在一起（一個劇本可以有多張桌）
        scn: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            scn.setdefault(str(r.get("scenario") or ""), []).append(r)
        # 定義層的劇本全部列出來（tableCount 可以是 0）——建桌選單才有「蒼梧」這種目前沒桌的劇本
        if mid != "（未分類）":
            for _sid in layout.scenarios(ctx.root, mid):
                scn.setdefault(_sid, [])
        out.append({
            "id": mid,
            "name": (layout.module_name(ctx.root, mid) if mid != "（未分類）" else mid),
            "tableCount": len(rows),
            "characters": len(layout.characters(ctx.root, mid)) if mid != "（未分類）" else 0,
            # 大廳的封面／名字由模組給（2026-10-19）：素材表（封面別名 `cover`）＋畫面用語表；沒有＝null
            "assets": ((module_assets.load(ctx.root, mid).manifest() or None) if mid != "（未分類）" else None),
            "scenarios": [{"id": sid, "name": layout.scenario_name(ctx.root, mid, sid),
                           # 2026-10-20：開局難度（建桌選單要列；沒有 starting_difficulty ⇒ [])
                           "difficulties": (layout.scenario_difficulties(ctx.root, mid, sid)
                                            if mid != "（未分類）" else []),
                           "tableCount": len(scn[sid]),
                           "tables": [_table_info(r, ctx) for r in sorted(scn[sid],
                                                                           key=lambda x: str(x.get("id")))]}
                          for sid in sorted(scn)],
        })
    return out

def _r_health(_root: Any, info: Any) -> Dict[str, Any]:
    ctx: Ctx = info.context
    rows = [_table_info(row, ctx) for row in tdir.discover(ctx.root)]
    return {"status": "ok", "service": "trpg-graphql", "tables": rows}

def _r_table(_root: Any, info: Any, id: str) -> Dict[str, Any]:        # noqa: A002
    ctx: Ctx = info.context
    svc = ctx.need(id)
    row = next((r for r in tdir.discover(ctx.root) if r["id"] == str(id)), None)
    if row is None:                        # 單桌模式：目錄掃不到（home 不在 repo 底下）⇒ 就地組一個
        cfg = svc.cfg or {}
        row = {"id": _tid(svc), "home": svc.table.home, "module": str(cfg.get("table_id") or ""),
               "scenario": str(cfg.get("scenario") or ""), "watch_port": None}
    # ★ 2026-10-19：這一張桌的讀取模式就定在這一格上（子欄位照它走，見 `_watch_of`）。
    #   匿名只讀（觀戰）不再需要桌級端點：根 `/graphql` 自己按 query 判。
    return {"_svc": svc, "_row": row, "id": row["id"],
            "_watch": bool(ctx.watch) or _anon_watch(ctx, svc)}

def _table_archived(r: Dict[str, Any]) -> bool:
    """`table(id:)` 這一局封存了沒。"""
    try:
        from trpg.service import run_archive as _ra                                          # noqa: PLC0415
        svc = r.get("_svc")
        home = str(getattr(svc, "home", "") or "")
        run = str(svc.table.session_id() or "") if svc is not None else ""
        return bool(home and _ra.is_archived(home, run))
    except Exception:                                                      # noqa: BLE001
        return False

def _table_owner(r: Dict[str, Any]) -> str:
    """`table(id:)` 這一局的 owner（給 Table.owner／Table.isOwner 用）。"""
    try:
        from trpg.service import run_owner as _ro                                        # noqa: PLC0415
        svc = r.get("_svc")
        home = str(getattr(svc, "home", "") or "")
        run = str(svc.table.session_id() or "") if svc is not None else ""
        return _ro.owner_of(home, run) if home else ""
    except Exception:                                                  # noqa: BLE001
        return ""

def _r_me(_root: Any, info: Any) -> Optional[Dict[str, Any]]:
    ctx: Ctx = info.context
    if ctx.svc is None:
        # 大廳的根端點沒有掛桌（2026-10-19 大廳改版）：憑證是跨桌通用的（帳號層一份），
        # ⇒ 找第一張「認得這把憑證」的桌來回答；都不認得＝沒登入（null，不是錯誤）。
        out = None
        for row in tdir.discover(ctx.root):
            svc = ctx.table(row["id"])
            if svc is None:
                continue
            try:
                code, got = call(svc, "GET", "/api/me", headers=ctx.headers)
            except Exception:                                    # noqa: BLE001
                continue
            if code == 200 and isinstance(got, dict) and got.get("account"):
                out = got
                break
        if out is None:
            return None
        return {"account": out.get("account"), "display": out.get("display"), "kind": out.get("kind"),
                "_memberships": _memberships(ctx, out)}
    code, out = call(ctx.svc, "GET", "/api/me", headers=ctx.headers, watch=ctx.watch)
    if code != 200 or not isinstance(out, dict):
        raise GraphQLError(str((out or {}).get("error") or f"/api/me 回了 {code}"))
    return {"account": out.get("account"), "display": out.get("display"), "kind": out.get("kind"),
            "_memberships": _memberships(ctx, out)}

def _memberships(ctx: Ctx, me: Dict[str, Any]) -> List[Dict[str, Any]]:
    """「我在哪些桌坐哪一席、演哪個角色、錢包多少」——跨桌（含**我這桌之外的**）。"""
    out: List[Dict[str, Any]] = []
    for row in tdir.discover(ctx.root):
        svc = ctx.table(row["id"])
        info = _table_info(row, ctx)
        if svc is None:
            out.append({"table": info, "seat": None, "charId": None, "instance": None,
                        "wallet": None, "busyIn": []})
            continue
        try:                                             # 用同一支 handler 讀（含席位判定）
            _c, st = call(svc, "GET", "/api/me", headers=ctx.headers)
        except Exception:                                # noqa: BLE001
            st = None
        seat = (st or {}).get("seat") if isinstance(st, dict) else None
        mine = bool(seat) and str((st or {}).get("account") or "") == str(me.get("account") or "")
        out.append({"table": info, "seat": seat if mine else None,
                    "charId": ((st or {}).get("char") if mine else None),
                    "instance": ((st or {}).get("instance") if mine else None),
                    "wallet": ((st or {}).get("wallet") if mine else None),
                    "busyIn": []})
    return out

def _r_t_scalar(key: str):
    def _f(root: Dict[str, Any], info: Any) -> Any:
        return _table_info(root["_row"], info.context)[key]
    return _f

def _r_t_session(root: Dict[str, Any], info: Any) -> Dict[str, Any]:
    ctx: Ctx = info.context
    svc = root["_svc"]
    cur = str(svc.table.session_id() or "")
    cfg = svc.cfg or {}
    code, me = call(svc, "GET", "/api/me", headers=ctx.headers)
    wb = bool((me or {}).get("run_written_back")) if isinstance(me, dict) else False
    return {"id": cur, "module": str(cfg.get("table_id") or ""),
            "scenario": str(cfg.get("scenario") or ""), "startedAt": None,
            "ended": bool(tdir.ended(svc.table.home)), "writtenBack": wb}

def _seats_of(home: str) -> List[Dict[str, Any]]:
    """劇本排的席位（`seats.json` 的 `seats` 列）。

    ⚠ 用 `layout.def_file`：拆層後 `seats.json` 住**劇本目錄**（桌目錄只有 config／runs）。
    直接讀 `<桌目錄>/seats.json` 的寫法會讓新建的桌「一席都沒有」（2026-10-20 實測）。
    """
    try:
        with open(layout.def_file(home, "seats.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        return [r for r in (doc.get("seats") or []) if isinstance(r, dict)]
    except (OSError, ValueError):
        return []

def _r_t_entry(root: Dict[str, Any], info: Any) -> Dict[str, Any]:
    """**進場判定**（不寫任何東西；純讀）。

    設計者 2026-10-07 的流程：「進入前先確認帳戶有沒有對應劇本的角色，有的話匯入已有角色實例，
    沒有的話讓用戶選擇創建或進入旁觀模式」。政策**不重寫**：可用角色用 `web_api._chars_available`
    （與 login／選角 UI 同一支），跨桌衝突用 `web_api._busy_tables`（同帳號同角色不能同時兩桌）。
    """
    from trpg.accounts import auth as auth                                                            # noqa: PLC0415
    from trpg.api import web as web_api                                                         # noqa: PLC0415
    ctx: Ctx = info.context
    svc = root["_svc"]
    acct = web_api._accounts_of(svc)
    tok = auth.token_from_request(headers=ctx.headers)
    who = acct.resolve(tok) if (tok and getattr(acct, "enabled", False)) else None
    seats = _seats_of(svc.table.home)
    seat_rows = [r for r in seats if str(r.get("kind")) in ("human", "agent")]
    free = {(x["seat"], x["char"]) for x in (web_api._chars_available(svc, acct)
                                             if getattr(acct, "enabled", False) else
                                             [{"seat": r.get("seat"), "char": r.get("character")}
                                              for r in seat_rows])}
    # ── 「席位上有沒有人」＝**這一局的投影**（`table.state["seats"]`），**不是**帳號 store 的
    #    `acct.seats`：DB 後端下 `acct.seats`（舊的 JSON 欄）永遠是空的 ⇒ 用它會把「別人已經坐著的
    #    席位」當成空的（2026-10-08 owner 實測：p2 已被 agent（シズク）坐著，網頁還端出「建立新角色」）。
    try:
        live_seats = {str(k) for k in ((svc.table.state or {}).get("seats") or {}).keys()}
    except Exception:                                                  # noqa: BLE001
        live_seats = set()
    mine_inst: Dict[str, Any] = {}
    if who:
        for r in seat_rows:
            ch = str(r.get("character") or "")
            ins = acct.instance(who["account"], ch) if ch else None
            if ins:
                mine_inst[ch] = ins
    # **角色的類由模組定**（`modules/<模組>/characters.json`）：候選一律以模組角色表為準，
    # 劇本席位只決定「這一局誰參與、坐哪」。表裡沒登記的（作者漏了）**照樣端出來但要標記**
    # ——寧可看得見地不完整，也不要靜靜少一個可選角色。
    _mod = str(svc.cfg.get("module") or layout.where(svc.table.home, ctx.root).get("module")
               or _tid(svc))
    roster = layout.characters(ctx.root, _mod)
    importable, creatable, taken_seats = [], [], []
    for r in seat_rows:
        seat, ch = str(r.get("seat") or ""), str(r.get("character") or "")
        meta = roster.get(ch) or {}
        opt = {"seat": seat, "charId": ch, "kind": projection.seat_kind_public(str(r.get("kind") or "")),
               "className": str(meta.get("class") or ""), "name": str(meta.get("name") or ch),
               "inRoster": bool(meta), "mine": ch in mine_inst, "instance": mine_inst.get(ch)}
        # 三分的規矩（互斥就在這裡）：
        #   ① 我在這個模組有這個角色 ⇒ 匯入（就算我已經坐在那一席上，也是「我的」）
        #   ② 席位上已經有人（這一局的投影）且**不是我** ⇒ 只看見、不能選（＝互斥）
        #   ③ 其他 ⇒ 可以新開
        if ch in mine_inst:
            importable.append(opt)
        elif seat in live_seats:
            taken_seats.append(opt)
        else:
            creatable.append(opt)
    watch_cfg = (svc.cfg.get("watch") or {})
    # ★ 2026-10-20：觀戰頁的正規 URL 是 **run 級**（`/watchtables/<run>`，與 `/tables/<run>` 對稱）。
    #   舊的 `/<桌>/watch/` 只剩前台兜底（會被 SPA 轉走）——別再讓客戶端（app／橋）拿舊形態，
    #   否則每開一次觀戰都多一跳，而且舊前綴在拆前後台之後已經沒有頁面了。
    #   開關也是局級：這一局的 `watch.json` 優先，舊局沒有檔才用 config 的預設。
    _run = str(svc.table.session_id() or "")
    _watch_on = bool(watch_cfg.get("enabled"))
    try:
        from trpg.service import run_watch as _rw                                        # noqa: PLC0415
        _got = _rw.read(str(getattr(svc, "home", "") or ""), _run)
        if _got is not None:
            _watch_on = bool(_got)
    except Exception:                                                                     # noqa: BLE001
        pass
    watch_path = f'/watchtables/{_run}' if (_watch_on and _run) else None
    reason = ""
    # 「要不要憑證」看的是**這一桌的宣告**（`auth.require_account`）或帳號層真的有東西；
    # 只看 `enabled` 會漏掉「宣告要憑證但 registry 還空」的桌（那時更該提示他貼憑證）。
    _needs_auth = bool((svc.cfg.get("auth") or {}).get("require_account")) \
        or bool(getattr(acct, "enabled", False))
    if not who and _needs_auth:
        reason = "這張桌要帳號憑證（貼上憑證或登入後再看得到你的角色）"
    return {"rosterSize": len(roster), "session": str(svc.table.session_id() or ""),
            "account": (who or {}).get("account"), "hasInstance": bool(mine_inst),
            "importable": importable, "creatable": creatable, "takenSeats": taken_seats,
            "spectateOk": True, "watchPath": watch_path, "reason": reason}

def _r_t_tools(root: Dict[str, Any], info: Any) -> List[Dict[str, Any]]:
    """這一桌的**工具目錄**（MCP 的 7 格）——直接端 `svc.tools`（＝`mcp_api.tools_for()` 那一份，
    已按這一桌的 config 調整過描述／列舉）。

    ⚠ 這一格的意義：MCP 適配器**不必抄第二份描述**、也不可能漂移（設計者：「保持 trpg 應用本身乾淨」
    ＋「工具描述逐字保持一樣」）。
    """
    if _watch_of(root, info.context):
        # 匿名只讀（觀戰）：不端 agent 面（工具目錄是給 MCP／GM 的，不是公開資訊）
        return []
    return [{"name": str(t.get("name") or ""), "description": str(t.get("description") or ""),
             "inputSchema": t.get("inputSchema") or {}} for t in (root["_svc"].tools or [])]

def _r_t_capabilities(root: Dict[str, Any], info: Any) -> Dict[str, Any]:
    svc = root["_svc"]
    cfg = svc.cfg or {}
    packs = cfg.get("packs") or {}
    wake = cfg.get("wake") or {}
    rules = cfg.get("rules") or {}
    return {
        "tableId": _tid(svc), "module": _tid(svc), "scenario": str(cfg.get("scenario") or ""),
        "mode": "turn",
        "packs": [str(x) for x in (packs.get("enabled") or [])],
        "allowedActions": [], "attributes": [str(x) for x in (rules.get("attributes") or [])],
        "board": bool(getattr(svc.table, "board", None) or cfg.get("board")),
        "turnSeconds": (int(cfg["turn_seconds"]) if cfg.get("turn_seconds") is not None else None),
        "quota": cfg.get("quota"), "toolPrefix": str(wake.get("tool_prefix") or ""),
    }


def _r_t_view(root: Dict[str, Any], info: Any, session: str, seat: Optional[str] = None,
              detail: Optional[str] = None) -> Any:
    ctx: Ctx = info.context
    svc = root["_svc"]
    _need_session(svc, session, name="view")
    _w = _watch_of(root, ctx)
    # `/api/state` 吃 query ⇒ 直接把參數拼進 path（handler 就是這樣讀的）
    _qp = {k: v for k, v in (("seat", seat), ("detail", detail)) if v is not None}
    if _w:
        _qp.pop("seat", None)          # 匿名只讀：席位一律不認（公開投影）
    # ⚠ **GM 那條路**：GET 分支只認 `?gm_token=`（`q.get("gm_token") == cfg.auth.gm_token`），不看標頭
    #   ⇒ 標頭帶來的 GM 鍵要轉進 query（**只在行程內**，不出網路、不落日誌）。
    try:
        _gmt = str(ctx.headers.get("X-Table-GM-Token") or "")
    except Exception:
        _gmt = ""
    if _gmt:
        _qp["gm_token"] = _gmt
    qs = "&".join(f"{k}={quote(str(v))}" for k, v in _qp.items())
    code, out = call(svc, "GET", "/api/state" + (f"?{qs}" if qs else ""),
                     headers=ctx.headers, watch=_w)
    if code != 200:
        raise GraphQLError(str((out or {}).get("error") or f"/api/state 回了 {code}"))
    # ★ 2026-10-19（前台回報）：**公開投影**（匿名觀戰那條）裡沒有 `auth` 這一格 ⇒ `View.auth`
    #   會變成 hasAccounts=false／required=false，前台就分不出「這張桌到底要不要帳號」
    #   （實測 hyakki_tower：`entry.reason` 說要憑證，`View.auth` 卻說不用）。
    #   一律用 `web_api._auth_info(svc)` 蓋掉——與 `/api/state` 同一支，權威只有一份。
    try:
        from trpg.api import web as _wa                                           # noqa: PLC0415
        if isinstance(out, dict):
            out["auth"] = _wa._auth_info(svc)
    except Exception:                                                       # noqa: BLE001
        pass
    return _view_shape(out, session=str(svc.table.session_id() or ""))

def _r_t_log(root: Dict[str, Any], info: Any, session: str, sinceSeq: Optional[int] = None,  # noqa: N803
             filter: Optional[str] = None, limit: Optional[int] = None,     # noqa: A002
             meta: Optional[bool] = None) -> Any:
    ctx: Ctx = info.context
    svc = root["_svc"]
    _need_session(svc, session, name="log")
    qs = []
    if sinceSeq is not None:
        qs.append(f"since_seq={int(sinceSeq)}")
    if filter:
        qs.append(f"filter={filter}")
    if limit is not None:
        qs.append(f"limit={int(limit)}")
    if meta:
        qs.append("meta=1")
    code, out = call(svc, "GET", "/api/log" + (f"?{'&'.join(qs)}" if qs else ""),
                     headers=ctx.headers, watch=_watch_of(root, ctx))
    if code != 200:
        raise GraphQLError(str((out or {}).get("error") or f"/api/log 回了 {code}"))
    return out

def _r_t_map(root: Dict[str, Any], info: Any, session: str) -> Any:
    ctx: Ctx = info.context
    svc = root["_svc"]
    _need_session(svc, session, name="map")
    code, out = call(svc, "GET", "/api/map", headers=ctx.headers, watch=_watch_of(root, ctx))
    if code != 200:
        raise GraphQLError(str((out or {}).get("error") or f"/api/map 回了 {code}"))
    return out
