#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""地塊表（`kind=lands`）＋「觸發的 `me`」自測。

驗的是行為，不是實作：
  A. 落地 ⇒ `land_stop` ＋ 表上的 `stop` 效果在**剛落地那個人**身上跑（`seat:"me"`）
  B. 沒有 lands 表／節點沒 `land` ⇒ 一個事件都不多發（legacy 零變更）
  C. 表壞掉（id 指不到／effect 沒人實作）⇒ **載入時**就叫
  D. 觸發的 ctx 帶 `me`（事件帶 seat）⇒ 一條 trigger 服務所有人
  E. 事件溯源：多了 land_stop 之後 replay 仍一致

跑法：python3 tests/test_lands.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from turn_helper import ensure_turn, drop_scene  # 拆景后的测试小工具

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from trpg.engine import Table  # noqa: E402

OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


BASE_CFG = {
    "table_id": "lands_test",
    "scenario": "lands test",
    "seed": 7,
    "packs": {"enabled": ["d20core", "board", "lands_test"]},
    "rules": {
        "name": "test-d20",
        "attributes": ["str", "agi", "wil"],
        "hp": {"base": 10, "per_str": 0},
        "check": {"die": 20, "attr_multiplier": 2, "dc_default": 12},
        "damage": {"die": 4, "attr": "str", "attr_multiplier": 0},
        "defend_bonus": 2,
        "actions": ["move", "check", "other"],
    },
    "board": {"file": "board.json"},
    "pacing": {"mode": "turn", "actions_per_scene": 5},
    "turn": {"seconds": 300, "actions_per_turn": 5},
}


def mk_home(*, lands=None, board_extra=None, blocks=None, triggers=None):
    tmp = tempfile.mkdtemp(prefix="trpg_lands_")
    os.makedirs(os.path.join(tmp, "packs", "lands_test", "tables"), exist_ok=True)
    _w(os.path.join(tmp, "config.json"), BASE_CFG)
    board = {
        "start_node": "a",
        "nodes": [
            {"id": "a", "jp": "A", "cn": "A", "type": "city", "edges": [{"to": "b", "kind": "road"}]},
            {"id": "b", "jp": "B", "cn": "B", "type": "city", "edges": [{"to": "a", "kind": "road"},
                                                                        {"to": "c", "kind": "road"}]},
            {"id": "c", "jp": "C", "cn": "C", "type": "city", "edges": [{"to": "b", "kind": "road"}]},
        ],
        "roster": [{"seat": "p1", "name": "甲", "attrs": {"str": 2, "agi": 2, "wil": 2}},
                   {"seat": "p2", "name": "乙", "attrs": {"str": 2, "agi": 2, "wil": 2}}],
    }
    for nid, land in (board_extra or {}).items():
        next(n for n in board["nodes"] if n["id"] == nid)["land"] = land
    _w(os.path.join(tmp, "board.json"), board)
    _w(os.path.join(tmp, "seats.json"), {"table": "lands_test", "work": "test",
                                        "seats": [{"seat": "p1", "kind": "human", "name": "甲", "character": None, "goal": None},
                                                  {"seat": "p2", "kind": "human", "name": "乙", "character": None, "goal": None}]})
    _w(os.path.join(tmp, "goals.json"), {"goals": []})
    _w(os.path.join(tmp, "clues.json"), {"claims": [], "fragments": []})
    scen = {"meta": {"id": "lands_test"}, "triggers": triggers or []}
    _w(os.path.join(tmp, "scenario.json"), scen)
    _w(os.path.join(tmp, "packs", "lands_test", "pack.json"), {
        "id": "lands_test", "name": "lands test",
        "requires": ["d20core", "board"],
        "conflicts": [],
        "provides": {
            "effects": ["heal", "damage", "fact_set"],
            "tables": ["tables/lands.json"],
        },
    })
    _w(os.path.join(tmp, "packs", "lands_test", "tables", "lands.json"),
       {"kind": "lands", "rows": lands if lands is not None else [
           {"id": "heal_spot", "name_jp": "回復格", "name_cn": "回复格",
            "stop": [{"heal": {"seat": "me", "bonus": 2}}], "params": {"k": 2}},
           {"id": "trap", "name_jp": "陷阱", "name_cn": "陷阱",
            "stop": [{"damage": {"seat": "me", "die": 0, "bonus": 2}}]},
       ]})
    return tmp


def _w(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)


