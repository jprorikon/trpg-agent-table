#!/usr/bin/env python3
"""**死亡與送還（death）的自測**（2026-10-07 設計者：「角色死亡会回到最近的城市并跳过2回合」）。

    python3 test_death.py

機制住內核（`death.py`）、數值住包（`packs/death/pack.json` 的 `provides.defaults.rules.death`）。
「最近的城市」＝無向 BFS 走棋盤 `adj` 挑第一個城市；「跳過 N 回合」＝回合機遇到缺席的人
發 `turn_skipped` 後直接交下一席（場景制把他從「還沒出手」名單排掉，否則 GM 收不了景）。

  D1 **包預設**：列了 `death` 包 ⇒ 旋鈕自動就位（`mode=return_city`／`skip_turns=2`…）。
  D2 **config 一律贏**：`rules.death` 寫什麼用什麼（包只補沒寫的）。
  D3 **壞旋鈕載入當場叫**：mode 亂寫／負數／`hp_restore=0`／`drop_coins` 超界或非數。
  D4 **沒啟用＝legacy**：HP≤0 只是 `down`（躺著等人救）——零 `death`／`turn_skipped` 事件、投影不多欄位。
  D5 **倒下即送還**：真打（`act(attack)`）把人打死 ⇒ `death` 事件（公開）、**回到最近的城市**
    （不是 `start_node`）、HP 復原、`down` 解除、缺席計數就位。
  D6 **罰則旋鈕**：`hp_restore`／`lost_memory`／`drop_coins`／`clear_conditions` 真的生效。
  D7 **跳過兩回合**：每一席各被跳掉兩次，之後恢復正常出手（事件順序逐筆對）。
  D8 **場景制不卡收景**：`pending_actors()` 不含缺席中的人。
  D9 **事件溯源**：死過人、跳過回合的流 `op=replay` 逐欄一致。
  D10 **投影與日誌**：`view.you.skip_turns` 出現／`recent` 有那兩行人話。
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

from trpg.rules import death as death                                                             # noqa: E402
from trpg.core import store as store                                                             # noqa: E402
from trpg.engine import Table                                                 # noqa: E402
from trpg.core.errors import RuleError                                             # noqa: E402

OK, FAIL = [], []
SPEC = ["d20core", "board", "semic", "mode.turn", "death"]
SPEC_SCENE = ["d20core", "board", "semic", "mode.turn", "death"]   # 2026-10-21：mode.scene 已刪（引擎只跑回合制）
LEGACY = ["d20core", "board", "semic", "mode.turn"]

# ── 測試用的棋盤：start_node＝beta（**故意不是最近的**），tow_f1 最近的城市是 alpha ──
BOARD = {
    "board_name": "death_test",
    "start_node": "beta",
    "nodes": [
        {"id": "alpha", "jp": "アルファ", "type": "city", "tags": ["city", "rest"],
         "edges": [{"to": "hub1", "kind": "road"}]},
        {"id": "hub1", "jp": "荒野A", "type": "wild",
         "edges": [{"to": "alpha", "kind": "road"}, {"to": "tow", "kind": "road"},
                   {"to": "hub2", "kind": "road"}]},
        {"id": "hub2", "jp": "荒野B", "type": "wild",
         "edges": [{"to": "hub1", "kind": "road"}, {"to": "beta", "kind": "road"}]},
        {"id": "beta", "jp": "ベータ", "type": "city", "tags": ["city", "rest"],
         "edges": [{"to": "hub2", "kind": "road"}]},
        {"id": "tow", "jp": "塔", "type": "monolith_entry", "tags": ["monolith_entry"],
         "edges": [{"to": "hub1", "kind": "road"}, {"to": "tow_f1", "kind": "entrance"}]},
        {"id": "tow_f1", "jp": "塔1階", "type": "tower_floor", "depth": 1, "mobs": 1,
         "tags": ["tower", "battle_domain"],
         "edges": [{"to": "tow", "kind": "exit"}]},
    ],
    "roster": [
        {"seat": "char_a", "name": "A", "attrs": {"str": 3, "agi": 3, "wil": 3}},
        {"seat": "char_b", "name": "B", "attrs": {"str": 3, "agi": 3, "wil": 3}},
    ],
}
SEATS = {"table": "death_test", "seats": [
    {"seat": "gm", "kind": "gm", "name": "GM", "character": None, "goal": None, "start_node": None},
    {"seat": "p1", "kind": "human", "name": "A", "character": "char_a", "goal": "g_a"},
    {"seat": "p2", "kind": "agent", "name": "B", "character": "char_b", "goal": "g_b"},
]}
GOALS = {"goals": [
    {"id": "g_a", "title": "a", "text": "a", "owner_character": "char_a"},
    {"id": "g_b", "title": "b", "text": "b", "owner_character": "char_b"},
]}
CLUES = {"claims": [{"id": "c_1", "text": "c1"}],
         "fragments": [{"id": "f1", "pool": "tower", "supports": ["c_1"], "weight": 1, "text": "frag"}]}


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


def _raises(fn, exc=Exception):
    try:
        fn()
    except exc as e:
        print(f"    （擋下了：{type(e).__name__}: {e}）")
        return True
    except Exception as e2:                                              # noqa: BLE001
        print(f"    （丟的是 {type(e2).__name__}: {e2}）")
        return False
    return False


def mk_home(*, spec=SPEC, rules=None, pacing="turn"):
    tmp = tempfile.mkdtemp(prefix="trpg_death_")
    cfg = {"table_id": "death_test", "seed": 7, "port": 0,
           "packs": {"enabled": list(spec)},
           "turn": {"seconds": 180, "policy_on_timeout": "pass", "actions_per_turn": 2,
                    "min_seats_to_start": 2, "auto_start_first_turn": False},
           "pacing": {"mode": pacing, "actions_per_scene": 2, "says_per_scene": 2},
           "rules": {"actions": ["move", "fight", "attack", "check", "defend", "use",
                                 "other", "pass", "end_turn"]}}
    if rules:
        cfg["rules"].update(rules)
    for name, data in (("config.json", cfg), ("board.json", BOARD), ("seats.json", SEATS),
                       ("goals.json", GOALS), ("clues.json", CLUES)):
        with open(os.path.join(tmp, name), "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
    return tmp, cfg


def mk_table(tmp, cfg):
    return Table(home=tmp, cfg=cfg, store=store.FileStore(tmp), clock=lambda: 1000.0)


def seat_up(t, sid="p1", name="A", kind="human", node="tow_f1", order=0):
    return t.join(seat=sid, name=name, kind=kind, node=node, order=order,
                  attrs={"str": 3, "agi": 3, "wil": 3})


def kill(t, sid="p1", src="monster"):
    """把人一击打死（走引擎的傷害路：`roll_damage` 與 `attack` 同一條算式）。"""
    return t.roll_damage(seat=sid, die=0, bonus=999, src=src, note=f"test kill {sid}")


def types(t):
    return [e["type"] for e in t.events]


def of(t, kind):
    return [e for e in t.events if e["type"] == kind]


def open_first_scene(t, seat=None):
    """夾具：開第一景 ⇒ 讓人齊了開第一輪（2026-10-08 起先開景才開輪；2026-10-20 起只有當前回合那一席能 act）。"""
    t.cfg.setdefault("turn", {})["min_seats_to_start"] = 1
    t.cfg.setdefault("turn", {})["auto_start_first_turn"] = True     # 有些桌刻意關掉（測「不自動開輪」用）
    st = t.state
    if not isinstance(st.get("semi"), dict):
        st["semi"] = {}
    if st.get("turn"):
        if seat and str((st.get("turn") or {}).get("seat")) != str(seat):
            t.end_turn(reason="end_turn")          # 讓回合轉到指定的人（測試要誰出手就交給誰）
            t._maybe_autostart()
        return t                                   # 冪等：已經開好就不動
    _sc = (st.get("semi") or {}).get("scene") or {}
    if _sc and _sc.get("closed_at") is None:
        t._maybe_autostart()                       # 景開著但還沒開輪 ⇒ 只補開輪
        return t
    ensure_turn(t)
    assert st.get("turn"), "夾具失敗：開景後仍沒開輪（檢查 turn.min_seats_to_start／pacing.mode）"
    return t


def main() -> int:                                                       # noqa: C901
    print("== D1／D2. 旋鈕（包預設 ＋ config 一律贏）==")
    tmp, cfg = mk_home()
    try:
        t = mk_table(tmp, cfg)
        check("D1a 列了 death 包 ⇒ 機制就位（death_enabled）", t.death_enabled is True)
        check("D1b 包預設就位（mode／skip_turns／hp_restore／nearest）",
              (t.death_cfg["mode"], t.death_cfg["skip_turns"], t.death_cfg["hp_restore"],
               t.death_cfg["nearest"]) == ("return_city", 2, 1, True),
              f"{t.death_cfg}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    tmp2, cfg2 = mk_home(rules={"death": {"skip_turns": 3, "hp_restore": 5}})
    try:
        t2 = mk_table(tmp2, cfg2)
        check("D2 config 覆寫贏、包只補沒寫的（skip 3／hp 5／mode 仍 return_city）",
              (t2.death_cfg["skip_turns"], t2.death_cfg["hp_restore"], t2.death_cfg["mode"])
              == (3, 5, "return_city"), f"{t2.death_cfg}")
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)

    print("== D3. 壞旋鈕載入當場叫 ==")
    for label, bad in (("mode 亂寫", {"mode": "fly"}), ("skip_turns 負數", {"skip_turns": -1}),
                       ("hp_restore 0", {"hp_restore": 0}), ("drop_coins 超界", {"drop_coins": 2}),
                       ("drop_coins 非數", {"drop_coins": "lots"}), ("lost_memory 負數", {"lost_memory": -3})):
        tmp3, cfg3 = mk_home(rules={"death": bad})
        try:
            check(f"D3 {label} ⇒ 當場叫", _raises(lambda: mk_table(tmp3, cfg3), RuleError))
        finally:
            shutil.rmtree(tmp3, ignore_errors=True)
    check("D3 旋鈕形狀表與實作一致（DEFAULT 的鍵就是引擎認得的那幾個）",
          set(death.DEFAULT) == {"mode", "skip_turns", "hp_restore", "nearest",
                                 "clear_conditions", "drop_coins", "lost_memory"})

    print("== D4. 沒啟用＝legacy（HP≤0 只是 down）==")
    tmp4, cfg4 = mk_home(spec=LEGACY)
    try:
        t4 = mk_table(tmp4, cfg4)
        seat_up(t4, "p1")
        before = len(t4.events)
        kill(t4, "p1")
        check("D4a 沒啟用 ⇒ 一個 death／turn_skipped 都不發",
              not of(t4, "death") and not of(t4, "turn_skipped"))
        check("D4b 舊行為：HP≤0 只是 down、人留在原地",
              "down" in t4.state["seats"]["p1"]["status"]
              and t4.state["seats"]["p1"]["node"] == "tow_f1")
        check("D4c 舊行為：state 沒有 skip 紀錄、投影不多 skip_turns",
              not t4.state.get("skip") and "skip_turns" not in (t4.view(seat="p1")["you"] or {}))
        check("D4d 舊行為：這一擊只多一筆事件（沒有偷偷加料）",
              len(t4.events) - before == 1, f"+{len(t4.events) - before}")
    finally:
        shutil.rmtree(tmp4, ignore_errors=True)

    print("== D5. 倒下即送還（真打）＋ 最近的城市 ==")
    tmp5, cfg5 = mk_home()
    try:
        t5 = mk_table(tmp5, cfg5)
        seat_up(t5, "p1", node="tow_f1")
        open_first_scene(t5)
        seat_up(t5, "p2", name="B", kind="agent", node="alpha", order=1)
        open_first_scene(t5, seat="p2")
        hit = t5.act(seat="p2", action="attack", target="p1")            # 真打一手（戰鬥路徑）
        check("D5a 真打一手有結果（roll／damage 至少一個）",
              bool(hit.get("roll") is not None), f"{hit.get('roll')}")
        t5.roll_damage(seat="p1", die=0, bonus=999, src="p2")            # 打死（同一條傷害算式）
        d = of(t5, "death")[-1]
        s = t5.state["seats"]["p1"]
        check("D5b 死了 ⇒ `death` 事件（公開、進日誌）",
              bool(d) and d.get("public") is True and d in t5.state["log"])
        check("D5c 回到**最近的城市**（alpha），不是 start_node（beta）",
              d["to"] == "alpha" and s["node"] == "alpha", f"to={d['to']} node={s['node']}")
        check("D5d HP 復原到 hp_restore、`down` 解除（不必等人救）",
              s["hp"] == t5.death_cfg["hp_restore"] and "down" not in s["status"],
              f"hp={s['hp']} status={s['status']}")
        check("D5e 缺席計數就位（skip_turns＝2）", t5.state["skip"].get("p1") == 2,
              f"{t5.state.get('skip')}")
        check("D5f 進塔紀錄歸零（人已經不在塔裡）",
              int((t5.state["board"]["inside_turns"] or {}).get("p1") or 0) == 0)
        check("D5g 旁人的視圖看得到（人話尾巴有那一行）",
              any("送还到" in x for x in (t5.view(seat="p2").get("recent") or [])))
    finally:
        shutil.rmtree(tmp5, ignore_errors=True)

    print("== D6. 罰則旋鈕（hp／記憶／貨幣／條件）==")
    tmp6, cfg6 = mk_home(spec=SPEC + ["wound"],
                         rules={"death": {"hp_restore": 5, "lost_memory": 3, "drop_coins": 1.0,
                                          "clear_conditions": True}})
    try:
        t6 = mk_table(tmp6, cfg6)
        seat_up(t6, "p1", node="tow_f1")
        t6.state["seats"]["p1"]["memory"] = 60                            # 直接鋪底（測罰則）
        t6.grant_coins(seat="p1", amount=4, reason="test")
        t6.set_condition(id="wound_head", target="p1")
        kill(t6, "p1")
        s = t6.state["seats"]["p1"]
        check("D6a hp_restore 生效（5）", s["hp"] == 5, f"hp={s['hp']}")
        check("D6b lost_memory 生效（60→57）", s["memory"] == 57, f"mem={s['memory']}")
        check("D6c drop_coins 生效（4 貨幣全掉）", int(s["star_coins"]) == 0, f"coins={s['star_coins']}")
        check("D6d clear_conditions 生效（頭部受傷洗掉）",
              not (t6.state["conditions"].get("p1") or {}), f"{t6.state.get('conditions')}")
    finally:
        shutil.rmtree(tmp6, ignore_errors=True)

    print("== D7. 跳過兩個回合 ==")
    tmp7, cfg7 = mk_home()
    try:
        t7 = mk_table(tmp7, cfg7)
        seat_up(t7, "p1", node="tow_f1")
        seat_up(t7, "p2", name="B", kind="agent", node="tow_f1", order=1)
        kill(t7, "p1")
        t7.start_turn()                                                   # 第一個該出手的是 p1 ⇒ 跳過
        t7.end_turn()                                                     # 下一輪又輪到 p1 ⇒ 再跳一次
        t7.end_turn()                                                     # 這一次 p1 恢復正常出手
        skips = [(e["seat"], e["remaining"]) for e in of(t7, "turn_skipped")]
        starts = [e["seat"] for e in of(t7, "turn_started")]
        check("D7a 兩次跳過，計數 2→1→0（逐筆對）", skips == [("p1", 1), ("p1", 0)], f"{skips}")
        check("D7b 缺席期間是別人拿到回合（p2、p2、p1）", starts == ["p2", "p2", "p1"], f"{starts}")
        check("D7c 兩回合之後恢復正常（skip 清空）", not t7.state.get("skip"),
              f"{t7.state.get('skip')}")
        check("D7d 跳過的席位沒有被『出手』（不進 turn_ended 名單）",
              [e["seat"] for e in of(t7, "turn_ended")] == ["p2", "p2"],
              f"{[e['seat'] for e in of(t7, 'turn_ended')]}")
    finally:
        shutil.rmtree(tmp7, ignore_errors=True)

    print("== D8. 場景制不卡收景 ==")
    tmp8, cfg8 = mk_home(spec=SPEC_SCENE, pacing="scene")
    try:
        t8 = mk_table(tmp8, cfg8)
        seat_up(t8, "p1", node="tow_f1")
        seat_up(t8, "p2", name="B", kind="agent", node="tow_f1", order=1)
        kill(t8, "p1")
        pend = t8.pending_actors()
        check("D8a 缺席中的人不在「還沒出手」名單（GM 收得了景）",
              "p1" not in pend and "p2" in pend, f"{pend}")
    finally:
        shutil.rmtree(tmp8, ignore_errors=True)

    print("== D9. 事件溯源（op=replay）==")
    tmp9, cfg9 = mk_home()
    try:
        t9 = mk_table(tmp9, cfg9)
        seat_up(t9, "p1", node="tow_f1")
        seat_up(t9, "p2", name="B", kind="agent", node="tow_f1", order=1)
        kill(t9, "p1")
        t9.start_turn()
        t9.end_turn()
        r = t9.replay_check()
        check("D9 死過人＋跳過回合的流回放逐欄一致", bool(r.get("replay_matches")), f"{r.get('diff')}")
    finally:
        shutil.rmtree(tmp9, ignore_errors=True)

    print("== D10. 投影／日誌 ==")
    tmp10, cfg10 = mk_home()
    try:
        t10 = mk_table(tmp10, cfg10)
        seat_up(t10, "p1", node="tow_f1")
        kill(t10, "p1")
        v = t10.view(seat="p1")["you"]
        check("D10a view.you.skip_turns 出現（＝2）", v.get("skip_turns") == 2, f"{v.get('skip_turns')}")
        lines = t10.view(seat="p1").get("recent") or []
        check("D10b 日誌有死亡那一行（自己的視圖看得到）",
              any("被击倒" in x for x in lines), f"{lines[-3:]}")
        tail = t10.render_log_tail(limit=20)
        check("D10c 人話尾巴兩種句子都排得出來（death／turn_skipped）",
              bool(tail) and t10.phrase("death.turn_skipped", remaining=1).startswith("傷"))
    finally:
        shutil.rmtree(tmp10, ignore_errors=True)

    print("== D11. 公開流（現行契約：匿名 /api/log 只吃內核白名單）==")
    tmp11, cfg11 = mk_home()
    try:
        t11 = mk_table(tmp11, cfg11)
        seat_up(t11, "p1", node="tow_f1")
        seat_up(t11, "p2", name="B", kind="agent", node="tow_f1", order=1)
        kill(t11, "p1")
        t11.start_turn()
        pub_types = [e["type"] for e in t11.public_events()]
        view_types = [e["type"] for e in t11.view(seat="p1").get("log") or []]
        check("D11a 玩家視圖看得到（view.log 用**這一桌**的白名單：death／turn_skipped）",
              "death" in view_types and "turn_skipped" in view_types, f"{sorted(set(view_types))}")
        check("D11b 匿名流 ⊆ view.log（不變式：匿名永不比 view 多）",
              set(pub_types) <= set(view_types), f"{sorted(set(pub_types) - set(view_types))}")
        # 現行契約：匿名 /api/log 只吃內核常數 ⇒ 包宣告的事件不在裡面（要改＝動契約，先問設計者）
        check("D11c 現行契約：包宣告的事件不進匿名流（documented gap，未改）",
              "death" not in pub_types, f"{sorted(set(pub_types))}")
    finally:
        shutil.rmtree(tmp11, ignore_errors=True)

    print(f"\n== 結果：{len(OK)} PASS / {len(FAIL)} FAIL ==")
    for x in FAIL:
        print("  FAIL " + x)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
