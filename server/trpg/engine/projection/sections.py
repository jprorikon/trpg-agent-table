"""sections（自 __init__.py 拆出）"""

from __future__ import annotations
from typing import Any, Dict, List, Optional

from .base import _aurl, _seat_art, _with_icon, own, seat_kind_public

def _entrance_left(state: Dict[str, Any], mono: str) -> Optional[int]:
    """這個入口還剩幾輪可用（None＝一次性／沒開）。"""
    b = state.get("board") or {}
    until = int((b.get("entrance_until") or {}).get(mono) or 0)
    if not until or not (b.get("entrances") or {}).get(mono, False):
        return None
    return max(0, until - int(state.get("round_no") or 0))


def _entrance_fresh(state: Dict[str, Any], cur: Dict[str, Any]) -> bool:
    b = state.get("board") or {}
    mono = cur.get("id")
    if not (b.get("entrances") or {}).get(mono, False):
        return False
    until = int((b.get("entrance_until") or {}).get(mono) or 0)
    return (not until) or int(state.get("round_no") or 0) <= until


def _seat_plan_public(state: Dict[str, Any], deps: Any, gm: bool = False) -> List[Dict[str, Any]]:
    """**劇本排的席位** × 這一局有沒有入座（2026-10-19：前端要把缺席的席位置灰）。

    `deps.seat_plan`＝L6 從 `seats.json` 讀進來的列（`{seat, name, kind, character}`，只含
    human／agent）。這裡只加一個 `present`，**不碰 state**；投影照舊不做淺拷貝。
    """
    joined = {str(k) for k in ((state.get("seats") or {}).keys())}
    return [{
        "seat": str(r.get("seat") or ""),
        "name": str(r.get("name") or r.get("seat") or ""),
        "kind": seat_kind_public(str(r.get("kind") or ""), gm),
        "character": (r.get("character") or None),
        "present": str(r.get("seat") or "") in joined,
    } for r in (getattr(deps, "seat_plan", None) or ()) if r.get("seat")]

def _seat_public(state: Dict[str, Any], s: Dict[str, Any], gm: bool = False,
                 deps: Any = None) -> Dict[str, Any]:
    """席位在公開投影裡的樣子（不帶任何私密欄位）。

    條件（部位受傷／異常狀態）是**公開**的（傷在身上看得到）——但**只有真的有條件才加欄位**：
    沒啟用條件機制的桌（legacy）投影逐字不變（`test_packs` P27 咬住這一條）。
    """
    cur = state["turn"]
    out = {"seat": s["seat"], "name": s["name"], "kind": seat_kind_public(s["kind"], gm),
           "hp": s["hp"], "max_hp": s["max_hp"], "status": own(s["status"]),
           "is_turn": bool(cur and cur["seat"] == s["seat"])}
    cond = own(state.get("conditions", {}).get(s["seat"]) or {})
    if cond:
        out["conditions"] = cond
    # M2 原語：屬性加值／通用資源（跟條件同款——**只有真的有值才加欄位**，legacy 逐字不變）
    mods = own((state.get("attr_mods") or {}).get(s["seat"]) or {})
    if mods:
        out["attr_mods"] = mods
    _star = own((state.get("star") or {}).get(s["seat"]))
    if _star:
        out["star"] = _star
    _items = own((state.get("inventory") or {}).get(s["seat"]) or {})
    if _items:
        out["items"] = _items
    res = own((state.get("resources") or {}).get(s["seat"]) or {})
    if res:
        out["resources"] = res
    # M4：等級／經驗（跟條件同款——**只有真的有值才加欄位**）
    if (state.get("level") or {}).get(s["seat"]):
        out["level"] = int(state["level"][s["seat"]])
    if (state.get("exp") or {}).get(s["seat"]):
        out["exp"] = int(state["exp"][s["seat"]])
    # ★ 素材（2026-10-09）：這一席角色的立繪／頭像（有素材的模組才有這兩格）
    if deps is not None:
        _seat_art(deps, out, s.get("seat"))
        # 狀態圖示（部位受傷／異常）：有素材才有這一格
        _si = {str(k): _aurl(deps, f"icon:condition:{k}") for k in (s.get("status") or [])}
        _si = {k: v for k, v in _si.items() if v}
        if _si:
            out["status_icons"] = _si
    return out

