#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**開局難度**自測（`starting_difficulty`）：合併、優先序、倍率、退檔。

這是 2026-10-20 設計者那條「A1 你按難度旋鈕定」＋「建房間時可以指定開局難度」的閘。
**與 `difficulty_presets` 無關**（那是 L3 導演的運行時糾偏，只動 DC／行動格）。

跑法：`python3 tests/test_difficulty.py`（或 `bash tools/run_tests.sh test_difficulty`）
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from typing import Any

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from trpg.service import table_factory                                    # noqa: E402

DEF = os.path.join(HERE, "modules", "hyakki_tower", "scenarios", "sannenme_no_onibarai")
OK, FAIL = [], []


def check(name, cond, detail: Any = ""):
    (OK if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


def apply(cfg, def_dir=DEF):
    return table_factory.apply_difficulty(dict(cfg), def_dir, log=lambda *_a: None)


def main() -> int:
    print("== D1 不指定難度 ⇒ 用劇本 default（normal），且與 2026-10-20 前的現值一致 ==")
    c = apply({})
    check("D1a 難度＝normal", c.get("difficulty") == "normal", c.get("difficulty"))
    check("D1b rules.death 由難度檔補進來（config 原本沒寫）",
          (c.get("rules") or {}).get("death", {}).get("skip_turns") == 2,
          (c.get("rules") or {}).get("death"))
    check("D1c rules.levels 由難度檔補進來（40/30＝現值）",
          (c.get("rules") or {}).get("levels") == {"exp_base": 40, "exp_step": 30},
          (c.get("rules") or {}).get("levels"))
    check("D1d normal 的五顆倍率都是中性",
          c.get("difficulty_mods") == {"monster_dc": 0, "exp_mult": 1.0,
                                       "coins_mult": 1.0, "price_mult": 1.0,
                                       "monster_hp_mult": 1.0},
          c.get("difficulty_mods"))

    print("== D2 easy／hard 各自的效果 ==")
    e = apply({"difficulty": "easy"})
    de, le, me = (e["rules"]["death"], e["rules"]["levels"], e["difficulty_mods"])
    check("D2a easy：缺席 1 回合、復活 2 HP、傷全清",
          de["skip_turns"] == 1 and de["hp_restore"] == 2 and de["clear_conditions"] is True, de)
    check("D2b easy：不掉貨幣／不掉記憶", de["drop_coins"] == 0 and de["lost_memory"] == 0)
    check("D2c easy：升級曲線 30/25", le == {"exp_base": 30, "exp_step": 25}, le)
    check("D2d easy：檢定 −1、經驗 ×1.2、貨幣 ×1.2、物價 ×0.8",
          me == {"monster_dc": -1, "exp_mult": 1.2, "coins_mult": 1.2, "price_mult": 0.8, "monster_hp_mult": 0.7}, me)

    h = apply({"difficulty": "hard"})
    dh, lh, mh = (h["rules"]["death"], h["rules"]["levels"], h["difficulty_mods"])
    check("D2e hard：缺席 3 回合、掉一半貨幣、掉 8 記憶",
          dh["skip_turns"] == 3 and dh["drop_coins"] == 0.5 and dh["lost_memory"] == 8, dh)
    check("D2f hard：傷照留（clear_conditions false）", dh["clear_conditions"] is False)
    check("D2g hard：升級曲線 50/40", lh == {"exp_base": 50, "exp_step": 40}, lh)
    check("D2h hard：檢定 +1、經驗 ×0.85、貨幣 ×0.85、物價 ×1.3",
          mh == {"monster_dc": 1, "exp_mult": 0.85, "coins_mult": 0.85, "price_mult": 1.3, "monster_hp_mult": 1.6}, mh)

    print("== D3 config 寫了一律贏（難度只補缺） ==")
    c = apply({"difficulty": "hard", "rules": {"death": {"skip_turns": 9}}})
    check("D3a config 的 rules.death 不被 hard 覆蓋", c["rules"]["death"]["skip_turns"] == 9,
          c["rules"]["death"])
    check("D3b 但倍率仍照 hard（那是這一檔的定義）", c["difficulty_mods"]["price_mult"] == 1.3)
    check("D3c 空值視同沒寫（{} ⇒ 難度補）",
          apply({"difficulty": "easy", "rules": {"death": {}}})["rules"]["death"]["skip_turns"] == 1)

    print("== D4 退檔與 legacy ==")
    c = apply({"difficulty": "nope"})
    check("D4a 指定不存在的檔名 ⇒ 退回 default（normal），不靜默",
          c.get("difficulty") == "normal", c.get("difficulty"))
    tmp = tempfile.mkdtemp(prefix="trpg_diff_")
    with open(os.path.join(tmp, "scenario.json"), "w", encoding="utf-8") as fh:
        json.dump({"meta": {"id": "x"}}, fh)
    c = apply({"difficulty": "hard"}, def_dir=tmp)
    check("D4b 劇本沒有 starting_difficulty ⇒ 原樣回傳（legacy 一字不變）",
          "difficulty_mods" not in c and "rules" not in c, c)
    check("D4c 劇本檔不存在 ⇒ 不炸、原樣回傳",
          "difficulty_mods" not in apply({"difficulty": "hard"},
                                         def_dir=os.path.join(tmp, "nope")))

    print("== D5 深拷貝：改結果不會污染劇本檔 ==")
    a, b = apply({"difficulty": "easy"}), apply({"difficulty": "easy"})
    a["rules"]["death"]["skip_turns"] = 99
    check("D5a 兩次套用互不影響", b["rules"]["death"]["skip_turns"] == 1)

    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    for f in FAIL:
        print("  FAIL:", f)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
