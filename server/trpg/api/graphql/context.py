"""context（自 __init__.py 拆出）"""

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

from .common import ARG_ALIAS, SESSION_EXEMPT, _count_events, _tid

class _Headers(dict):
    """大小寫不敏感的表頭（handler 那邊用 `.get("Authorization")`）。"""

    def __init__(self, src: Any = None) -> None:
        super().__init__()
        for k, v in dict(src or {}).items():
            self[str(k)] = v

    def get(self, key: str, default: Any = None) -> Any:      # type: ignore[override]
        for k, v in self.items():
            if str(k).lower() == str(key).lower():
                return v
        return default

class _Sink:
    """`wfile` 的替身（handler 只會 write，我們不真的回 HTTP）。"""

    def write(self, *_a: Any) -> None:
        pass

    def flush(self) -> None:
        pass

class _Shim:
    """把既有的 handler 當「操作層」在行程內叫一次：**政策只有一份**。

    只需要 handler 真的會用到的幾個屬性（`path`／`headers`／`_body()`／`service`／`_json`…），
    其餘（`send_response`／`send_header`／`end_headers`／`wfile`）都收成 no-op。
    """

    def __init__(self, svc: Any, path: str, *, body: Optional[Dict[str, Any]] = None,
                 headers: Any = None, watch: bool = False) -> None:
        self.service = svc
        self.path = path
        self.headers = _Headers(headers)
        self._watch_mode = bool(watch)
        self._internal = True      # ★ 自己是「新的面」的內部呼叫 ⇒ 不受 `/api/*` 退役閘（410）影響
        self._body_obj = dict(body or {})
        self.code: int = 200
        self.payload: Any = None
        self.ctype: str = "application/json"
        self.wfile = _Sink()
        self._nocache_sent = True

    def _body(self) -> Dict[str, Any]:
        return dict(self._body_obj)

    def _json(self, code: int, obj: Any, extra: Any = None) -> None:
        self.code, self.payload = int(code), obj

    def _send(self, code: int, body: bytes = b"", ctype: str = "application/json",
              extra: Any = None) -> None:
        self.code, self.ctype = int(code), ctype
        try:
            self.payload = json.loads(body.decode("utf-8") or "{}")
        except Exception:                                                # noqa: BLE001
            self.payload = {"_raw": body.decode("utf-8", "replace")}

    def send_response(self, *_a: Any, **_k: Any) -> None:
        pass

    def send_header(self, *_a: Any, **_k: Any) -> None:
        pass

    def end_headers(self) -> None:
        self._nocache_sent = True

    def log_message(self, *_a: Any, **_k: Any) -> None:
        pass

    def _handle_mcp(self, _body: Dict[str, Any]) -> None:
        raise GraphQLError("GraphQL 面沒有 MCP（那是另一個端點；agent 走 MCP）")

    def _post_auth(self, body: Dict[str, Any], svc: Any) -> bool:
        """把 POST 的 account 認證閘借給 shim（政策只有一份，住 `web/post_auth.py`）。"""
        from trpg.api import web as web_api                                        # noqa: PLC0415
        return web_api.Handler._post_auth(self, body, svc)

def call(svc: Any, method: str, path: str, *, body: Optional[Dict[str, Any]] = None,
         headers: Any = None, watch: bool = False) -> Tuple[int, Any]:
    """在行程內叫一次既有 handler（`web_api.Handler`）——**GraphQL 與 REST 共用同一份政策**。"""
    from trpg.api import web as web_api                                                        # noqa: PLC0415
    shim = _Shim(svc, path, body=body, headers=headers, watch=watch)
    shim.WATCH_GET = web_api.Handler.WATCH_GET      # 觀戰唯讀白名單**用 handler 那一份**（不另寫）
    if method == "GET":
        web_api.Handler.do_GET(shim)
    else:
        web_api.Handler.do_POST(shim)
    return shim.code, shim.payload

