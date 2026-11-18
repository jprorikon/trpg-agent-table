"""act（自 BoardMixin 拆出）。"""

from __future__ import annotations
import json
import os
import random
from typing import Any, Dict, List, Optional, Sequence


class BoardActMixin:
    def monster_slain(self, *, seat: str, node_id: str, mon: Optional[Dict[str, Any]],
                      uid: Optional[str], name: str) -> Dict[str, Any]:
        """怪物被打穿之後的**唯一一份**獎勵結算（fight 與技能傷害共用）。"""
        st = self.state
        node = self.node(node_id) or {}
        s = st["seats"].get(seat) or {}
        _luck = int(self.attrs_of(seat).get("luck", 0)) - int(self.rules.get("attr_default") or 0)
        _c0 = (int(mon["coins"]) if (mon and mon.get("coins") is not None)
               else int(self.tower_cfg.get("star_coin_per_kill", 2)))
        coins = _c0 + max(0, _luck)
        self._emit(self.slot_event("mob"), public=True, seat=seat, node=node_id, coins=coins,
                   monster=(str(mon["id"]) if mon else None), uid=uid, name=(name if mon else None))
        _exp = (int(mon["exp"]) if (mon and mon.get("exp") is not None)
                else int((getattr(self.packs, "params", {}) or {}).get("exp_per_star_kill", 0)))
        if _exp:
            self.exp_gain(seat=seat, amount=_exp, reason=self.slot_event("mob"), by="board")
        _is_boss = bool((mon or {}).get("boss")) or ("boss" in (node.get("tags") or []))
        if int(st["board"][self.slot("mob")].get(node_id, 0)) <= 0 and _is_boss:
            self._emit("boss_defeated", public=True, seat=seat, node=node_id,
                       text=self.phrase("boss_defeated.text"))
            self._emit("memory_change", public=True, seat=seat, delta=0,
                       before=s.get("memory", 0), after=s.get("memory", 0),
                       reason=self.phrase("boss.slain_by", place=node.get("jp", node_id)))
            _expb = int((getattr(self.packs, "params", {}) or {}).get("exp_per_boss_defeat", 0))
            if _expb:
                self.exp_gain(seat=seat, amount=_expb, reason="boss_defeated", by="board")
        return {self.slot("coin"): coins}

    def monster_damage(self, *, seat: str, uid: str, die: int = 0, bonus: int = 0,
                       attr: Optional[str] = None, note: Optional[str] = None) -> Dict[str, Any]:
        """技能／道具打**怪物**（2026-10-21：技能傷害原本只認席位 ⇒ 打不到小怪）。

        走同一條 `monster_damaged` → 打穿就 `monster_slain`（不新增第二套傷害公式）。
        """
        _nid = (self.state["seats"].get(seat) or {}).get("node")
        insts = self.alive_monsters(_nid) if hasattr(self, "alive_monsters") else []
        tgt = next((i for i in insts if str(uid) in (i.get("uid"), i.get("id"))), None)
        if tgt is None:
            raise ValueError(f"這一格沒有目標 {uid!r}（可用：{[i['uid'] for i in insts]}）")
        dcfg = self.rules["damage"]
        dseq = self.state["roll_seq"] + 1
        rng = random.Random(f"{self.cfg['seed']}:{dseq}")
        val = rng.randint(1, int(die or dcfg["die"]))
        mod = int(bonus or 0) + (int(self.attrs_of(seat).get(attr, 0)) * int(dcfg["attr_multiplier"]) if attr else 0)
        dmg = max(1, val + mod)
        hp_after = max(0, int(tgt.get("hp") or 0) - dmg)
        me = (self.state["seats"].get(seat) or {}).get("node")
        self._emit("roll", public=True, roll_seq=self.state["roll_seq"] + 1, seat=seat, kind="damage",
                   die=int(die or dcfg["die"]), value=val, mod=mod, total=dmg, dc=None,
                   success=None, reason=note or f"skill damage -> {uid}")
        self._emit("monster_damaged", public=True, seat=seat, node=me, uid=tgt["uid"],
                   monster=str(tgt["id"]), damage=dmg, hp_after=hp_after,
                   die=int(die or dcfg["die"]), value=val, mod=mod,
                   name=str(tgt.get("name_jp") or tgt["id"]))
        out: Dict[str, Any] = {"monster_damage": dmg, "monster_hp": hp_after, "monster": tgt["uid"]}
        if hp_after <= 0:
            mon = {"id": tgt["id"], "name_jp": tgt.get("name_jp"), "dc": tgt.get("dc"),
                   "check": tgt.get("check"), "coins": tgt.get("coins"), "exp": tgt.get("exp"),
                   "damage_die": tgt.get("damage_die"), "boss": bool(tgt.get("boss"))}
            self.monster_slain(seat=seat, node_id=me, mon=mon, uid=tgt["uid"],
                               name=str(tgt.get("name_jp") or tgt["id"]))
            out["killed"] = True
        return out

    def _entrance_open(self, mono: str) -> bool:
        """這個塔的入口現在能不能進？

        兩種門（2026-10-21 起並存）：
        - **一次性**（沒有 `entrance_until`）：歷史行為，開著 → 進一次就吃掉；
        - **有效期限**（`search` 開出來的）：到第 N 輪為止（`tower.entrance_open_rounds`，受難度旋鈕控制；
          2026-10-23 起**保底含下一輪**＝下限 2），期間**每個人都能進**（進塔不消耗它）。
        """
        b = self.state.get("board") or {}
        if not (b.get("entrances") or {}).get(mono, False):
            return False
        until = int((b.get("entrance_until") or {}).get(mono) or 0)
        return (not until) or int(self.state.get("round_no") or 0) <= until

    def board_act(self, *, seat: str, action: str, target: Optional[str] = None,
                  attr: Optional[str] = None, dc: Optional[int] = None,
                  note: Optional[str] = None) -> Dict[str, Any]:
        s = self.state["seats"][seat]
        me = s["node"]
        st = self.state
        out: Dict[str, Any] = {"seat": seat, "action": action}

        # ---- 移动 --------------------------------------------------------
        if action == "move":
            if not target:
                raise ValueError("move 必须给 target（相邻节点 id，先看 can_go）")
            _inside, _tgt_inside = self.in_tower(me), self.in_tower(target)
            _d_me = self.node(me).get("depth")
            _d_tgt = self.node(target).get("depth")
            # ① 外 → 內：等於「進塔」（要入口開著；落點＝入口樓層）
            if not _inside and _tgt_inside:
                node = self.node(me)
                mono = node.get("monolith")
                if mono is None and "monolith_entry" in (node.get("tags") or []):
                    mono = me
                if mono is None:
                    raise ValueError(f"{me} 附近沒有入口——先 search 找")
                if not st["board"]["entrances"].get(mono, False):
                    raise ValueError("這個入口已經失效（用過的入口不能再進）——先 search 找新的入口")
                floor = None
                for to, kind in self.declared.get(mono, {}).items():
                    if kind == "entrance":
                        floor = to
                        break
                if floor is None:
                    raise ValueError("這個地牢的內部還沒開放（board.json 裡沒有內部樓層）")
                if _d_tgt is not None and int(_d_tgt) != 1:
                    floor = next((n for n in self.declared.get(mono, {}) if n == floor), floor)
                # 有效期限內的門：留給隊友（consumed=False）；一次性的門：吃掉
                _until = int((st["board"].get("entrance_until") or {}).get(mono) or 0)
                _rno = int(st.get("round_no") or 0)
                self._emit("entered_tower", public=True, seat=seat, node=mono, floor=floor,
                           consumed=not (_until and _rno <= _until))
                self.land_stop(seat=seat, node=floor)
                out["floor"] = floor
                out["note"] = self.phrase("tower.enter_note")
                return out
            # ② 內 → 內：等於「上樓／下樓」（升降機；上行要清光）
            if _inside and _tgt_inside:
                kinds = self.adj.get(me, {})
                if kinds.get(target) != self.slot("lift"):
                    raise ValueError(self.phrase("board.err.no_ride_route", node=me, target=target))
                if (me, target) in self.hidden_edges and not st["board"]["boss_defeated"].get(me):
                    raise ValueError("隱藏樓層還沒開（本桌以「擊破該層 BOSS」為解鎖條件）")
                _going_up = True if (_d_me is None or _d_tgt is None) else int(_d_tgt) > int(_d_me)
                left = st["board"][self.slot("mob")].get(me, 0)
                if _going_up and left > 0:
                    raise ValueError(self.phrase("board.err.foes_block_ride", left=left))
                self._emit("elevator", public=True, seat=seat, frm=me, to=target,
                           blocked_by=("foes" if (left > 0 and _going_up) else None))
                self.land_stop(seat=seat, node=target)
                out["to"] = target
                out["node"] = {"jp": self.node(target)["jp"], "cn": self.node(target).get("cn")}
                return out
            # ③ 內 → 外：等於「出塔」（只有 1 階有出口；高層要先下樓）
            if _inside and not _tgt_inside:
                if _d_me is not None and int(_d_me) != 1:
                    raise ValueError("這裡沒有出口——先 move 到 1 階（下樓不受限），再 move 出去")
                exit_to = None
                for nid, kind in self.declared.get(me, {}).items():
                    if kind == "exit":
                        exit_to = nid
                        break
                if exit_to is None:
                    raise ValueError("這裡沒有出口——先 move 到 1 階，再 move 出去")
                confiscate = bool(self.tower_cfg.get("confiscate_on_exit", True))
                coins = s.get(self.slot("coin"), 0)
                self._emit("tower_exit", public=True, seat=seat, to=exit_to, reason="exit_tower",
                           confiscated=confiscate and coins > 0, coins_lost=(coins if confiscate else 0))
                self.land_stop(seat=seat, node=exit_to)
                out["to"] = exit_to
                out[f"confiscated_{self.slot('coin')}"] = coins if confiscate else 0
                return out

            kinds = self.adj.get(me, {})
            if target not in kinds:
                raise ValueError(f"{me} 与 {target} 不相邻；可去：{list(kinds)}")
            if target != me and self.in_tower(me) and kinds[target] != self.slot("lift"):
                raise ValueError(self.phrase("board.err.tower_ride_only"))
            if (me, target) in self.hidden_edges and not st["board"]["boss_defeated"].get(me):
                raise ValueError("这个入口还不能用（条件未满足）")
            tgt = self.node(target)
            kind = kinds[target]
            if kind == "entrance":
                raise ValueError(f"去塔里用 enter（{target} 是入口），不是 move")
            # 荒野道：要过移动判定
            if kind == "wild":
                check = self._roll(seat=seat, kind="travel", reason=f"travel {me}->{target}",
                                   mod=self._mod(self.attrs_of(seat),
                                                 self.board_cfg.get("travel_attr", "agi")),
                                   dc=(int(self.board_cfg.get("travel_dc_by_danger", [0, 10, 13, 16])[
                                           min(3, int(tgt.get("danger", 0)))])
                                       # 條件的影響維度：腳傷之類會讓路更難走（沒啟用條件表＝0）
                                       + int(self.conditions_mod(seat, "move_dc") or 0)))
                out["travel_check"] = check
                if not check["success"]:
                    out["stayed"] = me
                    out["note"] = "道中で足を止められた（移動失敗）"
                    return out
            self._emit("move", public=True, seat=seat, frm=me, to=target, kind=kind)
            # ★ 2026-10-21：落格效果（`kind=lands` 表；沒有表／節點沒寫 land ⇒ 回空）
            self.land_stop(seat=seat, node=target)
            out["to"] = target
            out["node"] = {"jp": tgt["jp"], "cn": tgt.get("cn"), "type": tgt["type"]}
            return out

        # ---- 探索（找入口 / 拾獲） ---------------------------------------
        if action == "search":
            node = self.node(me)
            dc_use = int(self.board_cfg.get("entrance_search_dc", 12)) + int(node.get("danger", 0))
            check = self._roll(seat=seat, kind="search", reason=f"search {me}",
                               mod=self._mod(self.attrs_of(seat), "wil"), dc=dc_use)
            out["roll"] = check
            if not check["success"]:
                out["found"] = None
                return out
            ent = st["board"]["entrances"]
            tgt_mono = node.get("monolith")
            if tgt_mono is None and "monolith_entry" in (node.get("tags") or []):
                tgt_mono = me
            if tgt_mono and not ent.get(tgt_mono, True):
                # 門開著的有效期：N 輪（N＝tower.entrance_open_rounds，受難度旋鈕控制）
                # ★ 2026-10-23（設計者 C）：「N 回合」一律**保底含下一輪**（下限 2）——
                #   舊寫法 N=1 只算開門當輪，這一輪行動格用完（search 自己就要 1 格、
                #   沒開 `turn.free_actions` 的桌連移動也吃格）就進不去 ⇒ 下輪門關 ⇒ **白搜**。
                #   ⇒ 就算有人把旋鈕設成 1，實際也是「當輪＋下一輪」；投影與事件都報實際值 2。
                _opens = max(2, int(self.tower_cfg.get("entrance_open_rounds", 2) or 2))
                _rno = int(st.get("round_no") or 0)
                _until = _rno + _opens
                self._emit("entrance_found", public=True, seat=seat, node=tgt_mono,
                           text=self.phrase("entrance_found.text"), until=_until, rounds=_opens)
                out["found"] = {"entrance": tgt_mono, "open_rounds": _opens, "until_round": _until}
                # ★ 2026-10-21：正向回饋——不給一句「找到了」，agent 只能靠 entrance_fresh 翻面猜
                out["note"] = self.phrase("entrance_found.note")
            else:
                self._memory_delta(seat, int(self.tower_cfg.get("search_memory_gain", 2)),
                                   self.phrase("memory.picked"))
                self._emit("search_loot", public=True, seat=seat, node=me)
                out["found"] = {"memory": int(self.tower_cfg.get("search_memory_gain", 2))}
            return out

        # ---- 入塔 --------------------------------------------------------
        if action == "enter":
            node = self.node(me)
            mono = node.get("monolith")
            if mono is None and "monolith_entry" in (node.get("tags") or []):
                mono = me
            if mono is None:
                raise ValueError(f"{me} 附近没有塔（没有入口）")
            if not self._entrance_open(mono):
                raise ValueError("这个入口已经失效（用过的入口不能再进，或有效期过了）——先 search 找新的入口")
            floor = None
            for to, kind in self.declared.get(mono, {}).items():
                if kind == "entrance":
                    floor = to
                    break
            if floor is None:
                raise ValueError("这个塔的内部还没开放（board.json 里没有内部楼层）")
            # 有效期限內的門：留給隊友（consumed=False）；一次性的門：吃掉
            _until = int((st["board"].get("entrance_until") or {}).get(mono) or 0)
            _rno = int(st.get("round_no") or 0)
            self._emit("entered_tower", public=True, seat=seat, node=mono, floor=floor,
                       consumed=not (_until and _rno <= _until))
            self.land_stop(seat=seat, node=floor)
            out["floor"] = floor
            out["note"] = self.phrase("tower.enter_note")
            return out

        # ---- 昇降機 ------------------------------------------------------
        if action == "ride":
            if not self.in_tower(me):
                raise ValueError(self.phrase("board.err.ride_outside"))
            if not target:
                raise ValueError("ride 必须给 target（相邻楼层）")
            kinds = self.adj.get(me, {})
            if kinds.get(target) != self.slot("lift"):
                raise ValueError(self.phrase("board.err.no_ride_route", node=me, target=target))
            if (me, target) in self.hidden_edges and not st["board"]["boss_defeated"].get(me):
                raise ValueError("隐藏楼层还没开（本作设定：到达条件不明；本桌以「击破该层 BOSS」为解锁条件）")
            # ★ 2026-10-19（設計者）：「下樓不受限」——沒清光的樓層只擋**上行**，回撤/下樓隨時可以。
            #   方向用節點 depth 判（兩邊都有 depth 才判；資料缺 depth ⇒ 維持舊的「一律擋」）。
            _d_from = self.node(me).get("depth")
            _d_to = self.node(target).get("depth")
            _going_up = True if (_d_from is None or _d_to is None) else int(_d_to) > int(_d_from)
            left = st["board"][self.slot("mob")].get(me, 0)
            if _going_up and left > 0:
                raise ValueError(self.phrase("board.err.foes_block_ride", left=left))
            self._emit("elevator", public=True, seat=seat, frm=me, to=target,
                       blocked_by=("foes" if (left > 0 and _going_up) else None))
            self.land_stop(seat=seat, node=target)
            out["to"] = target
            out["node"] = {"jp": self.node(target)["jp"], "cn": self.node(target).get("cn")}
            return out

        # ---- 战斗（打小怪） ----------------------------------------------
        if action == "fight":
            if not self.in_tower(me):
                raise ValueError(self.phrase("board.err.fight_outside"))
            left = st["board"][self.slot("mob")].get(me, 0)
            if left <= 0:
                raise ValueError(self.phrase("board.err.no_foes"))
            node = self.node(me)
            # ★4：這一格遇到哪一種怪由**表**決定（沒表／不命中 ⇒ None ⇒ 完全照舊走 config／包預設）
            _mon = self.monster_for(node) if hasattr(self, "monster_for") else None
            # ★ 2026-10-19：個體化的怪物（HP／同格多隻）。target 可以給 uid（`f2:hone_musha:0`）
            #   或 monster id；不給＝打第一隻還活著的。**沒有個體表 ⇒ 走下面原本那條路**（legacy）。
            _insts = self.alive_monsters(me) if hasattr(self, "alive_monsters") else []
            _tgt = None
            if _insts:
                if target:
                    _tgt = next((i for i in _insts if str(target) in (i.get("uid"), i.get("id"))), None)
                    if _tgt is None:
                        raise ValueError(f"這一格沒有目標 {target!r}（可用：{[i['uid'] for i in _insts]}）")
                else:
                    _tgt = _insts[0]
                _mon = {"id": _tgt["id"], "name_jp": _tgt.get("name_jp"), "dc": _tgt.get("dc"),
                        "check": _tgt.get("check"), "coins": _tgt.get("coins"), "exp": _tgt.get("exp"),
                        "damage_die": _tgt.get("damage_die"), "boss": bool(_tgt.get("boss"))}
            _mname = str((_mon or {}).get("name_jp") or self.slot("mob"))
            _dc0 = (int(_mon["dc"]) if (_mon and _mon.get("dc") is not None)
                    else int(self.board_cfg.get("mob_dc", 13)))
            dc_use = int(dc if dc is not None else _dc0 + int(node.get("danger", 0)))
            _chk_attr = str((_mon or {}).get("check") or "agi")
            check = self._roll(seat=seat, kind="fight", reason=f"fight {_mname}",
                               mod=self._mod(self.attrs_of(seat), _chk_attr), dc=dc_use)
            out["roll"] = check
            if check["success"] and _tgt is not None:
                # 有血量的個體：先扣血，打穿才擊破（hp 預設 1 ⇒ 一擊必殺，與舊行為相同）
                _dcfg = self.rules["damage"]
                _dseq = self.state["roll_seq"] + 1
                _drng = random.Random(f"{self.cfg['seed']}:{_dseq}")
                _dval = _drng.randint(1, int(_dcfg["die"]))
                _dmod = int(self.attrs_of(seat).get(_dcfg["attr"], 0)) * int(_dcfg["attr_multiplier"])
                dmg = max(1, _dval + _dmod)
                hp_after = max(0, int(_tgt.get("hp") or 0) - dmg)
                self._emit("monster_damaged", public=True, seat=seat, node=me, uid=_tgt["uid"],
                           monster=str(_tgt["id"]), damage=dmg, hp_after=hp_after,
                           die=int(_dcfg["die"]), value=_dval, mod=_dmod,
                           name=_mname)
                out["monster_damage"] = dmg
                out["monster_hp"] = hp_after
                out["monster"] = _tgt["uid"]
                if hp_after > 0:
                    return out
            if check["success"]:
                # 運（luck）：貨幣多掉一點（沒有 luck 這一項的桌＝0，行為不變）
                _got = self.monster_slain(seat=seat, node_id=me, mon=_mon, uid=(_tgt["uid"] if _tgt is not None else None),
                                          name=_mname)
                out["killed"] = True
                out[self.slot("coin")] = int(_got.get(self.slot("coin")) or 0)
            else:
                dmg_die = (int(_mon["damage_die"]) if (_mon and _mon.get("damage_die") is not None)
                           else int(self.tower_cfg.get("mob_damage_die", 4)))
                rng = random.Random(f"{self.cfg['seed']}:hurt:{self.state['roll_seq']}")
                dmg = rng.randint(1, dmg_die) + int(node.get("danger", 0))
                # ★ 2026-10-20：扣血走唯一出口（`hp_after` 下限 0 只在 `damage_apply` 算）
                ev = self.damage_apply(seat=seat, amount=dmg,
                                       src=(str(_mon["id"]) if _mon else self.slot("mob")))
                hp_after = ev["hp_after"]
                out[f"hit_by_{self.slot('mob')}"] = dmg
            return out

        # ---- 祈願（换神器，付记忆） --------------------------------------
        if action == "pray":
            node = self.node(me)
            box = node.get("wish_box")
            if not box:
                raise ValueError(self.phrase("board.err.no_wish_box", node=me))
            # ★ 2026-10-21（B3）：祈願箱可以設定「至少要幾個人在塔裡」（預設 1＝關掉）
            _need = int(self.tower_cfg.get("wish_box_min_party", 1) or 1)
            if _need > 1:
                _inside = [sid for sid, ss in (st.get("seats") or {}).items()
                           if self.in_tower((ss or {}).get("node"))]
                if len(_inside) < _need:
                    raise ValueError(f"祈願箱要 {_need} 個人在塔裡才開得起來（現在 {len(_inside)} 個）——"
                                     f"等隊友上來，或先下去接人")
            if st["board"].get("wish_box_used", {}).get(me):
                raise ValueError(self.phrase("board.err.wish_box_used", node=me))
            if st["board"][self.slot("mob")].get(me, 0) > 0:
                raise ValueError(self.phrase("board.err.wish_box_guarded"))
            check = self._roll(seat=seat, kind="pray", reason="pray to wish box", mod=0, dc=None)
            die = check["value"]
            tier = int(box.get("tier", 0))
            if die >= 17:
                tier += 1
            elif die == 1:
                tier = max(0, tier - 1)
            tier = min(tier, len(self.artifacts_table) - 1)
            row = self.artifacts_table[tier]
            rng = random.Random(f"{self.cfg['seed']}:artifact:{check['roll_seq']}")
            items = row.get("items") or [{"name": n, "desc": "", "hidden": ""}
                                         for n in row.get("names", [])]
            item = items[rng.randrange(len(items))]
            name, desc = item["name"], item.get("desc", "")
            cost = int(row["memory_cost"])
            # **只公开基础说明**；隐藏效果不进事件（免得从公开日志漏出去）
            self._emit("artifact_gained", public=True, seat=seat, name=name, tier=tier,
                       desc=desc, memory_cost=cost, node=me, die=die)
            # 半合作模式：祈願的代价（记忆）会以「碎片」回到桌上，落在非请求者手上
            if hasattr(self, "grant_fragment"):
                try:
                    # 2026-10-20：走 `grant_pray_drop`——它讀 `clues.json` 的 `drop_policy`
                    # （count_per_pray 幾枚、prefer_unheld、以及「按欠帳挑碎片」）。
                    if hasattr(self, "grant_pray_drop"):
                        self.grant_pray_drop(seat, pool="tower")
                    else:
                        self.grant_fragment(exclude=seat, pool="tower", reason=seat)
                except Exception:  # noqa: BLE001
                    pass
            out["artifact"] = {"name": name, "tier": tier, "memory_cost": cost}
            out["roll"] = check
            self._memory_delta(seat, -cost,
                                   self.phrase("memory.artifact_cost", name=name, tier=tier))
            return out

        # ---- 商店 ------------------------------------------------------------
        if action == "buy":
            node = self.node(me)
            if not node.get("shop"):
                raise ValueError(f"{me} に店はない")
            # 賣什麼由 `shop.py` 決定：`config.tower.shop` 有寫就全走 config（legacy 一字不變）；
            # 沒寫就看包帶來的 `shop` 表（賣道具 ⇒ 買到真的進 inventory，★2）。
            bought = self.shop_buy(seat=seat, item=(note or target or ""), node_id=me, note=note)
            out.update(bought)
            return out

        # ---- 出塔（収奪つき） --------------------------------------------
        if action == "exit_tower":
            if not self.in_tower(me):
                raise ValueError("塔の中にいない")
            to = None
            for nid, kind in self.declared.get(me, {}).items():
                if kind == "exit":
                    to = nid
                    break
            if to is None:
                raise ValueError("这里没有出口——用 ride 回到 1 階")
            confiscate = bool(self.tower_cfg.get("confiscate_on_exit", True))
            coins = s.get(self.slot("coin"), 0)
            self._emit("tower_exit", public=True, seat=seat, to=to, reason="exit_tower",
                       confiscated=confiscate and coins > 0, coins_lost=(coins if confiscate else 0))
            out["to"] = to
            out[f"confiscated_{self.slot('coin')}"] = coins if confiscate else 0
            return out

        # ---- 休息 / 游戏房 ----------------------------------------------
        if action == "rest":
            if self.in_tower(me):
                loss = int(self.tower_cfg.get("assimilation_food_memory_loss", 8))
                self._emit("assimilated", public=True, seat=seat, node=me,
                           text=self.phrase("assimilated.text"))
                out["assimilated"] = True
                self._memory_delta(seat, -loss, self.phrase("memory.tower_food"))
                return out
            node = self.node(me)
            if "rest" not in (node.get("tags") or []):
                raise ValueError(f"{me} では休めない（都市/村/港だけ）")
            gain = int(self.tower_cfg.get("rest_memory_gain", 5))
            hp_gain = int(self.tower_cfg.get("rest_heal", 3))
            hp_after = min(s["max_hp"], s["hp"] + hp_gain)
            self._emit("heal", public=True, seat=seat, amount=hp_gain, hp_after=hp_after)
            self._memory_delta(seat, gain,
                                   self.phrase("memory.daily", place=node["jp"]))
            out["rested"] = {"hp": hp_gain, "memory": gain}
            return out

        if action == "game":
            node = self.node(me)
            if not node.get("game_room"):
                raise ValueError(f"{me} にゲームセンターはない")
            gain = int(self.tower_cfg.get("game_memory_gain", 3))
            self._memory_delta(seat, gain, self.phrase("memory.arcade"))
            out["played"] = gain
            return out

        raise ValueError(f"未处理的 board 动作：{action}")

