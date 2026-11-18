#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**貨幣帶入的冪等**（review P1，2026-10-20）。

背景：`/api/enter` 每次進場都把 (帳號 × 模組) 錢包帶進局內。舊判據看
`DbAccounts.seats › wallet_carried`：
  * DB 走 `run_seat` 時那格**寫死 False** ⇒ 同一局進第二次就再帶一次；
  * JSON 後端則一桌只帶一次 ⇒ 新局反而不帶。
現在改用**這一局的事件流**當冪等鍵：同一局同一席最多一筆
`coin_grant reason="carry_over"`；換局（新 run）事件流是空的 ⇒ 新局會再帶一次。

驗：
  C1 同一局同一席：連續 3 次進場 ⇒ 只有 1 筆 carry_over、金額只加一次
  C2 不同席位互不影響
  C3 新局（新 table／新事件流）⇒ 會再帶一次
  C4 錢包 0 ⇒ 一筆都不發
  C5 併發的第二次進場（鎖內再判）⇒ 仍只有 1 筆
"""
from __future__ import annotations

import os
import sys
import threading

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from trpg.api.web.base import carry_over_wallet, _carry_over_done   # noqa: E402

OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


class FakeTable:
    def __init__(self):
        self.events = []
        self.lock = threading.RLock()

    def grant_coins(self, *, seat, amount, reason):
        self.events.append({"type": "coin_grant", "seat": seat,
                            "amount": int(amount), "reason": reason})
        return {"granted": int(amount)}


class FakeSvc:
    def __init__(self, table=None):
        self.table = table or FakeTable()


class FakeAcct:
    def __init__(self):
        self.fields = {}

    def set_seat_field(self, seat, key, value):
        self.fields[(seat, key)] = value


def carry_evs(table, seat="p1"):
    return [e for e in table.events
            if e.get("type") == "coin_grant" and e.get("reason") == "carry_over"
            and e.get("seat") == seat]


def main() -> int:
    print("== C1 同一局同一席連進 3 次 ==")
    svc, acct = FakeSvc(), FakeAcct()
    w = {"star_coins": 19}
    for _ in range(3):
        carry_over_wallet(svc, acct, "p1", w)
    check("C1a 只有 1 筆 carry_over", len(carry_evs(svc.table)) == 1, carry_evs(svc.table))
    check("C1b 只加一次金額（19）",
          sum(e["amount"] for e in carry_evs(svc.table)) == 19)
    check("C1c 相容欄 wallet_carried 也蓋了", acct.fields.get(("p1", "wallet_carried")) is True)

    print("== C2 不同席位互不影響 ==")
    carry_over_wallet(svc, acct, "p2", {"star_coins": 7})
    check("C2a p2 也有自己的一筆", len(carry_evs(svc.table, "p2")) == 1)
    check("C2b p1 仍只有一筆", len(carry_evs(svc.table, "p1")) == 1)

    print("== C3 新局（新事件流）會再帶一次 ==")
    svc2 = FakeSvc()
    carry_over_wallet(svc2, acct, "p1", w)
    check("C3a 新 run 帶一次", len(carry_evs(svc2.table)) == 1)
    check("C3b 新 run 的事件流是空的起點（沒有繼承上一局）",
          not _carry_over_done(FakeTable(), "p1"))

    print("== C4 錢包 0 ⇒ 不發事件 ==")
    svc3 = FakeSvc()
    carry_over_wallet(svc3, acct, "p1", {"star_coins": 0})
    check("C4a 0 元不帶、不留事件", svc3.table.events == [])

    print("== C5 併發（鎖內再判一次） ==")
    svc4 = FakeSvc()
    threads = [threading.Thread(target=carry_over_wallet,
                                args=(svc4, acct, "p1", w)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("C5a 8 個併發只留 1 筆", len(carry_evs(svc4.table)) == 1,
          len(carry_evs(svc4.table)))

    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
