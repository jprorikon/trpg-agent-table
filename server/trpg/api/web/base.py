#!/usr/bin/env python3
"""**L6｜HTTP handler 的共用底座**——模組級工具（認證、帳號、素材、忙碌桌偵測）＋ handler 的低階輸出。

從 `web_api.py` 拆出（2026-10-20 補齊重構漏掉的檔案）。
紀律不變：路由只做參數搬運與回碼；判斷與裁決一律在 `TableService`／引擎；
回應一律 `Cache-Control: no-store`（素材圖除外，`_send()` 自己指定）。
"""
from __future__ import annotations

import contextlib
import email.utils
import json
import os
import re
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
# ★ 2026-10-24（review R1）：憑證比對收進 `trpg.core.gmtoken`（唯一一份）。
#   `_secret_eq` 這個名字留著＝同一個實作（`test_secret_token` 直接咬 `web_api._secret_eq`）。
#   GM 閘一律走 `gm_token_ok()`——**配置沒 token ⇒ False**（修前是「跟空字串比 ⇒ 放行」）。
from trpg.core.gmtoken import gm_token_ok, secret_eq as _secret_eq       # noqa: E402
from trpg.service import assets
from trpg.service import table_directory as tdir
from trpg.service.wake import maybe_kick_round, round_kick_key

if TYPE_CHECKING:
    from trpg.service.service import TableService

#: `_secret_eq` 的實作已搬進 `trpg.core.gmtoken.secret_eq`（上面 import 進來，名字不變）。
#: 2026-10-24（review R1）：這裡從前自己養一份，現在兩處（web／mcp）共用同一份，
#: 免得「同一把 token 兩條路各比一次」再漂開。


def _watch_token_ok(svc: Any, tok: Optional[str]) -> bool:
    """**觀戰桌的讀取憑證**（`config.watch.read_token`）——只放行「公開投影」。

    觀戰桌（`watch_server.py`）是唯讀前門：`require_account` 開著的桌，匿名連讀都會 403，
    所以給它一把專供讀取的憑證。**它拿不到任何席位／私密**——比對成功就當成「沒帶 token 的
    公開視圖」（下面 `q.pop("seat")`），`gm_token` 那條路與它無關。
    """
    want = str(((svc.cfg.get("watch") or {}).get("read_token")) or "")
    return bool(want) and bool(tok) and _secret_eq(tok, want)


def _auth_info(svc: Any) -> Dict[str, Any]:
    """這一桌的帳號層狀態（**回應組裝層**的事，L2 投影不碰）：`has_accounts`／`required`／`seat_link`。

    前端據 `has_accounts` 決定要不要顯示登入卡——**沒有帳號層的桌**以前照樣掛著那張卡，
    貼什麼都回「token 不認得」，看起來像自己打錯密碼（2026-10-07 介面驗收記下的坑）。
    `seat_link`＝**相容期的 `?seat=` 連結**（貼連結＝坐在那一席）：預設 **false＝收掉**
    （2026-10-07 第二階段：進桌一律走帳號），要留的桌自己寫 `config.auth.seat_link: true`。
    """
    try:
        acct = _accounts_of(svc)
        a = (svc.cfg.get("auth") or {})
        return {"has_accounts": bool(acct.enabled),
                "required": bool(a.get("require_account")),
                "seat_link": bool(a.get("seat_link"))}
    except Exception:                                                   # noqa: BLE001
        return {"has_accounts": False, "required": False, "seat_link": False}


def _is_api_path(path: str) -> bool:
    """這條路徑是不是舊的 `/api/*`。

    ⚠ 前台（`hub_server`）轉發時**前綴沒有剝**（`self.path` 是 `/hyakki_tower/api/state`）⇒
    用 `startswith("/api/")` 會漏掉（實測踩過：退役閘沒發動，回的是 403 而不是 410）。
    """
    _i = str(path or "").find("/api/")
    return _i >= 0 and str(path)[_i:].startswith("/api/")


