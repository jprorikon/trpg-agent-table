"""model（自 __init__.py 拆出）"""

from __future__ import annotations
import hashlib
import contextlib
import os
import secrets
import time
from typing import Any, Dict, Iterator, List, Optional
from trpg.core import db as db

from .util import _basename, _hash

class DbAccounts:
    """account 層（DB 後端）。介面與 `auth.Accounts` 對齊 ⇒ 可互換。"""

    backend = "db"

    def __init__(self, home: str, table: Optional[str] = None, *,
                 conf: Optional[Dict[str, Any]] = None, schema: str = db.SCHEMA,
                 cfg: Optional[Dict[str, Any]] = None, path: Optional[str] = None,
                 module: Optional[str] = None, run_id: str = "") -> None:
        self.home = home
        # ★ 2026-10-20（P2）：`table` ＝ **副本 id**（座次／局內紀錄的維度）；
        #   `module` ＝ **真模組名**（角色實例／錢包的維度）。以前兩者混用 ⇒ 每桌一套帳號層。
        self.table = table or _basename(home)
        self.cfg = cfg or {}
        self.run_id = str(run_id or "")
        self.conf: Dict[str, Any] = conf or db.configure(cfg) or {}
        self.schema = schema
        self.module = str(module or self._resolve_module())
        self.scenario = self._resolve_scenario()
        self.path = f"postgres://{self.conf.get('host')}:{self.conf.get('port')}/{self.conf.get('database')}" \
            if self.conf else ""
        if not self.conf:
            raise db.DbError("沒有 DB 連線設定（config.auth.db／db_env）")

    def _resolve_module(self) -> str:
        """權威模組名：`cfg.module` → `layout.where(home)` → 最後才退回 table id（並可被上層察覺）。"""
        m = str((self.cfg or {}).get("module") or "").strip()
        if m:
            return m
        try:
            from trpg.core import layout                                        # noqa: PLC0415
            # 只有「分層路徑」（state/tables/<模組>/<劇本>/<桌>）才去推模組名；
            # 臨時桌／測試桌（tmp 目錄）推不出來 ⇒ 用 table 當維度（與舊行為一致）。
            if getattr(layout, "_split_state_home", None) and \
                    layout._split_state_home(os.path.abspath(self.home)) is None:
                return self.table
            root = layout.repo_root(self.home)
            m = str((layout.where(self.home, root) or {}).get("module") or "").strip()
            if m:
                return m
        except Exception:                                                       # noqa: BLE001
            pass
        return self.table

    def _resolve_scenario(self) -> str:
        """劇本名（副本要知道自己是哪個模組的哪個劇本）：`cfg.scenario` → `layout.where(home)` → 空。"""
        s = str((self.cfg or {}).get("scenario") or "").strip()
        if s:
            return s
        try:
            from trpg.core import layout                                        # noqa: PLC0415
            root = layout.repo_root(self.home)
            return str((layout.where(self.home, root) or {}).get("scenario") or "").strip()
        except Exception:                                                       # noqa: BLE001
            return ""

    # ------------------------------------------------------------------ 連線
    @contextlib.contextmanager
    def _cursor(self, *, commit: bool = False) -> Iterator[Any]:
        """一條連線＋一個 cursor；`commit=True` 才提交（讀取不提交、不留交易）。"""
        conn = db.connect(self.conf, schema=self.schema)
        try:
            cur = conn.cursor()
            yield cur
            if commit:
                conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:                                                # noqa: BLE001
                pass
            raise
        finally:
            try:
                conn.close()
            except Exception:                                                # noqa: BLE001
                pass

    @contextlib.contextmanager
    def _lock(self) -> Iterator[None]:
        """介面相容用（JSON 版靠檔案鎖；DB 版交易本身就是鎖）——什麼都不做。"""
        yield

    def save(self) -> None:
        """介面相容用：DB 版是即時寫入，沒有「存檔」這個動作。"""
        return None

    def refresh(self) -> None:
        return None

    def peer(self, home: str, table: Optional[str] = None) -> "DbAccounts":
        """別張桌的同一個後端（互斥檢查用）——同一個 DB／schema，只是換副本。"""
        return DbAccounts(home, table, conf=self.conf, schema=self.schema, cfg=self.cfg,
                          module=self.module)

    # ------------------------------------------------------------------ 基本
    @property
    def enabled(self) -> bool:
        with self._cursor() as cur:
            cur.execute("SELECT 1 FROM account LIMIT 1")
            return cur.fetchone() is not None

    def _module(self, module: Optional[str]) -> str:
        """**模組維度**（角色實例／錢包／副本的 module 欄）：真模組名，不是 table id。"""
        return str(module or self.module)

    def _table(self) -> str:
        """**副本維度**（座次／局內紀錄）：這一張桌的 id。"""
        return self.table

    def resolve(self, token: Optional[str]) -> Optional[Dict[str, Any]]:
        tok = (token or "").strip()
        if not tok:
            return None
        h = _hash(tok)
        with self._cursor() as cur:
            cur.execute("SELECT id FROM account WHERE token_sha256 = %s", (h,))
            row = cur.fetchone()
            if row is None:
                cur.execute("SELECT account FROM login_session WHERE token_sha256 = %s", (h,))
                row = cur.fetchone()
        return self.who(row[0]) if row else None

    def who(self, account: str) -> Dict[str, Any]:
        """這個 account 在**這一桌（模組）**的身分組裝；人級資料照舊。

        `chars`＝**這個模組**的角色（模型是 account×module×char）；`chars_all`＝全部模組的。
        """
        with self._cursor() as cur:
            cur.execute("SELECT id, display, kind FROM account WHERE id = %s", (account,))
            a = cur.fetchone()
            cur.execute("SELECT seat, char FROM seat_assignment WHERE module = %s AND account = %s"
                        " ORDER BY seat", (self.table, account))
            rows = cur.fetchall() or []
            # 角色實例是**模組維度**（同模組跨劇本共用），不是這一張桌
            cur.execute("SELECT char FROM char_instance WHERE module = %s AND account = %s"
                        " ORDER BY char", (self.module, account))
            chars = [r[0] for r in (cur.fetchall() or [])]
            cur.execute("SELECT DISTINCT module FROM char_instance WHERE account = %s", (account,))
            mods = [r[0] for r in (cur.fetchall() or [])]
        seats = [r[0] for r in rows]
        ch_by_seat = {r[0]: r[1] for r in rows}
        return {"account": account,
                "display": (a[1] if a else None), "kind": (a[2] if a else None),
                "seats": seats, "chars": chars, "chars_all": mods and chars or chars,
                "tables": self.tables_of(account),
                "seat": (seats[0] if len(seats) == 1 else None),
                "char": (ch_by_seat.get(seats[0]) if len(seats) == 1 else None)}

    # ------------------------------------------------------------- 桌級座次
    @property
    def seats(self) -> Dict[str, Any]:
        """這一局的 `seat -> {account, char, ...}`。

        ★ 2026-10-20（P2）：**有 `run_id` 就讀 `run_seat`**（＝這一局的物化座次，權威由
        「帳號選的角色 × 劇本 seats.json」推導）；沒有 `run_id`（舊呼叫端）才退回
        `seat_assignment`（相容視圖，同一份資料的舊位置）。

        ⚠ `wallet_carried` 已**不是**「帶錢包」的閘了：`run_seat` 這一格沒有欄位（歷史上寫死
        False，會讓 `/api/enter` 每次進場重帶）。現在的冪等鍵是**這一局的事件流**
        （`trpg/api/web/base.py › carry_over_wallet`），DB／JSON 後端同一語意；這裡的欄位
        只為舊工具／舊讀取保留。
        """
        if self.run_id:
            try:
                with self._cursor() as cur:
                    cur.execute("SELECT seat, account, char FROM run_seat"
                                " WHERE run_id = %s ORDER BY seat", (self.run_id,))
                    _rows = cur.fetchall() or []
                if _rows:
                    return {r[0]: {"account": r[1], "char": r[2],
                                   "wallet_carried": False, "coins_written": None}
                            for r in _rows}
            except Exception:                                                   # noqa: BLE001
                pass                        # schema 還沒遷移（run_seat 不存在）⇒ 退回相容表
        with self._cursor() as cur:
            cur.execute("SELECT seat, account, char, wallet_carried, coins_written"
                        " FROM seat_assignment WHERE module = %s ORDER BY seat", (self.table,))
            return {r[0]: {"account": r[1], "char": r[2],
                           "wallet_carried": r[3], "coins_written": r[4]}
                    for r in (cur.fetchall() or [])}

    def seat_record(self, seat: str) -> Dict[str, Any]:
        return dict(self.seats.get(seat) or {})

    # ------------------------------------------------------- 喚醒端點（按帳號）
    # 2026-10-20（設計者定案）：「每個 agent 端末（端末-角色區分）有一個帳號，這個帳號全桌可用，
    #   帳號綁定一個 wake url。」⇒ kick 只踢「這一局、這個席位真正的參加帳號」（見
    #   docs/PLAN_wake_by_account.md）。介面與 JSON 版逐字對齊。
    def account_of_seat(self, seat: str) -> Optional[str]:
        """這個席位**這一局**坐的是哪個 account（kick 的唯一依據；沒綁＝None）。

        ★ 2026-10-20（review P4）：有 `run_id` 時先讀 `run_seat`（這一局的座次）；沒有列
        （玩家還沒進這一局）才退回 `seat_assignment`（桌級相容視圖）。舊寫法只看
        `seat_assignment` ⇒ 換局後 kick 可能踢到**上一局**坐這一席的人。
        """
        if self.run_id:
            try:
                with self._cursor() as cur:
                    cur.execute("SELECT account FROM run_seat WHERE run_id = %s AND seat = %s",
                                (self.run_id, seat))
                    r = cur.fetchone()
                if r and r[0]:
                    return str(r[0])
            except Exception:                                                # noqa: BLE001
                pass                        # schema 還沒遷移（run_seat 不存在）⇒ 退回相容表
        with self._cursor() as cur:
            cur.execute("SELECT account FROM seat_assignment WHERE module = %s AND seat = %s",
                        (self.table, seat))
            r = cur.fetchone()
        return (str(r[0]) if r and r[0] else None)

    def wake_of(self, account: str) -> Dict[str, Any]:
        """這個帳號的喚醒端點（全桌共用）：`{url, token, token_file}`；沒有＝空字串。"""
        with self._cursor() as cur:
            cur.execute("SELECT wake_url, wake_token_file, wake_token FROM account WHERE id = %s",
                        (account,))
            r = cur.fetchone()
        return {"url": (str(r[0]) if r and r[0] else ""),
                "token": (str(r[2]) if r and r[2] else ""),
                "token_file": (str(r[1]) if r and r[1] else "")}

    def set_wake(self, account: str, url: str, token_file: str = "",
                 token: str = "") -> Dict[str, Any]:
        """設／改這個帳號的喚醒端點（遷移與維運用；不動遊戲狀態）。"""
        sets, args = ["wake_url = %s"], [str(url or "")]
        if token_file:
            sets.append("wake_token_file = %s")
            args.append(str(token_file))
        if token:
            sets.append("wake_token = %s")
            args.append(str(token))
        args.append(account)
        with self._cursor(commit=True) as cur:
            cur.execute(f"UPDATE account SET {', '.join(sets)} WHERE id = %s", tuple(args))
        return self.wake_of(account)

    def assign_seat(self, seat: str, account: str, char: str) -> None:
        """座次指派（**char↔seat 由劇本包決定**；這裡只寫「誰坐上去、演哪個角色」）。

        ★ 2026-10-20（P2）：`seat_assignment` 保留（相容，舊呼叫端／舊桌還讀它），
        但有 `run_id` 時**同步寫 `run_seat`**——那才是「這一局的座次」的正式位置。
        """
        with self._cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO seat_assignment (module, seat, account, char, updated_at)"
                " VALUES (%s, %s, %s, %s, now())"
                " ON CONFLICT (module, seat) DO UPDATE SET"
                "  account = EXCLUDED.account, char = EXCLUDED.char, updated_at = now()",
                (self.table, seat, account, char))
            if self.run_id:
                cur.execute(
                    "INSERT INTO run_seat (run_id, seat, module, account, char, updated_at)"
                    " VALUES (%s, %s, %s, %s, %s, now())"
                    " ON CONFLICT (run_id, seat) DO UPDATE SET"
                    "  account = EXCLUDED.account, char = EXCLUDED.char, updated_at = now()",
                    (self.run_id, seat, self.module, account, char))

    def set_seat_field(self, seat: str, key: str, value: Any) -> None:
        """`wallet_carried` / `coins_written` 這種記帳欄（白名單，別讓外部欄名進 SQL）。"""
        col = {"wallet_carried": "wallet_carried", "coins_written": "coins_written"}.get(key)
        if col is None:
            raise ValueError(f"不認得的座次欄位：{key}")
        with self._cursor(commit=True) as cur:
            cur.execute(f"UPDATE seat_assignment SET {col} = %s, updated_at = now()"
                        f" WHERE module = %s AND seat = %s", (value, self.table, seat))

    def seat_of(self, account: str) -> Optional[str]:
        seats = sorted(s for s, b in self.seats.items() if (b or {}).get("account") == account)
        return seats[0] if len(seats) == 1 else None

    def char_of(self, seat: str) -> Optional[str]:
        return (self.seats.get(seat) or {}).get("char")

    def tables_of(self, account: str) -> List[str]:
        """這個帳號待過哪些**副本**（run）：P2 起從 `run_seat`／`run_participation` 回推。

        以前是「`seat_assignment.module` 的 distinct 值」——那個欄位存的是 table id，
        但欄名叫 module，語意已經漂了。
        """
        with self._cursor() as cur:
            try:
                cur.execute("SELECT DISTINCT run_id FROM run_seat WHERE account = %s"
                            " ORDER BY run_id", (account,))
                out = [r[0] for r in (cur.fetchall() or [])]
            except Exception:                                                   # noqa: BLE001
                out = []                    # schema 還沒遷移（run_seat 不存在）⇒ 不擋身分組裝
            if not out:
                try:
                    cur.execute("SELECT DISTINCT run_id FROM run_participation WHERE account = %s"
                                " ORDER BY run_id", (account,))
                    out = [r[0] for r in (cur.fetchall() or [])]
                except Exception:                                               # noqa: BLE001
                    out = []
        return out

    def seats_with(self, account: str, char: str) -> List[Dict[str, Any]]:
        """這個 (account, char) 在**哪些模組**有座次（跨桌互斥檢查用）。"""
        with self._cursor() as cur:
            cur.execute("SELECT module, seat FROM seat_assignment WHERE account = %s AND char = %s"
                        " ORDER BY module", (account, char))
            return [{"module": r[0], "seat": r[1]} for r in (cur.fetchall() or [])]

    # --------------------------------------------------------------- 人級登入
    def set_login(self, account: str, username: str, password: str) -> None:
        with self._cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO account_login (account, username, password_sha256)"
                " VALUES (%s, %s, %s) ON CONFLICT (account, username) DO UPDATE SET"
                "  password_sha256 = EXCLUDED.password_sha256",
                (account, str(username).strip(), _hash("pw:" + str(password))))

    def login(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        u = str(username or "").strip().lower()
        p = str(password or "")
        if not u or not p:
            return None
        with self._cursor() as cur:
            cur.execute("SELECT account FROM account_login WHERE lower(username) = %s"
                        " AND password_sha256 = %s", (u, _hash("pw:" + p)))
            row = cur.fetchone()
        if row is None:
            return None
        aid = row[0]
        sess = "trpg_sess_" + secrets.token_hex(16)
        with self._cursor(commit=True) as cur:
            cur.execute("INSERT INTO login_session (token_sha256, account, label) VALUES (%s, %s, %s)",
                        (_hash(sess), aid, "web"))
        who = self.who(aid)
        who["token"] = sess
        who["instance"] = self.instance(aid, who.get("char") or "")
        who["wallet"] = self.wallet(aid)
        return who

    # ------------------------------------------------------- 角色實例（跨局）
    def instance(self, account: str, char: str, module: Optional[str] = None) -> Dict[str, Any]:
        m = self._module(module)
        with self._cursor() as cur:
            cur.execute("SELECT level, exp, note FROM char_instance"
                        " WHERE account = %s AND module = %s AND char = %s", (account, m, char))
            r = cur.fetchone()
        if r is None:
            return {}
        return {"level": int(r[0] or 1), "exp": int(r[1] or 0), **({"note": r[2]} if r[2] else {})}

    def write_instance(self, account: str, char: str, growth: Dict[str, Any],
                       module: Optional[str] = None) -> None:
        """只寫**局外成長**（`level／exp／note`）——★／道具／記憶是局內，不寫進來。

        ⚠ 不能寫成 `INSERT … ON CONFLICT … SET exp = COALESCE(EXCLUDED.exp, exp)`：
        `EXCLUDED.exp` 是「**已經套過** VALUES 裡 `COALESCE(%s, 0)` 的值」⇒ 沒給 exp 時它是 **0 而不是
        NULL**，於是「只寫 level」會把 exp 歸零（2026-10-07 自測 D4b 當場紅）。改成 UPDATE 再補 INSERT。
        """
        if not char:
            return
        m = self._module(module)
        level, exp, note = growth.get("level"), growth.get("exp"), growth.get("note")
        with self._cursor(commit=True) as cur:
            cur.execute(
                "UPDATE char_instance SET"
                "  level = COALESCE(%s, level), exp = COALESCE(%s, exp),"
                "  note = COALESCE(%s, note), updated_at = now()"
                " WHERE account = %s AND module = %s AND char = %s",
                (level, exp, note, account, m, char))
            if cur.rowcount == 0:
                cur.execute(
                    "INSERT INTO char_instance (account, module, char, level, exp, note, updated_at)"
                    " VALUES (%s, %s, %s, COALESCE(%s, 1), COALESCE(%s, 0), %s, now())",
                    (account, m, char, level, exp, note))

    # --------------------------------------------------------- 錢包（通用貨幣）
    def wallet(self, account: str, module: Optional[str] = None) -> Dict[str, Any]:
        m = self._module(module)
        with self._cursor() as cur:
            cur.execute("SELECT star_coins FROM wallet WHERE account = %s AND module = %s",
                        (account, m))
            r = cur.fetchone()
        return {"star_coins": int(r[0])} if r else {}

    def add_wallet(self, account: str, *, star_coins: int, module: Optional[str] = None) -> Dict[str, Any]:
        m = self._module(module)
        with self._cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO wallet (account, module, star_coins, updated_at) VALUES (%s, %s, %s, now())"
                " ON CONFLICT (account, module) DO UPDATE SET"
                "  star_coins = wallet.star_coins + EXCLUDED.star_coins, updated_at = now()"
                " RETURNING star_coins",
                (account, m, int(star_coins)))
            r = cur.fetchone()
        return {"star_coins": int(r[0])} if r else {}

    # --------------------------------------------------- 副本（run）與行為實例
    def start_run(self, run_id: str, module: Optional[str] = None) -> None:
        m = self._module(module)
        if not run_id:
            return
        with self._cursor(commit=True) as cur:
            cur.execute("INSERT INTO run (module, run_id, scenario) VALUES (%s, %s, %s)"
                        " ON CONFLICT (module, run_id) DO UPDATE SET"
                        "  scenario = COALESCE(EXCLUDED.scenario, run.scenario)",
                        (m, run_id, self.scenario or None))

    def join_run(self, account: str, char: str, run_id: str, seat: str,
                 module: Optional[str] = None) -> None:
        """說法 4 的那一列：**(account, module, char, 副本) → 局內行為實例**。

        ★ P2：同時把「這一局這一席是誰」物化進 `run_seat`（權威仍是劇本 seats.json）。
        """
        m = self._module(module)
        if not (run_id and char):
            return
        self.start_run(run_id, m)
        with self._cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO run_participation (account, module, char, run_id, seat)"
                " VALUES (%s, %s, %s, %s, %s)"
                " ON CONFLICT (account, module, char, run_id) DO UPDATE SET seat = EXCLUDED.seat",
                (account, m, char, run_id, seat))
            if seat:
                cur.execute(
                    "INSERT INTO run_seat (run_id, seat, module, account, char, updated_at)"
                    " VALUES (%s, %s, %s, %s, %s, now())"
                    " ON CONFLICT (run_id, seat) DO UPDATE SET"
                    "  account = EXCLUDED.account, char = EXCLUDED.char, updated_at = now()",
                    (run_id, seat, m, account, char))

    def run_written_back(self, run_id: str, module: Optional[str] = None) -> bool:
        m = self._module(module)
        if not run_id:
            return False
        with self._cursor() as cur:
            cur.execute("SELECT written_back FROM run WHERE module = %s AND run_id = %s", (m, run_id))
            r = cur.fetchone()
        return bool(r and r[0])

    def mark_run_written_back(self, run_id: str, module: Optional[str] = None) -> None:
        m = self._module(module)
        if not run_id:
            return
        with self._cursor(commit=True) as cur:
            cur.execute("INSERT INTO run (module, run_id, ended, ended_at, written_back)"
                        " VALUES (%s, %s, true, now(), true)"
                        " ON CONFLICT (module, run_id) DO UPDATE SET"
                        "  ended = true, ended_at = COALESCE(run.ended_at, now()), written_back = true",
                        (m, run_id))

    def participation(self, account: str, char: str, run_id: str,
                      module: Optional[str] = None) -> Dict[str, Any]:
        m = self._module(module)
        with self._cursor() as cur:
            cur.execute("SELECT seat, joined_at, ended, coins_written, snapshot FROM run_participation"
                        " WHERE account = %s AND module = %s AND char = %s AND run_id = %s",
                        (account, m, char, run_id))
            r = cur.fetchone()
        if r is None:
            return {}
        return {"seat": r[0], "joined_at": r[1], "ended": bool(r[2]), "coins_written": r[3],
                "snapshot": r[4]}

    def coins_written(self, account: str, char: str, run_id: str, seat: Optional[str] = None,
                      module: Optional[str] = None) -> Optional[int]:
        """**這一副本**已經寫回錢包的貨幣數（幂等鍵＝副本，不是桌 ⇒ 下一局會重新算）。"""
        m = self._module(module)
        if not (run_id and char):
            return None
        with self._cursor() as cur:
            cur.execute("SELECT coins_written FROM run_participation"
                        " WHERE account = %s AND module = %s AND char = %s AND run_id = %s",
                        (account, m, char, run_id))
            r = cur.fetchone()
        return (int(r[0]) if r and r[0] is not None else None)

    def set_coins_written(self, account: str, char: str, run_id: str, amount: int,
                          seat: Optional[str] = None, module: Optional[str] = None) -> None:
        m = self._module(module)
        if not (run_id and char):
            return
        self.start_run(run_id, m)
        with self._cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO run_participation (account, module, char, run_id, seat, coins_written)"
                " VALUES (%s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (account, module, char, run_id) DO UPDATE SET"
                "  seat = COALESCE(EXCLUDED.seat, run_participation.seat),"
                "  coins_written = EXCLUDED.coins_written",
                (account, m, char, run_id, seat, int(amount)))

    def finish_run(self, run_id: str, snapshots: Optional[Dict[str, Any]] = None,
                   module: Optional[str] = None) -> None:
        """收局：副本標 ended；每筆參與記 ended＋局內快照（`snapshots`＝`{char: dict}`）。"""
        m = self._module(module)
        if not run_id:
            return
        import json as _json
        with self._cursor(commit=True) as cur:
            cur.execute("UPDATE run SET ended = true, ended_at = COALESCE(ended_at, now())"
                        " WHERE module = %s AND run_id = %s", (m, run_id))
            for ch, snap in (snapshots or {}).items():
                cur.execute("UPDATE run_participation SET ended = true, snapshot = %s"
                            " WHERE module = %s AND run_id = %s AND char = %s",
                            (_json.dumps(snap, ensure_ascii=False), m, run_id, ch))

    # ------------------------------------------------------------------ 對外
    def snapshot(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        with self._cursor() as cur:
            cur.execute(
                "SELECT s.seat, s.account, a.display, a.kind, s.char"
                " FROM seat_assignment s LEFT JOIN account a ON a.id = s.account"
                " WHERE s.module = %s ORDER BY s.seat", (self.table,))
            seat_rows = cur.fetchall() or []
        for seat, aid, display, kind, ch in seat_rows:
            out.append({"seat": seat, "account": aid, "display": display, "kind": kind,
                        "char": ch,
                        "tables": self.tables_of(aid) if aid else [],
                        "wallet": self.wallet(aid) if aid else {},
                        "instance": self.instance(aid, ch or "") if aid else {}})
        return out
