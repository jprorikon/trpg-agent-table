#!/usr/bin/env python3
"""分層切刀的自測：把「L0 只裁決、L2 是唯一讀出口、L4 是唯一句庫」變成**機械判據**。

    python3 test_layers.py

四組：
  A. **L2 投影的安全與純度**（行為）：回傳物件不與 state 共用（改它不動桌子）、
     同一個 `now` 兩次投影逐字相同（純函數）、`scrub` 一律回新物件。
  B. **靜態紀律**（讀原始碼）：哪些層不准 import 什麼、不准出現哪些字（投影不讀時鐘、
     句庫不讀 state、L0 不再產句子）。
  C. **行為不變的金標**（搬移沒改字）：句庫渲染的每一句 == 舊版原文。
  D. **L0 的邊界值（第 3 刀）**：時鐘與 sink 都是**注入**的——假時鐘能整條換掉時間軸
     （暫停/順延都聽它）、裸桌不建任何檔（預設 MemoryStore）、注入 FileStore 就落檔、
     兩顆 sink 語意一致、engine.py 沒有 open(/os./sys.、`time.` 只在預設時鐘那一處。

改動這三層時，這一支就是「下一個人不要把它長回去」的閘（設計者 2026-10-06 拍的分層）。
"""
from __future__ import annotations

import ast
import json
import os
import re
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

from trpg.engine import presentation as presentation                                                       # noqa: E402
from trpg.engine import projection as projection                                                         # noqa: E402
from trpg.rules import scenario as scenario                                                           # noqa: E402
from trpg.core import store as store                                                              # noqa: E402
from trpg.engine import MemoryStore, Table                                     # noqa: E402
from support_fixture import seed_run  # noqa: E402  現役桌可能已退役 ⇒ 從歸檔／現場造一局

OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


def mk_table(*, clock=None, file_store=True, copy_data=True):
    """拿真劇本的資料在 temp home 起一張桌（不碰線上桌）。

    `file_store=False` ⇒ **裸桌**（sink 用內核的 `MemoryStore`，一個檔都不建）；
    `clock=callable` ⇒ 換掉注入的時鐘；`copy_data=False` ⇒ 不帶既有事件流（新局）。
    """
    tmp = tempfile.mkdtemp(prefix="trpg_layers_")
    for f in ("board.json", "goals.json", "clues.json", "seats.json"):
        shutil.copy(os.path.join(DEF, f), os.path.join(tmp, f))
    _psrc = os.path.join(DEF, "packs")        # 劇本自帶的作品層包（沒帶 ⇒ 啟用清單解析不動）
    if os.path.isdir(_psrc):
        shutil.copytree(_psrc, os.path.join(tmp, "packs"))
        packs.seed_module_packs(os.path.dirname(_psrc), tmp)   # ＋模組層的包（rules.core 住模組 repo）
    if copy_data:
        seed_run(CAMP, tmp)                             # 現役那一局（一局一檔）
    cfg = layout.read_table_config(CAMP)
    # ⚠ 夾具隔離（2026-10-07 單一進程 + 子路徑）：這兩個是**部署專屬**的鍵，
    #   從真 config 複製進來會讓 temp 桌指到現役 hub／帶別人的前綴 ⇒ 一律清掉。
    cfg.pop("hub_base", None)
    cfg.pop("public_prefix", None)
    cfg["port"] = 0
    return Table(home=tmp, cfg=cfg, clock=clock,
                 store=store.FileStore(tmp) if file_store else None), tmp


def uses_in_fn(name: str, pattern: str, fn: str) -> "tuple[int, int]":
    """(整份 code 的命中數, 指定函式內的命中數)——把「只准在這一處碰 X」寫成機械判據。"""
    text = code_of(name)
    inside = 0
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.FunctionDef) and node.name == fn:
            inside += len(re.findall(pattern, ast.get_source_segment(text, node) or ""))
    return len(re.findall(pattern, text)), inside


def src(name):
    return TS.source_text(name)


