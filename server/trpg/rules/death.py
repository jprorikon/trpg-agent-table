#!/usr/bin/env python3
"""**L1｜死亡與送還**（2026-10-07 設計者：「角色死亡会回到最近的城市并跳过2回合」）。

一顆旋鈕（`rules.death`；**包給預設、`config` 一律贏**；沒有這顆／`mode:"off"` ⇒ legacy 一字不變）：

```jsonc
"death": {"mode": "return_city",    // off＝舊行為：HP ≤ 0 只是 down，躺著等人救
          "skip_turns": 2,          // 送還後缺席幾個「自己的回合」
          "hp_restore": 1,          // 送還後的 HP（1＝只剩一口氣）
          "nearest": true,          // 回**最近的城市**（false＝退回 start_node）
          "clear_conditions": false,// 送還時洗掉部位受傷／異常狀態
          "drop_coins": 0,          // 掉幾成貨幣（0＝不掉、1＝全掉）
          "lost_memory": 0}         // 額外扣多少記憶
```

分工（照分層紀律）：
  * **機制住內核**（這一支）：被 `damage` 打到 HP ≤ 0 ⇒ `death` 事件（公開）＋節點換到最近的城市
    ＋HP 復原（順帶解掉 `down`）＋ `state["skip"][seat]` 記缺席回合；回合機（`start_turn`）遇到
    缺席的人發 `turn_skipped` 後**直接交下一席**。
  * **數值住包**：`packs/death/pack.json` 的 `provides.defaults.rules.death`（改數值不改程式）。
  * **事件溯源**：兩筆事件都帶**結果值**（`to`／`hp_after`／`remaining`）⇒ `op=replay` 不必重算。

⚠ 與「倒地（`down`）」的關係：`down` ＝ **沒死**——躺著等 `rescue`（★1）或治療。
   這裡是**死了**：規則接手把人送還，HP 回到一口氣、缺席 N 個回合，不必等人來救。
   「最近的城市」＝無向 BFS 走棋盤的 `adj`，挑 `type=="city"`（或 tags 有 `city`）的第一個；
   沒有棋盤／找不到城市 ⇒ 退回 `start_node`。**沒有棋盤的抽象桌**：只做 HP 復原與缺席（不動位置）。
"""
from __future__ import annotations

from collections import deque
from typing import Any, Dict, List, Optional

from trpg.core.errors import RuleError

#: 旋鈕的形狀（壞了＝**載入當場叫**，不拖到跑團中間）
DEFAULT: Dict[str, Any] = {
    "mode": "return_city", "skip_turns": 2, "hp_restore": 1, "nearest": True,
    "clear_conditions": False, "drop_coins": 0, "lost_memory": 0,
}

#: 認得的模式：`return_city`＝送還到最近的城市；`off`＝完全關掉（legacy）
MODES = ("return_city", "off")


