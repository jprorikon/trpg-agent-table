#!/usr/bin/env python3
"""**條件（部位受傷／異常狀態）的自測**——第一個「真包」（`packs/wound/`）的閘。

    python3 test_conditions.py

八組：
  W1 **表**：`packs/wound` 載得動；schema 壞（未知 tick 鍵／負傷害／turns=0／句庫缺鍵／id 重複）當場叫。
  W2 **沒啟用＝legacy**：表是空的、`set_condition` 拒絕、投影／事件流零變更。
  W3 **投上**：`condition_set` 進事件流（公開）、`state["conditions"]`、`conditions_view` 的名稱與數值。
  W4 **判定修正**：`conditions_mod` 進 d20 的 mod（attack／check 都吃）。
  W5 **每回合結算**：`turn_started` 時扣血、倒數；到期自動解除（`condition_set value=false`）。
  W6 **治**：GM／trigger 把條件拿掉。
  W7 **骰式傷害可重現**：同 seed 同流＝同值，且值寫進事件（回放不必重算）。
  W8 **事件溯源**：有條件的流 `op=replay` 一致；投影只給該席位與 GM。
  W9 **effect 收窄＋trigger 路線**：沒啟用 wound 的表用 `condition_set` ⇒ 載入時就叫；啟用後 trigger 能投。
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

from trpg.rules import conditions as conditions                                                        # noqa: E402
from trpg.rules import packs as packs                                                             # noqa: E402
from trpg.rules import scenario as scenario                                                          # noqa: E402
MODF = scenario.module_filename(DEF) or "scenario.json"   # 劇本規則資料的檔名（WS-B：新名優先，別硬編）
from trpg.core import store as store                                                             # noqa: E402
from trpg.engine import Table                                                 # noqa: E402

OK, FAIL = [], []
WOUND = ["d20core", "wound"]
LEGACY = ["d20core"]


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


def mk_home(*, spec=WOUND, data=None):
    """臨時劇本目錄（照抄現役的 JSON 檔；預設**新局**＝不抄事件流，測起來最乾淨）。"""
    tmp = tempfile.mkdtemp(prefix="trpg_cond_")
    for f in ("board.json", "goals.json", "clues.json", "seats.json", MODF):
        shutil.copy(os.path.join(DEF, f), os.path.join(tmp, f))       # 定義 ⇒ 定義層
    layout.copy_table_config(CAMP, os.path.join(tmp, "config.json"))   # 旋鈕 ⇒ 桌目錄
    if data:
        store.copy_current_run(data, tmp)          # 現役那一局（一局一檔；舊制 data/ 也吃）
    cfg = json.load(open(os.path.join(tmp, "config.json"), encoding="utf-8"))
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


def seat_up(t, sid="p1", name="カガリ", kind="human"):
    t.join(seat=sid, name=name, kind=kind, attrs={"str": 3, "agi": 3, "wil": 3}, order=0)


def turn_once(t, seat="p1"):
    """跑一個完整回合（結算發生在 `turn_started`）。"""
    if t.state.get("turn"):
        t.end_turn(reason="test", start_next=False)
    return t.start_turn(seat=seat)


def open_first_scene(t):
    """夾具：把「第一景開著」擺好，再讓人齊了開第一輪。

    ⚠ 2026-10-08 起 `_maybe_autostart` 要求**先開景**才會開輪（以前人一齊就開 ⇒ 沒有景卻在倒數）。
    而 2026-10-20 起 `act()` 又要求「只有當前回合那一席能動、沒有回合就不准動」——
    所以測試若只 `join` 就直接 `act()`，會撞「現在沒有進行中的回合」。這裡把兩件事一次做對：
    開景（`semi.scene` 有 no、沒 closed_at）→ 讓引擎自己開輪。
    """
    # 現役 config 的 `turn.min_seats_to_start` 是 2（那是一張真桌的設定），而測試只入座 1 人
    # ⇒ 人永遠「不齊」、輪永遠不開。這裡只為測試壓成 1（測的是商店／條件，不是開局規則）。
    t.cfg.setdefault("turn", {})["min_seats_to_start"] = 1
    t.cfg.setdefault("turn", {})["auto_start_first_turn"] = True     # 有些桌刻意關掉（測「不自動開輪」用）
    st = t.state
    if not isinstance(st.get("semi"), dict):
        st["semi"] = {}
    if st.get("turn"):
        return t                                   # 冪等：已經開好就不動（同一支測試可能呼叫兩次）
    _sc = (st.get("semi") or {}).get("scene") or {}
    if _sc and _sc.get("closed_at") is None:
        t._maybe_autostart()                       # 景開著但還沒開輪 ⇒ 只補開輪
        return t
    try:
        ensure_turn(t)          # 走正式路徑 ⇒ 有 `scene_open` 事件，回放才對得上
    except Exception:                             # noqa: BLE001
        drop_scene(type("_X", (), {"state": st})())
        t._maybe_autostart()
    assert st.get("turn"), "夾具失敗：開景後仍沒開輪（檢查 turn.min_seats_to_start 與 auto_start_first_turn）"
    # 把回合交給第一位玩家（測試都是一人入座）
    if (st.get("turn") or {}).get("seat") != "p1":
        try:
            t.end_turn(reason="end_turn")
        except Exception:
            pass
        t._maybe_autostart()
    return t

def main() -> int:
    print("== W1. 條件表：載入與驗證 ==")
    tmp, cfg = mk_home()
    t = mk_table(tmp, cfg)
    try:
        ids = t.condition_ids()
        check("W1 wound 包的表載得動（4 部位 ＋ 4 狀態）", len(ids) == 8 and "poison" in ids
              and "wound_head" in ids, f"{ids}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    check("W2 schema 壞掉當場叫：未知 tick 鍵／負傷害／turns=0／句庫缺鍵／id 重複",
          all(bool(conditions.validate(rows)) for rows in (
              [{"id": "x", "tick": {"nope": 1}}],
              [{"id": "x", "tick": {"damage": -1}}],
              [{"id": "x", "turns": 0}],
              [{"id": "x", "text_tick": "no.such.key"}],
              [{"id": "x"}, {"id": "x"}],))
          and conditions.validate([{"id": "ok", "tick": {"damage": 1, "die": 4}, "turns": 2}]) == [])
    check("W3 不是條件表（kind 不對）就不吃它",
          conditions.load([os.path.join(HERE, "packs", "example", "tables", "example.json")]) == {})

    print("== W2. 沒啟用＝legacy（零變更）==")
    tmp0, cfg0 = mk_home(spec=LEGACY)
    t0 = mk_table(tmp0, cfg0)
    try:
        seat_up(t0)
        n = len(t0.events)
        turn_once(t0)
        check("W4 沒啟用帶表的包 ⇒ 表空、沒有條件事件、state 空",
              t0.condition_ids() == [] and not t0.state["conditions"]
              and not [e for e in t0.events[n:] if e.get("type", "").startswith("condition")],
              f"{[e.get('type') for e in t0.events[n:]]}")
        check("W5 沒啟用時 set_condition 拒絕（不靜默）",
              _raises(lambda: t0.set_condition(target="p1", id="poison"), conditions.ConditionError))
        v0 = t0.view(seat="p1")
        check("W6 投影沒有多出來的欄位（legacy 視圖逐字不變）",
              "conditions" not in (v0.get("you") or {})
              and all("conditions" not in s for s in v0["seats"]))
    finally:
        shutil.rmtree(tmp0, ignore_errors=True)

    print("== W3. 投上／讀出 ==")
    tmp, cfg = mk_home()
    t = mk_table(tmp, cfg)
    try:
        seat_up(t)
        ev = t.set_condition(target="p1", id="poison", by="gm")
        cv = t.conditions_view("p1")
        check("W7 condition_set 進事件流（公開）且 state 折得出來",
              ev["type"] == "condition_set" and ev.get("public") is True
              and t.state["conditions"]["p1"]["poison"]["turns_left"] == 3,
              f"{t.state['conditions']}")
        check("W8 conditions_view：名稱／剩幾回合／加減值對得上",
              cv["poison"]["name_jp"] == "毒" and cv["poison"]["turns_left"] == 3
              and cv["poison"]["check_mod"] == -1 and cv["poison"]["tick_damage"] == 1,
              f"{cv}")
        check("W9 事件帶人話（句子的正本在包的 text.json）",
              "毒" in (ev.get("text") or ""))
        check("W10 不存在的條件／席位當場叫（列出可用的）",
              _raises(lambda: t.set_condition(target="p1", id="nope"), conditions.ConditionError)
              and _raises(lambda: t.set_condition(target="nobody", id="poison"),
                          conditions.ConditionError))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("== W4. 判定修正（進 d20 的 mod）==")
    tmp, cfg = mk_home()
    t = mk_table(tmp, cfg)
    try:
        seat_up(t, "p1", "カガリ", "human")
        open_first_scene(t)      # 開景⇒開輪（act 只准當前回合那一席動）
        seat_up(t, "p2", "シズク", "human")
        open_first_scene(t)      # 開景⇒開輪（act 只准當前回合那一席動）
        base_mod = t._mod(t.state["seats"]["p1"]["attrs"], "wil")
        t.set_condition(target="p1", id="wound_head")
        t.set_condition(target="p1", id="poison")
        check("W11 conditions_mod 加總（頭傷 −2、毒 −1）",
              t.conditions_mod("p1", "check") == -3 and t.conditions_mod("p1", "attack") == 0)
        out = t.act(seat="p1", action="check", attr="wil", dc=1)
        check("W12 check 的 roll.mod 真的帶上修正（base＋−3）",
              out["roll"]["mod"] == base_mod - 3, f"{out['roll']['mod']} vs {base_mod}")
        t.set_condition(target="p1", id="wound_arm")
        out2 = t.act(seat="p1", action="attack", target="p2", dc=1)
        check("W13 attack 的 roll.mod 也吃（腕傷 −2）",
              out2["roll"]["mod"] == t._mod(t.state["seats"]["p1"]["attrs"], "agi") - 2,
              f"{out2['roll']['mod']}")
        check("W14 別席不受影響（p2 的 mod 是 0）", t.conditions_mod("p2", "check") == 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("== W5. 每回合結算（扣血／倒數／到期）==")
    tmp, cfg = mk_home()
    t = mk_table(tmp, cfg)
    try:
        seat_up(t)
        t.set_condition(target="p1", id="poison")            # tick 1, turns 3
        hp0 = t.state["seats"]["p1"]["hp"]
        turn_once(t)
        check("W15 turn_started ⇒ condition_tick ＋ 扣血（HP −1）",
              t.state["seats"]["p1"]["hp"] == hp0 - 1
              and t.state["conditions"]["p1"]["poison"]["turns_left"] == 2,
              f"hp={t.state['seats']['p1']['hp']} cond={t.state['conditions']['p1']}")
        turn_once(t)
        check("W16 第二回合再扣一次（−2 累計）", t.state["seats"]["p1"]["hp"] == hp0 - 2)
        turn_once(t)
        check("W17 第三回合到期：自動解除（state 清掉、事件留痕 reason=expired）",
              "poison" not in t.state["conditions"].get("p1", {})
              and [e for e in t.events if e.get("type") == "condition_set"
                   and e.get("reason") == "expired"])
        check("W18 沒有條件的席位居多跑幾回合也不會多出事件",
              not [e for e in t.events if e.get("type", "").startswith("condition")
                   and e.get("seat") == "gm"])
        check("W19 有條件的流 op=replay 一致", bool(t.replay_check().get("replay_matches")),
              f"{t.replay_check().get('diff')}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("== W6. 治療／骰式傷害 ==")
    tmp, cfg = mk_home()
    t = mk_table(tmp, cfg)
    try:
        seat_up(t)
        t.set_condition(target="p1", id="wound_head")
        t.set_condition(target="p1", id="wound_head", value=False)
        check("W20 同一個條件投兩次只算一次；治好＝清掉（value=false）",
              "wound_head" not in (t.state["conditions"].get("p1") or {}))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    tmp, cfg = mk_home()
    tb = mk_table(tmp, cfg)
    try:
        # 臨時加一條「會骰」的條件：**作品層覆蓋引擎層**（`<劇本>/packs/<id>/` 同名蓋掉）——
        # 表是資料 ⇒ 測試直接把 wound 包整個抄進來再改它的表。1d4 ＋ 1。
        shutil.copytree(os.path.join(HERE, "packs", "wound"),
                        os.path.join(tmp, "packs", "wound"))
        p = os.path.join(tmp, "packs", "wound", "tables", "conditions.json")
        table = json.load(open(p, encoding="utf-8"))
        table["rows"].append({"id": "bleed", "name_jp": "出血", "tick": {"damage": 1, "die": 4},
                              "turns": 2, "text_tick": "condition.poison_tick"})
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(table, fh, ensure_ascii=False)
        t2 = mk_table(tmp, cfg)
        seat_up(t2)
        t2.set_condition(target="p1", id="bleed")
        hp0 = t2.state["seats"]["p1"]["hp"]
        turn_once(t2)
        ev = [e for e in t2.events if e.get("type") == "condition_tick"][-1]
        check("W21 骰式傷害：值寫進事件（回放不必重算）且 1..5",
              ev["damage"] == hp0 - t2.state["seats"]["p1"]["hp"] and 2 <= ev["damage"] <= 5,
              f"damage={ev['damage']}")
        check("W22 同一份流回放一致", bool(t2.replay_check().get("replay_matches")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("== W7. 投影（只給該席位與 GM）／公開 ==")
    tmp, cfg = mk_home()
    t = mk_table(tmp, cfg)
    try:
        seat_up(t, "p1", "カガリ", "human")
        seat_up(t, "p2", "シズク", "human")
        t.set_condition(target="p1", id="burn")
        v1 = t.view(seat="p1")
        v2 = t.view(seat="p2")
        check("W23 自己的卡看得到（id／剩幾回合／扣多少）",
              (v1.get("you") or {}).get("conditions", {}).get("burn", {}).get("turns_left") == 2,
              f"{(v1.get('you') or {}).get('conditions')}")
        check("W24 席位列表公開看得到（傷在身上看得到），沒條件的席位沒那格",
              any(s.get("conditions", {}).get("burn") for s in v1["seats"])
              and all("conditions" not in s for s in v1["seats"] if s["seat"] == "p2"))
        check("W25 別人的卡上沒有『我的條件』這格（沒有條件就不加欄位）",
              "conditions" not in (v2.get("you") or {}))
        gm = t.view(gm=True)
        check("W26 GM 的 full_state 看得到條件（裁定用）",
              (gm["gm"]["full_state"].get("conditions") or {}).get("p1", {}).get("burn"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("== W8. effect 收窄＋trigger 路線 ==")
    mod_cond = {"meta": {"id": "cond_fixture"},
                "triggers": [{"id": "t_trap", "on": "say",
                              "when": {"==": [{"var": "world.event.text"}, "踩到陷阱"]},
                              "then": [{"condition_set": {"id": "wound_leg", "seat": "p1",
                                                          "note": "陷阱"}}]}]}
    tmpA, _ = mk_home(spec=LEGACY)
    try:
        json.dump(mod_cond, open(os.path.join(tmpA, MODF), "w", encoding="utf-8"),
                  ensure_ascii=False)
        err = ""
        try:
            scenario.Scenario.load(tmpA, allowed_effects=["emit", "fact_set", "ending",
                                                          "difficulty_set"])
        except scenario.ModuleError as exc:
            err = str(exc)
        check("W27 沒啟用 wound ⇒ 劇本用 condition_set 在**載入時**就叫（收窄生效）",
              "condition_set" in err, err.splitlines()[-1][:70])
    finally:
        shutil.rmtree(tmpA, ignore_errors=True)

    tmpB, cfgB = mk_home(spec=WOUND)
    try:
        json.dump(mod_cond, open(os.path.join(tmpB, MODF), "w", encoding="utf-8"),
                  ensure_ascii=False)
        t3 = mk_table(tmpB, cfgB)
        seat_up(t3, "p1")
        t3._emit("say", public=True, seat="p1", text="踩到陷阱")
        check("W28 啟用 wound ⇒ trigger 的 condition_set 投得上（seat=me 解析）",
              "wound_leg" in (t3.state["conditions"].get("p1") or {}),
              f"{t3.state['conditions']}")
        check("W29 條件事件的型別在公開日誌白名單裡（包宣告 ⇒ 只增不減）",
              "condition_set" in t3.log_types and "condition_tick" in t3.log_types)
    finally:
        shutil.rmtree(tmpB, ignore_errors=True)

    tmpA2, cfgA2 = mk_home(spec=WOUND)
    try:
        t5 = mk_table(tmpA2, cfgA2)
        seat_up(t5, "p1")
        t5.set_condition(target="p1", id="poison")
        ctx = t5.module.ctx(t5, seat="p1")
        check("W31 AST 看得到條件（`me.conditions`／`seats.<席>.conditions`）——作者可寫判據",
              ctx["me"]["conditions"] == ["poison"]
              and ctx["seats"]["p1"]["conditions"] == ["poison"],
              f"{ctx['me']['conditions']}")
    finally:
        shutil.rmtree(tmpA2, ignore_errors=True)

    tmpC, cfgC = mk_home(spec=WOUND)
    try:
        t4 = mk_table(tmpC, cfgC)
        seat_up(t4, "p1")
        t4.set_condition(target="p1", id="poison")
        _ = t4.view(seat="p1")
        check("W30 有條件的桌回放一致（改投影／改事件都不飄）",
              bool(t4.replay_check().get("replay_matches")), f"{t4.replay_check().get('diff')}")
    finally:
        shutil.rmtree(tmpC, ignore_errors=True)

    print("== X. 影響維度（condition_mod 的每一維都要有消費處）==")
    # ① schema：新欄位與 tick.resource 的形狀
    _bad = conditions.validate([{"id": "a", "move_dc_mod": "2"}])
    check("X1a `move_dc_mod` 非整數 ⇒ 表不合法", any("move_dc_mod" in x for x in _bad), f"{_bad}")
    _bad2 = conditions.validate([{"id": "a", "tick": {"resource": {"name": "stamina"}}}])
    check("X1b `tick.resource` 缺 value ⇒ 表不合法", any("tick.resource" in x for x in _bad2), f"{_bad2}")
    _ok = conditions.validate([{"id": "a", "tick": {"resource": {"name": "stamina", "value": -1}},
                                "act_slots_mod": -1, "move_dc_mod": 2, "memory_drain_mod": 1}])
    check("X1c 合法的新欄位 ⇒ 過", _ok == [], f"{_ok}")
    # ② 移動 DC 吃 move_dc（腳傷 ⇒ 荒野道更難走）
    XSPEC = ["d20core", "board", "semic", "mode.turn", "wound"]
    tmpX, cfgX = mk_home(spec=XSPEC)
    try:
        t5 = mk_table(tmpX, cfgX)
        seat_up(t5, "p1")
        open_first_scene(t5)      # 開景⇒開輪（act 只准當前回合那一席動）
        base = int(t5.board_cfg.get("travel_dc_by_danger")[2])          # w_yamato 的 danger=2
        # ⚠ 這一條測的是**維度**不是地圖：把 teckan→w_yamato 這條邊改成荒野道（劇本裡是 road），
        #   才會走移動檢定（`board_act` 只在 kind=="wild" 時擲 travel 骰）
        t5.adj["teckan"]["w_yamato"] = "wild"
        t5.act(seat="p1", action="move", target="w_yamato")
        dc0 = next((e.get("dc") for e in t5.events if e.get("type") == "roll"), None)
        check("X2a 沒有腳傷 ⇒ 移動 DC ＝ 基礎值", dc0 == base, f"dc={dc0} base={base}")
        if t5.state["seats"]["p1"].get("node") != "teckan":             # 檢定沒過＝原地不動
            t5.act(seat="p1", action="move", target="teckan")              # 先走回鉄環市（都市＝不檢定）
        t5.set_condition(target="p1", id="wound_leg")
        n2 = len(t5.events)
        t5.act(seat="p1", action="move", target="w_yamato")
        dc1 = next((e.get("dc") for e in t5.events[n2:] if e.get("type") == "roll"), None)
        check("X2b 腳傷（move_dc_mod=2）⇒ 移動 DC ＋2", dc1 == base + 2, f"dc={dc1}")
        # ③ 塔內每回合記憶消耗吃 memory_drain（胸傷 ＋1）
        # 走真的進塔事件（別直接改 state——`op=replay` 會抓出來；X6 就是在咬這件事）
        t5._emit("entered_tower", public=True, seat="p1", node="hyakki_f1", floor="hyakki_f1")
        t5.tower_cfg["memory_per_turn_inside"] = 2        # 測維度用：把基礎值寫成 2（現役劇本沒有這條）
        t5.state["seats"]["p1"]["memory"] = 50            # 測維度用：這一席要有記憶可以扣
        base_drain = 2
        n3 = len(t5.events)
        t5.board_on_turn_start("p1")                                     # 回合開始鉤子（塔內記憶流失）
        _d = [e.get("delta") for e in t5.events[n3:] if e.get("type") == "memory_change"]
        check("X3a 沒有胸傷 ⇒ 塔內扣記憶 ＝ 基礎值", bool(_d) and _d[0] == -(base_drain),
              f"delta={_d} base={base_drain}")
        t5.set_condition(target="p1", id="wound_chest")
        n4 = len(t5.events)
        t5.board_on_turn_start("p1")
        _d2 = [e.get("delta") for e in t5.events[n4:] if e.get("type") == "memory_change"]
        check("X3b 胸傷（memory_drain_mod=1）⇒ 塔內每次多扣 1", bool(_d2) and _d2[0] == -(base_drain + 1),
              f"delta={_d2}")
        # ④ 行動格吃 act_slots（凍え ⇒ −1 格，下限 0）
        seat_up(t5, "gm", name="GM", kind="gm")
        ensure_turn(t5)                 # 2026-10-21 拆景：出手只要求「有進行中的回合」
        lim0 = t5._round_limit("p1", "act")
        t5.set_condition(target="p1", id="chill")
        lim1 = t5._round_limit("p1", "act")
        check("X4a 凍え（act_slots_mod=-1）⇒ 這一回合能動的格數 −1", lim1 == max(0, lim0 - 1),
              f"{lim0} → {lim1}")
        t5.state["paused"] = False
        t5.cfg.setdefault("turn", {})["actions_per_turn"] = 0          # 逼到下限
        lim2 = t5._round_limit("p1", "act")
        check("X4b 額度下限 0（條件不能把格數壓成負的）", lim2 >= 0, f"{lim2}")
        # ⑤ tick.resource：每回合扣通用資源（用記憶體裡的條件表，測機制本身）
        t5.condition_table["x_drain"] = {"id": "x_drain", "name_jp": "測試耗損",
                                         "tick": {"resource": {"name": "mana", "value": -1}}}
        t5.add_resource(target="p1", name="mana", value=3, mode="set", by="gm")
        t5.set_condition(target="p1", id="x_drain")
        n5 = len(t5.events)
        t5.conditions_tick("p1")
        rc = [e for e in t5.events[n5:] if e.get("type") == "resource_changed"]
        check("X5 `tick.resource` ⇒ 每回合扣通用資源（after 寫進事件）",
              rc and rc[0].get("name") == "mana" and rc[0].get("after") == 2,
              f"{[(e.get('name'), e.get('after')) for e in rc]}")
        check("X6 帶新維度的流回放一致", bool(t5.replay_check().get("replay_matches")),
              f"{t5.replay_check().get('diff')}")
    finally:
        shutil.rmtree(tmpX, ignore_errors=True)
    # ⑦ legacy：沒啟用條件表 ⇒ 每一維都必須是 0／不發事件
    tmpL, cfgL = mk_home(spec=["d20core", "board", "semic", "mode.turn"])
    try:
        t6 = mk_table(tmpL, cfgL)
        seat_up(t6, "p1")
        check("X7 沒啟用條件表 ⇒ 四個維度都是 0（legacy 一字不變）",
              t6.conditions_mod("p1", "move_dc") == 0
              and t6.conditions_mod("p1", "memory_drain") == 0
              and t6.conditions_mod("p1", "act_slots") == 0
              and t6._round_limit("p1", "act") >= 0)
    finally:
        shutil.rmtree(tmpL, ignore_errors=True)

    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