def _legacy_rest_ok() -> bool:
    """舊面要不要放行（**只給測試**：`TRPG_LEGACY_REST=1`）。

    `/api/*` 2026-10-07 退役：生產（沒設這個環境變數）**外部一律 410**。測試驗的是**引擎的 HTTP
    契約**，而新的 GraphQL 面在行程內就是「換入口叫同一支 handler」⇒ 讓測試用這個開關驗舊門
    ＝驗的還是同一份政策。**要在正式行程 unset**（hub 起的時候會大聲提醒）。
    """
    return str(os.environ.get("TRPG_LEGACY_REST") or "") in ("1", "true", "yes")


def _watch_json(h: Any, svc: Any, path: str, q: Dict[str, Any]) -> None:
    """觀戰的公開投影（＝以前 `watch_server` 代理上游拿到的那一份；同進程直接問服務）。

    ⚠ 一律 `seat=None, gm=False`：觀戰者**沒有身分**，連 `gm_token` 都不看。
    """
    if path == "/api/state":
        try:
            h._json(200, svc.table.view(seat=None, gm=False, detail=q.get("detail")))
        except Exception as exc:                                          # noqa: BLE001
            h._json(400, {"error": str(exc)})
        return
    if path == "/api/log":
        if q.get("meta") in ("1", "true", "yes"):                        # 最小資訊窗（筆數／尾序號）
            raw = svc.table.events
            h._json(200, {"count": len(raw), "last_seq": (raw[-1].get("seq") if raw else None)})
            return
        h._json(200, {"events": svc.table.public_events()})              # 公開投影（view 白名單 ∩ public）
        return
    if path == "/api/map":
        t = svc.table
        h._json(200, t.board_map() if hasattr(t, "board_map") else {})
        return
    h._json(404, {"error": "觀戰桌沒有這一條（唯讀前門）"})


def _maybe_writeback(svc: Any, acct: Any) -> None:
    """**收局寫回**（冪等）：桌子判了結局（`state.ended`）就把**局外該帶走的**寫回 store——

    * `level／exp` → 該 **char 實例**（`account×module×char`；說法 3）
    * **貨幣** → 該 **帳號在這一模組的錢包**（說法 2）
    * ★／道具／記憶／位置＝局內 ⇒ 不帶走（只留一份 `snapshot` 在**這一副本**的參與列上，說法 4）

    冪等鍵＝**這一局的 `run_id`**（DB 版寫 `run.written_back`；JSON 版落回桌級 `session.written_back`
    ⇒ legacy 行為一字不變）。呼叫時機：下一次請求順手結清，不必等誰按什麼。
    """
    if not acct.enabled:
        return
    t = getattr(svc, "table", None)
    if t is None or not t.state.get("ended"):
        return
    run_id = str(t.adapter_view().get("run_id") or "")
    if acct.run_written_back(run_id):
        return
    # ⚠ 先把「已寫回」蓋上再逐席寫——JSON 後端 `add_wallet`／`write_instance` 內部會 `refresh()`
    #   （比 mtime 重載），順序顛倒的話最後那筆旗標會被重載蓋掉（踩過）。
    acct.mark_run_written_back(run_id)
    snapshots: Dict[str, Any] = {}
    for seat, b in list((acct.seats or {}).items()):
        aid, ch = (b or {}).get("account"), (b or {}).get("char")
        if not aid or not ch:
            continue
        try:
            acct.write_instance(aid, ch, {"level": int(t.level_of(seat)), "exp": int(t.exp_of(seat))})
        except Exception:                                               # noqa: BLE001
            continue
        # 局內那幾格：只留存證（不寫回局外）——★／道具／記憶／位置
        try:
            snapshots[ch] = {
                "seat": seat,
                "star": (t.state.get("star", {}) or {}).get(seat),
                "items": (t.state.get("inventory", {}) or {}).get(seat),
                "memory": ((t.state.get("seats", {}) or {}).get(seat) or {}).get("memory"),
                "node": ((t.state.get("seats", {}) or {}).get(seat) or {}).get("node"),
            }
        except Exception:                                               # noqa: BLE001
            pass
        try:
            _ck = getattr(t, "slot", lambda _n: "star_coins")("coin")
            coins = int((t.state.get("seats", {}).get(seat) or {}).get(_ck) or 0)
            if coins and acct.coins_written(aid, ch, run_id, seat) != coins:
                # 幂等鍵＝**這一副本**（DB：`run_participation.coins_written`；JSON：座次上的舊欄）
                acct.add_wallet(aid, star_coins=coins)
                acct.set_coins_written(aid, ch, run_id, coins, seat)
        except Exception:                                               # noqa: BLE001
            pass
    try:
        acct.finish_run(run_id, snapshots)          # DB：副本標 ended＋參與列留快照；JSON：no-op
    except Exception:                                                   # noqa: BLE001
        pass