def code_of(name):
    """只留**程式碼**（註解與 docstring 不計）——靜態紀律要擋的是 code，不是說明。

    不這樣做的話，「投影不讀時鐘」這條會被自己的 docstring 裡那句「`now` 由呼叫端注入、
    以前用 `_now()`」擋掉——閘變成在擋說明文字，那就沒意義了。
    """
    tree = ast.parse(src(name))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            node.body = [ast.Pass()] + body[1:]
    return ast.unparse(tree)


def main() -> int:
    t, tmp = mk_table()
    try:
        print("== A. L2 投影：不共用、純、scrub 回新物件 ==")
        before = json.dumps(t.state, sort_keys=True, ensure_ascii=False)

        v = t.view(seat="p1")
        if v["log"]:
            v["log"].append({"seq": 999999, "type": "say", "text": "污染"})
        if v["seats"]:
            v["seats"][0]["name"] = "污染"
        if isinstance(v.get("board"), dict):
            v["board"].setdefault("can_go", []).append({"to": "污染"})
            v["board"]["mobs"]["__x__"] = 1
        if v.get("you"):
            v["you"]["attrs"]["str"] = 999
        if isinstance(v.get("settled"), dict):
            v["settled"]["__x__"] = 1
        check("A1 改投影（席位視圖）不會動到 state",
              json.dumps(t.state, sort_keys=True, ensure_ascii=False) == before)

        gm = t.view(gm=True)
        check("A2 GM 視圖的 full_state 不是 state 本人（也不是同一顆 dict）",
              gm["gm"]["full_state"] is not t.state)
        gm["gm"]["full_state"]["seats"]["p1"]["hp"] = -999
        gm["gm"]["full_state"]["log"].append({"seq": 111, "type": "say"})
        check("A3 改 full_state 不會動到 state",
              json.dumps(t.state, sort_keys=True, ensure_ascii=False) == before)

        seqs = [e["seq"] for e in gm["log"]]
        check("A4 GM 視圖的 log 沒有重號 seq（歷史 17 個重號 seq 只在原始檔裡）",
              len(seqs) == len(set(seqs)), f"{len(seqs)} 條 / {len(set(seqs))} 唯一")
        seqs2 = [e["seq"] for e in v["log"]]
        check("A5 席位視圖的 log 也沒有重號 seq", len(seqs2) == len(set(seqs2)))

        a = json.dumps(projection.project(t.state, t, viewer="p1", now=1000.0), sort_keys=True)
        b = json.dumps(projection.project(t.state, t, viewer="p1", now=1000.0), sort_keys=True)
        check("A6 同一個 now ⇒ 兩次投影逐字相同（投影是純函數）", a == b, f"len={len(a)}")

        def clockfree(obj):
            if isinstance(obj, dict):
                return {k: ("<t>" if k in ("now", "seconds_left", "paused_seconds") else clockfree(v))
                        for k, v in obj.items()}
            if isinstance(obj, list):
                return [clockfree(x) for x in obj]
            return obj

        c = json.dumps(clockfree(projection.project(t.state, t, viewer="p1", now=1000.0)),
                       sort_keys=True)
        d = json.dumps(clockfree(projection.project(t.state, t, viewer="p1", now=9999.0)),
                       sort_keys=True)
        check("A7 換 now 只有時鐘欄位不同（其餘欄位不受時間影響）", c == d)

        ev = {"seq": 1, "type": "fragment_granted", "public": True, "holder": "p2", "fid": "f_9"}
        check("A8 scrub 一律回新物件（不參照原事件）",
              projection.scrub_public(ev, "p2") is not ev
              and projection.scrub_public(ev, "p1") is not ev)
        check("A9 scrub：當事人留 fid、他人／匿名抹掉",
              projection.scrub_public(ev, "p2").get("fid") == "f_9"
              and "fid" not in projection.scrub_public(ev, "p1")
              and "fid" not in projection.scrub_public(ev, None))

        print("== B. 靜態紀律（讀原始碼）==")
        pj, pr, en = src("projection.py"), src("presentation.py"), src("engine.py")
        bd, sc = src("board.py"), src("semic.py")
        pjc, prc = code_of("projection.py"), code_of("presentation.py")
        check("B1 projection.py 不讀時鐘（沒有 import time／_now／datetime）",
              not any(x in pjc for x in ("import time", "_now", "datetime")))
        check("B2 projection.py 不碰網路／環境（urllib/socket/os.environ）",
              not any(x in pj for x in ("urllib", "socket", "os.environ")))
        check("B3 projection.py 沒有 self.（純函數；deps 是參數）", "self." not in pjc)
        check("B4 projection.py 沒有淺拷貝 dict(/list(（一律 own() 遞迴重建）",
              "dict(" not in pjc and "list(" not in pjc)
        check("B5 presentation.py 不 import 任何本專案模組（不回頭依賴 L0/L1/L2）",
              not any(f"import {m}" in prc for m in ("engine", "board", "semic", "projection")))
        check("B6 presentation.py 不讀 state、不碰時鐘／網路",
              "state[" not in prc and "self." not in prc and "import time" not in prc
              and "urllib" not in prc)
        check("B7 L0 不再產句子：engine.py 沒有「★…击破了 BOSS」這類成句",
              "击破了 BOSS" not in en and "百鬼に記憶を喰われた" not in en)
        check("B8 L1 的規則檔也不再自己拼敘事文案（搬去句庫）",
              "の主を討った" not in bd and "競合（" not in sc)
        check("B9 句庫是那些句子的家", "击破了 BOSS" in pr and "の主を討った" in pr)
        i = en.index("    def view(self, *, seat")
        j = en.index("    def _scrub_public(self, ev", i)
        thin = en[i:j].count(chr(10))
        check("B10 view() 是薄殼（本體 <= 20 行；投影不准長回來）", thin <= 20, f"{thin} 行")

        print("== C. 金標：搬移沒改一個字 ==")
        nm = lambda sid: {"p1": "カガリ", "p2": "シズク"}.get(sid or "", sid or "?")   # noqa: E731
        pl = lambda nid: {"n1": "鉄環市", "n2": "百鬼塔"}.get(nid or "", nid or "?")  # noqa: E731
        cases = [
            ([{"seq": 1, "type": "boss_defeated", "public": True, "seat": "p1", "node": "n2"}],
             ["★ カガリ 击破了 BOSS（百鬼塔）"]),
            ([{"seq": 2, "type": "memory_change", "public": True, "seat": "p2", "before": 60,
               "after": 52, "reason": "塔内滞在で記憶が薄れる"}],
             ["シズク 记忆 60→52（塔内滞在で記憶が薄れる）"]),
            ([{"seq": 3, "type": "move", "public": True, "seat": "p1", "frm": "n1", "to": "n2"}],
             ["カガリ 移动 鉄環市→百鬼塔"]),
            ([{"seq": 4, "type": "declare_won", "public": True, "seat": "p2", "node": "n2"}],
             ["★ 争夺揭晓：シズク 拿下 百鬼塔"]),
            ([{"seq": 5, "type": "fragment_granted", "public": True, "holder": "p1"}],
             ["一枚碎片落到 カガリ 手上（内容只有他看得到）"]),
            ([{"seq": 6, "type": "white_room", "public": True, "seat": "p1", "memory": 30}],
             ["⚠ カガリ 被百鬼吞掉记忆，吐回塔外（记忆回 30）"]),
            ([{"seq": 7, "type": "action_declared", "public": True, "seat": "p1", "action": "move"}],
             []),
        ]
        # 金標是**示範模組（百鬼塔）那一桌**的措辭：作品的字住模組的 text.json（夾具把它種進臨時桌）⇒ 用桌的句庫比
        sb = t.text_bank
        bad = []
        for evs, want in cases:
            got = presentation.render_log_tail(evs, name_of=nm, place_of=pl, bank=sb)
            if got != want:
                bad.append((want, got))
        check("C1 公開事件的「人話」行逐字不變（7 種事件）", not bad, str(bad)[:200])
        gold = {
            "boss.slain_by": ("「百鬼塔」の主を討った", {"place": "百鬼塔"}),
            "white_room.text": ("記憶が尽きる——百鬼に名を喰われかけ、気づけば塔の外の石畳に倒れていた。", {}),
            "white_room.reason": ("百鬼に記憶を喰われた（塔の外へ吐き出される）", {}),
            "entrance_found.text": ("瓦礫に紛れた塔の口を見つけた（口は毎回変わる）", {}),
            "boss_defeated.text": ("百鬼夜行の主を討った——最上階の鬼の宝櫃が開く", {}),
            "assimilated.text": ("塔の中で飲み食いした——鬼の側に一歩寄り、記憶が削られる", {}),
            "memory.tower_drain": ("塔内滞在で記憶が薄れる", {}),
            "memory.picked": ("「意味のある」記憶を拾った", {}),
            "memory.daily": ("鉄環市 で日常を過ごした（意味のある記憶）", {"place": "鉄環市"}),
            "memory.arcade": ("ゲームセンターで遊んだ（意味のある記憶）", {}),
            "memory.artifact_cost": ("神器「願い星のかけら」を得た代償（tier 4）", {"name": "願い星のかけら", "tier": 4}),
            "memory.item_effect": ("回復薬 の効果", {"item": "回復薬"}),
            "memory.tower_food": ("塔内饮食（一体化）", {}),
            "contest.rows": ("pray @ hyakki_f5 の競合（3 人）", {"kind": "pray", "node": "hyakki_f5", "n": 3}),
            "contest.left": ("勝ったが、その場にいない（宣言後に移動した）", {}),
            "timeout.defend_note": ("超时代打", {}),
        }
        miss = [(k, want, presentation.text(k, sb, **facts)) for k, (want, facts) in gold.items()
                if presentation.text(k, sb, **facts) != want]
        check("C2 寫進事件流的文案逐字不變（16 條）", not miss, str(miss)[:200])
        coined = ("百鬼", "銭", "宝櫃", "鉄環", "祓")          # 示範模組的造語（住它的 text.json）
        leak = [k for k, v in presentation.TEXT.items() if any(w in v for w in coined)]
        check("C2b 內核句庫只帶中性措辭（作品造語住模組的 text.json）", not leak, str(leak))
        check("C3 view.recent == 同一支句庫的輸出（不是第二份投影）",
              t.view(seat="p1")["recent"] == presentation.render_log_tail(
                  t.state["log"], name_of=t._name_of, place_of=t._place_of, limit=12, bank=sb))
        check("C4 op=replay 仍然一致（投影搬家沒動事件流）",
              bool(t.replay_check().get("replay_matches")))
        check("C5 events.jsonl 是真值來源：投影不寫檔（檔案大小前後不變）",
              os.path.getsize(layout.events_path(tmp)) > 0)

        print("== D. L0 邊界值：時鐘與 sink 都是注入的（第 3 刀）==")
        box = {"t": 1000.0}
        # copy_data=False：**不要複製線上事件流**——線上一旦剛好停在 `pacing_paused`，
        # `pause()` 會走 already 分支不發事件、這條就會讀到那筆真時間的舊事件而假紅
        # （2026-10-06 實際踩到：設計者把桌暫停著，這組就紅了）。本來要驗的是「時鐘注入」，
        # 不需要歷史 ⇒ 開一張新桌、自己鋪兩個席位與回合。
        t2, tmp2 = mk_table(clock=lambda: box["t"], copy_data=False)
        try:
            t2.join(seat="p1", name="甲", kind="human", order=0)
            t2.join(seat="p2", name="乙", kind="human", order=1)   # 兩席入座 ⇒ 自動開第一手
            box["t"] = 1000.0
            d0 = (t2.state.get("turn") or {}).get("deadline_at")
            t2.pause(reason="test")
            pv = t2.events[-1]
            check("D1 暫停事件聽注入的時鐘（paused_at／ts 都＝1000，不是真時間）",
                  pv.get("type") == "pacing_paused" and pv.get("paused_at") == 1000.0
                  and pv.get("ts") == 1000.0,
                  f"paused_at={pv.get('paused_at')} ts={pv.get('ts')}")
            box["t"] = 1014.0
            r = t2.resume(reason="test")
            d1 = (t2.state.get("turn") or {}).get("deadline_at")
            check("D2 恢復：凍掉的秒數＝假時鐘的差（14s）且死線只順延一次",
                  r["frozen_seconds"] == 14.0 and (d0 is None or abs(d1 - (d0 + 14.0)) < 1e-6),
                  f"frozen={r['frozen_seconds']} d0={d0} d1={d1}")
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)

        t3, tmp3 = mk_table(file_store=False, copy_data=False)
        try:
            check("D3 不給 store ⇒ 內核不建任何檔（預設 MemoryStore；data/ 不存在）",
                  not os.path.exists(os.path.join(tmp3, "data"))
                  and len(t3.events) == 1 and t3.events[0]["type"] == "table_created",
                  f"events={len(t3.events)} has_data_dir={os.path.exists(os.path.join(tmp3, 'data'))}")
            t3.join(seat="p1", name="甲", kind="human", order=0)
            t3.join(seat="p2", name="乙", kind="human", order=1)
            check("D4 裸桌照常能玩：事件只活在記憶體、replay 仍一致",
                  len(t3.events) >= 3 and bool(t3.replay_check().get("replay_matches"))
                  and not os.path.exists(os.path.join(tmp3, "data")),
                  f"events={len(t3.events)}")
        finally:
            shutil.rmtree(tmp3, ignore_errors=True)

        t4, tmp4 = mk_table(file_store=True, copy_data=False)
        try:
            t4.join(seat="p1", name="甲", kind="human", order=0)
            n4 = len(t4.events)
            t5 = Table(home=tmp4, cfg=t4.cfg, store=store.FileStore(tmp4))
            check("D5 注入 FileStore ⇒ 事件真的落檔、換一張桌載得回同一條流",
                  os.path.getsize(layout.events_path(tmp4)) > 0
                  and len(t5.events) == n4 and t5.replay_check().get("replay_matches"),
                  f"n={n4} n_reloaded={len(t5.events)}")
        finally:
            shutil.rmtree(tmp4, ignore_errors=True)

        tmp6 = tempfile.mkdtemp(prefix="trpg_sink_")
        try:
            for label, sk in (("MemoryStore", MemoryStore()), ("FileStore", store.FileStore(tmp6))):
                sk.append({"seq": 1, "type": "a"})
                off = sk.size()
                sk.append({"seq": 2, "type": "b"})
                ok = sk.replace_tail(off, {"seq": 2, "type": "b", "rejected": True})
                rows = sk.load()
                check(f"D6 {label}：append→replace_tail 就地覆寫尾行（不新增行、語意與另一顆一致）",
                      ok and len(rows) == 2 and rows[-1].get("rejected") is True
                      and rows[0].get("type") == "a",
                      f"ok={ok} rows={len(rows)} last={rows[-1] if rows else None}")
        finally:
            shutil.rmtree(tmp6, ignore_errors=True)

        enc = code_of("engine.py")
        check("D7 L0 不碰檔案／行程：engine.py 沒有 open( ／os. ／sys. ／subprocess",
              not any(x in enc for x in ("open(", "os.", "sys.", "subprocess")))
        tot, ins = uses_in_fn("engine.py", r"\btime\.", "_system_clock")
        check("D8 L0 只在預設時鐘那一處碰 time（其餘全走注入的 now()）",
              tot == ins and ins >= 1, f"total={tot} inside={ins}")
        check("D9 生產路徑確實把 FileStore 接上內核（service.py 是 L6 的組合點）",
              "store.FileStore(home" in src("service.py")     # 換局時會多帶 session=…（WS-E newRun）
              and "from trpg.core import store" in code_of("service.py"))
        print("== E. L1 資料化：規則住資料、引擎不認識任何人 ==")
        enc, scc = code_of("engine.py"), code_of("semic.py")
        exc = code_of("expr.py")
        check("E1 引擎／半合作**不再認識任何 NPC 的名字**（專屬知識住 module.json）",
              "tsurugi" not in enc and "tsurugi" not in scc
              and "ツルギ" not in scc)
        check("E2 戰役專屬的節點 id 不再寫死在程式裡（surface 判據住 L1）",
              not any(x in enc + scc for x in ("hyakki_f4", "hyakki_f5")))
        check("E3 expr.py 是純的：不讀時鐘／不隨機／不碰檔案或網路／不 import 專案模組",
              not any(x in exc for x in ("import time", "random", "open(", "urllib",
                                         "socket", "os.environ"))
              and not any(f"import {m}" in exc for m in ("engine", "board", "semic",
                                                         "projection", "presentation",
                                                         "scenario")))
        scy = code_of("scenario.py")
        check("E4 scenario.py 只吐事件、不直接改 state（事件溯源：replay 才會一致）",
              "state[" not in scy and ".state[" not in scy.replace("table.state.get", ""))
        check("E5 effect 白名單與實作一致（加效果只能從 EFFECT_KINDS 進）",
              all(k in scy for k in ("fact_set", "difficulty_set", "ending"))
              and "EFFECT_KINDS" in scy)
        check("E6 觸發只在最外層 _emit 之後跑（`_firing` 擋遞迴，不會觸發疊觸發炸開）",
              "_firing" in enc and scy.count("_firing") == 0)
        mod = scenario.Scenario.load(DEF)
        check("E7 真劇本的 module.json 全部合法（AST／effect／句庫鍵／預設名）",
              mod is not None and mod.validate() == [])
        check("E8 觸發／結局的**清單**住 L1（引擎只會問 module，不自帶任何一條）",
              "triggers" in (mod.data if mod else {}) and "endings" in (mod.data if mod else {})
              and '"triggers"' not in enc and '"endings"' not in enc
              and "triggers_for" in enc and "check_endings" in enc)

        print("== F. L3 導演／L6 適配器：讀出口與手段的紀律 ==")
        check("F1 玩家視圖**不含** ops（L6 的私密讀出口不外洩：wake_url／run_id…）",
              "ops" not in t.view(seat="p1") and "ops" not in t.view(seat=None)
              and "ops" not in t.view(gm=True))
        _ops = t.adapter_view()
        check("F2 adapter_view 存在且帶 L6 需要的東西（turn_key／seats.wake_url／run_id／paused）",
              all(k in _ops for k in ("turn_key", "run_id", "paused", "seats", "round_no",
                                      "last_gm_seq", "counts"))
              and all("wake_url" in s for s in _ops["seats"].values()))
        check("F3 ops 也不算規則（pending_actors 是問 L1 的方法，不在這裡重寫一遍）",
              "pending_actors" in projection.adapter_view(t.state, t, now=1000.0))
        check("F4 L3 檔（director.py）不讀 state、不 import 專案模組（只回提案）",
              not any(x in code_of("director.py") for x in ("state[", "table.state"))
              and not any(f"import {m}" in code_of("director.py")
                          for m in ("engine", "board", "semic", "projection", "presentation")))
        check("F5 難度是 L3 的**唯一**手段：獎懲都落在 difficulty（沒有別的寫入路徑）",
              "difficulty_set" in src("engine.py")
              and src("engine.py").count('_emit("difficulty_set"') == 1)
        check("F6 引擎的導演鉤子在跑定點的迴圈裡（不是另一個背景執行緒／第二條線）",
              "self.director.review(" in src("engine.py")
              and "def _run_triggers" in src("engine.py"))

        # ── G. 私訊喚醒的措辭閘（設計者 2026-10-06 實測：她以前把**私訊**回在公開頻道）──────────
        check("G1 私訊喚醒的 prompt 明說「不要用 table_say 回私訊」",
              "不要用 `table_say` 回私讯" in src("wake.py"))
        check("G2 私訊喚醒指定收件人（「只有你知道」那行帶「來自」）",
              "（来自 {s['from']}）" in src("wake.py"))
        check("G3 私訊喚醒仍然禁止宣告動作（回合制的順序靠這條維持）",
              "不要宣告动作" in src("wake.py"))

        # ── H. 機制包（packs/）：邊界宣告住 L1、包裡沒有程式碼、啟用清單凍進事件流 ──────────
        print("== H. 機制包（packs/）==")
        pkc = code_of("packs.py") + code_of("pack_kernel.py")
        check("H1 packs.py／pack_kernel.py 是純的（不讀時鐘／不隨機／不碰網路）＋**不讀 state**",
              not any(x in pkc for x in ("import time", "random", "urllib", "socket",
                                         "os.environ", "state[", "table.state")),
              f"len={len(pkc)}")
        check("H1b 分工：包本身住 packs.py、對內核的池子與核對住 pack_kernel.py（單向）",
              "KERNEL_EFFECTS: Tuple" not in code_of("packs.py")
              and "from trpg.rules.pack_kernel import" in src("packs.py")
              and "import packs" not in code_of("pack_kernel.py"))
        check("H2 包不含程式碼：packs/ 底下只有資料（pack.json／text.json／tables/*.json）",
              not [f for _r, _d, _fs in os.walk(os.path.join(HERE, "packs"))
                   for f in _fs if f.endswith((".py", ".pyc"))])
        from trpg.rules import packs as _pk                                                 # noqa: E402
        _reg = _pk.registry()
        _bad = []
        for _pid in _reg:
            _want = ["d20core"] if _pid != "d20core" else []
            try:
                _pk.resolve(_want + [_pid])
            except Exception as exc:                                        # noqa: BLE001
                _bad.append(f"{_pid}: {type(exc).__name__}: {exc}")
        check("H3 引擎層每個包都解析得動（與 d20core 一起；實作核對也過）",
              not _bad, str(_bad)[:200])
        check("H4 現役劇本的啟用清單合法（config.packs → resolve 不炸）",
              _pk.resolve((layout.read_table_config(CAMP) or {}).get("packs"),
                          home=DEF).enabled)      # 作品層包住在定義層
        check("H5 日誌白名單是**這一桌的**（per-instance log_types；常數仍 re-export 給舊呼叫端）",
              "self.log_types" in src("engine.py") and "log_types_for" in pkc
              and projection.VIEW_LOG_TYPES is not None and "VIEW_LOG_TYPES = projection." in src("engine.py"))
        # H6：只咬「載入時把這一桌可用的 effect 交給 L1」這件事，**不咬參數的寫法**
        # （2026-10-19 第二批给 Scenario.load 多加了 bank=…，舊的字串比對就假紅了）
        check("H6 引擎把「這桌可用的 effect」傳給 L1（載入時就叫，不拖到跑團中間）",
              re.search(r"Scenario\.load\(\s*home\s*,\s*allowed_effects=self\.allowed_effects",
                        src("engine.py")) is not None)
        check("H7 啟用清單凍進事件流（table_created 帶 ids＋hash）",
              "**self.packs.as_dict()" in src("engine.py") and "packs_hash" in pkc)
        cnd = code_of("conditions.py")
        # 讀**自己的** state 是機制的本分（board／semic 也一樣）；擋的是時鐘／網路／亂數不種子化
        check("H9 conditions.py 是純的：不讀時鐘／不碰網路／不碰別人的 state；亂數一律種子化",
              not any(x in cnd for x in ("import time", "datetime", "urllib", "socket",
                                         "os.environ", "table.state"))
              and cnd.count("random.") == cnd.count("random.Random("),
              f"random.*={cnd.count('random.')} Random={cnd.count('random.Random(')}")
        from trpg.rules import packs as _pk2                                                # noqa: E402
        check("H10 新機制檔有登記進 IMPL_SOURCES（否則包宣告它會被 verify_impl 擋掉）",
              any(x.endswith("conditions.py") for x in _pk2.IMPL_SOURCES))
        check("H11 條件事件的型別由包宣告（內核白名單不含它；啟用 wound 才進得去）",
              {"condition_set", "condition_tick"} <= set(_pk2.resolve(["d20core", "wound"]).events)
              and "condition_set" not in projection.VIEW_LOG_TYPES)
        check("H8 L6 不再自己讀一份節奏（以桌子為準；否則包覆寫 config 時會漂）",
              "self.pacing_mode: str = self.table.pacing_mode" in src("service.py")
              and 'cfg.get("pacing")' not in code_of("service.py"))

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
