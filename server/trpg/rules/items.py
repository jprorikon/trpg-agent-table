#!/usr/bin/env python3
"""**L1｜道具／庫存**（★2，2026-10-07）：道具表（`kind=items`）＋ `use_item` 動作。

機制住這一支；**內容住包**（`packs/<id>/tables/items.json`）：

```json
{"kind": "items",
 "rows": [{"id": "heal_pack", "name_jp": "回復薬", "name_cn": "回复药",
           "stackable": true, "price": 3,
           "use": {"target": "self", "effects": [{"heal": {"seat": "me", "die": 6, "bonus": 2}}]},
           "note": "使い切りの回復薬。"}]}
```

| 誰 | 做什麼 |
|---|---|
| `load(paths)` | 讀包帶的表（**只認 `{"kind": "items"}`**）；schema 不合法＝載入時就叫 |
| `items_apply(ev)` | 折算：`item_changed`（`after` 寫進事件 ⇒ 回放不必重算） |
| `item_add` / `item_take` | 給／拿走（下限 0；**扣到負＝當場叫**） |
| `use_item(...)` | 動作：**先扣 1**（不足⇒拒），成功才套 `use.effects`（與技能同一個 applier） |
| `items_of` / `items_view` | 讀出口（投影用） |

紀律：沒啟用帶道具表的包 ⇒ `item_table` 是空的 ⇒ 動作目錄不會有 `use_item`、`inventory` 永遠空的、
投影不多任何欄位（**legacy 零變更**）；效果一律發事件（事件溯源），不准直接改 state。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from trpg.rules import scenario as scenario                     # L1 的 effect applier（與技能表共用同一份實作）

#: 道具表在包裡的形狀（`{"kind": "items", "rows": [...]}`）
TABLE_KIND = "items"

#: 一列認得的欄位
ROW_KEYS = ("id", "name_jp", "name_cn", "stackable", "price", "use", "note")

#: `use` 區塊認得的鍵
USE_KEYS = ("target", "effects", "note")

#: `use.target` 認得的值（決定前端要不要問「對誰用」）
USE_TARGETS = ("self", "foe", "none")


class ItemError(ValueError):
    """道具表／道具操作不合法（載入或呼叫時就叫，不靜默）。"""


# ---------------------------------------------------------------------------
# 表：載入與驗證
# ---------------------------------------------------------------------------


def validate(rows: Any) -> List[str]:
    """表內容合不合法（作者工具與 `test_items` 共用）。"""
    e: List[str] = []
    if not isinstance(rows, list):
        return ["rows 要是陣列"]
    seen = set()
    for i, row in enumerate(rows):
        where = f"rows[{i}]"
        if not isinstance(row, dict):
            e.append(f"{where}: 要是物件")
            continue
        iid = str(row.get("id") or "").strip()
        where = f"rows[{i}]({iid or '?'})"
        if not iid:
            e.append(f"{where}: 每個道具要有 id")
        elif iid in seen:
            e.append(f"{where}: id 重複")
        seen.add(iid)
        for k in ("name_jp", "name_cn", "note"):
            if row.get(k) is not None and not isinstance(row[k], str):
                e.append(f"{where}.{k}: 要是字串")
        if row.get("stackable") is not None and not isinstance(row["stackable"], bool):
            e.append(f"{where}.stackable: 要是 true／false")
        price = row.get("price")
        if price is not None and (not isinstance(price, int) or price < 0):
            e.append(f"{where}.price: 要是 >=0 的整數")
        use = row.get("use")
        if use is not None:
            if not isinstance(use, dict):
                e.append(f"{where}.use: 要是物件（{{target, effects}}）")
            else:
                for k in use:
                    if k not in USE_KEYS:
                        e.append(f"{where}.use: 不認識的鍵 {k!r}（只有 {'/'.join(USE_KEYS)}）")
                tg = use.get("target")
                if tg is not None and tg not in USE_TARGETS:
                    e.append(f"{where}.use.target: 要是 {'／'.join(USE_TARGETS)}")
                effs = use.get("effects")
                if not isinstance(effs, list) or not effs:
                    e.append(f"{where}.use.effects: 要是非空陣列（用了這件道具要發生什麼）")
        for k in row:
            if k not in ROW_KEYS:
                e.append(f"{where}: 不認識的欄位 {k!r}（只有 {'/'.join(ROW_KEYS)}）")
    return e


def load(paths: Any) -> Dict[str, Dict[str, Any]]:
    """讀 `packs.tables` 裡所有 `kind=items` 的表，合成 `{id: entry}`（重複 id 寫得不一樣＝當場叫）。"""
    out: Dict[str, Dict[str, Any]] = {}
    for p in (paths or []):
        if not p or not os.path.exists(p):
            continue
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or data.get("kind") != TABLE_KIND:
            continue                                    # 不是道具表：與這一支無關（別人的表）
        rows = data.get("rows") or []
        errs = validate(rows)
        if errs:
            raise ItemError(f"{os.path.basename(p)}: " + "；".join(errs[:8]))
        for row in rows:
            iid = str(row["id"])
            if iid in out and out[iid] != row:
                raise ItemError(f"道具 {iid!r} 在兩張表裡寫得不一樣（{p}）")
            out[iid] = dict(row)
    return out


# ---------------------------------------------------------------------------
# 機制（Mixin；與 conditions／skills／advancement 同一種寫法）
# ---------------------------------------------------------------------------


class ItemsMixin:
    """道具機制的實作。**表是空的 ⇒ 全部方法都是 no-op**（legacy 零變更）。"""

    # ------------------------------------------------------------------ 載入
    def items_init(self, tables: Any = None) -> None:
        """載入道具表（`tables` 來自 L1 的 `PackView.tables`）。"""
        self.item_table: Dict[str, Dict[str, Any]] = load(tables)

    # ------------------------------------------------------------------ 折算
    def items_apply(self, ev: Dict[str, Any]) -> None:
        """把 `item_changed` 折進 `state["inventory"]`（事件溯源；`after` 是結果值）。"""
        st = self.state.get("inventory")
        if st is None:
            return
        if ev.get("type") == "item_changed":
            st.setdefault(str(ev["seat"]), {})[str(ev["id"])] = int(ev.get("after") or 0)

    # ------------------------------------------------------------------ 讀出口
    def item_ids(self) -> List[str]:
        """這張桌認得的道具 id（GM 工具面／自檢器／前端用）。"""
        return sorted((getattr(self, "item_table", None) or {}).keys())

    def item_def(self, id: str) -> Dict[str, Any]:
        """取一件道具的定義（不認識＝當場叫，列出有哪些）。"""
        tbl = getattr(self, "item_table", None) or {}
        iid = str(id or "").strip()
        if not tbl:
            raise ItemError("這張桌沒有啟用帶道具表的機制包（`config.packs` 裡加帶 items 表的包）")
        if iid not in tbl:
            raise ItemError(f"沒有這個道具：{iid!r}（有的：{self.item_ids()}）")
        return tbl[iid]

    def items_of(self, seat: str) -> Dict[str, int]:
        """這一席身上的道具（`{id: 數量}`；沒有＝空 dict）。"""
        return dict((self.state.get("inventory") or {}).get(str(seat)) or {})

    def items_view(self, seat: str) -> List[Dict[str, Any]]:
        """這一席的道具（給投影／前端用）：名稱／數量／能不能「用」／要對誰用。"""
        tbl = getattr(self, "item_table", None) or {}
        out: List[Dict[str, Any]] = []
        for iid, n in sorted(self.items_of(seat).items()):
            if not int(n or 0):
                continue
            row = tbl.get(iid) or {}
            use = row.get("use") or {}
            out.append({
                "id": iid,
                "count": int(n),
                "name_jp": row.get("name_jp") or iid,
                "name_cn": row.get("name_cn") or row.get("name_jp") or iid,
                "usable": bool(use.get("effects")),
                "use_target": use.get("target") or "self",
                "price": row.get("price"),
                "note": row.get("note"),
            })
        return out

    # ------------------------------------------------------------------ 給／拿
    def _item_delta(self, *, target: str, id: str, count: int, note: Optional[str] = None,
                    by: str = "gm") -> Dict[str, Any]:
        seat = str(target or "").strip()
        if seat not in (self.state.get("seats") or {}):
            raise ItemError(f"沒有這個席位：{seat!r}")
        self.item_def(id)                                   # id 不存在＝當場叫
        iid = str(id)
        cur = int(self.items_of(seat).get(iid, 0))
        after = cur + int(count)
        if after < 0:
            raise ItemError(f"{seat} 身上沒有那麼多 {iid}（有 {cur}，要拿掉 {-int(count)}）"
                            "——拒絕，不留幽靈事件")
        ev = self._emit("item_changed", public=True, seat=seat, id=iid, delta=after - cur,
                        after=after, note=note, by=by)
        return {"seat": seat, "id": iid, "count": after, "delta": after - cur, "event": ev}

    def item_add(self, *, target: str, id: str, count: int = 1, note: Optional[str] = None,
                 by: str = "gm") -> Dict[str, Any]:
        """給道具（`count` 可為負＝拿走；扣到負＝當場叫）。"""
        if not isinstance(count, int):
            raise ItemError("count 要是整數")
        return self._item_delta(target=target, id=id, count=count, note=note, by=by)

    def item_take(self, *, target: str, id: str, count: int = 1, note: Optional[str] = None,
                  by: str = "gm") -> Dict[str, Any]:
        """拿走道具（`count` 給正數；不足＝當場叫）。"""
        return self.item_add(target=target, id=id, count=-abs(int(count)), note=note, by=by)

    # ------------------------------------------------------------------ 用（動作）
    def use_item(self, *, seat: str, item: str, target: Optional[str] = None,
                 note: Optional[str] = None, by: str = "player") -> Dict[str, Any]:
        """用一件道具：**先扣 1**（不足⇒拒，不套效果），成功才套 `use.effects`。

        效果走 `scenario.effects_apply`——與技能表、`module.json` 的觸發**同一份實作**。

        ★ 2026-10-21：`use.target=foe` 的 `target` 也可以是**同格的怪物**（uid 或 monster id）——
          沒有這條，`星屑の爆弾`（「王の硬さを割るための道具」）只能丟隊友，打不到它要打的王。
          傷害走 `Table.monster_damage()`（＝fight／技能同一條 `monster_damaged` → 擊破獎勵）。
        """
        s = str(seat or "").strip()
        if s not in (self.state.get("seats") or {}):
            raise ItemError(f"沒有這個席位：{s!r}")
        defn = self.item_def(item)
        iid = str(item)
        use = defn.get("use") or {}
        effs = use.get("effects") or []
        if not effs:
            raise ItemError(f"{iid!r} 不能「用」（表上沒有 use.effects）")
        tg = str(use.get("target") or "self")
        tgt = str(target or "").strip()
        mon: Optional[Dict[str, Any]] = None
        if tg == "foe":
            if not tgt:
                raise ItemError(f"{iid!r} 要指定目標（target=<席位 或 怪物 uid>）")
            if tgt not in (self.state.get("seats") or {}):
                _nid = (self.state["seats"].get(s) or {}).get("node")
                _insts = self.alive_monsters(_nid) if hasattr(self, "alive_monsters") else []
                mon = next((i for i in _insts
                            if str(tgt) in (i.get("uid"), i.get("id"))), None)
                if mon is None:
                    raise ItemError(f"沒有這個席位：{tgt!r}")
                # 對怪物只認傷害效果（治療／狀態的「目標」是席位概念）——先擋，別把道具吃掉
                _bad = [e for e in effs if "damage" not in e]
                if _bad:
                    raise ItemError(f"{iid!r} 對怪物只支援 damage 效果；"
                                    f"這一張還有 {[k for e in _bad for k in e if k != 'damage']}")
        else:
            tgt = tgt or s
        # 先扣（不足＝當場叫、不留事件；也還沒有任何效果）
        take = self.item_take(target=s, id=iid, count=1, note=note, by="use_item")
        if mon is not None:
            made: List[Dict[str, Any]] = []
            for eff in effs:
                _b = eff.get("damage") or {}
                _bonus = int(_b.get("bonus") or 0)
                _ab = int(_b.get("ally_bonus") or 0)
                if _ab and hasattr(self, "node_mates"):
                    _bonus += _ab * len(self.node_mates(s))
                try:
                    _hit = self.monster_damage(seat=s, uid=mon["uid"], die=int(_b.get("die") or 0),
                                               bonus=_bonus,
                                               attr=(str(_b["attr"]) if _b.get("attr") else None),
                                               note=f"item {iid}")
                except ValueError as exc:                       # 怪物在扣血前就沒了（並發／多段）
                    raise ItemError(str(exc)) from exc
                # ⚠ 不能拿 `events[-1]`：打穿時後面還會接一筆「擊破」事件，取到的就不是傷害那筆。
                made.append(_hit)
            _last = made[-1] if made else {}
            return {"seat": s, "item": iid, "name": defn.get("name_jp") or iid,
                    "target": mon["uid"], "monster": mon["uid"],
                    "monster_damage": _last.get("monster_damage"),
                    "monster_hp": _last.get("monster_hp"),
                    **({"killed": True} if _last.get("killed") else {}),
                    "effects": made, "left": int(take.get("count") or 0)}
        ctx: Dict[str, Any] = {"me": {"seat": s}}
        ctx["target"] = {"seat": tgt}
        evs = scenario.effects_apply(self, effs, ctx, by="use_item")
        return {"seat": s, "item": iid, "name": defn.get("name_jp") or iid,
                "target": tgt, "effects": evs, "left": int(take.get("count") or 0)}
