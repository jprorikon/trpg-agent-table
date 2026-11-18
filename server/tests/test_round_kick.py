#!/usr/bin/env python3
"""輪開始的喚醒（`wake.maybe_kick_round`）——2026-10-21 拆景後唯一的喚醒點。

設計者的定案：引擎只跑回合制，GM 的工作＝**每一輪開始說一句**（固定），另有特定觸發的發言。
這一支驗三件事：
  * 有輪、人齊 ⇒ 叫一次 GM（文字裡帶「第 N 輪」與「用 say 說一句」）；
  * 同一輪不重複叫（冪等鍵帶輪號＋session）；
  * 人數不夠／局已結束 ⇒ 不叫。
跑：`python3 tests/test_round_kick.py`
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trpg.service import wake as wake                                                             # noqa: E402

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


class FakeTable:
    def __init__(self, *, round_no: int = 0, players: int = 3, ended: Any = None,
                 seat: str = "p1") -> None:
        self.state = {"round_no": round_no, "ended": ended,
                      "turn": {"seat": seat, "seq": 1, "index": 0} if seat else None}
        self.state["seats"] = {f"p{i}": {"kind": "human"} for i in range(1, players + 1)}
        self.events: List[Dict[str, Any]] = []

    def adapter_view(self) -> Dict[str, Any]:
        st = self.state
        return {"ended": st.get("ended"), "round_no": int(st.get("round_no") or 0),
                "turn": st.get("turn"), "turn_index": 0,
                "seats": {sid: {"kind": s.get("kind")} for sid, s in st["seats"].items()}}


class FakeSvc:
    def __init__(self, table: FakeTable, *, min_seats: int = 2) -> None:
        self.table = table
        self.cfg = {"table_id": "kick_demo", "turn": {"min_seats_to_start": min_seats}}
        self.rio_kick_keys: set = set()
        self.sent: List[Dict[str, Any]] = []

    def _session_id(self) -> str:
        return "sess1"

    def wake_gm(self, seat: str, text: str, kind: str, seq: int, **kw: Any) -> Dict[str, Any]:
        self.sent.append({"seat": seat, "text": text, "kind": kind, "kw": kw})
        return {"sent": True}


def main() -> int:
    print("== A. 輪開始叫 GM 說一句")
    svc = FakeSvc(FakeTable(round_no=0))
    r = wake.maybe_kick_round(svc)
    check("A1 有輪、人齊 ⇒ 叫一次 GM", bool(r.get("sent")) and len(svc.sent) == 1, r)
    txt = svc.sent[0]["text"] if svc.sent else ""
    check("A2 話裡有「第 1 輪」與「say」", "第 1 輪" in txt and "say" in txt, txt[:60])
    check("A3 冪等鍵帶輪號＋session",
          svc.sent and "round:1:sess1" in str(svc.sent[0]["kw"].get("turn_key")),
          svc.sent[0]["kw"] if svc.sent else "")
    r2 = wake.maybe_kick_round(svc)
    check("A4 同一輪不重複叫", (not r2.get("sent")) and len(svc.sent) == 1, r2)

    print("== B. 不該叫的情況")
    few = FakeSvc(FakeTable(round_no=0, players=1))
    check("B1 人數不夠 ⇒ 不叫", not wake.maybe_kick_round(few).get("sent"), few.sent)
    done = FakeSvc(FakeTable(round_no=3, ended="e_timeout"))
    check("B2 局已結束 ⇒ 不叫", not wake.maybe_kick_round(done).get("sent"), done.sent)

    print("== C. 換一輪就會再叫一次")
    svc2 = FakeSvc(FakeTable(round_no=4))
    r3 = wake.maybe_kick_round(svc2)
    check("C1 第 5 輪 ⇒ 叫（新鍵）", bool(r3.get("sent")) and "第 5 輪" in svc2.sent[0]["text"],
          svc2.sent[0]["text"][:40] if svc2.sent else "")

    print(f"\n===== {PASS} PASS / {FAIL} FAIL =====")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