class Ctx:
    """一次請求的上下文（誰在問、要問哪張桌、根目錄在哪）。"""

    def __init__(self, *, svc: Any = None, lookup: Any = None, root: str = "",
                 headers: Any = None, watch: bool = False, new_run: Any = None,
                 create_table: Any = None, set_watchable: Any = None,
                 read_only: bool = False) -> None:
        self.svc = svc
        self.watch = bool(watch)
        # ★ 2026-10-19（設計者：「graphql 應該技術上是支持按照 query 做訪問控制的」）：
        #   `read_only` ＝ 這一發是**只讀查詢**（operation=query）。寫面（mutation／subscription）
        #   一律 False ⇒ 匿名永遠拿不到寫入面。真正「匿名只讀」的判定在 `_anon_watch()`。
        self.read_only = bool(read_only)
        self.headers = _Headers(headers)
        self.root = root or (layout.repo_root(svc.table.home) if svc is not None else "")
        self._lookup = lookup
        # **前台層的換局**（`Hub.new_run`）：只有前台能換 registry 那一格；單桌模式＝None。
        self.new_run = new_run
        self.create_table = create_table
        self.set_watchable = set_watchable

    def table(self, tid: str) -> Any:
        if self._lookup is not None:
            got = self._lookup(str(tid))
            if got is not None:
                return got
        if self.svc is not None and _tid(self.svc) == str(tid):
            return self.svc
        return None

    def who(self) -> Any:
        """憑證 ⇒ 身分（沒有憑證／認不得 ⇒ `None`）。token 一律從標頭拿（不進參數／prompt）。"""
        from trpg.api import web as web_api                                                       # 延遲 import（免循環）
        svc = self.svc
        if svc is None:
            # 前台根端點：拿**任一桌**的帳號層來驗——hub 裡的桌共用同一份 registry（DB／accounts_file），
            # 所以用哪一張桌驗結果都一樣（`peer()` 就是靠這一點做跨桌檢查）。
            for row in tdir.discover(self.root):
                svc = self._lookup(str(row.get("id") or ""))
                if svc is not None:
                    break
        if svc is None:
            return None
        try:
            _tok = auth.token_from_request(headers=self.headers)
            return web_api._accounts_of(svc).resolve(_tok) if _tok else None
        except Exception:                                                    # noqa: BLE001
            return None

    def need(self, tid: str) -> Any:
        svc = self.table(tid)
        if svc is None:
            have = _tid(self.svc) if self.svc is not None else "（這個端點沒有掛桌）"
            raise GraphQLError(f"找不到桌 `{tid}`（這個端點服務的是 {have}）")
        return svc

def _session_ok(svc: Any, session: str) -> Optional[str]:
    """`session` 必須等於這一桌**現役那一局**。不符 ⇒ 回錯誤字串（呼叫端轉成 GraphQLError）。"""
    now = str(svc.table.session_id() or "")
    want = str(session or "").strip()
    if not want:
        return "這個操作要帶 session（＝這一局的副本 id；`table(id){session{id}}` 拿得到）"
    if want != now:
        return (f"session 不對：這一桌現在的 session 是 `{now}`，你帶的是 `{want}`"
                "（一副本一 session；換局之後舊 session 不能寫）")
    return None

def _need_session(svc: Any, session: str, *, name: str) -> None:
    if name in SESSION_EXEMPT:
        return
    err = _session_ok(svc, session)
    if err:
        raise GraphQLError(err)

