#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**kick 按帳號**（2026-10-20 設計者定案）——席位寫死端點那一套退役。

事故（2026-10-20 13:04 示範劇本）：p4 由本機 GM agent 入座出手，kick 卻照
`config.wake.seats.p4.url` 打到 外部 agent 橋（200 spawned）⇒ 叫醒沒參賽的帳號。

閘（A1–A5）：
  A1 席位寫死 URL ≠ 當前帳號 ⇒ 只踢帳號的 URL，寫死那條一次都沒被呼叫
  A2 席位沒綁帳號 ⇒ 不發任何 HTTP，kick 事件 `ok:false, via:"no-account"`（可見、不靜默）
  A3 帳號在、但 wake_url 空 ⇒ skip `account-no-url`，**不回退**席位設定
  A4 人類席位不踢（`human seat`，不記噪音）
  A5 同一帳號坐兩張桌 ⇒ 兩桌都踢同一個 URL（帳號跨桌共用）

跑：`TRPG_LEGACY_REST=1 python3 tests/test_wake_by_account.py`
"""
from __future__ import annotations

import io
import os
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trpg.service.wake.mixin import KickMixin                                         # noqa: E402

FAIL = 0
PASS = 0


def check(name: str, ok: Any, got: Any = "") -> None:
    global FAIL, PASS
    if ok:
        PASS += 1
        print(f"  PASS  {name}" + (f"  [{got}]" if got != "" else ""))
    else:
        FAIL += 1
        print(f"  FAIL  {name}  [{got}]")


class FakeResp:
    status = 200

    def read(self, n: int = -1) -> bytes:
        return b'{"ok": true}'

    def __enter__(self):
        return self

    def __exit__(self, *a: Any) -> None:
        return None


class FakeTable:
    def __init__(self, seats: Dict[str, Dict[str, Any]], *, run_id: str = "run_test") -> None:
        self.seats = seats
        self.run_id = run_id
        self.events: List[Dict[str, Any]] = []

    def adapter_view(self) -> Dict[str, Any]:
        return {"paused": False, "run_id": self.run_id, "roundNo": 1,
                "turn": None, "seats": self.seats}

    def _emit(self, etype: str, **kw: Any) -> Dict[str, Any]:
        ev = {"type": etype, **kw}
        self.events.append(ev)
        return ev


class FakeAccounts:
    """只帶 kick 需要的三個方法：account_of_seat / wake_of（set 不測）。"""

    def __init__(self, seat_account: Dict[str, str], wake: Dict[str, Dict[str, str]]) -> None:
        self._seat_account = seat_account
        self._wake = wake

    def account_of_seat(self, seat: str) -> Optional[str]:
        return self._seat_account.get(seat) or None

    def wake_of(self, account: str) -> Dict[str, Any]:
        return dict(self._wake.get(account) or {})


class FakeSvc(KickMixin):
    def __init__(self, *, seats: Dict[str, Dict[str, Any]], seat_cfg_urls: Dict[str, str],
                 accounts: FakeAccounts, legacy: bool = False) -> None:
        self.home = "/tmp/none"
        self.cfg = {
            "table_id": "t_test",
            "wake": {"seats": {s: {"url": u} for s, u in seat_cfg_urls.items()},
                     "legacy_seat_url": legacy},
        }
        self.table = FakeTable(seats)
        self.kicked: Dict[str, str] = {}
        self.wake_urls: Dict[str, str] = {}
        self.rio_kick_keys: set = set()
        self._acct_cache = accounts

    def kick_prompt(self, seat: str, reason: str = "turn") -> str:
        return f"kick {seat}"

    def _session_id(self) -> str:
        return self.table.run_id


def with_urlopen(svc: FakeSvc, fn) -> List[str]:
    """攔 `urllib.request.urlopen`，記下每一個被呼叫的 URL（標頭另存 HEADERS）。"""
    import trpg.service.wake.mixin as mx
    called: List[str] = []
    real = mx.urllib.request.urlopen

    def fake(req, *a, **kw):
        called.append(getattr(req, "full_url", str(req)))
        HEADERS.clear()
        HEADERS.update({str(k).lower(): v for k, v in (getattr(req, "headers", {}) or {}).items()})
        return FakeResp()

    mx.urllib.request.urlopen = fake
    try:
        fn()
    finally:
        mx.urllib.request.urlopen = real
    return called


HEADERS: Dict[str, str] = {}


def kick_event(svc: FakeSvc) -> Dict[str, Any]:
    evs = [e for e in svc.table.events if e.get("type") == "kick"]
    return evs[-1] if evs else {}


def main() -> int:
    print("== A1 席位寫死 URL ≠ 當前帳號：只踢帳號的 URL ==")
    seats = {"p4": {"kind": "agent"}}
    accts = FakeAccounts({"p4": "acct_blue"}, {"acct_blue": {"url": "http://acct-other/kick"}})
    svc = FakeSvc(seats=seats, seat_cfg_urls={"p4": "http://wrong-seat/kick"}, accounts=accts)
    called = with_urlopen(svc, lambda: svc.kick("p4", 1))
    check("只打帳號端點", called == ["http://acct-other/kick"], called)
    ev = kick_event(svc)
    check("事件 via=account:acct_blue", ev.get("via") == "account:acct_blue", ev.get("via"))
    check("事件 ok=true", ev.get("ok") is True, ev.get("ok"))

    print("\n== A2 席位沒綁帳號：不發 HTTP，事件可見 ==")
    svc2 = FakeSvc(seats={"p4": {"kind": "agent"}},
                   seat_cfg_urls={"p4": "http://wrong-seat/kick"}, accounts=FakeAccounts({}, {}))
    called2 = with_urlopen(svc2, lambda: svc2.kick("p4", 1))
    check("一個 HTTP 都沒發（連席位寫死的也沒碰）", called2 == [], called2)
    ev2 = kick_event(svc2)
    check("事件 via=no-account", ev2.get("via") == "no-account", ev2.get("via"))
    check("事件 ok=false 且 why=no-account",
          ev2.get("ok") is False and (ev2.get("detail") or {}).get("why") == "no-account",
          ev2.get("detail"))

    print("\n== A3 帳號在、wake_url 空：skip account-no-url，不回退席位 ==")
    svc3 = FakeSvc(seats={"p4": {"kind": "agent"}},
                   seat_cfg_urls={"p4": "http://wrong-seat/kick"},
                   accounts=FakeAccounts({"p4": "acct_blue"}, {"acct_blue": {"url": ""}}))
    called3 = with_urlopen(svc3, lambda: svc3.kick("p4", 1))
    check("沒有回退到席位寫死那條", called3 == [], called3)
    ev3 = kick_event(svc3)
    check("事件 why=account-no-url", (ev3.get("detail") or {}).get("why") == "account-no-url",
          ev3.get("detail"))

    print("\n== A4 人類席位不踢、不記噪音 ==")
    svc4 = FakeSvc(seats={"p2": {"kind": "human"}}, seat_cfg_urls={"p2": "http://x/kick"},
                   accounts=FakeAccounts({"p2": "acct_shinkai"}, {"acct_shinkai": {"url": ""}}))
    called4 = with_urlopen(svc4, lambda: svc4.kick("p2", 1))
    check("沒發 HTTP", called4 == [], called4)
    check("沒有 kick 事件（人類席位不記噪音）",
          [e for e in svc4.table.events if e.get("type") == "kick"] == [],
          svc4.table.events)

    print("\n== A5 同一帳號坐兩張桌：兩桌踢同一個 URL ==")
    accts5 = FakeAccounts({"p3": "acct_peer"}, {"acct_peer": {"url": "http://peer/kick"}})
    t1 = FakeSvc(seats={"p3": {"kind": "agent"}}, seat_cfg_urls={}, accounts=accts5)
    t2 = FakeSvc(seats={"p3": {"kind": "agent"}}, seat_cfg_urls={}, accounts=accts5)
    c1 = with_urlopen(t1, lambda: t1.kick("p3", 1))
    c2 = with_urlopen(t2, lambda: t2.kick("p3", 1))
    check("兩桌同一個端點", c1 == c2 == ["http://peer/kick"], (c1, c2))

    print("\n== A6 遷移期 legacy_seat_url=true 才有舊行為（事件標 legacy-seat） ==")
    svc6 = FakeSvc(seats={"p4": {"kind": "agent"}},
                   seat_cfg_urls={"p4": "http://wrong-seat/kick"},
                   accounts=FakeAccounts({}, {}), legacy=True)
    called6 = with_urlopen(svc6, lambda: svc6.kick("p4", 1))
    check("legacy 開關才走席位寫死", called6 == ["http://wrong-seat/kick"], called6)
    check("事件 via=legacy-seat", kick_event(svc6).get("via") == "legacy-seat",
          kick_event(svc6).get("via"))

    print("\n== A7 token 也按帳號：帳號的 token 優先於桌 config 的 seat token ==")
    accts7 = FakeAccounts({"p4": "acct_blue"},
                          {"acct_blue": {"url": "http://acct-other/kick", "token": "acct-token"}})
    svc7 = FakeSvc(seats={"p4": {"kind": "agent"}},
                   seat_cfg_urls={"p4": "http://wrong-seat/kick"}, accounts=accts7)
    svc7.cfg["wake"]["seats"]["p4"]["token"] = "seat-token"
    called7 = with_urlopen(svc7, lambda: svc7.kick("p4", 1))
    check("打帳號端點", called7 == ["http://acct-other/kick"], called7)
    check("X-Table-Kick-Token＝帳號的 token", HEADERS.get("x-table-kick-token") == "acct-token",
          HEADERS.get("x-table-kick-token"))

    print(f"\n===== {PASS} PASS / {FAIL} FAIL =====")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
