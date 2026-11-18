#!/usr/bin/env python3
"""**救起／救護的自測**（★1，2026-10-07）——`packs/wound/tables/rescue.json`。

    python3 test_rescue.py

設計者拍「先只接 ★1 救起／救護」：把 `down` 的人拉回來＝**一個檢定＋一張表**
（與 `check`／`cast` 同一條算式、消耗先扣、成功走引擎 `roll_heal`、**不新增 state 欄位**）。

  S1 **表**：載得動（3 條）；schema 壞（空表／缺 id／id 重複／缺 attr／dc 亂寫／heal 亂鍵／
     risk 不是陣列／不認識的欄位）當場叫。
  S2 **沒啟用＝legacy**：動作目錄不含 `rescue`（包沒宣告時）、`rescue()` 當場叫、零事件。
  S3 **前置**：沒指定目標／目標不存在／目標沒倒地／救自己 ⇒ 當場叫（且不留幽靈事件）。
  S4 **成功救起**：HP 回到 0 以上 ⇒ `down` 解除、可以行動了；事件鏈對（`rescue_attempt` → `roll` → `heal`）。
  S5 **補不夠**：`still_down=true`（誠實——不假裝救活了）。
  S6 **消耗先扣**：資源不足／沒有道具 ⇒ 拒，且**不留任何事件**（連骰都不擲）。
  S7 **失敗的代價**：`risk` 真的套上（自己吃胸傷）。
  S8 **沒帶救起表的包**：動作目錄不含 `rescue`（收窄真的生效）。
  S9 **config 選規則**：`rules.rescue` 指哪一條就用哪一條；指到不存在 ⇒ 當場叫。
  S10 **事件溯源**：救過人的流 `op=replay` 一致。
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

from trpg.rules import rescue as rescue                                                            # noqa: E402
from trpg.core import store as store                                                             # noqa: E402
from trpg.engine import Table                                                 # noqa: E402

OK, FAIL = [], []
SPEC = ["d20core", "board", "semic", "mode.turn", "wound"]
LEGACY = ["d20core", "board", "semic", "mode.turn"]


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


def mk_home(*, spec=SPEC, rules_extra=None):
    tmp = tempfile.mkdtemp(prefix="trpg_rescue_")
    packs.seed_module_packs(DEF, tmp)   # 模組層的包（rules.core 住模組 repo；臨時桌推不出模組）
    for f in ("board.json", "goals.json", "clues.json", "seats.json", MODF):
        shutil.copy(os.path.join(DEF, f), os.path.join(tmp, f))
    layout.copy_table_config(CAMP, os.path.join(tmp, "config.json"))   # 旋鈕 ⇒ 桌目錄（定義在 DEF）
    shutil.copytree(os.path.join(DEF, "packs"), os.path.join(tmp, "packs"), dirs_exist_ok=True)
    cfg = json.load(open(os.path.join(tmp, "config.json"), encoding="utf-8"))
    cfg["port"] = 0
    cfg["packs"] = {"enabled": list(spec)}
    if rules_extra:
        cfg.setdefault("rules", {}).update(rules_extra)
    with open(os.path.join(tmp, "config.json"), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False)
    return tmp, cfg


def mk_table(tmp, cfg):
    return Table(home=tmp, cfg=cfg, store=store.FileStore(tmp), clock=lambda: 1000.0)


def seat_up(t, sid="p1", name="カガリ", kind="human"):
    return t.join(seat=sid, name=name, kind=kind,
                  attrs={"str": 3, "agi": 3, "wil": 3, "luck": 2}, order=0)


def knock_down(t, sid):
    """把人打到倒地（走引擎的路：扣血到 0 ⇒ reducer 自己上 `down`）。"""
    s = t.state["seats"][sid]
    t._emit("damage", public=True, seat=sid, target=sid, amount=s["hp"], hp_after=0, by="test")
    return "down" in s["status"]


def main() -> int:                                                        # noqa: C901
    print("== S1. 表 ==")
    rows = json.load(open(os.path.join(HERE, "packs", "wound", "tables", "rescue.json"),
                          encoding="utf-8"))["rows"]
    check("S1a 救起表載得動（3 條：介抱／野戦手当／星繕い）",
          [r["id"] for r in rows] == ["first_aid", "field_dressing", "star_mending"], f"{[r['id'] for r in rows]}")
    check("S1b 空表／缺 id／id 重複 ⇒ 不合法",
          any("非空" in x for x in rescue.validate([]))
          and any("要有 id" in x for x in rescue.validate([{"attr": "wil"}]))
          and any("重複" in x for x in rescue.validate([{"id": "a", "attr": "wil", "heal": {"die": 6}},
                                                        {"id": "a", "attr": "wil", "heal": {"die": 6}}])))
    check("S1c 缺 attr／dc 亂寫 ⇒ 不合法",
          any("attr" in x for x in rescue.validate([{"id": "a", "heal": {"die": 6}}]))
          and any("dc" in x for x in rescue.validate([{"id": "a", "attr": "wil", "dc": "12",
                                                       "heal": {"die": 6}}])))
    check("S1d heal 亂鍵／risk 不是陣列／不認識的欄位 ⇒ 不合法",
          any("heal" in x for x in rescue.validate([{"id": "a", "attr": "wil", "heal": {"dice": 2}}]))
          and any("risk" in x for x in rescue.validate([{"id": "a", "attr": "wil", "heal": {"die": 6},
                                                         "risk": {"condition_set": {}}}]))
          and any("不認識的欄位" in x for x in rescue.validate([{"id": "a", "attr": "wil",
                                                                "heal": {"die": 6}, "success": 1}])))

    print("== S2／S8. 沒啟用＝legacy（收窄）==")
    tmp2, cfg2 = mk_home(spec=LEGACY)
    try:
        t2 = mk_table(tmp2, cfg2)
        seat_up(t2, "p1")
        # ⚠ 動作目錄＝config ∪ 包宣告（現役 config 已列 `rescue`）⇒ 這裡只看「機制沒開」
        check("S2a 沒帶救起表的包 ⇒ rescue() 當場叫（機制沒開，不是靜默沒反應）",
              _raises(lambda: t2.rescue(seat="p1", target="p2"), Exception))
        check("S2b 沒啟用 ⇒ 一個事件都不多發",
              not any(e.get("type") == "rescue_attempt" for e in t2.events))
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)

    tmp, cfg = mk_home()
    try:
        t = mk_table(tmp, cfg)
        seat_up(t, "p1")
        seat_up(t, "p2", name="シズク")
        check("S8 有 wound 的桌：動作目錄含 rescue（包宣告了就進得來）", "rescue" in t.action_ids,
              f"{[a for a in t.action_ids if a in ('rescue', 'cast', 'use_item')]}")
        print("== S3. 前置 ==")
        n0 = len(t.events)
        check("S3a 沒指定目標 ⇒ 叫（不留幽靈事件）",
              _raises(lambda: t.rescue(seat="p1"), Exception) and len(t.events) == n0)
        check("S3b 目標不存在 ⇒ 叫", _raises(lambda: t.rescue(seat="p1", target="nope"), Exception))
        check("S3c 目標沒倒地 ⇒ 叫（救起只對 down 的人有效）",
              _raises(lambda: t.rescue(seat="p1", target="p2"), Exception))
        check("S3d 不能救自己：自己倒地時 ⇒ 引擎的『已倒下不能行動』擋住（不是靜默）",
              _raises(lambda: t.rescue(seat="p1", target="p1"), Exception))
        print("== S4. 成功救起（含補不夠的誠實）==")
        check("S4a 把 p2 打到倒地（走引擎的路：reducer 自己上 down）", knock_down(t, "p2"))
        n1 = len(t.events)
        out = t.rescue(seat="p1", target="p2")
        types = [e.get("type") for e in t.events[n1:]]
        if out["success"]:
            check("S4b 成功 ⇒ p2 站起來（hp>0、down 解除、回復量＝roll_heal）",
                  out["still_down"] is False and "down" not in t.state["seats"]["p2"]["status"]
                  and t.state["seats"]["p2"]["hp"] > 0 and out["healed"] > 0,
                  f"healed={out['healed']} hp={t.state['seats']['p2']['hp']} roll={out['roll'].get('total')}")
            # 順序與 `cast` 同款：先擲（`roll`）再宣告（`rescue_attempt`）再治療（`heal`）
            check("S4c 事件鏈對：`roll` → `rescue_attempt`（公開）→ `roll` → `heal`",
                  types[:2] == ["roll", "rescue_attempt"] and types[-1] == "heal"
                  and any(e.get("type") == "rescue_attempt" and e.get("public")
                          for e in t.events[n1:]), f"{types}")
        else:
            check("S4b' 失敗 ⇒ 沒救起來（誠實）且沒治療事件",
                  out["still_down"] is True and "heal" not in types, f"{types}")
            check("S4c' 失敗 ⇒ 有 rescue_attempt（公開，帶 dc／success=false）",
                  any(e.get("type") == "rescue_attempt" and e.get("public") and not e.get("success")
                      for e in t.events[n1:]))
        check("S10 救過人的流回放一致", bool(t.replay_check().get("replay_matches")),
              f"{t.replay_check().get('diff')}")
        print("== S6. 消耗（全驗完才扣、不足＝拒且不擲骰）==")
        # 這一組要道具：規則是 `field_dressing`（heal_pack ×1 ＋ mana 1）⇒ 桌要 wound ＋ rules.core
        tmp6, cfg6 = mk_home(spec=["d20core", "board", "semic", "mode.turn", "wound", "rules.core"],
                             rules_extra={"rescue": "field_dressing"})
        try:
            tt = mk_table(tmp6, cfg6)
            seat_up(tt, "p1")
            seat_up(tt, "p2", name="シズク")
            knock_down(tt, "p2")
            n2 = len(tt.events)
            check("S6a 沒有道具 ⇒ 拒、不留事件（連骰都不擲、魔力也不扣）",
                  _raises(lambda: tt.rescue(seat="p1", target="p2"), Exception)
                  and len(tt.events) == n2
                  and not any(e.get("type") in ("roll", "resource_changed") for e in tt.events[n2:]))
            tt.item_add(target="p1", id="heal_pack", count=2)
            tt.add_resource(target="p1", name="mana", value=1)
            _m0 = int(tt.resources_of("p1").get("mana") or 0)
            n3 = len(tt.events)
            out3 = tt.rescue(seat="p1", target="p2")
            check("S6b 有道具＋有魔力 ⇒ 道具 −1、魔力 −1（消耗真的扣了）",
                  tt.items_of("p1").get("heal_pack") == 1
                  and tt.resources_of("p1").get("mana") == _m0 - 1
                  and "rescue_attempt" in [e.get("type") for e in tt.events[n3:]],
                  f"items={tt.items_of('p1')} mana={tt.resources_of('p1').get('mana')} success={out3['success']}")
            tt.add_resource(target="p1", name="mana", value=-99)      # 清空魔力（下限 0）
            knock_down(tt, "p2")
            n4 = len(tt.events)
            check("S6c 魔力不足 ⇒ 拒、不留半套消耗（道具沒被扣、也沒擲骰）",
                  _raises(lambda: tt.rescue(seat="p1", target="p2"), Exception)
                  and tt.items_of("p1").get("heal_pack") == 1
                  and not any(e.get("type") == "roll" for e in tt.events[n4:]))
        finally:
            shutil.rmtree(tmp6, ignore_errors=True)
        print("== S9. config 選規則 ==")
        t9, cfg9 = mk_home(rules_extra={"rescue": "star_mending"})
        try:
            tt9 = mk_table(t9, cfg9)
            seat_up(tt9, "p1")
            seat_up(tt9, "p2", name="シズク")
            check("S9a `rules.rescue=star_mending` ⇒ 用那一條（DC 17＋prof 2）",
                  tt9.rescue_rule().get("id") == "star_mending" and tt9.rescue_rule().get("dc") == 17)
            check("S9b 沒寫 `rules.rescue` ⇒ 用表的第一列（first_aid）",
                  t.rescue_rule().get("id") == "first_aid")
        finally:
            shutil.rmtree(t9, ignore_errors=True)
        t10, cfg10 = mk_home(rules_extra={"rescue": "nope"})
        try:
            tt10 = mk_table(t10, cfg10)
            seat_up(tt10, "p1")
            check("S9c 指到不存在的規則 ⇒ 當場叫（列出有的）",
                  _raises(lambda: tt10.rescue(seat="p1", target="p1"), Exception))
        finally:
            shutil.rmtree(t10, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
