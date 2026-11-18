#!/usr/bin/env python3
"""L1（求值器＋劇本模組＋觸發）的自測。

    python3 test_module.py

三組：
  A. **求值器**（`expr.py`）：值／邏輯／比較／算術／容器／失敗說明／靜態檢查。
     重點回歸：`and`／`or` 回傳的必須是**真值本身**（2026-10-06 真的踩到把
     `(值, 說明)` 元組當值傳出去 ⇒ 真值判斷變成「元組非空」⇒ 一律為真）。
  B. **劇本模組**（`scenario.py`）：載入／驗證／AST 判據／難度預設。
  C. **觸發跑到定點**：`on`／`when`／`once`／三種 effect／連鎖／事件溯源（`op=replay` 一致）。
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
from trpg.rules import packs as packs                                                             # noqa: E402
CAMP = layout.must_locate_scenario(HERE, "hyakki_tower", "sannenme_no_onibarai")
DEF = layout.def_dir_of(CAMP)          # 定義層（WS-B：桌目錄裡只剩 config／runs）

from trpg.rules import expr as expr                                                              # noqa: E402
from trpg.rules import scenario as scenario                                                          # noqa: E402
MODF = scenario.module_filename(DEF) or "scenario.json"   # 劇本規則資料的檔名（WS-B：新名優先，別硬編）
from trpg.core import store as store                                                             # noqa: E402
from trpg.engine import Table                                                 # noqa: E402
from support_fixture import seed_run  # noqa: E402  現役桌可能已退役 ⇒ 從歸檔／現場造一局

OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


CTX = {
    "me": {"seat": "p1", "node": "w_minato", "claims": ["c_cat"], "fragment_sources": [],
           "shown_to": {"npc1": 2}, "memory": 40},
    "seats": {"p1": {"seat": "p1", "node": "hyakki_f5", "memory": 40},
              "p2": {"seat": "p2", "node": "w_minato", "memory": 0}},
    "world": {"pray_first": {"hyakki_f4": "p1"}, "roundNo": 3},
    "facts": {"flag": True},
    "round_no": 2,
}


def trim_stream(path, drop_from):
    """把事件流**從第一個 `drop_from` 事件起整段砍掉**（連續尾切 ⇒ seq 仍連續、replay 仍一致）。

    為什麼要有這支：真桌的事件流會一直長（也可能**已經判過結局**）。測試若照抄現役流，
    就變成「在測線上狀態」而不是測機制——實例：`check_endings` 見到 state 已經 `ended` 就短路，
    B10／C3 直接假紅（2026-10-06 加「②已通塔＝已贏」那條判據之後就中了）。
    """
    if not os.path.exists(path):
        return
    keep = []
    for line in open(path, encoding="utf-8"):
        if not line.strip():
            continue
        if json.loads(line).get("type") == drop_from:
            break
        keep.append(line if line.endswith("\n") else line + "\n")
    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(keep)


def mk_home(*, module=None, copy_data=True, with_module=True, trim="ending"):
    """臨時劇本目錄（預設照抄真劇本，含 `module.json`）。

    `module=<dict>` ⇒ 覆寫成合成模組；`with_module=False` ⇒ 不帶模組（測「舊劇本」路徑）。
    `trim="ending"` ⇒ 抄完事件流後砍掉「第一個結局之後」的部分（測判決時用自己的流，見
    `trim_stream`）；要照抄整條現役流就傳 `trim=None`。
    """
    tmp = tempfile.mkdtemp(prefix="trpg_module_")
    for f in ("board.json", "goals.json", "clues.json", "seats.json"):
        shutil.copy(os.path.join(DEF, f), os.path.join(tmp, f))
    _psrc = os.path.join(DEF, "packs")        # 劇本自帶的作品層包（沒帶 ⇒ 啟用清單解析不動）
    if os.path.isdir(_psrc):
        shutil.copytree(_psrc, os.path.join(tmp, "packs"))
        packs.seed_module_packs(os.path.dirname(_psrc), tmp)   # ＋模組層的包（rules.core 住模組 repo）
    if with_module and os.path.exists(os.path.join(DEF, MODF)):
        shutil.copy(os.path.join(DEF, MODF), os.path.join(tmp, MODF))
    if copy_data:
        seed_run(CAMP, tmp, with_module=with_module)     # 現役那一局（一局一檔）；「舊劇本」路徑不許塞模組檔
        if trim:
            trim_stream(layout.events_path(tmp), trim)
    cfg = layout.read_table_config(CAMP)
    # ⚠ 夾具隔離（2026-10-07 單一進程 + 子路徑）：這兩個是**部署專屬**的鍵，
    #   從真 config 複製進來會讓 temp 桌指到現役 hub／帶別人的前綴 ⇒ 一律清掉。
    cfg.pop("hub_base", None)
    cfg.pop("public_prefix", None)
    cfg["port"] = 0
    json.dump(cfg, open(os.path.join(tmp, "config.json"), "w", encoding="utf-8"),
              ensure_ascii=False)
    if module is not None:
        # ⚠ 合成的模組要寫**權威名**（MODF），而且先把另一個名字清掉——
        #   否則夾具抄進來的真劇本會 shadow 掉合成模組（2026-10-07 踩到：C1／C3／C5 假紅）。
        for _n in scenario.MODULE_FILES:
            _p = os.path.join(tmp, _n)
            if os.path.exists(_p):
                os.remove(_p)
        json.dump(module, open(os.path.join(tmp, MODF), "w", encoding="utf-8"),
                  ensure_ascii=False)
    return tmp, cfg


def mk_table(tmp, cfg):
    return Table(home=tmp, cfg=cfg, store=store.FileStore(tmp), clock=lambda: 1000.0)


def main() -> int:
    print("== A. 求值器（expr.py）==")
    check("A1 字面值直通（數字／字串／布林／null／陣列）",
          expr.evaluate(7, CTX) == 7 and expr.evaluate("x", CTX) == "x"
          and expr.evaluate(None, CTX) is None and expr.evaluate([1, 2], CTX) == [1, 2])
    check("A2 var 點號路徑查值／缺失回 null",
          expr.evaluate({"var": "me.node"}, CTX) == "w_minato"
          and expr.evaluate({"var": "me.nope.deep"}, CTX) is None
          and expr.evaluate({"var": "seats.p2.memory"}, CTX) == 0)
    check("A3 exists／get（含預設）",
          expr.evaluate({"exists": "world.pray_first.hyakki_f4"}, CTX) is True
          and expr.evaluate({"exists": "world.pray_first.hyakki_f9"}, CTX) is False
          and expr.evaluate({"get": [{"var": "me.shown_to"}, "npc1", 0]}, CTX) == 2
          and expr.evaluate({"get": [{"var": "me.shown_to"}, "nobody", 0]}, CTX) == 0)
    andv, orv = expr.evaluate({"and": [1, 0]}, CTX), expr.evaluate({"or": [0, 2]}, CTX)
    check("A4 and／or／not 回傳的是**真值本身**（不是 (值,說明) 元組——回歸）",
          andv is False and orv is True and expr.evaluate({"not": 0}, CTX) is True
          and isinstance(andv, bool), f"and={andv!r}({type(andv).__name__}) or={orv!r}")
    check("A5 and 短路（後面那條壞了也不會被算到）",
          expr.evaluate({"and": [0, {"+": ["a", 1]}]}, CTX) is False)
    check("A6 比較：null 一律 false，不炸",
          expr.evaluate({"<": [{"var": "me.nope"}, 3]}, CTX) is False
          and expr.evaluate({">=": [3, 3]}, CTX) is True
          and expr.evaluate({"!=": [{"var": "me.nope"}, 1]}, CTX) is False)
    check("A7 in／nin／contains",
          expr.evaluate({"in": ["c_cat", {"var": "me.claims"}]}, CTX) is True
          and expr.evaluate({"nin": ["c_x", {"var": "me.claims"}]}, CTX) is True
          and expr.evaluate({"contains": ["abc", "b"]}, CTX) is True
          and expr.evaluate({"contains": [{"var": "me.claims"}, "c_cat"]}, CTX) is True)
    check("A8 算術：+ 可串字串、- * / % min max、abs／floor",
          expr.evaluate({"+": [1, 2, 3]}, CTX) == 6
          and expr.evaluate({"+": ["a", "b"]}, CTX) == "ab"
          and expr.evaluate({"/": [7, 2]}, CTX) == 3.5
          and expr.evaluate({"%": [7, 3]}, CTX) == 1
          and expr.evaluate({"min": [3, 1, 2]}, CTX) == 1
          and expr.evaluate({"floor": -1.2}, CTX) == -2)
    try:
        expr.evaluate({"/": [1, 0]}, CTX)
        check("A9 除以 0 會叫（ExprError，不是默默回 0）", False)
    except expr.ExprError:
        check("A9 除以 0 會叫（ExprError，不是默默回 0）", True)
    check("A10 聚合：len／count／sum／any／all／coalesce／concat／select",
          expr.evaluate({"len": {"var": "me.claims"}}, CTX) == 1
          and expr.evaluate({"sum": [1, 2, 3]}, CTX) == 6
          and expr.evaluate({"any": [0, 2]}, CTX) is True
          and expr.evaluate({"all": [1, 0]}, CTX) is False
          and expr.evaluate({"coalesce": [None, None, 5]}, CTX) == 5
          and expr.evaluate({"concat": ["a", None, "b"]}, CTX) == "ab"
          and expr.evaluate({"select": [1, "yes", "no"]}, CTX) == "yes")
    check("A11 容器走訪：values／keys／pluck（「有人站上最頂」的寫法）",
          expr.evaluate({"in": ["hyakki_f5", {"pluck": [{"values": {"var": "seats"}}, "node"]}]},
                        CTX) is True
          and expr.evaluate({"keys": {"var": "me.shown_to"}}, CTX) == ["npc1"]
          and expr.evaluate({"len": {"values": {"var": "seats"}}}, CTX) == 2)
    node = {"and": [
        {"in": ["c_x", {"var": "me.claims"}], "why": {"need_claims": "c_x",
                                                     "you_have": {"var": "me.claims"}}},
        {"in": [{"var": "me.node"}, ["hyakki_f1"]], "why": {"need_node": "hyakki_f1",
                                                           "you_are": {"var": "me.node"}}}]}
    ok, detail = expr.evaluate_detail(node, CTX)
    check("A12 失敗說明：只收決定成敗那一側的 why（且值已求值）",
          ok is False and detail.get("need_claims") == "c_x"
          and detail.get("you_have") == ["c_cat"] and "need_node" not in detail,
          f"{detail}")
    ok2, det2 = expr.evaluate_detail({"in": ["hyakki_f5", {"pluck": [{"values": {"var": "seats"}},
                                                                  "node"]}]}, CTX)
    check("A13 過了就不帶說明", ok2 is True and det2 == {})
    errs = expr.validate({"nope": [1, 2]})
    errs2 = expr.validate({"var": "3bad..path"})
    errs3 = expr.validate({"<": [1]})
    errs4 = expr.validate({"and": [{"in": ["a", {"var": "ok"}]}]})
    check("A14 靜態檢查：未知算子／壞路徑／參數太少都當場叫",
          bool(errs) and bool(errs2) and bool(errs3) and errs4 == [],
          f"{errs[:1]} {errs2[:1]} {errs3[:1]}")
    deep = {"not": 1}
    for _ in range(expr.MAX_DEPTH + 2):
        deep = {"not": deep}
    check("A15 深度超限會被擋（作者寫出無窮遞迴長相時）", bool(expr.validate(deep)))
    check("A16 explain 印得出來（文件與除錯用）",
          "pluck" in expr.explain({"in": ["hyakki_f5",
                                          {"pluck": [{"values": {"var": "seats"}}, "node"]}]}))

    print("== B. 劇本模組（scenario.py）==")
    tmp0, cfg0 = mk_home(with_module=False)
    check("B1 沒有 module.json ⇒ load 回 None（舊劇本照舊跑）",
          scenario.Scenario.load(tmp0) is None)
    shutil.rmtree(tmp0, ignore_errors=True)

    tmp, cfg = mk_home()
    t = mk_table(tmp, cfg)
    try:
        mod = t.module
        check("B2 真劇本 module.json 載入且 validate 全過（真表起桌不炸）",
              mod is not None and mod.validate() == [])
        check("B3 五條願望都有 AST；判據與 goals.json 解耦（goals.json 已無 required）",
              all(mod.requirement(g) is not None for g in ("g_blade", "g_record", "g_ember",
                                                           "g_route", "g_stand"))
              and all("required" not in g for g in
                      json.load(open(os.path.join(tmp, "goals.json"), encoding="utf-8"))["goals"]))
        ready = {}
        for sid in t.state["seats"]:
            ready[sid] = t._check_goal(sid, "g_blade")[0]
        check("B4 真表上跑 AST 判據不炸、回布林", all(isinstance(v, bool) for v in ready.values()),
              f"{ready}")
        gs = mod.goal_ok(t, "p1", "g_nonexistent")
        check("B5 沒有 AST 的願望 ⇒ 回 None（呼叫端才退回舊判據）", gs is None)

        check("B6 validate 抓到「作者寫壞」的情形（未知算子／空 then／未知句庫鍵）",
              bool(scenario.Scenario({"triggers": [{"id": "x", "then": []}]}).validate())
              and bool(scenario.Scenario({"requirements": {"g": {"expr": {"nope": 1}}}}).validate())
              and bool(scenario.Scenario({"triggers": [{"id": "t", "then": [
                  {"emit": {"type": "say", "text_key": "no.such.key"}}]}]}).validate()))
        base = mod.difficulty("normal")["dc_mod"]
        check("B7 難度預設：normal＝0（＝行為與以前一致）、relaxed／tight＝∓1",
              base == 0 and mod.difficulty("relaxed")["dc_mod"] == -1
              and mod.difficulty("tight")["dc_mod"] == 1)
        _t8tmp, _t8cfg = mk_home(copy_data=False)          # 新局（事件流裡沒有 difficulty_set）
        _t8 = mk_table(_t8tmp, _t8cfg)
        check("B8 沒設難度時 difficulty_mod()＝0（新局；難度一旦設過會隨事件流留存）",
              _t8.difficulty_mod() == 0 and _t8.state.get("difficulty") is None)
        shutil.rmtree(_t8tmp, ignore_errors=True)
        t.director = None      # 這一組測「模組機制」：先把 L3 導演關掉（免得它接管難度）
        t._emit("difficulty_set", public=False, name="relaxed", by="test")
        check("B9 設了 relaxed ⇒ DC 修正 −1，且進 state（L3 的落腳點）",
              t.difficulty_mod() == -1 and t.state["difficulty"] == "relaxed")

        t._emit("boss_defeated", public=True, seat="p1", node="hyakki_f6")
        # 這裡斷言的是**整體不變式**（結局成立且只記一次）：示範劇本的 e_win 判據＝`world.event.type == boss_defeated`。
        end_ev = [e for e in t.events if e.get("type") == "ending"]
        check("B10 結局：被判到 e_win（win），只記一次",
              len(end_ev) == 1 and end_ev[0]["id"] == "e_win"
              and end_ev[0]["kind"] == "win" and t.state["ended"] == "e_win",
              f"n={len(end_ev)} ended={t.state['ended']!r}")
        t._emit("boss_defeated", public=True, seat="p2", node="hyakki_f6")
        check("B11 已經結局了就不會再判第二個",
              len([e for e in t.events if e.get("type") == "ending"]) == 1)

        # B10b：反面——新桌、不 emit boss_defeated：一筆中性事件**不該**把結局判出來
        #   （結局只在判據成立時落地，不會被無關事件誤觸）。
        _tmp9, _cfg9 = mk_home()
        _t9 = mk_table(_tmp9, _cfg9)
        try:
            _t9._emit("say", public=True, seat="p1", text="（中性事件）")
            check("B10b 判據不成立 ⇒ 中性事件不會誤判結局",
                  not _t9.state.get("ended"), f"ended={_t9.state.get('ended')!r}")
        finally:
            shutil.rmtree(_tmp9, ignore_errors=True)
        check("B12 觸發／結局都不動 replay（事件溯源）",
              bool(t.replay_check().get("replay_matches")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("== C. 觸發跑到定點 ==")
    mod_json = {
        "meta": {"id": "trigger_fixture"},
        "triggers": [
            {"id": "t_hello", "on": "say", "when": {"==": [{"var": "world.event.text"}, "hi"]},
             "then": [{"fact_set": {"key": "greeted", "value": True}},
                      {"emit": {"type": "world_note", "public": False, "text": "打了招呼"}}],
             "once": True},
            {"id": "t_chain", "on": "world_note",
             "then": [{"fact_set": {"key": "chain", "value": {"+": [1, 1]}}}]},
            {"id": "t_ending", "on": "world_note",
             "then": [{"ending": {"id": "e_test", "kind": "neutral"}}]},
            {"id": "t_preset", "on": "fact_set", "when": {"==": [{"var": "facts.greeted"}, True]},
             "then": [{"difficulty_set": {"name": "tight"}}], "once": True},
        ],
        "difficulty_presets": {"tight": {"dc_mod": 1}},
    }
    tmp2, cfg2 = mk_home(module=mod_json)
    t2 = mk_table(tmp2, cfg2)
    try:
        t2._emit("say", public=True, seat="p1", text="hi")
        typs = [e.get("type") for e in t2.events]
        check("C1 on／when 命中 ⇒ effects 全部執行（fact_set ＋ emit）",
              t2.state["facts"].get("greeted") is True and "world_note" in typs)
        check("C2 連鎖：trigger 產生的新事件又踩到別的 trigger（跑到定點）",
              t2.state["facts"].get("chain") == 2)
        check("C3 ending effect 判到結局", t2.state["ended"] == "e_test")
        check("C4 difficulty_set effect 生效", t2.state["difficulty"] == "tight"
              and t2.difficulty_mod() == 1)
        check("C5 once：蓋章後同一觸發不再跑", t2.state["facts"].get("_fired.t_hello") is True)
        n2 = len(t2.events)
        t2._emit("say", public=True, seat="p1", text="hi")
        check("C6 once 的第二次不重複（沒有新的 greeted 事件）",
              len([e for e in t2.events[n2:] if e.get("type") == "world_note"]) == 0)
        t2._emit("say", public=True, seat="p1", text="別的")
        check("C7 when 不成立就不跑", len([e for e in t2.events[n2:] if e.get("type") == "world_note"]) == 0)
        check("C8 觸發寫進去的狀態全部來自事件 ⇒ replay 一致",
              bool(t2.replay_check().get("replay_matches")))
        check("C9 difficulty_set 指到不存在的預設 ⇒ 當場 ModuleError（不靜默）",
              _raises(lambda: t2.module._apply_effect(t2, {"difficulty_set": {"name": "nope"}},
                                                      t2.module.ctx(t2)),
                      scenario.ModuleError))
        check("C10 沒有 module 的桌子照舊跑（module is None ⇒ 不跑觸發、不炸）",
              _module_none_ok())
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)

    print("\n== D. 劇本自檢器（tools/check_campaign.py；作者入口）==")
    d1 = tempfile.mkdtemp(prefix="ck-")
    try:
        # D1 回歸：**抽象桌（沒有 board.json）不是錯**。2026-10-06 踩到——自檢器無條件讀
        #    board.json ⇒ 抽象桌劇本被誤報「board.json: JSON 讀不動」，作者會以為一定要做棋盤。
        _write_campaign(os.path.join(d1, "abs"), board=False)
        e, _w = _check_campaign(os.path.join(d1, "abs"))
        check("D1 抽象桌（無 board.json）⇒ 0 error（回歸）", not e, f"{e}")

        _write_campaign(os.path.join(d1, "ok"))
        e, _w = _check_campaign(os.path.join(d1, "ok"))
        check("D2 有棋盤的正常劇本 ⇒ 0 error", not e, f"{e}")

        _write_campaign(os.path.join(d1, "edge"), bad_edge=True)
        e, _w = _check_campaign(os.path.join(d1, "edge"))
        check("D3 邊指向不存在的節點 ⇒ error", any("不存在的節點" in x for x in e), f"{e}")

        _write_campaign(os.path.join(d1, "sup"), bad_support=True)
        e, _w = _check_campaign(os.path.join(d1, "sup"))
        check("D4 碎片 supports 指到不存在的結論 ⇒ error", any("不在 clues.claims" in x for x in e), f"{e}")

        _write_campaign(os.path.join(d1, "orph"), orphan_req=True)
        e, _w = _check_campaign(os.path.join(d1, "orph"))
        check("D5 requirements 對不到任何願望 ⇒ error（打錯字不靜默）",
              any("對不到 goals.json" in x for x in e), f"{e}")

        _write_campaign(os.path.join(d1, "nomod"), no_module=True)
        e, _w = _check_campaign(os.path.join(d1, "nomod"))
        check("D6 沒有 module.json ⇒ 只是 warn（legacy 分支仍可跑）",
              not e and any("module.json" in x for x in _w), f"errs={e} warns={_w}")

        # D7 機制表：**用引擎的 loader 讀一遍**——表寫壞（同節點同 id 兩列）從前只有起桌才叫，
        #    作者的自檢卻印「可以直接起桌」（2026-10-07 真的踩到：shop 表 rope 兩列 ⇒ 起桌當場炸）。
        _write_campaign(os.path.join(d1, "shop"), bad_shop=True)
        e, _w = _check_campaign(os.path.join(d1, "shop"))
        check("D7 機制表同節點同 id 兩列 ⇒ error（與起桌同一套判據）",
              any("機制表（shop）" in x for x in e), f"{e}")
    finally:
        shutil.rmtree(d1, ignore_errors=True)

    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0




def _write_campaign(d, *, board=True, bad_edge=False, bad_support=False, orphan_req=False,
                    no_module=False, bad_shop=False) -> None:
    """寫一份最小劇本目錄出來（自檢器的測試用；不碰任何真劇本）。"""
    os.makedirs(d, exist_ok=True)

    def J(name, obj):
        with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False)

    J("config.json", {"table_id": "ck", "scenario": "s", "seed": 1,
                      "packs": ({"enabled": ["ckworld"]} if bad_shop else None),
                      "rules": {"name": "r", "attributes": ["str"], "attr_range": [1, 2],
                                "attr_default": 1, "hp": {"base": 1}, "check": {"dc_default": 10},
                                "actions": ["move"]},
                      "turn": {"seconds": 60}})
    if bad_shop:
        # 作品層包：一張**同一節點同 id 兩列**的商店表（引擎載入時會叫；自檢器要抓得到）
        pk = os.path.join(d, "packs", "ckworld")
        os.makedirs(os.path.join(pk, "tables"), exist_ok=True)
        with open(os.path.join(pk, "pack.json"), "w", encoding="utf-8") as fh:
            json.dump({"id": "ckworld", "provides": {"tables": ["tables/shop.json"]}}, fh,
                      ensure_ascii=False)
        with open(os.path.join(pk, "tables", "shop.json"), "w", encoding="utf-8") as fh:
            json.dump({"kind": "shop", "rows": [
                {"id": "rope", "price": 2},
                {"id": "rope", "price": 3, "nodes": ["a"]}]}, fh, ensure_ascii=False)
    J("seats.json", {"seats": [{"seat": "gm", "kind": "gm"},
                               {"seat": "p1", "kind": "human", "character": "ch1", "goal": "g1"}]})
    J("goals.json", {"goals": [{"id": "g1", "text": "t", "owner_character": "ch1"}]})
    J("clues.json", {"claims": [{"id": "c1", "text": "t"}],
                     "fragments": [{"id": "f1", "text": "t",
                                    "supports": ["c9" if bad_support else "c1"]}]})
    if board:
        J("board.json", {"start_node": "a", "nodes": [
            {"id": "a", "jp": "A", "type": "city",
             "edges": [{"to": "nope" if bad_edge else "b", "kind": "road"}]},
            {"id": "b", "jp": "B", "type": "city", "edges": []}]})
    if not no_module:
        J("module.json", {"meta": {"id": "ck"},
                          "requirements": {("gX" if orphan_req else "g1"): {"expr": True}}})


def _check_campaign(d):
    """直接叫作者那支自檢器（同一套判據），回 (errs, warns)。"""
    import importlib.util
    # 重構後 tests/ 是子目錄：工具在 repo 根（`../tools`），不是 `tests/tools`
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "check_campaign.py")
    spec = importlib.util.spec_from_file_location("check_campaign_under_test", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.check(d)




def _raises(fn, exc) -> bool:
    try:
        fn()
        return False
    except exc:
        return True
    except Exception:                       # noqa: BLE001
        return False


def _module_none_ok() -> bool:
    tmp, cfg = mk_home(copy_data=False, with_module=False)
    try:
        t = mk_table(tmp, cfg)
        t.join(seat="p1", name="甲", kind="human", order=0)
        return t.module is None and t._emit("say", public=True, seat="p1", text="x")["seq"] > 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
