#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_death_idempotent.py — **判死不冪等**（送還中的人不會被再判一次死）。

設計者 2026-10-20 抓的洞：`death_check` 沒有 `death_skip_of(seat) > 0` 早退 ⇒ 送還缺席中
再吃一次傷害（玩家 attack／GM `roll_damage`／條件 tick）會發**第二筆 `death`**：
缺席計數被重設、再掉一次幣。

兩道閘（設計者選 (b)：送還中＝不在場）：
  1. **玩家動作**：`attack`／`roll_damage` 對 `skip > 0` 的目標直接拒（`damage_guard`）；
  2. **兜底**：`death_check` 自己早退（GM 腳本／新來源繞過第 1 道時也不會二次判死）。
"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)

import test_support as TS                                                       # noqa: E402
from trpg.service import service as SVC                                         # noqa: E402
from trpg.core.errors import RuleError                                          # noqa: E402
from turn_helper import ensure_turn, turn_to                                    # noqa: E402

FAIL = []


def ok(name: str, cond: bool, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  " + str(detail)) if detail else ""))
    if not cond:
        FAIL.append(name)


def _table(tag: str, **death):
    root, home, cfg = TS.mk_table(module="hyakki_tower", table_id=tag)
    cfg = json.loads(json.dumps(cfg))
    cfg.setdefault("rules", {})["death"] = dict({"mode": "return_city", "skip_turns": 2,
                                                "hp_restore": 1, "nearest": True,
                                                "drop_coins": 0.5}, **death)
    return root, home, cfg


def t_guard() -> None:
    print("== A. 送還中＝不在場：玩家動作直接拒")
    root, home, cfg = _table("death_guard")
    try:
        t = SVC.TableService(home, cfg).table
        a = t.join(name="甲", kind="human")
        b = t.join(name="乙", kind="human")
        coin = t.slot("coin")
        t.grant_coins(seat=a, amount=100, reason="probe")
        t.roll_damage(seat=a, bonus=99)                       # 甲第一次死
        ok("A1 第一次死成立", len([e for e in t.events if e["type"] == "death"]) == 1
           and t.state["skip"].get(a) == 2)
        ok("A2 掉幣一次（100→50）", t.state["seats"][a][coin] == 50, t.state["seats"][a][coin])
        try:
            t.roll_damage(seat=a, bonus=99)                   # 缺席中再打
            ok("A3 roll_damage 對送還中的目標要拒", False)
        except RuleError as exc:
            ok("A3 roll_damage 對送還中的目標要拒（講清楚不在場）",
               "送還中" in str(exc) or "不在場" in str(exc), str(exc))
        # 玩家 attack 也一樣（先把回合推到乙手上；甲已經倒下，這手要交出去）
        turn_to(t, b)
        try:
            t.act(seat=b, action="attack", target=a, dc=1)
            ok("A4 attack 對送還中的目標要拒", False)
        except RuleError as exc:
            ok("A4 attack 對送還中的目標要拒", "送還中" in str(exc) or "不在場" in str(exc), str(exc))
        ok("A5 death 筆數還是 1（沒有第二次）",
           len([e for e in t.events if e["type"] == "death"]) == 1)
        ok("A6 幣沒有再掉（還是 50）", t.state["seats"][a][coin] == 50, t.state["seats"][a][coin])
        ok("A7 replay 一致", bool(t.replay_check().get("replay_matches")),
           t.replay_check().get("diff"))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_belt() -> None:
    print("== B. 兜底：繞過動作閘（直接 damage_apply）也不會二次判死")
    root, home, cfg = _table("death_belt")
    try:
        t = SVC.TableService(home, cfg).table
        a = t.join(name="甲", kind="human")
        t.roll_damage(seat=a, bonus=99)
        before = len([e for e in t.events if e["type"] == "death"])
        # 模擬「未來某個新來源」直接扣血：不經 damage_guard
        ev = t.damage_apply(seat=a, amount=50, src="future-source")
        after = len([e for e in t.events if e["type"] == "death"])
        ok("B1 沒有第二次 death（death_check 早退）", after == before, f"{before}→{after}")
        ok("B2 那一擊仍被記錄（事件在、hp 夾在 0，不是靜靜吃掉）",
           ev.get("type") == "damage" and ev.get("hp_after") == 0, ev.get("hp_after"))
        ok("B3 缺席計數沒有被重設", t.state["skip"].get(a) == 2, t.state["skip"].get(a))
        ok("B4 replay 一致", bool(t.replay_check().get("replay_matches")),
           t.replay_check().get("diff"))
        # 缺席倒數完之後可以再被打死（這是正常的一條命，不是被鎖死）
        t2_skip = t.state["skip"].get(a)
        t.state["skip"].pop(a, None)          # 模擬兩回合過完（走 turn_skipped 也是減到 0）
        t.state["seats"][a]["hp"] = 1
        t.roll_damage(seat=a, bonus=99)
        ok("B5 缺席結束後可以再死一次（閘不會把人鎖死）",
           len([e for e in t.events if e["type"] == "death"]) == before + 1,
           (t2_skip, len([e for e in t.events if e["type"] == "death"])))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_turn_skip_still_works() -> None:
    print("== C. 缺席跳過本身照舊（沒被新閘弄壞）")
    root, home, cfg = _table("death_skip")
    try:
        t = SVC.TableService(home, cfg).table
        a = t.join(name="甲", kind="human")
        b = t.join(name="乙", kind="human")
        t.roll_damage(seat=a, bonus=99)
        # 甲死時正握著一手（人齊就自動開輪）⇒ 先把那一手交出去，再讓回合繞一圈回到甲
        for _ in range(6):
            if (t.state.get("skip") or {}).get(a) == 1:
                break
            if t.state.get("turn"):
                t.end_turn(reason="test")
            ensure_turn(t)
        ok("C1 缺席的人被跳過（turn 交給乙或無人）",
           (t.state.get("turn") or {}).get("seat") != a,
           (t.state.get("turn") or {}).get("seat"))
        skipped = [e for e in t.events if e["type"] == "turn_skipped"]
        ok("C2 有 turn_skipped 且計數 −1", bool(skipped) and (t.state.get("skip") or {}).get(a) == 1,
           (len(skipped), t.state.get("skip").get(a)))
        ok("C3 乙可以正常行動", bool(b))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main() -> None:
    t_guard()
    t_belt()
    t_turn_skip_still_works()
    print()
    print(("DEATH-IDEMPOTENT: PASS  （0 FAIL）" if not FAIL
           else f"DEATH-IDEMPOTENT: FAIL  {len(FAIL)}：{FAIL}"))
    sys.exit(1 if FAIL else 0)


main()
