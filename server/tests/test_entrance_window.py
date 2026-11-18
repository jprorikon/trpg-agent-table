#!/usr/bin/env python3
"""入口有效期限（B2 軟化版）＋ 獨走代價（B3）的測試（2026-10-21）。

W0 進塔會吃掉「一次性」的門（歷史行為不變）
W1 `search` 開出來的門在 N 輪內**多人都能進**、進塔不消耗它；投影給剩餘輪數
W2 期限過了門自己關（要再 search）
W3 塔裡只剩自己一個人 ⇒ 記憶流失 ×`memory_drain_solo_multiplier`
W4 `wish_box_min_party` > 1 ⇒ 單人祈願被擋
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from trpg.core import layout, store                    # noqa: E402
from trpg.engine import Table                          # noqa: E402
from trpg.rules import packs                           # noqa: E402

OK, FAIL = [], []


def check(name: str, cond, detail: str = "") -> None:
    ok = bool(cond)
    (OK if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


def mk_table(*, tower=None, start_node="hyakki", seats=("p1", "p2")) -> Table:
    def_dir = layout.must_locate_scenario(ROOT, "hyakki_tower", "sannenme_no_onibarai")
    tmp = tempfile.mkdtemp(prefix="trpg_door_")
    for f in ("board.json", "goals.json", "clues.json", "seats.json", "scenario.json"):
        p = os.path.join(def_dir, f)
        if os.path.exists(p):
            shutil.copy(p, os.path.join(tmp, f))
    psrc = os.path.join(def_dir, "packs")
    if os.path.isdir(psrc):
        shutil.copytree(psrc, os.path.join(tmp, "packs"))
        packs.seed_module_packs(os.path.dirname(psrc), tmp)
    board = json.load(open(os.path.join(tmp, "board.json"), encoding="utf-8"))
    board["start_node"] = start_node
    for n in board["nodes"]:
        if n["id"] == start_node:
            n["mobs"] = 0                              # 塔前不放怪，專測門
    json.dump(board, open(os.path.join(tmp, "board.json"), "w", encoding="utf-8"), ensure_ascii=False)
    cfg = layout.read_table_config(def_dir)
    for k in ("hub_base", "public_prefix"):
        cfg.pop(k, None)
    cfg.update({"port": 0, "table_id": "doortest", "run_id": "doortest"})
    cfg.setdefault("auth", {})
    cfg.setdefault("session", {})["require"] = False
    cfg.setdefault("turn", {}).update({"auto_start_first_turn": False, "policy_on_timeout": "wait",
                                      "end_turn_on_action": True, "actions_per_turn": 6,
                                      "says_per_turn": 6})
    cfg.setdefault("tower", {}).update(tower or {})
    t = Table(home=tmp, cfg=cfg, store=store.FileStore(tmp))
    for s in seats:
        t.join(seat=s, name=s, kind="human")
    return t


def cur_seat(t: Table) -> str:
    return str((t.state.get("turn") or {}).get("seat") or "")


def turn_to(t: Table, seat: str) -> None:
    for _ in range(30):
        if cur_seat(t) == seat:
            return
        t.act(seat=cur_seat(t), action="end_turn")


def open_door(t: Table, seat: str) -> dict:
    """讓 seat 在塔前 search，直到成功開門（回傳 act 結果）。"""
    for _ in range(8):
        turn_to(t, seat)
        r = t.act(seat=seat, action="search")
        if (r.get("found") or {}).get("entrance"):
            return r
    return {}


def main() -> int:
    print("== W0／W1／W2 入口有效期限 ==")
    t = mk_table(tower={"entrance_open_rounds": 2})
    try:
        t.start_turn()
        turn_to(t, "p1")
        t.act(seat="p1", action="move", target="hyakki_f1")
        v = t.view(seat="p1")
        check("W0 一次性門：進塔就吃掉（歷史行為不變）",
              v["board"]["entrance_used"]["hyakki"] is True, str(v["board"].get("entrance_used")))
        turn_to(t, "p1")
        t.act(seat="p1", action="move", target="hyakki")         # 回塔前
        r = open_door(t, "p1")
        found = r.get("found") or {}
        check("W1a search 開門回傳有效期限（N 輪）",
              found.get("open_rounds") == 2 and found.get("until_round"), json.dumps(found, ensure_ascii=False))
        check("W1b 開門給一句人話（note）", "入口" in str(r.get("note") or ""), str(r.get("note"))[:40])
        v = t.view(seat="p1")
        check("W1c 期限內的門在投影裡是新鮮的＋給剩餘輪數",
              v["board"]["you_are"]["entrance_fresh"] is True
              and v["board"]["you_are"].get("entrance_open_rounds_left") is not None,
              json.dumps(v["board"]["you_are"], ensure_ascii=False)[:120])
        turn_to(t, "p1")
        r1 = t.act(seat="p1", action="move", target="hyakki_f1")
        check("W1d 期限內 p1 進塔", r1.get("floor") == "hyakki_f1", json.dumps(r1, ensure_ascii=False)[:80])
        turn_to(t, "p2")
        r2 = t.act(seat="p2", action="move", target="hyakki_f1")
        check("W1e 期限內隊友 p2 也進得去（門沒被吃掉）",
              r2.get("floor") == "hyakki_f1", json.dumps(r2, ensure_ascii=False)[:80])
        # W2：兩人都出來，等期限過去
        for _ in range(6):
            turn_to(t, "p1")
            try:
                t.act(seat="p1", action="move", target="hyakki")
            except Exception:                                     # noqa: BLE001
                pass
            turn_to(t, "p2")
            try:
                t.act(seat="p2", action="move", target="hyakki")
            except Exception:                                     # noqa: BLE001
                pass
            if int(t.state.get("round_no") or 0) > int((found.get("until_round") or 0)):
                break
        v = t.view(seat="p1")
        check("W2 期限過了門自己關（投影不再是新鮮的）",
              v["board"]["you_are"]["entrance_fresh"] is False,
              json.dumps({"rno": t.state.get("round_no"), "until": found.get("until_round"),
                          "fresh": v["board"]["you_are"]["entrance_fresh"]}, ensure_ascii=False))
    finally:
        shutil.rmtree(str(t.home), ignore_errors=True)

    print("== W3 獨自進塔的記憶代價 ==")
    t2 = mk_table(tower={"memory_per_turn_inside": 2, "memory_drain_solo_multiplier": 3,
                        "memory_drain_every_rounds": 1, "entrance_open_rounds": 2})
    try:
        t2.start_turn()
        turn_to(t2, "p1")
        t2.act(seat="p1", action="move", target="hyakki_f1")
        turn_to(t2, "p2")                                    # 交棒（p1 仍在塔內）
        _m0 = int(t2.state["seats"]["p1"]["memory"])
        turn_to(t2, "p1")                                    # p1 的回合開始 ⇒ 結算塔內流失
        _m1 = int(t2.state["seats"]["p1"]["memory"])
        check("W3 塔裡只有自己 ⇒ 記憶流失 ×3（基礎 2 ⇒ 6）", _m0 - _m1 >= 6, f"{_m0} → {_m1}")
    finally:
        shutil.rmtree(str(t2.home), ignore_errors=True)

    print("== W4 祈願箱需要人數 ==")
    t3 = mk_table(start_node="hyakki_f5", tower={"wish_box_min_party": 2})
    try:
        t3.state["seats"]["p2"]["node"] = "hyakki"          # 兩個人都起在 f5 就測不到「只有一人」了
        t3.state["board"]["boss_defeated"]["hyakki_f4"] = True   # 解鎖隱藏層（不然 p2 上不去）
        t3.start_turn()
        turn_to(t3, "p1")
        err = ""
        try:
            t3.act(seat="p1", action="pray")
        except Exception as exc:                              # noqa: BLE001
            err = str(exc)
        check("W4a 一個人在箱前祈願 ⇒ 被擋且講清楚要幾個人",
              "祈願箱" in err and "2" in err, err[:70])
        turn_to(t3, "p2")
        for step in ("hyakki_f1", "hyakki_f2", "hyakki_f3", "hyakki_f4", "hyakki_f5"):
            try:
                t3.act(seat="p2", action="move", target=step)
            except Exception as exc:                          # noqa: BLE001
                print("    （p2 上樓失敗）", step, str(exc)[:50])
                continue
            turn_to(t3, "p2")
        turn_to(t3, "p1")
        ok = True
        err = ""
        try:
            t3.act(seat="p1", action="pray")
        except Exception as exc:                              # noqa: BLE001
            ok = False
            err = str(exc)
        check("W4b 兩個人在塔裡 ⇒ 祈願放行", ok, err[:70])
    finally:
        shutil.rmtree(str(t3.home), ignore_errors=True)

    print("== W5 N=1 也保底含下一輪（不會白搜）==")
    t4 = mk_table(tower={"entrance_open_rounds": 1})
    try:
        def advance_to(t, want_rno: int) -> None:
            """把輪次推到 want_rno（測試桌 end_turn_on_action=True ⇒ 用 end_turn 推）。"""
            guard = 0
            while int(t.state.get("round_no") or 0) < want_rno and guard < 40:
                cur = str((t.state.get("turn") or {}).get("seat") or "")
                if not cur:
                    t.start_turn()
                else:
                    t.act(seat=cur, action="end_turn")
                guard += 1

        t4.start_turn()
        turn_to(t4, "p1")
        t4.act(seat="p1", action="move", target="hyakki_f1")     # 先吃掉那個一次性門
        turn_to(t4, "p1")
        t4.act(seat="p1", action="move", target="hyakki")
        turn_to(t4, "p1")
        r = open_door(t4, "p1")
        found = r.get("found") or {}
        rno0 = int(t4.state.get("round_no") or 0)
        check("W5a 旋鈕設 1 ⇒ 實際抬到下限 2 輪（投影／事件同一個數）",
              found.get("open_rounds") == 2 and int(found.get("until_round") or 0) == rno0 + 2,
              json.dumps(found, ensure_ascii=False))

        advance_to(t4, rno0 + 1)                                # 開門當輪不進塔
        turn_to(t4, "p1")
        v = t4.view(seat="p1")["board"]["you_are"]
        check("W5b 開門當輪沒進，下一輪門還開著",
              v.get("entrance_fresh") is True,
              json.dumps({"rno": t4.state.get("round_no"), "fresh": v.get("entrance_fresh"),
                          "left": v.get("entrance_open_rounds_left")}, ensure_ascii=False))
        got, err = None, ""
        try:
            got = t4.act(seat="p1", action="move", target="hyakki_f1")
        except Exception as exc:                              # noqa: BLE001
            err = str(exc)
        check("W5c（N=1 的洞）下一輪真的進得去 ⇒ 不白搜",
              (got or {}).get("floor") == "hyakki_f1", err[:70] or json.dumps(got, ensure_ascii=False)[:70])

        # 再往下一輪：這時候才到期
        advance_to(t4, rno0 + 2)
        turn_to(t4, "p1")
        try:
            t4.act(seat="p1", action="move", target="hyakki")
        except Exception:                                     # noqa: BLE001
            pass
        v = t4.view(seat="p1")["board"]["you_are"]
        check("W5d 再下一輪才到期（有效期＝當輪＋1）",
              v.get("entrance_fresh") is False,
              json.dumps({"rno": t4.state.get("round_no"), "fresh": v.get("entrance_fresh")},
                         ensure_ascii=False))
    finally:
        shutil.rmtree(str(t4.home), ignore_errors=True)

    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    for f in FAIL:
        print("  FAILED:", f)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
