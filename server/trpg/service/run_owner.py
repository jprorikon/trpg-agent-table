#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一局一個 owner（`runs/<run>/owner`）。

設計者 2026-10-19：「另外一個 run 最好有一個 owner，只有 owner 能呼喚 gm 和調整可見性。」

存法刻意最笨：`runs/<run>/owner` 一行純文字（帳號 id），跟 `runs/current` 同一層。
**不放 config.json**——config 是「桌」的（跨局沿用），owner 是「局」的（換局可以換人）。
"""
from __future__ import annotations

import os

OWNER = "owner"


def _path(home: str, session: str) -> str:
    return os.path.join(home, "runs", str(session), OWNER)


def owner_of(home: str, session: str = "") -> str:
    """這一局的 owner（沒有就回空字串）。`session` 省略 ⇒ 現役那一局。"""
    from trpg.core.store import current_run                                   # 延遲 import：同層工具
    run = str(session or "") or str(current_run(home) or "")
    if not run:
        return ""
    try:
        with open(_path(home, run), encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def set_owner(home: str, session: str, account: str) -> bool:
    """寫這一局的 owner（`account` 空 ⇒ 不寫、回 False）。"""
    acct = str(account or "").strip()
    if not acct or not session:
        return False
    p = _path(home, session)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(acct + "\n")
        return True
    except OSError:
        return False


def is_owner(home: str, session: str, account: str) -> bool:
    """這一局是不是他當家。**沒有 owner 檔的舊局 ⇒ 誰都不算**（免得預設放行）。"""
    acct = str(account or "").strip()
    return bool(acct) and owner_of(home, session) == acct
