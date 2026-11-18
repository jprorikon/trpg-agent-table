#!/usr/bin/env python3
"""玩法循環幾個旋鈕的自測（2026-10-20；**2026-10-21 拆景後改寫成「輪」語意**）。

    python3 tests/test_loop_knobs.py

A. **碎片按欠帳出土＋drop_policy 真的生效**（`clues.json` 的 drop_policy 以前引擎完全沒讀）
B. **移動不佔行動格**（`turn.free_actions`）
C. **輪界**：非末席結束 ⇒ 不收輪；末席結束 ⇒ `round_ended`＋`round_started` 並自動開下一輪
D. **逐席節拍**（`turn.seconds_by_kind`／`max_waits_by_kind`）

原本 C 組測的是「收景綁手數（`pacing.auto_close_scene`）」、E 組測「催 GM 開下一景」——
2026-10-21 設計者定案拆掉「景」整層，這兩組的語意由**輪界**接手：
收景那一腳 → `case_round_boundary`；催下一景那一腳 → `tests/test_round_kick.py`（每輪叫 GM 說一句）。

全部用臨時桌（`test_support.mk_table`），不碰現役桌與現役事件流。
"""
from __future__ import annotations
from turn_helper import ensure_turn  # 拆景后的测试小工具

import json
import os
import shutil
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


def build(*, clues=None, turn=None, scenario="sannenme_no_onibarai"):
    """造一張臨時桌（定義層可以改 clues／節拍），回 `(table, defdir, home)`。

    `turn` 就是 `config.turn`（2026-10-21 起是**唯一**的節拍段；`pacing` 已不存在）。
    """
    root, home, cfg = ts.mk_table("hyakki_tower", scenario=scenario, table_id="kb", keep=True)
    defdir = os.path.join(root, "modules", "hyakki_tower", "scenarios", scenario)
    if clues is not None:
        with open(os.path.join(defdir, "clues.json"), "w", encoding="utf-8") as fh:
            json.dump(clues, fh, ensure_ascii=False)
    cfg.pop("pacing", None)                     # 舊旋鈕段：引擎現在看到 mode=scene 會當場拒
    cfg.setdefault("turn", {}).update(turn or {})
    cfg["session"] = {"require": False}
    table = Table(home=defdir, cfg=cfg, store=store.FileStore(home), clock=lambda: 0.0)
    return table, defdir, home


def join(table, seats=(("p1", "human"), ("p2", "human"))):
    for seat, kind in seats:
        table.join(seat=seat, name=seat.upper(), kind=kind)


# ── A. 碎片：drop_policy ＋ 欠帳 ────────────────────────────────────────────────
def case_drop_policy():
    print("== A. 碎片按欠帳出土 ＋ drop_policy")
    clues = {
        "claims": [{"id": "c_hot", "text": "熱線"}, {"id": "c_cold", "text": "冷線"}],
        "fragments": [
            {"id": "f_hot1", "pool": "tower", "supports": ["c_hot"], "weight": 1, "text": "熱 1"},
            {"id": "f_hot2", "pool": "tower", "supports": ["c_hot"], "weight": 2, "text": "熱 2"},
            {"id": "f_cold1", "pool": "tower", "supports": ["c_cold"], "weight": 3, "text": "冷 1"},
            {"id": "f_cold2", "pool": "tower", "supports": ["c_cold"], "weight": 1, "text": "冷 2"},
        ],
        "drop_policy": {"on_pray_to": "anyone_but_requester", "prefer_unheld": True,
                        "count_per_pray": 2,
                        "note": "測試：一次兩枚、不給提出者、先補欠帳"},
    }
    table, _d, _h = build(clues=clues, turn={"actions_per_turn": 2, "says_per_turn": 2})
    join(table, (("p1", "human"), ("p2", "human"), ("p3", "agent")))
    ensure_turn(table)

    # 先手投一枚「熱線」出去 ⇒ 熱線出土 1、冷線 0 ⇒ 下一次要優先補冷線
    table.grant_fragment(fid="f_hot1", to="p2", reason="seed")
    got = table.grant_pray_drop("p1")
    check("A1 count_per_pray=2 ⇒ 一次祈願投兩枚", len(got) == 2, f"{[g['fid'] for g in got]}")
    check("A2 on_pray_to=anyone_but_requester ⇒ 都不落在提出者手上",
          all(g["to"] != "p1" for g in got), f"{[(g['fid'], g['to']) for g in got]}")
    first = got[0]["fid"] if got else ""
    check("A3 欠帳優先 ⇒ 第一枚補「冷線」（熱線已出土 1 枚）",
          first in ("f_cold1", "f_cold2"), first)
    holders = [g["to"] for g in got]
    check("A4 prefer_unheld ⇒ 第一枚給「手上還沒有碎片」的人（p3）",
          holders[0] == "p3", f"{holders}")


# ── B. 移動不佔行動格 ────────────────────────────────────────────────────────
def case_free_move():
    print("== B. 移動不佔行動格（turn.free_actions）")
    table, _d, _h = build(turn={"actions_per_turn": 1, "free_actions": ["move"]})
    join(table)
    ensure_turn(table)
    out = table.act(seat="p1", action="move", target="w_teckan")
    counters = table.state["semi"]["counters"].get("p1", {})
    check("B1 移動成功", bool(out), str(out)[:60])
    check("B2 移動不計入本輪行動額度", int(counters.get("act", 0)) == 0, str(counters))
    out2 = table.act(seat="p1", action="search")
    check("B3 走完還能做一個真正的行動（額度沒被吃掉）", bool(out2), str(out2)[:60])
    check("B4 那個行動才算 1 格", int(table.state["semi"]["counters"]["p1"]["act"]) == 1,
          str(table.state["semi"]["counters"]["p1"]))


