#!/usr/bin/env python3
"""**技能／法術的自測**（M3，2026-10-07）——技能表（`packs/rules.core/tables/skills.json`）＋ `cast`。

    python3 test_skills.py

九組：
  K1 **表**：技能表載得動（8 條）；schema 壞（缺 id／attr、cost 沒 resource、id 重複、target 亂寫）當場叫。
  K2 **沒啟用＝legacy**：`cast` 不在動作目錄、`cast()` 當場叫、投影沒有 skills 欄位、事件流零變更。
  K3 **cast 成功**：`skill_used` 進事件流（公開）、傷害真的進 `damage` 事件、消耗扣掉。
  K4 **檢定算式**：與 `check` 同一條（屬性×mult ＋ 熟練 ＋ 條件修正）；屬性加值也吃。
  K5 **消耗不足**：當場拒、**不擲骰、不留事件、不扣資源**。
  K6 **等級需求**：`lvl 2` 的技能在 1 級被拒；升到 2 級後可放。
  K7 **條件型技能**：`condition_set` 生效；沒啟用帶條件表的包 ⇒ **載入時**就叫。
  K8 **收窄**：沒啟用宣告 `damage` 的包 ⇒ 劇本用它**載入時**就叫。
  K9 **事件溯源**：cast 過的流 `op=replay` 一致。
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
CAMP = layout.must_locate_scenario(HERE, "hyakki_tower", "sannenme_no_onibarai")
DEF = layout.def_dir_of(CAMP)          # 定義層（WS-B：桌目錄裡只剩 config／runs）

from trpg.rules import packs as packs                                                             # noqa: E402
from trpg.rules import scenario as scenario                                                          # noqa: E402
MODF = scenario.module_filename(DEF) or "scenario.json"   # 劇本規則資料的檔名（WS-B：新名優先，別硬編）
from trpg.rules import skills as skills                                                            # noqa: E402
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


def mk_home(*, spec=SPEC, tables=None):
    """臨時劇本目錄（照抄現役 JSON；`tables` 給定時覆寫技能表的路徑）。"""
    tmp = tempfile.mkdtemp(prefix="trpg_skill_")
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


def seat_up(t, sid="p1", name="カガリ", attrs=None):
    return t.join(seat=sid, name=name, kind="human",
                  attrs=attrs or {"str": 3, "agi": 3, "wil": 3, "luck": 2}, order=0)


def last(t, etype):
    for ev in reversed(t.events):
        if ev.get("type") == etype:
            return ev
    return {}


def main() -> int:                                                        # noqa: C901
    print("== K1. 技能表 ==")
    tmp1, _ = mk_home()
    try:
        t = mk_table(tmp1, {**json.load(open(os.path.join(tmp1, "config.json"),
                                             encoding="utf-8")), "port": 0,
                            "packs": {"enabled": SPEC}})
        ids = t.skill_ids()
        check("K1a 技能表載得動（26 條；`rules.core`，含兩條協力技）", len(ids) == 26, f"{len(ids)}：{ids}")
        e = t.skill("kagaribi")
        check("K1b 條目欄位齊（屬性／熟練／消耗／難度／效果）",
              e.get("attr") == "wil" and int(e.get("prof") or 0) == 2
              and (e.get("cost") or {}).get("resource") == "mana"
              and any("condition_set" in x for x in (e.get("effects") or [])), f"{e.get('id')}")
        bad = os.path.join(tmp1, "bad_skills.json")
        with open(bad, "w", encoding="utf-8") as fh:
            json.dump({"kind": "skills", "rows": [{"id": "x", "attr": "str"},
                                                  {"id": "x", "attr": "str"}]}, fh)
        check("K1c schema 壞（id 重複）⇒ 載入時叫",
              _raises(lambda: skills.load([bad]), skills.SkillError))
        with open(bad, "w", encoding="utf-8") as fh:
            json.dump({"kind": "skills", "rows": [{"id": "y", "attr": "str",
                                                   "cost": {"value": 1}}]}, fh)
        check("K1d cost 沒 resource ⇒ 載入時叫",
              _raises(lambda: skills.load([bad]), skills.SkillError))
    finally:
        shutil.rmtree(tmp1, ignore_errors=True)

    print("== K2. 沒啟用＝legacy ==")
    tmp2, cfg2 = mk_home(spec=["d20core"])       # 只有底層規則：沒有技能表／等級表
    try:
        t = mk_table(tmp2, cfg2)
        # ⚠ 動作目錄＝`config.rules.actions` ∪ 包宣告的動作：現役 config 已把 `cast` 列進去了
        #   （2026-10-07 裝規則書時寫的），所以這裡**不看目錄**，只看「機制有沒有開」——
        #   沒啟用帶技能表的包 ⇒ `cast()` 當場叫、投影沒有 skills、零事件（K2b／K2c）。
        check("K2a 沒啟用帶技能表的包：cast() 當場叫（機制沒開，不是靜默沒反應）",
              _raises(lambda: t.cast(seat="p1", skill="kagaribi"), Exception))
        s = seat_up(t)
        v = t.view(seat=s)
        check("K2b 投影沒有 skills／level 欄位（legacy 逐字不變）",
              "skills" not in json.dumps(v) and "level" not in (v.get("you") or {}))
        check("K2c join 沒有多發事件（沒有 level_up）",
              not any(e.get("type") == "level_up" for e in t.events))
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)

    print("== K3/K4. cast 成功 ＋ 檢定算式 ==")
    tmp3, cfg3 = mk_home()
    try:
        t = mk_table(tmp3, cfg3)
        seat_up(t, "p1", "カガリ", {"str": 4, "agi": 3, "wil": 3, "luck": 2})
        seat_up(t, "p2", "シズク", {"str": 3, "agi": 3, "wil": 3, "luck": 2})
        t.add_resource(target="p1", name="mana", value=3, mode="set", by="gm")
        hp0 = t.state["seats"]["p2"]["hp"]
        r = t.cast(seat="p1", skill="hoshikuzu_giri", target="p2")
        ev = last(t, "skill_used")
        check("K3a skill_used 進事件流（公開、帶技能／目標／成敗）",
              ev.get("public") is True and ev.get("skill") == "hoshikuzu_giri"
              and ev.get("target") == "p2" and ev.get("seat") == "p1", f"{ev}")
        check("K3b 傷害走引擎（`damage` 事件 ＋ HP 真的掉）",
              r.get("success") and t.state["seats"]["p2"]["hp"] < hp0
              and last(t, "damage").get("target") == "p2",
              f"{hp0} -> {t.state['seats']['p2']['hp']}")
        check("K3c 消耗扣掉（mana 3 → 2）", t.resources_of("p1").get("mana") == 2,
              f"{t.resources_of('p1')}")
        # ★ 2026-10-21 動作收束：對外只宣告 `use`（`cast`／`use_item` 是同義呼叫）⇒
        #   `use(skill=…)` 必須走同一條技能鏈，不能退化成「泛用使用」的 note（這裡踩過一次）
        t.add_resource(target="p1", name="mana", value=3, mode="set", by="gm")
        hp_before = t.state["seats"]["p2"]["hp"]
        ru = t.act(seat="p1", action="use", skill="hoshikuzu_giri", target="p2")
        check("K3d `use(skill=…)` 走的是 cast 那條鏈（有骰、有消耗、有傷害）",
              bool(ru.get("roll")) and bool(ru.get("skill"))
              and last(t, "skill_used").get("skill") == "hoshikuzu_giri"
              and t.resources_of("p1").get("mana") == 2,
              f"mana={t.resources_of('p1').get('mana')} hp={hp_before}->{t.state['seats']['p2']['hp']}")
        want_mod = (int(t.attrs_of("p1")["str"]) * int(t.rules["check"]["attr_multiplier"])
                    + 2 + t.conditions_mod("p1", "check"))
        check("K4a 檢定 mod＝屬性×mult＋熟練＋條件修正（與 check 同一條算式）",
              (r.get("roll") or {}).get("mod") == want_mod,
              f"{(r.get('roll') or {}).get('mod')} vs {want_mod}")
        t.set_attr_mod(target="p1", attr="str", value=2, by="gm")
        r2 = t.cast(seat="p1", skill="hoshikuzu_giri", target="p2")
        check("K4b 屬性加值（M2 原語）也吃進技能檢定",
              (r2.get("roll") or {}).get("mod") == want_mod + 2 * int(t.rules["check"]["attr_multiplier"]),
              f"{(r2.get('roll') or {}).get('mod')}")
    finally:
        shutil.rmtree(tmp3, ignore_errors=True)

    print("== K5/K6. 消耗不足 ＋ 等級需求 ==")
    tmp5, cfg5 = mk_home()
    try:
        t = mk_table(tmp5, cfg5)
        seat_up(t, "p1")
        seat_up(t, "p2")
        t.add_resource(target="p1", name="mana", value=0, mode="set", by="gm")   # 先把 mana 歸零
        n0 = len(t.events)
        err = ""
        try:
            t.cast(seat="p1", skill="hoshikuzu_giri", target="p2")   # mana 0
        except Exception as exc:                                       # noqa: BLE001
            err = str(exc)
        check("K5a 消耗不足 ⇒ 當場拒（訊息帶資源與數量）",
              "mana" in err and "足" in err, err[:80])
        check("K5b 拒掉不留幽靈事件／不扣資源",
              len(t.events) == n0 and t.resources_of("p1").get("mana", 0) == 0,
              f"{len(t.events)} vs {n0}")
        err2 = ""
        try:
            t.cast(seat="p1", skill="hyousou_no_sho", target="p2")     # lvl 2
        except Exception as exc:                                       # noqa: BLE001
            err2 = str(exc)
        check("K6a 等級不夠 ⇒ 當場拒（說要幾級）", "2 級" in err2, err2[:80])
        t.exp_gain(seat="p1", amount=30, reason="test", by="gm")
        t.add_resource(target="p1", name="mana", value=5, mode="set", by="gm")
        t.add_resource(target="p1", name="mana", value=-5, mode="add", by="gm")   # 保持有值可控
        t.add_resource(target="p1", name="mana", value=2, mode="add", by="gm")
        r = t.cast(seat="p1", skill="hyousou_no_sho", target="p2")
        check("K6b 升到 2 級之後放得出來（並掛上 chill）",
              bool(r.get("success")) and "chill" in (t.state.get("conditions") or {}).get("p2", {}),
              f"{(t.state.get('conditions') or {}).get('p2')}")
    finally:
        shutil.rmtree(tmp5, ignore_errors=True)

    print("== K7. 條件型技能需要條件表 ==")
    tmp7, cfg7 = mk_home(spec=["d20core", "rules.core"])
    try:
        check("K7 技能表用 condition_set 但沒啟用帶條件表的包 ⇒ 載入時叫",
              _raises(lambda: mk_table(tmp7, cfg7), skills.SkillError))
    finally:
        shutil.rmtree(tmp7, ignore_errors=True)

    print("== K8. 收窄（effect 池）==")
    tmp8, _ = mk_home(spec=None)
    try:
        with open(os.path.join(tmp8, MODF), "w", encoding="utf-8") as fh:
            json.dump({"meta": {"id": "t"}, "triggers": [
                {"id": "x", "on": "say",
                 "then": [{"damage": {"seat": "p1", "die": 4, "bonus": 1}}]}]}, fh)
        err = ""
        try:
            scenario.Scenario.load(tmp8, allowed_effects=["emit", "fact_set"])
        except scenario.ModuleError as exc:
            err = str(exc)
        check("K8 沒啟用宣告 `damage` 的包 ⇒ 劇本載入時就叫（收窄真的生效）",
              "damage" in err and "機制包" in err, err.splitlines()[-1][:80])
    finally:
        shutil.rmtree(tmp8, ignore_errors=True)

    print("== K9. 事件溯源 ==")
    tmp9, cfg9 = mk_home()
    try:
        t = mk_table(tmp9, cfg9)
        seat_up(t, "p1")
        seat_up(t, "p2")
        t.add_resource(target="p1", name="mana", value=3, mode="set", by="gm")
        t.cast(seat="p1", skill="kagaribi", target="p2")
        rp = t.replay_check()
        check("K9 cast（含技能傷害／消耗／條件）過的流 op=replay 一致",
              bool(rp.get("replay_matches")), f"{rp.get('diff')}")
    finally:
        shutil.rmtree(tmp9, ignore_errors=True)

    print("== K10 協力技 ally_bonus（同節點每多一位隊友 ＋2 傷害）==")
    tmpA, cfgA = mk_home(spec=SPEC)
    try:
        tA = mk_table(tmpA, cfgA)
        tA.join(seat="p1", name="p1", kind="human")
        tA.join(seat="p2", name="p2", kind="human")
        tA.join(seat="p3", name="p3", kind="human")
        # 這一檔的夾具沒有棋盤 ⇒ node 是 None；協力技要真的「站在一起」，所以自己給一個節點
        for _s in ("p1", "p2", "p3"):
            tA.state["seats"][_s]["node"] = "n_test"
        node = "n_test"
        tA.add_resource(target="p1", name="mana", value=9, mode="set", by="gm")
        def _dmg_mod() -> int:
            """傷害那一筆 roll 的 `mod`（＝bonus，含 ally_bonus）——檢定那筆不算。"""
            for e in reversed(tA.events):
                if e.get("type") == "roll" and e.get("kind") == "damage" and e.get("seat") == "p1":
                    return int(e.get("mod") or 0)
            return -1

        rA = tA.cast(seat="p1", skill="kizuna_no_ichigeki", target="p2")
        modA = _dmg_mod()
        tA.state["seats"]["p3"]["node"] = "n_far"      # 一個人走開 ⇒ 只剩 1 位隊友
        tA.add_resource(target="p1", name="mana", value=9, mode="set", by="gm")
        rB = tA.cast(seat="p1", skill="kizuna_no_ichigeki", target="p2")
        modB = _dmg_mod()
        check("K10a 站在一起的隊友會讓協力技變重（傷害 bonus：2 位隊友 vs 1 位，差 2）",
              modA - modB == 2, f"傷害 mod A={modA}（2 隊友） B={modB}（1 隊友）")
        check("K10b 協力技走自己的 DC（不是 fight 的 DC 17）",
              int(tA.skill("kizuna_no_ichigeki").get("dc") or 0) == 12,
              str(tA.skill("kizuna_no_ichigeki").get("dc")))
    finally:
        shutil.rmtree(tmpA, ignore_errors=True)

    print("== K11 技能打怪（target 是怪物 uid ⇒ 走 monster_damaged／擊破）==")
    tmpM, cfgM = mk_home(spec=SPEC + ["board"])
    try:
        tM = mk_table(tmpM, cfgM)
        tM.join(seat="p1", name="p1", kind="human")
        nid = str((tM.state["seats"]["p1"] or {}).get("node") or "here")
        tM.state.setdefault("board", {}).setdefault("monsters", {})[nid] = [{
            "uid": f"{nid}:mob:0", "id": "mob", "name_jp": "小怪",
            "dc": 13, "check": "agi", "coins": 2, "exp": 5, "damage_die": 4,
            "hp": 3, "max_hp": 3, "active": False, "boss": False}]
        tM.state["board"].setdefault("mobs", {})[nid] = 1
        tM.add_resource(target="p1", name="mana", value=9, mode="set", by="gm")
        r = tM.cast(seat="p1", skill="hoshikuzu_giri", target=f"{nid}:mob:0")
        dmg = [e for e in tM.events if e.get("type") == "monster_damaged"]
        check("K11a 技能打怪物會發 monster_damaged（不再「打不到」）", bool(dmg),
              json.dumps(dmg[-1], ensure_ascii=False)[:90] if dmg else "沒有事件")
        check("K11b 命中後怪物的 hp 真的掉了", bool(dmg) and int(dmg[-1].get("hp_after") or 0) < 3,
              f"3 -> {dmg[-1].get('hp_after') if dmg else '?'}")
        check("K11c 技能對怪物的檢定仍是技能自己的 DC（不是 fight 的）",
              int((r.get("roll") or {}).get("dc") or 0) == 12, str((r.get("roll") or {}).get("dc")))
    finally:
        shutil.rmtree(tmpM, ignore_errors=True)

    print("\n== K12 技能打怪物：混合效果不再半途拋錯（review R2；設計者 2026-10-24 定案 (a)）==")
    # 病根：`cast` 打怪時只把 `damage` 交給 `monster_damage`，`seat:"target"` 的其他效果
    #   （condition_set／attr_mod）仍丟給 `effects_apply` ⇒ 把怪的 uid 當席位解 ⇒ 拋錯。
    #   拋錯時傷害／法力／skill_used 都已落盤 ⇒ 玩家收到報錯、行動額度沒扣（可以重複出手）。
    # 定案 (a)：怪物沒有狀態／屬性修正機制 ⇒ 那些效果**跳過並在回執註明**；`seat:"me"` 照常。
    tmpX, cfgX = mk_home(spec=SPEC + ["board", "semic"])
    try:
        # semic 要 `config.semi` 那一段才會啟用（額度記帳要靠它；沒有它 = no-op）
        cfgX["semi"] = {"goals_file": "goals.json", "clues_file": "clues.json",
                        "seats_file": "seats.json"}
        tX = mk_table(tmpX, cfgX)
        tX.join(seat="p1", name="p1", kind="human")
        nid = str((tX.state["seats"]["p1"] or {}).get("node") or "here")
        tX.state.setdefault("board", {}).setdefault("monsters", {})[nid] = [{
            "uid": f"{nid}:mob:0", "id": "mob", "name_jp": "小怪",
            "dc": 13, "check": "agi", "coins": 2, "exp": 5, "damage_die": 4,
            "hp": 30, "max_hp": 30, "active": False, "boss": False}]
        tX.state["board"].setdefault("mobs", {})[nid] = 1
        tX.add_resource(target="p1", name="mana", value=20, mode="set", by="gm")
        mon_uid = f"{nid}:mob:0"
        check("K12 前提：semic 真的啟用了（額度記帳那一組才有意義）",
              bool(getattr(tX, "semic_enabled", False)), str(bool(getattr(tX, "semi_cfg", {}))))

        # ── K12a-c：kagaribi（damage ＋ condition_set(seat:target)）
        n0 = len(tX.events)
        r, err = None, None
        try:
            r = tX.cast(seat="p1", skill="kagaribi", target=mon_uid, dc=1)
        except Exception as exc:                                          # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"
        types = [e.get("type") for e in tX.events[n0:]]
        check("K12a 打怪**不再拋錯**（修前：ConditionError 沒有這個席位：'<怪 uid>'）",
              err is None, err or "")
        dmg = [e for e in tX.events[n0:] if e.get("type") == "monster_damaged"]
        check("K12b 傷害照樣落地（monster_damaged 一筆、hp_after 掉）",
              len(dmg) == 1 and int(dmg[0].get("hp_after") or 999) < 30,
              f"30 -> {dmg[0].get('hp_after') if dmg else '?'}")
        check("K12c 條件**沒有**套到怪身上，回執明說「對怪物無效」",
              bool(r) and "condition_set" not in types
              and "condition_set" in (r.get("skipped") or []) and bool(r.get("skipped_note")),
              f"skipped={r.get('skipped') if r else '?'} events={types}")

        # ── K12d：attr_mod（kageotoshi）——同一條路徑的另一種效果（等級需求壓成 1 才放得出來）
        _lvl = tX.skills_table["kageotoshi"].get("lvl")
        tX.skills_table["kageotoshi"]["lvl"] = 1
        n1 = len(tX.events)
        r2, err2 = None, None
        try:
            r2 = tX.cast(seat="p1", skill="kageotoshi", target=mon_uid, dc=1)
        except Exception as exc:                                          # noqa: BLE001
            err2 = f"{type(exc).__name__}: {exc}"
        finally:
            tX.skills_table["kageotoshi"]["lvl"] = _lvl
        types2 = [e.get("type") for e in tX.events[n1:]]
        check("K12d attr_mod 型技能打怪同樣不拋錯，且 attr_mod 被跳過、傷害照打",
              err2 is None and "attr_mod" not in types2
              and "attr_mod" in (r2.get("skipped") or [])
              and any(e.get("type") == "monster_damaged" for e in tX.events[n1:]),
              err2 or f"skipped={r2.get('skipped')} events={types2}")

        # ── K12e：`seat:"me"` 的效果要照常成立（送り火的 heal me 打怪 ⇒ 怪掉血＋自己回血）
        _eff0 = tX.skills_table["kagaribi"]["effects"]
        tX.skills_table["kagaribi"]["effects"] = [
            {"damage": {"seat": "target", "die": 4, "attr": "wil", "bonus": 0}},
            {"heal": {"seat": "me", "die": 4, "bonus": 0}},
        ]
        tX.state["seats"]["p1"]["hp"] = 3
        hp0 = int(tX.state["seats"]["p1"]["hp"])
        n2 = len(tX.events)
        r3, err3 = None, None
        try:
            r3 = tX.cast(seat="p1", skill="kagaribi", target=mon_uid, dc=1)
        except Exception as exc:                                          # noqa: BLE001
            err3 = f"{type(exc).__name__}: {exc}"
        finally:
            tX.skills_table["kagaribi"]["effects"] = _eff0
        hp1 = int(tX.state["seats"]["p1"]["hp"])
        check("K12e `seat:\"me\"` 的 heal 打怪時照常對施法者生效（不因目標是怪就被丟）",
              err3 is None and hp1 > hp0
              and any(e.get("type") == "monster_damaged" for e in tX.events[n2:]),
              err3 or f"自己 hp {hp0} -> {hp1}")

        # ── K12f：走 `act(action="use")` —— 額度要照扣、回合照走（修前：拋錯 ⇒ 沒扣 ⇒ 能重複出手）
        tX.add_resource(target="p1", name="mana", value=20, mode="set", by="gm")
        tX.start_turn(seat="p1")                 # 回合制下 `act` 要有進行中的回合
        before = int((tX.state.get("semi") or {}).get("counters", {}).get("p1", {}).get("act", 0))
        n3 = len(tX.events)
        ra, err4 = None, None
        try:
            ra = tX.act(seat="p1", action="use", skill="kagaribi", target=mon_uid, dc=1)
        except Exception as exc:                                          # noqa: BLE001
            err4 = f"{type(exc).__name__}: {exc}"
        after = int((tX.state.get("semi") or {}).get("counters", {}).get("p1", {}).get("act", 0))
        _ev = [e for e in tX.events[n3:]]
        check("K12f `act(action=\"use\")` 打怪不拋錯、額度照扣 1、留一手 acted",
              err4 is None and after == before + 1
              and any(e.get("type") == "acted" for e in _ev)
              and any(e.get("type") == "monster_damaged" for e in _ev),
              err4 or f"act 額度 {before} -> {after}；events={[e.get('type') for e in _ev]}")
        check("K12g 宣告沒有被回收成 rejected（成功的一手不該留下 rejected 標記）",
              not any(e.get("type") == "action_declared" and e.get("rejected") for e in _ev),
              str([e.get("type") for e in _ev]))
    finally:
        shutil.rmtree(tmpX, ignore_errors=True)

    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
