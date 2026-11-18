#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**L6｜關聯式儲存層（PostgreSQL）**——account 模型的關係資料住這裡。

為什麼不是 JSON 檔：設計者 2026-10-07 定案的模型是**四層關係鍵**——

    account（人級／框架層，跨模組唯一）
      ├─ wallet(account, module)              ← 通用貨幣（模組內）
      ├─ char_instance(account, module, char) ← 角色實例（跨局成長）
      └─ run_participation(account, module, char, run_id) ← 這一「副本」的行為實例關聯

「關係」就是這張圖本身；用檔案做要自己在程式裡維護 union／衝突／跨行程鎖（那個 `accounts_file`
的 `_lock()` 就是為了這件事存在的）。所以這一層走資料庫（容器＝`deploy/db_up.sh`）。

**driver**：`pg8000`（純 python，**vendored 在 `vendor/`**）——不動 部署機 的共享 runtime。
`vendor/` 只在真的要用 DB 時才進 `sys.path`（`_vendor_path()`），所以純 stdlib 的桌一字不變。

**連線設定**（優先序）：`config.auth.db`（DSN 字串或 dict）＞ `config.auth.db_env`（env 檔路徑）
＞ 預設 `<repo>/.secrets/trpg_db.env`。都沒有 ⇒ `configured()` 回 None ⇒ 呼叫端退回 JSON registry。
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

from trpg.paths import ROOT as HERE  # repo 根（資料／vendor／docs 都在這裡）
VENDOR = os.path.join(HERE, "vendor")

DEFAULT_ENV_FILE = os.path.join(HERE, ".secrets", "trpg_db.env")

SCHEMA = "public"                       # 預設 schema；測試用別的（互不干擾）

# ── schema DDL：一次建齊（`IF NOT EXISTS` ⇒ 幂等；起桌／測試都能直接跑）─────────────────
DDL: List[str] = [
    # 人級：一個 identity。**不綁模組**（說法 1）。
    """CREATE TABLE IF NOT EXISTS account (
         id           text PRIMARY KEY,          -- ascii id（要能當 HTTP 標頭 ⇒ 一律 ASCII）
         display      text,
         kind         text,                      -- human|agent|gm|npc
         note         text,
         token_sha256 text UNIQUE,               -- 帳號 token（只存 sha256）
         wake_url     text,                      -- ★ 喚醒端點（kick 按帳號踢；全桌共用一個）
         wake_token_file text,                   -- kick 的 X-Table-Kick-Token 放哪個檔（600）
         created_at   timestamptz NOT NULL DEFAULT now()
       )""",
    # 人類入口：帳號＋密碼（可多把；登入成功才發 session token）
    """CREATE TABLE IF NOT EXISTS account_login (
         account         text NOT NULL REFERENCES account(id) ON DELETE CASCADE,
         username        text NOT NULL,
         password_sha256 text NOT NULL,
         PRIMARY KEY (account, username)
       )""",
    # 登入後發的 session token（與帳號 token 同效）
    """CREATE TABLE IF NOT EXISTS login_session (
         token_sha256 text PRIMARY KEY,
         account      text NOT NULL REFERENCES account(id) ON DELETE CASCADE,
         label        text,
         created_at   timestamptz NOT NULL DEFAULT now()
       )""",
    # 桌級：模組 × 席位 →（account, char）——**char↔seat 由劇本包決定**
    """CREATE TABLE IF NOT EXISTS seat_assignment (
         module         text NOT NULL,
         seat           text NOT NULL,
         account        text REFERENCES account(id) ON DELETE SET NULL,
         char           text,
         wallet_carried boolean NOT NULL DEFAULT false,
         coins_written  integer,
         updated_at     timestamptz NOT NULL DEFAULT now(),
         PRIMARY KEY (module, seat)
       )""",
    # 說法 2：account × module → 通用貨幣（**錢包只在模組內通用**）
    """CREATE TABLE IF NOT EXISTS wallet (
         account    text NOT NULL REFERENCES account(id) ON DELETE CASCADE,
         module     text NOT NULL,
         star_coins integer NOT NULL DEFAULT 0,
         updated_at timestamptz NOT NULL DEFAULT now(),
         PRIMARY KEY (account, module)
       )""",
    # 說法 3：account × module × char → 角色實例（含等級在內的可跨局參數）
    """CREATE TABLE IF NOT EXISTS char_instance (
         account    text NOT NULL REFERENCES account(id) ON DELETE CASCADE,
         module     text NOT NULL,
         char       text NOT NULL,
         level      integer NOT NULL DEFAULT 1,
         exp        integer NOT NULL DEFAULT 0,
         note       text,
         updated_at timestamptz NOT NULL DEFAULT now(),
         PRIMARY KEY (account, module, char)
       )""",
    # 說法 4：一次「副本」（一局）＝一列；written_back 掛在這一局上
    """CREATE TABLE IF NOT EXISTS run (
         module       text NOT NULL,
         run_id       text NOT NULL,
         started_at   timestamptz NOT NULL DEFAULT now(),
         ended        boolean NOT NULL DEFAULT false,
         ended_at     timestamptz,
         written_back boolean NOT NULL DEFAULT false,
         PRIMARY KEY (module, run_id)
       )""",
    # 說法 4：account × module × char × run → 局內行為實例的關聯
    #   `snapshot`＝收局時把局內那幾格（★／道具／記憶／位置）留一份存證；
    #   活的副本的「行為實例」仍是桌子的 state（不重複一套）。
    #   `coins_written`＝**這一副本**已寫回錢包的貨幣數（幂等鍵是副本，不是桌）。
    """CREATE TABLE IF NOT EXISTS run_participation (
         account       text NOT NULL REFERENCES account(id) ON DELETE CASCADE,
         module        text NOT NULL,
         char          text NOT NULL,
         run_id        text NOT NULL,
         seat          text,
         joined_at     timestamptz NOT NULL DEFAULT now(),
         ended         boolean NOT NULL DEFAULT false,
         coins_written integer,
         snapshot      jsonb,
         PRIMARY KEY (account, module, char, run_id)
       )""",
    # ★ 2026-10-20（P2，設計者定案「座次不直接和帳號綁定：帳號決定角色、劇本決定角色的座次」）：
    #   `run_seat`＝**這一局誰坐哪一席**，是「帳號選的角色 ＋ 劇本 `seats.json` 的 char↔seat」
    #   推導出來的**物化結果**。權威仍是劇本檔（定義層在 git），這張表只讓查詢不必每次回推，
    #   也讓「這一局的席位」跟「帳號資料」分開（`seat_assignment` 因此降級成相容視圖）。
    """CREATE TABLE IF NOT EXISTS run_seat (
         run_id     text NOT NULL,
         seat       text NOT NULL,
         module     text NOT NULL,
         account    text REFERENCES account(id) ON DELETE SET NULL,
         char       text,
         updated_at timestamptz NOT NULL DEFAULT now(),
         PRIMARY KEY (run_id, seat)
       )""",
]

