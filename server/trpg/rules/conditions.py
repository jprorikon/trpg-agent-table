#!/usr/bin/env python3
"""**L1｜條件（部位受傷／異常狀態）**——機制在內核、內容在包的表（`conditions.json`）。

第一個「真包」的內核側：`packs/wound/` 只**宣告與填表**，實作在這裡。

## 機制（這一支）

| 誰 | 做什麼 |
|---|---|
| `load(paths)` | 讀包帶的表（**只認 `{"kind": "conditions", ...}`**）；schema 不合法＝載入時就叫 |
| `conditions_apply(ev)` | 狀態折算：`condition_set`（加／移）、`condition_tick`（傷害與倒數） |
| `conditions_tick(seat)` | **每回合結算**（`turn_started` 時由 L0 呼）：該席每個條件跑一次 |
| `conditions_mod(seat, kind)` | **影響維度**（`<kind>_mod`）：`check`／`attack` 進 d20 的 mod、`move_dc` 進移動 DC、`memory_drain` 進塔內記憶消耗、`act_slots`／`say_slots` 加減額度 |
| `conditions_mod(seat, kind)` | 判定修正：把條件的 `check_mod`／`attack_mod` 加進 d20 的 mod |
| `conditions_view(seat)` | 給投影用的（誰身上有什麼、還剩幾回合、扣多少） |
| `set_condition(...)` | GM 當場投／治（`/api/gm op=condition`、MCP `table_gm`） |

## 內容（包）

```json
{"kind": "conditions",
 "rows": [{"id": "bleed", "name_jp": "出血", "name_cn": "出血",
           "check_mod": -1, "attack_mod": 0,
           "tick": {"damage": 1},        // 每回合固定 1；也可 {"damage": 1, "die": 4} 加骰
           "turns": 3,                    // 幾回合後自己好（省略＝不會自己好）
           "text_onset": "condition.bleed_onset",   // 句庫鍵（包自己的 text.json）
           "text_tick": "condition.bleed_tick",
           "text_end":  "condition.bleed_end"}]}
```

係數一律住包的 `params`（作者也能自己寫觸發：`{"var": "pack.params.<鍵>"}`）。

## 紀律

* **不讀時鐘／不碰網路**；只在載入時讀表檔（`engine.py` 不准有 `open(`，所以讀檔住這一支）。
  傷害的骰**一律種子化**（`random.Random(f"{seed}:condition:…")`，與 board／semic 同一套），
  而且**值寫進事件**⇒ 回放不必重算（`test_layers` H 組咬「裸 random 呼叫」）。
* **沒有啟用帶條件表的包 ⇒ 表是空的 ⇒ 一個事件都不會多發**（legacy 零變更；`test_conditions` W 組咬住）。
* 事件一律由 `_emit` 走（事件溯源）⇒ `op=replay` 一致；**不准直接改 state**。
"""
from __future__ import annotations

import json
import os
import random
from typing import Any, Dict, List, Mapping, Optional, Tuple

from trpg.engine import presentation as presentation


#: 條件表在包裡的形狀（`{"kind": "conditions", "rows": [...]}`）
TABLE_KIND = "conditions"

#: `tick` 區塊認得的鍵（`resource`＝每回合扣／給通用資源，例 `{"name": "stamina", "value": -1}`）
TICK_KEYS: Tuple[str, ...] = ("damage", "die", "resource")

#: **影響維度**（表上的 `<kind>_mod` 欄位）。每一維都必須有一個消費處——寫了沒消費＝等於沒寫：
#:   `check`／`attack`     → d20 的 mod（引擎的檢定與攻擊）
#:   `move_dc`             → 棋盤移動檢定的 DC（`board.board_act` 的荒野道）
#:   `memory_drain`        → 塔內每回合的記憶消耗（`board` 的 `tower_turn`）
#:   `act_slots`/`say_slots` → 這一回合／這一回合的行動格與發言格（`semic._round_limit`）
MOD_KINDS: Tuple[str, ...] = ("check", "attack", "move_dc", "memory_drain",
                              "act_slots", "say_slots")


class ConditionError(ValueError):
    """條件表／條件操作不合法（載入或呼叫時就叫，不靜默）。"""


# ---------------------------------------------------------------------------
# 表：載入與驗證
# ---------------------------------------------------------------------------


