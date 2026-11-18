#!/usr/bin/env python3
"""**升級所需經驗跟著等級走**的自測（設計者 2026-10-07）。

    python3 test_levels_curve.py

  D1 有旋鈕（`rules.levels.exp_base/exp_step`）⇒ Δ(L)＝base＋step×(L−1)，累計寫回 `need`。
  D2 **沒旋鈕 ⇒ 用表裡原本的 `need`**（legacy 一字不變）。
  D3 config 覆蓋包的預設（config 是旋鈕的權威）。
  D4 升級真的按新門檻發生（exp 剛好到的時候，多 1 點才升）。
  D5 壞旋鈕（負數／非數字）⇒ 載入當場叫（不靜默算歪）。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo 根
sys.path.insert(0, HERE)
from trpg.core import layout as layout

from trpg.rules import scenario as scenario                                                          # noqa: E402
CAMP = layout.must_locate_scenario(HERE, "hyakki_tower", "sannenme_no_onibarai")
DEF = layout.def_dir_of(CAMP)          # 定義層（WS-B：桌目錄裡只剩 config／runs）
from trpg.rules import packs as packs                                                             # noqa: E402
MODF = scenario.module_filename(DEF) or "scenario.json"   # 劇本規則資料的檔名（WS-B：新名優先，別硬編）

from trpg.core import store as store                                                             # noqa: E402
from trpg.engine import Table                                                 # noqa: E402

OK, FAIL = [], []
SPEC = ["d20core", "board", "semic", "mode.turn", "wound", "rules.core", "hyakki_world"]
NO_CURVE = {"resources": {"memory": {"base": 60, "per_wil": 10}, "mana": {"base": 2}}}


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


def mk(rule_override=None, drop_curve=False):
    tmp = tempfile.mkdtemp(prefix="trpg_lv_")
    packs.seed_module_packs(DEF, tmp)   # 模組層的包（rules.core 住模組 repo；臨時桌推不出模組）
    for f in ("board.json", "goals.json", "clues.json", "seats.json", MODF):
        shutil.copy(os.path.join(DEF, f), os.path.join(tmp, f))
    layout.copy_table_config(CAMP, os.path.join(tmp, "config.json"))   # 旋鈕 ⇒ 桌目錄（定義在 DEF）
    shutil.copytree(os.path.join(DEF, "packs"), os.path.join(tmp, "packs"), dirs_exist_ok=True)
    cfg = json.load(open(os.path.join(tmp, "config.json"), encoding="utf-8"))
    cfg["port"] = 0
    cfg["packs"] = {"enabled": list(SPEC)}
    if rule_override is not None:
        cfg.setdefault("rules", {}).update(rule_override)
    with open(os.path.join(tmp, "config.json"), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False)
    return tmp, cfg


def mk_t(tmp, cfg):
    return Table(home=tmp, cfg=cfg, store=store.FileStore(tmp), clock=lambda: 1000.0)


def needs(t):
    return [int(r["need"]) for r in sorted(t.levels_table, key=lambda r: int(r["level"]))]


def main() -> int:
    print("== D1 有旋鈕：Δ(L)＝base＋step×(L−1) ==")
    tmp, cfg = mk(rule_override={"levels": {"exp_base": 30, "exp_step": 20}})
    t = mk_t(tmp, cfg)
    check("need ＝ 0/30/80/150/240", needs(t) == [0, 30, 80, 150, 240], str(needs(t)))

    print("== D2 沒旋鈕 ⇒ 用表裡的 need ==")
    tmp2, cfg2 = mk(rule_override={**NO_CURVE, "levels": None})
    t2 = mk_t(tmp2, cfg2)
    check("照表：0/30/80/150/250", needs(t2) == [0, 30, 80, 150, 250], str(needs(t2)))

    print("== D3 config 覆蓋 ==")
    tmp3, cfg3 = mk(rule_override={"levels": {"exp_base": 100, "exp_step": 100}})
    t3 = mk_t(tmp3, cfg3)
    check("base100 step100 ⇒ 0/100/300/600/1000", needs(t3) == [0, 100, 300, 600, 1000], str(needs(t3)))

    print("== D4 升級照新門檻發生 ==")
    tmp4, cfg4 = mk(rule_override={"levels": {"exp_base": 30, "exp_step": 20}})
    t4 = mk_t(tmp4, cfg4)
    t4.join(seat="p1", name="カガリ", kind="human", attrs={"str": 1, "agi": 5, "wil": 3, "luck": 2})
    t4.exp_gain(seat="p1", amount=29, reason="test", by="test")
    lv29 = t4.level_of("p1")
    t4.exp_gain(seat="p1", amount=2, reason="test", by="test")
    lv31 = t4.level_of("p1")
    check("29 點還沒升（Lv1）", lv29 == 1, f"Lv{lv29}")
    check("31 點 ⇒ Lv2（門檻 30）", lv31 == 2, f"Lv{lv31}")

    print("== D5 壞旋鈕當場叫 ==")
    try:
        tmp5, cfg5 = mk(rule_override={"levels": {"exp_base": -1}})
        mk_t(tmp5, cfg5)
        check("負數要被擋", False, "竟然載得動")
    except Exception as e:                                               # noqa: BLE001
        check("負數要被擋", "exp_base" in str(e), str(e)[:60])

    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    for f in FAIL:
        print("  FAILED: " + f)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
