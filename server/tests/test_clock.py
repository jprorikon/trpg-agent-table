#!/usr/bin/env python3
"""時鐘（clocks）自測（2026-10-20）。

    python3 tests/test_clock.py

測的是「世界自己的時鐘」這個維度：
  A. `scenario.json` 宣告 `clocks` ＋ 觸發 `clock_tick` ⇒ `state["clocks"]` 由 `clock_changed` 事件折出
  B. 上限（`max`）會被夾住，`clock_set` 可以歸零
  C. AST 看得到 `clocks.<name>`（ending／trigger 的 when 用它）
  D. 回放一致（`replay_check()`）
"""
from __future__ import annotations
from turn_helper import ensure_turn, drop_scene  # 拆景后的测试小工具

import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tests"))

import test_support as ts                                                            # noqa: E402
from trpg.core import store as store                                                 # noqa: E402
from trpg.engine import Table                                                       # noqa: E402

OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


def build(*, clocks=None, extra_triggers=None, extra_endings=None, scenario="sannenme_no_onibarai"):
    root, home, cfg = ts.mk_table("hyakki_tower", scenario=scenario, table_id="clk", keep=True)
    defdir = os.path.join(root, "modules", "hyakki_tower", "scenarios", scenario)
    sc = json.load(open(os.path.join(defdir, "scenario.json"), encoding="utf-8"))
    sc["clocks"] = clocks or {"night": {"max": 3}}
    sc.setdefault("triggers", []).extend(extra_triggers or [])
    sc.setdefault("endings", []).extend(extra_endings or [])
    with open(os.path.join(defdir, "scenario.json"), "w", encoding="utf-8") as fh:
        json.dump(sc, fh, ensure_ascii=False)
    cfg.setdefault("pacing", {})["mode"] = "turn"
    cfg.setdefault("turn", {}).update({"auto_start_first_turn": False, "seconds": 300})
    cfg["session"] = {"require": False}
    table = Table(home=defdir, cfg=cfg, store=store.FileStore(home), clock=lambda: 0.0)
    return table


def main() -> int:
    print("== A/D. clock_tick 折進 state ＋ 回放一致")
    tick = [{"id": "t_night", "on": ["turn_ended"], "then": [{"clock_tick": {"name": "night"}}]}]
    end = [{"id": "e_night", "kind": "neutral", "when": {">=": [{"var": "clocks.night"}, 3]}}]
    table = build(extra_triggers=tick, extra_endings=end)
    table.join(seat="p1", name="A", kind="human")
    table.join(seat="p2", name="B", kind="human")
    ensure_turn(table)

    # `end_turn` 會自動開下一手 ⇒ 只開一次、連收三次
    # ⚠ 2026-10-20：夾具改從 `config.example.json` 取樣後，「自動開下一手」不一定開著
    #   ⇒ 收手前先確保這一手是開的（已在進行中就不要重開）。
    def _next_turn(t):
        try:
            ensure_turn(t)
        except Exception:                                                 # noqa: BLE001
            pass

    ensure_turn(table)
    for i in range(2):
        table.end_turn(reason="t")
        _next_turn(table)
    check("A1 兩次 turn_ended ⇒ 時鐘 2", table.state["clocks"].get("night") == 2,
          str(table.state.get("clocks")))
    check("A2 clock_changed 事件帶結果值 after",
          [e.get("after") for e in table.events if e["type"] == "clock_changed"] == [1, 2],
          str([e.get("after") for e in table.events if e["type"] == "clock_changed"]))
    check("A3 還沒到上限 ⇒ 沒有判結局", not table.state.get("ended"), str(table.state.get("ended")))

    table.end_turn(reason="t")
    check("C1 clocks.night 到 3 ⇒ 判 neutral 結局（AST 看得到 clocks）",
          table.state.get("ended") == "e_night", str(table.state.get("ended")))
    _rp = table.replay_check()
    check("D1 回放一致（replay_check 沒有差異）", bool(_rp.get("replay_matches")), str(_rp)[:120])

    print("== B. max 夾住 ＋ clock_set 歸零")
    table2 = build(clocks={"night": {"max": 3}},
                   extra_triggers=[{"id": "t_over", "on": ["turn_ended"], "once": True,
                                    "then": [{"clock_tick": {"name": "night", "value": 9}}]}])
    table2.join(seat="p1", name="A", kind="human")
    ensure_turn(table2)
    ensure_turn(table2)
    table2.end_turn(reason="t")
    check("B1 value=9 被 max 夾到 3", table2.state["clocks"].get("night") == 3,
          str(table2.state.get("clocks")))
    gm = table2.module
    made = gm._apply_effect(table2, {"clock_set": {"name": "night", "value": 0}},
                            gm.ctx(table2), by="gm")
    check("B2 clock_set 歸零", table2.state["clocks"].get("night") == 0,
          str(table2.state.get("clocks")))
    check("B3 clock_set 也有事件（回放一致）", bool(made) and made[0]["type"] == "clock_changed",
          str(made)[:80])

    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
