#!/usr/bin/env python3
"""**資源上限的自測**（記憶／魔力）——設計者 2026-10-07：「魔力和記憶你需要設計好上限；魔力上限可以跟著等級或者星級走」。

    python3 test_resources.py

  R1 有旋鈕的桌：`you.max_memory` ＝ `base + per_wil×意志 ＋ per_level×(等級−1)`；
     `you.max_mana` ＝ `base + per_level×(等級−1) ＋ per_star×星等`。
  R2 **沒啟用帶旋鈕的包 ⇒ 不露**（legacy 投影一字不變）。
  R3 記憶上限**不低於當前值**（保護舊資料：公式值 < 當前值時取當前值）。
  R4 等級／星等一動，魔力上限跟著動（per_level／per_star 都真的生效）。
  R5 config 覆蓋包的預設（**config 是旋鈕的權威**）。
  R6 上限只在**自己的** `you` 裡（`seats[]` 公開名單不加欄位）。
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
from trpg.rules import packs as _packs                     # noqa: E402  （`mk(packs=…)` 的參數名會遮住 `packs`）
MODF = scenario.module_filename(DEF) or "scenario.json"   # 劇本規則資料的檔名（WS-B：新名優先，別硬編）

from trpg.core import store as store                                                             # noqa: E402
from trpg.engine import Table                                                 # noqa: E402

OK, FAIL = [], []
SPEC = ["d20core", "board", "semic", "mode.turn", "wound", "rules.core", "hyakki_world"]
LEGACY = ["d20core", "board", "semic", "mode.turn"]


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


def mk(packs=SPEC, rule_override=None):
    tmp = tempfile.mkdtemp(prefix="trpg_cap_")
    _packs.seed_module_packs(DEF, tmp)   # 模組層的包（rules.core 住模組 repo；臨時桌推不出模組）
    for f in ("board.json", "goals.json", "clues.json", "seats.json", MODF):
        shutil.copy(os.path.join(DEF, f), os.path.join(tmp, f))
    layout.copy_table_config(CAMP, os.path.join(tmp, "config.json"))   # 旋鈕 ⇒ 桌目錄（定義在 DEF）
    shutil.copytree(os.path.join(DEF, "packs"), os.path.join(tmp, "packs"), dirs_exist_ok=True)
    cfg = json.load(open(os.path.join(tmp, "config.json"), encoding="utf-8"))
    cfg["port"] = 0
    cfg["packs"] = {"enabled": list(packs)}
    if rule_override is not None:
        cfg.setdefault("rules", {})["resources"] = rule_override
    with open(os.path.join(tmp, "config.json"), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False)
    t = Table(home=tmp, cfg=cfg, store=store.FileStore(tmp), clock=lambda: 1000.0)
    return tmp, t


def you(t, seat="p1", name="カガリ"):
    if seat not in t.state["seats"]:
        t.join(seat=seat, name=name, kind="human", attrs={"str": 1, "agi": 5, "wil": 3, "luck": 2})
    return t.view(seat=seat)["you"]


def main() -> int:
    print("== R1 有旋鈕：兩條上限算得出來 ==")
    tmp, t = mk()
    y = you(t)
    wil = int((y["attrs"] or {}).get("wil") or 0)
    lv = int(y.get("level") or 1)
    exp_lv0 = t.rules["resources"]["memory"]["base"] + t.rules["resources"]["memory"]["per_wil"] * wil
    check("max_memory 露出來", "max_memory" in y, str(y.get("max_memory")))
    check("max_memory ＝ base＋per_wil×意志（Lv1）", y.get("max_memory") == max(exp_lv0, int(y.get("memory") or 0)),
          f"{y.get('max_memory')} vs {exp_lv0}")
    check("max_mana 露出來", "max_mana" in y, str(y.get("max_mana")))
    # ⚠ 入座時星等表會發 `stars_baseline` ⇒ ★1（不是 0）——期望值一律**從 state 讀**，別寫死
    _st = int((t.state.get("star") or {}).get("p1") or 0)
    _mn = t.rules["resources"]["mana"]
    _exp_mn = int(_mn.get("base") or 0) + int(_mn.get("per_level") or 0) * (lv - 1) + int(_mn.get("per_star") or 0) * _st
    check("max_mana ＝ base＋per_level×(等級−1)＋per_star×星等", y.get("max_mana") == _exp_mn,
          f"{y.get('max_mana')} vs {_exp_mn}（★{_st}）")

    print("== R2 legacy：沒旋鈕 ⇒ 不露 ==")
    tmp2, t2 = mk(packs=LEGACY)
    y2 = you(t2)
    check("legacy 沒有 max_memory", "max_memory" not in y2)
    check("legacy 沒有 max_mana", "max_mana" not in y2)

    print("== R3 記憶上限不低於當前值 ==")
    tmp3, t3 = mk(rule_override={"memory": {"base": 5, "per_wil": 0, "per_level": 0},
                                "mana": {"base": 0, "per_level": 0, "per_star": 0}})
    y3 = you(t3)
    cur = int(y3.get("memory") or 0)
    check("公式值 < 當前值 ⇒ 取當前值", y3.get("max_memory") == max(5, cur), f"{y3.get('max_memory')} cur={cur}")

    print("== R4 等級／星等會動到魔力上限 ==")
    tmp4, t4 = mk(rule_override={"mana": {"base": 2, "per_level": 1, "per_star": 2}})
    y4 = you(t4)
    base4 = y4.get("max_mana")
    t4.state.setdefault("level", {})["p1"] = 3          # 直接擺狀態（只驗投影算式，不走事件）
    t4.state.setdefault("star", {})["p1"] = 2           # `state["star"][seat]` 是數字
    y4b = t4.view(seat="p1")["you"]
    _mn4 = t4.rules["resources"]["mana"]
    _exp4 = int(_mn4.get("base") or 0) + int(_mn4.get("per_level") or 0) * 2 + int(_mn4.get("per_star") or 0) * 2
    check("Lv3★2 ⇒ base＋1×2(等級)＋2×2(星等)", y4b.get("max_mana") == _exp4, f"{base4} → {y4b.get('max_mana')} vs {_exp4}")

    print("== R5 config 贏過包的預設 ==")
    tmp5, t5 = mk(rule_override={"memory": {"base": 999, "per_wil": 0, "per_level": 0}, "mana": {"base": 7}})
    y5 = you(t5)
    check("config 的 base=999 生效", y5.get("max_memory") == max(999, int(y5.get("memory") or 0)), str(y5.get("max_memory")))
    _st5 = int((t5.state.get("star") or {}).get("p1") or 0)
    _st5 = int((t5.state.get("star") or {}).get("p1") or 0)
    check("config 的 mana base=7 生效（★1 ⇒ +2）", y5.get("max_mana") == 7 + 2 * _st5, str(y5.get("max_mana")))

    print("== R6 只在自己的 you 裡 ==")
    tmp6, t6 = mk()
    you(t6)
    v6 = t6.view(seat="p1")
    check("seats[] 不加 max_memory", all("max_memory" not in s for s in v6["seats"]))
    check("seats[] 不加 max_mana", all("max_mana" not in s for s in v6["seats"]))

    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    for f in FAIL:
        print("  FAILED: " + f)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