def _board_section(state: Dict[str, Any], deps: Any, viewer: Optional[str]) -> Dict[str, Any]:
    """棋盤那一格（從前 `board.py:board_view`；規則/防火牆的計算仍在 deps 那側）。"""
    if not getattr(deps, "board_enabled", False):
        return {}
    nid = state["seats"][viewer].get("node") if viewer and viewer in state["seats"] else None
    cur = deps.node(nid) if nid in deps.nodes else None
    # ★2 商店：**表路**的貨品清單（config 的 legacy 商店照舊不露清單——它的商品不是道具）。
    # 只有「這一節點有店」且真的有貨品時才出現（legacy 桌：一個欄位都不加）。
    # ★屬性表（config.rules.attributes 是權威）：**非預設 [str,agi,wil] 時才露** ⇒ 舊桌視圖一字不變；
    #   前端據此畫出第四項（例 luck）——不然它只知道前三項（設計者 2026-10-07：「新加的屬性描述缺失」）。
    _rk = [str(x) for x in ((getattr(deps, "rules", None) or {}).get("attributes") or [])]
    if _rk and _rk != ["str", "agi", "wil"]:
        _out_rules_attrs = _rk
    else:
        _out_rules_attrs = None
    _slot = getattr(deps, "slot", lambda _n, _d={"mob": "mobs", "coin": "star_coins"}: _d.get(_n, _n))
    _ya = None if not cur else {
        "id": cur["id"], "jp": cur["jp"], "cn": cur.get("cn"), "type": cur["type"],
        "depth": cur.get("depth"), "flavor": cur.get("flavor"),
        "mob_left": state["board"][_slot("mob")].get(cur["id"], 0),
        # 舊別名（前端契約）：槽位是 mobs 時就是同一個數字
        **({"mobs_left": state["board"][_slot("mob")].get(cur["id"], 0)}
           if _slot("mob") == "mobs" else {}),
        "wish_box": own(cur.get("wish_box")), "shop": bool(cur.get("shop")),
        "game_room": bool(cur.get("game_room")),
        # 2026-10-21：有效期限內的門（search 開的）在期限內都算「新鮮」；
        # 另外給 `entrance_open_rounds_left`（還剩幾輪會關），讓 agent 知道要趕。
        "entrance_fresh": (_entrance_fresh(state, cur) if "monolith_entry" in (cur.get("tags") or []) else None),
        **({"entrance_open_rounds_left": _entrance_left(state, cur["id"])}
           if "monolith_entry" in (cur.get("tags") or []) else {}),
        # ★ 素材（2026-10-09）：這一格的場景圖（`bg:<節點 id>`；模組沒素材 ⇒ 不加）
        **({"art": _u} if (_u := _aurl(deps, f"bg:{cur['id']}")) else {}),
    }
    # ★ 2026-10-21（協力）：這一格還有哪些人——協力技（ally_bonus）只在同一格才有加成，
    #   不把這件事寫進投影，agent 根本不知道要「站在一起」。
    if _ya is not None and viewer:
        _here = sorted(sid for sid, ss in (state.get("seats") or {}).items()
                       if sid != viewer and (ss or {}).get("node") == cur["id"])
        if _here:
            _ya["seats_here"] = _here
    # ★4 遭遇／怪物表：這一格的敵人（沒表／不命中 ⇒ 不加欄位；legacy 逐字不變）
    # ⚠ 只在「這一格真的有敵人」時才露（塔內樓層／有小怪的節點）——城裡不會無緣無故顯示怪物
    if (_ya is not None and hasattr(deps, "monster_view")
            and (cur.get("depth") is not None
                 or int((state["board"][_slot("mob")] or {}).get(cur["id"], 0)) > 0)):
        _mv = own(deps.monster_view(cur))
        if _mv:
            # ★ 素材（2026-10-09）：敵人的圖示（`icon:monster:<id>`）
            _mu = _aurl(deps, f"icon:monster:{_mv.get('id')}") if _mv.get("id") else None
            if _mu:
                _mv["icon"] = _mu
            _ya["monster"] = _mv
    if _ya is not None and cur.get("shop") and hasattr(deps, "shop_view"):
        _si = own(deps.shop_view(seat=viewer, node_id=cur["id"]))
        if _si:
            # ★ 素材（2026-10-09）：貨品的圖示（跟 `you.items` 同一套別名）
            _ya["shop_items"] = own(_with_icon(
                deps, _si, lambda r: f"icon:item:{r.get('id')}" if r.get("id") else ""))
    return {**({"rules_attrs": _out_rules_attrs} if _out_rules_attrs else {}), "board": {
        "name": deps.board.get("board_name"),
        "work": deps.board.get("work"),
        "node_count": len(deps.nodes),
        "you_are": _ya,
        "can_go": [] if not cur else own(deps.neighbors(cur["id"])),
        # ★ 2026-10-21：舊的 `entrances`（true＝還開著）語意反直覺 ⇒ 改成 `entrance_used`
        #   （true＝那個塔的入口已經被用掉，要去塔前 search 重開）。這顆是**全桌旗標**：
        #   單一席位要看自己的門請讀 `you_are.entrance_fresh`。
        "entrance_used": {k: (not v) for k, v in own(state["board"]["entrances"]).items()},
        "mob": own(state["board"][_slot("mob")]),
        **({"mobs": own(state["board"][_slot("mob")])}
           if _slot("mob") == "mobs" else {}),
        "roster": [{"seat": r["seat"], "name": r["name"], "faction": r.get("faction"),
                    "role": r.get("role")} for r in deps.roster.values()],
        # 隊友位置（信息防火牆：塔內只給「塔內」，不給層數）——名單在 deps 那側算
        "party": own(deps.party_view(viewer=viewer)),
    }}

