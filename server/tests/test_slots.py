#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**泛用槽位**自測（2026-10-20）：引擎不再寫死 mobs／star_coins／elevator／white_room。

兩段：
  A. 單元：`slots.load()` 的合併與驗證（缺項用預設／模組覆蓋／劇本覆蓋／壞宣告要叫）。
  B. **整合（真正的證據）**：複製百鬼塔的劇本，把四個名字**全部換掉**
     （`mob=shades`＋事件 `shade_slain`、`coin=gold`、`lift=lift`、`sanctum=chapel`），
     連 `board.json` 的節點欄位與 `type` 一起改成新詞 ⇒ 起桌、打怪、看投影、記憶見底被送回。
     引擎一行都不用改，這就是「具體名字由模組級別確定」的意思。

跑法：`python3 tests/test_slots.py`
"""
from __future__ import annotations
from turn_helper import ensure_turn, drop_scene  # 拆景后的测试小工具

import json
import os
import shutil
import sys
import tempfile
from typing import Any

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from trpg.rules.board import slots as slots_mod                         # noqa: E402
from trpg.service import table_factory                                  # noqa: E402

SRC = os.path.join(HERE, "modules", "hyakki_tower")
OK, FAIL = [], []

#: 這一組是「別的世界」的詞（一個都不跟百鬼塔重疊）
ALT = {
    "mob": {"key": "shades", "event": "shade_slain", "name_jp": "影", "name_cn": "影"},
    "coin": {"key": "gold", "name_jp": "金貨", "name_cn": "金币"},
    "lift": {"key": "lift", "event": "lift", "name_jp": "昇降筒", "name_cn": "升降筒"},
    "sanctum": {"key": "chapel", "event": "chapel", "name_jp": "礼拝堂", "name_cn": "礼拜堂"},
}


def check(name, cond, detail: Any = ""):
    (OK if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


# ---------------------------------------------------------------- A. 單元
def unit() -> None:
    print("== A1 預設（沒有 slots 段）＝現在的值，legacy 一字不變 ==")
    d = slots_mod.load({}, {})
    check("A1a 四個槽位的預設", {k: v["key"] for k, v in d.items()} ==
          {"mob": "mobs", "coin": "star_coins", "lift": "elevator", "sanctum": "white_room"},
          {k: v["key"] for k, v in d.items()})
    check("A1b 事件預設（coin 沒有事件型別）",
          slots_mod.event_of(d, "mob") == "mob_killed"
          and slots_mod.event_of(d, "coin") == "coin_changed"
          and slots_mod.event_of(d, "sanctum") == "white_room")

    print("== A2 模組／劇本宣告：近的贏 ==")
    mod = slots_mod.load({"slots": {"coin": {"key": "gold"}}}, {})
    check("A2a 模組宣告覆蓋預設，其他槽位不動",
          slots_mod.key_of(mod, "coin") == "gold" and slots_mod.key_of(mod, "mob") == "mobs")
    both = slots_mod.load({"slots": {"coin": {"key": "gold"}}},
                          {"slots": {"coin": {"key": "silver"}}})
    check("A2b 劇本層再蓋一層（silver 贏）", slots_mod.key_of(both, "coin") == "silver")

    print("== A3 事件別名：換了名字之後，舊事件仍要認（回放） ==")
    alt = slots_mod.load({"slots": ALT}, {})
    check("A3a 新名字在前、舊名字仍在",
          slots_mod.event_aliases(alt, "mob") == ("shade_slain", "mob_killed"),
          slots_mod.event_aliases(alt, "mob"))

    print("== A4 壞宣告要當場叫（不靜默） ==")
    for bad, why in (
        ({"slots": {"nope": {"key": "x"}}}, "不認識的槽位"),
        ({"slots": {"coin": {"key": ""}}}, "key 空字串"),
        ({"slots": {"coin": {"key": "gold", "event": "x"}}}, "coin 不需要 event"),
        ({"slots": {"mob": {"key": "x", "bogus": 1}}}, "不認識的欄位"),
        ({"slots": "not-a-dict"}, "slots 不是物件"),
    ):
        try:
            slots_mod.load(bad, {})
            check(f"A4 {why} ⇒ 叫", False, "沒有拋錯")
        except slots_mod.SlotError:
            check(f"A4 {why} ⇒ 叫", True)


# ---------------------------------------------------------------- B. 整合
def build_alt_world(tmp: str) -> str:
    """複製百鬼塔的**定義層**到 tmp，並把四個名字全部換成別的作品的詞。"""
    scen_rel = os.path.join("scenarios", "sannenme_no_onibarai")
    dst = os.path.join(tmp, "alt_world")
    # ⚠ 不能排掉 `tables`——機制包的表（packs/*/tables/*.json）就在裡面，排掉會「包不合法：找不到表」
    shutil.copytree(SRC, dst, ignore=shutil.ignore_patterns(
        "runs", ".git", ".studio", "assets", "node_modules"))

    # ① 模組層：module.json 宣告新槽位（sanctum 刻意留給劇本層——驗「近的贏」）
    mp = os.path.join(dst, "module.json")
    mod = json.load(open(mp, encoding="utf-8")) if os.path.exists(mp) else {}
    mod["slots"] = {k: v for k, v in ALT.items() if k != "sanctum"}
    json.dump(mod, open(mp, "w", encoding="utf-8"), ensure_ascii=False)

    # ①' 劇本層：scenario.json 再蓋一層（sanctum ⇒ chapel）
    sp = os.path.join(dst, scen_rel, "scenario.json")
    sd = json.load(open(sp, encoding="utf-8")) if os.path.exists(sp) else {}
    sd["slots"] = {"sanctum": ALT["sanctum"]}
    json.dump(sd, open(sp, "w", encoding="utf-8"), ensure_ascii=False)

    # ② 劇本層：board.json 的節點欄位與 type 一起換詞（資料跟著槽位走）
    bp = os.path.join(dst, scen_rel, "board.json")
    bd = json.load(open(bp, encoding="utf-8"))
    for n in (bd.get("nodes") or []):
        if "mobs" in n:
            n["shades"] = n.pop("mobs")
        if n.get("type") == "elevator":
            n["type"] = "lift"
    json.dump(bd, open(bp, "w", encoding="utf-8"), ensure_ascii=False)

    # ③ 商店表：幣種改 gold（這張表的 currency 是資料，跟著槽位走）
    shop_p = os.path.join(dst, scen_rel, "packs", "hyakki_world", "tables", "shop.json")
    if os.path.exists(shop_p):
        sd = json.load(open(shop_p, encoding="utf-8"))
        for r in (sd.get("rows") or []):
            if r.get("currency") in (None, "star_coins"):
                r["currency"] = "gold"
        json.dump(sd, open(shop_p, "w", encoding="utf-8"), ensure_ascii=False)
    return os.path.join(dst, scen_rel)


def integration() -> None:
    print("== B 整合作證：四個名字全換掉，引擎不動 ==")
    tmp = tempfile.mkdtemp(prefix="trpg_slots_")
    try:
        def_dir = build_alt_world(tmp)
        cfg_path = os.path.join(def_dir, "config.example.json")
        cfg = table_factory.load_cfg(cfg_path)
        cfg["auth"] = {"gm_token": "x"}
        cfg["session"] = {"require": False}
        cfg.setdefault("turn", {})["min_seats_to_start"] = 1
        home = os.path.join(tmp, "home")
        os.makedirs(home, exist_ok=True)
        svc = table_factory.build(home, cfg, log=lambda *a: None, def_dir=def_dir)
        t = svc.table
        t.join(seat="p1", name="テスト", kind="human", attrs={"str": 3, "agi": 3, "wil": 3}, order=0)

        check("B1 槽位載得進來（引擎認的是新詞）",
              t.slot("mob") == "shades" and t.slot("coin") == "gold"
              and t.slot("lift") == "lift" and t.slot("sanctum") == "chapel",
              (t.slot("mob"), t.slot("coin"), t.slot("lift"), t.slot("sanctum")))
        check("B2 board 的初始怪數用新詞（shades）",
              "shades" in (t.state.get("board") or {}) and "mobs" not in (t.state.get("board") or {}),
              sorted((t.state.get("board") or {}).keys())[:8])

        # 找一個有怪且 depth 的節點，把人放過去
        nodes = [n for n in (t.board.get("nodes") or [])
                 if int(n.get("shades") or 0) > 0 and n.get("depth") is not None]
        check("B3 有節點帶新詞的怪數（board.json 已改成 shades）", bool(nodes), len(nodes))
        node = nodes[0]
        t.state["seats"]["p1"]["node"] = node["id"]
        left0 = int(t.state["board"]["shades"].get(node["id"]) or 0)

        # 開景 → 開輪（fight 只准當前回合那一席動）
        t.cfg.setdefault("turn", {})["min_seats_to_start"] = 1
        ensure_turn(t)
        n_ev = len(t.events)
        # 個體化怪物有 HP（1~4）⇒ 一隻要打好幾下；回合額度花完就收回來再打
        out, killed = None, False
        for _ in range(12):
            out = t.act(seat="p1", action="fight")
            if isinstance(out, dict) and out.get("error"):
                break
            if any(e.get("type") == "shade_slain" for e in t.events[n_ev:]):
                killed = True
                break
            if "down" in (t.state["seats"]["p1"].get("status") or []):
                break
            if (t.state.get("turn") or {}).get("seat") != "p1":
                t.end_turn(reason="end_turn")
                t._maybe_autostart()
        new_ev = [e for e in t.events[n_ev:]]
        check("B4 打得到（fight 走通，沒有因為換詞而找不到怪）",
              not (isinstance(out, dict) and out.get("error")), str(out)[:120])
        check("B5 擊破事件用新詞（shade_slain）", killed,
              [e.get("type") for e in new_ev][:8])
        check("B6 state 的剩餘怪數用新詞且真的少了一隻",
              int(t.state["board"]["shades"].get(node["id"]) or 0) == max(0, left0 - 1),
              (left0, t.state["board"]["shades"].get(node["id"])))
        check("B7 獎勵進的是 gold（不是 star_coins）",
              int(t.state["seats"]["p1"].get("gold") or 0) >= 0
              and "star_coins" not in t.state["seats"]["p1"],
              {k: v for k, v in t.state["seats"]["p1"].items() if k in ("gold", "star_coins")})

        v = t.view(seat="p1")
        check("B8 投影給抽象名 coin／mob（前端可讀新世界）",
              "coin" in ((v.get("you") or {})), sorted((v.get("you") or {}).keys())[:10])
        check("B9 投影的 board 段有 mob（不再是 mobs）",
              "mob" in ((v.get("board") or {})) and "mobs" not in (v.get("board") or {}),
              [k for k in (v.get("board") or {})][:8])

        # 記憶見底 ⇒ 送回 chapel（sanctum 槽位）
        t.state["seats"]["p1"]["memory"] = 1
        t._memory_delta("p1", -5, "（測試）") if hasattr(t, "_memory_delta") else None
        ev_types = [e.get("type") for e in t.events]
        check("B10 記憶見底走 sanctum 槽位（事件名 chapel）",
              ("chapel" in ev_types) or (t.state["seats"]["p1"].get("node") not in (None, node["id"])),
              ev_types[-4:])

        # 回放一致（槽位換了名字，事件流仍要能重算）
        rc = t.replay_check()
        check("B11 換了名字的世界，回放仍然一致", bool(rc.get("replay_matches")), rc.get("diff"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    unit()
    integration()
    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    for f in FAIL:
        print("  FAIL:", f)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
