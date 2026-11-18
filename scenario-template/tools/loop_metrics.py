#!/usr/bin/env python3
"""玩法循環量測——從一局的 events.jsonl 算出「這一局到底把時間花在哪」。

只管量測，不改任何東西；輸出是一張給人看的表（也支援 --json）。

用法：
    python3 tools/loop_metrics.py <桌目錄>                 # 自動找 runs/current 或最新 run
    python3 tools/loop_metrics.py <run 目錄>               # 直接給含 events.jsonl 的目錄
    python3 tools/loop_metrics.py <events.jsonl> --board <模組>/scenarios/<劇本>/board.json
    python3 tools/loop_metrics.py <events.jsonl> --json

看什麼：
    手數（turn_ended）＝這一局被切成幾「手」；行動數（action_declared）＝實際有人做事的手；
    超時（seat_timeout）＝等到死的手（牆鐘殺手）；活躍分鐘＝把 >10 分鐘的空檔扣掉之後的時間；
    動作組合＝移動／搜尋／戰鬥／日常各佔幾成；節點利用率＝地圖用了幾個節點（要 --board）。

判讀原則（寫在 docs/GAMEPLAY_LOOP.md）：
    * 超時率 > 25% ⇒ 先降 wait／max_waits，不要先砍 turn.seconds（會把人逼成超時）。
    * 移動＋ride 佔 > 35% ⇒ 地圖太大或移動太貴。
    * 戰鬥行動 < 15% ⇒ 這一齣戲不是戰鬥戲，或敵人放太少。
    * 每景碎片流入 < 0.5 ⇒ 資訊密度太低（玩家在走，不在發現）。
"""
from __future__ import annotations

import json
import os
import statistics
import sys
from collections import Counter

GAP_IDLE = 600  # 超過 10 分鐘的空檔算「沒在玩」

COMBAT = {"fight", "attack", "cast", "use_item", "rescue", "flee"}
MOVE = {"move", "ride", "enter", "exit_tower", "elevator", "climb"}
DAILY = {"rest", "buy", "shop", "say", "show", "give", "wish", "search", "check", "other"}


def find_events(target: str) -> str:
    if os.path.isfile(target):
        return target
    cur = os.path.join(target, "runs", "current")
    if os.path.isdir(cur) or os.path.isfile(cur):
        cand = os.path.realpath(cur)
        path = os.path.join(cand, "events.jsonl")
        if os.path.exists(path):
            return path
    runs = os.path.join(target, "runs")
    if os.path.isdir(runs):
        best = max(
            (os.path.join(runs, r) for r in os.listdir(runs)),
            key=lambda p: os.path.getmtime(os.path.join(p, "events.jsonl"))
            if os.path.exists(os.path.join(p, "events.jsonl"))
            else 0,
            default=None,
        )
        if best:
            path = os.path.join(best, "events.jsonl")
            if os.path.exists(path):
                return path
    path = os.path.join(target, "events.jsonl")
    if os.path.exists(path):
        return path
    raise SystemExit(f"找不到 events.jsonl：{target}")


def board_nodes(path: str | None) -> list[str]:
    if not path:
        return []
    board = json.load(open(path, encoding="utf-8"))
    return [n["id"] for n in board.get("nodes", []) if isinstance(n, dict) and n.get("id")]


