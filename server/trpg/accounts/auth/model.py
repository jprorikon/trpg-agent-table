"""model（自 __init__.py 拆出）"""

from __future__ import annotations
import contextlib
import hashlib
import json
import os
import secrets
import time
from typing import Any, Dict, Iterator, List, Optional

from .util import AccountsError, _hash, registry_path

try:                                    # 共用 registry 的讀-改-寫要跨行程互斥（兩張桌＝兩個行程）
    import fcntl as _fcntl
except ImportError:                                                     # noqa: BLE001
    _fcntl = None                       # 非 POSIX：退化成「沒有鎖」（單機單桌才安全）

class Accounts:
    """account registry 的唯讀視圖＋實例寫回（**一台機器一份或一桌一份都行**）。

    兩個層次（2026-10-07 第二階段）：
      * **人級**：`accounts.<id>`——display／kind／chars（跨局成長）／wallet（貨幣）／
        login（帳號密碼）／token。**跨桌唯一**（同一把 token 在每張桌都認得）。
      * **桌級**：`sessions.<table>.seats`——他在**這張桌**坐哪一席、綁哪個 char、
        `written_back`／`wallet_carried` 這些「這一局寫過沒」。**別的桌看不到**。
    舊形狀（頂層 `session`）在載入時自動視為「這個檔所屬那張桌的 session」⇒ 舊桌不必先遷移。
    """

    def __init__(self, home: str, table: Optional[str] = None, *,
                 path: Optional[str] = None, cfg: Optional[Dict[str, Any]] = None) -> None:
        self.home = home
        self.table = table or os.path.basename(home.rstrip("/"))
        self.path = path or registry_path(home, cfg)
        self.data: Dict[str, Any] = {}
        self._mtime: Optional[float] = None
        self.load()

    def load(self) -> None:
        if not os.path.exists(self.path):
            self.data = {}
            self._mtime = None
            return
        with open(self.path, encoding="utf-8") as fh:
            self.data = json.load(fh)
        try:
            self._mtime = os.path.getmtime(self.path)
        except OSError:                                                 # noqa: BLE001
            self._mtime = None
        if not isinstance(self.data.get("accounts", {}), dict):
            raise AccountsError(f"{self.path}: accounts 要是物件")

    def save(self) -> None:
        """把記憶體裡的 registry 寫回檔（只給 account 層自己用；不碰遊戲狀態）。"""
        if not self.data:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)
        os.chmod(self.path, 0o600)
        try:
            self._mtime = os.path.getmtime(self.path)
        except OSError:                                                 # noqa: BLE001
            pass

    def refresh(self) -> None:
        """registry 檔改了就重讀——**換發憑證／改綁定不必重啟桌子**（只比 mtime，很便宜）。"""
        try:
            mt = os.path.getmtime(self.path) if os.path.exists(self.path) else None
        except OSError:                                                 # noqa: BLE001
            return
        if mt != self._mtime:
            self.load()

    @property
    def enabled(self) -> bool:
        return bool(self.data.get("accounts"))

    # ------------------------------------------------- 桌級：這一桌的座次（sessions）
    @contextlib.contextmanager
    def _lock(self) -> Iterator[None]:
        """跨行程互斥（兩張桌共用一份 registry ⇒ 讀-改-寫會互相蓋掉，實測過）。

        進鎖前**重讀一次**（別的桌可能剛寫過），出鎖後由呼叫端自己 `save()`。
        非 POSIX（沒有 fcntl）⇒ 不鎖（單機單桌時本來就沒有競爭）。
        """
        if _fcntl is None:
            self.load()
            yield
            return
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path + ".lock", "a+", encoding="utf-8") as fh:
            _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX)
            try:
                if os.path.exists(self.path):
                    self.load()
                yield
            finally:
                _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)

    def _sessions(self) -> Dict[str, Any]:
        """`sessions{<桌>: {...}}`；**舊形狀頂層 `session` 借住進來**（不刪原欄位，唯讀相容）。

        ⚠ 舊檔那個 `session` 就是**這個檔所屬那張桌**的 ⇒ 一律認給 `self.table`
        （不是照抄檔裡記的 `table`：測試／臨時桌的目錄名常與 table id 不同，照抄會對不上座次）。
        """
        raw = self.data.get("sessions")
        if not isinstance(raw, dict):
            raw = {}
            self.data["sessions"] = raw
        legacy = self.data.get("session")
        if isinstance(legacy, dict) and legacy:
            raw.setdefault(self.table, legacy)
        return raw

    def sess(self) -> Dict[str, Any]:
        """**這一桌**的 session 格（`seats`／`written_back`／`wallet_carried` 都住這裡）。"""
        s = self._sessions().get(self.table)
        if not isinstance(s, dict):
            s = {"table": self.table, "seats": {}}
            self._sessions()[self.table] = s
        s.setdefault("table", self.table)
        if not isinstance(s.get("seats"), dict):
            s["seats"] = {}
        return s

    @property
    def seats(self) -> Dict[str, Any]:
        """這一桌的 `seat -> {account, char, …}`。"""
        return self.sess()["seats"]

    def tables_of(self, account: str) -> List[str]:
        """這個人在**哪幾張桌**有座位（運維／verify 用）。"""
        out = []
        for tbl, s in (self._sessions() or {}).items():
            if any((b or {}).get("account") == account for b in ((s or {}).get("seats") or {}).values()):
                out.append(tbl)
        return sorted(out)

    # ------------------------------------------------------------------ 認證
    def resolve(self, token: Optional[str]) -> Optional[Dict[str, Any]]:
        """token ⇒ `{account, display, kind, seats, chars}`；不認得＝None。"""
        self.refresh()
        tok = (token or "").strip()
        if not tok or not self.enabled:
            return None
        h = _hash(tok)
        for aid, a in (self.data.get("accounts") or {}).items():
            if a.get("token_sha256") and secrets.compare_digest(str(a["token_sha256"]), h):
                return self.who(aid)
            # ★ 人類用「帳號＋密碼」登入時發的那把（`sessions`），與帳號 token 同效
            for sh in (a.get("sessions") or {}):
                if secrets.compare_digest(str(sh), h):
                    return self.who(aid)
        return None

    def who(self, account: str) -> Dict[str, Any]:
        """這個 account 在**這一桌**的身分組裝（席位／角色／顯示名）；人級資料照舊。"""
        a = (self.data.get("accounts") or {}).get(account) or {}
        seats = sorted(s for s, b in self.seats.items() if (b or {}).get("account") == account)
        return {"account": account, "display": a.get("display"), "kind": a.get("kind"),
                "seats": seats, "chars": sorted(a.get("chars") or {}),
                "tables": self.tables_of(account),
                "seat": (seats[0] if len(seats) == 1 else None)}

    # --------------------------------------------------- 人類入口：帳號＋密碼
    # 設計者 2026-10-07：「人類用帳號和密碼…兩段帳號密碼對應到一個 token」
    # ⇒ login(username, password) 成功時**發一把新的 session token**（registry 仍只存它的 sha256），
    #   之後它與帳號 token 同效（同一條 `resolve`）。密碼也只存 sha256（加 `pw:` 前綴避免與 token 撞）。
    def set_login(self, account: str, username: str, password: str) -> None:
        if not self.enabled:
            return
        with self._lock():
            a = (self.data.get("accounts") or {}).get(account)
            if a is None:
                return
            a["login"] = {"username": str(username).strip(),
                          "password_sha256": _hash("pw:" + str(password))}
            self.save()

    def login(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        """帳號＋密碼 ⇒ 認出 account 並發一把 session token（回傳含 `token` 的 dict；失敗＝None）。

        寫入走 `_lock()`（共用 registry 時別的桌可能同時在寫）；比對先做、拿鎖才寫。
        """
        self.refresh()
        u = str(username or "").strip().lower()
        p = str(password or "")
        if not u or not p or not self.enabled:
            return None
        aid = None
        for k, a in (self.data.get("accounts") or {}).items():
            lg = a.get("login") or {}
            if not lg.get("username") or not lg.get("password_sha256"):
                continue
            if str(lg["username"]).strip().lower() != u:
                continue
            if not secrets.compare_digest(str(lg["password_sha256"]), _hash("pw:" + p)):
                return None
            aid = k
            break
        if aid is None:
            return None
        sess = "trpg_sess_" + secrets.token_hex(16)
        with self._lock():
            a2 = (self.data.get("accounts") or {}).get(aid)
            if a2 is None:
                return None
            a2.setdefault("sessions", {})[_hash(sess)] = {
                "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "label": "web"}
            self.save()
        who = self.who(aid)
        who["token"] = sess
        who["instance"] = self.instance(aid, self.char_of(who.get("seat") or "") or "")
        who["wallet"] = self.wallet(aid)
        return who

    def seat_of(self, account: str) -> Optional[str]:
        """他在**這一桌**的席位（唯一時才回；沒配席＝None＝在這張桌只是觀眾）。"""
        seats = sorted(s for s, b in self.seats.items() if (b or {}).get("account") == account)
        return seats[0] if len(seats) == 1 else None

    # ------------------------------------------------------- 喚醒端點（按帳號）
    # 2026-10-20（設計者定案）：「每個 agent 端末（端末-角色區分）有一個帳號，這個帳號全桌可用，
    #   帳號綁定一個 wake url。」⇒ kick 只踢「這一局、這個席位真正的參加帳號」；
    #   席位寫死的 URL 正是「踢到沒參加的人」的來源（見 docs/PLAN_wake_by_account.md）。
    def account_of_seat(self, seat: str) -> Optional[str]:
        """這個席位現在坐的是哪個 account（kick 的唯一依據；沒綁＝None）。"""
        b = (self.seats or {}).get(seat) or {}
        return str(b.get("account") or "") or None

    def wake_of(self, account: str) -> Dict[str, Any]:
        """這個帳號的喚醒端點（全桌共用）：`{url, token, token_file}`；沒有＝空字串。"""
        a = (self.data.get("accounts") or {}).get(account) or {}
        return {"url": str(a.get("wake_url") or ""),
                "token": str(a.get("wake_token") or ""),
                "token_file": str(a.get("wake_token_file") or "")}

    def set_wake(self, account: str, url: str, token_file: str = "",
                 token: str = "") -> Dict[str, Any]:
        """設／改這個帳號的喚醒端點（遷移與維運用；不動遊戲狀態）。"""
        if not self.enabled:
            return {}
        with self._lock():
            a = (self.data.get("accounts") or {}).get(account)
            if a is None:
                return {}
            a["wake_url"] = str(url or "")
            if token_file:
                a["wake_token_file"] = str(token_file)
            if token:
                a["wake_token"] = str(token)
            self.save()
        return self.wake_of(account)

    def char_of(self, seat: str) -> Optional[str]:
        """這一桌的 `seat -> char`（由**這張桌**的 seats.json 決定）。"""
        return (self.seats.get(seat) or {}).get("char")

    # ------------------------------------------------------- 跨局實例（成長）
    def instance(self, account: str, char: str) -> Dict[str, Any]:
        """這個 account 在這個 char 上的實例（沒有＝空 dict）。"""
        self.refresh()
        return dict(((self.data.get("accounts") or {}).get(account, {}).get("chars") or {}).get(char) or {})

    def write_instance(self, account: str, char: str, growth: Dict[str, Any]) -> None:
        """把成長寫回（只寫成長；貨幣／道具／記憶是**局內資源**，不帶走）。"""
        if not self.enabled:
            return
        with self._lock():
            a = (self.data.get("accounts") or {}).get(account)
            if not a:
                return
            # ⚠ **不要寫成 `(a.setdefault("chars", {}) or {}).setdefault(...)`**：
            #   `chars` 是空 dict 時是 falsy，`or {}` 會把它丟掉、改在一個**臨時 dict** 上寫
            #   ⇒ 「這個帳號第一次寫角色實例」會被**靜默丟棄**（實測踩到：createChar 回 ok 但
            #   實例沒落地）。`setdefault` 本身就保證回同一個 dict，不需要 `or {}`。
            inst = a.setdefault("chars", {}).setdefault(char, {})
            # 局外只留**成長**（level／exp）；`star`（★）與局內資源（道具／記憶）不寫進來。
            # 貨幣是**通用貨幣** ⇒ 放 account 層的錢包（見 `wallet`／`add_wallet`）。
            inst.update({k: v for k, v in growth.items() if k in ("level", "exp", "note")})
            self.save()

    # --------------------------------------------------------- 錢包（通用貨幣）
    # 設計者 2026-10-07：「货币算是通用货币，可以放到局外」⇒ 貨幣不放 char 實例，放 **account 層的錢包**
    # （同一個人的不同角色共用同一筆貨幣——這是「通用」的意思）。
    #
    # ⚠ 2026-10-07 模型收斂（說法 2）：**DB 後端**（`accounts_db.DbAccounts`）把錢包下沉一層成
    #   `(account, module)`——「模組內通用」。JSON 後端沒有模組維（一桌一檔）⇒ `module` 傳進來也
    #   只有一個模組，行為與舊版一字不變（legacy 相容）。
    def wallet(self, account: str, module: Optional[str] = None) -> Dict[str, Any]:
        self.refresh()
        return dict(((self.data.get("accounts") or {}).get(account, {}).get("wallet")) or {})

    def add_wallet(self, account: str, *, star_coins: int, module: Optional[str] = None) -> Dict[str, Any]:
        """把貨幣增減寫進錢包（收局時用）。回新的錢包。"""
        self.refresh()
        if not self.enabled:
            return {}
        with self._lock():
            a = (self.data.get("accounts") or {}).get(account)
            if not a:
                return {}
            w = a.setdefault("wallet", {})
            w["star_coins"] = int(w.get("star_coins", 0)) + int(star_coins)
            self.save()
            return dict(w)

    # ------------------------------------------- 副本（run）／座次：**後端統一介面**
    # 這一組是「DB 後端也有一模一樣的方法」的統一介面（`accounts_db.DbAccounts`）：
    # 消費端（`web_api`）只呼叫這些名字，不自己摸 `sessions`／`chars` 的形狀。
    # JSON 版沒有真正的「副本」維度（一桌＝一局）⇒ `run_*` 就落回**這一桌的 `written_back`**
    # （舊行為、一字不變）；DB 版才是真的 `run` 表（說法 4）。
    backend = "json"

    def peer(self, home: str, table: Optional[str] = None) -> "Accounts":
        """別張桌的同一個後端（互斥檢查用）——共用同一份 registry 檔。"""
        return Accounts(home, table, path=self.path)

    def assign_seat(self, seat: str, account: str, char: str) -> None:
        """座次指派（`account ↔ char` 由玩家選、`char ↔ seat` 由劇本包決定）。"""
        with self._lock():
            self.seats[seat] = {"account": account, "char": char}
            self.save()

    def seat_record(self, seat: str) -> Dict[str, Any]:
        return dict(self.seats.get(seat) or {})

    def set_seat_field(self, seat: str, key: str, value: Any) -> None:
        with self._lock():
            self.seats.setdefault(seat, {})[str(key)] = value
            self.save()

    def seats_with(self, account: str, char: str) -> List[Dict[str, Any]]:
        """這個 (account, char) 在哪些桌有座次（跨桌互斥檢查用）。"""
        out: List[Dict[str, Any]] = []
        for tbl, s in (self._sessions() or {}).items():
            for seat, b in ((s or {}).get("seats") or {}).items():
                if (b or {}).get("account") == account and (b or {}).get("char") == char:
                    out.append({"module": tbl, "seat": seat})
        return sorted(out, key=lambda x: (x["module"], x["seat"]))

    def start_run(self, run_id: str, module: Optional[str] = None) -> None:
        return None                       # JSON 版沒有副本表

    def join_run(self, account: str, char: str, run_id: str, seat: str,
                 module: Optional[str] = None) -> None:
        return None

    def finish_run(self, run_id: str, snapshots: Optional[Dict[str, Any]] = None,
                   module: Optional[str] = None) -> None:
        return None

    def run_written_back(self, run_id: str, module: Optional[str] = None) -> bool:
        return bool(self.sess().get("written_back"))

    def mark_run_written_back(self, run_id: str, module: Optional[str] = None) -> None:
        self.sess()["written_back"] = True
        self.save()

    def coins_written(self, account: str, char: str, run_id: str, seat: Optional[str] = None,
                      module: Optional[str] = None) -> Optional[int]:
        """JSON 版沒有副本維 ⇒ 記在**座次**上（舊行為、一字不變）。"""
        v = (self.seats.get(seat or "") or {}).get("coins_written")
        return int(v) if v is not None else None

    def set_coins_written(self, account: str, char: str, run_id: str, amount: int,
                          seat: Optional[str] = None, module: Optional[str] = None) -> None:
        self.set_seat_field(seat or "", "coins_written", int(amount))

    # ------------------------------------------------------------------ 對外
    def snapshot(self) -> List[Dict[str, Any]]:
        """給 GM／運維看的一張表（**不含 token**）——**這張桌**的席位 × 人。"""
        out = []
        for seat, b in sorted(self.seats.items()):
            aid = (b or {}).get("account")
            a = (self.data.get("accounts") or {}).get(aid) or {}
            out.append({"seat": seat, "account": aid, "display": a.get("display"),
                        "kind": a.get("kind"), "char": (b or {}).get("char"),
                        "tables": self.tables_of(aid) if aid else [],
                        "wallet": self.wallet(aid) if aid else {},
                        "instance": self.instance(aid, (b or {}).get("char") or "") if aid else {}})
        return out
