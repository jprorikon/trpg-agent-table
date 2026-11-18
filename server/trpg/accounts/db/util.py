"""util（自 __init__.py 拆出）"""

from __future__ import annotations
import hashlib
import contextlib
import secrets
import time
from typing import Any, Dict, Iterator, List, Optional
from trpg.core import db as db


def _hash(tok: str) -> str:
    return hashlib.sha256(tok.encode("utf-8")).hexdigest()

def _basename(home: str) -> str:
    import os
    return os.path.basename(str(home).rstrip("/")) or "table"

def issue_token(acct: DbAccounts, account: str) -> str:
    """發一把新帳號 token：**只寫 sha256**、明文回給呼叫端（寫檔交人，不進版控）。"""
    tok = f"trpg_{account}_{secrets.token_hex(16)}"
    with acct._cursor(commit=True) as cur:
        cur.execute("UPDATE account SET token_sha256 = %s WHERE id = %s", (_hash(tok), account))
    return tok

def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")