INDEXES: List[str] = [
    "CREATE INDEX IF NOT EXISTS seat_assignment_account_idx ON seat_assignment(account)",
    "CREATE INDEX IF NOT EXISTS run_participation_seat_idx ON run_participation(module, seat)",
    "CREATE INDEX IF NOT EXISTS run_seat_account_idx ON run_seat(account)",
    "CREATE INDEX IF NOT EXISTS run_seat_module_idx ON run_seat(module, char)",
]

# ── 遷移（既有表加欄；`IF NOT EXISTS` ⇒ 幂等，每次 ensure_schema 都跑一遍）─────────────
MIGRATIONS: List[str] = [
    # 2026-10-20 kick 按帳號：喚醒端點與 token 檔掛在 account 上（全桌共用一個）
    "ALTER TABLE account ADD COLUMN IF NOT EXISTS wake_url text",
    "ALTER TABLE account ADD COLUMN IF NOT EXISTS wake_token_file text",
    # 2026-10-20 續：token 也按帳號（payload 已帶 run id ⇒ 接收方靠它校驗，不必按桌/席位分開）
    "ALTER TABLE account ADD COLUMN IF NOT EXISTS wake_token text",
    # 2026-10-20 P2：副本要知道「哪個模組的哪個劇本」（以前 module 欄位被塞 table_id）
    "ALTER TABLE run ADD COLUMN IF NOT EXISTS scenario text",
]


class DbError(RuntimeError):
    """連線／schema 出問題（呼叫端要能分辨「沒設定」與「壞掉」）。"""


def _vendor_path() -> None:
    """把 vendored 的 pg8000 掛進 sys.path（只掛一次）。"""
    if os.path.isdir(VENDOR) and VENDOR not in sys.path:
        sys.path.insert(0, VENDOR)


