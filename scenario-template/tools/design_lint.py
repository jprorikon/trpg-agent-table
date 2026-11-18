#!/usr/bin/env python3
"""劇本「設計味道」掃描——不是自檢器。

分工（很重要）：
  * 能不能起桌、格式對不對 → 引擎 `tools/check_campaign.py`（本目錄的 `check_module.sh` 就是它的入口）。
  * 這齣戲「好不好玩／會不會卡死」→ 本工具。**它只讀 JSON 與 GM.md，不載入引擎**，
    所以任何一條規則都不該被當成「起桌前提」；它報的是**設計味道**。

用法：
    python3 tools/design_lint.py <模組目錄>                # 掃模組裡每一個劇本
    python3 tools/design_lint.py <劇本目錄>                # 只掃這一齣
    python3 tools/design_lint.py <模組目錄> --strict       # 有 WARN 就 exit 1（給 CI／交件前用）
    python3 tools/design_lint.py <模組目錄> --json         # 機器可讀

嚴重度：
    WARN  會讓「一桌人卡住／某人的願望註定失敗」的結構問題（交桌前應該看一眼）
    INFO  設計味道或提醒（可能是刻意的，自己判斷）

規則清單（每條都對應 docs/SCENARIO_DESIGN.md 的一節）：
    clue.*    三線索（P2）／孤兒結論
    goal.*    願望共用瓶頸（P5）／單一路徑
    seat.*    玩家席位沒有綁願望（P5：每個人都要有私人的理由）
    board.*   節點圖的選擇數與環路（P3）
    ending.*  失敗出口（P6）
    clock.*   世界時鐘（P4）
    loop.*    每景手數預算／一手的牆鐘上限（玩法循環，見 docs/GAMEPLAY_LOOP.md）
    gm.*      GM 手冊的必填段（P1／P7）
    clue.place_bound  碎片正文與投放池矛盾（P7）
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter, defaultdict

WARN = "WARN"
INFO = "INFO"

REQUIRED = ("config.example.json", "seats.json", "goals.json", "clues.json", "scenario.json")
TEXT_KEYS = ("text", "name", "name_jp", "doc")


class Report:
    def __init__(self, label: str) -> None:
        self.label = label
        self.items: list[tuple[str, str, str]] = []
        self.facts: dict[str, object] = {}

    def add(self, level: str, code: str, message: str) -> None:
        self.items.append((level, code, message))

    def warn(self, code: str, message: str) -> None:
        self.add(WARN, code, message)

    def info(self, code: str, message: str) -> None:
        self.add(INFO, code, message)

    @property
    def warns(self) -> int:
        return sum(1 for level, _, _ in self.items if level == WARN)


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def walk_strings(node, out: list[str]) -> list[str]:
    """把一份 JSON 裡所有字串收出來（用來找「這個判據引用了哪些結論 id」）。"""
    if isinstance(node, dict):
        for value in node.values():
            walk_strings(value, out)
    elif isinstance(node, list):
        for value in node:
            walk_strings(value, out)
    elif isinstance(node, str):
        out.append(node)
    return out


def has_operator(node, name: str) -> bool:
    """這棵 AST 裡有沒有用到某個運算子（以「鍵名」判斷，不做字串包含——
    `c_thunder_core` 裡也有 "or"，用字串比對會誤判）。"""
    if isinstance(node, dict):
        if name in node:
            return True
        return any(has_operator(value, name) for value in node.values())
    if isinstance(node, list):
        return any(has_operator(value, name) for value in node)
    return False


# ── 圖論小工具 ────────────────────────────────────────────────────────────────


def graph(board: dict) -> tuple[dict[str, dict], dict[str, list[str]], dict[str, int]]:
    nodes = {n["id"]: n for n in board.get("nodes", []) if isinstance(n, dict) and n.get("id")}
    out: dict[str, list[str]] = {nid: [] for nid in nodes}
    indeg: Counter[str] = Counter()
    for nid, node in nodes.items():
        for edge in node.get("edges", []) or []:
            target = edge.get("to")
            if target in nodes:
                out[nid].append(target)
                indeg[target] += 1
    return nodes, out, dict(indeg)


def reachable(start: str, out: dict[str, list[str]]) -> set[str]:
    if start not in out:
        return set()
    seen, stack = {start}, [start]
    while stack:
        for nxt in out[stack.pop()]:
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def has_cycle(out: dict[str, list[str]]) -> bool:
    """有向圖有沒有環（不管從哪裡起）。"""
    state: dict[str, int] = {}

    def visit(nid: str) -> bool:
        state[nid] = 1
        for nxt in out.get(nid, []):
            if state.get(nxt) == 1:
                return True
            if state.get(nxt, 0) == 0 and visit(nxt):
                return True
        state[nid] = 2
        return False

    return any(state.get(nid, 0) == 0 and visit(nid) for nid in out)


def chain_degree(out: dict[str, list[str]], indeg: dict[str, int], nodes: dict[str, dict], types: set[str]) -> list[str]:
    """只看某一類節點（例：塔層）時，出度 1 的節點＝單鏈。"""
    picked = [nid for nid, node in nodes.items() if node.get("type") in types]
    linked = [nid for nid in picked if len(out.get(nid, [])) == 1 and [t for t in out[nid] if t in picked]]
    return linked


# ── 各段檢查 ─────────────────────────────────────────────────────────────────


def check_clues(rep: Report, goals: dict, clues: dict, scenario: dict) -> set[str]:
    claims = [c for c in clues.get("claims", []) if isinstance(c, dict) and c.get("id")]
    frags = [f for f in clues.get("fragments", []) if isinstance(f, dict) and f.get("id")]
    claim_ids = [c["id"] for c in claims]

    # 先算「有哪些結論被願望／表層／觸發真的用到」——P2 是對這些結論的要求，
    # 純氣氛的結論（沒有人要靠它達成什麼）薄一點沒關係，降成 INFO。
    expr_strings: list[str] = []
    walk_strings(scenario.get("requirements"), expr_strings)
    walk_strings(scenario.get("surface"), expr_strings)
    walk_strings(scenario.get("triggers"), expr_strings)
    walk_strings(scenario.get("endings"), expr_strings)
    used = set(expr_strings)

    by_claim: dict[str, list[dict]] = defaultdict(list)
    for frag in frags:
        for claim in frag.get("supports", []) or []:
            by_claim[claim].append(frag)

    # P2：每個結論至少三條證據
    for claim in claim_ids:
        held = by_claim.get(claim, [])
        pools = Counter(f.get("pool") for f in held)
        needed = claim in used
        if not held:
            if needed:
                rep.warn("clue.unsupported_claim", f"結論 {claim} 沒有任何碎片支持——願望判據永遠不會成立")
            else:
                rep.info("clue.flavor_unsupported", f"結論 {claim}（純氣氛）沒有任何碎片支持")
        elif len(held) < 3:
            names = ", ".join(f["id"] for f in held)
            if needed:
                rep.warn(
                    "clue.redundancy",
                    f"結論 {claim} 只有 {len(held)} 枚碎片（建議 ≥3）：{names}"
                    f"——玩家漏掉／誤讀就沒有備援，這是 chokepoint",
                )
            else:
                rep.info(
                    "clue.flavor_thin",
                    f"結論 {claim}（純氣氛，沒有願望靠它）只有 {len(held)} 枚碎片：{names}",
                )
        if len(held) >= 3 and len(pools) == 1:
            rep.info("clue.single_pool", f"結論 {claim} 的碎片都只從 {next(iter(pools))} 池出來（只有一種取得方式）")

    # P7：pool=outside 的碎片，正文不該寫成「只有塔裡才看得到」
    place_words = ("塔", "昇降機", "エレベーター", "階", "ダクト", "壁", "床")
    portable_words = (
        "書", "記録", "手記", "手帳", "台帳", "レシート", "紙", "断片", "走り書き",
        "手引書", "符", "写真", "日記", "メモ", "報告", "手紙", "札", "地図",
    )
    for frag in frags:
        if frag.get("pool") != "outside":
            continue
        text = str(frag.get("text", ""))
        hit = [w for w in place_words if w in text]
        carrying = [w for w in portable_words if w in text]
        if hit and not carrying:
            rep.info(
                "clue.place_bound",
                f"碎片 {frag['id']} 的 pool=outside，但正文提到「{'/'.join(hit)}」"
                f"——若它在城裡落地，文案會與地點矛盾（改成可攜物件，或把它放進 tower 池）",
            )

    # 孤兒結論：沒有任何判據引用
    orphans = [cid for cid in claim_ids if cid not in used]
    if orphans:
        rep.info("clue.orphan_claim", f"這些結論沒有被任何願望／表層／觸發用到（純氣氛可以留）：{', '.join(orphans)}")

    if not isinstance(clues.get("drop_policy"), dict):
        rep.warn("clue.no_drop_policy", "clues.json 沒有 drop_policy——碎片怎麼出土全靠 GM 臨場，重現不了")

    return used


def check_goals(rep: Report, goals: dict, scenario: dict, claim_ids: list[str]) -> None:
    rows = [g for g in goals.get("goals", []) if isinstance(g, dict) and g.get("id")]
    requirements = scenario.get("requirements") or {}

    shared: dict[str, list[str]] = defaultdict(list)
    for goal in rows:
        gid = goal["id"]
        req = requirements.get(gid)
        if not req:
            rep.info("goal.no_requirement", f"願望 {gid} 沒有 requirements 判據——它永遠不會被判定達成")
            continue
        strings: list[str] = []
        walk_strings(req.get("expr"), strings)
        hit = [s for s in strings if s in claim_ids]
        if has_operator(req.get("expr"), "or"):
            # 有 or 分支＝這條結論是「替代路線」而不是必要條件，不算共用瓶頸
            rep.info("goal.alternative", f"願望 {gid} 有替代路線（{', '.join(sorted(set(hit))) or '非結論條件'}）——好")
        else:
            for claim in set(hit):
                shared[claim].append(gid)
            rep.info("goal.single_route", f"願望 {gid} 的判據沒有 or 分支（只有一條路能達成）")

    for claim, gids in shared.items():
        if len(gids) >= 2:
            rep.warn(
                "goal.shared_claim",
                f"{len(gids)} 條願望都要結論 {claim}（{', '.join(sorted(gids))}）"
                f"——這條線斷了就是多人一起失敗（相關性失敗）",
            )


def check_board(rep: Report, board: dict) -> None:
    nodes, out, indeg = graph(board)
    if not nodes:
        return
    start = board.get("start_node")
    terminal = {nid for nid in nodes if not out[nid]}
    chokepoints = [nid for nid in nodes if nid not in terminal and len(out[nid]) == 1]

    rep.facts.update(
        nodes=len(nodes),
        edges=sum(len(v) for v in out.values()),
        chokepoints=len(chokepoints),
    )

    if isinstance(start, str) and start in nodes and len(out[start]) < 2:
        rep.warn(
            "board.start_single_exit",
            f"起點 {start} 只有 {len(out[start])} 條出路——開場沒有選擇，玩家只能照劇本走（P1／P3）",
        )
    if chokepoints:
        sample = ", ".join(chokepoints[:8]) + ("…" if len(chokepoints) > 8 else "")
        rep.info(
            "board.chokepoint",
            f"{len(chokepoints)} 個節點只有一條出路：{sample}"
            f"——單鏈本身不是錯，但玩家的「選擇」會退化成「要不要繼續走」",
        )
    for types, label in (({"tower_floor"}, "塔層"), ({"dungeon", "ruins"}, "地城")):
        linked = chain_degree(out, indeg, nodes, types)
        if len(linked) >= 3:
            rep.info("board.linear_chain", f"{label}有 {len(linked)} 個節點是單線相連（沒有繞路／捷徑／環路）")
            break
    if not has_cycle(out):
        rep.info("board.no_loop", "地圖沒有環路：玩家不會回頭、不會繞過、不會從另一頭冒出來（Xandering 的核心）")
    if isinstance(start, str) and start in nodes:
        lost = sorted(set(nodes) - reachable(start, out))
        if lost:
            rep.warn("board.unreachable", f"從 {start} 走不到這些節點：{', '.join(lost[:8])}")


def check_endings(rep: Report, scenario: dict, config: dict | None, endings: list[dict]) -> None:
    kinds = Counter(e.get("kind") for e in endings if isinstance(e, dict))
    rep.facts.update(endings=len(endings))
    if not endings:
        rep.warn("ending.none", "沒有 endings——這一局沒有結尾")
        return
    if not kinds.get("neutral"):
        rep.warn(
            "ending.no_neutral",
            "只有 win／lose，沒有 neutral（撤退、時間到、談和）——卡住或不想打到底的桌沒有體面的收法（P6）",
        )
    if kinds.get("lose") and config is not None:
        packs = ((config.get("packs") or {}).get("enabled") or [])
        text = json.dumps([e for e in endings if e.get("kind") == "lose"], ensure_ascii=False)
        if "death" in packs and "world.up" in text:
            rep.info(
                "ending.lose_gated_by_death",
                "lose 結局要求「全員倒地」，但這張桌開了 death 包（HP≤0 會被送還城市）——這個 lose 幾乎不會成立",
            )


def check_clock(rep: Report, scenario: dict) -> None:
    triggers = [t for t in scenario.get("triggers", []) if isinstance(t, dict)]
    rep.facts.update(triggers=len(triggers))
    world_events = {"turn_ended", "scene_open", "scene_closed", "move", "tick"}
    world_clock = []
    for trigger in triggers:
        on = trigger.get("on")
        ons = on if isinstance(on, list) else [on]
        blob = json.dumps(trigger, ensure_ascii=False)
        if any(e in world_events for e in ons if isinstance(e, str)) and ("scene" in blob or "turn" in blob or "round" in blob):
            world_clock.append(trigger.get("id"))
    if triggers and not world_clock:
        rep.info(
            "clock.missing",
            "所有觸發都掛在玩家「成功事件」（擊破／進場／打贏）上，沒有世界自己的時鐘"
            "——玩家不動，世界也不會動（P4：fronts／clocks）",
        )


def check_gm(rep: Report, gm_path: str) -> None:
    if not os.path.exists(gm_path):
        rep.info("gm.missing", "沒有 GM.md——GM（或演 GM 的 agent）拿不到調性、開場、收景判準")
        return
    with open(gm_path, encoding="utf-8") as handle:
        text = handle.read()
    headings = [line for line in text.splitlines() if line.lstrip().startswith("#")]
    missing = []
    for label, keywords in (
        ("調性（這一齣戲要什麼感覺）", ("調性", "氛圍", "tone")),
        ("開場（一個具體的麻煩＋兩個方向）", ("開場", "第一景", "第 1 景")),
        ("收景判準（什麼時候算這一景結束）", ("收景", "收場")),
        ("不能做的事（防止 GM 硬推）", ("不能做", "不要做", "禁止")),
    ):
        if not any(any(kw in head for kw in keywords) for head in headings):
            missing.append(label)
    if missing:
        rep.warn("gm.missing_section", "GM.md 缺這些段：" + "；".join(missing) + "（模板要求有，抄模板時別刪）")


def check_seats(rep: Report, seats: dict, goals: dict) -> None:
    goal_ids = {g["id"] for g in goals.get("goals", []) if isinstance(g, dict) and g.get("id")}
    rows = [s for s in seats.get("seats", []) if isinstance(s, dict) and s.get("seat")]
    for seat in rows:
        if seat.get("kind") in ("human", "agent") and not seat.get("goal"):
            rep.warn(
                "seat.no_goal",
                f"玩家席位 {seat['seat']}（{seat.get('character') or '?'}）沒有綁願望"
                f"——這個人沒有私人的理由留在桌上，只能陪跑",
            )
    bound = {s.get("goal") for s in rows if s.get("goal")}
    unbound = sorted(goal_ids - bound)
    if unbound:
        rep.info("goal.not_seated", f"這些願望沒有綁到任何席位（寫了但沒人拿得到）：{', '.join(unbound)}")


def check_loop(rep: Report, config: dict | None, seats: dict) -> None:
    """每景手數＝玩家席位 × 每景行動格；一手牆鐘＝turn.seconds 與 max_waits 的乘積。
    這兩件事決定一局會拖多長（實測見 docs/GAMEPLAY_LOOP.md）。"""
    if config is None:
        return
    players = [s for s in seats.get("seats", []) if isinstance(s, dict) and s.get("kind") in ("human", "agent")]
    pacing = config.get("pacing") or {}
    turn = config.get("turn") or {}
    aps = pacing.get("actions_per_scene") or turn.get("actions_per_turn") or 2
    budget = len(players) * int(aps)
    rep.facts["hands_per_scene"] = budget
    if budget > 8:
        rep.warn(
            "loop.scene_budget",
            f"每景手數上限 {len(players)} 席 × {aps} 格 = {budget} 手——超過 8 手一景，收景會拖（實測：9.5 手／景）",
        )
    seconds = turn.get("seconds") or 0
    waits = turn.get("max_waits") or 0
    extra = turn.get("wait_extra_seconds") or 0
    if seconds:
        worst = (seconds + extra * waits) / 60
        rep.facts["hand_wall_max_min"] = round(worst, 1)
        if seconds >= 180 and waits >= 2:
            rep.info(
                "loop.hand_wall",
                f"一手最壞要等 {worst:.0f} 分（{seconds}s × (1+{waits}) ＋ 加時）——超時率高的桌先降 max_waits／加時，再降秒數",
            )


def lint_scenario(dir_path: str) -> Report:
    rep = Report(dir_path)
    missing = [name for name in REQUIRED if not os.path.exists(os.path.join(dir_path, name))]
    if missing:
        rep.warn("files.missing", "缺檔（這不是本工具的職責，先去跑 check_module.sh）：" + ", ".join(missing))
        return rep

    goals = load(os.path.join(dir_path, "goals.json"))
    clues = load(os.path.join(dir_path, "clues.json"))
    scenario = load(os.path.join(dir_path, "scenario.json"))
    config = None
    for name in ("config.json", "config.example.json"):
        path = os.path.join(dir_path, name)
        if os.path.exists(path):
            config = load(path)
            break

    claim_ids = [c["id"] for c in clues.get("claims", []) if isinstance(c, dict) and c.get("id")]
    check_clues(rep, goals, clues, scenario)
    check_goals(rep, goals, scenario, claim_ids)
    seats = load(os.path.join(dir_path, "seats.json"))
    check_seats(rep, seats, goals)
    check_loop(rep, config, seats)
    board_path = os.path.join(dir_path, "board.json")
    if os.path.exists(board_path):
        check_board(rep, load(board_path))
    else:
        rep.info("board.none", "沒有 board.json（抽象桌）——地點與移動靠文字，P3 的檢查不適用；改成確認 GM.md 有寫「去哪裡」的判準")
    endings = [e for e in scenario.get("endings", []) if isinstance(e, dict)]
    check_endings(rep, scenario, config, endings)
    check_clock(rep, scenario)
    check_gm(rep, os.path.join(dir_path, "GM.md"))

    rep.facts.update(
        goals=len(goals.get("goals", []) or []),
        claims=len(claim_ids),
        fragments=len(clues.get("fragments", []) or []),
    )
    return rep


def scenarios_of(module_dir: str) -> list[str]:
    root = os.path.join(module_dir, "scenarios")
    if not os.path.isdir(root):
        return []
    return [
        os.path.join(root, name)
        for name in sorted(os.listdir(root))
        if os.path.isdir(os.path.join(root, name)) and os.path.exists(os.path.join(root, name, "scenario.json"))
    ]


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("-")]
    strict = "--strict" in argv
    as_json = "--json" in argv
    if not args:
        print((__doc__ or "").strip().splitlines()[0] if __doc__ else "design_lint")
        print("用法：python3 tools/design_lint.py <模組目錄|劇本目錄> [--strict] [--json]")
        return 2

    target = os.path.abspath(args[0])
    if not os.path.isdir(target):
        print(f"找不到目錄：{target}")
        return 2
    if os.path.exists(os.path.join(target, "scenario.json")):
        targets = [target]
    else:
        targets = scenarios_of(target)
    if not targets:
        print(f"{target} 底下沒有劇本（找不到 scenarios/*/scenario.json）")
        return 2

    reports = [lint_scenario(path) for path in targets]
    if as_json:
        payload = [
            {
                "scenario": os.path.relpath(rep.label, target) if rep.label.startswith(target) else rep.label,
                "facts": rep.facts,
                "issues": [{"level": lv, "code": code, "message": msg} for lv, code, msg in rep.items],
            }
            for rep in reports
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        total_warn = 0
        for rep in reports:
            rel = os.path.relpath(rep.label, target)
            print(f"\n== {rel if rel != '.' else os.path.basename(rep.label)}")
            if rep.facts:
                facts = " / ".join(f"{k} {v}" for k, v in rep.facts.items())
                print(f"   {facts}")
            if not rep.items:
                print("   沒有設計味道（不代表好玩，只代表沒有明顯的卡死結構）")
            for level, code, message in rep.items:
                print(f"   [{level}] {code:26s} {message}")
            total_warn += rep.warns
        print(f"\n摘要：{len(reports)} 個劇本，WARN {total_warn} 條（WARN 要人看一眼，INFO 可能是刻意的）")

    if strict and any(rep.warns for rep in reports):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
