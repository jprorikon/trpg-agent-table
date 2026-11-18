"""keys（自 __init__.py 拆出；2026-10-21 拆景：只剩「輪」這一組）。

冪等鍵是**喚醒**這一層的東西：接收器（kick receiver）自己有一張持久化的去重表，
同一把鍵只會真的叫一次。所以鍵算錯＝那一腳被吞掉（靜默），鍵太鬆＝同一件事叫很多次。
"""

from __future__ import annotations
from typing import Any, Optional


def _round_no(svc) -> int:
    """輪號一律走讀出口（`adapter_view()`）——L6 不准直接摸 `table.state`（test_director A4）。"""
    return int((svc.table.adapter_view() or {}).get("round_no") or 0)


def round_kick_key(svc) -> str:
    """「這一輪開始了、GM 該說一句」的冪等鍵：`桌:round:<輪號>:<session>`。

    帶 session 是必要的（接收器的去重表持久化）：不帶的話換局之後同一個輪號不會再叫。
    """
    _sess = ""
    try:
        _sess = str(svc._session_id() or "")
    except Exception:                                                      # noqa: BLE001
        _sess = ""
    no = _round_no(svc) + 1
    return f"{svc.cfg.get('table_id') or 'table'}:round:{no}:{_sess or 'r0'}"


def round_pacing_line(svc) -> str:
    """每輪給 GM 的那句話：現在第幾輪、要做什麼（固定一句）。"""
    no = _round_no(svc) + 1
    return (f"（節奏）**第 {no} 輪**開始。請先說一句公開敘述"
            "（用 `say`／`table_gm` 的 `{\"op\": \"say\", \"text\": \"…\"}`）："
            "描寫眼前、點名剛剛那一手的後果；一句就夠，不要替玩家做決定。")
