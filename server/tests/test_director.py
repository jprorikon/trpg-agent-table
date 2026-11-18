#!/usr/bin/env python3
"""L3 導演的自測（`director.py`）＋ L6「不准繞過 L2」的閘。

    python3 test_director.py

四組：
  A. **戒律（靜態）**：L3 不讀 state、不 import 專案模組、不讀時鐘／隨機／檔案網路；
     L6 的四個檔案不准出現 `.state[`（＝不准繞過 L2）。
  B. **指標**（`signals`）：主線距離／偏離全部從**事件流**算（可重放）。
  C. **提案**：命中政策 → 檔位＋理由；同檔位不重提；沒開導演就不說話。
  D. **整合**：真桌上跑一段 —— `difficulty_set` 由**內核代發**（`by=director`）、
     獎懲真的生效（DC／行動格）、只發這一種事件、`op=replay` 一致。
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo 根
sys.path.insert(0, HERE)
from trpg.core import layout as layout
import test_support as TS                                                          # noqa: E402
from trpg.rules import packs as packs                                                             # noqa: E402
CAMP = layout.must_locate_scenario(HERE, "hyakki_tower", "sannenme_no_onibarai")
DEF = layout.def_dir_of(CAMP)          # 定義層（WS-B：桌目錄裡只剩 config／runs）

from trpg.engine import director as director                                                          # noqa: E402
from trpg.engine import projection as projection                                                        # noqa: E402
from trpg.rules import scenario as scenario                                                          # noqa: E402
MODF = scenario.module_filename(DEF) or "scenario.json"   # 劇本規則資料的檔名（WS-B：新名優先，別硬編）
from trpg.core import store as store                                                             # noqa: E402
from trpg.engine import Table                                                 # noqa: E402

OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


def code_of(name: str) -> str:
    """只留程式碼（註解／docstring 不算）——閘擋的是 code，不是說明。"""
    src = TS.source_text(name)
    tree = ast.parse(src)
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            node.body = [ast.Pass()] + body[1:]
    return ast.unparse(tree)


def mk_module(policy=None, presets=None) -> dict:
    base = json.load(open(os.path.join(DEF, MODF), encoding="utf-8"))
    base = json.loads(json.dumps(base))
    if policy is not None:
        base["director"]["policy"] = policy
    if presets is not None:
        base["difficulty_presets"] = presets
    base["director"]["track"] = True          # 測試測的是「機制」：一律上膛
    return base


def mk_table(module=None):
    tmp = tempfile.mkdtemp(prefix="trpg_dir_")
    for f in ("board.json", "goals.json", "clues.json", "seats.json"):
        shutil.copy(os.path.join(DEF, f), os.path.join(tmp, f))
    _psrc = os.path.join(DEF, "packs")        # 劇本自帶的作品層包（沒帶 ⇒ 啟用清單解析不動）
    if os.path.isdir(_psrc):
        shutil.copytree(_psrc, os.path.join(tmp, "packs"))
        packs.seed_module_packs(os.path.dirname(_psrc), tmp)   # ＋模組層的包（rules.core 住模組 repo）
    data = module if module is not None else json.load(
        open(os.path.join(DEF, MODF), encoding="utf-8"))
    data = json.loads(json.dumps(data))
    if data.get("director"):                  # 測試一律上膛（真劇本檔案預設是關的）
        data["director"]["track"] = True
    json.dump(data, open(os.path.join(tmp, MODF), "w", encoding="utf-8"),
              ensure_ascii=False)
    cfg = layout.read_table_config(CAMP)
    # ⚠ 夾具隔離（2026-10-07 單一進程 + 子路徑）：這兩個是**部署專屬**的鍵，
    #   從真 config 複製進來會讓 temp 桌指到現役 hub／帶別人的前綴 ⇒ 一律清掉。
    cfg.pop("hub_base", None)
    cfg.pop("public_prefix", None)
    cfg["port"] = 0
    t = Table(home=tmp, cfg=cfg, store=store.FileStore(tmp), clock=lambda: 1000.0)
    for s, n in (("p1", "甲"), ("p2", "乙")):
        t.join(seat=s, name=n, kind="human", order=int(s[1]) - 1)
    return t, tmp


def play_flat(t, hands=0, *, idle=True, move=False):
    """假裝玩 `hands` 手（沒有推進；`idle`＝空手／超時收場）。"""
    for i in range(hands):
        t._emit("turn_started", public=True, seat="p1", index=i, deadline_at=2000, reason="test")
        t._emit("turn_ended", public=True, seat="p1", turn_seq=i + 1,
                reason="timeout" if idle else "action")
        if move:
            t._emit("move", public=True, seat="p1", frm="teckan", to="w_teckan")


def main() -> int:
    print("== A. 戒律（靜態）==")
    dc = code_of("director.py")
    check("A1 L3 看不到 state（沒有 state[／table.state）",
          "state[" not in dc and "table.state" not in dc)
    check("A2 L3 不 import 專案模組（只回提案，不碰引擎內部）",
          not any(f"import {m}" in dc for m in ("engine", "board", "semic", "projection",
                                                "presentation", "store", "scenario",
                                                "service", "wake")))
    check("A3 L3 不讀時鐘／不隨機／不碰檔案網路",
          not any(x in dc for x in ("import time", "random", "open(", "urllib", "socket",
                                    "os.environ")))
    l6 = {f: code_of(f) for f in ("service.py", "wake.py", "kick_sender.py", "web_api.py")}
    bad = [f for f, c in l6.items() if ".state[" in c or "table.state" in c]
    check("A4 L6 四個檔案都不准直接讀 state（禁繞過 L2）", not bad, f"違規={bad}")
    readers = {f: c for f, c in l6.items() if f != "kick_sender.py"}   # 送腳那支＝純出向 HTTP，不讀世界
    check("A5 L6 的讀者都是走讀出口拿資料的（adapter_view／view 至少用了一個）",
          all(("adapter_view" in c or ".view(" in c) for c in readers.values()))
    check("A7 真劇本檔案目前的 track 狀態會印出來（給人看；不當斷言）",
          True, f"hyakki_tower director.track={json.load(open(os.path.join(DEF, MODF), encoding='utf-8'))['director']['track']}")
    check("A5b kick_sender.py 不讀世界（只把腳送出去）",
          "adapter_view" not in l6["kick_sender.py"] and ".state[" not in l6["kick_sender.py"])
    eng_raw = TS.source_text("engine.py")
    check("A6 L3 在引擎裡的鉤子**只**發 difficulty_set（L3 沒有別的手段）",
          'name=prop["preset"]' in eng_raw and 'by="director"' in eng_raw
          and "self.director.review(" in eng_raw)

    print("== B. 指標（signals：全部從事件流算）==")
    t, tmp = mk_table()
    try:
        play_flat(t, hands=6, idle=True, move=False)
        sig = t.director.signals(t.events, projection.adapter_view(t.state, t, now=1000.0))
        check("B1 hands 數得對（6 手）", sig["hands"] == 6, f"hands={sig['hands']}")
        check("B2 沒有任何推進 ⇒ progress=0、stall_hands=hands",
              sig["progress"] == 0 and sig["stall_hands"] == 6, f"{sig['progress']}/{sig['stall_hands']}")
        check("B3 idle（超時／空手）算得出來", sig["idle_hands"] >= 6 and sig["idle_streak"] >= 6)
        check("B4 move_lag＝距上次真正換地點的手數（沒動過 ⇒ ＝hands）", sig["move_lag"] == 6)
        t._emit("mob_killed", public=True, seat="p1", node="hyakki_f1", coins=1)
        sig2 = t.director.signals(t.events, projection.adapter_view(t.state, t, now=1000.0))
        check("B5 出現主線事件 ⇒ progress +1、stall_hands 歸零",
              sig2["progress"] == 1 and sig2["stall_hands"] == 0, f"{sig2['progress']}/{sig2['stall_hands']}")
        # 2026-10-21 拆景：指標只剩「計數＋輪」（`rounds`）；`scene_no`／`roundNo` 都不在了
        check("B6 指標不含任何私密欄位（只有計數與輪）",
              set(sig2) == {"hands", "progress", "stall_hands", "idle_hands", "idle_streak",
                            "recent_idle", "progress_recent", "window", "move_lag", "rounds",
                            "players", "agents", "down", "difficulty"}, f"{sorted(sig2)}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("== C. 提案 ==")
    STALL = [{"when": {">=": [{"var": "signals.stall_hands"}, 10]}, "preset": "tight",
              "why": "卡了 {stall_hands} 手沒有推進"},
             {"when": True, "preset": "normal", "why": "回到標準"}]
    t, tmp = mk_table(module=mk_module(policy=STALL))
    try:
        t.director = None                     # 先關掉鉤子：讓 playing 期間不提案，這一組自己餵
        play_flat(t, hands=11, idle=True)
        d = director.Director(t.module)
        prop = d.review(t.events, projection.adapter_view(t.state, t, now=1000.0), None)
        check("C1 卡 11 手（全超時）⇒ 提 tight，理由帶指標數字",
              prop and prop["preset"] == "tight"
              and any(ch.isdigit() for ch in prop["why"]), f"{prop}")
        check("C2 提案帶 metrics（GM 看得到為什麼）",
              prop and prop["metrics"]["stall_hands"] == 11)
        check("C3 已經是 tight ⇒ 不再提（不刷事件）",
              d.review(t.events, projection.adapter_view(t.state, t, now=1000.0), "tight") is None)
        off = director.Director(scenario.Scenario({"meta": {"id": "x"}}))
        check("C4 沒寫 director 區塊 ⇒ 永遠不說話（＝舊行為）", off.review(t.events, {}, None) is None)
        off2 = director.Director(scenario.Scenario({"director": {"track": False, "policy": [
            {"when": True, "preset": "tight"}]}}))
        check("C5 track:false ⇒ 不說話（導演可以關掉）", off2.review(t.events, {}, None) is None)
        relax = director.Director(scenario.Scenario(mk_module(policy=[
            {"when": True, "preset": "relaxed", "why": "測試賞"}])))
        check("C6 政策可以任意組合（賞的方向也走同一條路）",
              (relax.review(t.events, {}, "normal") or {}).get("preset") == "relaxed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("== D. 整合（真桌：獎懲真的生效、只發一種事件、可重放）==")
    t, tmp = mk_table(module=mk_module(policy=[
        {"when": {">=": [{"var": "signals.stall_hands"}, 5]}, "preset": "tight",
         "why": "卡了 {stall_hands} 手"}]))
    try:
        n0 = len(t.events)
        play_flat(t, hands=8, idle=True)
        made = [e for e in t.events[n0:] if e.get("type") == "difficulty_set"]
        check("D1 導演在跑到定點時被叫到，內核代發 difficulty_set（by=director）",
              len(made) == 1 and made[0].get("by") == "director"
              and made[0].get("name") == "tight", f"{made[-1] if made else None}")
        check("D2 罰：DC +1（檢定真的變難）", t.difficulty_mod() == 1)
        check("D3 罰：行動格 −1（真的少一格）",
              t.difficulty_act_mod() == -1
              and t._round_limit("p1", "act") == max(1, t.turn_action_budget() - 1),
              f"limit={t._round_limit('p1', 'act')} base={t.turn_action_budget()}")
        check("D4 導演只發 difficulty_set 這一種事件（沒有偷改別的東西）",
              {e.get("type") for e in t.events[n0:]} <=
              {"turn_started", "turn_ended", "difficulty_set"})
        check("D5 導演寫進去的東西全部來自事件 ⇒ replay 一致",
              bool(t.replay_check().get("replay_matches")),
              f"diff={list((t.replay_check().get('diff') or {}))[:3]}")
        t.director = None                     # 關掉導演：這一題只驗「賞」的機制（免得導演立刻又收緊）
        t._emit("difficulty_set", public=False, name="relaxed", by="test")
        check("D6 賞：DC −1、行動格 +1", t.difficulty_mod() == -1
              and t.difficulty_act_mod() == 1
              and t._round_limit("p1", "act") == t.turn_action_budget() + 1,
              f"limit={t._round_limit('p1', 'act')}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("== E. 政策驗證（作者寫錯要當場叫）==")
    d1 = director.Director(scenario.Scenario(mk_module(policy=[
        {"when": True, "preset": "no_such_preset"}])))
    d2 = director.Director(scenario.Scenario(mk_module(policy=[
        {"when": {"==": [{"var": "me.node"}, "x"]}, "preset": "tight"}])))
    d3 = director.Director(scenario.Scenario(mk_module(policy=[
        {"when": True, "preset": "tight", "why": "{nope_metric}"}])))
    d4 = director.Director(scenario.Scenario(mk_module(policy=[
        {"when": {"nope": 1}, "preset": "tight"}])))
    check("E1 未知難度預設被擋", bool(d1.validate()), d1.validate()[:1])
    check("E2 L3 只看得到 signals.*（偷看 me.*／world.* 被擋）", bool(d2.validate()), d2.validate()[:1])
    check("E3 why 的未知指標被擋", bool(d3.validate()), d3.validate()[:1])
    check("E4 AST 壞掉被擋", bool(d4.validate()), d4.validate()[:1])
    real = director.Director(scenario.Scenario.load(DEF))
    check("E5 真劇本的政策全合法", real.validate() == [], real.validate()[:2])
    check("E6 真劇本：三檔難度都有 dc_mod 與 act_mod（獎懲對稱）",
          all(isinstance(real.module.difficulty(n).get(k), (int, float))
              for n in ("relaxed", "normal", "tight") for k in ("dc_mod", "act_mod")))

    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
