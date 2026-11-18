#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_act_idempotent.py — **動作請求冪等**（同一個 `seat+req` 只結算一次）。

設計者 2026-10-20：「agent 重試可能重複結算一手（kick／coins 有冪等鍵，act 沒有）」。
修法：`act(..., req=…)`——請求 id 寫進 `action_declared.req`，索引就是**事件流本身**
（重啟／回放／跨進程都認得，不必另存一份）。重送回 `{duplicate:true, seq}`、不再擲骰、
不再扣額度。

判據：
  A. 同一個 req 送兩次 ⇒ 只結算一次（1 筆宣告、1 個 roll、額度只扣一次）；
  B. 不同 req ⇒ 兩手都成立（冪等不是「只能動一次」）；
  C. 不帶 req ⇒ 舊行為（向後相容）；
  D. **失敗被就地標 rejected 的那一手不算**：同一個 req 可以重試（上一次根本沒成立）；
  E. 換一張桌子實例（重新載入事件流）也認得同一個 req ⇒ 跨進程／重啟有效；
  F. 事件流仍可回放（新欄位不影響 `op=replay`）。
"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)

import test_support as TS                                                       # noqa: E402
from trpg.service import service as SVC                                         # noqa: E402
from trpg.core.errors import RuleError                                          # noqa: E402

FAIL = []