def _carry_over_done(table: Any, seat: str) -> bool:
    """這一局這一席**已經帶過錢包**了嗎？

    ★ 2026-10-20（review P1）：以前看 `DbAccounts.seats` 的 `wallet_carried`，但 DB 走
    `run_seat` 時那一格寫死 False（每次進場都再發一次 `carry_over`）；JSON 後端則一桌只帶
    一次（新局反而不帶）。**事件的 append-only 流才是「這一局發生過什麼」的權威**：
    同一局已經有一筆這個席位的 `coin_grant reason=carry_over` ⇒ 不再帶。兩個後端語意一致。
    """
    sid = str(seat or "")
    for e in (getattr(table, "events", None) or []):
        if (str(e.get("type") or "") == "coin_grant"
                and str(e.get("reason") or "") == "carry_over"
                and str(e.get("seat") or "") == sid):
            return True
    return False


def carry_over_wallet(svc: Any, acct: Any, seat: str,
                      wallet: Dict[str, Any]) -> bool:
    """把局外錢包帶進這一局：**同一局同一席最多一次**（有事件流當冪等鍵）。回 True＝真的帶了。"""
    amount = int((wallet or {}).get("star_coins") or 0)
    if amount <= 0:
        return False
    t = getattr(svc, "table", None)
    if t is None:
        return False
    if _carry_over_done(t, seat):
        return False
    lock = getattr(t, "lock", None) or contextlib.nullcontext()
    with lock:
        if _carry_over_done(t, seat):        # 併發的第二次 enter：鎖內再判一次
            return False
        t.grant_coins(seat=str(seat), amount=amount, reason="carry_over")
        try:
            acct.set_seat_field(str(seat), "wallet_carried", True)   # 相容舊工具／舊讀取
        except Exception:                                            # noqa: BLE001
            pass
        return True


def _chars_available(svc: Any, acct: Any) -> List[Dict[str, Any]]:
    """這一局**包排了哪些 char 還沒被佔**（角色選擇 UI 用）。

    ⚠ 讀 `layout.def_file`：拆層後 `seats.json` 住**劇本目錄**，新桌的桌目錄裡只有 config／runs。
    以前直接讀 `<桌目錄>/seats.json`，新建的桌就永遠回空（2026-10-20：app 選了角色仍被拒
    「這一局沒有可選的 X」）。
    """
    seats_doc = {}
    try:
        with open(layout.def_file(svc.table.home, "seats.json"), encoding="utf-8") as fh:
            seats_doc = json.load(fh)
    except Exception:                                                   # noqa: BLE001
        return []
    taken = {b.get("char") for b in (acct.seats or {}).values() if (b or {}).get("char")}
    out = []
    for row in (seats_doc.get("seats") or []):
        if str(row.get("kind")) not in ("human", "agent"):
            continue
        ch = row.get("character")
        if ch and ch not in taken:
            out.append({"char": ch, "seat": row.get("seat"), "kind": row.get("kind")})
    return out