# ── C. 輪界（原本的「收景」） ─────────────────────────────────────────────────
def case_round_boundary():
    print("== C. 輪界：末席結束回合才收輪，收完自動開下一輪")
    table, _d, _h = build(turn={"actions_per_turn": 1, "seconds": 300})
    join(table)
    ensure_turn(table)
    check("C1 人齊 ⇒ 自動開第一輪（輪號還沒結算）", int(table.state.get("round_no") or 0) == 0,
          f"round_no={table.state.get('round_no')}")
    table.act(seat="p1", action="search")
    types = [e["type"] for e in table.events]
    check("C2 非末席結束回合 ⇒ 還不到輪末（沒有 round_ended）", "round_ended" not in types,
          str([t for t in types if t.startswith("round")]))
    check("C2b 照舊交棒給下一席", (table.state.get("turn") or {}).get("seat") == "p2",
          str(table.state.get("turn")))
    table.act(seat="p2", action="search")
    types = [e["type"] for e in table.events]
    check("C3 末席結束 ⇒ 發 round_ended（輪號 +1）",
          "round_ended" in types and int(table.state.get("round_no") or 0) == 1,
          f"round_no={table.state.get('round_no')}")
    check("C4 接著發 round_started 並**自動**開下一輪（不用 GM 再開）",
          "round_started" in types[types.index("round_ended"):]
          and (table.state.get("turn") or {}).get("seat") == "p1",
          str(table.state.get("turn")))
    view = table.adapter_view()
    check("C5 投影裡沒有任何 scene 欄位（景這層真的沒了）",
          not any(k.startswith("scene") for k in view), str(sorted(view))[:120])


def case_gm_cannot_close_scene():
    print("== C6. GM 的開景／收景 op 已經不存在（拆景後只剩「每輪說一句」）")
    table, _d, _h = build(turn={"seconds": 300})
    join(table)
    ensure_turn(table)
    for op in ("scene_open", "scene_close", "scene"):
        try:
            table.gm_op(op=op, text="測試")
            check(f"C6 GM op={op} 要當場拒（未知操作）", False, "沒被拒")
        except Exception as exc:                                               # noqa: BLE001
            check(f"C6 GM op={op} 要當場拒（未知操作）", "未知" in str(exc), str(exc)[:50])


# ── C10～C12：回合歸屬與順序（原樣保留） ─────────────────────────────────────
def case_turn_ownership():
    print("== C10. 非自己 phase 不能 act（2026-10-20 設計者定案）")
    t10, _d10, _h10 = build(turn={"seconds": 300})
    join(t10)
    ensure_turn(t10)
    try:
        t10.act(seat="p2", action="search")
        check("C10 非當前回合的 act 被拒", False, "沒被拒")
    except Exception as exc:                                                   # noqa: BLE001
        check("C10 非當前回合的 act 被拒", "回合" in str(exc), str(exc)[:60])
    check("C11 當前回合的 act 照常可以",
          isinstance(t10.act(seat="p1", action="search"), dict), "ok")

    print("== C12. 行動順序＝劇本席位順序 p1→p4（不是入座先後）")
    t12, _d12, _h12 = build(turn={})
    join(t12, (("p2", "human"), ("p1", "human")))     # 故意先 p2 再 p1
    check("C12 入座序 p2→p1 ⇒ 行動序仍是 p1→p2", t12.state["order"] == ["p1", "p2"],
          str(t12.state["order"]))


# ── D. 逐席節拍 ──────────────────────────────────────────────────────────────
def case_per_seat_pacing():
    print("== D. 逐席節拍（turn.seconds_by_kind）")
    table, _d, _h = build(turn={"seconds": 300, "max_waits": 2,
                                "seconds_by_kind": {"agent": 60},
                                "max_waits_by_kind": {"agent": 0},
                                "auto_start_first_turn": False})
    join(table, (("p1", "human"), ("p3", "agent")))
    ensure_turn(table)
    check("D1 human 的秒數＝300（沒有 by_kind 覆寫）",
          int(table.turn_cfg_for("p1").get("seconds")) == 300, str(table.turn_cfg_for("p1").get("seconds")))
    check("D2 agent 的秒數＝60（by_kind 覆寫）",
          int(table.turn_cfg_for("p3").get("seconds")) == 60, str(table.turn_cfg_for("p3").get("seconds")))
    check("D3 agent 的 max_waits＝0", int(table.turn_cfg_for("p3").get("max_waits")) == 0,
          str(table.turn_cfg_for("p3").get("max_waits")))
    # 拆景後「人齊就開輪」是自動的 ⇒ 交棒給 agent 那一席（引擎自己開），再看它的死線
    table.end_turn(reason="test")
    ev = [e for e in table.events if e["type"] == "turn_started"][-1]
    check("D4 輪到 agent 那一手用 60 秒算死線",
          ev.get("seat") == "p3" and float(ev["deadline_at"]) <= 60.0, str(ev.get("deadline_at")))


def main() -> int:
    case_drop_policy()
    case_free_move()
    case_round_boundary()
    case_gm_cannot_close_scene()
    case_turn_ownership()
    case_per_seat_pacing()
    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
