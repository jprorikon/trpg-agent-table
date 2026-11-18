"""model（自 __init__.py 拆出）"""

from __future__ import annotations
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple
from trpg.rules import expr as expr
from trpg.engine import presentation as presentation

from .loader import ModuleError, _node_of, _pack_ctx, resolve_module_path, resolve_seat
from .common import EFFECT_KINDS, EFFECT_SPECS, NUMERIC_PRESET_KEYS

from .loading import ScenarioLoadingMixin

class Scenario(ScenarioLoadingMixin):
    """一份已驗證的劇本模組（薄資料物件；不持有桌子）。"""
    def __init__(self, data: Dict[str, Any], path: str = "",
                 allowed_effects: Optional[Any] = None,
                 bank: Optional[Dict[str, str]] = None) -> None:
        self.data = data or {}
        self.path = path
        #: 這一桌**可用**的 effect＝啟用包宣告的那些（沒給＝內核整池，legacy）
        self.allowed = tuple(allowed_effects or EFFECT_KINDS)
        #: 這一桌的句庫（`packs.text_bank_for`；驗 `emit.text_key` 用）——沒給＝內核預設
        self.bank = bank

    def requirement(self, gid: str) -> Optional[Any]:
        raw = (self.data.get("requirements") or {}).get(gid)
        return _node_of(raw) if raw is not None else None
    @property
    def terms(self) -> Dict[str, Any]:
        """模組自訂用詞（例 `{"fragment": {"jp": "星屑", "cn": "星屑"}}`）。

        引擎只認 key（fragment／wish／pray_box），顯示名一律問這一格
        （`Table.term_name()`）；沒宣告 ⇒ 內核預設「碎片」。
        寫在 `scenario.json`：頂層 `terms` 或 `meta.terms` 都認（劇本層可以覆寫世界層）。
        """
        _t = self.data.get("terms") or (self.data.get("meta") or {}).get("terms")
        return dict(_t) if isinstance(_t, dict) else {}

    def surface_expr(self, key: str) -> Optional[Any]:
        raw = (self.data.get("surface") or {}).get(key)
        return _node_of(raw) if raw is not None else None
    def entity(self, key: str) -> Dict[str, Any]:
        return ((self.data.get("entities") or {}).get(key) or {})
    def endings(self) -> List[Dict[str, Any]]:
        return list(self.data.get("endings") or [])
    def difficulty(self, name: str) -> Optional[Dict[str, Any]]:
        return ((self.data.get("difficulty_presets") or {}).get(name))
    def difficulty_names(self) -> List[str]:
        return sorted((self.data.get("difficulty_presets") or {}).keys())
    def ctx(self, table: Any, *, seat: Optional[str] = None,
            event: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """把戰局攤成 **AST 看得到的欄位**（純資料，不給函式、不給引擎物件）。

        這裡是「劇本詞彙表」的唯一正本：作者寫 `me.claims`／`world.pray_first.<node>`
        這些路徑，都由這裡供值。
        """
        st = table.state
        semi = st.get("semi") or {}
        clues = getattr(table, "clues", {}) or {}
        seats: Dict[str, Any] = {}
        _conds = st.get("conditions") or {}
        _mods = st.get("attr_mods") or {}
        _res = st.get("resources") or {}
        for sid, s in (st.get("seats") or {}).items():
            seats[sid] = {"seat": sid, "name": s.get("name"), "kind": s.get("kind"),
                          "node": s.get("node"), "memory": s.get("memory"), "hp": s.get("hp"),
                          "down": "down" in (s.get("status") or []),
                          "star_coins": s.get(getattr(table, "slot", lambda _n: "star_coins")("coin")),
                          "artifacts": [a.get("name") for a in (s.get("artifacts") or [])],
                          # 身上的條件 id（部位受傷／異常狀態）——作者可寫「我中毒時…」的判據
                          "conditions": sorted((_conds.get(sid) or {}).keys()),
                          # M2 原語：屬性加值與通用資源（作者可寫「還有法術位才…」的判據）
                          "attr_mods": dict(_mods.get(sid) or {}),
                          "resources": dict(_res.get(sid) or {})}
        sources = semi.get("sources") or {}
        frag_holder = dict(semi.get("frag_holder") or {})
        turn = st.get("turn") or None
        ctx: Dict[str, Any] = {
            "facts": st.get("facts") or {},
            # ★ 時鐘（2026-10-20）：`clocks.<name>` 是 0…max 的整數，作者直接寫進 when
            "clocks": dict(st.get("clocks") or {}),
            "seats": seats,
            "order": list(st.get("order") or []),
            "round_no": int(st.get("round_no") or 0),
            "paused": bool(st.get("paused")),
            "ended": st.get("ended"),
            "difficulty": st.get("difficulty"),
            "turn": None if not turn else {"seat": turn.get("seat"), "index": turn.get("index")},
            "world": {
                "pray_first": dict(semi.get("pray_first") or {}),
                "frag_holder": frag_holder,
                "fragments_out": len(frag_holder),
                "round_no": int((getattr(self, "state", {}) or {}).get("round_no") or 0),
                "settled": bool(semi.get("settled")),
                "up": [sid for sid, v in seats.items() if not v["down"]],
                "down": [sid for sid, v in seats.items() if v["down"]],
                "event_type": (event or {}).get("type"),
                "event": dict(event or {}),
            },
            # 機制包（L1 的 `packs.py`）：作者可以拿 `pack.params.*` 當係數（係數外部化）
            "pack": _pack_ctx(table),
        }
        if seat:
            mine = [fid for fid, h in frag_holder.items() if h == seat]
            seen = list((sources.get(seat) or {}).keys())
            claims: List[str] = []
            for fid in set(mine) | set(seen):
                claims += list((clues.get(fid) or {}).get("supports") or [])
            srcs = sorted({x for froms in (sources.get(seat) or {}).values() for x in froms if x != seat})
            shown_to: Dict[str, int] = {}
            for to, froms in sources.items():
                n = len([fid for fid, who in (froms or {}).items() if seat in (who or [])])
                if n:
                    shown_to[to] = n
            s = (st.get("seats") or {}).get(seat) or {}
            goal = (getattr(table, "goal_by_seat", {}) or {}).get(seat)
            ctx["me"] = {
                "seat": seat, "node": s.get("node"), "memory": s.get("memory"), "hp": s.get("hp"),
                "conditions": sorted((_conds.get(seat) or {}).keys()),
                "claims": sorted(set(claims)), "fragment_sources": srcs,
                "fragments": sorted(mine), "shown_to": shown_to,
                "goal": goal, "goal_achieved": bool(
                    ((semi.get("goals") or {}).get(seat) or {}).get("achieved")),
                "actions_used": dict(((semi.get("counters") or {}).get(seat) or {})),
                "attr_mods": dict(_mods.get(seat) or {}),
                "resources": dict(_res.get(seat) or {}),
            }
        return ctx
    def check(self, node: Any, table: Any, *, seat: Optional[str] = None,
              event: Optional[Dict[str, Any]] = None) -> Any:
        """對戰局求值一條 AST。"""
        return expr.evaluate(node, self.ctx(table, seat=seat, event=event))
    def goal_ok(self, table: Any, seat: str, gid: str) -> Optional[Tuple[bool, Dict[str, Any]]]:
        """願望達成判定（AST 版）。**沒有這條願望的 AST ⇒ 回 None**（呼叫端退回舊判據）。"""
        node = self.requirement(gid)
        if node is None:
            return None
        ok, detail = expr.evaluate_detail(node, self.ctx(table, seat=seat))
        return bool(ok), detail
    def triggers_for(self, table: Any, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        """這一筆事件會踩到哪些觸發（`on` 不給＝任何事件都看；`once` 已燒過就跳過）。"""
        fired = (table.state.get("facts") or {})
        out = []
        for t in (self.data.get("triggers") or []):
            on = t.get("on")
            if on:
                tset = [on] if isinstance(on, str) else list(on)
                if event.get("type") not in tset:
                    continue
            if t.get("once") and fired.get("_fired." + str(t["id"])):
                continue
            if "when" in t and not expr.evaluate(t["when"], self.ctx(table, event=event)):
                continue
            out.append(t)
        return out
    def fire(self, table: Any, trig: Dict[str, Any], event: Dict[str, Any]) -> List[Dict[str, Any]]:
        """執行一個觸發的 effects；**回傳它產出的新事件**（呼叫端拿去跑下一輪）。"""
        made: List[Dict[str, Any]] = []
        if trig.get("once"):                      # 先蓋章再動：不然自己的 effect 會再踩到自己
            made.append(table._emit("fact_set", public=False, key="_fired." + str(trig["id"]),
                                    value=True, by="trigger"))
        # ★ 2026-10-21：觸發的 effect 也要能指到**動作人**（`seat: "me"`）。
        #   以前 ctx 沒有 me ⇒ 劇本只能把 p1／p2／p3 各抄一條（加人再加）。
        #   事件帶 seat 時就把它當 me；沒有 seat 的事件的效果照舊要寫明確席位。
        ctx = self.ctx(table, seat=(event.get("seat") or None), event=event)
        for eff in (trig.get("then") or []):
            made += self._apply_effect(table, eff, ctx)
        return made
    def _apply_effect(self, table: Any, eff: Dict[str, Any], ctx: Dict[str, Any],
                      by: str = "trigger") -> List[Dict[str, Any]]:
        kind = next(iter(eff))
        body = eff[kind]
        if kind == "emit":
            kw = {k: v for k, v in body.items() if k not in ("type", "text_key", "facts")}
            if body.get("text_key"):
                kw["text"] = presentation.text(str(body["text_key"]), getattr(table, "text_bank", None),
                                               **{k: expr.evaluate(v, ctx)
                                                  for k, v in (body.get("facts") or {}).items()})
            return [table._emit(str(body["type"]), **kw)]
        if kind == "fact_set":
            return [table._emit("fact_set", public=False, key=str(body["key"]),
                                value=expr.evaluate(body.get("value"), ctx), by=by)]
        if kind in ("clock_tick", "clock_set"):
            # ★ 時鐘：**結果值寫進事件**（`after`）⇒ 回放不必重算（與條件／資源同一套）。
            name = str(body.get("name") or "")
            decl = (self.data.get("clocks") or {}).get(name) or {}
            mx = decl.get("max")
            cur = int((table.state.get("clocks") or {}).get(name) or 0)
            if kind == "clock_set":
                raw = int(expr.evaluate(body.get("value"), ctx) or 0)
                after = raw
            else:
                delta = int(expr.evaluate(body.get("value", 1), ctx) or 0)
                after = cur + delta
            after = max(0, int(after))
            if mx is not None:
                after = min(int(mx), after)
            return [table._emit("clock_changed", public=True, name=name,
                                mode=("set" if kind == "clock_set" else "add"),
                                value=int(expr.evaluate(body.get("value", 1 if kind == "clock_tick" else 0), ctx) or 0),
                                after=after, max=mx, by=by)]
        if kind == "ending":
            return [table._emit("ending", public=False, id=str(body["id"]),
                                kind=body.get("kind") or "neutral", by=by)]
        if kind in ("attr_mod", "resource_add", "resource_set"):
            # M2 原語（`dsl.py`）：屬性加值／通用資源——**GM 當場裁定與 trigger 同一條路**
            from trpg.core.errors import RuleError as _RuleError                       # noqa: PLC0415
            seat = resolve_seat(body.get("seat"), ctx)
            if not seat:
                raise _RuleError(f"{kind}：seat 指到的席位不存在"
                                 f"（`me`／`target` 要有對應的人；也可以寫明確的席位 id）")
            raw = expr.evaluate(body.get("value"), ctx)
            try:
                val = int(raw)
            except (TypeError, ValueError):
                raise _RuleError(f"{kind}：value 要是整數（拿到 {raw!r}）") from None
            note = str(expr.evaluate(body["note"], ctx)) if body.get("note") else None
            if kind == "attr_mod":
                return [table.set_attr_mod(target=str(seat), attr=str(body.get("attr")),
                                           value=val, note=note, by=by)]
            return [table.add_resource(target=str(seat), name=str(body.get("name")), value=val,
                                       mode=("add" if kind == "resource_add" else "set"),
                                       note=note, by=by)]
        if kind == "condition_set":
            # 投／治一個條件：走 `conditions.set_condition`（與 GM 當場裁定**同一條路**）
            from trpg.core.errors import RuleError as _RuleError                       # noqa: PLC0415
            seat = resolve_seat(body.get("seat"), ctx)
            if not seat:
                raise _RuleError("condition_set：seat 指到的席位不存在"
                                 "（`me`／`target` 要有對應的人；也可以寫明確的席位 id）")
            kw: Dict[str, Any] = {"target": str(seat), "id": str(body["id"]), "by": by}
            if "value" in body:
                kw["value"] = bool(expr.evaluate(body["value"], ctx))
            if "turns" in body:
                kw["turns"] = int(expr.evaluate(body["turns"], ctx))
            if body.get("note"):
                kw["note"] = str(expr.evaluate(body["note"], ctx))
            return [table.set_condition(**kw)]
        if kind in ("damage", "heal"):
            # M3：傷害／治療走**引擎的算式**（技能表與 trigger 共用；不新增第二套傷害公式）
            from trpg.core.errors import RuleError as _RuleError                       # noqa: PLC0415
            seat = resolve_seat(body.get("seat"), ctx)
            if not seat or seat not in (table.state.get("seats") or {}):
                raise _RuleError(f"{kind}：seat 指到的席位不存在（{body.get('seat')!r}）")
            note = str(expr.evaluate(body["note"], ctx)) if body.get("note") else None
            die = int(expr.evaluate(body["die"], ctx)) if body.get("die") is not None else 0
            bonus = int(expr.evaluate(body["bonus"], ctx)) if body.get("bonus") is not None else 0
            src = ((ctx.get("me") or {}).get("seat")) or None
            # ★ 2026-10-21（協力技）：`ally_bonus`＝同一個節點上「除了自己以外」每多一個人就加這麼多點傷害。
            #   寫在技能的 damage effect 裡即可（不需要新機制）——站在一起打同一隻王才划算。
            _ab = int(expr.evaluate(body["ally_bonus"], ctx)) if body.get("ally_bonus") is not None else 0
            if _ab and src and hasattr(table, "node_mates"):
                _mates = table.node_mates(src)          # 引擎算，scenario 層不碰 state
                if _mates:
                    bonus += _ab * len(_mates)
            if kind == "damage":
                attr = str(body["attr"]) if body.get("attr") else None
                res = table.roll_damage(seat=str(seat), die=die, bonus=bonus, src=src,
                                        attr=attr, note=note)
                return [res["event"]]
            table.roll_heal(seat=str(seat), die=die, bonus=bonus, note=note)
            return [table.events[-1]]
        if kind in ("star_set", "growth_gain"):
            # 升星的兩條路：①劇情判據（`stars_check` 每筆事件後自動判）②當場給（這裡／GM op=star）
            from trpg.core.errors import RuleError as _RuleError                       # noqa: PLC0415
            seat = resolve_seat(body.get("seat"), ctx)
            if not seat or seat not in (table.state.get("seats") or {}):
                raise _RuleError(f"{kind}：seat 指到的席位不存在（{body.get('seat')!r}）")
            note = str(expr.evaluate(body["note"], ctx)) if body.get("note") else None
            if kind == "growth_gain":
                res = table.exp_gain(seat=str(seat),
                                     amount=int(expr.evaluate(body["amount"], ctx)),
                                     reason=note, by=by)
                return [e for e in (res.get("events") or [])] or [table.events[-1]]
            star = int(expr.evaluate(body["star"], ctx)) if body.get("star") is not None else 0
            out2 = table.star_set(seat=str(seat), star=star, by=by, note=note)
            return [out2["event"]]
        if kind == "star_grant":
            # ★ 渠道給星（2026-10-20）：買星、全體副任務…都走這條。
            #   `channel`＝劇本 `stars.json` 的 `channels` 名；`value` 預設 1；
            #   `scope: "all"` ⇒ **全桌玩家**各加（副任務的語意），否則看 `seat`（省略＝me）。
            #   ⚠ `scope:"all"` 只給 human／agent——GM／NPC 不升星（見 stars.star_seats）。
            from trpg.core.errors import RuleError as _RuleError                       # noqa: PLC0415
            ch = str(body.get("channel") or "")
            if not ch:
                raise _RuleError("star_grant：要有 channel（劇本 stars 表的 channels 裡的名字）")
            val = int(expr.evaluate(body.get("value", 1), ctx) or 1)
            note = str(expr.evaluate(body["note"], ctx)) if body.get("note") else None
            if str(body.get("scope") or "") == "all":
                _all = getattr(table, "star_seats", None)
                targets = ([str(x) for x in _all()] if callable(_all)
                           else [str(x) for x in (table.state.get("seats") or {})])
            else:
                seat = resolve_seat(body.get("seat"), ctx)
                if not seat or seat not in (table.state.get("seats") or {}):
                    raise _RuleError(f"star_grant：seat 指到的席位不存在（{body.get('seat')!r}）")
                targets = [str(seat)]
            evs: List[Any] = []
            for _s in targets:
                r = table.star_grant(seat=_s, channel=ch, value=val, by=by, note=note)
                if r.get("event"):
                    evs.append(r["event"])
            # ⚠ 滿額時不回傳 `table.events[-1]`：那會把**觸發它的事件**塞回 `_run_triggers` 的佇列，
            #   讓同一筆事件被再處理一輪（trigger／結局／turn_started 條件結算都會重跑）。
            return evs
        if kind in ("item_grant", "item_take"):
            # ★2：給／拿走道具（機制住 items.py；`seat` 省略＝me，`count` 預設 1）
            from trpg.core.errors import RuleError as _RuleError                       # noqa: PLC0415
            seat = resolve_seat(body.get("seat"), ctx)
            if not seat or seat not in (table.state.get("seats") or {}):
                raise _RuleError(f"{kind}：seat 指到的席位不存在（{body.get('seat')!r}）")
            iid = str(expr.evaluate(body["id"], ctx)) if body.get("id") else ""
            cnt = int(expr.evaluate(body["count"], ctx)) if body.get("count") is not None else 1
            note = str(expr.evaluate(body["note"], ctx)) if body.get("note") else None
            res = (table.item_add(target=str(seat), id=iid, count=cnt, note=note, by="script")
                   if kind == "item_grant"
                   else table.item_take(target=str(seat), id=iid, count=cnt, note=note, by="script"))
            return [res["event"]]
        name = str(body["name"])
        if self.difficulty(name) is None:
            raise ModuleError(f"difficulty_set：沒有這個難度預設 {name!r}"
                              f"（有：{self.difficulty_names()}）")
        return [table._emit("difficulty_set", public=False, name=name, by="trigger")]
    def check_endings(self, table: Any, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        """結局判定：**只判還沒結的那一個**（先搶先贏＝列表順序）。"""
        if table.state.get("ended"):
            return []
        ctx = self.ctx(table, event=event)
        for en in self.endings():
            if expr.evaluate(en.get("when"), ctx):
                # ★3 結局收尾：`ending` **公開**（日誌看得到「這一局到此為止」），並把結局的名字
                # 一起帶過去（前端拿它做結局卡；沒寫 name_* 就退回 id）。
                kw: Dict[str, Any] = {"id": str(en["id"]), "kind": en.get("kind") or "neutral",
                                      "by": "endings"}
                for k in ("name_jp", "name_cn", "text"):
                    if en.get(k):
                        kw[k] = en[k]
                return [table._emit("ending", public=True, **kw)]
        return []
    def active_preset(self, table: Any) -> Dict[str, Any]:
        """目前生效的難度預設（沒設／名子不存在＝空 dict）。"""
        name = table.state.get("difficulty")
        return (self.difficulty(str(name)) or {}) if name else {}

def effects_apply(table: Any, effs: Any, ctx: Dict[str, Any], *,
                  by: str = "trigger") -> List[Dict[str, Any]]:
    """跑一串 effect——**唯一一份實作**：`module.json` 的觸發與技能表（`skills.py`）都走這裡。

    沒有 `module.json` 的桌也能用（借一個空殼 `Scenario`）；只有 `difficulty_set` 需要真劇本
    （它要查 `difficulty_presets`）。effect 只能**發事件**或呼叫機制出口，不直接改 state。
    """
    mod = getattr(table, "module", None)
    if mod is None:
        mod = Scenario({"meta": {"id": "_inline"}})
    out: List[Dict[str, Any]] = []
    for eff in (effs or []):
        out += mod._apply_effect(table, eff, ctx, by=by)
    return out

