#!/usr/bin/env python3
"""**L1｜地塊表（lands）**——「走到這一格會發生什麼」變成資料，不再寫死成 per-seat 觸發。

為什麼要有這一支：棋盤的落格效果（起始點回血、毒沼、商店過路費、事件格…）原本只有兩條路：
① 寫進 `board_act`（＝只能是引擎作者改）；② 用劇本的 `on:"move"` 觸發，但觸發的 effect
只能指到**寫死的席位 id**（`Scenario.fire()` 的 ctx 沒有 `me`）⇒ 三個人就要抄三條，加人再加。

這一支把「哪一格、什麼效果」變成表（`kind=lands`），**效果在 `me`（＝剛落地的那個人）身上跑**：

```jsonc
// packs/<包>/tables/lands.json
{"kind": "lands",
 "rows": [{"id": "heal_spot", "name_jp": "回復格", "name_cn": "回复格",
           "stop": [{"heal": {"seat": "me", "bonus": 2}}],   // 停留＝落地
           "pass": [],                                        // 經過（多格移動時用；目前只有停留）
           "params": {"fee": 3},                              // 給 effect 取用的係數（作者自己寫進 effect）
           "note": "起始点／保障点"}]}
```

`board.json` 的節點加 `"land": "heal_spot"`（不寫＝這一格沒有落格效果，行為一字不變）。
落地時引擎先發一筆 **`land_stop`**（不公開的內部事件，帶 `seat`／`node`／`land`），
再用**劇本自己的 effect applier** 跑 `stop` 裡的效果——因此：

* `seat: "me"` ＝剛落地的人（`ctx["me"]`），`seat: "target"` 也可以（本支沒有 target，會當成沒有）；
* 效果只准用**這一桌啟用的包宣告過的**（`allowed_effects`）；表裡寫了沒人實作的 ⇒ 載入時就叫；
* `land_stop` 本身是事件 ⇒ 回放一致；效果自己發的事件照舊（傷害／治療／碎片…）；
* **沒有 lands 表／節點沒有 `land`** ⇒ 一個事件都不多發（legacy 零變更）。

`pass` 是**保留欄位**：目前引擎只有「走一格」，沒有「經過中間格」，所以 `pass` 不會被觸發；
等之後做多格移動（report 08 的 M3）再開。寫了不會報錯（作者可以先把未來的效果寫好），但目前不會被觸發。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

from trpg.rules import scenario

#: 表在包裡的形狀（`{"kind": "lands", "rows": [...]}`）
TABLE_KIND = "lands"

#: 一列認得的欄位
ROW_KEYS = ("id", "name_jp", "name_cn", "stop", "pass", "params", "note")

#: 兩個相位（目前只消費 stop）
PHASES = ("stop", "pass")


class LandError(ValueError):
    """地塊表不合法（**載入時就叫**，不拖到跑團中間）。"""


def _check_effects(effs: Any, where: str) -> List[str]:
    e: List[str] = []
    if effs is None:
        return e
    if not isinstance(effs, list):
        return [f"{where}: 要是 effect 的陣列"]
    for i, eff in enumerate(effs):
        if not isinstance(eff, dict) or len(eff) != 1:
            e.append(f"{where}[{i}]: 要是「一個 key 的物件」（例 {{'heal': {{...}}}}）")
            continue
        kind = next(iter(eff))
        if kind not in scenario.EFFECT_KINDS:
            e.append(f"{where}[{i}]: 不認識的 effect {kind!r}（內核有 {list(scenario.EFFECT_KINDS)}）")
        elif not isinstance(eff[kind], dict):
            e.append(f"{where}[{i}].{kind}: 要是物件")
    return e


def validate(rows: Any) -> List[str]:
    """表內容的基本形狀（作者工具與載入共用同一套判據）。"""
    e: List[str] = []
    if not isinstance(rows, list):
        return ["rows 要是陣列"]
    seen = set()
    for i, row in enumerate(rows):
        where = f"rows[{i}]"
        if not isinstance(row, dict):
            e.append(f"{where}: 要是物件")
            continue
        rid = str(row.get("id") or "").strip()
        where = f"rows[{i}]({rid or '?'})"
        if not rid:
            e.append(f"{where}: 每個地塊要有 id")
        elif rid in seen:
            e.append(f"{where}: id 重複")
        seen.add(rid)
        for k in row:
            if k not in ROW_KEYS:
                e.append(f"{where}: 不認識的欄位 {k!r}（只有 {'／'.join(ROW_KEYS)}）")
        for phase in PHASES:
            e += _check_effects(row.get(phase), f"{where}.{phase}")
        params = row.get("params")
        if params is not None and not isinstance(params, dict):
            e.append(f"{where}.params: 要是物件（係數；effect 自己取用）")
    return e


def load(paths: Any) -> Dict[str, Dict[str, Any]]:
    """讀包帶的所有 `kind=lands` 表，合成 `{id: row}`（重複 id 且內容不同＝當場叫）。"""
    out: Dict[str, Dict[str, Any]] = {}
    for p in (paths or []):
        if not p or not os.path.exists(p):
            continue
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or data.get("kind") != TABLE_KIND:
            continue                                    # 不是地塊表：與這一支無關（別人的表）
        rows = data.get("rows") or []
        errs = validate(rows)
        if errs:
            raise LandError(f"{os.path.basename(p)}: " + "；".join(errs[:8]))
        for row in rows:
            rid = str(row["id"])
            if rid in out and out[rid] != row:
                raise LandError(f"地塊 {rid!r} 在兩張表裡寫得不一樣（{p}）")
            out[rid] = dict(row)
    return out


class LandsMixin:
    """地塊表 ＋ 落格派發（掛在 `Table` 上；沒有表＝全部 no-op）。"""

    # ------------------------------------------------------------------ 載入
    def lands_init(self, tables: Any = None) -> None:
        """載入地塊表；**順便驗節點的 `land` 指得到、effect 是這一桌允許的**（載入時就叫）。"""
        self.lands_table: Dict[str, Dict[str, Any]] = load(tables)
        allowed = tuple(getattr(self, "allowed_effects", ()) or ())
        for rid, row in (self.lands_table or {}).items():
            for phase in PHASES:
                for eff in (row.get(phase) or []):
                    kind = next(iter(eff))
                    if allowed and kind not in allowed:
                        raise LandError(
                            f"地塊 {rid!r} 的 {phase} 用到 {kind!r}，但這張桌啟用的包沒有宣告它"
                            f"（可用：{list(allowed)}）——把帶這個 effect 的包列進 config.packs")
        # 節點 → 地塊：寫錯 id 就是作者打錯字，載入時指給他看
        for nid, n in (getattr(self, "nodes", {}) or {}).items():
            lid = (n or {}).get("land")
            if lid and str(lid) not in (self.lands_table or {}):
                raise LandError(f"board.json 節點 {nid!r} 的 land={lid!r} 不存在"
                                f"（地塊表有：{sorted(self.lands_table or {})}）")

    # ------------------------------------------------------------------ 讀
    def land_row(self, node: Optional[str]) -> Optional[Dict[str, Any]]:
        """這一格的地塊列（沒有＝None）。"""
        if not node:
            return None
        n = (getattr(self, "nodes", {}) or {}).get(str(node)) or {}
        lid = n.get("land")
        if not lid:
            return None
        return (getattr(self, "lands_table", None) or {}).get(str(lid))

    def land_effects(self, node: Optional[str], phase: str = "stop") -> List[Dict[str, Any]]:
        row = self.land_row(node)
        return list((row or {}).get(phase) or []) if row else []

    # ------------------------------------------------------------------ 派發
    def land_stop(self, *, seat: str, node: str) -> List[Dict[str, Any]]:
        """有人**停在**這一格 ⇒ 跑這一格的地塊效果（`me`＝這個人）。沒有地塊＝回空陣列。"""
        row = self.land_row(node)
        if not row:
            return []
        ev = self._emit("land_stop", public=False, seat=str(seat), node=str(node),
                        land=str(row["id"]))
        mod = getattr(self, "module", None)
        if mod is not None:
            ctx = mod.ctx(self, seat=str(seat), event=ev)
        else:                                   # 沒有劇本規則資料（legacy）：借空殼取同一個 ctx
            ctx = scenario.Scenario({"meta": {"id": "_inline"}}).ctx(self, seat=str(seat), event=ev)
        out = list(scenario.effects_apply(self, row.get("stop") or [], ctx, by="land"))
        return [ev] + out
