#!/usr/bin/env python3
"""**L1｜動作 DSL 的兩個原語**（M2，2026-10-07）：**屬性加值** 與 **通用資源**。

為什麼要這一支：`wound`（條件）能做的只有「檢定／攻擊加減值」與「每回合扣血」；
「裝備給 +2 agi」「法術位剩 1」「彈藥剩 3」這種**改數值／存資源**的表達力，內核原本沒有 ⇒
劇本只能靠工程寫死。這一支把兩個最小原語做成「資料可配」，包／劇本用 effect 就能用。

| 原語 | state | 事件 | 消費處 |
|---|---|---|---|
| 屬性加值 | `state["attr_mods"][seat][attr]` | `attr_mod_set`（`value` 是**絕對值**，0＝拿掉） | `Table.attrs_of(seat)`：檢定／攻擊／傷害／移動全部吃它 |
| 通用資源 | `state["resources"][seat][name]` | `resource_changed`（`mode=add/set`，**`after` 是結果值**） | AST（`me.resources.<名>`）／投影／GM |

紀律（`test_dsl.py` 咬住）：
  * **事件溯源**：兩個原語都只發事件，state 一律由事件折出來；`after`／`value` **寫進事件** ⇒
    回放不必重算（跟 `conditions.py` 的傷害值同一套）。
  * **不讀時鐘、不隨機、不碰網路、不碰別人的 state**（`test_layers` H9 那組的同一把尺）。
  * **legacy 零變更**：沒有用到這兩個原語的桌，state 多兩個空 dict、投影一個字都不變
    （投影只在「真的有值」時才加欄位）。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

#: 事件型別（由包宣告 `provides.events` 才進公開日誌白名單；`log_types` 只增不減）
DSL_EVENTS = ("attr_mod_set", "resource_changed")


class DslError(ValueError):
    """原語的參數不合法（**當場就叫**，不拖到跑團中間）。"""


class DslMixin:
    """屬性加值 ＋ 通用資源（掛在 `Table` 上）。"""

    # ------------------------------------------------------------------ 讀
    def attr_mods_of(self, seat: str) -> Dict[str, int]:
        """這一席的屬性加值（沒有＝空 dict）。"""
        return dict((self.state.get("attr_mods") or {}).get(seat) or {})

    def attrs_of(self, seat: str) -> Dict[str, int]:
        """**實際生效的屬性**＝基準屬性 ＋ 加值（檢定／攻擊／傷害／移動一律走這一支）。

        沒有加值時回的就是基準屬性本身（legacy 行為一字不變）。
        """
        s = (self.state.get("seats") or {}).get(seat) or {}
        base = dict(s.get("attrs") or {})
        for k, v in (self.attr_mods_of(seat) or {}).items():
            base[k] = int(base.get(k, 0)) + int(v)
        return base

    def resources_of(self, seat: str) -> Dict[str, int]:
        """這一席的通用資源（`{名: 值}`；沒有＝空 dict）。"""
        return dict((self.state.get("resources") or {}).get(seat) or {})

    # ------------------------------------------------------------------ 折算
    def dsl_apply(self, ev: Dict[str, Any]) -> None:
        """把 `attr_mod_set`／`resource_changed` 折進 state（事件溯源；不重算）。"""
        t = ev.get("type")
        if t == "attr_mod_set":
            st = self.state.setdefault("attr_mods", {})
            seat, attr = ev.get("seat"), str(ev.get("attr") or "")
            if not seat or not attr:
                return
            per = st.setdefault(seat, {})
            val = int(ev.get("value") or 0)
            if val:
                per[attr] = val
            else:
                per.pop(attr, None)          # 加值 0 ＝拿掉（別留空殼讓投影多欄位）
                if not per:
                    st.pop(seat, None)
        elif t == "resource_changed":
            st = self.state.setdefault("resources", {})
            seat, name = ev.get("seat"), str(ev.get("name") or "")
            if not seat or not name:
                return
            st.setdefault(seat, {})[name] = int(ev.get("after") or 0)

    # ------------------------------------------------------------------ 寫
    def set_attr_mod(self, *, target: str, attr: str, value: int,
                     note: Optional[str] = None, by: str = "gm") -> Dict[str, Any]:
        """設某席某屬性的**加值絕對值**（0＝拿掉）。GM 當場裁定與 trigger 走同一條路。"""
        seat = str(target or "").strip()
        if seat not in (self.state.get("seats") or {}):
            raise DslError(f"沒有這個席位：{seat!r}")
        a = str(attr or "").strip()
        if a not in (self.rules.get("attributes") or []):
            raise DslError(f"沒有這個屬性：{a!r}（這張桌有的是 {list(self.rules.get('attributes') or [])}）")
        try:
            val = int(value)
        except (TypeError, ValueError):
            raise DslError(f"attr_mod 的 value 要是整數（拿到 {value!r}）") from None
        kw: Dict[str, Any] = {"public": True, "seat": seat, "attr": a, "value": val, "by": by}
        if note:
            kw["note"] = str(note)
        return self._emit("attr_mod_set", **kw)

    def add_resource(self, *, target: str, name: str, value: int, mode: str = "add",
                     note: Optional[str] = None, by: str = "gm") -> Dict[str, Any]:
        """改某席的通用資源：`mode=add`（加減，下限 0）或 `set`（設成某值）。

        **結果值 `after` 寫進事件** ⇒ 回放不必重算（事件溯源）。
        """
        seat = str(target or "").strip()
        if seat not in (self.state.get("seats") or {}):
            raise DslError(f"沒有這個席位：{seat!r}")
        nm = str(name or "").strip()
        if not nm:
            raise DslError("resource 要有 name（例：spell_slot／ammo／stamina）")
        if mode not in ("add", "set"):
            raise DslError(f"resource 的 mode 只能是 add／set（拿到 {mode!r}）")
        try:
            val = int(value)
        except (TypeError, ValueError):
            raise DslError(f"resource 的 value 要是整數（拿到 {value!r}）") from None
        cur = int(self.resources_of(seat).get(nm, 0))
        after = val if mode == "set" else max(0, cur + val)
        kw: Dict[str, Any] = {"public": True, "seat": seat, "name": nm, "mode": mode,
                              "value": val, "after": after, "by": by}
        if note:
            kw["note"] = str(note)
        return self._emit("resource_changed", **kw)
