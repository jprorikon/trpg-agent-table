#!/usr/bin/env python3
"""**判了結局之後，桌子要停**（2026-10-07 設計者抓到：「局收尾了為什麼還在倒數」「回合輪人也沒停止」）。

    python3 test_ending_stop.py

病情：`state["ended"]` 有值（`ending` 事件判過）但**沒有任何地方看它**——
`pacing.tick()` 照樣超時換手、節拍器照樣每回合踢一次 agent、`act()` 照樣能動、
投影也**沒露** `ended`（畫面上完全看不出這一局已經結束）。實測事件流裡累積了 406 筆 `kick`。

修法（與「暫停」同一立場：**凍牌面與時間軸，不凍對話**）：
  E1 判了結局 ⇒ `tick()` 不再換手／不再超時代打。
  E2 判了結局 ⇒ `act()`（含 `end_turn`／`cast`）被拒，`say`／`tell` 照常。
  E3 投影露 `ended`（玩家視圖 ＋ L6 快照）⇒ 前端才有東西可以顯示「本局已結束」。
  E4 判了結局 ⇒ 不自動開局（`_maybe_autostart` 停）。
  E5 沒判結局 ⇒ 行為一字不變（對照組）。
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

from trpg.engine import projection as projection                                                        # noqa: E402
from trpg.core import store as store                                                             # noqa: E402
from trpg.engine import Table                                                 # noqa: E402

OK, FAIL = [], []
SPEC = ["d20core", "board", "semic", "mode.turn", "wound", "rules.core"]


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


def mk_home(spec=SPEC):
    tmp = tempfile.mkdtemp(prefix="trpg_end_")
    packs.seed_module_packs(DEF, tmp)   # 模組層的包（rules.core 住模組 repo；臨時桌推不出模組）
    for f in ("board.json", "goals.json", "clues.json", "seats.json", MODF):
        shutil.copy(os.path.join(DEF, f), os.path.join(tmp, f))
    cfg = layout.read_table_config(CAMP)
    # ⚠ 夾具隔離（2026-10-07 單一進程 + 子路徑）：這兩個是**部署專屬**的鍵，
    #   從真 config 複製進來會讓 temp 桌指到現役 hub／帶別人的前綴 ⇒ 一律清掉。
    cfg.pop("hub_base", None)
    cfg.pop("public_prefix", None)
    cfg["port"] = 0
    cfg["packs"] = {"enabled": list(spec)}
    with open(os.path.join(tmp, "config.json"), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False)
    return tmp, cfg


def mk_table(tmp, cfg):
    return Table(home=tmp, cfg=cfg, store=store.FileStore(tmp), clock=lambda: 1000.0)


def seat_up(t, sid, name):
    return t.join(seat=sid, name=name, kind="human",
                  attrs={"str": 3, "agi": 3, "wil": 3, "luck": 2})


def main() -> int:                                                        # noqa: C901
    tmp, cfg = mk_home()
    try:
        t = mk_table(tmp, cfg)
        seat_up(t, "p1", "カガリ")
        seat_up(t, "p2", "シズク")
        # ★2026-10-21（拆景）：沒有「景」這一層了 ⇒ 人齊**就**開第一輪（倒數從這裡算）。
        #   原本的兩條是「入座不開局／開景才開局」，拆景後合成一條語意。
        check("E0a-1 兩席入座 ⇒ 自動開第一輪（倒數從這裡算；不再等 GM 開景）",
              bool(t.state.get("turn")) and not (t.state.get("semi") or {}).get("scene"),
              f"turn={t.state.get('turn')}")
        cur0 = dict(t.state["turn"] or {})
        far = 10 ** 7
        out = t.tick(now=far)                      # 沒判結局：超時 ⇒ 換手（對照組）
        check("E5 沒判結局 ⇒ tick 照舊換手（行為一字不變）",
              len(out) >= 1 and (t.state["turn"] or {}).get("seq") != cur0.get("seq"),
              f"{[e.get('type') for e in out]}")
        # 判結局（＝引擎的 `ending` 事件；這裡直接發，模擬劇本判到）
        t._emit("ending", public=True, id="e_test", kind="win", by="endings")   # 引擎那條路＝公開
        check("E0 判了結局 ⇒ state.ended 有值", t.state.get("ended") == "e_test")
        cur1 = dict(t.state["turn"] or {})
        out2 = t.tick(now=far * 2)
        check("E1 判了結局 ⇒ tick 不再換手／不再超時代打",
              out2 == [] and (t.state["turn"] or {}).get("seq") == cur1.get("seq"),
              f"out={[e.get('type') for e in out2]}")
        err = ""
        try:
            t.act(seat="p1", action="check", attr="wil", note="after-ending")
        except Exception as exc:                                       # noqa: BLE001
            err = str(exc)
        check("E2a 判了結局 ⇒ act() 被拒（訊息說明「已結束」並指出對話照常）",
              "結束" in err and "say" in err, err[:70])
        _say = t.say(seat="p1", text="（善後）辛苦了")
        check("E2b 對話照常（say 不受影響；跟暫停同一立場）",
              _say.get("type") == "say", f"{_say.get('type')}")
        v = t.view(seat="p1")
        av = t.adapter_view()
        check("E3 投影露 ended（玩家視圖 ＋ L6 快照）⇒ 前端有東西可顯示",
              v.get("ended") == "e_test" and av.get("ended") == "e_test",
              f"view={v.get('ended')} adapter={av.get('ended')}")
        check("E3b 判了結局 ⇒ 倒數凍在 0（顯示也不能還在跑）",
              (v.get("turn") or {}).get("seconds_left", None) == 0,
              f"{(v.get('turn') or {}).get('seconds_left')}")
        # ★3 結局收尾：公開的 `ending`（日誌看得到）＋結局卡要用的 info／summary
        _end_ev = [e for e in t.events if e.get("type") == "ending"]
        check("E7a `ending` 事件公開（這一局到此為止看得見）",
              _end_ev and _end_ev[-1].get("public") is True, f"{_end_ev[-1].get('public') if _end_ev else None}")
        check("E7b 投影給結局卡：`ended_info`（id／kind／名字）＋`summary`（手數／各席戰果）",
              (v.get("ended_info") or {}).get("id") == "e_test"
              and (v.get("ended_info") or {}).get("kind") == "neutral"   # e_test 不在 module ⇒ 退回 neutral
              and isinstance(v.get("summary"), dict)
              and v["summary"].get("turns", 0) >= 1
              and len(v["summary"].get("seats") or []) >= 2,
              f"info={v.get('ended_info')} seats={len((v.get('summary') or {}).get('seats') or [])}")
        _bi = projection._ending_info(t, {"ended": "e_retreat"})
        check("E7c 結局名字從劇本的 `endings[]` 查（不是新 state 欄位）",
              _bi.get("kind") == "neutral" and _bi.get("name_jp") == "今日は帰る", f"{_bi}")
        _pv = projection.project(t.state, t, viewer="p1", now=1000.0)
        check("E7d 兩條出口一致（view() 與 project() 都帶 ended_info／summary）",
              (_pv.get("ended_info") or {}).get("id") == "e_test"
              and isinstance(_pv.get("summary"), dict))
        t.state["turn"] = None                     # 就算回合被清掉，也不該自動開局
        t._maybe_autostart()
        check("E4 判了結局 ⇒ 不自動開局",
              t.state.get("turn") is None)
        check("E6 判了結局後 `tick()` 不寫任何事件（時間軸真的停住）",
              t.tick(now=far * 3) == [])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