def _read_env_file(path: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    except OSError:
        return {}
    return out


def _from_env_dict(env: Dict[str, str]) -> Optional[Dict[str, Any]]:
    if not (env.get("TRPG_DB_PASSWORD") and env.get("TRPG_DB_NAME")):
        return None
    return {"host": env.get("TRPG_DB_HOST", "127.0.0.1"),
            "port": int(env.get("TRPG_DB_PORT", "5432")),
            "user": env.get("TRPG_DB_USER", "trpg"),
            "password": env["TRPG_DB_PASSWORD"],
            "database": env["TRPG_DB_NAME"]}


def parse_dsn(dsn: str) -> Dict[str, Any]:
    """`postgresql://user:pw@host:port/db` → dict（也吃 `postgres://`）。"""
    s = dsn.strip()
    for pref in ("postgresql://", "postgres://"):
        if s.startswith(pref):
            s = s[len(pref):]
            break
    userinfo, _, hostpart = s.partition("@")
    user, _, pw = userinfo.partition(":")
    hostport, _, db = hostpart.partition("/")
    host, _, port = hostport.partition(":")
    return {"host": host or "127.0.0.1", "port": int(port or 5432),
            "user": user or "trpg", "password": pw, "database": db or "trpg"}


def configure(cfg: Optional[Dict[str, Any]], *, default_env: bool = False) -> Optional[Dict[str, Any]]:
    """從 config 決定 DB 連線；**沒設定＝None**（呼叫端退回 JSON／legacy）。

    ⚠ `default_env` **預設 False**：只有 **config 明確說**（`auth.db` 或 `auth.db_env`）才算——
    這樣「沒設定的桌」不會因為機器上剛好有 `.secrets/trpg_db.env` 就被偷偷切到 DB
    （那會破「沒有 accounts 設定的桌 legacy 一字不變」這條不變量）。CLI 工具自己傳 `True`。
    """
    a = ((cfg or {}).get("auth") or {})
    db = a.get("db")
    if isinstance(db, dict):
        if db.get("password") and db.get("database"):
            return {"host": db.get("host", "127.0.0.1"), "port": int(db.get("port", 5432)),
                    "user": db.get("user", "trpg"), "password": db["password"],
                    "database": db["database"]}
        return None
    if isinstance(db, str) and db.strip():
        return parse_dsn(db)
    envf = str(a.get("db_env") or "").strip()
    if not envf and default_env:
        envf = DEFAULT_ENV_FILE
    if not envf:
        return None
    return _from_env_dict(_read_env_file(envf))


def connect(conf: Dict[str, Any], *, schema: Optional[str] = SCHEMA):
    """開一條連線（autocommit 關；呼叫端自己 commit）。"""
    _vendor_path()
    import pg8000.dbapi as dbapi                                   # noqa: PLC0415
    try:
        conn = dbapi.connect(host=conf["host"], port=int(conf["port"]), user=conf["user"],
                             password=conf["password"], database=conf["database"], timeout=10)
    except Exception as exc:                                                 # noqa: BLE001
        raise DbError(f"連不上 PostgreSQL（{conf.get('host')}:{conf.get('port')}"
                      f"/{conf.get('database')}）：{exc}") from exc
    cur = conn.cursor()
    if schema and schema != "public":
        cur.execute(f'SET search_path TO "{schema}"')
    cur.close()
    return conn


def ensure_schema(conf: Dict[str, Any], *, schema: str = SCHEMA) -> None:
    """建 schema 與表（幂等）。`schema != public` 時順便 `CREATE SCHEMA IF NOT EXISTS`。"""
    conn = connect(conf, schema=None)
    try:
        cur = conn.cursor()
        if schema and schema != "public":
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            cur.execute(f'SET search_path TO "{schema}"')
        for sql in DDL:
            cur.execute(sql)
        for sql in MIGRATIONS:
            cur.execute(sql)
        for sql in INDEXES:
            cur.execute(sql)
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:                                                    # noqa: BLE001
            pass


def rows(cur) -> List[Dict[str, Any]]:
    """cursor ⇒ list[dict]（欄名取 `cursor.description`）。"""
    if cur.description is None:
        return []
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def one(cur) -> Optional[Dict[str, Any]]:
    r = rows(cur)
    return r[0] if r else None


def drop_schema(conf: Dict[str, Any], schema: str) -> None:
    """測試用：整格砍掉（**只給非 public 的 schema**，避免手滑）。"""
    if not schema or schema == "public":
        raise DbError("拒絕 drop public schema")
    conn = connect(conf, schema=None)
    try:
        cur = conn.cursor()
        cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        conn.commit()
    finally:
        conn.close()
