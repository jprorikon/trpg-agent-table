"""tests 共用的小工具（2026-10-21 拆景后）。

引擎不再有「景」：测试里原本用 `scene_open(...)` 把桌子推到「可以出手」的状态，
现在改成「确保有一轮在跑」；原本的 `scene_close(...)`／手写 `state["semi"]["scene"]`
则直接变成空操作（景这个概念不存在了）。
"""
from __future__ import annotations

from typing import Any


def ensure_turn(t: Any) -> Any:
    """确保这张桌有一轮在跑（没座位就先入座；没有轮就开一轮；已有就什么都不做）。"""
    if (getattr(t, "state", {}) or {}).get("turn"):
        return t
    # 没座位 ⇒ 先按 seats.json 把玩家席拉进来（拆景后「人齐就开轮」）
    if not (getattr(t, "state", {}) or {}).get("seats"):
        for sid, spec in (getattr(t, "seats_spec", {}) or {}).items():
            if spec.get("kind") not in ("human", "agent"):
                continue
            try:
                t.join(seat=sid, name=spec.get("name") or spec.get("character") or sid,
                       kind=spec["kind"], node=spec.get("start_node"))
            except Exception:                                              # noqa: BLE001
                continue
    try:
        return t.start_turn()
    except Exception:                                                     # noqa: BLE001
        order = (getattr(t, "state", {}) or {}).get("order") or []
        for seat in order:
            try:
                return t.start_turn(seat=seat)
            except Exception:                                             # noqa: BLE001
                continue
    return t


def turn_to(t: Any, seat: str, limit: int = 12) -> Any:
    """把回合推到指定席位手上（拆景后「只有当前席能出手」；旧测试里「谁都能动」的写法用它过渡）。"""
    for _ in range(limit):
        cur = (getattr(t, "state", {}) or {}).get("turn") or {}
        if cur.get("seat") == seat:
            return t
        if cur:
            try:
                t.end_turn(reason="test")      # 交棒（引擎會自己開下一手）
                continue
            except Exception:                                              # noqa: BLE001
                break
        ensure_turn(t)
        if not ((getattr(t, "state", {}) or {}).get("turn") or {}):
            break
    return t


def drop_scene(t: Any) -> Any:
    """兼容旧测试里的 `scene_close(...)`／「铺一个景」：拆景后是空操作。"""
    try:
        (t.state.get("semi") or {}).pop("scene", None)
    except Exception:                                                     # noqa: BLE001
        pass
    return t
