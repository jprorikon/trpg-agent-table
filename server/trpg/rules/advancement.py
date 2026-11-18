#!/usr/bin/env python3
"""**L1｜經驗與等級**（M4，2026-10-07）：等級表（`kind=levels`）＋ `exp_gained`／`level_up`。

機制住這一支；**內容住包**（`packs/<id>/tables/levels.json`，例：模組 `hyakki_tower` 的 `packs/rules.core/`）：

| 事情 | 規則 |
|---|---|
| 門檻 | 表裡的 `need`＝**累計**經驗；升到該級＝「`need` ≤ 現在經驗」的最高那一列 |
| 成長 | `gains`＝`max_hp`（加上限，同時補同值血）／`attrs`（**基準屬性**，永久；與 `dsl` 的加值層分開）／`resources`（通用資源） |
| 事件溯源 | `exp_gained` 帶 `total`、`level_up` 帶 `after`（max_hp／hp／attrs／resources 的**結果值**）⇒ 回放不必重算 |
| 開局 | 有啟用本機制時，`join` 之後自動給第 1 級（起始資源由表的那一列出）——沒啟用＝**一個事件都不多發** |

沒啟用帶等級表的包 ⇒ `exp`／`level` 永遠是空的、`level_of()` 一律回 1（legacy 零變更）。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

TABLE_KIND = "levels"


class AdvancementError(ValueError):
    """等級表的內容／參數不合法（**當場就叫**）。"""


def validate(rows: Any) -> List[str]:
    e: List[str] = []
    if not isinstance(rows, list):
        return ["rows 要是陣列"]
    seen = set()
    for i, row in enumerate(rows):
        where = f"rows[{i}]"
        if not isinstance(row, dict):
            e.append(f"{where}: 要是物件")
            continue
        if not isinstance(row.get("level"), int) or int(row["level"]) < 1:
            e.append(f"{where}.level: 要是 ≥1 的整數")
        if not isinstance(row.get("need"), (int, float)) or int(row["need"]) < 0:
            e.append(f"{where}.need: 要是 ≥0 的數字（累計經驗門檻）")
        lv = row.get("level")
        if lv in seen:
            e.append(f"{where}: level 重複（{lv}）")
        seen.add(lv)
        g = row.get("gains") or {}
        if g and not isinstance(g, dict):
            e.append(f"{where}.gains: 要是物件")
        elif g:
            for k in g:
                if k not in ("max_hp", "attrs", "resources"):
                    e.append(f"{where}.gains.{k}: 只能是 max_hp／attrs／resources")
    return e


def load(paths: Any) -> Dict[str, Any]:
    """讀 `packs.tables` 裡 `kind=levels` 的表（**一張桌只准一張**：兩張＝當場叫）。"""
    got: Optional[Dict[str, Any]] = None
    for p in (paths or []):
        if not p or not os.path.exists(p):
            continue
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or data.get("kind") != TABLE_KIND:
            continue
        rows = data.get("rows") or []
        errs = validate(rows)
        if errs:
            raise AdvancementError(f"{os.path.basename(p)}: " + "；".join(errs[:8]))
        if got is not None:
            raise AdvancementError(f"兩張等級表（{os.path.basename(p)}）——一張桌只能有一張")
        got = {"rows": sorted(rows, key=lambda r: int(r["level"])),
               "base": int(data.get("base") or 1)}
    return got or {"rows": [], "base": 1}


class AdvancementMixin:
    """等級表 ＋ 經驗（掛在 `Table` 上）。"""

    # ------------------------------------------------------------------ 載入
    def advancement_load(self, tables: Any = None) -> None:
        data = load(tables)
        self.levels_table: List[Dict[str, Any]] = data["rows"]
        self.levels_base: int = int(data["base"])
        self._apply_level_curve()

    # ------------------------------------------------------------------ 讀
    def _apply_level_curve(self) -> None:
        """★**每次升級所需的經驗跟著等級走**（設計者 2026-10-07）：
        旋鈕 `rules.levels = {exp_base, exp_step}` ⇒ 從第 L 級升到 L+1 級要 `exp_base + exp_step×(L−1)`，
        累計寫回每一列的 `need`（`need(L)`＝前面所有 Δ 的和；第 1 級＝0）。
        **沒有這顆旋鈕 ⇒ 一字不動，照表裡原本的 `need`**（legacy／自訂表都安全）。
        形狀不對（負數／非數字）⇒ 當場叫（別讓它靜默算歪）。"""
        cv = dict(((getattr(self, "rules", None) or {}).get("levels") or {}))
        if not cv or ("exp_base" not in cv and "exp_step" not in cv):
            return
        def _n(key, dflt):
            v = cv.get(key, dflt)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or int(v) < 0:
                raise ValueError(f"rules.levels.{key} 要是 ≥0 的數字（現在：{v!r}）")
            return int(v)
        base = _n("exp_base", 30)
        step = _n("exp_step", 0)
        rows = sorted((getattr(self, "levels_table", None) or []), key=lambda r: int(r["level"]))
        total = 0
        for i, r in enumerate(rows):
            if i == 0:
                r["need"] = 0                     # 起始級＝0
                continue
            total += base + step * (i - 1)        # Δ(L)＝從上一級升到這一級所需
            r["need"] = total
        self.levels_curve = {"exp_base": base, "exp_step": step}

    def level_of(self, seat: str) -> int:
        return int((self.state.get("level") or {}).get(seat) or self.levels_base)

    def exp_of(self, seat: str) -> int:
        return int((self.state.get("exp") or {}).get(seat) or 0)

    def level_row(self, level: int) -> Dict[str, Any]:
        for r in (getattr(self, "levels_table", None) or []):
            if int(r["level"]) == int(level):
                return r
        return {}

    def level_for_exp(self, exp: int) -> int:
        """這個累計經驗該是幾級（表裡 `need` ≤ exp 的最高一級；沒有表＝1）。"""
        lv = int(getattr(self, "levels_base", 1) or 1)
        for r in (getattr(self, "levels_table", None) or []):
            if int(exp) >= int(r["need"]):
                lv = max(lv, int(r["level"]))
        return lv

    def progression_view(self, seat: str) -> Dict[str, Any]:
        rows = getattr(self, "levels_table", None) or []
        lv = self.level_of(seat)
        nxt = next((r for r in rows if int(r["level"]) > lv), None)
        return {"level": lv, "exp": self.exp_of(seat),
                "next_need": (int(nxt["need"]) if nxt else None)}

    # ------------------------------------------------------------------ 折算
    def advancement_apply(self, ev: Dict[str, Any]) -> None:
        """把 `exp_gained`／`level_up` 折進 state（**只用事件裡的值**，不重算）。"""
        t = ev.get("type")
        seat = ev.get("seat")
        if not seat:
            return
        if t == "exp_gained":
            self.state.setdefault("exp", {})[seat] = int(ev.get("total") or 0)
        elif t == "level_up":
            self.state.setdefault("level", {})[seat] = int(ev.get("level") or self.levels_base)
            s = (self.state.get("seats") or {}).get(seat)
            after = ev.get("after") or {}
            if s:
                if after.get("max_hp") is not None:
                    s["max_hp"] = int(after["max_hp"])
                if after.get("hp") is not None:
                    s["hp"] = int(after["hp"])
                if after.get("attrs"):
                    s["attrs"] = dict(after["attrs"])
            if after.get("resources"):
                self.state.setdefault("resources", {})[seat] = {
                    str(k): int(v) for k, v in after["resources"].items()}

    # ------------------------------------------------------------------ 寫
    def _gains_after(self, seat: str, gains: Dict[str, Any]) -> Dict[str, Any]:
        """算出成長**之後**的結果值（純計算；真的落地靠事件折出）。"""
        s = self._require_seat(seat)
        attrs = dict(s.get("attrs") or {})
        for k, v in ((gains or {}).get("attrs") or {}).items():
            attrs[str(k)] = int(attrs.get(str(k), 0)) + int(v)
        add_hp = int((gains or {}).get("max_hp") or 0)
        max_hp = int(s.get("max_hp") or 0) + add_hp
        hp = min(max_hp, int(s.get("hp") or 0) + max(0, add_hp))
        res = dict(self.resources_of(seat))
        for k, v in ((gains or {}).get("resources") or {}).items():
            res[str(k)] = max(0, int(res.get(str(k), 0)) + int(v))
        return {"max_hp": max_hp, "hp": hp, "attrs": attrs, "resources": res}

    def _level_up_to(self, seat: str, level: int, *, by: str = "exp",
                     cur: Optional[int] = None) -> List[Dict[str, Any]]:
        """從 `cur` 升到 `level`（**逐級發事件**，每級都帶自己的 after ⇒ 回放不必重算）。

        `cur=None` ⇒ 用現在的等級；開局那次要傳 `cur=0`（還沒給過級，`level_of` 會回 base ⇒
        直接拿它當「現在幾級」會把第 1 級吃掉、一個事件都發不出來）。
        """
        out: List[Dict[str, Any]] = []
        if cur is None:
            cur = self.level_of(seat)
        for r in (getattr(self, "levels_table", None) or []):
            lv = int(r["level"])
            if lv <= cur or lv > int(level):
                continue
            gains = r.get("gains") or {}
            after = self._gains_after(seat, gains)
            ev = self._emit("level_up", public=True, seat=seat, level=lv, gains=gains,
                            after=after, note=r.get("note"), by=by)
            out.append(ev)
        return out

    def carry_over(self, *, seat: str, level: int, exp: int, by: str = "account") -> Dict[str, Any]:
        """**開局把「局外」的成長帶進來**（account×module×char 的實例 → 這一局）。

        做法：把「該等級的累計門檻 ＋ 實例的 exp」換算成一次 `exp_gain`（附 `reason="carry_over"`）——
        **完全走既有的事件路**（可回放、可審計；不是偷偷改 state）。沒有等級表的桌＝no-op。

        ⚠ 2026-10-07 修：原本寫 `want = need(level) + exp` ⇒ **重複計算**（`level_for_exp` 與
        `need` 都是**累計**口徑：第 3 級＝80、第 4 級＝150…）。實測「實例 (L4, exp100)」會被帶成
        **L5**（150+100=250 ≥ 240）。正解＝取兩者的**大者**（等級當下限、exp 當累計）：
        (L3,80)⇒80→L3、(L3,0)⇒80→L3、(L1,6)⇒6→L1——**現役資料全部是 L1/exp 6 或 0 ⇒ 行為不變**。
        """
        tbl = getattr(self, "levels_table", None) or []
        if not tbl or int(level or 0) <= 1 and not int(exp or 0):
            return {"carried": False}
        need = 0
        for r in tbl:
            if int(r.get("level") or 0) <= int(level or 1):
                need = max(need, int(r.get("need") or 0))
        want = max(need, max(0, int(exp or 0)))          # 累計口徑：取大者，不是相加
        have = int(self.exp_of(seat))
        if want <= have:
            return {"carried": False, "exp": have}
        return {"carried": True, "exp_gain": self.exp_gain(seat=seat, amount=want - have,
                                                           reason="carry_over", by=by)}

    def exp_gain(self, *, seat: str, amount: int, reason: Optional[str] = None,
                 by: str = "gm") -> Dict[str, Any]:
        """給經驗（GM 當場裁定／擊破小怪／劇本效果都走這條）；順手判升級。"""
        with self.lock:
            if not (getattr(self, "levels_table", None) or []):
                raise AdvancementError("這張桌沒有啟用帶等級表的機制包"
                                       "（`config.packs` 裡加 rules.core 之類）")
            self._require_seat(seat)
            baseline: List[Dict[str, Any]] = []
            if str(seat) not in (self.state.get("level") or {}):
                # 這一局現在才啟用規則書（或是加入時還沒有等級表）⇒ 先補「開局第 1 級」
                baseline = self._level_up_to(seat, int(getattr(self, "levels_base", 1) or 1),
                                             by="join", cur=0)
            amt = int(amount or 0)
            total = max(0, self.exp_of(seat) + amt)
            self._emit("exp_gained", public=True, seat=seat, amount=amt, total=total,
                       reason=reason, by=by)
            ups = self._level_up_to(seat, self.level_for_exp(total), by=by)
            return {"exp": total, "level": self.level_of(seat),
                    "level_ups": [e.get("level") for e in (baseline + ups)]}

    def advancement_baseline(self, seat: str) -> Optional[Dict[str, Any]]:
        """開局給第 1 級（只有啟用等級表、而且表上有那一列時才發事件）。"""
        if not (getattr(self, "levels_table", None) or []):
            return None
        base = int(getattr(self, "levels_base", 1) or 1)
        if base in (self.state.get("level") or {}):
            return None
        ups = self._level_up_to(seat, base, by="join", cur=0)
        return ups[-1] if ups else None