def _pick_char(svc: Any, acct: Any, who: Dict[str, Any], char: str) -> Dict[str, Any]:
    """角色選擇：account 挑一個 char ⇒ 綁到包排給那個 char 的席位（**account↔char 由玩家選、char↔seat 由包**）。"""
    if not char:
        return {"ok": False, "error": "沒指定 char", "chars_available": _chars_available(svc, acct)}
    # ★ 2026-10-20：**已經是自己在這張桌的座次 ⇒ 直接成功**（重進桌／重送請求不該被拒）。
    #   以前一律走「可用清單」比對，而那份清單把**已佔**（含自己）都排除 ⇒ 自己坐著的席
    #   反而回「這一局沒有可選的 X」，換桌重進／重連就卡死。
    _mine_seat = next((str(s) for s, b in (acct.seats or {}).items()
                       if str((b or {}).get("account") or "") == str(who.get("account") or "")
                       and str((b or {}).get("char") or "") == char), "")
    if _mine_seat:
        return {"ok": True, "account": who.get("account"), "charId": char, "seat": _mine_seat,
                "already": True}
    avail = _chars_available(svc, acct)
    hit = next((x for x in avail if x["char"] == char), None)
    if hit is None:
        return {"ok": False, "error": f"這一局沒有可選的 {char!r}（可能已被佔或包沒排）",
                "chars_available": avail}
    seat = hit["seat"]
    _busy = _busy_tables(svc, who["account"], char)
    if _busy:
        return {"ok": False, "error": _busy_msg(char, _busy), "busy_in": _busy,
                "chars_available": avail}
    # ⚠ **不要包 `acct._lock()`**：`assign_seat()` 自己就取鎖，而 JSON 後端的鎖是 `fcntl.flock`
    #   （同行程再開一個 fd 也會擋）⇒ 巢狀取鎖＝死鎖（2026-10-07 在 `createChar` 上實測踩到，
    #   這裡是同一種寫法，順手修）。DB 後端不受影響。
    acct.assign_seat(seat, who["account"], char)      # 桌級座次：寫進 store
    return {"ok": True, "account": who["account"], "char": char, "seat": seat}


def _busy_tables(svc: Any, account: str, char: str, *, prober: Any = None) -> List[Dict[str, Any]]:
    """這個 **account×char 還在哪些「活桌」出場**（線上且未收局）——設計者 2026-10-07 定案：

        「**一個帳號的一個角色不能同時參加兩張桌子**」（座位號由劇本定死，一個角色一份實例）

    判準＝**別的桌開著且還沒收局**才擋（離線／已收局 ⇒ 那個角色是自由的，可以帶去別的桌）。
    ⚠ 兩張桌共用同一份 registry（`auth.accounts_file`）時才看得到彼此的座次；各自一份的桌看不到
      別桌 ⇒ 這條規則在那種桌上是 no-op（不會誤擋）。
    """
    if not account or not char:
        return []
    acct = _accounts_of(svc)
    me = str(svc.cfg.get("table_id") or "")
    # ── **同進程（hub）**：不必探 HTTP——別的桌就在**這個進程裡**，直接問它們（更準、更快；也免了
    #    「桌退到 loopback 之後還要探哪個埠」的問題）。單桌模式（沒有 hub）才走下面的 HTTP 探活。
    if prober is None and os.environ.get("TRPG_HUB_ROOT"):
        try:
            import hub_server                                                # 同進程才 import 得到
            out0: List[Dict[str, Any]] = []
            for tid2, svc2 in list(hub_server.Hub.services.items()):
                if tid2 == me:
                    continue
                other = acct.peer(svc2.home, tid2)          # 同一個後端（DB 或 JSON）的另一桌
                if not any((b or {}).get("account") == account and (b or {}).get("char") == char
                           for b in (other.seats or {}).values()):
                    continue
                try:
                    ops = svc2.table.adapter_view()
                except Exception:                                        # noqa: BLE001
                    continue
                if not ops.get("ended"):
                    out0.append({"id": tid2, "scenario": str(svc2.cfg.get("scenario") or ""),
                                 "port": svc2.cfg.get("port"),
                                 "events": len(svc2.table.events), "inproc": True})
            return out0
        except Exception:                                                    # noqa: BLE001
            pass                                    # hub 不在／匯入失敗 ⇒ 退回 HTTP 探活
    root = tdir.repo_root_of(svc.table.home)
    probe = prober or tdir.probe
    out: List[Dict[str, Any]] = []
    for t in tdir.discover(root):
        if t.get("config_error") or t.get("id") == me:
            continue
        other = acct.peer(svc.table.home, t["id"])      # 同一個後端（DB 或 JSON）的另一桌
        if not any((b or {}).get("account") == account and (b or {}).get("char") == char
                   for b in (other.seats or {}).values()):
            continue
        info = probe(t)
        if info.get("online") and not info.get("ended"):
            out.append({"id": t["id"], "scenario": t.get("scenario") or "",
                        "port": t.get("port"), "events": info.get("events")})
    return out