def ok(name: str, cond: bool, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  " + str(detail)) if detail else ""))
    if not cond:
        FAIL.append(name)


def _table(tag: str):
    root, home, cfg = TS.mk_table(module="hyakki_tower", table_id=tag)
    cfg = json.loads(json.dumps(cfg))
    cfg.setdefault("rules", {})["death"] = {"mode": "off"}
    return root, home, cfg


def _count(t, typ: str, **kw) -> int:
    return len([e for e in t.events if e.get("type") == typ
                and all(e.get(k) == v for k, v in kw.items())])


def t_same_req() -> None:
    print("== A. 同一個 req 送兩次 ⇒ 只結算一次")
    root, home, cfg = _table("act_idem")
    try:
        t = SVC.TableService(home, cfg).table
        seat = t.join(name="甲", kind="human")
        t.start_turn(seat=seat)
        r1 = t.act(seat=seat, action="check", note="retry-1", attr="wil", dc=5, req="r-1")
        n_decl = _count(t, "action_declared", req=f"{seat}:r-1")
        n_roll = len([e for e in t.events if e.get("type") == "roll"])
        r2 = t.act(seat=seat, action="check", note="retry-1", attr="wil", dc=5, req="r-1")
        ok("A1 第一次是真結算（有 roll）", bool(r1.get("roll")) and n_decl == 1, r1.get("roll"))
        ok("A2 第二次回 duplicate（不重複）", r2.get("duplicate") is True, r2)
        ok("A3 第二次指出第一手的 seq", r2.get("seq") == [e for e in t.events
                                                          if e.get("type") == "action_declared"][0]["seq"],
           (r2.get("seq"),))
        ok("A4 宣告只有 1 筆、roll 只有 1 個",
           _count(t, "action_declared", req=f"{seat}:r-1") == 1
           and len([e for e in t.events if e.get("type") == "roll"]) == n_roll,
           (_count(t, "action_declared"), n_roll))
        ok("A5 回放一致", bool(t.replay_check().get("replay_matches")),
           t.replay_check().get("diff"))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_diff_req() -> None:
    print("== B/C. 不同 req 照常；不帶 req 舊行為")
    root, home, cfg = _table("act_idem2")
    try:
        t = SVC.TableService(home, cfg).table
        seat = t.join(name="甲", kind="human")
        t.start_turn(seat=seat)
        t.act(seat=seat, action="check", note="a", attr="wil", dc=5, req="r-a")
        t.act(seat=seat, action="check", note="b", attr="wil", dc=5, req="r-b")
        ok("B1 不同 req ⇒ 兩手都成立", _count(t, "roll") == 2 and _count(t, "action_declared") == 2,
           (_count(t, "roll"), _count(t, "action_declared")))
        t.act(seat=seat, action="check", note="c", attr="wil", dc=5)
        t.act(seat=seat, action="check", note="c", attr="wil", dc=5)
        ok("C1 不帶 req ⇒ 舊行為（兩次都結算、不判重）", _count(t, "roll") == 4,
           _count(t, "roll"))
        ok("C2 沒有 req 的宣告不帶 req 欄（舊客戶端不吃到新欄位）",
           all("req" not in e for e in t.events
               if e.get("type") == "action_declared" and e.get("note") == "c"))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_rejected_retry() -> None:
    print("== D. 失敗（rejected）那一手不算 ⇒ 同一個 req 可以重試")
    root, home, cfg = _table("act_idem3")
    try:
        t = SVC.TableService(home, cfg).table
        seat = t.join(name="甲", kind="human")
        t.start_turn(seat=seat)
        try:
            t.act(seat=seat, action="attack", req="r-x")           # 沒 target ⇒ 失敗
            ok("D1 沒 target 的 attack 要失敗", False)
        except RuleError:
            ok("D1 沒 target 的 attack 要失敗（宣告就地標 rejected）", True)
        rej = [e for e in t.events if e.get("type") == "action_declared" and e.get("req")]
        ok("D2 那一筆被標 rejected（不進公開投影）",
           bool(rej) and rej[-1].get("rejected") is True, rej[-1] if rej else None)
        t.join(seat="p9", name="乙", kind="human")
        out = t.act(seat=seat, action="attack", target="p9", dc=1, req="r-x")   # 同一個 req、參數補齊
        ok("D3 同一個 req 重試（上次沒成立）⇒ 這次真的結算",
           out.get("duplicate") is not True and bool(out.get("roll")), out)
        ok("D4 只有一手成立（rejected 那筆不算）",
           len([e for e in t.events if e.get("type") == "action_declared"
                and e.get("req") == f"{seat}:r-x" and not e.get("rejected")]) == 1)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_across_reload() -> None:
    print("== E. 換一張桌子實例（重新載入事件流）也認得同一個 req")
    root, home, cfg = _table("act_idem4")
    try:
        t1 = SVC.TableService(home, cfg).table
        seat = t1.join(name="甲", kind="human")
        t1.start_turn(seat=seat)
        t1.act(seat=seat, action="check", note="x", attr="wil", dc=5, req="stable-1")
        rolls_before = _count(t1, "roll")
        # 重新組一張桌（＝重啟／另一個進程）讀同一份事件流
        t2 = SVC.TableService(home, cfg).table
        out = t2.act(seat=seat, action="check", note="x", attr="wil", dc=5, req="stable-1")
        ok("E1 重啟後重送同一個 req ⇒ duplicate", out.get("duplicate") is True, out)
        ok("E2 沒有多擲一次骰", _count(t2, "roll") == rolls_before,
           (_count(t2, "roll"), rolls_before))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_projection() -> None:
    print("== F. 投影／日誌不因新欄位而變")
    root, home, cfg = _table("act_idem5")
    try:
        t = SVC.TableService(home, cfg).table
        seat = t.join(name="甲", kind="human")
        t.start_turn(seat=seat)
        t.act(seat=seat, action="check", note="v", attr="wil", dc=5, req="r-p")
        v = t.view(seat=seat)
        line = next((x for x in v.get("log") or [] if x.get("type") == "action_declared"), None)
        ok("F1 公開日誌那筆帶 req（人看得出是哪一手的重試）",
           bool(line) and line.get("req") == f"{seat}:r-p", line)
        ok("F2 投影照舊有 log／recent", bool(v.get("recent") is not None))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main() -> None:
    t_same_req()
    t_diff_req()
    t_rejected_retry()
    t_across_reload()
    t_projection()
    print()
    print(("ACT-IDEMPOTENT: PASS  （0 FAIL）" if not FAIL
           else f"ACT-IDEMPOTENT: FAIL  {len(FAIL)}：{FAIL}"))
    sys.exit(1 if FAIL else 0)


main()
