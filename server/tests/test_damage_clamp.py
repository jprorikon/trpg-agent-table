#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_damage_clamp.py — **HP 單點收口**（`damage_apply` 是唯一算 `hp_after` 的地方）。

設計者 2026-10-20 抓的洞：`damage` 事件有 5 個發射點，每一處都自己寫 `hp - dmg`
（`engine/act.py` 的 attack／`roll_damage`、`rules/board/act.py` 的 fight 失敗、
`rules/board/state.py` 的主動怪、`rules/conditions.py` 的條件 tick）⇒ **HP 可以是負的**，
而折 state 只照抄 `ev["hp_after"]` ⇒ 投影／回執／公開日誌全都顯示 `-15`。

判據（五組）：
  A. `damage_apply` 本身：下限 0、事件形狀不變、amount 保留原值；
  B. 走真實入口：`roll_damage`／條件 tick ⇒ 事件、state、投影都不會負；
  C. **別的來源不得自己算 hp**（原始碼閘：5 個發射點一個都不准出現 `hp - dmg` 那種寫法）；
  D. `_apply` **不加 clamp**（舊局已經存著負數 ⇒ 加了就 replay 漂）；
  E. `op=replay` 在扣血之後仍一致。
"""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)

import test_support as TS                                                       # noqa: E402
from trpg.service import service as SVC                                         # noqa: E402
from trpg.core.errors import RuleError                                          # noqa: E402

FAIL = []


def ok(name: str, cond: bool, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  " + str(detail)) if detail else ""))
    if not cond:
        FAIL.append(name)


def _table(tag: str = "dmg_probe", **rules):
    import json
    root, home, cfg = TS.mk_table(module="hyakki_tower", table_id=tag)
    cfg = json.loads(json.dumps(cfg))
    cfg.setdefault("rules", {}).update(rules)
    return root, home, cfg


def t_apply() -> None:
    print("== A. damage_apply：下限 0、事件形狀、amount 原值")
    root, home, cfg = _table("dmg_apply", death={"mode": "off"})
    try:
        t = SVC.TableService(home, cfg).table
        seat = t.join(name="甲", kind="human")
        t.state["seats"][seat]["hp"] = 5
        ev = t.damage_apply(seat=seat, amount=20, src="probe")
        ok("A1 hp 5 吃 20 ⇒ hp_after=0（不是 -15）", ev.get("hp_after") == 0, ev.get("hp_after"))
        ok("A2 amount 保留原值（日誌要看得出他吃了 20）", ev.get("amount") == 20)
        ok("A3 state.hp 也是 0", t.state["seats"][seat]["hp"] == 0)
        ev2 = t.damage_apply(seat=seat, amount=7, src="probe")
        ok("A4 已經 0 再吃傷害 ⇒ 還是 0（不會累積成負）", ev2.get("hp_after") == 0)
        ok("A5 事件形狀沒變（type/target/amount/hp_after/src）",
           ev.get("type") == "damage" and ev.get("target") == seat and ev.get("src") == "probe")
        ok("A6 沒有這個席位 ⇒ 當場拒", _raises(lambda: t.damage_apply(seat="nope", amount=1),
                                          RuleError))
        _e = t.damage_apply(seat=seat, amount=1, extra={"node": "x", "chance": 0.5})
        ok("A7 extra 併進同一顆事件（其他來源要留痕跡）",
           _e.get("node") == "x" and _e.get("chance") == 0.5)
        ok("A8 extra 不能蓋掉核心欄位", _e.get("hp_after") == 0 and _e.get("amount") == 1)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_paths() -> None:
    print("== B. 真實入口：roll_damage／條件 tick")
    root, home, cfg = _table("dmg_paths", death={"mode": "off"})
    try:
        t = SVC.TableService(home, cfg).table
        seat = t.join(name="甲", kind="human")
        t.state["seats"][seat]["hp"] = 3
        r = t.roll_damage(seat=seat, bonus=20)
        ok("B1 roll_damage：回執的 target_hp=0（不是負）", r["target_hp"] == 0, r["target_hp"])
        ok("B2 roll_damage 的 damage 事件 hp_after=0", r["event"]["hp_after"] == 0)
        v = t.view(seat=seat)
        ok("B3 投影 you.hp / seats[].hp 都是 0",
           (v.get("you") or {}).get("hp") == 0
           and all(s.get("hp") >= 0 for s in v["seats"]), v.get("you", {}).get("hp"))
        # 條件 tick：中毒/出血每次結算都扣血
        t.set_condition(target=seat, id=_first_condition(t), value=True, turns=3, by="probe")
        t.state["seats"][seat]["hp"] = 2
        evs = t.conditions_tick(seat)
        dmg_evs = [e for e in evs if e.get("type") == "damage"]
        ok("B4 條件 tick 有扣血事件", bool(dmg_evs), [e.get("type") for e in evs])
        ok("B5 條件 tick 的 hp_after 也不會負",
           all(int(e.get("hp_after") or 0) >= 0 for e in dmg_evs),
           [(e.get("amount"), e.get("hp_after")) for e in dmg_evs])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _first_condition(t) -> str:
    ids = t.condition_ids()
    return str(ids[0])


def t_static() -> None:
    print("== C. 原始碼閘：5 個發射點都不准自己算 hp")
    def code_of(rel: str) -> str:
        with open(os.path.join(HERE, "trpg", rel), encoding="utf-8") as fh:
            return fh.read()

    files = ("engine/act.py", "rules/board/act.py", "rules/board/state.py", "rules/conditions.py")
    bad = []
    for rel in files:
        code = code_of(rel)
        for pat in ('hp_after=s["hp"] -', "hp_after = t[\"hp\"] -", 'hp_after=int(p.get("hp") or 0) -',
                    'hp_after=s["hp"] - dmg'):
            if pat in code:
                bad.append(f"{rel}: {pat}")
    ok("C1 沒有第二個地方自己算 hp_after", not bad, bad)
    ok("C2 engine/damage.py 是唯一算 hp_after 的地方",
       "hp_after = max(0, int(s.get(\"hp\") or 0) - amt)" in code_of("engine/damage.py"))
    for rel in files:
        code = code_of(rel)
        if "\"damage\"" in code:
            ok(f"C3 {rel} 的 damage 事件走 damage_apply",
               "damage_apply(" in code or "\"damage\"" not in code)


def t_apply_no_clamp() -> None:
    print("== D. _apply 不加 clamp（舊局的負數歷史值 replay 不會漂）")
    root, home, cfg = _table("dmg_legacy", death={"mode": "off"})
    try:
        t = SVC.TableService(home, cfg).table
        seat = t.join(name="甲", kind="human")
        t.state["seats"][seat]["hp"] = 5
        # 直接塞一筆「舊制」的負值事件（模擬歷史資料）——_apply 必須照抄
        t._emit("damage", public=True, target=seat, amount=20, hp_after=-15, src="legacy")
        ok("D1 舊事件照抄（state 可以是 -15，不改歷史）",
           t.state["seats"][seat]["hp"] == -15, t.state["seats"][seat]["hp"])
        ok("D2 replay 仍一致（沒有偷偷 clamp）",
           bool(t.replay_check().get("replay_matches")), t.replay_check().get("diff"))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_replay() -> None:
    print("== E. op=replay：新制扣血之後仍一致")
    root, home, cfg = _table("dmg_replay")
    try:
        t = SVC.TableService(home, cfg).table
        seat = t.join(name="甲", kind="human")
        t.state["seats"][seat]["hp"] = 4
        t.roll_damage(seat=seat, bonus=30)
        ok("E1 replay_matches", bool(t.replay_check().get("replay_matches")),
           t.replay_check().get("diff"))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _raises(fn, exc) -> bool:
    try:
        fn()
        return False
    except exc:
        return True


def main() -> None:
    t_apply()
    t_paths()
    t_static()
    t_apply_no_clamp()
    t_replay()
    print()
    print(("DAMAGE-CLAMP: PASS  （0 FAIL）" if not FAIL
           else f"DAMAGE-CLAMP: FAIL  {len(FAIL)}：{FAIL}"))
    sys.exit(1 if FAIL else 0)


main()
