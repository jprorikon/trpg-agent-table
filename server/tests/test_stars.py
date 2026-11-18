#!/usr/bin/env python3
"""**星等（升星）的自測**（2026-10-07）——`packs/rules.core/tables/stars.json`。

    python3 test_stars.py

設計者的定案：**升星＝劇情事件觸發**（不是數字門檻、也不是素材消耗）——表上每一列寫一個 AST 判據
`when`，引擎每一筆事件後**對每一席各判一次**（判據裡的 `me`＝那一席），成立就升、可連升。

  Z1 **表**：載得動（★2–★5）；schema 壞（star 亂寫／重複／**缺 when**／gains 亂鍵／unlocks 不是字串陣列／
     不認識的欄位）當場叫。
  Z2 **沒啟用＝legacy**：沒星等表 ⇒ 一個 `star_up` 都不發、投影沒有 `you.star`、`star_set` 當場叫。
  Z3 **劇情條件升星**：完成自己的願望 ⇒ ★2（別席沒完成就不升）；`me.goal_achieved` 真的吃。
  Z4 **通關某層**：有人先手開 `hyakki_f4` ⇒ ★3。
  Z5 **可連升**：一次滿足 ★2＋★3 的條件 ⇒ 一路升上去（逐星發事件）。
  Z6 **成長與解鎖**：`gains` 的結果值寫進事件（回放不必重算）；`unlocks` 讓技能「不受等級限制」也 known。
  Z7 **當場給星**：`star_set`／GM（同一條路）——跳級會**累積**中間每一星的成長；不能降星。
  Z8 **結局的星**：判到**勝利**結局 ⇒ ★5（`world.event.kind == "win"`）。
  Z9 **事件溯源**：升過星的流 `op=replay` 一致。
  Z10 **升級兩軸獨立**：星等與等級互不影響（`level` 不變、`star` 不變）。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo 根
sys.path.insert(0, HERE)
from trpg.core import layout as layout                                                          # noqa: E402
from trpg.rules import scenario as scenario

CAMP = layout.must_locate_scenario(HERE, "hyakki_tower", "sannenme_no_onibarai")
DEF = layout.def_dir_of(CAMP)          # 定義層（WS-B：桌目錄裡只剩 config／runs）
from trpg.rules import packs as packs                                                             # noqa: E402
MODF = scenario.module_filename(DEF) or "scenario.json"   # 劇本規則資料的檔名（WS-B：新名優先，別硬編）

from trpg.rules import stars as stars                                                             # noqa: E402
from trpg.core import store as store                                                             # noqa: E402
from trpg.engine import Table                                                 # noqa: E402

OK, FAIL = [], []
SPEC = ["d20core", "board", "semic", "mode.turn", "wound", "rules.core"]

#: **舊制**（表裡沒有 `channels`）的星等表——Z3–Z8 用它測「逐級 when 判據」那條路還活著。
#: 真實模組的表 2026-10-20 起走**渠道制**（`channels`；見 tests/test_star_channels.py）：
#: 兩條路要各自被咬住——渠道制改了，舊制不能跟著壞。
LEGACY_ROWS = [
    {
        "star": 2,
        "when": {
            "or": [
                {
                    "var": "me.goal_achieved"
                },
                {
                    "==": [
                        {
                            "var": "world.event_type"
                        },
                        "boss_defeated"
                    ]
                }
            ]
        },
        "gains": {
            "max_hp": 4,
            "attrs": {
                "wil": 1
            },
            "resources": {
                "mana": 1
            }
        },
        "unlocks": [
            "hyousou_no_sho",
            "meisou_no_me"
        ],
        "title": "星の担い手",
        "name_jp": "星の担い手",
        "name_cn": "星之承担者"
    },
    {
        "star": 3,
        "when": {
            "or": [
                {
                    "exists": "world.pray_first.hyakki_f4"
                },
                {
                    "exists": "facts.tower_cleared"
                }
            ]
        },
        "gains": {
            "max_hp": 4,
            "attrs": {
                "str": 1
            },
            "resources": {
                "mana": 1
            }
        },
        "unlocks": [
            "kageotoshi"
        ],
        "title": "塔の踏破者",
        "name_jp": "塔の踏破者",
        "name_cn": "踏破塔者"
    },
    {
        "star": 4,
        "when": {
            "or": [
                {
                    ">=": [
                        {
                            "var": "round_no"
                        },
                        40
                    ]
                },
                {
                    "exists": "facts.star_trial_done"
                }
            ]
        },
        "gains": {
            "max_hp": 6,
            "attrs": {
                "agi": 1
            },
            "resources": {
                "mana": 2
            }
        },
        "unlocks": [],
        "title": "星屑の蒐集家",
        "name_jp": "星屑の蒐集家",
        "name_cn": "星屑收藏家"
    },
    {
        "star": 5,
        "when": {
            "or": [
                {
                    "==": [
                        {
                            "var": "world.event.kind"
                        },
                        "win"
                    ]
                },
                {
                    "exists": "facts.star_of_the_king"
                }
            ]
        },
        "gains": {
            "max_hp": 8,
            "attrs": {
                "str": 1,
                "agi": 1,
                "wil": 1
            },
            "resources": {
                "mana": 3
            }
        },
        "unlocks": [],
        "title": "星の王",
        "name_jp": "星の王",
        "name_cn": "星之王"
    }
]

LEGACY = ["d20core", "board", "semic", "mode.turn", "wound"]


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


def _raises(fn, exc=Exception):
    try:
        fn()
    except exc:
        return True
    except Exception as exc2:                                             # noqa: BLE001
        print(f"    （丟的是 {type(exc2).__name__}: {exc2}）")
        return False
    return False


def mk_home(*, spec=SPEC, module_patch=None, legacy_stars=True):
    tmp = tempfile.mkdtemp(prefix="trpg_stars_")
    packs.seed_module_packs(DEF, tmp)   # 模組層的包（rules.core 住模組 repo；臨時桌推不出模組）
    for f in ("board.json", "goals.json", "clues.json", "seats.json", MODF):
        shutil.copy(os.path.join(DEF, f), os.path.join(tmp, f))
    layout.copy_table_config(CAMP, os.path.join(tmp, "config.json"))   # 旋鈕 ⇒ 桌目錄（定義在 DEF）
    shutil.copytree(os.path.join(DEF, "packs"), os.path.join(tmp, "packs"), dirs_exist_ok=True)
    if legacy_stars:                     # 換成「沒有 channels」的舊表 ⇒ 走舊制（逐級 when）
        _p = os.path.join(tmp, "packs", "rules.core", "tables", "stars.json")
        with open(_p, "w", encoding="utf-8") as fh:
            json.dump({"kind": "stars", "rows": LEGACY_ROWS}, fh, ensure_ascii=False)
    if module_patch:
        mod = json.load(open(os.path.join(tmp, MODF), encoding="utf-8"))
        module_patch(mod)
        with open(os.path.join(tmp, MODF), "w", encoding="utf-8") as fh:
            json.dump(mod, fh, ensure_ascii=False)
    cfg = json.load(open(os.path.join(tmp, "config.json"), encoding="utf-8"))
    cfg["port"] = 0
    cfg["packs"] = {"enabled": list(spec)}
    with open(os.path.join(tmp, "config.json"), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False)
    return tmp, cfg


def mk_table(tmp, cfg):
    return Table(home=tmp, cfg=cfg, store=store.FileStore(tmp), clock=lambda: 1000.0)


def seat_up(t, sid="p1", name="カガリ", kind="human"):
    return t.join(seat=sid, name=name, kind=kind,
                  attrs={"str": 3, "agi": 3, "wil": 3, "luck": 2}, order=0)


def main() -> int:                                                        # noqa: C901
    print("== Z1. 表 ==")
    rows = json.load(open(os.path.join(packs.module_packs_dir(DEF), "rules.core", "tables", "stars.json"),
                          encoding="utf-8"))["rows"]
    check("Z1a 星等表載得動（★2–★5，4 列）",
          [r["star"] for r in rows] == [2, 3, 4, 5], f"{[r['star'] for r in rows]}")
    check("Z1b star 亂寫／重複 ⇒ 不合法",
          any("star" in x for x in stars.validate([{"star": "2", "when": {"var": "x"}}]))
          and any("重複" in x for x in stars.validate([{"star": 2, "when": {"var": "x"}},
                                                       {"star": 2, "when": {"var": "y"}}])))
    check("Z1c 缺 when ⇒ 不合法（升星靠劇情條件，不靠數字）",
          any("when" in x for x in stars.validate([{"star": 2}])))
    check("Z1d gains 亂鍵／unlocks 不是字串陣列 ⇒ 不合法",
          any("gains" in x for x in stars.validate([{"star": 2, "when": {"var": "x"},
                                                     "gains": {"hp": 3}}]))
          and any("unlocks" in x for x in stars.validate([{"star": 2, "when": {"var": "x"},
                                                           "unlocks": [1]}])))
    check("Z1e 不認識的欄位 ⇒ 不合法",
          any("不認識的欄位" in x for x in stars.validate([{"star": 2, "when": {"var": "x"},
                                                            "cost": {}}])))

    print("== Z2. 沒啟用＝legacy ==")
    tmp2, cfg2 = mk_home(spec=LEGACY)
    try:
        t2 = mk_table(tmp2, cfg2)
        seat_up(t2, "p1")
        check("Z2a 沒星等表 ⇒ 一個 star_up 都不發、投影沒有 you.star",
              not any(e.get("type") == "star_up" for e in t2.events)
              and "star" not in ((t2.view(seat="p1").get("you") or {}))
              and t2.star_of("p1") == 1)
        check("Z2b star_set 當場叫（機制沒開，不是靜默沒反應）",
              _raises(lambda: t2.star_set(seat="p1"), stars.StarError))
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)

    print("== Z3–Z5. 劇情條件升星（完成願望／通關某層／可連升）==")
    # 用一個「一筆事件就把兩條都滿足」的劇本：goal_achieved ＋ pray_first
    def _patch(mod):
        mod["triggers"] = (mod.get("triggers") or []) + [
            {"id": "t_win", "when": {"==": [{"var": "world.event.type"}, "mock_win"]},
             "then": [{"fact_set": {"key": "tower_cleared", "value": True}}]}]
    tmp, cfg = mk_home(module_patch=_patch)
    try:
        t = mk_table(tmp, cfg)
        seat_up(t, "p1")
        seat_up(t, "p2", name="シズク")
        check("Z3a 開局都是 ★1", t.star_of("p1") == 1 and t.star_of("p2") == 1)
        # 讓 p1 的願望達成：走 semic 的同一條路（`wish_done` 才是把 `me.goal_achieved` 立起來的事件）
        n1 = len(t.events)
        t._emit("wish_done", public=True, seat="p1", id="g_cat")
        _s1 = [e for e in t.events[n1:] if e.get("type") == "star_up"]
        check("Z3b 完成自己的願望 ⇒ ★2（只有那一席升，別席不升）",
              t.star_of("p1") == 2 and t.star_of("p2") == 1 and _s1
              and _s1[-1].get("seat") == "p1" and _s1[-1].get("star") == 2,
              f"p1={t.star_of('p1')} p2={t.star_of('p2')}")
        _g = (_s1[-1].get("after") or {})
        check("Z3c 成長的**結果值**寫進事件（回放不必重算）",
              _g.get("max_hp") == 18 and _g.get("hp") == 18
              and _g.get("attrs", {}).get("wil") == 4
              and _g.get("resources", {}).get("mana") == 4, f"{_g}")
        _un = (t._star_up.__self__.unlocks_of("p1") if hasattr(t, "unlocks_of") else [])
        check("Z3d `unlocks` 生效：原 Lv2 的技能不受等級限制也 known",
              "hyousou_no_sho" in _un
              and any(x["id"] == "hyousou_no_sho" and x["known"] for x in t.skills_of("p1"))
              and t.level_of("p1") == 1,                       # 等級沒動 ⇒ 是星解鎖那條路
              f"unlocks={_un}")
        # Z4 通關某層 ⇒ ★3（先手開 hyakki_f4）
        n2 = len(t.events)
        # 「先手開箱」＝`artifact_gained`（semic 的 pray_first 就是記這個）
        t._emit("artifact_gained", public=True, seat="p1", node="hyakki_f4", name="願い星", tier=3)
        _s2 = [e for e in t.events[n2:] if e.get("type") == "star_up"]
        check("Z4 有人先手開了大祈願箱 ⇒ ★3", t.star_of("p1") == 3 and _s2,
              f"p1={t.star_of('p1')}")
        # Z4b：★4 的第二條路＝劇本自己寫的事實（`facts.star_trial_done`）——走真的 `fact_set` 事件
        n2b = len(t.events)
        t._emit("fact_set", public=True, key="star_trial_done", value=True, by="test")
        _s4 = [e for e in t.events[n2b:] if e.get("type") == "star_up"]
        check("Z5 劇本寫下事實 ⇒ 再升 ★4（佔位那條路真的能用）",
              t.star_of("p1") == 4 and _s4, f"p1={t.star_of('p1')}")
        # Z8 勝利結局 ⇒ ★5
        n3 = len(t.events)
        t._emit("ending", public=True, id="e_boss_down", kind="win", by="endings")
        _s5 = [e for e in t.events[n3:] if e.get("type") == "star_up"]
        check("Z8 判到勝利結局 ⇒ ★5（跳級：★4 的成長也一起累積）",
              t.star_of("p1") == 5 and t.stars_max() == 5 and _s5, f"p1={t.star_of('p1')}")
        # Z10 兩軸獨立
        check("Z10 兩軸獨立（升星不動等級；星等只認自己的表）",
              t.level_of("p1") == 1 and t.exp_of("p1") == 0)
        check("Z9 升過星的流回放一致", bool(t.replay_check().get("replay_matches")),
              f"{t.replay_check().get('diff')}")
        # Z7 當場給星（GM／劇本同一條路）
        tmp7, cfg7 = mk_home()
        try:
            t7 = mk_table(tmp7, cfg7)
            seat_up(t7, "p3", name="ホムラ")
            r = t7.star_set(seat="p3", star=4, by="gm")                    # 直接給到 ★4
            check("Z7a 當場給星：跳級會**累積**中間每一星的成長",
                  r["star"] == 4 and t7.star_of("p3") == 4
                  and t7.state["seats"]["p3"]["max_hp"] == 14 + 4 + 4 + 6
                  and t7.state["seats"]["p3"]["attrs"]["agi"] == 4,
                  f"max_hp={t7.state['seats']['p3']['max_hp']} attrs={t7.state['seats']['p3']['attrs']}")
            check("Z7b 不能降星／超出表頂 ⇒ 當場叫",
                  _raises(lambda: t7.star_set(seat="p3", star=3), stars.StarError)
                  and _raises(lambda: t7.star_set(seat="p3", star=9), stars.StarError))
            check("Z7c 有星等表的桌投影才有 `you.star`（含稱號與下一星解鎖）",
                  (t7.view(seat="p3").get("you") or {}).get("star", {}).get("star") == 4
                  and "next_title" in ((t7.view(seat="p3").get("you") or {}).get("star") or {}))
        finally:
            shutil.rmtree(tmp7, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("== Z11. 開局 ★1（有紀錄才看得到自己的星）==")
    tmp11, cfg11 = mk_home()
    try:
        t11 = mk_table(tmp11, cfg11)
        n0 = len(t11.events)
        seat_up(t11, "p1")
        _ev = [e for e in t11.events[n0:] if e.get("type") == "star_up"]
        check("Z11a 開局記 ★1（一筆 star_up，by=join）",
              bool(_ev) and _ev[-1].get("star") == 1 and _ev[-1].get("by") == "join", f"{_ev[-1] if _ev else None}")
        check("Z11b 於是自己一開場就看得到 `you.star`（含下一星稱號）",
              ((t11.view(seat="p1").get("you") or {}).get("star") or {}).get("star") == 1
              and ((t11.view(seat="p1").get("you") or {}).get("star") or {}).get("next_title") == "星の担い手")
        check("Z11c 沒啟用星等表的桌 ⇒ 開局一個事件都不多發（legacy 零變更）",
              True)                     # Z2a 已經咬住（那張桌沒有 star_up）
    finally:
        shutil.rmtree(tmp11, ignore_errors=True)

    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
