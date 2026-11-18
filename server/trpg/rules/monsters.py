#!/usr/bin/env python3
"""**L1｜遭遇／怪物表**（★4，2026-10-07 設計者拍「繼續做 4」）：把塔裡的小怪數值搬出程式。

以前「這一層的小怪」數值全在程式裡（`board_cfg.mob_dc`／`tower_cfg.mob_damage_die`／
`tower_cfg.star_coin_per_kill`／`params.exp_per_star_kill`）——**作者改不動敵人**。這一支把「遇到誰、
他多強、打死給什麼」變成**一張表**：

```json
{"kind": "monsters",
 "rows": [{"id": "mob", "name_jp": "小怪", "dc": 13, "check": "agi",
           "coins": 2, "exp": 6, "damage_die": 4,
           "nodes": ["hyakki_f4"], "depth_min": 1, "depth_max": 5, "boss": false}]}
```

| 這一格遇到誰 | 選擇規則（**越精確越優先**） |
|---|---|
| ① `nodes` 命中這一節點 | 最優先（例：`hyakki_f4`＝塔の主） |
| ② 沒寫 `nodes`、`depth_min/max` 含這一層的深度 | 次之（例：深層的群れの長） |
| ③ 都沒寫 | 通用（哪裡都適用） |
| 表是空的／都不命中 | **回退**舊的 config／包預設（examples：`mob_dc` 13／`damage_die` 4／`star_coin_per_kill` 2／`exp_per_star_kill` 6）⇒ legacy 一字不變 |

其他：
* **遭遇的「數量」仍由 `board.json` 的 `mobs`（每節點）決定**——表決定的是「這一格遇到**哪一種**」。
* 表的 `boss: true` 也能宣告「打光這一格 ⇒ `boss_defeated`」（原本只有節點的 `boss` 標籤能）。
* 事件：`mob_killed` 多帶 `monster`（id）與 `name`；`damage` 的 `src` 換成怪物 id（沒表＝照舊 `mob`）。
* **沒啟用帶怪物表的包 ⇒ 一個欄位都不加、fight 完全照舊**（legacy 零變更）。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

#: 怪物表在包裡的形狀（`{"kind": "monsters", "rows": [...]}`）
TABLE_KIND = "monsters"

#: 一列認得的欄位
ROW_KEYS = ("id", "name_jp", "name_cn", "dc", "check", "coins", "exp", "damage_die",
            "nodes", "depth_min", "depth_max", "boss", "note",
            # 2026-10-19 戰鬥擴充：HP／同格多隻／主動怪
            "hp", "count", "active")

#: 數值欄位（整數；可省略＝回退舊預設）
NUM_KEYS = ("dc", "coins", "exp", "damage_die", "depth_min", "depth_max", "hp", "count")


class MonsterError(ValueError):
    """怪物表不合法（載入時就叫，不靜默）。"""


def validate(rows: Any) -> List[str]:
    """表內容合不合法（作者工具與 `test_monsters` 共用）。"""
    e: List[str] = []
    if not isinstance(rows, list):
        return ["rows 要是陣列"]
    seen = set()
    for i, row in enumerate(rows):
        where = f"rows[{i}]"
        if not isinstance(row, dict):
            e.append(f"{where}: 要是物件")
            continue
        mid = str(row.get("id") or "").strip()
        where = f"rows[{i}]({mid or '?'})"
        if not mid:
            e.append(f"{where}: 每一種怪要有 id")
        elif mid in seen:
            e.append(f"{where}: id 重複（一種怪只能一列）")
        seen.add(mid)
        for k in NUM_KEYS:
            if row.get(k) is not None and (not isinstance(row[k], int) or row[k] < 0):
                e.append(f"{where}.{k}: 要是 >=0 的整數")
        if row.get("check") is not None and not isinstance(row["check"], str):
            e.append(f"{where}.check: 要是屬性名（字串；省略＝agi）")
        if row.get("nodes") is not None and (not isinstance(row["nodes"], list)
                                            or any(not isinstance(x, str) or not x for x in row["nodes"])):
            e.append(f"{where}.nodes: 要是節點 id 的字串陣列（省略＝不限節點）")
        if row.get("boss") is not None and not isinstance(row["boss"], bool):
            e.append(f"{where}.boss: 要是 true/false")
        if row.get("hp") is not None and (not isinstance(row["hp"], int) or row["hp"] < 1):
            e.append(f"{where}.hp: 要是 >=1 的整數（不寫＝1，一擊必殺）")
        if row.get("count") is not None and (not isinstance(row["count"], int) or row["count"] < 1):
            e.append(f"{where}.count: 要是 >=1 的整數（不寫＝這個節點的 mobs 由同格幾種怪平分）")
        act = row.get("active")
        if act is not None and not isinstance(act, (bool, dict)):
            e.append(f"{where}.active: 要是 true/false 或 {{chance: 0~1}}")
        elif isinstance(act, dict):
            ch = act.get("chance")
            if ch is not None and (not isinstance(ch, (int, float)) or not 0 <= float(ch) <= 1):
                e.append(f"{where}.active.chance: 要是 0~1 的數字")
            mt = act.get("max_targets")
            if mt is not None and (not isinstance(mt, int) or mt < 0):
                e.append(f"{where}.active.max_targets: 要是 >=0 的整數（0＝等同被動）")
            for k in act:
                if k not in ("chance", "max_targets", "note"):
                    e.append(f"{where}.active: 不認識的欄位 {k!r}（只有 chance／max_targets／note）")
        for k in ("name_jp", "name_cn", "note"):
            if row.get(k) is not None and not isinstance(row[k], str):
                e.append(f"{where}.{k}: 要是字串")
        if row.get("depth_min") is not None and row.get("depth_max") is not None \
                and int(row["depth_min"]) > int(row["depth_max"]):
            e.append(f"{where}: depth_min 比 depth_max 大（那永遠不會命中）")
        for k in row:
            if k not in ROW_KEYS:
                e.append(f"{where}: 不認識的欄位 {k!r}（只有 {'／'.join(ROW_KEYS)}）")
    return e


def load(paths: Any) -> List[Dict[str, Any]]:
    """讀 `packs.tables` 裡所有 `kind=monsters` 的表（**保持順序**：同級別取先寫的那一列）。"""
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
            raise MonsterError(f"{os.path.basename(p)}: " + "；".join(errs[:8]))
        for row in rows:
            mid = str(row["id"])
            if mid in seen:
                raise MonsterError(f"怪物 id 重複：{mid}（{p}）")
            seen.add(mid)
            out.append(dict(row))
    return out


class MonstersMixin:
    """遭遇／怪物表的實作。**表空 ⇒ 全部 no-op**（fight 照舊走 config／包預設）。"""

    def monsters_init(self, tables: Any = None) -> None:
        self.monster_rows: List[Dict[str, Any]] = load(tables)

    # ------------------------------------------------------------------ 讀出口
    def monster_ids(self) -> List[str]:
        return [str(r.get("id")) for r in (getattr(self, "monster_rows", None) or [])]

    def monster_for(self, node: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """這一格遇到哪一種怪：`nodes` 命中 ＞ depth 範圍命中 ＞ 通用；都不命中＝None（回退舊預設）。"""
        rows = getattr(self, "monster_rows", None) or []
        if not rows or not node:
            return None
        nid = str(node.get("id") or "")
        depth = node.get("depth")
        best: Optional[Dict[str, Any]] = None
        rank = 0
        for row in rows:
            nodes = row.get("nodes")
            if nodes is not None:
                if nid in [str(x) for x in nodes]:
                    return row                                     # ① 指定節點：最優先，先寫先贏
                continue
            lo, hi = row.get("depth_min"), row.get("depth_max")
            if lo is not None or hi is not None:
                if depth is None:
                    continue
                if (lo is None or int(depth) >= int(lo)) and (hi is None or int(depth) <= int(hi)):
                    if rank < 2:                                   # ② 深度範圍
                        best, rank = row, 2
                continue
            if best is None:                                       # ③ 通用
                best, rank = row, 1
        return best

    # ------------------------------------------------------------ 同格多隻／HP（2026-10-19）

    def monsters_for(self, node: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """這一格**排了哪些怪**（可多筆；順序＝表上的順序）。

        規則沿用「越精確越優先」，但同一級別**不再只取第一筆**：
          ① `nodes` 命中這一節點 ⇒ **全部命中的列**（同格多種怪就是靠這個）
          ② 否則 `depth` 範圍命中 ⇒ 第一筆（legacy 語意不變）
          ③ 否則通用列 ⇒ 第一筆（legacy 語意不變）
        """
        rows = getattr(self, "monster_rows", None) or []
        if not rows or not node:
            return []
        nid = str(node.get("id") or "")
        depth = node.get("depth")
        hits = [r for r in rows if r.get("nodes") is not None and nid in [str(x) for x in r["nodes"]]]
        if hits:
            return hits
        for row in rows:
            if row.get("nodes") is not None:
                continue
            lo, hi = row.get("depth_min"), row.get("depth_max")
            if lo is None and hi is None:
                continue
            if depth is None:
                continue
            if (lo is None or int(depth) >= int(lo)) and (hi is None or int(depth) <= int(hi)):
                return [row]
        generic = [r for r in rows if r.get("nodes") is None
                   and r.get("depth_min") is None and r.get("depth_max") is None]
        return generic[:1]

    def monster_instances_for(self, node: Dict[str, Any]) -> List[Dict[str, Any]]:
        """把「這一格排了哪些怪」展開成**可打擊的個體**（HP／主動／uid）。

        數量規則：
          * 每列都寫了 `count` ⇒ 照寫的；
          * 都沒寫 ⇒ 把節點 `mobs`（總隻數）**平分**給這幾列（餘數給最後一列）——
            單列時＝原本的「一格 3 隻」語意，**legacy 一字不變**；
          * 只有部分列寫 `count` ⇒ 寫了的照算，剩下的平分「總數 − 已寫的」。
        """
        rows = self.monsters_for(node)
        if not rows:
            return []
        nid = str(node.get("id") or "")
        try:
            total = int((self.state.get("board") or {}).get(self.slot("mob"), {}).get(nid,
                        node.get(self.slot("mob"), 0)))
        except Exception:                                                  # noqa: BLE001
            total = int(node.get(self.slot("mob"), 0) or 0)
        counts = self._split_counts(rows, total)
        # ★ 2026-10-20 開局難度：三顆倍率（`config.difficulty_mods`；沒設＝原值，legacy 一字不變）
        _mods = (getattr(self, "cfg", {}) or {}).get("difficulty_mods") or {}
        _dc_add = int(_mods.get("monster_dc") or 0)
        _exp_mul = float(_mods.get("exp_mult") or 1.0)
        _coin_mul = float(_mods.get("coins_mult") or 1.0)
        # ★ 2026-10-21：血量倍率——高難度下 BOSS 的 HP 要「一個角色一回合打不死」，
        #   逼出「一起打／輪流坦」的合作（`starting_difficulty.levels.<級>.mods.monster_hp_mult`）。
        _hp_mul = float(_mods.get("monster_hp_mult") or 1.0)

        def _scaled(v: Any, mul: float, add: int = 0) -> Any:
            if v is None:
                return None
            return max(0, int(round(int(v) * mul)) + add)
        base_chance = float((getattr(self, "cfg", {}).get("combat") or {}).get("active_chance", 0.35))
        out: List[Dict[str, Any]] = []
        for row, n in zip(rows, counts):
            hp = max(1, int(round(int(row.get("hp") or 1) * _hp_mul)))
            act = row.get("active")
            _max_t = int((getattr(self, "cfg", {}).get("combat") or {}).get("active_max_targets", 1) or 1)
            if isinstance(act, bool):
                active, chance, max_targets = act, base_chance, _max_t
            elif isinstance(act, dict):
                chance = float(act.get("chance", base_chance))
                max_targets = int(act.get("max_targets", _max_t))
                active = max_targets > 0            # ★ 2026-10-19 設計者：n=0 ⇒ 當被動怪
            else:
                active, chance, max_targets = False, base_chance, 0
            for k in range(int(n)):
                out.append({
                    "uid": f"{nid}:{row['id']}:{k}",
                    "id": str(row["id"]),
                    "name_jp": row.get("name_jp") or str(row["id"]),
                    "name_cn": row.get("name_cn") or row.get("name_jp") or str(row["id"]),
                    "hp": hp, "max_hp": hp,
                    "dc": _scaled(row.get("dc"), 1.0, _dc_add), "check": row.get("check") or "agi",
                    "coins": _scaled(row.get("coins"), _coin_mul), "exp": _scaled(row.get("exp"), _exp_mul),
                    "damage_die": row.get("damage_die"),
                    "boss": bool(row.get("boss")),
                    "active": active, "chance": round(max(0.0, min(1.0, chance)), 3),
                    "max_targets": (max(0, max_targets) if active else 0),
                })
        return out

    @staticmethod
    def _split_counts(rows: List[Dict[str, Any]], total: int) -> List[int]:
        total = max(0, int(total))
        k = len(rows)
        if k == 0:
            return []
        fixed = [int(r["count"]) for r in rows if r.get("count") is not None]
        if len(fixed) == k:
            return [int(r["count"]) for r in rows]
        left = max(0, total - sum(fixed))
        free = [i for i, r in enumerate(rows) if r.get("count") is None]
        counts = [0] * k
        for i, r in enumerate(rows):
            if r.get("count") is not None:
                counts[i] = int(r["count"])
        base, rem = (left // len(free), left % len(free)) if free else (0, 0)
        for j, i in enumerate(free):
            counts[i] = base + (rem if j == len(free) - 1 else 0)
        return counts

    def alive_monsters(self, nid: str) -> List[Dict[str, Any]]:
        """這一格**還活著**的怪（投影與 fight 共用同一個口徑）。"""
        insts = ((self.state.get("board") or {}).get("monsters") or {}).get(str(nid)) or []
        return [i for i in insts if int(i.get("hp") or 0) > 0]

    def monster_view(self, node: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """給投影用：這一格的敵人長什麼樣（沒表／不命中＝None ⇒ 投影不加欄位）。

        2026-10-19：**同格多隻＋HP**——`monster` 仍是「第一隻還活著的」（舊前端照舊），
        另外多給 `monsters`（全部還活著的個體，帶 hp／max_hp／active）。
        """
        if not node:
            return None
        alive = self.alive_monsters(str(node.get("id") or ""))
        if alive:
            first = alive[0]
            return {"id": first["id"], "name_jp": first.get("name_jp"), "name_cn": first.get("name_cn"),
                    "dc": first.get("dc"), "coins": first.get("coins"), "exp": first.get("exp"),
                    "damage_die": first.get("damage_die"), "boss": bool(first.get("boss")),
                    "hp": first.get("hp"), "max_hp": first.get("max_hp"),
                    "active": bool(first.get("active")),
                    "monsters": [{"uid": i["uid"], "id": i["id"], "name_jp": i.get("name_jp"),
                                  "name_cn": i.get("name_cn"), "hp": i.get("hp"),
                                  "max_hp": i.get("max_hp"), "active": bool(i.get("active")),
                                  "boss": bool(i.get("boss"))} for i in alive],
                    "alive": len(alive)}
        m = self.monster_for(node)                       # 表有這一格、但怪物都打完了
        if not m:
            return None
        return {"id": str(m.get("id")), "name_jp": m.get("name_jp") or str(m.get("id")),
                "name_cn": m.get("name_cn") or m.get("name_jp") or str(m.get("id")),
                "dc": m.get("dc"), "coins": m.get("coins"), "exp": m.get("exp"),
                "damage_die": m.get("damage_die"), "boss": bool(m.get("boss")),
                "hp": 0, "max_hp": max(1, int(m.get("hp") or 1)), "active": bool(m.get("active")),
                "monsters": [], "alive": 0}
