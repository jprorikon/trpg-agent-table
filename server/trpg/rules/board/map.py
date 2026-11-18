"""map（自 BoardMixin 拆出）。"""

from __future__ import annotations
import json
import os
import random
from typing import Any, Dict, List, Optional, Sequence
from .base import TOTAL_ACTIONS

class BoardMapMixin:
    def board_actions(self) -> tuple:
        return TOTAL_ACTIONS if getattr(self, "board_enabled", False) else ()


    def node(self, nid: str) -> Dict[str, Any]:
        if nid not in self.nodes:
            raise ValueError(f"不存在的节点：{nid}")
        return self.nodes[nid]


    def node_kind(self, nid: str) -> str:
        return self.nodes[nid]["type"] if nid in self.nodes else "offboard"


    def in_tower(self, nid: str) -> bool:
        return nid in self.nodes and self.nodes[nid]["type"] == "tower_floor"


    def neighbors(self, nid: str) -> List[Dict[str, Any]]:
        out = []
        for to, kind in sorted(self.adj.get(nid, {}).items()):
            n = self.nodes[to]
            out.append({"to": to, "kind": kind, "jp": n["jp"], "cn": n.get("cn", n["jp"]),
                        "type": n["type"], "danger": n.get("danger", 0),
                        "hidden": (nid, to) in self.hidden_edges,
                        f"{self.slot('mob')}_left": self.state["board"][self.slot("mob")].get(to, 0)})
        return out


    def party_view(self, viewer: Optional[str] = None) -> List[Dict[str, Any]]:
        """队友位置投影（**信息防火墙**）——规则由 `config.board.visibility` 决定：

        - **塔外**（都市/村/荒野/塔前）：位置可见（大家走的是同一条路，互相看得见）。
        - **塔内**（tower_floor）：**特殊场景**——塔是一体化的异空间，只给「塔内」，**不暴露层数**。
        - GM 一律不列；玩家自己也不列（自己那格有 you_are）。NPC 是世界实体，位置照常公开。
        """
        rules = (self.cfg.get("board") or {}).get("visibility") or {}
        hide_inside = bool(rules.get("tower_inside_hides_floor", True))
        kinds = set(rules.get("applies_to_kinds") or ["human", "agent"])
        hide_gm = bool(rules.get("hide_gm", True))
        out: List[Dict[str, Any]] = []
        for s in self.state["seats"].values():
            sid = s["seat"]
            spec = self.seats_spec.get(sid) or {}
            kind = spec.get("kind") or s.get("kind")
            if sid == viewer or (hide_gm and kind == "gm"):
                continue
            n = self.nodes.get(s.get("node"))
            if not n:
                out.append({"seat": sid, "name": s.get("name"), "kind": kind,
                            "is_party": kind in ("human", "agent"),
                            "where": None, "where_cn": None, "node": None,
                            "inside_tower": False, "hidden_floor": False})
                continue
            inside = n.get("type") == "tower_floor"
            hidden = inside and hide_inside and kind in kinds
            out.append({
                "seat": sid, "name": s.get("name"), "kind": kind,
                # **队友＝玩家席位**（human/agent）；NPC 是世界实体，前端分开列
                "is_party": kind in ("human", "agent"),
                "where": "塔内" if hidden else n.get("jp"),
                "where_cn": None if hidden else n.get("cn"),
                "node": None if hidden else n["id"],
                "inside_tower": inside, "hidden_floor": hidden,
                "hp": s.get("hp"), "max_hp": s.get("max_hp"),
                "status": list(s.get("status") or []),
            })
        return out


    def artifact_catalog(self) -> List[Dict[str, Any]]:
        """**GM 专用**：神器全表（含隐藏效果）。玩家视图永不带 hidden。"""
        out = []
        for row in self.artifacts_table:
            items = row.get("items") or [{"name": n} for n in row.get("names", [])]
            out.append({"tier": row.get("tier"), "memory_cost": row.get("memory_cost"),
                        "items": [{"name": i["name"], "desc": i.get("desc", ""),
                                   "hidden": i.get("hidden", "")} for i in items]})
        return out


    def artifact_hidden(self, name: str) -> str:
        for row in self.artifacts_table:
            for i in (row.get("items") or []):
                if i.get("name") == name:
                    return i.get("hidden", "")
        return ""


    def reveal_artifact(self, *, seat: str, name: Optional[str] = None) -> Dict[str, Any]:
        """GM 揭示隐藏效果：**公开事件**（桌上都会知道效果文本），持有人视图从此带上它。"""
        s = self.state["seats"].get(seat) or {}
        arts = s.get("artifacts") or []
        pick = None
        for a in arts:
            if (name is None and not a.get("revealed")) or (name is not None and a["name"] == name):
                pick = a
                break
        if pick is None:
            raise ValueError("这位席位没有可揭示的神器（或都已揭示）")
        hidden = self.artifact_hidden(pick["name"])
        if not hidden:
            raise ValueError("这件神器没有隐藏效果")
        self._emit("artifact_revealed", public=True, seat=seat, name=pick["name"], hidden=hidden)
        return {"seat": seat, "name": pick["name"], "hidden": hidden}


    def board_map(self) -> Dict[str, Any]:
        """静态地图（**公开知识**）：节点坐标 + 边。给网页画图用，不含任何席位私有信息。

        坐标写在 board.json 的 x/y 上（换作品只改数据）；缺坐标时前端会退回文字视图。
        """
        if not getattr(self, "board_enabled", False):
            return {}
        edges, seen = [], set()
        for n in self.board["nodes"]:
            for e in n.get("edges", []):
                key = tuple(sorted([n["id"], e["to"]]))
                if key in seen:
                    continue
                seen.add(key)
                edges.append({"from": n["id"], "to": e["to"], "kind": e.get("kind", "road")})
        return {
            "board_name": self.board.get("board_name"), "work": self.board.get("work"),
            "start_node": self.board.get("start_node"),
            "nodes": [{"id": n["id"], "jp": n["jp"], "cn": n.get("cn"), "type": n["type"],
                       "danger": n.get("danger", 0), "x": n.get("x"), "y": n.get("y"),
                       "tags": n.get("tags") or [], "depth": n.get("depth"),
                       "wish_box": n.get("wish_box"), "shop": bool(n.get("shop")),
                       "game_room": bool(n.get("game_room"))} for n in self.board["nodes"]],
            "edges": edges,
        }


    def _memory_delta(self, seat: str, delta: int, reason: str) -> Dict[str, Any]:
        s = self.state["seats"][seat]
        before = int(s.get("memory", 0))
        after = max(0, min(100, before + delta))
        if after == before:
            return {}
        ev = self._emit("memory_change", public=True, seat=seat, delta=after - before,
                        before=before, after=after, reason=reason)
        if after <= 0:
            self._sanctum(seat)
        return ev


    def _sanctum(self, seat: str) -> Dict[str, Any]:
        """記憶見底 ⇒ 送回「起點房間」（槽位 `sanctum`；舊模組＝`white_room`）。"""
        restore = int(self.tower_cfg.get("white_room_restore", 40))   # config 鍵保留原名（劇本作者的旋鈕）
        s = self.state["seats"][seat]
        back = self.start_node
        if self.in_tower(s["node"]):
            for to, kind in self.adj.get(s["node"], {}).items():
                if kind == "exit":
                    back = to
                    break
        self._emit(self.slot_event("sanctum"), public=True, seat=seat, to=back, memory=restore,
                   text=self.phrase("white_room.text"))
        self._emit("memory_change", public=True, seat=seat, delta=0, before=0, after=restore,
                   reason=self.phrase("white_room.reason"))
        return self.state


    def _eject(self, seat: str, why: str) -> Dict[str, Any]:
        s = self.state["seats"][seat]
        to = None
        for nid, kind in self.adj.get(s["node"], {}).items():
            if kind == "exit":
                to = nid
                break
        if to is None:
            to = self.start_node
        return self._emit("tower_exit", public=True, seat=seat, to=to, reason=why,
                          confiscated=False)

