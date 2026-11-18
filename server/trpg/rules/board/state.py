"""state（自 BoardMixin 拆出）。"""

from __future__ import annotations
import json
import os
import random
from trpg.core import layout as layout
from trpg.rules.board import slots as slots
from typing import Any, Dict, List, Optional, Sequence


class BoardStateMixin:
    def board_init(self, home: str, cfg: Dict[str, Any]) -> None:
        # ★ 2026-10-20 設計者：「改成泛用的槽位，具體名字由模組級別確定」。
        #   引擎只認 mob／coin／lift／sanctum 四個槽位名；實際字串（mobs／star_coins…）
        #   由模組的 `module.json` 宣告，沒有宣告就用預設 ⇒ 舊模組一字不變（見 `board/slots.py`）。
        #   **劇本層可再蓋一層**（近的贏）：`scenario.json`（舊名 `module.json`）的 `slots`。
        _def_dir = layout.def_dir_of(home)
        _scen_doc: Dict[str, Any] = {}
        for _name in ("scenario.json", "module.json"):
            _p = os.path.join(_def_dir, _name)
            try:
                with open(_p, "r", encoding="utf-8") as _fh:
                    _got = json.load(_fh)
                if isinstance(_got, dict):
                    _scen_doc = _got
                    break
            except (OSError, ValueError):
                continue
        self.slots = slots.load_for(_def_dir, _scen_doc or None)
        self.board_cfg = cfg.get("board", {})
        self.tower_cfg = cfg.get("tower", {})
        self.mem_cfg = cfg.get("memory", {})
        path = os.path.join(home, self.board_cfg.get("file", "board.json"))
        self.board_enabled = bool(self.board_cfg) and os.path.exists(path)
        if not self.board_enabled:
            # 没配棋盘 / 没有 board.json → 纯引擎模式（沿用之前的抽象桌）
            self.board = {"nodes": [], "roster": [], "artifact_table": []}
            self.nodes = {}
            self.adj = {}
            self.hidden_edges = set()
            self.roster = {}
            self.artifacts_table = []
            self.start_node = None
            return
        with open(path, "r", encoding="utf-8") as fh:
            self.board = json.load(fh)
        self.nodes: Dict[str, Dict[str, Any]] = {n["id"]: n for n in self.board["nodes"]}
        # 邻接表（对称）
        self.adj: Dict[str, Dict[str, str]] = {nid: {} for nid in self.nodes}
        # 声明边（有向、按 board.json 原文）——入塔/出塔这类不对称关系按它判
        self.declared: Dict[str, Dict[str, str]] = {nid: {} for nid in self.nodes}
        for n in self.board["nodes"]:
            for e in n.get("edges", []):
                if e["to"] not in self.nodes:
                    raise ValueError(f"board.json 有指向不存在节点的边：{n['id']} -> {e['to']}")
                self.adj[n["id"]][e["to"]] = e.get("kind", "road")
                rev = self.adj.setdefault(e["to"], {})
                # 反向边：别用泛化的 road 覆盖更具体的说法（wild/entrance/elevator 等）
                if n["id"] not in rev or (rev[n["id"]] == "road" and e.get("kind", "road") != "road"):
                    rev[n["id"]] = e.get("kind", "road")
                self.declared[n["id"]][e["to"]] = e.get("kind", "road")
        self.hidden_edges = {(n["id"], e["to"]) for n in self.board["nodes"]
                             for e in n.get("edges", []) if e.get("hidden")}
        self.roster: Dict[str, Dict[str, Any]] = {r["seat"]: r for r in self.board.get("roster", [])}
        # 神器表：`board.json` 有寫就用它（舊行為不變）；沒有 ⇒ 由**包的表**補
        # （`board_load_tables`，kind=artifacts；作品層包＝這一桌的內容——見 packs/README.md）。
        self.artifacts_table: List[Dict[str, Any]] = list(self.board.get("artifact_table") or [])
        self.artifacts_table_path: str = ""
        self.start_node: str = self.board.get("start_node") or sorted(self.nodes)[0]


    # ------------------------------------------------------------------ 泛用槽位（2026-10-20）
    def slot(self, name: str) -> str:
        """槽位 → **實際欄位名**（state／投影／事件的 key）。例：`slot("mob")` → `mobs`（預設）
        或模組宣告的 `shades`。"""
        return slots.key_of(getattr(self, "slots", None) or {}, name)

    def slot_event(self, name: str) -> str:
        """槽位 → **要發的事件型別**。例：`slot_event("mob")` → `mob_killed`（預設）。"""
        return slots.event_of(getattr(self, "slots", None) or {}, name)

    def slot_event_map(self) -> Dict[str, Any]:
        """給 L4（presentation）用的**純資料**：每個槽位認得的事件型別（含預設別名）。

        L4 不准 import board ⇒ 由這裡算好、當參數傳下去（`render_log_tail(slot_events=…)`）。
        """
        got = getattr(self, "slots", None) or {}
        return {n: slots.event_aliases(got, n) for n in ("mob", "lift", "sanctum")}

    def is_slot_event(self, name: str, ev_type: Any) -> bool:
        """這筆事件是不是該槽位的事件（**含預設名的別名**：換了名字之後，舊 run 的事件仍要認）。"""
        return str(ev_type) in slots.event_aliases(getattr(self, "slots", None) or {}, name)

    def board_load_tables(self, paths: Sequence[str]) -> None:
        """載入**包帶的表**（`packs/<id>/tables/*.json`）：只認 `{"kind": "artifacts"}`。

        與 `conditions.py` 同一套規矩：**機制在內核、內容在包的表**；`board.json` 有寫的一份優先
        （一個旋鈕一個權威＝劇本檔贏）。schema 不合法**載入時就拋**，不拖到跑團中間。
        """
        if self.artifacts_table:
            return
        for p in paths or []:
            with open(p, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
            if not isinstance(doc, dict) or doc.get("kind") != "artifacts":
                continue
            rows = doc.get("rows")
            if not isinstance(rows, list) or not rows:
                raise ValueError(f"{p}: artifacts 表的 rows 要是非空陣列")
            for i, row in enumerate(rows):
                if not isinstance(row, dict) or not isinstance(row.get("items"), list):
                    raise ValueError(f"{p}: rows[{i}] 要是物件且要有 items 陣列（tier／memory_cost 選用）")
            self.artifacts_table = list(rows)
            self.artifacts_table_path = p
            return


    def _deepest_nodes(self) -> set:
        """棋盤上**最深的一層**（`depth` 最大的那些節點）——給「有人登頂了嗎」這類判據用。

        只讀 `board.json` 的資料（沒有 depth 的城市／荒野節點不計），不認識任何戰役節點名。
        """
        depths = [int(n.get("depth")) for n in getattr(self, "nodes", {}).values()
                  if isinstance(n.get("depth"), (int, float))]
        if not depths:
            return set()
        top = max(depths)
        return {nid for nid, n in self.nodes.items()
                if isinstance(n.get("depth"), (int, float)) and int(n["depth"]) == top}


    def board_state_default(self) -> Dict[str, Any]:
        if not getattr(self, "board_enabled", False):
            return {}
        return {
            self.slot("mob"): {nid: int(n.get(self.slot("mob"), 0)) for nid, n in self.nodes.items()},
            # ★ 同格多隻＋HP（2026-10-19）：把怪物表展開成**可打擊的個體**。
            #   沒啟用怪物表 ⇒ 空 dict（fight 走原本的「一隻一隻打掉 mobs」路徑）。
            "monsters": {nid: insts for nid, n in self.nodes.items()
                         for insts in [self.monster_instances_for(n)] if insts},
            "entrances": {nid: True for nid, n in self.nodes.items()
                          if "monolith_entry" in (n.get("tags") or [])},
            # ★ 2026-10-21（B2 軟化版）：`search` 開出來的門**有效到第 N 輪**
            #   （N＝`tower.entrance_open_rounds`，受難度旋鈕控制；2026-10-23 起下限 2＝
            #   保底含下一輪，見 `act.py` 的 search 分支）。到期就關、要再 search。
            "entrance_until": {},
            "boss_defeated": {},
            "inside_turns": {},
            "wish_box_used": {},      # node -> seat：祈願箱「一度きり」（叙述与数值必须一致）
        }


    def grant_coins(self, *, seat: str, amount: int, reason: Optional[str] = None,
                    by: str = "account") -> Dict[str, Any]:
        """貨幣增減（**走事件**）：`coin_grant`。account 層用它把局外錢包帶進／帶出這一局。"""
        if not int(amount or 0):
            return {"granted": 0}
        self._emit("coin_grant", public=True, seat=seat, amount=int(amount),
                   reason=(reason or "grant"), by=by)
        return {"granted": int(amount),
                self.slot("coin"): int((self.state["seats"].get(seat) or {}).get(self.slot("coin")) or 0)}


    def board_scene_attacks(self) -> Dict[str, Any]:
        """景的結束階段：**主動怪**按機率攻擊「同一格」的玩家。

        規則（全部資料驅動）：
          * 只看還活著、`active` 的個體；機率＝列上的 `active.chance`（沒寫＝`config.combat.active_chance`，預設 0.35）。
          * 每一隻主動怪獨立擲一次（種子化：seed＋uid＋roll_seq）——中了就對**同一節點上所有活著的
            human／agent** 各咬一口（傷害 `1..damage_die`），各自一筆公開的 `damage` 事件。
          * `config.combat.active_attacks=false` 可整桌關掉；沒有怪物表／沒有主動怪 ⇒ 什麼都不做
            （legacy 一字不變）。
        """
        if not getattr(self, "board_enabled", False) or not getattr(self, "monster_rows", None):
            return {"attacks": 0}
        if not bool((self.cfg.get("combat") or {}).get("active_attacks", True)):
            return {"attacks": 0, "skipped": "disabled"}
        hits: List[Dict[str, Any]] = []
        for nid, insts in ((self.state.get("board") or {}).get("monsters") or {}).items():
            alive = [i for i in insts if int(i.get("hp") or 0) > 0 and i.get("active")]
            if not alive:
                continue
            players = [s for s in self.state["seats"].values()
                       if s.get("node") == nid and s.get("kind") in ("human", "agent")
                       and int(s.get("hp") or 0) > 0]
            if not players:
                continue
            for inst in alive:
                seq = self.state["roll_seq"] + 1
                rng = random.Random(f"{self.cfg['seed']}:monster_attack:{inst['uid']}:{seq}")
                roll = rng.random()
                chance = float(inst.get("chance") or 0.0)
                if roll >= chance:
                    continue
                # ★ 2026-10-19（設計者）：「每景只咬 n 個人」——n＝該隻的 `max_targets`（0＝被動，不會進到這裡）。
                #   從同格活著的玩家人裡**種子化抽 n 個**（不挑人、不偏袒；n 大於人數＝全咬）。
                n_tgt = max(0, int(inst.get("max_targets") or 0))
                if n_tgt <= 0:
                    continue
                pool = list(players)
                rng.shuffle(pool)
                for p in pool[:n_tgt]:
                    die = int(inst.get("damage_die") or
                              self.tower_cfg.get("mob_damage_die", 4) or 4)
                    dmg = rng.randint(1, max(1, die))
                    # ★ 2026-10-20：扣血走唯一出口（下限 0 只在 `damage_apply` 算）；
                    #   這一筆的「主動怪痕跡」（node／active／chance／roll）走 extra 一起寫進事件。
                    self.damage_apply(seat=p["seat"], amount=dmg, src=str(inst["id"]),
                                      extra={"node": nid, "active": True, "chance": chance,
                                             "roll": round(roll, 4)})
                    hits.append({"node": nid, "monster": inst["id"], "seat": p["seat"], "damage": dmg})
        return {"attacks": len(hits), "hits": hits}


    def board_apply(self, ev: Dict[str, Any]) -> None:
        """把 board 相关事件折进 state（由 engine._apply 调用）。"""
        t = ev["type"]
        b = self.state.get("board") or {}
        if not b:
            return
        if t == "move":
            s = self.state["seats"].get(ev["seat"])
            if s is not None:
                s["node"] = ev["to"]
        elif self.is_slot_event("lift", t):
            s = self.state["seats"].get(ev["seat"])
            if s is not None:
                s["node"] = ev["to"]
        elif t == "entrance_found":
            b["entrances"][ev["node"]] = True
            if ev.get("until"):
                b.setdefault("entrance_until", {})[ev["node"]] = int(ev["until"])
        elif t == "round_started":
            # 入口有效期限到期（每輪開始結算一次；回放一致）
            rno = int(ev.get("no") or 0)
            for mono, until in list((b.get("entrance_until") or {}).items()):
                if rno > int(until):
                    b["entrances"][mono] = False
                    b["entrance_until"].pop(mono, None)
        elif t == "tower_turn":
            b["inside_turns"][ev["seat"]] = int(ev["inside_turns"])
        elif t == "entered_tower":
            # 一次性的門：進塔就吃掉（舊行為）。有效期限內的門：`consumed=False` ⇒ 留著給隊友。
            if ev.get("consumed", True):
                b["entrances"][ev["node"]] = False
                (b.get("entrance_until") or {}).pop(ev["node"], None)
            s = self.state["seats"].get(ev["seat"])
            if s is not None:
                s["node"] = ev["floor"]
            b["inside_turns"].setdefault(ev["seat"], 0)
        elif t == "monster_damaged":
            # 打了但沒打死：只動那一隻的 hp（回放不必重算骰子）
            insts = (b.get("monsters") or {}).get(ev.get("node")) or []
            hit = next((i for i in insts if i.get("uid") == ev.get("uid")), None)
            if hit is not None:
                hit["hp"] = max(0, int(ev.get("hp_after") or 0))
        elif self.is_slot_event("mob", t):
            _mob = self.slot("mob")
            if ev["node"] in b[_mob]:
                b[_mob][ev["node"]] = max(0, b[_mob][ev["node"]] - 1)
            # 個體表同步：這一隻 hp 歸 0（有 uid 就認 uid；舊局的擊破事件沒有 uid ⇒ 取第一隻還活著的）
            insts = (b.get("monsters") or {}).get(ev.get("node")) or []
            if ev.get("uid"):
                dead = next((i for i in insts if i.get("uid") == ev.get("uid")), None)
            else:
                dead = next((i for i in insts if int(i.get("hp") or 0) > 0
                             and (not ev.get("monster") or i.get("id") == ev.get("monster"))), None)
            if dead is not None:
                dead["hp"] = 0
            s = self.state["seats"].get(ev["seat"])
            if s is not None:
                _c = self.slot("coin")
                s[_c] = s.get(_c, 0) + int(ev["coins"])
        elif t == "coin_grant":
            # ★account 層用：把**局外貨幣錢包**帶進這一局（`reason="carry_over"`）或收局時把它收回。
            # 語意乾淨的一筆「貨幣增減」——不必借用 `mob_killed`（那是「擊破小怪」）。
            s = self.state["seats"].get(ev.get("seat"))
            if s is not None:
                _c = self.slot("coin")
                s[_c] = max(0, s.get(_c, 0) + int(ev.get("amount") or 0))
        elif t == "boss_defeated":
            b["boss_defeated"][ev["node"]] = True
        elif t == "artifact_gained":
            s = self.state["seats"].get(ev["seat"])
            if s is not None:
                s.setdefault("artifacts", []).append({"name": ev["name"], "tier": ev["tier"],
                                                      "desc": ev.get("desc", ""), "revealed": False})
            # 祈願箱は一度きり：**最初に開けた者**を記録（叙述と数値を一致させる）
            b.setdefault("wish_box_used", {}).setdefault(ev["node"], ev["seat"])
        elif t == "artifact_revealed":
            s = self.state["seats"].get(ev["seat"])
            if s is not None:
                for a in s.get("artifacts") or []:
                    if a.get("name") == ev.get("name"):
                        a["revealed"] = True
                        a["hidden"] = ev.get("hidden", "")
        elif t == "memory_change":
            s = self.state["seats"].get(ev["seat"])
            if s is not None:
                s["memory"] = ev["after"]
        elif t == "shop_buy":
            s = self.state["seats"].get(ev["seat"])
            if s is not None:
                _c = self.slot("coin")
                s[_c] = max(0, s.get(_c, 0) - int(ev["cost"]))
                if ev.get("heal"):
                    s["hp"] = min(s["max_hp"], s["hp"] + int(ev["heal"]))
        elif t == "tower_exit":
            s = self.state["seats"].get(ev["seat"])
            if s is not None:
                s["node"] = ev["to"]
                if ev.get("confiscated"):
                    s[self.slot("coin")] = 0
            b["inside_turns"][ev["seat"]] = 0
        elif self.is_slot_event("sanctum", t):
            s = self.state["seats"].get(ev["seat"])
            if s is not None:
                s["node"] = ev["to"]
                s["status"] = [x for x in s["status"] if x != self.slot_event("sanctum")]
                s["hp"] = max(1, s["hp"])
            b["inside_turns"][ev["seat"]] = 0


    def board_on_turn_start(self, seat: str) -> List[Dict[str, Any]]:
        """回合开始钩子（由 engine.start_turn 调用）：塔内记忆流失 / 强制排出。"""
        out: List[Dict[str, Any]] = []
        if not getattr(self, "board_enabled", False):
            return out
        s = self.state["seats"].get(seat)
        if not s or not self.in_tower(s["node"]):
            return out
        b = self.state["board"]
        n = b["inside_turns"].get(seat, 0) + 1
        out.append(self._emit("tower_turn", public=False, seat=seat, inside_turns=n))
        # 塔內每回合的記憶消耗 ＋ 條件的影響維度（受傷／發燒會更耗；沒啟用條件表＝0）
        # ★ 2026-10-21（A3）：`memory_drain_every_rounds`（預設 1）⇒ 每 N 輪才扣一次。
        #   實測「每輪 −1」會讓保守派永遠不敢上頂層（16 輪全卡在 f2～f3），所以做成可調。
        every = max(1, int(self.tower_cfg.get("memory_drain_every_rounds", 1) or 1))
        rno = int(self.state.get("round_no") or 0)
        drain = 0
        if every <= 1 or (rno % every == 0):
            drain = max(0, int(self.tower_cfg.get("memory_per_turn_inside", 0))
                        + int(self.conditions_mod(seat, "memory_drain") or 0))
            # ★ 2026-10-21（B3）：一個人自己爬塔（塔裡只有你）⇒ 記憶流失 ×N——協作有回報、獨走有代價。
            inside = sum(1 for sid, ss in (self.state.get("seats") or {}).items()
                         if self.in_tower((ss or {}).get("node")))
            if drain and inside <= 1:
                mult = float(self.tower_cfg.get("memory_drain_solo_multiplier", 1) or 1)
                drain = int(round(drain * mult)) if mult > 1 else drain
        if drain:
            out.append(self._memory_delta(seat, -drain, self.phrase("memory.tower_drain")))
        limit = int(self.tower_cfg.get("max_turns_inside", 0))
        if limit and n > limit:
            out.append(self._eject(seat, "探索が長すぎる——塔から弾き出された"))
        return [x for x in out if x]

