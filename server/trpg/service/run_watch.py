#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**一局一個觀戰開關**（`runs/<run>/watch.json`）。

2026-10-20 設計者：「现在没有桌设计了，所有的都是局」——`watch.enabled` 以前寫在桌的
`config.json`，是**桌級**狀態；那會讓「這一局關觀戰」影響到下一局，而且換局後新 owner
改的是整桌的設定。現在：

* 每一局自己一份 `runs/<run>/watch.json`：`{"enabled": bool, "at": ts, "by": account}`。
* **有檔就以此為準**；沒有檔（舊局）才退回 `config.watch.enabled`（部署預設／相容）。
* `setWatchable(tableId, session)` 只改**那一局**；`newRun(watchable:)` 只給新局。
* `config.watch.port`／`read_token` 仍是部署層（觀戰桌怎麼起、token 放哪），不隨局變。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional

WATCH = "watch.json"


def path(home: str, session: str) -> str:
    return os.path.join(home, "runs", str(session), WATCH)


def _run_of(home: str, session: str) -> str:
    run = str(session or "").strip()
    if run:
        return run
    from trpg.core.store import current_run                      # noqa: PLC0415
    return str(current_run(home) or "")


def read(home: str, session: str = "") -> Optional[bool]:
    """這一局的觀戰開關；**沒有檔＝None**（呼叫端決定要不要退回 config 的預設）。"""
    run = _run_of(home, session)
    if not run:
        return None
    try:
        with open(path(home, run), encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or "enabled" not in doc:
        return None
    return bool(doc.get("enabled"))


def enabled(home: str, session: str = "", default: bool = False) -> bool:
    """這一局開不開觀戰（沒有局檔 ⇒ `default`＝config 的值）。"""
    got = read(home, session)
    return bool(default) if got is None else got


def set_enabled(home: str, session: str, on: bool, by: str = "") -> bool:
    """寫這一局的開關（只動 `runs/<run>/watch.json`）。"""
    run = _run_of(home, session)
    if not run:
        return False
    p = path(home, run)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"enabled": bool(on), "at": time.time(), "by": str(by or "")},
                      fh, ensure_ascii=False, indent=1)
        return True
    except OSError:
        return False


def doc(home: str, session: str = "") -> Dict[str, Any]:
    """診斷用：這一局的原始檔（沒有＝空 dict）。"""
    run = _run_of(home, session)
    try:
        with open(path(home, run), encoding="utf-8") as fh:
            got = json.load(fh)
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}