def _busy_msg(char: str, busy: List[Dict[str, Any]]) -> str:
    where = "、".join(f'「{(b.get("scenario") or b["id"]).split("──")[0].strip() or b["id"]}」'
                      f'（{b["id"]}，事件 {b.get("events")}）' for b in busy)
    return (f"你的角色 {char} 還在 {where} 出場中——"
            f"**同一個帳號的同一個角色不能同時參加兩張桌**；先等那張桌收局（或退席）再來。")


def _accounts_of(svc: Any) -> Any:
    """伺服器層的 account store（**引擎不認識 account**）——懶載入、沒有 store＝空的。

    後端由 `account_store.accounts_for` 挑：`config.auth.db`（或 `db_env`）有設 ⇒ **DB**；
    沒設 ⇒ `config.auth.accounts_file`（**多張桌共用的那一份**）＞ `<home>/accounts.json`
    （一桌一份的舊寫法，legacy 一字不變）。
    `table` 傳這一桌的 id ⇒ 座次（`sessions[<table>]`／`seat_assignment.module`）只認自己這一桌的。
    """
    acc = getattr(svc, "_accounts_cache", None)
    if acc is None:
        home = (getattr(svc, "home", None)
                or getattr(getattr(svc, "table", None), "home", None) or ".")
        cfg = getattr(svc, "cfg", None) or {}
        table = str(cfg.get("table_id") or os.path.basename(str(home).rstrip("/")))
        # ★ P2：帶上這一局的 run_id（座次走 `run_seat`）與真模組名（角色實例／錢包的維度）
        try:
            _rid = str(svc.table.session_id() or "")
        except Exception:                                                       # noqa: BLE001
            _rid = ""
        acc = account_store.accounts_for(home, table, cfg, run_id=_rid)
        svc._accounts_cache = acc
    return acc


if TYPE_CHECKING:                       # 只給型別註解；執行期不 import（免循環）
    from service import TableService

HERE = os.path.dirname(os.path.abspath(__file__))


def _range_of(rng: str, total: int) -> Any:
    """`Range: bytes=…` ⇒ `(start, end)`（含頭尾）／`None`（不認得的寫法，當整份送）／`"bad"`（416）。

    只給音訊用（圖不需要 seek）。手機 Safari 對**沒有 206／Accept-Ranges 的音檔**會直接不播，
    所以這一小段是「音訊在手機上真的會響」的前提，不是優化。
    """
    m = re.match(r"^bytes=(\d*)-(\d*)$", str(rng or "").strip())
    if not m:
        return None
    first, last = m.group(1), m.group(2)
    if not first and not last:
        return None
    if total <= 0:
        return "bad"
    if not first:                                  # `bytes=-500`＝最後 500 bytes
        n = int(last)
        if n <= 0:
            return "bad"
        return (max(0, total - n), total - 1)
    start = int(first)
    end = int(last) if last else total - 1
    if start >= total or end < start:
        return "bad"
    return (start, min(end, total - 1))


