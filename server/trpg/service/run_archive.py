#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一局的 Archive（封存）狀態——`runs/<run>/archived.json`。

設計者 2026-10-19：「再加一個 Archive 狀態，owner 可以把 run 設為 Archive，此時就不再出現在
選桌裡面；web 也對應修改；Archive 狀態下也不再能發送信息。」

設計取捨：
* **與 `ended` 不同**。`ended` 是「這一局的劇情跑完了」（引擎判定，事件流裡有 `ended`）；
  Archive 是「owner 把這一局收起來了」（管理動作）——可以封存一局沒跑完的桌、也可以不封存已收局的桌。
* **不放 config.json**：config 是「桌」的（跨局沿用），封存是「局」的 ⇒ 跟 owner 一樣住局目錄。
* 檔案內容帶 `{at, by}`（誰、什麼時候），方便事後追。
"""
from __future__ import annotations

import json
import os
import time

ARCHIVED = "archived.json"


def _path(home: str, session: str) -> str:
    return os.path.join(home, "runs", str(session), ARCHIVED)


def info(home: str, session: str = "") -> dict:
    """`{archived: bool, at: float, by: str}`；沒有檔＝沒封存。`session` 省略 ⇒ 現役那一局。"""
    run = str(session or "")
    if not run:
        try:
            from trpg.core import store as _st                                            # noqa: PLC0415
            run = str(_st.current_run(home) or "")
        except Exception:                                                  # noqa: BLE001
            run = ""
    if not run:
        return {"archived": False, "at": 0.0, "by": ""}
    try:
        with open(_path(home, run), encoding="utf-8") as fh:
            d = json.load(fh)
        return {"archived": bool(d.get("archived", True)),
                "at": float(d.get("at") or 0.0), "by": str(d.get("by") or "")}
    except (OSError, ValueError):
        return {"archived": False, "at": 0.0, "by": ""}


def is_archived(home: str, session: str = "") -> bool:
    return bool(info(home, session).get("archived"))


def set_archived(home: str, session: str, on: bool, by: str = "") -> bool:
    """設／取消封存。`on=False` ⇒ 刪檔（不寫 archived:false，少一種狀態）。"""
    run = str(session or "")
    if not run:
        return False
    p = _path(home, run)
    if not on:
        try:
            os.remove(p)
            return True
        except OSError:
            return True                     # 本來就沒有＝已經是「沒封存」
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"archived": True, "at": time.time(), "by": str(by or "")}, fh,
                      ensure_ascii=False)
        return True
    except OSError:
        return False