def _anon_watch(ctx: "Ctx", svc: Any) -> bool:
    """**匿名只讀（觀戰）** 的判定——「按 query 做訪問控制」的那一半。

    設計者 2026-10-19：「graphql 應該技術上是支持按照 query 做訪問控制的」。判據四條，
    全部成立才給公開投影（＝舊 `/watchtables/<run>/graphql` 那一份：`you` 缺席、沒有私密）：

      ① 這一發是**只讀查詢**（`operation=query`；mutation／subscription 一律不給）；
      ② **完全沒帶憑證**（帶了憑證就交給正常路徑：認不得就明講「token 不認得」，不靜默降級）；
      ③ 這張桌**宣告要 account**（`auth.require_account`）——不宣告的桌本來就公開；
      ④ **這一局**開了觀戰（`runs/<run>/watch.json`；舊局沒有檔才退回 `config.watch.enabled`）。
         2026-10-20 設計者：「现在没有桌设计了，所有的都是局」⇒ 開關是局級。

    桌級別的結果存在 `table()` 回傳的那個 dict 的 `_watch` 上，子欄位（view／log／map…）照它走
    ⇒ 一次請求問多張桌時不會互相污染。
    """
    if not getattr(ctx, "read_only", False):
        return False
    from trpg.accounts import auth as _auth                                                # noqa: PLC0415
    if _auth.token_from_request(headers=ctx.headers):
        # ⚠ 帶了憑證就不降級：舊 token／別的帳號的 token ⇒ 照舊報錯（前端才知道要重登）。
        #   否則「憑證過期」會變成靜靜被降成觀戰，最難查。
        return False
    cfg = getattr(svc, "cfg", None) or {}
    if not bool((cfg.get("auth") or {}).get("require_account")):
        return False
    # 局級開關：這一局有 `runs/<run>/watch.json` 就以此為準；舊局沒有檔才退回 config 預設。
    try:
        from trpg.service import run_watch as _rw                                        # noqa: PLC0415
        _home = str(getattr(svc, "home", "") or "")
        _run = str(svc.table.session_id() or "")
        _got = _rw.read(_home, _run) if _home else None
        if _got is not None:
            return bool(_got)
    except Exception:                                                                     # noqa: BLE001
        pass
    return bool((cfg.get("watch") or {}).get("enabled"))

def _watch_of(root: Any, ctx: "Ctx") -> bool:
    """這一張桌的讀取模式（`_watch` 在 `table()` 那一格上；沒有就沿請求層級的旗標）。"""
    if isinstance(root, dict) and "_watch" in root:
        return bool(root.get("_watch"))
    return bool(getattr(ctx, "watch", False))

def _is_read_operation(text: str) -> bool:
    """這一發是不是「只讀查詢」（operation=query）——按 query 做訪問控制的入口判據。

    mutation／subscription ⇒ False；解析不了（語法錯）⇒ False（保守：不放行，交給 graphql 回語法錯）。
    一份文件可以有多個具名操作（靠 `operationName` 選）⇒ **全部都是 query** 才算只讀。
    """
    try:
        from graphql import parse as _parse                                # noqa: PLC0415
        doc = _parse(text)
    except Exception:                                                       # noqa: BLE001
        return False
    ops = [d for d in doc.definitions if getattr(d, "operation", None) is not None]
    if not ops:
        return False
    return all(str(getattr(getattr(op, "operation", None), "value",
                           getattr(op, "operation", ""))) == "query" for op in ops)

def _body_of(args: Dict[str, Any], *, skip: Tuple[str, ...] = ()) -> Dict[str, Any]:
    """GraphQL 參數 → REST body（**原名優先**；camelCase 別名對應回 snake_case）。"""
    out: Dict[str, Any] = {}
    for k, v in (args or {}).items():
        if k in skip or v is None:
            continue
        out[ARG_ALIAS.get(k, k)] = v
    return out

def _run_owner(row: Dict[str, Any], cfg: Dict[str, Any], h: Dict[str, Any]) -> str:
    """這一局的 owner（`runs/<run>/owner` 一行；沒有＝空字串）。"""
    try:
        from trpg.service import run_owner as _ro                                    # noqa: PLC0415
        home = str(row.get("home") or "")
        run = str((h or {}).get("session") or "")
        if not run:
            from trpg.core import store as _st                                        # noqa: PLC0415
            run = str(_st.current_run(home) or "")
        return _ro.owner_of(home, run) if home else ""
    except Exception:                                            # noqa: BLE001
        return ""