def asset_response(root: str, path: str, headers: Any = None) -> tuple:
    """`GET /assets/<模組>/<相對路徑>` ⇒ `(code, body, ctype, extra_headers)`。

    **模組級靜態面**（2026-10-09）：只送 `<repo>/modules/<模組>/assets/` 底下的圖
    （副檔名白名單、路徑不得穿越、realpath 必須落在該模組的 assets 內）。

    三條刻意的設計：
    * **不認任何身分**——觀戰頁（`/<桌>/watch/`）是匿名的，它也要看得到圖；
      素材本來就是各桌共用的一份（同模組）。
    * **不進 `/api/*` 退役閘**——這條路徑裡沒有 `/api/`，天然不衝突；放在 `do_GET` 最前面。
    * **可以長快取**：`Cache-Control: public, max-age=86400` ＋ `ETag`／`Last-Modified`＋304。
      頁面本身仍然 `no-store`（頁面「上線即換」；圖片是「換檔才變」，沒快取會被輪詢拖爆）。

    找不到（模組不存在／沒這個檔／路徑不合規）⇒ 404 JSON，形狀與其他面一致。
    """
    p = str(path or "").split("?", 1)[0]
    _miss = (404, json.dumps({"error": "沒有這個素材"}, ensure_ascii=False).encode("utf-8"),
             "application/json", {"Cache-Control": "no-store"})
    if not p.startswith(assets.URL_PREFIX + "/"):
        return _miss
    rest = p[len(assets.URL_PREFIX) + 1:]
    if "/" not in rest:
        return _miss
    module, rel = rest.split("/", 1)
    full = assets.resolve_file(root, module, rel)
    if not full:
        return _miss
    try:
        st = os.stat(full)
        etag = f'"{int(st.st_mtime)}-{int(st.st_size)}"'
        inm = str((headers or {}).get("If-None-Match") or "")
        # 2026-10-19：**清單類 JSON（index.json／index/<類>.json）與 locale.json 不能長快取**——
        # 它們是「指標」，改一次就整批換（圖才是長快取：`/assets/<模組>/index.json` 改了、圖不必換）。
        # 以前一律 86400 ⇒ 改了 index.json 瀏覽器還拿舊的（拆分後更明顯：manifest 舊＝看不到新圖）。
        # 用 `no-cache`＋ETag：還是會 304，只是每次先問一句。
        _is_manifest = full.lower().endswith(".json")
        _cache = ("no-cache, must-revalidate" if _is_manifest
                  else "public, max-age=86400")
        if inm and inm == etag:
            return (304, b"", assets.mime_of(full),
                    {"Cache-Control": _cache, "ETag": etag})
        with open(full, "rb") as f:
            body = f.read()
    except OSError:
        return _miss
    _extra = {"Cache-Control": _cache, "ETag": etag,
              "Last-Modified": email.utils.formatdate(st.st_mtime, usegmt=True)}
    # ★ 音訊（2026-10-20）：支援 `Range` ⇒ 206（手機 Safari 沒這個就不播／不能 seek）。
    #   圖不走這條（沒有 `Range` 就是 200 整份，行為與以前一字不變）。
    if os.path.splitext(full)[1].lower() in assets.AUDIO_EXT:
        _extra["Accept-Ranges"] = "bytes"
        _rng = str((headers or {}).get("Range") or "").strip()
        if _rng:
            _span = _range_of(_rng, len(body))
            if _span == "bad":
                return (416, b"", assets.mime_of(full),
                        {**_extra, "Content-Range": f"bytes */{len(body)}"})
            if _span:
                _s, _e = _span
                return (206, body[_s:_e + 1], assets.mime_of(full),
                        {**_extra, "Content-Range": f"bytes {_s}-{_e}/{len(body)}"})
    return (200, body, assets.mime_of(full), _extra)


