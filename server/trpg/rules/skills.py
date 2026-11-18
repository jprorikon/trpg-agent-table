#!/usr/bin/env python3
"""**L1｜技能／法術**（M3，2026-10-07）：技能表（`kind=skills`）＋ `cast` 的檢定鏈。

機制住這一支；**內容住包**（`packs/<id>/tables/skills.json`，例：模組 `hyakki_tower` 的 `packs/rules.core/`）：

| 步驟 | 誰算 |
|---|---|
| 這張桌有沒有這個技能 id（沒有＝列出有的） | 這裡（表） |
| 等級需求（`lvl`；**只有啟用經驗機制的桌才真的檢查**，沒啟用＝都當 1 級） | `advancement.level_of` |
| 消耗先扣（`cost.resource`；不足 ⇒ **當場拒，不擲骰、不留事件**） | `dsl.add_resource`（下限 0） |
| 檢定＝`d20 ＋ 生效屬性×attr_multiplier ＋ 熟練 prof ＋ 條件修正 vs DC` | `engine._roll`（**與 `check` 同一條算式**） |
| 成功才套 `effects` | `scenario.effects_apply`（**與 module.json 的觸發同一份實作**） |

紀律：**技能表不能自己算傷害**——`damage`／`heal` 走引擎的算式（`Table.roll_damage`／`roll_heal`），
所以「招式只是資料」。沒啟用帶技能表的包 ⇒ 整條機制 no-op（`cast` 當場叫，legacy 零變更）。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

TABLE_KIND = "skills"

#: 技能表可以長什麼樣（載入時檢查形狀；數值本身不檢查——那是內容）
_REQUIRED = ("id", "attr")


class SkillError(ValueError):
    """技能表的內容／參數不合法（**當場就叫**，不拖到跑團中間）。"""


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
        for k in _REQUIRED:
            if not row.get(k):
                e.append(f"{where}: 缺 {k}")
        sid = str(row.get("id") or "")
        if sid and sid in seen:
            e.append(f"{where}: id 重複（{sid}）")
        seen.add(sid)
        for k in ("prof", "dc", "lvl"):
            if row.get(k) is not None and not isinstance(row[k], (int, float)):
                e.append(f"{where}.{k}: 要是數字")
        cost = row.get("cost") or {}
        if cost and not isinstance(cost, dict):
            e.append(f"{where}.cost: 要是物件（{{resource, value}}）")
        elif cost and not cost.get("resource"):
            e.append(f"{where}.cost: 要有 resource")
        if row.get("effects") is not None and not isinstance(row["effects"], list):
            e.append(f"{where}.effects: 要是陣列")
        if row.get("target") not in (None, "foe", "self", "none", "ally"):
            e.append(f"{where}.target: 只能是 foe／self／none／ally")
    return e


def load(paths: Any) -> Dict[str, Dict[str, Any]]:
    """讀 `packs.tables` 裡所有 `kind=skills` 的表，合成 `{id: entry}`（重複 id＝當場叫）。"""
    out: Dict[str, Dict[str, Any]] = {}
    for p in (paths or []):
        if not p or not os.path.exists(p):
            continue
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or data.get("kind") != TABLE_KIND:
            continue                                    # 不是技能表：與這一支無關（別人的表）
        rows = data.get("rows") or []
        errs = validate(rows)
        if errs:
            raise SkillError(f"{os.path.basename(p)}: " + "；".join(errs[:8]))
        for row in rows:
            sid = str(row["id"])
            entry = dict(row)
            entry.setdefault("prof", 0)
            entry.setdefault("dc", 12)
            entry.setdefault("lvl", 1)
            entry.setdefault("target", "none")
            if sid in out and out[sid] != entry:
                raise SkillError(f"技能 {sid!r} 在兩張表裡寫得不一樣（{p}）")
            out[sid] = entry
    return out


class SkillsMixin:
    """技能表 ＋ `cast`（掛在 `Table` 上）。"""

    # ------------------------------------------------------------------ 載入
    def skills_load(self, tables: Any = None) -> None:
        """載入技能表（`tables` 來自 L1 的 `PackView.tables`）。"""
        self.skills_table: Dict[str, Dict[str, Any]] = load(tables)
        # 這張桌用到 `condition_set` 的技能 ⇒ 要有條件表（沒有的話 cast 到一半才爆太晚）
        if self.skills_table and not (getattr(self, "condition_table", None) or {}):
            for sid, s in self.skills_table.items():
                if any("condition_set" in (e or {}) for e in (s.get("effects") or [])):
                    raise SkillError(
                        f"技能表用到 `condition_set`（{sid}），但這張桌沒啟用帶條件表的包"
                        f"（例：`packs.enabled` 加 wound）")

    # ------------------------------------------------------------------ 讀
    def skill_ids(self) -> List[str]:
        return sorted((getattr(self, "skills_table", None) or {}).keys())

    def skill(self, sid: str) -> Dict[str, Any]:
        return ((getattr(self, "skills_table", None) or {}).get(str(sid)) or {})

    def skills_of(self, seat: str) -> List[Dict[str, Any]]:
        """這一席現在能用的技能（等級夠的就列出來；給 view／MCP 用）。"""
        lvl = int(getattr(self, "level_of", lambda _s: 1)(seat))
        out = []
        for sid in self.skill_ids():
            s = self.skill(sid)
            out.append({"id": sid, "name_jp": s.get("name_jp"), "name_cn": s.get("name_cn"),
                        "element": s.get("element"), "style": s.get("style"),
                        "attr": s.get("attr"), "dc": s.get("dc"), "cost": s.get("cost") or {},
                        "target": s.get("target"), "lvl": s.get("lvl"),
                        # 兩條路都算會：等級夠 **或** 星等解鎖（`stars.py` 的 unlocks）
                        "known": (lvl >= int(s.get("lvl") or 1)
                                  or sid in (self.unlocks_of(seat)
                                             if hasattr(self, "unlocks_of") else [])),
                        "desc": s.get("desc")})
        return out

    # ------------------------------------------------------------------ 動作
    def cast(self, *, seat: str, skill: str, target: Optional[str] = None,
             dc: Optional[int] = None, note: Optional[str] = None) -> Dict[str, Any]:
        """放一招：先驗（技能／等級／消耗／目標）→ 扣消耗 → 擲檢定 → 成功才套 effects。

        回傳 `{skill, roll, effects, ...}`；失敗（沒這招／等級不夠／消耗不足／目標不對）**當場 raise**，
        `act()` 會把整筆宣告標成 rejected（不留幽靈宣告）。
        """
        with self.lock:
            from trpg.core.errors import RuleError                                   # noqa: PLC0415
            if not getattr(self, "skills_table", None):
                raise RuleError("這張桌沒有啟用帶技能表的機制包"
                                "（`config.packs` 裡加 rules.core 之類）")
            sid = str(skill or "").strip()
            if sid not in self.skills_table:
                raise RuleError(f"沒有這個技能：{sid!r}（有的：{self.skill_ids()}）")
            ent = self.skill(sid)
            s = self._require_seat(seat) if hasattr(self, "_require_seat") else self.state["seats"].get(seat)
            if not s:
                raise RuleError(f"沒有這個席位：{seat}")
            want_level = int(ent.get("lvl") or 1)
            lvl = int(getattr(self, "level_of", lambda _x: 1)(seat))
            if lvl < want_level:
                raise RuleError(f"「{ent.get('name_jp') or sid}」要 {want_level} 級"
                                f"（{seat} 現在 {lvl} 級）")
            attr = str(ent.get("attr") or "")
            if attr not in (self.rules.get("attributes") or []):
                raise RuleError(f"技能表寫的屬性 {attr!r} 不在這張桌的屬性表"
                                f"（{list(self.rules.get('attributes') or [])}）")
            tgt_want = str(ent.get("target") or "none")
            tseat = str(target or "").strip() or None
            if tgt_want == "foe" and not tseat:
                raise RuleError(f"「{ent.get('name_jp') or sid}」要指定目標（target）")
            # 2026-10-21：`target` 可以是**同格的怪物**（uid 或 id）——技能才打得到小怪。
            _mon_ok = False
            if tseat and tseat not in (self.state.get("seats") or {}):
                _nid0 = (self.state["seats"].get(seat) or {}).get("node")
                _insts0 = self.alive_monsters(_nid0) if hasattr(self, "alive_monsters") else []
                _mon_ok = any(str(tseat) in (i.get("uid"), i.get("id")) for i in _insts0)
                if not _mon_ok:
                    raise RuleError(f"沒有這個席位：{tseat}")
            if tgt_want == "self":
                tseat = seat
            cost = ent.get("cost") or {}
            cres, cval = str(cost.get("resource") or ""), int(cost.get("value") or 0)
            have = int(self.resources_of(seat).get(cres, 0)) if cres else 0
            if cres and cval > have:
                raise RuleError(self.phrase(
                    "skill.no_resource", skill=(ent.get("name_jp") or sid),
                    resource=f"{cres}（{have}/{cval}）"))
            if cres and cval:
                self.add_resource(target=seat, name=cres, value=-cval, by="skill")
            # 檢定：與 `check` 同一條算式（屬性×mult ＋ 熟練 ＋ 條件修正），難度吃導演檔位
            skill_dc = int(dc if dc is not None else ent.get("dc") or 12) + self.difficulty_mod()
            mod = (self._mod(self.attrs_of(seat), attr)
                   + int(ent.get("prof") or 0) + self.conditions_mod(seat, "check"))
            roll = self._roll(seat=seat, kind="skill", reason=f"cast {sid}", mod=mod, dc=skill_dc)
            ev = self._emit("skill_used", public=True, seat=seat, skill=sid,
                            name=str(ent.get("name_jp") or sid), target=tseat,
                            roll_seq=roll.get("roll_seq"), success=bool(roll.get("success")),
                            dc=skill_dc, note=note)
            out: Dict[str, Any] = {"skill": sid, "roll": roll, "success": bool(roll.get("success")),
                                   "target": tseat, "cost": ({"resource": cres, "value": cval}
                                                             if cres else {}), "seq": ev.get("seq")}
            if roll.get("success"):
                from trpg.rules import scenario as scenario                                                # noqa: PLC0415
                # ★ 2026-10-21：`target` 也可能是一隻**怪物**（uid／id）——技能的 `damage` 走
                #   `monster_damage`（同一條 monster_damaged／擊破獎勵），其餘 effect 照舊走席位。
                _nid = (self.state["seats"].get(seat) or {}).get("node")
                _insts = self.alive_monsters(_nid) if hasattr(self, "alive_monsters") else []
                _mon_t = next((i for i in _insts if str(tseat) in (i.get("uid"), i.get("id"))), None)
                made = []
                _skipped: List[str] = []
                for eff in (ent.get("effects") or []):
                    # ★ 2026-10-24（review R2）：目標是**怪物**時先按 effect 的 `seat` 分流。
                    #   從前這裡只認 `damage`（走 `monster_damage`），其餘一律丟給
                    #   `_effect_ctx(target=<怪 uid>)` ⇒ `seat:"target"` 的 `condition_set`／
                    #   `attr_mod` 把怪的 uid 當成一個席位去解 ⇒ ConditionError **半途拋錯**：
                    #   傷害／法力／skill_used 都已落盤，玩家收到報錯、行動額度沒扣（可以重複出手）。
                    #   設計者 2026-10-24 定案 (a)：怪物沒有狀態／屬性修正機制 ⇒
                    #     `seat:"target"` 的 `damage` 照舊打怪（monster_damage）；
                    #     `seat:"target"` 的其他效果 **跳過**，在回執 `skipped` 裡註明；
                    #     `seat:"me"`（或省略＝me）一律照常（送り火的 heal me 要成立）。
                    #   **不允許「部分事件已落盤後再拋錯」**——所以判斷全部在發事件之前做完。
                    _kind = next(iter(eff), "")
                    _body = eff.get(_kind) if isinstance(eff.get(_kind), dict) else {}
                    _seat_of = str(_body.get("seat") or ("target" if _kind == "damage" else "me"))
                    if _mon_t is not None and _seat_of == "target":
                        if _kind != "damage":
                            _skipped.append(_kind)
                            continue
                        _b = _body
                        _ab = int(_b.get("ally_bonus") or 0)
                        _bonus = int(_b.get("bonus") or 0)
                        if _ab and hasattr(self, "node_mates"):
                            _mates = self.node_mates(seat)
                            _bonus += _ab * len(_mates)
                        _hit = self.monster_damage(seat=seat, uid=_mon_t["uid"],
                                                   die=int(_b.get("die") or 0), bonus=_bonus,
                                                   attr=(str(_b["attr"]) if _b.get("attr") else None),
                                                   note=f"skill {sid}")
                        # 打穿時後面會再接「擊破」事件 ⇒ 傷害數字要從回傳值拿，不能拿 events[-1]
                        made.append(next((e for e in reversed(self.events)
                                          if e.get("type") == "monster_damaged"), self.events[-1]))
                        out["monster_damage"] = _hit.get("monster_damage")
                        out["monster_hp"] = _hit.get("monster_hp")
                        continue
                    ctx = self._effect_ctx(seat=seat, target=tseat)
                    made += scenario.effects_apply(self, [eff], ctx, by="skill")
                out["effects"] = [e.get("type") for e in made]
                if _skipped:
                    out["skipped"] = _skipped
                    out["skipped_note"] = ("怪物沒有狀態／屬性修正機制" +
                                           f"（{'、'.join(_skipped)} 對怪物無效）")
                if _mon_t is not None:
                    out["monster"] = _mon_t["uid"]
            return out

    def _effect_ctx(self, *, seat: Optional[str] = None,
                    target: Optional[str] = None) -> Dict[str, Any]:
        """給 effect 求值用的語境：有 `module.json` 就用劇本那份（能力更全），沒有就用最小殼。"""
        mod = getattr(self, "module", None)
        if mod is not None:
            try:
                ctx = mod.ctx(self, seat=seat)
            except Exception:                                            # noqa: BLE001
                ctx = {}
        else:
            ctx = {"facts": self.state.get("facts") or {}, "seats": {},
                   "pack": {"ids": list(getattr(getattr(self, "packs", None), "ids", []) or [])}}
        ctx.setdefault("target", None)
        if target:
            ctx["target"] = {"seat": target}
        return ctx
