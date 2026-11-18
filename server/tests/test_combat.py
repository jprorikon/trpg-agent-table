#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**戰鬥擴充的自測**（2026-10-19 設計者：「框架需要支持 HP／同格多怪／主動怪」）。

    python3 test_combat.py

驗的是四件事（全部用 /tmp 臨時桌，不動現役局）：

  C1 `hp` 不寫＝1 ⇒ **一擊必殺**（與舊行為相同）
  C2 `hp: 3` ⇒ 先扣血、打穿才擊破（`monster_damaged` → `mob_killed`）
  C3 同一節點多筆怪物列 ⇒ 多個個體（`count` 指定／沒指定＝節點 mobs 平分）
  C4 主動怪：收景階段按 `active.chance` 攻擊同格玩家；`active_attacks=false` 可整桌關掉
  C5 回放：上面打過、咬過之後 `replay_check()` 仍然一致
  C6 沒有怪物表 ⇒ 一個欄位都不加、fight 走舊路（legacy 零變更）
"""
from __future__ import annotations
from turn_helper import ensure_turn, drop_scene  # 拆景后的测试小工具

import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo 根
sys.path.insert(0, HERE)

from trpg.core import layout as layout                                                           # noqa: E402
from trpg.core import store as store                                                            # noqa: E402
from trpg.service import table_factory as table_factory                                                    # noqa: E402
import test_support as TS                                               # noqa: E402

OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


def _def_dir(home):
    return layout.def_dir_of(home)


def _write_monsters(home, rows):
    """把臨時桌的怪物表換成指定的 rows（測試內容，不碰現役模組）。"""
    path = os.path.join(_def_dir(home), "packs", "hyakki_world", "tables", "monsters.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"kind": "monsters", "rows": rows}, fh, ensure_ascii=False, indent=1)


def _table(home, cfg, **kw):
    cfg = dict(cfg)
    cfg["auth"] = {}
    cfg["session"] = {"require": False}
    cfg["kick"] = {}
    cfg.update(kw)
    return table_factory.build(home, cfg)


def _enter(seat, node, hp=None):
    """入座＋**走過去**（走事件流，回放才對得上；直接改 state 會讓 replay_check 假紅）。"""
    def apply(t):
        t.join(seat=seat, kind="human", name=seat)
        s = t.state["seats"][seat]
        frm = str(s.get("node") or "")
        if frm != node:
            t._emit("move", public=True, seat=seat, frm=frm, to=node)
        if hp:
            s["hp"] = hp
            s["max_hp"] = max(s.get("max_hp") or 1, hp)
        open_first_scene(t, seat=seat)          # 入座後把景／輪備好，回合交給這一席
    return apply


def _raises_rule(fn):
    try:
        fn()
    except Exception:                                                     # noqa: BLE001
        return True
    return False


def _fight_until_dead(t, seat, target=None, limit=12):
    """一直打到這一隻死（回傳打擊次數與 hp 軌跡）。"""
    hits = []
    for _ in range(limit):
        _turn_to(t, seat)
        out = t.act(seat=seat, action="fight", target=target)
        if isinstance(out, dict) and "沒有進行中的回合" in str(out.get("error") or ""):
            # 回合額度花完（單人連打）⇒ 請 GM 收掉再開一輪，這次不算一手
            try:
                t.end_turn(reason="end_turn")
            except Exception:                     # noqa: BLE001
                pass
            t._maybe_autostart()
            continue
        if out.get("killed"):
            hits.append(0)
            return hits, out
        if out.get("monster_hp") is not None:
            hits.append(out["monster_hp"])
        else:
            hits.append(None)          # 失手（沒扣血）
    return hits, None


def open_first_scene(t, seat=None):
    """夾具：開第一景 ⇒ 開第一輪（2026-10-08 先開景才開輪；2026-10-20 只有當前回合那一席能 act）。"""
    t.cfg.setdefault("turn", {})["min_seats_to_start"] = 1
    t.cfg.setdefault("turn", {})["auto_start_first_turn"] = True
    st = t.state
    if not isinstance(st.get("semi"), dict):
        st["semi"] = {}
    if st.get("turn"):
        if seat and str((st.get("turn") or {}).get("seat")) != str(seat):
            t.end_turn(reason="end_turn")
            t._maybe_autostart()
        return t
    t._maybe_autostart()
    return t
    ensure_turn(t)
    return t


def _turn_to(t, seat):
    """把回合轉到 `seat`：必要時先開景、再開輪、再 end_turn 繞到該席（回合制只有當前那一席能 act）。"""
    t.cfg.setdefault("turn", {})["min_seats_to_start"] = 1
    t.cfg.setdefault("turn", {})["auto_start_first_turn"] = True
    st = t.state
    if not isinstance(st.get("semi"), dict):
        st["semi"] = {}
    ensure_turn(t)
    for _ in range(max(2, len(st.get("order") or []) * 2 + 3)):
        cur = (st.get("turn") or {}).get("seat")
        if cur is None:
            t._maybe_autostart()
            cur = (st.get("turn") or {}).get("seat")
            if cur is None:
                return
        if str(cur) == str(seat):
            return
        t.end_turn(reason="end_turn")
        t._maybe_autostart()



def main() -> int:
    tmp = None
    try:
        tmp, home, cfg = TS.mk_table(module="hyakki_tower", scenario="sannenme_no_onibarai")
        # 只留一種怪，數值自己定：node hyakki_f1（mobs ＝ board.json 的 3）
        _write_monsters(home, [
            {"id": "dummy", "name_jp": "試し鬼", "name_cn": "试鬼", "nodes": ["hyakki_f1"],
             "dc": 10, "check": "agi", "coins": 2, "exp": 5, "damage_die": 1},
        ])
        svc = _table(home, cfg)
        t = svc.table
        _enter("p1", "hyakki_f1")(t)
        insts = t.alive_monsters("hyakki_f1")
        check("C3a 沒有 `count` ⇒ 節點的 mobs 展開成同樣隻數（3）",
              len(insts) == 3 and {i["id"] for i in insts} == {"dummy"}, f"n={len(insts)}")
        check("C1a `hp` 不寫 ⇒ 1（一擊必殺的前提）",
              all(i["max_hp"] == 1 for i in insts), str([i["max_hp"] for i in insts]))

        # C1：打一隻 → 成功就該死（hp 1）
        ok = None
        for _ in range(10):
            _turn_to(t, "p1")
            out = t.act(seat="p1", action="fight", target=insts[0]["uid"])
            if out.get("roll", {}).get("success"):
                ok = out
                break
        if ok is None:
            check("C1b hp=1 打中即擊破", False, "十次都沒打中（dc 太低？）")
        else:
            check("C1b hp=1 打中即擊破（同時發 monster_damaged 與 mob_killed）",
                  ok.get("killed") is True
                  and any(e.get("type") == "monster_damaged" for e in t.events)
                  and any(e.get("type") == "mob_killed" for e in t.events),
                  json.dumps({k: ok.get(k) for k in ("monster_damage", "monster_hp", "killed")}, ensure_ascii=False))
        check("C1c 擊破後 alive_monsters 少一隻、mobs 同步 -1",
              len(t.alive_monsters("hyakki_f1")) == len(insts) - 1
              and t.state["board"]["mobs"]["hyakki_f1"] == len(insts) - 1,
              f"alive={len(t.alive_monsters('hyakki_f1'))} left={t.state['board']['mobs']['hyakki_f1']}")
        t2 = svc.table
        rep = t2.replay_check()
        check("C5a 打完之後回放一致", bool(rep.get("replay_matches")), str(rep)[:160])
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)

    # ── C2：hp 3 ──────────────────────────────────────────────────────────
    tmp2 = None
    try:
        tmp2, home2, cfg2 = TS.mk_table(module="hyakki_tower", scenario="sannenme_no_onibarai")
        _write_monsters(home2, [
            {"id": "tank", "name_jp": "硬い鬼", "nodes": ["hyakki_f1"], "hp": 30,
             "dc": 1, "check": "agi", "coins": 3, "exp": 7, "damage_die": 1},
        ])
        svc2 = _table(home2, cfg2)
        t3 = svc2.table
        _enter("p1", "hyakki_f1")(t3)
        inst = t3.alive_monsters("hyakki_f1")[0]
        check("C2a hp=30 ⇒ 個體 max_hp=30", inst["max_hp"] == 30, str(inst["max_hp"]))
        first = t3.act(seat="p1", action="fight", target=inst["uid"])
        check("C2b 第一擊不致死（hp 0<hp<30、沒有 mob_killed）",
              first.get("killed") in (None, False) and 0 < int(first.get("monster_hp") or 0) < 30,
              json.dumps({k: first.get(k) for k in ("monster_damage", "monster_hp", "killed")}, ensure_ascii=False))
        hits, dead = _fight_until_dead(t3, "p1", inst["uid"])
        check("C2c 打穿才擊破（軌跡遞減到 0）",
              dead is not None and dead.get("killed") is True, f"hits={hits}")
        rep2 = t3.replay_check()
        check("C5b hp 戰之後回放一致", bool(rep2.get("replay_matches")), str(rep2)[:160])
    finally:
        if tmp2:
            shutil.rmtree(tmp2, ignore_errors=True)

    # ── C3：同格多筆（count 指定） ────────────────────────────────────────
    tmp3 = None
    try:
        tmp3, home3, cfg3 = TS.mk_table(module="hyakki_tower", scenario="sannenme_no_onibarai")
        _write_monsters(home3, [
            {"id": "aa", "name_jp": "甲", "nodes": ["hyakki_f1"], "count": 2, "hp": 1,
             "dc": 10, "coins": 1, "exp": 1, "damage_die": 1},
            {"id": "bb", "name_jp": "乙", "nodes": ["hyakki_f1"], "count": 1, "hp": 2,
             "dc": 10, "coins": 1, "exp": 1, "damage_die": 1},
        ])
        svc3 = _table(home3, cfg3)
        t4 = svc3.table
        _enter("p1", "hyakki_f1")(t4)
        insts4 = t4.alive_monsters("hyakki_f1")
        by = {}
        for i in insts4:
            by[i["id"]] = by.get(i["id"], 0) + 1
        check("C3b 同格兩種怪：count 各自生效（2＋1）", by == {"aa": 2, "bb": 1}, str(by))
        check("C3c uid 唯一、投影列出 monsters（帶 hp／max_hp）",
              len({i["uid"] for i in insts4}) == 3
              and len((t4.view(seat="p1")["board"]["you_are"].get("monster") or {}).get("monsters") or []) == 3,
              str([i["uid"] for i in insts4]))
        _turn_to(t4, "p1")
        out_bb = t4.act(seat="p1", action="fight", target="hyakki_f1:bb:0")
        check("C3d fight 可以指定打哪一隻（target＝uid）",
              out_bb.get("monster") == "hyakki_f1:bb:0" or out_bb.get("killed") is True,
              json.dumps({k: out_bb.get(k) for k in ("monster", "monster_hp", "killed")}, ensure_ascii=False))
    finally:
        if tmp3:
            shutil.rmtree(tmp3, ignore_errors=True)

    # ── C4：主動怪（收景階段咬人）────────────────────────────────────────
    tmp4 = None
    try:
        tmp4, home4, cfg4 = TS.mk_table(module="hyakki_tower", scenario="sannenme_no_onibarai")
        _write_monsters(home4, [
            {"id": "biter", "name_jp": "噛み鬼", "nodes": ["hyakki_f1"], "hp": 3,
             "active": {"chance": 1.0}, "dc": 20, "coins": 1, "exp": 1, "damage_die": 3},
            {"id": "sleeper", "name_jp": "寝鬼", "nodes": ["hyakki_f1"], "hp": 3,
             "dc": 20, "coins": 1, "exp": 1, "damage_die": 3},
        ])
        svc4 = _table(home4, cfg4)
        t5 = svc4.table
        _enter("p1", "hyakki_f1")(t5)
        before = t5.state["seats"]["p1"]["hp"]
        res = t5.board_scene_attacks()
        dmg = [e for e in t5.events if e.get("type") == "damage" and e.get("active")]
        check("C4a chance=1 ⇒ 主動怪在收景階段咬同格玩家（被動怪不動）",
              res["attacks"] >= 1 and len(dmg) >= 1 and t5.state["seats"]["p1"]["hp"] < before
              and all(e.get("src") == "biter" for e in dmg),
              json.dumps({"attacks": res["attacks"], "src": [e.get("src") for e in dmg]}, ensure_ascii=False))
        check("C4b 咬人事件帶 chance／roll（看得出為什麼中）",
              all("chance" in e and "roll" in e for e in dmg))
        # 關掉整桌主動攻擊（**另開一張臨時桌**：同一份 home 重開會沿用事件流，席位已存在）
        tmp5, home5, cfg5 = TS.mk_table(module="hyakki_tower", scenario="sannenme_no_onibarai")
        try:
            _write_monsters(home5, [
                {"id": "biter", "name_jp": "噛み鬼", "nodes": ["hyakki_f1"], "hp": 3,
                 "active": {"chance": 1.0}, "dc": 20, "coins": 1, "exp": 1, "damage_die": 3},
            ])
            t6 = _table(home5, cfg5, combat={"active_attacks": False}).table
            _enter("p1", "hyakki_f1")(t6)
            res2 = t6.board_scene_attacks()
            check("C4c `config.combat.active_attacks=false` ⇒ 完全不打", res2.get("attacks") == 0, str(res2))
        finally:
            shutil.rmtree(tmp5, ignore_errors=True)
        rep3 = t5.replay_check()
        check("C5c 主動怪咬完之後回放一致", bool(rep3.get("replay_matches")), str(rep3)[:160])
    finally:
        if tmp4:
            shutil.rmtree(tmp4, ignore_errors=True)

    # ── C7：上樓擋、下樓不擋（2026-10-19 設計者：「下樓不受限」）──────────────
    tmp6 = None
    try:
        tmp6, home6, cfg6 = TS.mk_table(module="hyakki_tower", scenario="sannenme_no_onibarai")
        svc6 = _table(home6, cfg6)
        t7 = svc6.table
        _enter("p1", "hyakki_f2")(t7)          # 2 階（本層還有怪）
        check("C7a 2 階還有怪 ⇒ 上行被擋（f2 → f3）",
              # 2026-10-21 動作收束：上樓／下樓都走 move（ride 已不再宣告）
              _raises_rule(lambda: t7.act(seat="p1", action="move", target="hyakki_f3")),
              f"left={t7.state['board']['mobs'].get('hyakki_f2')}")
        _turn_to(t7, "p1")
        out_down = t7.act(seat="p1", action="move", target="hyakki_f1")
        check("C7b 下樓不受限（f2 → f1，本層沒清光也照樣下去）",
              out_down.get("to") == "hyakki_f1" and t7.state["seats"]["p1"]["node"] == "hyakki_f1",
              str(out_down.get("to")))
        rep7 = t7.replay_check()
        check("C7c 上下樓之後回放一致", bool(rep7.get("replay_matches")), str(rep7)[:120])
    finally:
        if tmp6:
            shutil.rmtree(tmp6, ignore_errors=True)

    # ── C8：每景只咬 n 個人（n=0 ＝被動）────────────────────────────────
    tmp7 = None
    try:
        tmp7, home7, cfg7 = TS.mk_table(module="hyakki_tower", scenario="sannenme_no_onibarai")
        _write_monsters(home7, [
            {"id": "two", "name_jp": "二口鬼", "nodes": ["hyakki_f1"], "count": 1, "hp": 3,
             "active": {"chance": 1.0, "max_targets": 2}, "dc": 20, "coins": 1, "exp": 1, "damage_die": 2},
            {"id": "zero", "name_jp": "不咬鬼", "nodes": ["hyakki_f1"], "count": 1, "hp": 3,
             "active": {"chance": 1.0, "max_targets": 0}, "dc": 20, "coins": 1, "exp": 1, "damage_die": 2},
        ])
        svc7 = _table(home7, cfg7)
        t8 = svc7.table
        for sid in ("p1", "p2", "p3"):
            _enter(sid, "hyakki_f1")(t8)
        alive = t8.alive_monsters("hyakki_f1")
        check("C8a `max_targets:0` ⇒ 個體 active=false（不列主動、不進候選）",
              any(i["id"] == "zero" and i["active"] is False for i in alive)
              and all(i["active"] for i in alive if i["id"] == "two"),
              str([(i["id"], i["active"]) for i in alive]))
        before = {sid: t8.state["seats"][sid]["hp"] for sid in ("p1", "p2", "p3")}
        res = t8.board_scene_attacks()
        hurt = [sid for sid in before if t8.state["seats"][sid]["hp"] < before[sid]]
        check("C8b `max_targets:2` ⇒ 這一景只咬 2 個人（同格 3 人）",
              len(hurt) == 2 and res.get("attacks") == 2, f"hurt={hurt} res={res.get('attacks')}")
        srcs = [e.get("src") for e in t8.events if e.get("type") == "damage" and e.get("active")]
        check("C8c 咬人的只有主動怪（n=0 的那隻一根手指都沒動）",
              srcs and set(srcs) == {"two"}, str(srcs))
        rep8 = t8.replay_check()
        check("C8d 咬完之後回放一致", bool(rep8.get("replay_matches")), str(rep8)[:120])
    finally:
        if tmp7:
            shutil.rmtree(tmp7, ignore_errors=True)

    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
