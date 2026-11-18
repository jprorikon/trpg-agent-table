#!/usr/bin/env python3
"""**經驗／等級的自測**（M4，2026-10-07）——等級表（`packs/rules.core/tables/levels.json`）。

    python3 test_advancement.py

八組：
  L1 **表**：等級表載得動（5 級）；schema 壞（缺 level／need、level 重複、gains 亂鍵、兩張表）當場叫。
  L2 **沒啟用＝legacy**：`exp`／`level` 是空的、join **一個事件都不多發**、投影沒有 level 欄位。
  L3 **開局**：join 之後自動 1 級（`level_up` 帶 `after`）＝起始資源從表的那一列出。
  L4 **給經驗**：`exp_gained` 帶 `total`；跨門檻**自動升級**（逐級發事件）。
  L5 **成長真的生效**：`max_hp` 加上限（同時補血）、基準屬性 +1（`attrs_of` 反映）。
  L6 **擊破小怪給經驗**：`params.exp_per_star_kill` 開關（沒啟用該包＝沒有這個參數 ⇒ 不發事件）。
  L7 **事件溯源＋投影**：`op=replay` 一致；自己的卡上有 level／exp／next_need。
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
MODF = scenario.module_filename(DEF) or "scenario.json"   # 劇本規則資料的檔名（WS-B：新名優先，別硬編）

from trpg.rules import advancement as advancement                                                       # noqa: E402
from trpg.rules import packs as packs                                                             # noqa: E402
from trpg.core import store as store                                                             # noqa: E402
from trpg.engine import Table                                                 # noqa: E402

OK, FAIL = [], []
SPEC = ["d20core", "wound", "rules.core"]


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


def _raises(fn, exc):
    try:
        fn()
    except exc:
        return True
    except Exception as exc2:                                             # noqa: BLE001
        print(f"    （丟的是 {type(exc2).__name__}: {exc2}）")
        return False
    return False


def mk_home(*, spec=SPEC):
    tmp = tempfile.mkdtemp(prefix="trpg_adv_")
    packs.seed_module_packs(DEF, tmp)   # 模組層的包（rules.core 住模組 repo；臨時桌推不出模組）
    for f in ("board.json", "goals.json", "clues.json", "seats.json", MODF):
        shutil.copy(os.path.join(DEF, f), os.path.join(tmp, f))
    cfg = layout.read_table_config(CAMP)
    # ⚠ 夾具隔離（2026-10-07 單一進程 + 子路徑）：這兩個是**部署專屬**的鍵，
    #   從真 config 複製進來會讓 temp 桌指到現役 hub／帶別人的前綴 ⇒ 一律清掉。
    cfg.pop("hub_base", None)
    cfg.pop("public_prefix", None)
    cfg["port"] = 0
    if spec is None:
        cfg.pop("packs", None)
    else:
        cfg["packs"] = {"enabled": list(spec)}
    with open(os.path.join(tmp, "config.json"), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False)
    return tmp, cfg


def mk_table(tmp, cfg):
    return Table(home=tmp, cfg=cfg, store=store.FileStore(tmp), clock=lambda: 1000.0)


def main() -> int:                                                        # noqa: C901
    print("== L1. 等級表 ==")
    tmp1, cfg1 = mk_home()
    try:
        t = mk_table(tmp1, cfg1)
        check("L1a 等級表載得動（5 級；base 1）",
              len(t.levels_table) == 5 and t.levels_base == 1,
              f"{[r['level'] for r in t.levels_table]}")
        bad = os.path.join(tmp1, "bad_levels.json")
        with open(bad, "w", encoding="utf-8") as fh:
            json.dump({"kind": "levels", "rows": [{"level": 2, "need": 10},
                                                  {"level": 2, "need": 20}]}, fh)
        check("L1b schema 壞（level 重複）⇒ 載入時叫",
              _raises(lambda: advancement.load([bad]), advancement.AdvancementError))
        with open(bad, "w", encoding="utf-8") as fh:
            json.dump({"kind": "levels", "rows": [{"level": 2, "need": 10,
                                                   "gains": {"hp": 3}}]}, fh)
        check("L1c gains 亂鍵（hp 不是 max_hp／attrs／resources）⇒ 當場叫",
              _raises(lambda: advancement.load([bad]), advancement.AdvancementError))
        with open(bad, "w", encoding="utf-8") as fh:
            json.dump({"kind": "levels", "rows": [{"level": 1, "need": 0}]}, fh)
        with open(bad + "2", "w", encoding="utf-8") as fh:
            json.dump({"kind": "levels", "rows": [{"level": 1, "need": 0}]}, fh)
        check("L1d 一張桌兩張等級表 ⇒ 當場叫",
              _raises(lambda: advancement.load([bad, bad + "2"]), advancement.AdvancementError))
    finally:
        shutil.rmtree(tmp1, ignore_errors=True)

    print("== L2. 沒啟用＝legacy ==")
    tmp2, cfg2 = mk_home(spec=["d20core"])
    try:
        t = mk_table(tmp2, cfg2)
        n0 = len(t.events)
        t.join(seat="p1", name="カガリ", kind="human", attrs={"str": 3, "agi": 3, "wil": 3}, order=0)
        _new = [e.get("type") for e in t.events][n0:]
        check("L2a join 不多發經驗相關事件（沒有 level_up／exp_gained）",
              "level_up" not in _new and "exp_gained" not in _new, f"{_new}")
        check("L2b exp／level 是空的、level_of 回 1、給經驗當場叫",
              t.state["exp"] == {} and t.state["level"] == {} and t.level_of("p1") == 1
              and _raises(lambda: t.exp_gain(seat="p1", amount=10), Exception))
        check("L2c 投影沒有 level／skills 欄位（legacy 逐字不變）",
              "level" not in (t.view(seat="p1").get("you") or {}))
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)

    print("== L3/L4/L5. 開局 1 級 → 給經驗 → 升級 ==")
    tmp3, cfg3 = mk_home()
    try:
        t = mk_table(tmp3, cfg3)
        t.join(seat="p1", name="カガリ", kind="human",
               attrs={"str": 3, "agi": 3, "wil": 3, "luck": 2}, order=0)
        ev1 = [e for e in t.events if e.get("type") == "level_up"]
        check("L3a 開局自動 1 級（level_up 事件帶 after）",
              len(ev1) == 1 and ev1[0]["level"] == 1 and ev1[0].get("by") == "join"
              and ev1[0]["after"]["resources"].get("mana") == 3,
              f"{ev1[0]['after'] if ev1 else None}")
        check("L3b 起始資源落地（mana 3；HP 上限從 rules.hp 出）",
              t.resources_of("p1").get("mana") == 3 and t.state["seats"]["p1"]["hp"] > 0,
              f"{t.resources_of('p1')}")
        max_hp0 = t.state["seats"]["p1"]["max_hp"]
        r = t.exp_gain(seat="p1", amount=30, reason="test", by="gm")
        check("L4a exp_gained 帶 total（回放不必重算）",
              r["exp"] == 30 and [e for e in t.events if e.get("type") == "exp_gained"][-1]["total"] == 30)
        check("L4b 跨門檻自動升級（level 2）", r["level"] == 2 and r["level_ups"] == [2],
              f"{r}")
        s = t.state["seats"]["p1"]
        check("L5a 成長生效：max_hp ＋4、agi 基準 ＋1",
              s["max_hp"] == max_hp0 + 4 and int(s["attrs"]["agi"]) == 4,
              f"max_hp {max_hp0}->{s['max_hp']} attrs={s['attrs']}")
        check("L5b 屬性成長進 `attrs_of`（檢定吃得到）",
              int(t.attrs_of("p1")["agi"]) == 4 and int(t.attrs_of("p1")["str"]) == 3,
              f"{t.attrs_of('p1')}")
        r2 = t.exp_gain(seat="p1", amount=60, reason="test2", by="gm")     # 累計 90 ⇒ 3 級
        check("L4c 一次給多級：逐級發 level_up（90 ⇒ 3 級）",
              r2["level"] == 3 and r2["level_ups"] == [3], f"{r2}")
        _v = t.view(seat="p1")
        check("L7a 自己的卡上有 level／exp／next_need",
              (_v.get("you") or {}).get("level") == 3 and (_v.get("you") or {}).get("exp") == 90
              and (_v.get("you") or {}).get("next_need") == 150,
              f"{(  _v.get('you') or {}).get('level')}/{(  _v.get('you') or {}).get('exp')}")
        rp = t.replay_check()
        check("L7b 有經驗／升級事件的流 op=replay 一致", bool(rp.get("replay_matches")),
              f"{rp.get('diff')}")
    finally:
        shutil.rmtree(tmp3, ignore_errors=True)

    print("== L8. 這一局才啟用規則書：GM 給一次經驗 ⇒ 自動補開局 1 級 ==")
    tmp8, cfg8 = mk_home(spec=["d20core"])          # 先當「沒有等級表」的桌
    try:
        t8 = mk_table(tmp8, cfg8)
        t8.join(seat="p1", name="カガリ", kind="human",
                attrs={"str": 3, "agi": 3, "wil": 3}, order=0)
        check("L8a 沒啟用時 join 過去的席位沒有等級", t8.level_of("p1") == 1
              and "p1" not in (t8.state.get("level") or {}))
        cfg9 = json.loads(json.dumps(cfg8))
        cfg9["packs"] = {"enabled": SPEC}           # 之後才把規則書裝上去（同一份事件流）
        with open(os.path.join(tmp8, "config.json"), "w", encoding="utf-8") as fh:
            json.dump(cfg9, fh, ensure_ascii=False)
        t9 = mk_table(tmp8, cfg9)
        check("L8b 只是裝上去**不會自己寫事件**（啟用包不該改遊戲）",
              [e.get("type") for e in t9.events] == [e.get("type") for e in t8.events],
              f"{len(t9.events)} vs {len(t8.events)}")
        r = t9.exp_gain(seat="p1", amount=6, reason="規則書生效以來的歷練", by="gm")
        check("L8c GM 給一次經驗 ⇒ 先補開局 1 級（起始資源到手）＋記下經驗",
              t9.level_of("p1") == 1 and t9.resources_of("p1").get("mana") == 3
              and t9.exp_of("p1") == 6 and r.get("level") == 1,
              f"lv={t9.level_of('p1')} mana={t9.resources_of('p1')} exp={t9.exp_of('p1')}")
        check("L8d 補過之後投影看得到技能清單（給前端選技能用）",
              bool(((t9.view(seat="p1").get("you") or {}).get("skills") or [])))
    finally:
        shutil.rmtree(tmp8, ignore_errors=True)

    print("== L6. 擊破小怪給經驗（包 params 開關）==")
    _p = packs.resolve(SPEC, home=DEF).params
    _p0 = packs.resolve(["d20core", "board", "semic"]).params
    check("L6a 啟用規則書包 ⇒ params 有 exp_per_star_kill（board 的擊破分支才會走）",
          int(_p.get("exp_per_star_kill") or 0) == 6
          and "exp_per_star_kill" not in _p0,
          f"{_p.get('exp_per_star_kill')} / 沒包={_p0.get('exp_per_star_kill')}")
    check("L6b 沒啟用 ⇒ 參數不存在 ⇒ `if _exp:` 為假（不發 exp 事件，行為不變）",
          not _p0.get("exp_per_star_kill"))

    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