def _run_archived(row: Dict[str, Any], h: Dict[str, Any]) -> bool:
    """這一局是不是 Archive（`runs/<run>/archived.json`）。"""
    try:
        from trpg.service import run_archive as _ra                                          # noqa: PLC0415
        home = str(row.get("home") or "")
        run = str((h or {}).get("session") or "")
        if not run:
            from trpg.core import store as _st                                            # noqa: PLC0415
            run = str(_st.current_run(home) or "")
        return bool(home and _ra.is_archived(home, run))
    except Exception:                                                      # noqa: BLE001
        return False

def _who_id(ctx: Any) -> str:
    """請求者的帳號 id（沒有憑證＝空字串）。

    ⚠ 2026-10-20：以前寫成 `str((w or {}).get("account") ...) or ""` ⇒ 匿名時
    `str(None)`＝`"None"`（**非空字串**）⇒ 各處 `if not _who_id(ctx)` 的匿名判斷失效
    （newRun 會把匿名當成一個叫 "None" 的帳號）。這裡先取出值再 `or ""`。
    """
    try:
        w = ctx.who()
        aid = (w or {}).get("account") if isinstance(w, dict) else w
        return str(aid or "")
    except Exception:                                            # noqa: BLE001
        return ""

def _table_info(row: Dict[str, Any], ctx: Ctx) -> Dict[str, Any]:
    home = row["home"]
    svc = ctx.table(row["id"])
    cfg = layout.read_config(home) or {}
    h: Dict[str, Any] = {}
    if svc is not None:
        _code, h = call(svc, "GET", "/health")
        h = h if isinstance(h, dict) else {}
    return {
        "id": row["id"], "module": row["module"] or "",
        # 顯示名：模組名（module.json）／劇本名（scenario.json 的 meta.name）——設計者 2026-10-07
        "moduleName": str(row.get("module_name") or row.get("module") or ""),
        "scenario": row["scenario"] or "",
        "scenarioName": str(row.get("scenario_name") or row.get("scenario") or ""),
        "name": str(cfg.get("name") or "") or None,
        "online": bool(svc is not None) and h.get("status") == "ok",
        "ended": bool(h.get("ended")) if h else bool(tdir.ended(home)),
        "seats": h.get("seats"),
        # 劇本設計的玩家席數（`seats.json`）——大廳的 `n/m 席` 分母；拿不到＝null（只顯示 n）
        "seatCap": h.get("seat_cap", row.get("seat_cap")),
        "events": _count_events(home),
        # 2026-10-19：觀戰改看開關（hub 模式沒有 per-table 觀戰埠，用 port 判斷會一律 false）
        "watchable": bool(row["watch_enabled"] if "watch_enabled" in row else row.get("watch_port")),
        # 2026-10-19：這一局有沒有 owner（設計者：「一個 run 最好有一個 owner」）——
        # 只有 owner 能呼喚 GM 與調整可見性；`isOwner` 給前端決定要不要顯示那兩顆。
        "owner": _run_owner(row, cfg, h) or None,
        # 2026-10-19：這一局封存了沒（Archive＝owner 收起來；不進選桌、也不能再寫）
        "archived": _run_archived(row, h),
        "isOwner": bool(_run_owner(row, cfg, h) and _run_owner(row, cfg, h) == _who_id(ctx)),
        "session": (h.get("session") or store.current_run(home) or None),
        # 大廳用（2026-10-19）：第幾景／這一景開著沒／暫停中／還在等誰出手（都是公開投影的事）
        "roundNo": int(h.get("round_no") or 0),
        "paused": bool(h.get("paused")),
        "waiting": [str(x) for x in (h.get("waiting") or [])],
        # ★ 2026-10-21：開局難度（建桌時選的 easy／normal／hard；`config.difficulty`）。
        #   以前只有「選」的入口、沒有「看」的地方——大廳看不出這一局是多難。
        "difficulty": (str(cfg.get("difficulty")) if cfg.get("difficulty") else None),
    }
