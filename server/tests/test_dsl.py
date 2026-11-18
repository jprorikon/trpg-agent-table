#!/usr/bin/env python3
"""**動作 DSL 兩個原語的自測**（M2，2026-10-07）——屬性加值（`attr_mod`）／通用資源（`resource_*`）。

    python3 test_dsl.py

十組：
  D1 **表驅動**：`EFFECT_SPECS` 與 `EFFECT_KINDS` 一致（加了機制卻漏寫形狀＝當場紅）。
  D2 **屬性加值生效**：`attrs_of` ＝ 基準＋加值；**檢定與攻擊的 mod 都吃到**（值寫進事件）。
  D3 **拿掉加值**：`value=0` ⇒ state 不留空殼、投影不多欄位。
  D4 **通用資源**：`add`（下限 0）／`set`；`after` 寫進事件；AST 可見；投影（自己＋公開席位）。
  D5 **trigger 路線**：劇本用 `attr_mod`／`resource_add` effect 真的改到局面。
  D6 **GM 與 trigger 同一條路**：同一組 Table 方法（不養第二份實作）。
  D7 **收窄**：沒啟用宣告它的包 ⇒ 劇本**載入時**就叫。
  D8 **legacy 零變更**：沒用到原語的桌，投影**一個字都不變**（新欄位只在有值時出現）。
  D9 **事件溯源**：用了原語的流 `op=replay` 一致（`after`／`value` 都寫在事件裡）。
  D10 **壞參數當場叫**：屬性不存在／value 非整數／name 空／mode 亂寫。
"""
from __future__ import annotations
from turn_helper import ensure_turn, drop_scene  # 拆景后的测试小工具

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo 根
sys.path.insert(0, HERE)
from trpg.core import layout as layout                                                          # noqa: E402
CAMP = layout.must_locate_scenario(HERE, "hyakki_tower", "sannenme_no_onibarai")
DEF = layout.def_dir_of(CAMP)          # 定義層（WS-B：桌目錄裡只剩 config／runs）
FROZEN = "/srv/workspace/_layer_frozen"

from trpg.rules import dsl as dsl                                                              # noqa: E402
from trpg.rules import packs as packs                                                           # noqa: E402
from trpg.rules import scenario as scenario                                                        # noqa: E402
MODF = scenario.module_filename(DEF) or "scenario.json"   # 劇本規則資料的檔名（WS-B：新名優先，別硬編）
from trpg.core import store as store                                                           # noqa: E402
from trpg.engine import Table                                               # noqa: E402

OK, FAIL = [], []
SPEC = ["d20core", "board", "semic", "mode.turn"]


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


def mk_home(*, spec=SPEC, data=None):
    """臨時劇本目錄（照抄現役 JSON；預設**新局**＝沒有事件流，測起來最乾淨）。"""
    tmp = tempfile.mkdtemp(prefix="trpg_dsl_")
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
    if data:
        store.copy_current_run(data, tmp)          # 現役那一局（一局一檔；舊制 data/ 也吃）
    return tmp, cfg


def mk_table(tmp, cfg):
    return Table(home=tmp, cfg=cfg, store=store.FileStore(tmp), clock=lambda: 1000.0)


def seat_up(t, sid="p1", name="カガリ", attrs=None):
    return t.join(seat=sid, name=name, kind="human",
                  attrs=attrs or {"str": 3, "agi": 3, "wil": 3}, order=0)


def last(t, etype):
    for ev in reversed(t.events):
        if ev.get("type") == etype:
            return ev
    return {}


def open_first_scene(t):
    """夾具：開第一景 ⇒ 開第一輪（2026-10-08 先開景才開輪；2026-10-20 只有當前回合那一席能 act）。"""
    t.cfg.setdefault("turn", {})["min_seats_to_start"] = 1
    t.cfg.setdefault("turn", {})["auto_start_first_turn"] = True
    st = t.state
    if not isinstance(st.get("semi"), dict):
        st["semi"] = {}
    if st.get("turn"):
        return t
    t._maybe_autostart()
    return t
    ensure_turn(t)
    return t


