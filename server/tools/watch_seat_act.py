#!/usr/bin/env python3
"""盯「某一席有沒有真的動」——事件流上出現那席的動作就收工（背景跑，預設最多 10 分鐘）。

為什麼要有：桌端與 agent 客戶端之間有**好幾層**（kick → 橋 → 桌的 GraphQL → 事件流），
「她沒動」可能是任何一層。這一支只看**最後的事實**（事件流），適合在「剛修完、等她下一輪」
這種時刻當驗收：看到動作＝整條鏈通；看不到＝至少知道還是沒落地（並印出最後幾筆事件）。

用法：
    python3 tools/watch_seat_act.py --table <桌 id> --seat p2 [--since-seq 14] [--minutes 10]
    python3 tools/watch_seat_act.py --table hyakki_tower_sannenme_no_onibarai_1 --seat p2

⚠ 讀 `runs/current` 那一局（換局要重跑）；純讀，不寫桌上任何東西。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from trpg.core import layout as layout                                    # noqa: E402  桌的位置（唯一入口）
ACTS = ("say", "acted", "action_declared", "move", "search_loot", "roll", "fight", "pray")


def table_home(tid: str) -> str:
    """桌目錄：走 `layout.locate`（現行 `state/tables/<模組>/<劇本>/<桌ID>`；舊制 modules／campaigns 也吃）。"""
    row = layout.locate(ROOT, tid)
    if row:
        return row["home"]
    raise SystemExit(f"找不到桌目錄：{tid}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", required=True)
    ap.add_argument("--seat", required=True, help="盯哪一席（例：p2）")
    ap.add_argument("--since-seq", type=int, default=0, help="只看 seq 大於這個值的動作（略過舊資料）")
    ap.add_argument("--minutes", type=float, default=10.0)
    ap.add_argument("--poll", type=float, default=8.0)
    a = ap.parse_args()

    home = table_home(a.table)
    run = open(os.path.join(home, "runs", "current"), encoding="utf-8").read().strip()
    ev_path = os.path.join(home, "runs", run, "events.jsonl")
    print(f"盯 {a.table} / 席位 {a.seat}（局 {run[:8]}…，seq>{a.since_seq}）", flush=True)

    deadline = time.time() + a.minutes * 60
    evs: list = []
    while time.time() < deadline:
        try:
            evs = [json.loads(l) for l in open(ev_path, encoding="utf-8") if l.strip()]
        except Exception:                                                  # noqa: BLE001
            time.sleep(a.poll)
            continue
        hits = [e for e in evs
                if e.get("seat") == a.seat and e.get("type") in ACTS
                and int(e.get("seq") or 0) > a.since_seq
                and str(e.get("text") or e.get("action") or "").strip()]
        if hits:
            e = hits[0]
            print(f"她動了 ✓ seq={e.get('seq')} type={e.get('type')} action={e.get('action')} "
                  f"text={str(e.get('text') or '')[:80]!r}", flush=True)
            return 0
        time.sleep(a.poll)
    tail = ", ".join(f"{x.get('type')}@{x.get('seat')}" for x in evs[-4:])
    print(f"{a.minutes:.0f} 分鐘內 {a.seat} 還是沒動作 ✗（事件數 {len(evs)}；最後幾筆：{tail}）", flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
