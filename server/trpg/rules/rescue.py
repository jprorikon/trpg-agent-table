#!/usr/bin/env python3
"""**L1｜救起／救護**（★1，2026-10-07 設計者拍）：把「倒地的人」拉回來。

半合作最容易出戲的一格：有人 `down` 之後，桌上原本**沒有任何救人的動作**（只能等治療法術或 GM 放水）。
這一支把「救人」做成**一個檢定＋一張表**：

```json
{"kind": "rescue",
 "rows": [{"id": "first_aid", "name_jp": "介抱", "name_cn": "急救",
           "attr": "wil", "prof": 0, "dc": 12,        // 與 check／cast 同一條檢定算式
           "heal": {"die": 6, "bonus": 2},            // 成功 ⇒ 走引擎 roll_heal（骰值進事件）
           "require_item": "heal_pack",               // 選填：要身上有這件道具（★2 的道具表 id）
           "cost": {"resource": "mana", "value": 1},   // 選填：先扣（不足＝當場拒）
           "risk": [{"condition_set": {"id": "wound_arm", "seat": "me"}}]}]}]   // 選填：失敗的代價
```

語意（與 `cast`／`check` 同一套口徑，**不新增第二套算式、不新增 state 欄位**）：

| 步驟 | 行為 |
|---|---|
| 前置 | 目標要**真的倒地**（`status` 有 `down`）／不能救自己／目標席位要存在；自己倒地＝引擎既有閘擋住 |
| 這一桌用哪一條 | `config.rules.rescue`（旋鈕的權威）；省略＝表的第一列（**config 一律贏**） |
| 消耗**先扣** | `cost`（通用資源）與 `require_item`（道具 −1，走 `items.py`）；不足⇒當場叫、**不留事件** |
| 檢定 | `d20 ＋ 生效屬性×mult ＋ prof ＋ 條件修正 vs dc ＋ 導演檔位`（＝`check` 那條） |
| 成功 | 對目標 `roll_heal(die, bonus)` ⇒ hp 回到 0 以上時**引擎自己會解掉 `down`**（既有 reducer） |
| 失敗 | 表上有 `risk` 就套（走 `scenario.effects_apply`，與劇本觸發共用） |
| 事件 | `rescue_attempt`（**公開**，帶 rule／dc／success／骰序）＋ 既有的 `heal`／條件事件 |

⚠ 誠實：補不夠血 ⇒ **還是倒地**（回傳 `still_down: true`），不假裝救活了。
沒啟用帶救起表的包 ⇒ 表是空的 ⇒ 動作目錄不含 `rescue`、`rescue()` 當場叫（legacy 零變更）。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

#: 救起表在包裡的形狀（`{"kind": "rescue", "rows": [...]}`）
TABLE_KIND = "rescue"

#: 一列認得的欄位
ROW_KEYS = ("id", "name_jp", "name_cn", "attr", "prof", "dc", "heal",
            "require_item", "cost", "risk", "note")


class RescueError(ValueError):
    """救起表／救起動作不合法（載入或呼叫時就叫，不靜默）。"""


def validate(rows: Any) -> List[str]:
    """表內容合不合法（作者工具與 `test_rescue` 共用）。"""
    e: List[str] = []
    if not isinstance(rows, list) or not rows:
        return ["rows 要是**非空**陣列（至少要有一條救起規則）"]
    seen = set()
    for i, row in enumerate(rows):
        where = f"rows[{i}]"
        if not isinstance(row, dict):
            e.append(f"{where}: 要是物件")
            continue
        rid = str(row.get("id") or "").strip()
        where = f"rows[{i}]({rid or '?'})"
        if not rid:
            e.append(f"{where}: 每一條規則要有 id")
        elif rid in seen:
            e.append(f"{where}: id 重複")
        seen.add(rid)
        if not str(row.get("attr") or "").strip():
            e.append(f"{where}.attr: 要寫用哪個屬性（`config.rules.attributes` 裡的一項）")
        dc = row.get("dc", 12)
        if not isinstance(dc, int) or dc < 1:
            e.append(f"{where}.dc: 要是 >=1 的整數")
        if row.get("prof") is not None and not isinstance(row["prof"], int):
            e.append(f"{where}.prof: 要是整數（熟練加值）")
        heal = row.get("heal")
        if not isinstance(heal, dict):
            e.append(f"{where}.heal: 要是物件（{{die, bonus}}——成功後回復多少）")
        else:
            for k in heal:
                if k not in ("die", "bonus"):
                    e.append(f"{where}.heal: 不認識的鍵 {k!r}（只有 die／bonus）")
            if not isinstance(heal.get("die", 0), int) or not isinstance(heal.get("bonus", 0), int):
                e.append(f"{where}.heal.die／bonus: 要是整數")
        cost = row.get("cost")
        if cost is not None and (not isinstance(cost, dict)
                                 or set(cost) - {"resource", "value"}
                                 or not isinstance(cost.get("value", 0), int)):
            e.append(f"{where}.cost: 要是 {{resource, value}}（value 整數）")
        if row.get("require_item") is not None and not isinstance(row["require_item"], str):
            e.append(f"{where}.require_item: 要是道具 id（字串）")
        risk = row.get("risk")
        if risk is not None and not isinstance(risk, list):
            e.append(f"{where}.risk: 要是 effect 陣列（失敗時套什麼）")
        for k in ("name_jp", "name_cn", "note"):
            if row.get(k) is not None and not isinstance(row[k], str):
                e.append(f"{where}.{k}: 要是字串")
        for k in row:
            if k not in ROW_KEYS:
                e.append(f"{where}: 不認識的欄位 {k!r}（只有 {'／'.join(ROW_KEYS)}）")
    return e


def load(paths: Any) -> List[Dict[str, Any]]:
    """讀 `packs.tables` 裡所有 `kind=rescue` 的表（合成後**保持檔案順序**：第一列＝預設規則）。"""
    out: List[Dict[str, Any]] = []
    seen = set()
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
            raise RescueError(f"{os.path.basename(p)}: " + "；".join(errs[:8]))
        for row in rows:
            if str(row["id"]) in seen:
                raise RescueError(f"救起規則 id 重複：{row['id']}（{p}）")
            seen.add(str(row["id"]))
            out.append(dict(row))
    return out


class RescueMixin:
    """救起機制的實作。**表空 ⇒ 全部 no-op**（legacy 零變更）。"""

    # ------------------------------------------------------------------ 載入
    def rescue_init(self, tables: Any = None) -> None:
        self.rescue_table: List[Dict[str, Any]] = load(tables)

    # ------------------------------------------------------------------ 讀出口
    def rescue_ids(self) -> List[str]:
        return [str(r.get("id")) for r in (getattr(self, "rescue_table", None) or [])]

    def rescue_rule(self, rid: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """這一桌用哪一條救起規則：`config.rules.rescue`（旋鈕權威）＞表的第一列。"""
        rows = getattr(self, "rescue_table", None) or []
        if not rows:
            return None
        want = str((self.rules or {}).get("rescue") or rid or "").strip()
        if want:
            for row in rows:
                if str(row.get("id")) == want:
                    return row
            raise RescueError(f"config.rules.rescue 指的規則不存在：{want!r}（有的：{self.rescue_ids()}）")
        return rows[0]

    def down_seats(self) -> List[str]:
        """現在倒地的人（前端挑目標用；公開資訊）。"""
        return [sid for sid, s in (self.state.get("seats") or {}).items()
                if "down" in (s.get("status") or [])]

    # ------------------------------------------------------------------ 動作
    def rescue(self, *, seat: str, target: Optional[str] = None,
               note: Optional[str] = None) -> Dict[str, Any]:
        """救起一個倒地的人：先驗 → 扣消耗 → 檢定 → 成功才治療（不新增任何 state 欄位）。"""
        with self.lock:
            from trpg.core.errors import RuleError                                   # noqa: PLC0415
            rule = self.rescue_rule()
            if rule is None:
                raise RuleError("這張桌沒有啟用帶救起規則的機制包"
                                "（例：`config.packs` 裡加 wound——它帶 tables/rescue.json）")
            s = (self._require_seat(seat) if hasattr(self, "_require_seat")
                 else self.state["seats"].get(seat))
            if not s:
                raise RuleError(f"沒有這個席位：{seat}")
            tseat = str(target or "").strip()
            if not tseat:
                raise RuleError("救起要指定目標（target＝倒地的席位）")
            if tseat not in (self.state.get("seats") or {}):
                raise RuleError(f"沒有這個席位：{tseat}")
            if tseat == seat:
                raise RuleError("不能救自己（自己的傷自己起不來）")
            if "down" not in (self.state["seats"][tseat].get("status") or []):
                raise RuleError(f"{tseat} 沒有倒地，不需要救（救起只對 `down` 的人有效）")
            attr = str(rule.get("attr") or "")
            if attr not in (self.rules.get("attributes") or []):
                raise RuleError(f"救起表寫的屬性 {attr!r} 不在這張桌的屬性表"
                                f"（{list(self.rules.get('attributes') or [])}）")
            rid = str(rule.get("id"))
            # **全驗完才扣**（資源與道具一起驗；任何一項不足＝當場拒、不留半套消耗、不留幽靈事件）
            cost = rule.get("cost") or {}
            cres, cval = str(cost.get("resource") or ""), int(cost.get("value") or 0)
            need = str(rule.get("require_item") or "").strip()
            if cres and cval:
                have = int(self.resources_of(seat).get(cres, 0))
                if have < cval:
                    raise RuleError(f"救起要 {cres}×{cval}（{seat} 只有 {have}）")
            if need:
                have_it = int(self.items_of(seat).get(need, 0))
                if have_it < 1:
                    raise RuleError(f"救起要身上有「{need}」（數量 0）——`op=item` 或劇本 `item_grant` 先給")
            if cres and cval:
                self.add_resource(target=seat, name=cres, value=-cval, by="rescue")
            if need:
                self.item_take(target=seat, id=need, count=1, note=f"rescue:{rid}", by="rescue")
            # 檢定：與 check／cast 同一條算式（屬性×mult ＋ prof ＋ 條件修正；難度吃導演檔位）
            rdc = int(rule.get("dc") or 12) + self.difficulty_mod()
            mod = (self._mod(self.attrs_of(seat), attr) + int(rule.get("prof") or 0)
                   + self.conditions_mod(seat, "check"))
            roll = self._roll(seat=seat, kind="rescue", reason=f"rescue {rid}", mod=mod, dc=rdc)
            ok = bool(roll.get("success"))
            ev = self._emit("rescue_attempt", public=True, seat=seat, target=tseat, rule=rid,
                            name=str(rule.get("name_jp") or rid),
                            roll_seq=roll.get("roll_seq"), success=ok, dc=rdc, note=note)
            out: Dict[str, Any] = {"rule": rid, "roll": roll, "success": ok, "target": tseat,
                                   "seq": ev.get("seq"), "healed": 0, "still_down": True}
            if ok:
                heal = rule.get("heal") or {}
                res = self.roll_heal(seat=tseat, die=int(heal.get("die") or 0),
                                     bonus=int(heal.get("bonus") or 0), note=f"rescue:{rid}")
                out["healed"] = int(res.get("heal") or 0)          # `roll_heal` 回的是 {heal, hp_after}
                out["target_hp"] = int(self.state["seats"][tseat].get("hp") or 0)
                out["still_down"] = "down" in (self.state["seats"][tseat].get("status") or [])
            else:
                risk = rule.get("risk") or []
                if risk:
                    from trpg.rules import scenario as scenario                                            # noqa: PLC0415
                    out["risk"] = [e.get("type") for e in scenario.effects_apply(
                        self, risk, {"me": {"seat": seat}, "target": {"seat": tseat}}, by="rescue")]
                else:
                    out["risk"] = []
            return out