class DeathMixin:
    """死亡／送還的實作。**沒啟用 ⇒ 每個方法都是 no-op**（legacy 零變更）。"""

    # ------------------------------------------------------------------ 載入
    def death_init(self, home: str = "", cfg: Optional[Dict[str, Any]] = None) -> None:
        """讀 `rules.death`（config ＋ 包預設合併後的那一份）；形狀壞了當場叫。"""
        raw = (self.rules or {}).get("death") or {}
        if not isinstance(raw, dict):
            raise RuleError("rules.death 要是物件（見 docs/SCENARIO_FORMAT.md §4.10）")
        got = dict(DEFAULT)
        got.update(raw)

        mode = str(got.get("mode") or "return_city").lower()
        if mode not in MODES:
            raise RuleError(f"rules.death.mode 只能是 {MODES}（拿到 {mode!r}）")
        got["mode"] = mode

        def _int(key: str, lo: int) -> int:
            v = got.get(key)
            try:
                n = int(v)
            except (TypeError, ValueError):
                raise RuleError(f"rules.death.{key} 要是整數（拿到 {v!r}）") from None
            if n < lo:
                raise RuleError(f"rules.death.{key} 不可小於 {lo}（拿到 {n}）")
            return n

        got["skip_turns"] = _int("skip_turns", 0)
        got["hp_restore"] = _int("hp_restore", 1)
        got["lost_memory"] = _int("lost_memory", 0)

        dc = got.get("drop_coins") or 0
        try:
            dc = float(dc)
        except (TypeError, ValueError):
            raise RuleError(f"rules.death.drop_coins 要是 0~1 的數（拿到 {got.get('drop_coins')!r}）") from None
        if not (0.0 <= dc <= 1.0):
            raise RuleError(f"rules.death.drop_coins 要在 0~1 之間（幾成貨幣；拿到 {dc}）")
        got["drop_coins"] = dc
        got["nearest"] = bool(got.get("nearest", True))
        got["clear_conditions"] = bool(got.get("clear_conditions", False))

        self.death_cfg: Dict[str, Any] = got
        self.death_enabled: bool = bool(raw) and mode != "off"

    # ------------------------------------------------------------------ 折算
    def death_apply(self, ev: Dict[str, Any]) -> None:
        """把 `death`／`turn_skipped` 折進 state（**只用事件裡的值**，不重算）。"""
        t = ev.get("type")
        if t not in ("death", "turn_skipped"):
            return
        seat = ev.get("seat")
        if not seat:
            return
        if t == "death":
            s = (self.state.get("seats") or {}).get(seat)
            if s is None:
                return
            if ev.get("to"):
                s["node"] = ev["to"]
            if ev.get("hp_after") is not None:
                s["hp"] = int(ev["hp_after"])
            s["status"] = [x for x in (s.get("status") or []) if x != "down"]
            if ev.get("clear_conditions"):
                (self.state.get("conditions") or {}).pop(seat, None)
            skip = int(ev.get("skip") or 0)
            sk = self.state.setdefault("skip", {})
            if skip > 0:
                sk[seat] = skip
            else:
                sk.pop(seat, None)
            b = self.state.get("board") or {}
            if b and seat in (b.get("inside_turns") or {}):
                b["inside_turns"][seat] = 0        # 人已經不在塔裡了
        else:                                       # turn_skipped
            sk = self.state.setdefault("skip", {})
            rem = int(ev.get("remaining") or 0)
            if rem > 0:
                sk[seat] = rem
            else:
                sk.pop(seat, None)

    # ------------------------------------------------------------------ 查詢
    def death_skip_of(self, seat: str) -> int:
        """這一席**還要被跳過幾個回合**（0＝正常出手）。沒啟用＝永遠 0。"""
        if not getattr(self, "death_enabled", False):
            return 0
        return int((self.state.get("skip") or {}).get(seat) or 0)

    def death_return_node(self, node: Optional[str]) -> Optional[str]:
        """**最近的城市**：無向 BFS 走 `adj`，挑第一個城市（`nearest:false` ⇒ `start_node`）。"""
        back = getattr(self, "start_node", None)
        if not getattr(self, "board_enabled", False) or not getattr(self, "nodes", None):
            return back                                  # 抽象桌：沒有圖 ⇒ 不動位置
        if not bool(self.death_cfg.get("nearest", True)):
            return back
        if not node or node not in self.nodes:
            return back
        seen = {node}
        q: deque = deque([node])
        while q:
            cur = q.popleft()
            n = self.nodes.get(cur) or {}
            if cur != node and (n.get("type") == "city" or "city" in (n.get("tags") or [])):
                return cur
            for nb in (self.adj.get(cur) or {}):
                if nb not in seen:
                    seen.add(nb)
                    q.append(nb)
        return back                                      # 圖上沒有城市 ⇒ 退回起點

    # ------------------------------------------------------------------ 送還
    def death_check(self, ev: Dict[str, Any]) -> List[Dict[str, Any]]:
        """`damage` 事件把某人打到 HP ≤ 0 ⇒ 送還。

        由 `_run_triggers`（L0 的「跑到定點」迴圈）呼叫——與條件結算同一拍、只在最外層跑
        ⇒ 四個傷害來源（`attack`／`roll_damage`／`fight` 失敗被打／條件 `tick.damage`）全都吃得到。
        """
        if not getattr(self, "death_enabled", False) or ev.get("type") != "damage":
            return []
        seat = ev.get("target") or ev.get("seat")
        hp = ev.get("hp_after")
        if not seat or hp is None:
            return []
        s = (self.state.get("seats") or {}).get(seat)
        if s is None or s.get("kind") == "gm" or int(hp) > 0:
            return []                                    # GM 不在棋盤上；HP 還有就沒死
        # ★ 2026-10-20（設計者抓的「判死不幂等」）：**送還中的人不再判死**。
        #   他已經在城裡躺著（`skip > 0`），這時再吃一次傷害（玩家 attack／GM roll_damage／
        #   條件 tick）會走到這裡 ⇒ 以前會發第二筆 `death`：缺席計數被重設、再掉一次幣。
        #   這一支是**兜底**（玩家動作那一側另有 `damage_guard` 直接拒「打不到不在場的人」）。
        if self.death_skip_of(seat) > 0:
            return []

        cfg = self.death_cfg
        to = self.death_return_node(s.get("node"))
        hp_restore = max(1, int(cfg["hp_restore"]))
        out: List[Dict[str, Any]] = [self._emit(
            "death", public=True, seat=seat, at=s.get("node"), to=to,
            skip=int(cfg["skip_turns"]), hp_after=hp_restore,
            clear_conditions=bool(cfg["clear_conditions"]),
            reason=str(ev.get("src") or ev.get("note") or "killed"),
            text=self.phrase("death.text"))]

        lost_mem = int(cfg.get("lost_memory") or 0)
        if lost_mem and s.get("memory") is not None:
            out.append(self.death_memory(seat, -lost_mem))

        ratio = float(cfg.get("drop_coins") or 0)
        if ratio > 0 and getattr(self, "board_enabled", False):
            coins = int(s.get(self.slot("coin")) or 0)
            lost = int(coins * ratio)
            if lost:
                self.grant_coins(seat=seat, amount=-lost, reason="death", by="death")
                out.append(self.events[-1])              # coin_grant（同一個 lock 內、緊接著）
        return out

    def death_memory(self, seat: str, delta: int) -> Dict[str, Any]:
        """記憶增減（**只給死亡罰則用**；記憶欄位與棋盤同一套，沒棋盤＝不動）。"""
        s = (self.state.get("seats") or {}).get(seat)
        if s is None or s.get("memory") is None or not getattr(self, "board_enabled", False):
            return {}
        before = int(s.get("memory") or 0)
        after = max(0, before + int(delta))
        if after == before:
            return {}
        return self._emit("memory_change", public=True, seat=seat, delta=after - before,
                          before=before, after=after,
                          reason=self.phrase("death.memory_lost"))

    # ------------------------------------------------------------------ 回合機
    def death_skip_turn(self, seat: str) -> Optional[Dict[str, Any]]:
        """這一席這一回合被跳過：發 `turn_skipped`（公開）並把計數 −1。回事件或 None。"""
        rem = self.death_skip_of(seat)
        if rem <= 0:
            return None
        return self._emit("turn_skipped", public=True, seat=seat, remaining=max(0, rem - 1),
                          text=self.phrase("death.turn_skipped", remaining=max(0, rem - 1)))
