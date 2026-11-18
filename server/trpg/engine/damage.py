#!/usr/bin/env python3
"""**L0｜HP 扣血的唯一出口**（設計者 2026-10-20：「HP 寫入要單點收口」）。

為什麼要有這一支（實測抓到的三個症狀）：

* `damage` 事件有 **5 個發射點**（`engine/act.py` 的 attack／`roll_damage`、
  `rules/board/act.py` 的 fight 失敗被打、`rules/board/state.py` 的主動怪、
  `rules/conditions.py` 的條件 tick），每一處都自己寫 `hp - dmg` ⇒ **HP 可以變成負的**，
  而折 state 只有 `state["hp"] = ev["hp_after"]` 一道（不 clamp）⇒ 投影／回執／公開日誌
  全都照原樣顯示 `-15`。打**怪**有下限（`max(0,…)`）、打**人**沒有——這個不對稱就是漏洞。
* 下限只能有一個地方算。**在 `_apply` 加 clamp 是錯的**：舊局已經存著負數的歷史事件，
  一重放就會和記憶體對不上（`op=replay` 立刻紅）。所以收口在**發射端**：
  事件從此不帶負值，舊事件一字不動。

`damage_apply` 的語意：
  * `amount`＝這一擊打出的傷害（原值，不因血量不足而縮水——日誌要看得出「他吃了 20」）；
  * `hp_after`＝`max(0, 目前 HP - amount)`（**唯一的算法**）；
  * 事件形狀與以前完全相同（`target`／`amount`／`hp_after`／`src`／`note`）⇒ 投影、
    補課行、`death_check` 都不用改。

`damage_guard`：**送還中（`skip > 0`）＝不在場**，不能被當成攻擊目標（設計者 2026-10-20 定的 (b)）。
放在這裡是為了讓「打人」的每一條路都問同一支，而不是各寫一次 `if skip > 0`。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from trpg.core.errors import RuleError


class DamageMixin:
    """HP 扣血／目標可打性的單一出口（`Table` 繼承）。"""

    def damage_apply(self, *, seat: str, amount: int, src: Optional[str] = None,
                     note: Optional[str] = None,
                     extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """扣血並發 `damage` 事件（**唯一算 hp_after 的地方**）。

        ⚠ 這裡**不**拒絕「送還中」的目標：條件 tick／主動怪這種「機制造成」的傷害是
        世界的事實（人也可能真的在城裡被追殺的劇情），拒絕會讓結算半途炸掉。
        「人不在場所以打不到」是**玩家動作**的閘 ⇒ 呼叫端先問 `damage_guard()`。
        """
        target = str(seat or "")
        s = (self.state.get("seats") or {}).get(target)
        if s is None:
            raise RuleError(f"沒有這個席位：{seat}")
        amt = max(0, int(amount or 0))
        hp_after = max(0, int(s.get("hp") or 0) - amt)      # ★ 單點收口：下限 0
        # `extra`：某些來源要在同一顆事件上留自己的痕跡（主動怪的 node／chance／roll…）。
        # 一律**在 emit 前**併進去——emit 之後再 update 會讓記憶體與落盤那一行不一致。
        tail = {k: v for k, v in (extra or {}).items() if k not in ("type", "seq", "ts", "target",
                                                                   "amount", "hp_after", "src")}
        return self._emit("damage", public=True, target=target, amount=amt,
                          hp_after=hp_after, src=src, **tail,
                          **({"note": note} if note else {}))

    def damage_guard(self, seat: str) -> None:
        """這一席現在「在場、可以被傷害」嗎？送還中 ⇒ `RuleError`（打不到）。"""
        rem = int(self.death_skip_of(seat) or 0) if hasattr(self, "death_skip_of") else 0
        if rem > 0:
            raise RuleError(f"{seat} 在送還中（還缺席 {rem} 回合，不在場）——打不到他")
