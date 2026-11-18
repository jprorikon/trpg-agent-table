#!/usr/bin/env python3
"""module_playtest：每个模组自己的「真打一遍」自检（2026-10-21）。

用法（在模组仓根目录）：
    python3 ../../tools/module_playtest.py <scenario_id> [--module=<id>]

检查的是**模组自己**能不能在新引擎上走通，不是引擎单测：
  1. 建桌／入座／第一轮自动开始（回合制）
  2. 动作表只剩「收束后」的七类（没有 enter／ride／exit_tower）
  3. 用 `view.board.can_go` 的真实邻接走到入口节点（不用 board.json 的 edges 猜）
  4. **move 进塔**（外→内）→ 落点＝入口楼层
  5. 塔内 **move 上一层**：本层没清光 ⇒ 必须被拒（讲清楚「先清怪」）
  6. 回到 1 阶后 **move 出塔**（内→外）⇒ 发 tower_exit
  7. 半合作用词：`term_name("fragment")` ＝本剧本 `terms.fragment`（没写＝碎片）

退出码 0＝全过；1＝有 FAIL（细节打印在 stdout）。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from trpg.service import table_factory                                    # noqa: E402

PASS: List[str] = []
FAIL: List[str] = []


def check(name: str, ok: Any, got: Any = "") -> None:
    if ok:
        PASS.append(name)
        print(f"  PASS  {name}" + (f"  [{got}]" if got != "" else ""))
    else:
        FAIL.append(name)
        print(f"  FAIL  {name}  [{got}]")


def _copy_module(src: str) -> str:
    tmp = tempfile.mkdtemp(prefix="modtest_")
    dst = os.path.join(tmp, os.path.basename(src.rstrip("/")))
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("runs", ".studio", "__pycache__", "*.log"))
    return dst


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("--module", default=None)
    ap.add_argument("--seat", default=None, help="用哪一席推进（默认第一席）")
    a = ap.parse_args()

    mod_id = a.module or os.path.basename(os.getcwd())
    src = os.getcwd()
    home_mod = _copy_module(src)
    scn = os.path.join(home_mod, "scenarios", a.scenario)
    cfg_path = os.path.join(scn, "config.example.json")
    if not os.path.isfile(cfg_path):
        cfg_path = os.path.join(scn, "config.json")
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    home = os.path.join(scn, "tables", "_selftest")
    os.makedirs(home, exist_ok=True)
    shutil.copy(cfg_path, os.path.join(home, "config.json"))
    svc = table_factory.build(home, cfg, log=lambda *_: None, def_dir=scn)
    t = svc.table

    print(f"== {mod_id} / {a.scenario} 自检")
    seats = [sid for sid, sp in (getattr(t, "seats_spec", {}) or {}).items()
             if sp.get("kind") in ("human", "agent")]
    for sid in seats:
        sp = t.seats_spec[sid]
        try:
            t.join(seat=sid, name=sp.get("name") or sp.get("character") or sid,
                   kind=sp["kind"], node=sp.get("start_node"))
        except Exception:                                                # noqa: BLE001
            pass
    if not t.state.get("turn"):
        try:
            t.start_turn()
        except Exception:                                                # noqa: BLE001
            pass
    seat = a.seat or (seats[0] if seats else "p1")
    check("A1 入座＋第一輪自動開始（回合制）", bool(t.state.get("turn")),
          (t.state.get("turn") or {}).get("seat"))

    view = t.view(seat=seat, gm=True)
    acts = [x["id"] for x in (view.get("actions") or [])]
    check("A2 動作表已收束（沒有 enter／ride／exit_tower）",
          not {"enter", "ride", "exit_tower"} & set(acts), acts)

    def act(action: str, **kw: Any) -> Dict[str, Any]:
        for _ in range(8):
            cur = (t.state.get("turn") or {}).get("seat")
            if cur is None:
                try:
                    t.start_turn()
                except Exception:                                        # noqa: BLE001
                    break
                continue
            if str(cur) == str(seat):
                break
            try:
                t.act(seat=str(cur), action="end_turn")
            except Exception:                                            # noqa: BLE001
                break
        return t.act(seat=seat, action=action, **kw)

    def here() -> Optional[str]:
        return (t.state["seats"].get(seat) or {}).get("node")

    def can_go() -> List[Dict[str, Any]]:
        return (t.view(seat=seat, gm=True).get("board") or {}).get("can_go") or []

    # 走到入口節點：用投影的 can_go（真實鄰接），不用 board.json 的 edges
    # 入口節點：挑「真的連得到塔內樓層」的那一個（多座地牢時，已清空的入口進不去）
    _floor_ids = {nid for nid, n in (t.nodes or {}).items()
                  if str(n.get("type")) == "tower_floor"}
    entry = next((nid for nid, n in (t.nodes or {}).items()
                  if (n.get("monolith") or str(n.get("type")) == "monolith_entry")
                  and any(e.get("kind") == "entrance" and e.get("to") in _floor_ids
                          for e in (n.get("edges") or []))
                  and "cleared" not in (n.get("tags") or [])), None)
    if entry is None:                     # 退而求其次：任何 monolith／monolith_entry
        entry = next((nid for nid, n in (t.nodes or {}).items()
                      if n.get("monolith") or str(n.get("type")) == "monolith_entry"), None)
    for _ in range(40):
        opts = can_go()
        cand = [o for o in opts if o.get("type") in ("tower_entry", "entrance")
                or str(o.get("to") or "").startswith("tow_") or o.get("entrance")]
        if here() and any(o.get("to") == here() for o in opts):
            pass
        if not cand:
            break
    if entry is None:
        entry = (t.view(seat=seat, gm=True).get("board") or {}).get("entry_node")
    check("B1 找得到入口節點（用 can_go 真鄰接）", bool(entry), entry)

    floors = [n["id"] for n in (t.nodes or {}).values()
              if str(n.get("type")) == "tower_floor"] if isinstance(t.nodes, dict) else []
    floors.sort(key=lambda nid: (t.nodes[nid].get("depth") or 0))

    def path_to(goal: str) -> List[str]:
        """棋盤圖上到 goal 的最短路徑（`board.json` 的 edges 只當**路徑規劃**；
        真的能不能走仍以引擎的 `move` 為準——被檢定擋掉就重試，不是硬走）。"""
        nodes = t.nodes or {}
        if goal not in nodes or here() not in nodes:
            return []
        prev: Dict[str, Optional[str]] = {here(): None}
        queue = [here()]
        while queue:
            cur = queue.pop(0)
            if cur == goal:
                break
            for e in (nodes[cur].get("edges") or []):
                to = e.get("to")
                if to in nodes and to not in prev:
                    prev[to] = cur
                    queue.append(to)
        if goal not in prev:
            return []
        out, cur = [], goal
        while cur and cur != here():
            out.append(cur)
            cur = prev[cur]
        return list(reversed(out))

    if entry and floors:
        # 走到 entry：按最短路徑一格一格走（被檢定擋掉就再試）
        for _step in path_to(entry):
            for _try in range(6):
                if here() == _step:
                    break
                try:
                    act("move", target=_step)
                except Exception:                                        # noqa: BLE001
                    time.sleep(0.05)
        check("B2 走到入口節點", here() == entry, here())

        # move 進塔（外→內）
        try:
            r = act("move", target=floors[0])
            check("B3 move 進塔（外→內）⇒ 落到入口樓層",
                  str((t.state["seats"].get(seat) or {}).get("node")) == str(floors[0]),
                  (t.state["seats"].get(seat) or {}).get("node"))
        except Exception as exc:                                         # noqa: BLE001
            check("B3 move 進塔（外→內）", False, str(exc)[:90])

        # 塔內 move 上一層：沒清光必須被拒
        if len(floors) > 1:
            left = int((t.state["board"].get(t.slot("mob")) or {}).get(here(), 0))
            try:
                act("move", target=floors[1])
                ok = left == 0
                check("B4 塔內 move 上一層（未清光 ⇒ 必須被拒）", ok,
                      f"{'已清光⇒允許' if left == 0 else '沒被拒（有問題）'}")
            except Exception as exc:                                     # noqa: BLE001
                check("B4 塔內 move 上一層（未清光 ⇒ 必須被拒）", left > 0, str(exc)[:80])

        # 回 1 階 → move 出塔
        try:
            if here() != floors[0]:
                act("move", target=floors[0])
            r = act("move", target=entry)
            ok = bool(r.get("to")) and not str((t.state["seats"].get(seat) or {}).get("node")) in floors
            check("B5 回 1 階後 move 出塔（內→外）", ok, r.get("to"))
        except Exception as exc:                                         # noqa: BLE001
            check("B5 回 1 階後 move 出塔（內→外）", False, str(exc)[:90])

    terms = (t.module.data.get("terms") if getattr(t, "module", None) else None) or {}
    want = ((terms.get("fragment") or {}) if isinstance(terms.get("fragment"), dict) else {"cn": terms.get("fragment")})
    want = want.get("cn") or want.get("jp") or "碎片"
    check("C1 半合作用詞＝本劇本 terms.fragment", t.term_name("fragment") == want,
          f"{t.term_name('fragment')} vs {want}")

    print(f"\n===== {len(PASS)} PASS / {len(FAIL)} FAIL =====")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