def measure(events_path: str, board_path: str | None = None) -> dict:
    events = [json.loads(line) for line in open(events_path, encoding="utf-8") if line.strip()]
    if not events:
        raise SystemExit("events.jsonl 是空的")

    types = Counter(e.get("type") for e in events)
    hands = types.get("turn_ended", 0)
    actions = types.get("action_declared", 0)
    timeouts = types.get("seat_timeout", 0)

    gaps = [events[i + 1]["ts"] - events[i]["ts"] for i in range(len(events) - 1)]
    active = sum(g for g in gaps if g < GAP_IDLE)
    idle_gaps = [g for g in gaps if g >= GAP_IDLE]

    mix = Counter(e.get("action") for e in events if e.get("type") == "action_declared" and e.get("action"))
    combat = sum(v for k, v in mix.items() if k in COMBAT)
    move = sum(v for k, v in mix.items() if k in MOVE)
    daily = sum(v for k, v in mix.items() if k in DAILY)

    per_seat_timeout = Counter(e.get("seat") for e in events if e.get("type") == "seat_timeout")
    per_seat_action = Counter(e.get("seat") for e in events if e.get("type") == "action_declared")

    visited = set()
    for e in events:
        if e.get("type") in ("move", "seat_joined") and e.get("to"):
            visited.add(e["to"])
        if e.get("type") == "move" and e.get("node"):
            visited.add(e["node"])
    board = board_nodes(board_path)
    used = [n for n in board if n in visited] if board else sorted(visited)

    # 景：相鄰 scene_open 之間有幾手（收景與手數是否對齊）
    starts = [i for i, e in enumerate(events) if e.get("type") == "scene_open"]
    hands_per_scene = []
    for k, s in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else len(events)
        hands_per_scene.append(sum(1 for e in events[s:end] if e.get("type") == "turn_ended"))
    empty_scenes = sum(1 for h in hands_per_scene if h == 0)

    span_min = (events[-1]["ts"] - events[0]["ts"]) / 60
    return {
        "events": len(events),
        "hands": hands,
        "actions": actions,
        "timeouts": timeouts,
        "timeout_rate": round(100 * timeouts / hands, 1) if hands else None,
        "actions_per_hand": round(actions / hands, 2) if hands else None,
        "span_min": round(span_min, 1),
        "active_min": round(active / 60, 1),
        "idle_gaps": len(idle_gaps),
        "idle_min": round(sum(idle_gaps) / 60, 1),
        "active_min_per_hand": round(active / 60 / hands, 2) if hands else None,
        "scenes": len(starts),
        "empty_scenes": empty_scenes,
        "hands_per_scene_median": statistics.median(hands_per_scene) if hands_per_scene else None,
        "mix": dict(mix.most_common()),
        "combat_share": round(100 * combat / actions, 1) if actions else None,
        "move_share": round(100 * move / actions, 1) if actions else None,
        "daily_share": round(100 * daily / actions, 1) if actions else None,
        "fragments": {
            "granted": types.get("fragment_granted", 0),
            "seen": types.get("fragment_seen", 0),
            "given": types.get("fragment_given", 0),
            "per_scene": round(types.get("fragment_seen", 0) / len(starts), 2) if starts else None,
        },
        "combat_events": {
            "rolls": types.get("roll", 0),
            "kills": types.get("mob_killed", 0),
            "heal": types.get("heal", 0),
            "damage": types.get("damage", 0),
        },
        "timeouts_by_seat": dict(per_seat_timeout),
        "actions_by_seat": dict(per_seat_action),
        "nodes": {"board": len(board) or None, "visited": len(used), "used": used},
        "ending": next(({"id": e.get("id"), "kind": e.get("kind")} for e in events if e.get("type") == "ending"), None),
    }


def show(name: str, m: dict) -> None:
    print(f"\n== {name}")
    print(
        f"   手 {m['hands']} / 行動 {m['actions']}（{m['actions_per_hand']} 動作·手）"
        f" / 超時 {m['timeouts']}（{m['timeout_rate']}%）"
    )
    print(
        f"   活躍 {m['active_min']} 分（牆鐘 {m['span_min']} 分；>10 分空檔 {m['idle_gaps']} 次／{m['idle_min']} 分）"
        f" → 每手活躍 {m['active_min_per_hand']} 分"
    )
    print(f"   景 {m['scenes']}（其中 {m['empty_scenes']} 景沒有 turn_ended；每景手中位 {m['hands_per_scene_median']}）")
    print(f"   動作：戰鬥 {m['combat_share']}% / 移動 {m['move_share']}% / 其他 {m['daily_share']}%")
    print(f"   事件：roll {m['combat_events']['rolls']}、擊破 {m['combat_events']['kills']}、heal {m['combat_events']['heal']}")
    fr = m["fragments"]
    print(f"   碎片：出土 {fr['granted']}、被看到 {fr['seen']}、被出示 {fr['given']}（每景 {fr['per_scene']}）")
    nodes = m["nodes"]
    if nodes["board"]:
        print(f"   地圖：用 {nodes['visited']}/{nodes['board']} 個節點")
    if m["timeouts_by_seat"]:
        print(f"   超時 by 席位：{m['timeouts_by_seat']}")
    print(f"   結局：{m['ending'] or '（尚未判定）'}")
    print(f"   動作明細：{m['mix']}")


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    as_json = "--json" in argv
    board = None
    if "--board" in argv:
        i = argv.index("--board")
        board = argv[i + 1] if i + 1 < len(argv) else None
        args = [a for a in args if a != board]
    if not args:
        print((__doc__ or "").strip().splitlines()[0] if __doc__ else "loop_metrics")
        return 2
    target = args[0]
    events = find_events(target)
    metrics = measure(events, board)
    if as_json:
        print(json.dumps(metrics, ensure_ascii=False, indent=2))
    else:
        show(os.path.basename(os.path.dirname(events)) or events, metrics)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
