#!/usr/bin/env python3
"""**L0｜回放比對**（取證用）——事件流重放一次，與記憶體狀態逐欄比對。

從 `engine.py` 切出來（2026-10-06 結構整理）。這裡是**純比較**：
不碰時鐘（`fresh` 用同一顆注入時鐘重放）、不碰檔案（事件已在記憶體）、不產句子。

呼叫端（`engine.Table.replay_check`）負責鎖與造 `fresh`（一顆同 home/cfg/store/clock、
`events` 空的 Table），差異回報形狀不變：`{replay_matches, events, diff}`。
"""
from __future__ import annotations

import json
from typing import Any, Dict


def replay_diff(table: Any, fresh: Any) -> Dict[str, Any]:
    """把 `table.events` 重放進 `fresh`（已就緒的空桌）並逐欄比對。"""
    for ev in table.events:
        fresh.events.append(ev)
        fresh._apply(ev)          # noqa: SLF001（同族內部：fresh 是同一個 Table）
    same = json.dumps(fresh.state, sort_keys=True) == json.dumps(table.state, sort_keys=True)
    diff: Dict[str, Any] = {}
    if not same:
        for k in set(fresh.state) | set(table.state):
            a, b = fresh.state.get(k), table.state.get(k)
            if a != b:
                if k == "seats" and isinstance(a, dict) and isinstance(b, dict):
                    for sid in set(a) | set(b):
                        sa, sb = a.get(sid) or {}, b.get(sid) or {}
                        for f in set(sa) | set(sb):
                            if sa.get(f) != sb.get(f):
                                diff[f"seats.{sid}.{f}"] = [sa.get(f), sb.get(f)]
                elif isinstance(a, dict) and isinstance(b, dict):
                    # 只报差在哪几个子键（值截断），别把整个 dict 打出来
                    for f in set(a) | set(b):
                        if a.get(f) != b.get(f):
                            diff[f"{k}.{f}"] = [str(a.get(f))[:200], str(b.get(f))[:200]]
                else:
                    diff[k] = [a, b]
    return {"replay_matches": same, "events": len(table.events), "diff": diff}
