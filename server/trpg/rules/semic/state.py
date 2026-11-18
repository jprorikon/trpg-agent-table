"""state（自 SemiCoopMixin 拆出）。"""

from __future__ import annotations
import json
import os
import random
from typing import Any, Dict, List, Optional
from trpg.core.errors import RuleError  # 与 engine.RuleError 同一个类（本模块不 import engine，避免循环依赖）
from .base import PLAYER_ACTIONS

class SemiCoopStateMixin:
    def semic_init(self, home: str, cfg: Dict[str, Any]) -> None:
        self.semi_cfg = cfg.get("semi", {})
        self.pacing = cfg.get("pacing", {})
        self.goals: Dict[str, Dict[str, Any]] = {}
        self.clues: Dict[str, Dict[str, Any]] = {}
        # 碎片的投放策略（clues.json 的 `drop_policy`）：on_pray_to／prefer_unheld／count_per_pray。
        # 2026-10-20 之前引擎**完全沒讀**這一格（資料寫了、行為還是隨機）⇒ 這裡收進來給 grant 用。
        self.drop_policy: Dict[str, Any] = {}
        self.claims: Dict[str, Dict[str, Any]] = {}
        self.seats_spec: Dict[str, Dict[str, Any]] = {}
        self.semic_enabled = bool(self.semi_cfg)
        if not self.semic_enabled:
            return
        base = home
        for key, attr, idkey in (("goals_file", "goals", "goals"),
                                 ("clues_file", "clues", "fragments"),
                                 ("claims_file", "claims", "claims"),
                                 ("seats_file", "seats_spec", "seats")):
            fname = self.semi_cfg.get(key)
            if not fname:
                continue
            path = os.path.join(base, fname)
            if not os.path.exists(path):
                raise ValueError(f"semi 配置缺少文件：{fname}")
            data = json.load(open(path, "r", encoding="utf-8"))
            rows = data.get(idkey, []) or []
            if attr == "clues":
                self.drop_policy = dict(data.get("drop_policy") or {})
            if attr == "seats_spec":
                self.seats_spec = {r["seat"]: r for r in rows}
            else:
                setattr(self, attr, {r["id"]: r for r in rows})
        # 愿望 -> 席位（按角色名匹配 seats.json 的 character）
        self.goal_by_seat: Dict[str, str] = {}
        for seat, spec in self.seats_spec.items():
            gid = spec.get("goal")
            if gid:
                if gid not in self.goals:
                    raise ValueError(f"seats.json 里 {seat} 指向不存在的愿望：{gid}")
                self.goal_by_seat[seat] = gid
        for seat, gid in self.goal_by_seat.items():
            owner = self.goals[gid].get("owner_character")
            if owner and owner != self.seats_spec[seat].get("character"):
                raise ValueError(f"愿望 {gid} 的 owner_character={owner} 与席位 {seat} 的角色不符")


    def semic_state_default(self) -> Dict[str, Any]:
        if not getattr(self, "semic_enabled", False):
            return {}
        return {
            "counters": {},
            "pending": [],          # 争夺宣言
            "frag_holder": {},      # fid -> seat
            "frag_public": False,   # 由 frag_seen_by 计算，这里不用
            "frag_seen_by": {},     # fid -> [seat...]
            "sources": {},          # seat -> {fid: [来自谁...]}
            "goals": {seat: {"id": gid, "achieved": False} for seat, gid in
                      getattr(self, "goal_by_seat", {}).items()},
            "pray_first": {},       # node -> seat
            "public": [],           # 已公开的私密条目 key
            "leaks": [],
            "settled": None,
            "gm_lines": 0,
            "acted": {},            # 本景谁已经出手（含 pass）
            "ended": {},            # 本景谁明確結束了（pass／「結束這一景」按鈕）
        }


    def semic_actions(self) -> tuple:
        return PLAYER_ACTIONS if getattr(self, "semic_enabled", False) else ()


    def semic_apply(self, ev: Dict[str, Any]) -> None:
        t = ev["type"]
        st = self.state.get("semi") or {}
        if not st:
            return
        if t == "round_started":
            st["counters"] = {}
            st["acted"] = {}
            st["ended"] = {}
        elif t == "declare":
            st["pending"].append({"seat": ev["seat"], "kind": ev["kind"], "node": ev["node"],
                                  "seq": ev["seq"]})
        elif t == "declare_resolved":
            st["pending"] = [p for p in st["pending"] if p["seq"] != ev["declare_seq"]]
            if ev.get("winner"):
                st["pray_first"].setdefault(ev["node"], ev["winner"])
        elif t == "fragment_granted":
            st["frag_holder"][ev["fid"]] = ev.get("holder")
        elif t == "artifact_gained":
            # 直接 pray（未经争夺）也要记「这个箱子的第一个祈願者」——
            # 之前只有 declare 揭晓才写 pray_first，导致结算表误报 tower_cleared=false
            st["pray_first"].setdefault(ev.get("node"), ev.get("seat"))
        elif t == "fragment_seen":
            seen = st["frag_seen_by"].setdefault(ev["fid"], [])
            if ev["to"] not in seen:
                seen.append(ev["to"])
            st["sources"].setdefault(ev["to"], {}).setdefault(ev["fid"], []).append(
                ev.get("from") or ev["seat"])
        elif t == "fragment_given":
            st["frag_holder"][ev["fid"]] = ev["to"]
            st["sources"].setdefault(ev["to"], {}).setdefault(ev["fid"], []).append(
                ev.get("from") or ev["seat"])
        elif t == "wish_done":
            st["goals"].setdefault(ev["seat"], {})["achieved"] = True
            if ev.get("id") not in st["public"]:
                st["public"].append(ev.get("id"))
        elif t == "settle":
            st["settled"] = ev.get("table")
        elif t == "acted":
            st["acted"][ev["seat"]] = ev.get("note") or "acted"
        elif t == "gm_leak_blocked":
            st["leaks"].append({"seat": ev["seat"], "hit": ev["hit"], "ts": ev["ts"]})
        elif t == "gm_say":
            st["gm_lines"] = st.get("gm_lines", 0) + 1
        elif t == "throttle_tick":
            st["counters"].setdefault(ev["seat"], {})[ev["kind"]] = int(ev["used"])
        elif t == "ended":
            st.setdefault("ended", {})[ev["seat"]] = ev.get("round_no")

