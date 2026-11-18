"""limits（自 SemiCoopMixin 拆出）。"""

from __future__ import annotations
import json
import os
import random
from typing import Any, Dict, List, Optional
from trpg.core.errors import RuleError  # 与 engine.RuleError 同一个类（本模块不 import engine，避免循环依赖）


class SemiCoopLimitsMixin:
    def semic_gate(self, seat: str, kind: str) -> None:
        """每回合额度（**只检查，不计数**）：**所有人**（human/agent）同一套上限（config.turn）。

        计数由 `semic_count()` 在**动作成功之后**做——因为「试了但被规则拒掉」
        （例如条件不满足的 wish）不该扣掉它这一回合的额度。
        GM / NPC 不受限（它们不是玩家）。
        """
        if not getattr(self, "semic_enabled", False):
            return
        if self._seat_kind(seat) not in ("human", "agent"):
            return
        st = self.state["semi"]
        limit = self._round_limit(seat, kind)
        used = int(st["counters"].get(seat, {}).get(kind, 0))
        if used >= limit:
            raise RuleError(f"你这一轮的 {kind} 已经用完了（{used}/{limit}）"
                            "——等下一轮再动（想提早收手用 action=end_turn）")


    def _seat_kind(self, seat: str) -> str:
        return (self.seats_spec.get(seat) or {}).get("kind") \
            or (self.state["seats"].get(seat) or {}).get("kind")


    def _round_limit(self, seat: str, kind: str) -> int:
        """每回合额度：动作走 `turn_action_budget()`（＝`turn.actions_per_turn`），
        发言走 `turn.says_per_turn`（迁移期仍认旧剧本写的 `pacing.says_per_scene`）。
        再各自吃条件的加减格（`act_slots_mod`／`say_slots_mod`）。
        """
        if kind == "act":
            # 難度檔位加減格數（收緊＝−1、放鬆＝+1）；再吃條件的影響維度
            base = max(1, self.turn_action_budget() + self.difficulty_act_mod())
            return max(0, base + self._cond_slots(seat, "act"))
        # 發言：`turn.says_per_turn`（迁移期：旧剧本写在 pacing.says_per_scene 也认）
        v = (self.turn_cfg or {}).get("says_per_turn")
        if v is None:
            v = (self.cfg.get("pacing") or {}).get("says_per_scene")
        try:
            base = int(v) if v is not None else 2
        except (TypeError, ValueError):
            base = 2
        return max(0, base + self._cond_slots(seat, "say"))


    def _cond_slots(self, seat: str, kind: str) -> int:
        """條件對額度的加減（`act_slots_mod`／`say_slots_mod`）。沒啟用條件表／沒這欄＝0。"""
        try:
            return int(self.conditions_mod(seat, kind + "_slots") or 0)
        except Exception:                                                     # noqa: BLE001
            return 0


    def semic_count(self, seat: str, kind: str) -> None:
        """动作**成功之后**才计数（计数也走事件，否则回放对不上内存）。"""
        if not getattr(self, "semic_enabled", False):
            return
        if self._seat_kind(seat) not in ("human", "agent"):
            return
        st = self.state["semi"]
        c = st["counters"].setdefault(seat, {})
        c[kind] = int(c.get(kind, 0)) + 1
        # 计数也走事件（否则回放对不上内存）
        self._emit("throttle_tick", public=False, seat=seat, kind=kind, used=c[kind])

