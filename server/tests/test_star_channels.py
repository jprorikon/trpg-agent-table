#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**星等渠道制**自測（2026-10-20）：shop／goal／side 三條路各漲幾星、總上限、買星價格遞增。

設計者的定調：「這是劇本決定的，我只是說有這個機制」——所以這裡測的是**機制**：
`channels` 怎麼被讀、怎麼封頂、條件成立時怎麼補滿；**數字全部從劇本的表來**。

跑法：`python3 tests/test_star_channels.py`
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
from trpg.rules import scenario as scenario_mod                           # noqa: E402

DEF = os.path.join(HERE, "modules", "hyakki_tower", "scenarios", "sannenme_no_onibarai")
OK, FAIL = [], []


def check(name, cond, detail: Any = ""):
    (OK if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


def mk(level="normal"):
    cfg = table_factory.load_cfg(os.path.join(DEF, "config.example.json"))
    cfg["auth"] = {"gm_token": "x"}
    cfg["session"] = {"require": False}
    cfg["difficulty"] = level
    home = tempfile.mkdtemp(prefix="starch_")
    svc = table_factory.build(home, cfg, log=lambda *a: None, def_dir=DEF)
    t = svc.table
    t.join(seat="p1", name="テスト", kind="human")
    t.state["seats"]["p1"]["star_coins"] = 200
    return t


def main() -> int:                                                        # noqa: C901
    print("== S1 表讀得進來（渠道由劇本宣告） ==")
    t = mk()
    chs = t.star_channels
    check("S1a 三條渠道都在", set(chs) == {"shop", "goal", "side"}, sorted(chs))
    check("S1b 各渠道的 max 照劇本（2/2/2）", [chs[k]["max"] for k in ("shop", "goal", "side")] == [2, 2, 2])
    check("S1c 總上限＝劇本 max_star（5）", t.stars_max() == 5, t.stars_max())
    check("S1d 開局 ★1、各渠道都 0", t.star_of("p1") == 1 and t.star_channel_of("p1") == {}, t.star_channel_of("p1"))

    print("== S2 買星：渠道 max 生效、星等跟著漲 ==")
    t = mk()
    r1 = t.star_grant(seat="p1", channel="shop", value=1, by="shop")
    check("S2a 第一顆 ⇒ ★2", r1["added"] == 1 and t.star_of("p1") == 2, r1)
    r2 = t.star_grant(seat="p1", channel="shop", value=1, by="shop")
    check("S2b 第二顆 ⇒ ★3", r2["added"] == 1 and t.star_of("p1") == 3)
    r3 = t.star_grant(seat="p1", channel="shop", value=1, by="shop")
    check("S2c 第三顆被渠道 max 擋下（capped，不報錯）",
          r3["added"] == 0 and r3.get("capped") and r3.get("reason") == "channel_max", r3)
    check("S2d 星等沒超過 ★3", t.star_of("p1") == 3)

    print("== S3 條件成立 ⇒ **一次補滿該渠道**（不是每筆事件 +1） ==")
    t = mk()
    t.star_grant(seat="p1", channel="shop", value=2, by="shop")           # ★3
    t.state.setdefault("facts", {})["side_quest_done"] = True              # 劇本寫下的全桌事實
    evs = t.stars_check({"type": "fact_set"})
    check("S3a side 一次補滿 2 星 ⇒ ★5", t.star_of("p1") == 5, (t.star_of("p1"), t.star_channel_of("p1")))
    check("S3b 有發 star_up 事件（可回放）", bool(evs), len(evs))
    evs2 = t.stars_check({"type": "fact_set"})
    check("S3c 第二筆事件不再重複給（已滿）", not evs2 and t.star_of("p1") == 5)
    check("S3d GM／NPC 不升星（全桌事實只給玩家席）",
          not (t.state.get("star") or {}).get("gm")
          and not (t.state.get("star") or {}).get("npc1"),
          t.state.get("star"))

    print("== S3b 渠道制：scope=all 也只給玩家席 ==")
    t = mk()
    _evs = scenario_mod.effects_apply(
        t, [{"star_grant": {"channel": "shop", "value": 1, "scope": "all"}}], {}, by="test")
    _seats = {e.get("seat") for e in _evs}
    check("S3e scope=all 不含 gm／npc1", "gm" not in _seats and "npc1" not in _seats, sorted(_seats))

    print("== S4 總上限封頂（1 + 三渠道各 2 ＝ 7 ⇒ 封在 5） ==")
    t = mk()
    t.star_grant(seat="p1", channel="shop", value=2, by="shop")           # ★3
    t.star_grant(seat="p1", channel="goal", value=2, by="goals")          # ★5
    r = t.star_grant(seat="p1", channel="side", value=2, by="story")
    check("S4a 已經 ★5 ⇒ 再給被 star_cap 擋", r["added"] == 0 and r.get("reason") == "star_cap", r)
    check("S4b max_star 是硬上限", t.star_of("p1") == 5)

    print("== S5 買星走商店（表路）：價格逐次遞增、不進庫存 ==")
    t = mk()
    v = {r["item"]: r for r in t.shop_view(seat="p1", node_id="hyakki_f3")}
    check("S5a 3 階店裡有賣星的商品", "star_upgrade" in v, sorted(v))
    p1 = v["star_upgrade"]["price"]
    out1 = t.shop_buy(seat="p1", item="star_upgrade", node_id="hyakki_f3")
    v2 = {r["item"]: r for r in t.shop_view(seat="p1", node_id="hyakki_f3")}
    p2 = v2["star_upgrade"]["price"]
    check("S5b 第一顆 15、第二顆 25（price_step 由劇本給）", (p1, p2) == (15, 25), (p1, p2))
    check("S5c 買到不進 inventory（賣的是星不是道具）",
          out1.get("via") == "table" and not (t.state.get("inventory") or {}).get("p1"),
          out1)
    check("S5d 買完 ★2、渠道 shop=1", t.star_of("p1") == 2 and t.star_channel_of("p1").get("shop") == 1)
    out2 = t.shop_buy(seat="p1", item="star_upgrade", node_id="hyakki_f3")
    check("S5e 第二顆買下去 ★3", t.star_of("p1") == 3, out2)
    _coins_before = int(t.state["seats"]["p1"].get("star_coins") or 0)
    try:
        t.shop_buy(seat="p1", item="star_upgrade", node_id="hyakki_f3")
        _err = ""
    except Exception as exc:                                              # noqa: BLE001
        _err = str(exc)
    check("S5f 第三顆：**買之前就被擋**（渠道滿了不再賣，不白扣錢）",
          "買滿" in _err and int(t.state["seats"]["p1"].get("star_coins") or 0) == _coins_before, _err)
    _v3 = {r["item"]: r for r in t.shop_view(seat="p1", node_id="hyakki_f3")}
    check("S5g 商店那邊標 sold_out（前端可以据此置灰）", _v3["star_upgrade"].get("sold_out") is True,
          _v3["star_upgrade"])

    print("== S5b 總星等封頂時：商店**不賣**（不能扣錢只給 0 星） ==")
    t = mk()
    t.star_grant(seat="p1", channel="goal", value=2, by="t")       # 1+2
    t.star_grant(seat="p1", channel="side", value=2, by="t")       # ★5（shop 渠道還是 0）
    _v = {r["item"]: r for r in t.shop_view(seat="p1", node_id="hyakki_f3")}
    check("S5h 總上限到了 ⇒ 賣星商品 sold_out", _v["star_upgrade"].get("sold_out") is True, _v["star_upgrade"])
    _coins0 = int(t.state["seats"]["p1"].get("star_coins") or 0)
    try:
        t.shop_buy(seat="p1", item="star_upgrade", node_id="hyakki_f3")
        _err = ""
    except Exception as exc:                                              # noqa: BLE001
        _err = str(exc)
    check("S5i 買之前就被擋（錢沒扣、星沒給）",
          "買滿" in _err and int(t.state["seats"]["p1"].get("star_coins") or 0) == _coins0
          and t.star_of("p1") == 5, _err)

    print("== S5c config 路的賣星：壞渠道／滿額都在扣錢前擋 ==")
    t = mk()
    t.tower_cfg["shop"] = {"star_cfg": {"cost": 10, "star": 1, "star_channel": "nope"}}
    _coins0 = int(t.state["seats"]["p1"].get("star_coins") or 0)
    _n0 = len(t.events)
    try:
        t.shop_buy(seat="p1", item="star_cfg", node_id="hyakki_f3")
        _err = ""
    except Exception as exc:                                              # noqa: BLE001
        _err = str(exc)
    check("S5j 壞渠道：扣錢前就拒、不留 shop_buy 事件",
          "渠道" in _err and len(t.events) == _n0
          and int(t.state["seats"]["p1"].get("star_coins") or 0) == _coins0, _err)
    t.star_grant(seat="p1", channel="goal", value=2, by="t")
    t.star_grant(seat="p1", channel="side", value=2, by="t")       # ★5，shop 仍 0
    t.tower_cfg["shop"] = {"star_cfg": {"cost": 10, "star": 1, "star_channel": "shop"}}
    try:
        t.shop_buy(seat="p1", item="star_cfg", node_id="hyakki_f3")
        _err2 = ""
    except Exception as exc:                                              # noqa: BLE001
        _err2 = str(exc)
    check("S5k 總上限到了：config 路也不賣（不扣錢）",
          "買滿" in _err2 and int(t.state["seats"]["p1"].get("star_coins") or 0) == _coins0, _err2)

    print("== S5d 滿額的 star_grant effect 不回傳舊事件（不重跑觸發） ==")
    t = mk()
    t.star_grant(seat="p1", channel="shop", value=2, by="t")       # 渠道滿
    _got = scenario_mod.effects_apply(
        t, [{"star_grant": {"channel": "shop", "value": 2}}],
        {"me": {"seat": "p1"}}, by="test")
    check("S5l 回空列表（不是 table.events[-1]）", _got == [], _got)

    print("== S5e `price_step_mode` 由模組定：star（預設）vs purchase ==")
    t = mk()
    t.shop_rows.append({"id": "step_buy", "price": 10, "price_step": 10,
                        "star": 1, "price_step_mode": "purchase"})
    _v = {r["item"]: r for r in t.shop_view(seat="p1", node_id="hyakki_f3")}
    _p1 = _v["step_buy"]["price"]
    t.shop_buy(seat="p1", item="step_buy", node_id="hyakki_f3")
    _v2 = {r["item"]: r for r in t.shop_view(seat="p1", node_id="hyakki_f3")}
    _p2 = _v2["step_buy"]["price"]
    t.shop_buy(seat="p1", item="step_buy", node_id="hyakki_f3")
    _v3 = {r["item"]: r for r in t.shop_view(seat="p1", node_id="hyakki_f3")}
    _p3 = _v3["step_buy"]["price"]
    check("S5m purchase 模式：10 → 20 → 30（按購買次數，不是星數）",
          (_p1, _p2, _p3) == (10, 20, 30), (_p1, _p2, _p3))

    print("== S6 不在那家店就買不到（nodes 限定照舊） ==")
    t = mk()
    v = {r["item"] for r in t.shop_view(seat="p1", node_id="teckan")}
    check("S6a 鍛冶の街沒有賣星", "star_upgrade" not in v, sorted(v))

    print("== S7 全體副任務：劇本 trigger 寫下事實 ⇒ 全桌 side 渠道補滿 ==")
    t = mk()
    t.join(seat="p2", name="テスト2", kind="human")
    # 2026-10-21：判據是「輪」，門檻住在劇本（`t_side_all_endure`）——直接讀它，別寫死
    _scn = json.load(open(os.path.join(DEF, "scenario.json"), encoding="utf-8"))
    _thr = 30
    for _t in (_scn.get("triggers") or []):
        if _t.get("id") == "t_side_all_endure":
            _thr = int((_t.get("when") or {}).get(">=", [None, 30])[1])
    t.state["round_no"] = _thr
    t._emit("turn_ended", public=True)
    _f = (t.state.get("facts") or {})
    check("S7a 劇本 trigger 真的寫了 facts.side_quest_done", bool(_f.get("side_quest_done")), _f)
    check("S7b 兩席都拿到 side 2 星（★3）",
          t.star_channel_of("p1").get("side") == 2 and t.star_channel_of("p2").get("side") == 2
          and t.star_of("p1") == 3 and t.star_of("p2") == 3,
          (t.star_channel_of("p1"), t.star_channel_of("p2"), t.star_of("p1"), t.star_of("p2")))
    t._emit("turn_ended", public=True)
    check("S7c 再一筆事件不會重複給（渠道已滿）",
          t.star_of("p1") == 3 and t.star_channel_of("p1").get("side") == 2)
    check("S7d GM／NPC 沒有吃到全體副任務的星",
          not (t.state.get("star") or {}).get("gm")
          and not (t.state.get("star") or {}).get("npc1"),
          t.state.get("star"))

    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    for f in FAIL:
        print("  FAIL:", f)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
