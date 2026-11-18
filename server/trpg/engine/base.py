"""內核的共用底座：預設時鐘、MemoryStore、公開白名單與動作目錄轉出。"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, List

from trpg.engine import projection as projection
from trpg.rules import action_catalog as action_catalog


def _system_clock() -> float:
    """**預設時鐘**——L0 唯一允許碰到 `time` 的地方（它本身就是「注入值」的來源）。

    第 3 刀（2026-10-06 設計者拍的分層）：內核不准讀時鐘。所有「現在幾點」都走
    `Table.now()` ⇒ `Table(clock=...)` 換一顆就換掉整張桌的時間軸（測試餵假時鐘、
    回放可重現、投影可比較）。
    """
    return time.time()


class MemoryStore:
    """**內核的預設 sink**：事件只留在記憶體——L0 只吐事件，寫檔是 L6 的事。

    與 `store.FileStore` 同一組語意（append-only、尾行可就地覆寫），只是不持久化。
    要落檔＝把 `store.FileStore(home)` 注入 `Table(..., store=...)`。
    """

    def __init__(self) -> None:
        self._lines: List[bytes] = []

    def load(self) -> List[Dict[str, Any]]:
        """這一顆 sink 裡現有的事件（全新的一顆＝空 ⇒ 桌子照舊自己發 `table_created`）。"""
        return [json.loads(x) for x in self._lines]

    def append(self, ev: Dict[str, Any]) -> None:
        self._lines.append((json.dumps(ev, ensure_ascii=False) + "\n").encode("utf-8"))

    def size(self) -> int:
        return sum(len(x) for x in self._lines)

    def replace_tail(self, offset: int, ev: Dict[str, Any]) -> bool:
        blob = b"".join(self._lines)
        if not 0 <= offset <= len(blob):
            return False
        tail = (json.dumps(ev, ensure_ascii=False) + "\n").encode("utf-8")
        self._lines = [x + b"\n" for x in (blob[:offset] + tail).split(b"\n") if x]
        return True


VIEW_LOG_TYPES = projection.VIEW_LOG_TYPES


normalize_actions = action_catalog.normalize_actions


action_modes = action_catalog.action_modes


visible_actions = action_catalog.visible_actions