def _semi_section(state: Dict[str, Any], deps: Any, viewer: Optional[str]) -> Dict[str, Any]:
    """半合作那一格（從前 `semic.py:semic_view`）：只投影「該席位該知道的」。"""
    if not getattr(deps, "semic_enabled", False):
        return {}
    st = state["semi"]
    out: Dict[str, Any] = {
        "settled": own(st.get("settled")),
    }
    if not viewer:
        return out
    # ⚠ 觀眾／還沒入座的席位（例：`/api/state?seat=p1` 但 p1 沒 join）只給公開那半。
    #   以前直接往下走 `_check_goal` ⇒ `state["seats"][viewer]` KeyError（500）。
    if viewer not in (state.get("seats") or {}):
        return out
    spec = deps.seats_spec.get(viewer) or {}
    # 进度与节流。kind 必须走 _seat_kind：**动态入座的席位不在 seats.json 里**，
    # 只看 spec 会让它拿不到 throttle ⇒ 前端额度显示成「—」（2026-10-05 整合测试抓到）。
    if deps._seat_kind(viewer) in ("human", "agent"):
        out["throttle"] = {"used": own(st["counters"].get(viewer, {})),
                           "limits": {"act": deps._round_limit(viewer, "act"),
                                      "say": deps._round_limit(viewer, "say")}}
    gid = deps.goal_by_seat.get(viewer)
    if gid:
        g = deps.goals[gid]
        ok, detail = deps._check_goal(viewer, gid)
        out["your_goal"] = {"id": gid, "title": g["title"], "text": g["text"],
                            "required": g.get("required"),
                            "achieved": bool(st["goals"].get(viewer, {}).get("achieved")),
                            "ready": ok, "progress": own(detail),
                            "note": "只有你知道。可以暗示、可以交易，别直接念出来。"}
    mine = [fid for fid, h in st["frag_holder"].items() if h == viewer]
    out["your_fragments"] = [{"id": fid, "name": deps._frag_title(fid),
                              "text": deps.clues[fid]["text"],
                              "supports": own(deps.clues[fid].get("supports"))} for fid in mine]
    seen = []
    for fid, froms in (st["sources"].get(viewer) or {}).items():
        seen.append({"id": fid, "name": deps._frag_title(fid), "from": sorted(set(froms)),
                     "text": deps.clues[fid]["text"]})
    out["seen_by_you"] = seen
    out["your_declarations"] = [{"kind": p["kind"], "node": p["node"], "seq": p["seq"]}
                                for p in st["pending"] if p["seat"] == viewer]
    out["round_waiting"] = own(deps.pending_actors())      # 本轮还没出手的人（公开信息）
    out["you_acted"] = bool((st.get("acted") or {}).get(viewer))
    out["you_ended"] = bool((st.get("ended") or {}).get(viewer))
    if spec.get("kind") in ("gm", "npc"):
        out["you_are"] = {"role": spec.get("character") or "GM",
                          "duty": spec.get("duty"),
                          "npc_knowledge": (own(deps.npc_knowledge(viewer))
                                            if spec.get("kind") == "npc" else None)}
    return out
