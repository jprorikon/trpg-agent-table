#!/usr/bin/env python3
"""**道具／庫存的自測**（★2，2026-10-07）——道具表（`packs/rules.core/tables/items.json`）。

    python3 test_items.py

十組：
  Y1 **表**：`rules.core` 的道具表載得動；schema 壞（缺 id／id 重複／price 亂寫／use.effects 空／
     use.target 亂寫／不認識的欄位）當場叫。
  Y2 **沒啟用＝legacy**：動作目錄沒有 `use_item`、`inventory` 空、投影沒有 `you.items`、
     `item_add` 當場叫、事件流零變更。
  Y3 **給／拿**：`item_changed` 進事件流（公開、`after` 是結果值）；扣到負＝當場叫、**不留幽靈事件**。
  Y4 **用**：先扣 1 再套效果（heal 真的補血）；不足⇒拒且**不套效果**。
  Y5 **要目標的**：`use.target=foe` 沒給 target ⇒ 叫；target 不存在 ⇒ 叫。
  Y6 **不能用的**：沒有 `use.effects` 的道具 ⇒ 叫（不是靜默沒反應）。
  Y7 **投影**：`view.you.items`（數量／能不能用／對誰用）與席位公開 `items`；空的不出現。
  Y8 **收窄**：沒啟用帶道具表的包 ⇒ 劇本用 `item_grant`／動作 `use_item` **載入時就叫**。
  Y9 **劇本路線**：`effects_apply` 跑 `item_grant`／`item_take`（與 GM／動作同一條路）。
  Y10 **事件溯源**：用過道具的流 `op=replay` 一致。
  Y11 **打怪物**：`use.target=foe` 的 `target` 給怪物 uid ⇒ 走 `monster_damaged`（破甲道具丟得到王）；
     給不存在的 uid ⇒ 叫，且**不扣道具**。
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

from trpg.rules import items as items                                                             # noqa: E402
from trpg.rules import scenario as scenario                                                          # noqa: E402
from trpg.core import store as store                                                             # noqa: E402
from trpg.engine import Table                                                 # noqa: E402

OK, FAIL = [], []
SPEC = ["d20core", "board", "semic", "mode.turn", "wound", "rules.core"]
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


def mk_home(*, spec=SPEC):
    tmp = tempfile.mkdtemp(prefix="trpg_items_")
    packs.seed_module_packs(DEF, tmp)   # 模組層的包（rules.core 住模組 repo；臨時桌推不出模組）
    for f in ("board.json", "goals.json", "clues.json", "seats.json", MODF):
        shutil.copy(os.path.join(DEF, f), os.path.join(tmp, f))
    layout.copy_table_config(CAMP, os.path.join(tmp, "config.json"))   # 旋鈕 ⇒ 桌目錄（定義在 DEF）
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
    print("== Y1. 表 ==")
    rows = json.load(open(os.path.join(packs.module_packs_dir(DEF), "rules.core", "tables", "items.json"),
                          encoding="utf-8"))["rows"]
    check("Y1a 道具表載得動（7 件：含炸彈／急救包）", len(rows) == 7, f"{[r['id'] for r in rows]}")
    check("Y1b 缺 id ⇒ 不合法", any("id" in x for x in items.validate([{"name_jp": "x"}])))
    check("Y1c id 重複 ⇒ 不合法",
          any("重複" in x for x in items.validate([{"id": "a"}, {"id": "a"}])))
    check("Y1d price 非整數 ⇒ 不合法",
          any("price" in x for x in items.validate([{"id": "a", "price": "3"}])))
    check("Y1e use.effects 空 ⇒ 不合法",
          any("use.effects" in x for x in items.validate([{"id": "a", "use": {"effects": []}}])))
    check("Y1f use.target 亂寫 ⇒ 不合法",
          any("use.target" in x for x in items.validate(
              [{"id": "a", "use": {"target": "nope", "effects": [{"emit": {"type": "x"}}]}}])))
    check("Y1g 不認識的欄位 ⇒ 不合法",
          any("不認識的欄位" in x for x in items.validate([{"id": "a", "weight": 1}])))

    print("== Y2. 沒啟用＝legacy ==")
    tmp2, cfg2 = mk_home(spec=LEGACY)
    try:
        t2 = mk_table(tmp2, cfg2)
        seat_up(t2, "p1")
        # ⚠ 動作目錄＝config ∪ 包宣告；現役 config 已列 `use_item` ⇒ 這裡只看「機制沒開」
        check("Y2a 沒啟用帶道具表的包：use_item() 當場叫（機制沒開，不是靜默沒反應）",
              _raises(lambda: t2.use_item(seat="p1", item="rope"), Exception))
        check("Y2b 庫存與投影都是空的（legacy 欄位不出現）",
              t2.items_of("p1") == {} and t2.items_view("p1") == []
              and "items" not in ((t2.view(seat="p1").get("you") or {})))
        check("Y2c item_add 當場叫（沒有表就沒有道具）",
              _raises(lambda: t2.item_add(target="p1", id="rope"), items.ItemError))
        check("Y2d 沒啟用 ⇒ 一個事件都不多發",
              not any(e.get("type") == "item_changed" for e in t2.events))
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)

    tmp, cfg = mk_home()
    try:
        t = mk_table(tmp, cfg)
        seat_up(t, "p1")
        seat_up(t, "p2", name="シズク")
        check("Y3a 動作目錄有 use（道具／技能都走這一手；use_item 已收斂）", "use" in t.action_ids)
        r = t.item_add(target="p1", id="heal_pack", count=2)
        check("Y3b 給道具 ⇒ item_changed 公開事件（after 是結果值）",
              r["count"] == 2 and any(e.get("type") == "item_changed" and e.get("after") == 2
                                      and e.get("public") for e in t.events),
              f"{r}")
        n0 = len(t.events)
        check("Y3c 扣到負 ⇒ 當場叫、不留幽靈事件",
              _raises(lambda: t.item_take(target="p1", id="heal_pack", count=5), items.ItemError)
              and len(t.events) == n0)
        check("Y3d 不認識的道具 id ⇒ 當場叫（且列出有哪些）",
              _raises(lambda: t.item_add(target="p1", id="nope"), items.ItemError))
        # Y4 用：先扣再套效果
        t.state["seats"]["p1"]["hp"] = 5
        n1 = len(t.events)
        u = t.use_item(seat="p1", item="heal_pack")
        _heal = [e for e in t.events[n1:] if e.get("type") == "heal"]
        check("Y4a 用道具 ⇒ 先扣 1（left=1）再套 effects（真的補血）",
              u["left"] == 1 and _heal and t.state["seats"]["p1"]["hp"] > 5,
              f"left={u['left']} hp={t.state['seats']['p1']['hp']}")
        n2 = len(t.events)
        t.item_take(target="p1", id="heal_pack", count=1)          # 用光
        _hp_before = t.state["seats"]["p1"]["hp"]
        check("Y4b 沒貨 ⇒ 拒（不套效果、不留效果事件）",
              _raises(lambda: t.use_item(seat="p1", item="heal_pack"), items.ItemError)
              and t.state["seats"]["p1"]["hp"] == _hp_before
              and not [e for e in t.events[n2:] if e.get("type") == "heal"])
        # Y5 要目標的
        t.item_add(target="p1", id="star_flare")
        check("Y5a use.target=foe 沒給 target ⇒ 叫",
              _raises(lambda: t.use_item(seat="p1", item="star_flare"), items.ItemError))
        check("Y5b target 席位不存在 ⇒ 叫",
              _raises(lambda: t.use_item(seat="p1", item="star_flare", target="nope"),
                      items.ItemError))
        n3 = len(t.events)
        t.item_add(target="p1", id="rope")
        check("Y6 沒有 use.effects 的道具 ⇒ 叫（不是靜默沒反應）",
              _raises(lambda: t.use_item(seat="p1", item="rope"), items.ItemError)
              and not [e for e in t.events[n3:] if e.get("type") == "damage"])
        # Y7 投影
        v = t.view(seat="p1")
        yitems = (v.get("you") or {}).get("items") or []
        check("Y7a 自己看得到道具（數量／能不能用／對誰用）",
              any(x["id"] == "star_flare" and x["count"] == 1 and x["usable"]
                  and x["use_target"] == "foe" for x in yitems)
              and all(x["count"] > 0 for x in yitems), f"{[(x['id'], x['count']) for x in yitems]}")
        seats = {s["seat"]: s for s in v.get("seats") or []}
        check("Y7b 席位公開看得到道具（有值才出現）",
              seats["p1"].get("items", {}).get("star_flare") == 1
              and "items" not in (seats.get("p2") or {}), f"{seats['p1'].get('items')}")
        # Y8 收窄
        tmp8, cfg8 = mk_home(spec=LEGACY)
        try:
            cfg8["module"] = cfg8.get("module")
            with open(scenario.resolve_module_path(DEF)[0],          # 新名／舊名都吃
                      encoding="utf-8") as fh:
                mod = json.load(fh)
            mod.setdefault("endings", [])
            mod["endings"].append({"id": "e_item", "kind": "neutral",
                                   "when": {"==": [{"var": "world.event.type"}, "never"]}})
            mod["triggers"] = (mod.get("triggers") or []) + [
                {"id": "t_item", "when": {"==": [{"var": "world.event.type"}, "mock"]},
                 "then": [{"item_grant": {"id": "rope"}}]}]
            with open(os.path.join(tmp8, MODF), "w", encoding="utf-8") as fh:
                json.dump(mod, fh, ensure_ascii=False)
            try:
                mk_table(tmp8, cfg8)
                _narrow = False
            except Exception as exc:                                          # noqa: BLE001
                _narrow = "item_grant" in str(exc)
            check("Y8 沒啟用帶道具表的包 ⇒ 劇本用 item_grant 載入時就叫（收窄真的生效）", _narrow)
        finally:
            shutil.rmtree(tmp8, ignore_errors=True)
        # Y9 劇本路線（與 GM／動作同一條路）
        n4 = len(t.events)
        evs = scenario.effects_apply(t, [{"item_grant": {"id": "mana_drop", "count": 2}},
                                         {"item_take": {"id": "mana_drop", "count": 1}}],
                                     {"me": {"seat": "p1"}}, by="script")
        check("Y9 劇本的 item_grant／item_take 走同一條路（淨 +1）",
              t.items_of("p1").get("mana_drop") == 1 and len(evs) == 2,
              f"{t.items_of('p1')}")
        check("Y10 用過道具的流回放一致", bool(t.replay_check().get("replay_matches")),
              f"{t.replay_check().get('diff')}")
        # Y11 打怪物（2026-10-21：`星屑の爆弾` 是「割王的硬度」的道具，不能只丟得到隊友）
        nid = str((t.state["seats"]["p1"] or {}).get("node") or "here")
        uid = f"{nid}:mob_king:0"
        t.state.setdefault("board", {}).setdefault("monsters", {})[nid] = [{
            "uid": uid, "id": "mob_king", "name_jp": "塔の主",
            "dc": 17, "check": "agi", "coins": 5, "exp": 10, "damage_die": 6,
            "hp": 20, "max_hp": 20, "active": False, "boss": True}]
        t.item_add(target="p1", id="hoshikuzu_bakudan")
        n5 = len(t.events)
        r = t.use_item(seat="p1", item="hoshikuzu_bakudan", target=uid)
        _dm = [e for e in t.events[n5:] if e.get("type") == "monster_damaged"]
        check("Y11a 道具打怪物 ⇒ 發 monster_damaged，回傳傷害與剩餘 HP",
              bool(_dm) and r.get("monster") == uid and int(r.get("monster_damage") or 0) >= 1
              and 0 <= int(r.get("monster_hp")) < 20,
              json.dumps(_dm[-1], ensure_ascii=False)[:100] if _dm else "沒有事件")
        check("Y11b 道具真的被扣掉（用完 = 0）",
              int(t.items_of("p1").get("hoshikuzu_bakudan", 0)) == 0,
              str(t.items_of("p1").get("hoshikuzu_bakudan")))
        t.item_add(target="p1", id="hoshikuzu_bakudan")
        n6 = len(t.events)
        check("Y11c 目標不存在 ⇒ 叫，且**不扣**道具（不留幽靈消耗）",
              _raises(lambda: t.use_item(seat="p1", item="hoshikuzu_bakudan",
                                         target=f"{nid}:no_such:9"), items.ItemError)
              and int(t.items_of("p1").get("hoshikuzu_bakudan", 0)) == 1
              and not [e for e in t.events[n6:] if e.get("type") == "item_changed"],
              str(t.items_of("p1").get("hoshikuzu_bakudan")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