def mk_table(tmp, cfg=None):
    cfg = dict(cfg or BASE_CFG)
    cfg["port"] = 0
    t = Table(home=tmp, cfg=cfg)
    t.join(seat="p1", kind="human", name="甲")
    t.join(seat="p2", kind="human", name="乙")
    return t


def main():
    print("== A. 落地 ⇒ land_stop ＋ stop 效果（me＝剛落地的人） ==")
    tmp = mk_home(board_extra={"b": "heal_spot", "c": "trap"})
    try:
        t = mk_table(tmp)
        ensure_turn(t)
        t.damage_apply(seat="p1", amount=5, src="test")       # 先扣到 5／10
        hp0 = t.state["seats"]["p1"]["hp"]
        r = t.act(seat="p1", action="move", target="b")
        check("A1 走到有地塊的格子", r.get("to") == "b", str(r)[:80])
        check("A2 地塊效果在『本人』身上（+2 HP）",
              t.state["seats"]["p1"]["hp"] == hp0 + 2,
              f"{hp0}->{t.state['seats']['p1']['hp']}")
        check("A3 land_stop 是內部事件（不公開、帶 land id）",
              any(e["type"] == "land_stop" and e.get("seat") == "p1"
                  and e.get("land") == "heal_spot" and not e.get("public", True) for e in t.events))
        hp1 = t.state["seats"]["p1"]["hp"]
        t.act(seat="p1", action="move", target="c")
        check("A4 陷阱格扣血（同一條 me）", t.state["seats"]["p1"]["hp"] == hp1 - 2,
              f"{hp1}->{t.state['seats']['p1']['hp']}")
        check("A5 另一席（沒落地）不受影響", t.state["seats"]["p2"]["hp"] == 10,
              str(t.state["seats"]["p2"]["hp"]))
        check("A6 replay 一致", bool(t.replay_check().get("replay_matches")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("== B. 沒有地塊 ⇒ 不多發事件 ==")
    tmp = mk_home()
    try:
        t = mk_table(tmp)
        ensure_turn(t)
        before = len(t.events)
        t.act(seat="p1", action="move", target="b")
        check("B1 節點沒 land ⇒ 沒有 land_stop", not any(e["type"] == "land_stop" for e in t.events))
        check("B2 事件只多了 action_declared＋move（2 筆，沒有地塊的份）",
              len(t.events) == before + 2, f"{before}->{len(t.events)}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("== C. 表壞掉 ⇒ 載入時就叫 ==")
    tmp = mk_home(lands=[{"id": "x", "stop": [{"heal": {"seat": "me", "bonus": 1}}]}],
                  board_extra={"b": "nope"})
    try:
        try:
            mk_table(tmp)
            ok = False
        except Exception as exc:                                        # noqa: BLE001
            ok = "nope" in str(exc)
        check("C1 節點 land 指到不存在的 id ⇒ LandError", ok)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    tmp = mk_home(lands=[{"id": "x", "stop": [{"condition_set": {"seat": "me", "id": "poison"}}]}],
                  board_extra={"b": "x"})
    try:
        try:
            mk_table(tmp)
            ok = False
        except Exception as exc:                                        # noqa: BLE001
            ok = "condition_set" in str(exc)
        check("C2 effect 沒被任何啟用的包宣告 ⇒ 載入時叫", ok)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("== D. 觸發的 me（一條 trigger 服務所有人） ==")
    trig = [{"id": "t_ping", "on": ["ping"],
             "then": [{"heal": {"seat": "me", "bonus": 1}},
                      {"fact_set": {"key": "last_actor", "value": {"var": "me.seat"}}}]}]
    tmp = mk_home(triggers=trig)
    try:
        t = mk_table(tmp)
        t.damage_apply(seat="p1", amount=3, src="test")
        t.damage_apply(seat="p2", amount=3, src="test")
        h1, h2 = t.state["seats"]["p1"]["hp"], t.state["seats"]["p2"]["hp"]
        t._emit("ping", public=True, seat="p2", text="p2 觸發")
        check("D1 只有動作人吃到 effect（p2 +1）", t.state["seats"]["p2"]["hp"] == h2 + 1,
              f"{h2}->{t.state['seats']['p2']['hp']}")
        check("D2 其他人不動（p1 不變）", t.state["seats"]["p1"]["hp"] == h1,
              f"{h1}->{t.state['seats']['p1']['hp']}")
        check("D3 fact_set 的 AST 取到 me.seat", t.state["facts"].get("last_actor") == "p2",
              str(t.state["facts"]))
        check("D4 replay 一致", bool(t.replay_check().get("replay_matches")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    if FAIL:
        print("FAILED:", FAIL)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