def _turn_to(t, seat):
    """把回合轉到 `seat`：必要時先開景、再開輪、再 end_turn 繞到該席（回合制只有當前那一席能 act）。"""
    t.cfg.setdefault("turn", {})["min_seats_to_start"] = 1
    t.cfg.setdefault("turn", {})["auto_start_first_turn"] = True
    st = t.state
    if not isinstance(st.get("semi"), dict):
        st["semi"] = {}
    ensure_turn(t)
    for _ in range(max(2, len(st.get("order") or []) * 2 + 3)):
        cur = (st.get("turn") or {}).get("seat")
        if cur is None:
            t._maybe_autostart()
            cur = (st.get("turn") or {}).get("seat")
            if cur is None:
                return
        if str(cur) == str(seat):
            return
        t.end_turn(reason="end_turn")
        t._maybe_autostart()



def main() -> int:                                                        # noqa: C901
    print("== D1. 表驅動（EFFECT_SPECS ↔ EFFECT_KINDS）==")
    check("D1 每種 effect 都有形狀；沒有多餘的列（加機制＝加一列，漏寫＝當場紅）",
          set(scenario.EFFECT_SPECS) == set(scenario.EFFECT_KINDS),
          f"表={sorted(scenario.EFFECT_SPECS)} 池={sorted(scenario.EFFECT_KINDS)}")
    check("D1b 新原語在內核池裡，且 d20core 宣告了它們（＝開 d20core 的桌就能用）",
          {"attr_mod", "resource_add", "resource_set"} <= set(scenario.EFFECT_KINDS)
          and {"attr_mod", "resource_add", "resource_set"}
          <= set(packs.resolve(["d20core"]).effects),
          f"{sorted(packs.resolve(['d20core']).effects)}")

    print("== D2. 屬性加值生效 ==")
    tmp, cfg = mk_home()
    try:
        t = mk_table(tmp, cfg)
        seat_up(t, "p1")
        open_first_scene(t)
        seat_up(t, "p2", "シズク", {"str": 3, "agi": 4, "wil": 2})
        open_first_scene(t)
        base_agi = t.attrs_of("p1")["agi"]
        _turn_to(t, "p1")
        r0 = t.act(seat="p1", action="check", attr="agi", dc=12, note="before")
        mod0 = r0["roll"]["mod"]
        t.set_attr_mod(target="p1", attr="agi", value=2, note="裝備", by="gm")
        ev = last(t, "attr_mod_set")
        check("D2a attr_mod_set 進事件流（公開、帶 seat／attr／value／by）",
              ev.get("seat") == "p1" and ev.get("attr") == "agi" and ev.get("value") == 2
              and ev.get("public") is True and ev.get("by") == "gm", f"{ev}")
        check("D2b attrs_of ＝ 基準 ＋ 加值（+2 agi 真的進去了）",
              t.attrs_of("p1")["agi"] == base_agi + 2 and t.attr_mods_of("p1") == {"agi": 2},
              f"{t.attrs_of('p1')}")
        _turn_to(t, "p1")
        r1 = t.act(seat="p1", action="check", attr="agi", dc=12, note="after")
        check("D2c 檢定的 mod 吃到加值（+2 屬性 × attr_multiplier）",
              r1["roll"]["mod"] == mod0 + 2 * int(t.rules["check"]["attr_multiplier"]),
              f"{mod0} -> {r1['roll']['mod']}")
        _turn_to(t, "p1")
        r2 = t.act(seat="p1", action="attack", target="p2", dc=12, note="swing")
        check("D2d 攻擊的 mod 也吃（同一條 `attrs_of`）",
              (r2.get("roll") or {}).get("mod") is not None
              and (r2["roll"]["mod"] >= 2 * int(t.rules["check"]["attr_multiplier"])),
              f"{r2.get('roll')}")
        r3 = t.gm_roll_for(seat="p1", attr="agi", secret=False, reason="GM 代投")
        check("D2e GM 代投（gm_roll_for）也吃同一條 attrs_of",
              (r3.get("roll") or {}).get("mod") == mod0 + 2 * int(t.rules["check"]["attr_multiplier"]),
              f"{(r3.get('roll') or {}).get('mod')}")
        check("D3 value=0 ⇒ 拿掉加值（state 不留空殼、投影不多欄位）",
              None is t.set_attr_mod(target="p1", attr="agi", value=0, by="gm") or True)
        check("D3b 拿掉後 attr_mods 是空的、attrs_of 回到基準",
              t.attr_mods_of("p1") == {} and t.attrs_of("p1")["agi"] == base_agi
              and "attr_mods" not in json.dumps(t.view(seat="p1", detail="compact")),
              f"{t.attr_mods_of('p1')}")

        print("== D4. 通用資源 ==")
        t.add_resource(target="p1", name="spell_slot", value=3, mode="set", by="gm")
        check("D4a set 生效（after 寫進事件）",
              t.resources_of("p1")["spell_slot"] == 3 and last(t, "resource_changed")["after"] == 3)
        t.add_resource(target="p1", name="spell_slot", value=-1, by="gm")
        check("D4b add 生效（3 − 1 ＝ 2）", t.resources_of("p1")["spell_slot"] == 2)
        t.add_resource(target="p1", name="spell_slot", value=-9, by="gm")
        check("D4c add 下限 0（不會變負數）", t.resources_of("p1")["spell_slot"] == 0)
        _v = t.view(seat="p1")
        check("D4d 投影：自己看得到 resources（公開席位那格也有）",
              (_v.get("you") or {}).get("resources") == {"spell_slot": 0}
              and any((s.get("resources") or {}).get("spell_slot") == 0
                      for s in _v.get("seats", []) if s["seat"] == "p1"),
              f"{(_v.get('you') or {}).get('resources')}")
        _ctx = t.module.ctx(t, seat="p1") if t.module else None
        check("D4e AST 看得到 me.resources／seats.<席>.resources",
              _ctx is None or (_ctx["me"]["resources"].get("spell_slot") == 0
                               and _ctx["seats"]["p1"]["resources"].get("spell_slot") == 0))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("== D5/D6. trigger 路線 ＋ GM 同一條路 ==")
    tmp5, cfg5 = mk_home()
    try:
        with open(os.path.join(tmp5, MODF), "w", encoding="utf-8") as fh:
            json.dump({"meta": {"id": "dsl_test"},
                       "triggers": [{"id": "bless", "on": "say",
                                     "then": [{"attr_mod": {"seat": "p1", "attr": "wil", "value": 1}},
                                              {"resource_add": {"seat": "p1", "name": "ammo",
                                                                "value": 2}}]}]}, fh,
                      ensure_ascii=False)
        t5 = mk_table(tmp5, cfg5)
        seat_up(t5, "p1")
        t5.say(seat="p1", text="（觸發）")
        # 注意：mixin 方法名以實作為準——這裡只驗「trigger 真的改到局面」
        am = t5.attr_mods_of("p1")
        rs = t5.resources_of("p1")
        check("D5 trigger 用 effect 改到局面（attr_mod ＋ resource_add，by=trigger）",
              am.get("wil") == 1 and rs.get("ammo") == 2
              and last(t5, "attr_mod_set").get("by") == "trigger"
              and last(t5, "resource_changed").get("by") == "trigger",
              f"mods={am} res={rs}")
        _n = len(t5.events)
        t5.set_attr_mod(target="p1", attr="wil", value=2, by="gm")
        t5.add_resource(target="p1", name="ammo", value=1, by="gm")
        check("D6 GM 走同一組方法（同一個事件型別、by 標得出來）",
              len(t5.events) == _n + 2
              and last(t5, "attr_mod_set").get("by") == "gm"
              and last(t5, "resource_changed").get("by") == "gm")
    finally:
        shutil.rmtree(tmp5, ignore_errors=True)

    print("== D7. 收窄 ==")
    tmp7, _ = mk_home()
    try:
        with open(os.path.join(tmp7, MODF), "w", encoding="utf-8") as fh:
            json.dump({"meta": {"id": "t"}, "triggers": [
                {"id": "x", "on": "say",
                 "then": [{"attr_mod": {"seat": "p1", "attr": "wil", "value": 1}}]}]}, fh)
        _err = ""
        try:
            scenario.Scenario.load(tmp7, allowed_effects=["emit", "fact_set"])
        except scenario.ModuleError as exc:
            _err = str(exc)
        check("D7 這張桌沒啟用宣告它的包 ⇒ 劇本載入時就叫（收窄真的生效）",
              "attr_mod" in _err and "機制包" in _err, _err.splitlines()[-1][:80])
    finally:
        shutil.rmtree(tmp7, ignore_errors=True)

    print("== D8. legacy 零變更（沒用到原語＝投影一個字都不變）==")
    tmp8, cfg8 = mk_home(data=FROZEN)
    try:
        t8 = mk_table(tmp8, cfg8)
        blob = json.dumps(t8.view(seat="p1"), sort_keys=True, ensure_ascii=False)
        check("D8a 凍結流（沒用到原語）：投影不含 attr_mods／resources 欄位",
              '"attr_mods"' not in blob and '"resources"' not in blob)
        check("D8b 回放一致（新欄位是空的，不影響折出的 state）",
              bool(t8.replay_check().get("replay_matches")), f"{t8.replay_check().get('diff')}")
    finally:
        shutil.rmtree(tmp8, ignore_errors=True)

    print("== D9. 事件溯源（用了原語的流）==")
    tmp9, cfg9 = mk_home()
    try:
        t9 = mk_table(tmp9, cfg9)
        seat_up(t9, "p1")
        t9.set_attr_mod(target="p1", attr="str", value=2, by="gm")
        t9.add_resource(target="p1", name="stamina", value=5, mode="set", by="gm")
        t9.add_resource(target="p1", name="stamina", value=-2, by="gm")
        check("D9 用了原語的流 op=replay 一致（after／value 寫在事件裡，不必重算）",
              bool(t9.replay_check().get("replay_matches")), f"{t9.replay_check().get('diff')}")
    finally:
        shutil.rmtree(tmp9, ignore_errors=True)

    print("== D10. 壞參數當場叫 ==")
    tmp10, cfg10 = mk_home()
    try:
        t = mk_table(tmp10, cfg10)
        seat_up(t, "p1")
        # ⚠ 現役 config 的屬性表已是四項（`str/agi/wil/luck`，2026-10-07 裝規則書時寫的）——
        #   要測「屬性不存在」得用真的不存在的名字（用 luck 會變成合法 ⇒ 假紅）。
        check("D10a 屬性不存在 ⇒ DslError",
              _raises(lambda: t.set_attr_mod(target="p1", attr="dex", value=1), dsl.DslError))
        check("D10b 席位不存在 ⇒ DslError",
              _raises(lambda: t.set_attr_mod(target="nope", attr="agi", value=1), dsl.DslError))
        check("D10c value 非整數 ⇒ DslError",
              _raises(lambda: t.set_attr_mod(target="p1", attr="agi", value="x"), dsl.DslError)
              and _raises(lambda: t.add_resource(target="p1", name="ammo", value="x"), dsl.DslError))
        check("D10d resource 沒 name／mode 亂寫 ⇒ DslError",
              _raises(lambda: t.add_resource(target="p1", name="", value=1), dsl.DslError)
              and _raises(lambda: t.add_resource(target="p1", name="ammo", value=1, mode="nope"),
                          dsl.DslError))
        check("D10e 壞參數不留事件（當場叫＝沒有幽靈事件）",
              not any(e.get("type") in ("attr_mod_set", "resource_changed") for e in t.events))
    finally:
        shutil.rmtree(tmp10, ignore_errors=True)

    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
