#!/usr/bin/env python3
"""**L1｜動作目錄**——`rules.actions` 的解析與節奏過濾（config 驅動，不硬編碼）。

從 `engine.py` 切出來（2026-10-06 結構整理）：內核只消費它的**結果**
（`action_ids`／工具列舉），這幾個函式本身與裁決無關，擺在 L1 更名副其實。

- `normalize_actions()`：**規則閘**吃的那份（寬鬆：字串或 `{id,label,desc}` 都吃）。
- `visible_actions()`：**UI／工具面**看得到的那份（再過 `modes` 節奏過濾）。
- `action_modes()`：條目上 `modes: [scene|turn]` 的解析。
"""
from __future__ import annotations

from typing import Any, List, Optional


def normalize_actions(raw: Any) -> "tuple[list[str], list[dict[str, str]]]":
    """rules.actions 允许两种写法（協作端 2026-10-05 §2105 契约；默认值语义见 §2121）：
      - 字符串 id（旧）⇒ label／desc 默认都是 ""（＝「没有内容」，由前端回退内建字串。
        不把 id 回声成 label——那会让砖面显示原始 id，见 協作端 2121）
      - 对象 {id, label, desc}（新）⇒ 剧本自己给显示名与说明；缺的字段同样默认 ""
    返回 (ids, catalog)：ids 供规则闸/枚举用；catalog 供 /api/state 的 actions[] 用。
    """
    ids: list[str] = []
    catalog: list[dict[str, str]] = []
    for it in raw or []:
        if isinstance(it, dict):
            aid = str(it.get("id") or "").strip()
            if not aid:
                continue
            label = str(it.get("label") or "")
            desc = str(it.get("desc") or "")
        else:
            aid = str(it)
            label = ""
            desc = ""
        ids.append(aid)
        catalog.append({"id": aid, "label": label, "desc": desc})
    return ids, catalog


def action_modes(it: Any) -> List[str]:
    """條目上的可選欄位 ``modes``：這個動作只在哪些**節奏**下該出現（scene／turn）。

    不給＝兩種節奏都出現（向後相容）。給字串或給陣列都吃（沿用 rules.actions 的寬鬆風格）。
    """
    if isinstance(it, dict):
        raw = it.get("modes")
        if isinstance(raw, str):
            raw = [raw]
        if isinstance(raw, (list, tuple)):
            return [str(x).strip().lower() for x in raw if str(x).strip()]
    return []


def visible_actions(raw: Any, pacing_mode: Optional[str]) -> "tuple[List[str], List[dict]]":
    """UI／工具面該看到的動作：``rules.actions`` ＋ **節奏過濾**（2026-10-06）。

    為什麼要分「規則閘吃什麼」與「看得見什麼」：`pass`（這一景我不動）是**場景制**的動作；
    回合制下「我不出手」＝「結束我的回合」，前端不該再給那顆磚。但橋的離線代打還在送 `pass`
    ⇒ **規則閘仍吃它**（`Table.action_ids` 用未過濾的），只是不進 `allowed_actions`／工具列舉。
    """
    ids: List[str] = []
    catalog: List[dict] = []
    mode = str(pacing_mode or "scene").lower()
    for it in raw or []:
        if isinstance(it, dict):
            aid = str(it.get("id") or "").strip()
            label = str(it.get("label") or "")
            desc = str(it.get("desc") or "")
        else:
            aid, label, desc = str(it).strip(), "", ""
        if not aid:
            continue
        modes = action_modes(it)
        if modes and mode not in modes:
            continue
        ids.append(aid)
        catalog.append({"id": aid, "label": label, "desc": desc})
    return ids, catalog
