#!/usr/bin/env python3
"""**L3 Director**——主線距離／偏離／難度：處理「超出劇本預期」的情況，給出**明確的糾正力量**。

## 職責與手段（設計者 2026-10-06 的定位）

玩家跑歪了、卡住了、或衝太快——劇本（L1）沒寫到這些情況。這一層就是那種時候的**裁判之手**：
讀出「離主線多遠／偏離多久」，提議一個**難度檔位**。手段**只有一個**（規格定的）：
`difficulty_set` 事件——檔位再由 L1 的 `difficulty_presets` 決定實際後果：

    tight（罰）：所有檢定 DC +1、每回合行動格 −1
    relaxed（賞）：DC −1、行動格 +1
    normal：回到標準

## 紀律（`test_layers.py` F 組機械擋）

* **不讀 state**：輸入只有 ①**事件流**（鐵律③：唯一真相）②**劇本資料**（`module.director`）
  ③**L2 的 `ops` 快照**（`projection.ops`）。看不到 `state` 這顆字典。
* **不改 state、不發別的事件**：這一支**只回一個提案**（`{"preset","why","metrics"}` 或 `None`），
  `difficulty_set` 由 **L0 內核代發**（所以它想擅自改別的東西也辦不到——介面上就沒有那把鑰匙）。
* 不讀時鐘、不隨機（亂數在 L0）、不碰檔案／網路。同一個事件流 ⇒ 同一個提案（可重放）。

## 政策寫在劇本裡（`module.json` 的 `director` 區塊）

    "director": {
      "track": true,
      "progress_on": ["mob_killed", "entered_tower", "boss_defeated", "wish_done"],
      "policy": [
        {"when": {">=": [{"var": "signals.stall_hands"}, 10]}, "preset": "tight",
         "why": "卡了 {stall_hands} 手沒有推進"},
        {"when": true, "preset": "normal", "why": "回到標準"}
      ]
    }

`signals` 是這一層算出來的**指標**（見 `signals()`）：`hands`／`progress`（累計推進）／
`stall_hands`（距上次推進幾手）／`progress_recent`／`recent_idle`（最近 `window` 手，預設 5）／
`idle_hands`／`idle_streak`／`move_lag`／`rounds`／`window`／`players`／`down`。
**政策一律用「窗口」或「距離」指標**（`stall_hands`／`recent_idle`／`progress_recent`）——
累計數（`idle_hands`／`progress`）在長局一定會飽和，拿它當門檻會一開局就誤判。
`why` 可以寫 `{指標名}`（代進數值）——它會跟著 `difficulty_set` 事件進日誌，GM 看得到。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

#: ⚠ 拆景（2026-10-21）：「開新景」原本算一次推進；新的 `round_started` **每輪都發生** ⇒ 不能算推進
#:   （算了就等於「導演永遠覺得在推進」，永遠不會收緊）。所以這裡只留真正的事件。
DEFAULT_PROGRESS_ON = ("mob_killed", "entered_tower", "elevator", "boss_defeated",
                       "wish_done", "fragment_granted", "white_room")
#: 「空手」的定義：**超時**或場景制下的 `pass`。回合制的 `end_turn` 是正常交棒（不算卡）。
IDLE_REASONS = ("timeout", "pass")


class Director:
    """一個桌子一顆（無狀態快取只為了別每次重掃事件流）。"""

    def __init__(self, module: Any = None) -> None:
        self.module = module
        self._cache: Optional[tuple] = None

    # ------------------------------------------------------------------ 資料

    @property
    def policy_block(self) -> Dict[str, Any]:
        data = getattr(self.module, "data", None) or {}
        blk = data.get("director")
        return blk if isinstance(blk, dict) else {}

    def tracking(self) -> bool:
        blk = self.policy_block
        return bool(blk) and blk.get("track", True) is not False \
            and bool(blk.get("policy"))

    def progress_on(self) -> set:
        blk = self.policy_block
        return set(blk.get("progress_on") or DEFAULT_PROGRESS_ON)

    # ------------------------------------------------------------------ 指標

    def signals(self, events: List[Dict[str, Any]], ops: Dict[str, Any]) -> Dict[str, Any]:
        """**主線距離／偏離**的量化：全部從事件流算（可重放、與 state 無關）。

        - `progress`：主線推進次數（`progress_on` 那些事件）；`stall_hands`：距上次推進幾手。
        - `idle_hands`／`idle_streak`：空手／超時的累計與連續數（有人一直在原地）。
        - `move_lag`：距上次真正換地點（move／elevator／entered_tower）幾手。
        """
        n = len(events)
        if self._cache and self._cache[0] == n:
            return self._cache[1]
        prog_on = self.progress_on()
        window = int(self.policy_block.get("window") or 5)
        hands = 0
        progress = 0
        last_prog_hand = 0
        idle = 0
        streak = 0
        last_move_hand = 0
        # 逐手的標記（最近 `window` 手用得到）：1＝這一手有推進、2＝這一手空手/超時
        marks: List[int] = []
        for ev in events:
            t = ev.get("type")
            if t == "turn_ended":
                hands += 1
                marks.append(2 if ev.get("reason") in IDLE_REASONS else 0)
                if ev.get("reason") in IDLE_REASONS:
                    idle += 1
                    streak += 1
                else:
                    streak = 0
            elif t == "seat_timeout":
                idle += 1
                streak += 1
                if marks:
                    marks[-1] = 2
            elif t in prog_on:
                progress += 1
                last_prog_hand = hands
                if marks:
                    marks[-1] = 1
            elif t in ("move", "elevator", "entered_tower") or str(t).endswith("_killed"):
                last_move_hand = hands
        recent = marks[-window:] if marks else []
        seats = ops.get("seats") or {}
        out: Dict[str, Any] = {
            "hands": hands, "progress": progress,
            "stall_hands": hands - last_prog_hand,
            "idle_hands": idle, "idle_streak": streak,
            "recent_idle": sum(1 for m in recent if m == 2),
            "progress_recent": sum(1 for m in recent if m == 1),
            "window": window,
            "move_lag": hands - last_move_hand,
            "rounds": int(ops.get("round_no") or 0),
            "players": len(ops.get("humans") or []) + len(ops.get("agents") or []),
            "agents": len(ops.get("agents") or []),
            "down": sum(1 for s in seats.values() if s.get("down")),
            "difficulty": ops.get("difficulty"),
        }
        self._cache = (n, out)
        return out

    # ------------------------------------------------------------------ 提案

    def review(self, events: List[Dict[str, Any]], ops: Dict[str, Any],
               current: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """看一輪，回**一個難度提案**（`None`＝不說話）。

        規則：政策第一條命中的就是提案；**與現行檔位相同就不提**（不刷事件）；
        `track: false`／沒寫 `director` 區塊 ⇒ 永遠不說話（＝舊行為）。
        """
        if not self.tracking():
            return None
        sig = self.signals(events, ops)
        cur = current if current is not None else sig.get("difficulty")
        # 匯入放這裡（延後）：避免 L3 在 import 期就依賴 L1 的求值器
        from trpg.rules import expr as expr

        for rule in (self.policy_block.get("policy") or []):
            node = rule.get("when")
            try:
                hit = True if node is None else bool(expr.evaluate(node, {"signals": sig}))
            except expr.ExprError:
                raise
            if not hit:
                continue
            preset = rule.get("preset")
            if not preset or preset == cur:
                return None
            why = str(rule.get("why") or "").format(**{k: v for k, v in sig.items()
                                                       if isinstance(v, (int, str))})
            return {"preset": str(preset), "why": why or f"難度 → {preset}",
                    "metrics": {k: sig[k] for k in ("progress", "progress_recent", "stall_hands",
                                                    "recent_idle", "idle_streak", "move_lag",
                                                    "rounds")}}
        return None

    def validate(self) -> List[str]:
        """政策本身合法嗎（pytest 與 `tools/check_campaign.py` 共用）。"""
        from trpg.rules import expr as expr

        errs: List[str] = []
        blk = self.policy_block
        if not blk:
            return errs
        presets = getattr(self.module, "difficulty_names", lambda: [])()
        for i, rule in enumerate(blk.get("policy") or []):
            if not isinstance(rule, dict) or not rule.get("preset"):
                errs.append(f"director.policy[{i}]: 每條要 preset")
                continue
            if presets and rule["preset"] not in presets:
                errs.append(f"director.policy[{i}]: 沒有這個難度預設 {rule['preset']!r}"
                            f"（有：{presets}）")
            node = rule.get("when")
            if node is not None:
                errs += [f"director.policy[{i}].when: {e}"
                         for e in expr.validate(node)]
                import json as _json
                for var in re.findall(r'"var"\s*:\s*"([^"]+)"', _json.dumps(node)):
                    if not var.startswith("signals."):
                        errs.append(f"director.policy[{i}].when: L3 只看得到 signals.*（{var!r}）")
                for key in re.findall(r"\{(\w+)\}", str(rule.get("why") or "")):
                    if key not in ("hands", "progress", "progress_recent", "stall_hands",
                                   "idle_hands", "idle_streak", "recent_idle", "move_lag",
                                   "rounds", "window", "players", "agents",
                                   "down", "difficulty"):
                        errs.append(f"director.policy[{i}].why: 沒有這個指標 {key!r}")
        return errs
