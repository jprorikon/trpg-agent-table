#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**挑 account 後端**（L6）：有設 DB ⇒ `accounts_db.DbAccounts`；沒設 ⇒ `auth.Accounts`（JSON）。

消費端（`web_api`／`mcp_api`／工具）只要跟這支拿 store，不直接決定後端——
「**同一件事不做兩份**」的那條紀律：介面一份（方法名一樣），實作兩份。

    account_store.accounts_for(home, table_id, cfg)      # 這一桌
    store.peer(other_home, other_table_id)               # 別張桌（互斥檢查用；同一個後端）
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from trpg.accounts import auth as auth

from trpg.core import db as db



def configured(cfg: Optional[Dict[str, Any]]) -> bool:
    """這個 config 有沒有設 DB（決定要走哪個後端；也給 `/api/state` 的 auth 資訊用）。"""
    return db.configure(cfg or {}) is not None


def accounts_for(home: str, table: Optional[str] = None, cfg: Optional[Dict[str, Any]] = None,
                 *, run_id: str = "", module: Optional[str] = None):
    """這一桌的 store。

    ★ 2026-10-20（P2）：`table`＝**副本 id**、`module`＝**真模組名**（不給就從 cfg／路徑解）。
    `run_id`＝這一局的 id：給了之後座次讀寫走 `run_seat`（這一局的物化座次）。
    """
    cfg = cfg or {}
    conf = db.configure(cfg)
    if conf:
        from trpg.accounts import db as accounts_db                                            # 只在真的要 DB 時才 import
        schema = str(((cfg.get("auth") or {}).get("db_schema")) or db.SCHEMA)
        return accounts_db.DbAccounts(home, table, conf=conf, schema=schema, cfg=cfg,
                                      module=module, run_id=run_id)
    return auth.Accounts(home, table, cfg=cfg)