def load(paths: Any, bank: Optional[Mapping[str, str]] = None) -> Dict[str, Dict[str, Any]]:
    """讀 `packs.tables` 裡所有 `kind=conditions` 的表，合成 `{id: entry}`（重複 id＝當場叫）。

    `bank`＝這一桌的句庫（`packs.text_bank_for`）；文案鍵要在裡面。沒給＝內核預設＋表旁邊包的 `text.json`。
    """
    out: Dict[str, Dict[str, Any]] = {}
    for p in (paths or []):
        if not p or not os.path.exists(p):
            continue
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or data.get("kind") != TABLE_KIND:
            continue                                    # 不是條件表：與這一支無關（別人的表）
        rows = data.get("rows") or []
        errs = validate(rows, bank if bank is not None else _sibling_bank(p))
        if errs:
            raise ConditionError(f"{os.path.basename(p)}: " + "；".join(errs[:8]))
        for row in rows:
            cid = str(row["id"])
            if cid in out and out[cid] != row:
                raise ConditionError(f"條件 {cid!r} 在兩張表裡寫得不一樣（{p}）")
            out[cid] = dict(row)
    return out


def _sibling_bank(table_path: str) -> Dict[str, str]:
    """沒給句庫時的退路：內核預設 ＋ 這張表所屬包的 `text.json`（`<包>/tables/x.json` ⇒ `<包>/text.json`）。"""
    p = os.path.join(os.path.dirname(os.path.dirname(table_path)), "text.json")
    extra: Dict[str, str] = {}
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        extra = {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
    return presentation.make_bank(extra)


def validate(rows: Any, bank: Optional[Mapping[str, str]] = None) -> List[str]:
    """表內容合不合法（`packs.load` 之前不會跑到這裡；作者工具與 `test_conditions` 共用）。"""
    e: List[str] = []
    if not isinstance(rows, list):
        return ["rows 要是陣列"]
    seen = set()
    for i, row in enumerate(rows):
        where = f"rows[{i}]"
        if not isinstance(row, dict):
            e.append(f"{where}: 要是物件")
            continue
        cid = str(row.get("id") or "").strip()
        where = f"rows[{i}]({cid or '?'})"
        if not cid:
            e.append(f"{where}: 每個條件要有 id")
        elif cid in seen:
            e.append(f"{where}: id 重複")
        seen.add(cid)
        for k in MOD_KINDS:
            v = row.get(k + "_mod")
            if v is not None and not isinstance(v, int):
                e.append(f"{where}.{k}_mod: 要是整數（負＝不利）")
        tick = row.get("tick")
        if tick is not None:
            if not isinstance(tick, dict):
                e.append(f"{where}.tick: 要是物件（{{damage, die}}）")
            else:
                for k in tick:
                    if k not in TICK_KEYS:
                        e.append(f"{where}.tick: 不認識的鍵 {k!r}（只有 {'/'.join(TICK_KEYS)}）")
                d = tick.get("damage")
                if d is not None and (not isinstance(d, int) or d < 0):
                    e.append(f"{where}.tick.damage: 要是 >=0 的整數")
                die = tick.get("die")
                if die is not None and (not isinstance(die, int) or die < 2):
                    e.append(f"{where}.tick.die: 要是 >=2 的整數（骰面）")
                rs = tick.get("resource")
                if rs is not None and (not isinstance(rs, dict)
                                       or not str(rs.get("name") or "").strip()
                                       or not isinstance(rs.get("value"), int)):
                    e.append(f"{where}.tick.resource: 要是 {{name: 資源名, value: 整數}}"
                             "（每回合扣／給通用資源，例 {\"name\": \"stamina\", \"value\": -1}）")
        turns = row.get("turns")
        if turns is not None and (not isinstance(turns, int) or turns < 1):
            e.append(f"{where}.turns: 要是 >=1 的整數（幾回合後自己好）")
        for k in ("text_onset", "text_tick", "text_end"):
            key = row.get(k)
            if not key:
                continue
            if str(key) not in (bank if bank is not None else presentation.TEXT):
                e.append(f"{where}.{k}: 句庫沒有 {key!r}（放進包的 text.json 並在 "
                         f"provides.text_keys 裡宣告）")
    return e


# ---------------------------------------------------------------------------
# 機制（Mixin；與 board／semic 同一種寫法）
# ---------------------------------------------------------------------------


class ConditionsMixin:
    """條件機制的實作。**表是空的 ⇒ 全部方法都是 no-op**（legacy 零變更）。"""

    # ------------------------------------------------------------------ 載入
    def conditions_init(self, home: str, cfg: Dict[str, Any], tables: Any = None) -> None:
        """載入條件表（`tables` 來自 L1 的 `PackView.tables`）。"""
        self.condition_table: Dict[str, Dict[str, Any]] = load(tables, getattr(self, "text_bank", None))

    # ------------------------------------------------------------------ 折算
    def conditions_apply(self, ev: Dict[str, Any]) -> None:
        """把 `condition_set`／`condition_tick` 折進 `state["conditions"]`（事件溯源）。"""
        st = self.state.get("conditions")
        if st is None:
            return
        t = ev.get("type")
        if t == "condition_set":
            seat, cid = ev.get("seat"), str(ev.get("id") or "")
            if not seat or not cid or seat not in self.state.get("seats", {}):
                return
            if ev.get("value", True):
                entry = (self.condition_table or {}).get(cid) or {}
                turns = ev.get("turns") if ev.get("turns") is not None else entry.get("turns")
                st.setdefault(seat, {})[cid] = {
                    "since_seq": int(ev.get("seq") or 0),
                    "by": ev.get("by"),
                    "turns_left": (int(turns) if turns is not None else None),
                    "note": ev.get("note"),
                }
            else:
                (st.get(seat) or {}).pop(cid, None)
                if not st.get(seat):
                    st.pop(seat, None)
        elif t == "condition_tick":
            seat, cid = ev.get("seat"), str(ev.get("id") or "")
            cur = (st.get(seat) or {}).get(cid)
            if cur is None:
                return
            left = ev.get("turns_left")
            if left is None:
                return
            if int(left) <= 0:
                (st.get(seat) or {}).pop(cid, None)
                if not st.get(seat):
                    st.pop(seat, None)
            else:
                cur["turns_left"] = int(left)

    # ------------------------------------------------------------------ 每回合結算
    def conditions_tick(self, seat: str) -> List[Dict[str, Any]]:
        """該席的條件各跑一次（`turn_started` 時由 L0 呼）：先報，再扣血，再倒數／到期。

        回傳這一輪產生的事件（呼叫端拿去排下一輪觸發）。**沒表／沒條件 ⇒ 回空**。
        """
        out: List[Dict[str, Any]] = []
        table = getattr(self, "condition_table", None) or {}
        if not table:
            return out
        cur = dict((self.state.get("conditions") or {}).get(seat) or {})
        if not cur:
            return out
        for cid in sorted(cur):
            entry = table.get(cid)
            if not entry:
                continue                       # 表裡沒有的（包被拔掉）→ 不結算、也不動它
            dmg = self._condition_damage(entry, seat, cid)
            left = cur[cid].get("turns_left")
            left2 = None if left is None else max(0, int(left) - 1)
            out.append(self._emit("condition_tick", public=True, seat=seat, id=cid, damage=dmg,
                                  turns_left=left2,
                                  text=self._condition_text(entry, "tick", seat=seat, damage=dmg)))
            if dmg > 0:
                # ★ 2026-10-20：扣血走唯一出口（`hp_after` 下限 0 只在 `damage_apply` 算）
                if self.state["seats"].get(seat):
                    out.append(self.damage_apply(seat=seat, amount=dmg,
                                                 src="condition:" + cid))
            rs = tick.get("resource") if isinstance(tick := (entry.get("tick") or {}), dict) else None
            if isinstance(rs, dict) and str(rs.get("name") or "").strip():
                # 每回合扣／給通用資源（例：中毒也在耗體力）——走 dsl 的同一條路（after 寫進事件）
                out.append(self.add_resource(target=seat, name=str(rs["name"]),
                                             value=int(rs.get("value") or 0),
                                             by="condition:" + cid))
            if left2 == 0:
                out.append(self._emit("condition_set", public=True, seat=seat, id=cid,
                                      value=False, reason="expired", by="condition",
                                      text=self._condition_text(entry, "end", seat=seat)))
        return out

    def _condition_damage(self, entry: Dict[str, Any], seat: str, cid: str) -> int:
        """這次結算的傷害（固定值 ＋ 可選的骰）。**值會寫進事件**⇒ 回放不必重算。"""
        tick = entry.get("tick") or {}
        dmg = int(tick.get("damage") or 0)
        die = tick.get("die")
        if die:
            seq = len(self.events) + 1
            rng = random.Random(f"{self.cfg.get('seed')}:condition:{seat}:{cid}:{seq}")
            dmg += rng.randint(1, int(die))
        return max(0, int(dmg))

    def _condition_text(self, entry: Dict[str, Any], when: str, *, seat: str,
                        damage: int = 0) -> Optional[str]:
        """句子的正本在包的 `text.json`（這一桌的句庫 `self.text_bank`）；沒寫就回 None（只有數值行）。"""
        key = entry.get("text_" + when)
        if not key:
            return None
        try:
            return presentation.text(str(key), getattr(self, "text_bank", None),
                                     seat=seat, name=self._name_of(seat),
                                     cond=entry.get("name_jp") or entry.get("name_cn") or entry.get("id"),
                                     damage=damage)
        except KeyError:
            return None                       # 表驗證會先擋；這裡再保守一次（不炸跑團）

    # ------------------------------------------------------------------ 判定修正
    def conditions_mod(self, seat: str, kind: str) -> int:
        """該席的條件對 d20 mod 的總修正（負＝不利）。沒表／沒條件 ⇒ 0。"""
        table = getattr(self, "condition_table", None) or {}
        cur = (self.state.get("conditions") or {}).get(seat) or {}
        total = 0
        for cid in cur:
            entry = table.get(cid)
            if entry:
                total += int(entry.get(kind + "_mod") or 0)
        return total

    # ------------------------------------------------------------------ 讀出口（給 L2 投影）
    def conditions_view(self, seat: str) -> Dict[str, Any]:
        """該席身上的條件（給投影用）：id → {名稱／剩幾回合／扣多少／說明}。空＝沒有。"""
        table = getattr(self, "condition_table", None) or {}
        cur = (self.state.get("conditions") or {}).get(seat) or {}
        out: Dict[str, Any] = {}
        for cid, info in cur.items():
            entry = table.get(cid) or {}
            tick = entry.get("tick") or {}
            out[cid] = {
                "id": cid,
                "name_jp": entry.get("name_jp") or cid,
                "name_cn": entry.get("name_cn") or entry.get("name_jp") or cid,
                "turns_left": info.get("turns_left"),
                "since_seq": info.get("since_seq"),
                "check_mod": int(entry.get("check_mod") or 0),
                "attack_mod": int(entry.get("attack_mod") or 0),
                "move_dc_mod": int(entry.get("move_dc_mod") or 0),
                "memory_drain_mod": int(entry.get("memory_drain_mod") or 0),
                "act_slots_mod": int(entry.get("act_slots_mod") or 0),
                "say_slots_mod": int(entry.get("say_slots_mod") or 0),
                "tick_damage": int(tick.get("damage") or 0),
                "tick_die": tick.get("die"),
                "tick_resource": tick.get("resource"),
                "note": entry.get("note") or info.get("note"),
            }
        return out

    def condition_ids(self) -> List[str]:
        """這張桌認得的條件 id（GM 工具面／自檢器用）。"""
        return sorted((getattr(self, "condition_table", None) or {}).keys())

    # ------------------------------------------------------------------ 對外操作
    def set_condition(self, *, target: str, id: str, value: bool = True,
                      turns: Optional[int] = None, note: Optional[str] = None,
                      by: str = "gm") -> Dict[str, Any]:
        """投／治一個條件（GM 當場裁定用；與 trigger 的 `condition_set` effect 同一條路）。"""
        if not (getattr(self, "condition_table", None) or {}):
            raise ConditionError("這張桌沒有啟用帶條件表的機制包（`config.packs` 裡加 wound 之類）")
        cid = str(id or "").strip()
        if cid not in self.condition_table:
            raise ConditionError(f"沒有這個條件：{cid!r}（有的：{self.condition_ids()}）")
        seat = str(target or "").strip()
        if seat not in (self.state.get("seats") or {}):
            raise ConditionError(f"沒有這個席位：{seat!r}")
        entry = self.condition_table[cid]
        kw: Dict[str, Any] = {"public": True, "seat": seat, "id": cid,
                              "value": bool(value), "by": by}
        if not value:
            kw["reason"] = "cleared"
            kw["text"] = self._condition_text(entry, "end", seat=seat)
        else:
            if turns is not None:
                kw["turns"] = int(turns)
            if note:
                kw["note"] = str(note)
            kw["text"] = self._condition_text(entry, "onset", seat=seat)
        return self._emit("condition_set", **kw)
