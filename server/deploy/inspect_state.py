#!/usr/bin/env python3
"""看桌子状态的帮手（给 deploy/status.sh 用）：拉 /api/state 打印席位与景。
用法： inspect_state.py http://127.0.0.1:8971"""
import json
import sys
import urllib.request

url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8971"
# 必须带 seat=gm：`scene_waiting`（本景还没出手的席位）只在**有席位**的视图里才有。
# 不带席位的公开视图没有这一格 ⇒ 会一律印成「（全员都出手了）」（2026-10-06 实测：
# 景 35 明明还在等 p1/p2，status.sh 却印全员出手）。
seat = sys.argv[2] if len(sys.argv) > 2 else "gm"
try:
    with urllib.request.urlopen(url.rstrip("/") + f"/api/state?seat={seat}", timeout=5) as fh:
        v = json.loads(fh.read().decode("utf-8"))
except Exception as exc:                                    # noqa: BLE001
    print(f"  （取不到 /api/state：{exc}）")
    sys.exit(0)

stage = v.get("stage") or {}
print(f"  景 {stage.get('no')} " + ("进行中" if stage.get("open") else "已收")
      + f" | 还没出手：{v.get('scene_waiting') or '（全员都出手了）'}")
for s in v.get("seats", []):
    extra = []
    if s.get("kind") == "gm":
        extra.append("主持")
    if s.get("kind") == "npc":
        extra.append("NPC")
    if s.get("hp") is not None:
        extra.append(f"HP {s['hp']}/{s.get('max_hp')}")
    print(f"  {s.get('seat'):6} {s.get('name'):10} {s.get('kind'):6} " + " ".join(extra))