class HandlerBaseMixin:
    def _send(self, code: int, body: bytes = b"", ctype: str = "application/json",
              extra: Optional[Dict[str, str]] = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        _extra = dict(extra or {})
        for k, v in _extra.items():
            self.send_header(k, v)
        # ★ 素材（2026-10-09）：**自己指定了 `Cache-Control` 的回應，`end_headers()` 不要再補 no-store**
        #   ——圖片要長快取（`public, max-age=86400`）。沒指定的一律照舊 no-store（頁面／API／錯誤）。
        self._cache_set = any(k.lower() == "cache-control" for k in _extra)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, code: int, obj: Any, extra: Optional[Dict[str, str]] = None) -> None:
        # ⚠ 錯誤回應**不准快取**（2026-10-08 實測踩到：`/api/*` 退役期回過的 410 被瀏覽器快取住，
        #   之後帶 `TRPG_LEGACY_REST=1` 重啟、curl 都 200，**頁面還是拿著快取的 410**⇒投影一片空白）。
        if code >= 400:
            extra = dict(extra or {})
            extra.setdefault("Cache-Control", "no-store")
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json", extra)

    def _body(self) -> Dict[str, Any]:
        # ★ hub 的 `/mcp` 前門**已經把 body 讀走了**（它得先看 `session`／`table` 才知道是哪張桌，
        #   見 `hub_server.Handler._hub_mcp`）——這裡直接用它讀好的那一份，不要再讀一次
        #   （`rfile` 已消費完 ⇒ 再讀會**卡住等 Content-Length 那麼多 byte**，實測＝請求逾時）。
        pre = getattr(self, "_prebody", None)
        if isinstance(pre, dict):
            return dict(pre)
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    def log_message(self, fmt: str, *args: Any) -> None:
        """访问日志（默认开，写 stderr→logs/table.log）。

        排查「玩家进不来」时这条是决定性的：能看出请求到底**有没有到达**这台机器。
        轮询接口 /api/state 每 1.5 秒一次，太吵 → 单独跳过。
        """
        msg = fmt % args
        if "/api/state" in msg or "/api/log" in msg:
            return
        if os.environ.get("TRPG_HTTP_LOG", "1") == "1":
            sys.stderr.write("%s %s - %s\n" % (time.strftime("%m-%d %H:%M:%S"), self.address_string(), msg))

    # -- 路由 ---------------------------------------------------------------

    def end_headers(self) -> None:  # noqa: N802
        """所有响应都不许缓存**——除了自己指定了 Cache-Control 的那几种**（素材图）。

        踩过的坑：没有 Cache-Control 时浏览器会启发式缓存，改版/重启后玩家可能拿到
        **旧页面甚至写文件写了一半的页面** → 页面半死、看起来「进不去」。
        ★ 2026-10-09：素材图（`/assets/...`）刻意走长快取 ⇒ `_send()` 已经送了
        `Cache-Control` 时**不再补 no-store**（否则图片每次轮询都重抓，整面图墙拖爆手机）。
        """
        if not getattr(self, "_nocache_sent", False):
            self._nocache_sent = True
            if not getattr(self, "_cache_set", False):
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                self.send_header("Pragma", "no-cache")
        super().end_headers()

    # 觀戰白名單（同 `watch_server.py` 的唯讀面）：只有這幾條；其餘路徑與**所有寫入**一律 404
    # ★ 2026-10-19：頁面（`/`、`/index.html`）不在這裡了——後台不送網頁，觀戰只給投影。
    WATCH_GET = ("/api/state", "/api/log", "/api/map", "/health")

