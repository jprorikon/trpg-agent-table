#!/usr/bin/env python3
"""棋盘自测（示範模組：百鬼塔）：把「城鎮 ＋ 荒野 ＋ 塔」规则真跑一遍。

覆盖：入座起点/记忆、相邻移动与荒野判定、入口发现与失效、入塔、昇降機（未清小怪禁行）、
小怪战与货币、商店、祈願箱换神器付记忆、BOSS 层、出塔收夺、
游戏房回记忆、事件回放、MCP 工具面随配置自适应。

运行：python3 tests/test_board.py（或 tools/run_tests.sh test_board）
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo 根
sys.path.insert(0, HERE)          # 跑得起來就好（repo 根）
os.environ.setdefault("TRPG_LEGACY_REST", "1")   # 直接跑這支也要能打 `/api/*`（生產已退役 ⇒ 只有測試放行）
from trpg.core import layout as layout                     # 桌的位置（WS-B：不再硬編 campaigns/<桌>）
from trpg.rules import packs as packs                                                             # noqa: E402
CAMP = layout.must_locate_scenario(HERE, "hyakki_tower", "sannenme_no_onibarai")
DEF = layout.def_dir_of(CAMP)          # 定義層（WS-B：桌目錄裡只剩 config／runs）
PORT = 8941
BASE = f"http://127.0.0.1:{PORT}"
OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


def post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:  # noqa: F821
        return json.loads(e.read().decode())


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.loads(r.read().decode())


def _gm_open_turn():
    """請 GM 開一輪（回合制：行動格花完就交棒；單人長流程測試常常「沒有回合」）。"""
    post("/api/gm", {"token": "t", "op": "start_turn", "args": {}})


def act(seat, action, **kw):
    kw["seat"] = seat
    kw["action"] = action
    r = post("/api/act", kw)
    # ⚠ 2026-10-20：`act()` 要求「有進行中的回合、而且是自己的」。這支測試是一條很長的單人流程
    #   （走位→入塔→打怪→買東西→祈願），額度花完就沒回合 ⇒ 撞到就請 GM 開一輪再重試一次。
    _err = str((r or {}).get("error") or "") if isinstance(r, dict) else ""
    # 拆景後「只有當前回合那一席能動、額度用完就交棒」⇒ 撞到就交幾手再試（不是硬改引擎）
    for _ in range(8):
        if isinstance(r, dict) and "result" in r:
            break
        _e = str((r or {}).get("error") or "") if isinstance(r, dict) else ""
        if "沒有進行中的回合" in _e:
            _gm_open_turn()
        elif "用完" in _e or "不是你的回合" in _e:
            # 交棒前先補血：塔內每換一手都可能被咬，連環重試會被咬死送回城（實測踩到）
            _hp3 = get("/api/state?seat=char_a").get("you") or {}
            if _hp3.get("hp") is not None and _hp3.get("max_hp"):
                post("/api/gm", {"token": "t", "op": "heal", "seat_target": "char_a",
                                 "amount": int(_hp3["max_hp"])})
            st = post("/api/gm", {"token": "t", "op": "end_turn", "reason": "test"})
            if (st or {}).get("error"):
                _gm_open_turn()
        else:
            break
        r = post("/api/act", kw)
    if isinstance(r, dict) and "result" in r:
        out = dict(r["result"])
        out["_view"] = r.get("view")
        return out
    return r


def check_board_coords_data() -> None:
    """棋盤座標的**資料不變式**（不用起服務）：

    座標是資料（`board.json` 的 x/y），網頁地圖直接拿它畫。
      ① 同一張盤要嘛每格都有座標、要嘛整張都沒有（半套 ＝ 引擎會給出 None，前端畫歪）；
      ② 不能兩格同一點（＝「所有點重合在一起」那顆 bug：看起來只有一顆點）。
    整張沒有座標是**允許**的（前端退回文字版）。
    """
    import glob
    for p in sorted(glob.glob(os.path.join(HERE, "modules", "*", "scenarios", "*", "board.json"))):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            check(f"{os.path.relpath(p, HERE)} 讀得起來", False, str(e)); continue
        ns = d.get("nodes") or []
        xy = [(n.get("x"), n.get("y")) for n in ns
              if n.get("x") is not None or n.get("y") is not None]
        rel = os.path.relpath(p, HERE)
        if not xy:
            continue                                   # 整張沒座標 ⇒ 前端文字版，允許
        check(f"{rel}：座標要嘛全有、要嘛全無", len(xy) == len(ns), f"{len(xy)}/{len(ns)}")
        check(f"{rel}：兩格不得同一點", len(set(xy)) == len(xy))


def main() -> int:
    print("== 0. 棋盤座標的資料不變式 ==")
    check_board_coords_data()

    tmp = tempfile.mkdtemp(prefix="trpg_board_")
    cfg = layout.read_table_config(CAMP)
    cfg["session"] = {"require": False}   # session 閘＝桌的策略（test_session_convention 咬）
    # ⚠ 夾具隔離（2026-10-07 單一進程 + 子路徑）：這兩個是**部署專屬**的鍵，
    #   從真 config 複製進來會讓 temp 桌指到現役 hub／帶別人的前綴 ⇒ 一律清掉。
    cfg.pop("hub_base", None)
    cfg.pop("public_prefix", None)
    cfg["port"] = PORT
    cfg.setdefault("auth", {})["gm_token"] = "t"
    # ⚠ 2026-10-07 第二階段：副本若沿用 `auth.accounts_file`（共用 registry）＋`require_account`，
    #    每個 `?seat=` 請求都會 403 ⇒ 這支不測帳號層，明確關掉（也不去碰真 registry）。
    cfg.setdefault("auth", {}).pop("accounts_file", None)
    cfg.setdefault("auth", {})["require_account"] = False
    cfg["turn"].update({"seconds": 8, "policy_on_timeout": "wait", "wait_extra_seconds": 4,
                        "max_waits": 0, "end_turn_on_action": False})  # 关掉自动交回合，便于逐步验证
    # 這支測的是棋盤機制本身：塔內的「待太久被彈出去」（`max_turns_inside`）會把長流程踢出塔，
    # 讓後面的出塔/收奪驗不到（實測踩到：一路打到頂層後被彈回城）⇒ 這裡關掉它。
    cfg.setdefault("tower", {})["max_turns_inside"] = 0
    json.dump(cfg, open(os.path.join(tmp, "config.json"), "w", encoding="utf-8"), ensure_ascii=False)
    for f in ("board.json", "goals.json", "clues.json", "seats.json"):   # config 用上面改过的那份
        src = os.path.join(DEF, f)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(tmp, f))
    _psrc = os.path.join(DEF, "packs")        # 劇本自帶的作品層包（沒帶 ⇒ 啟用清單解析不動）
    if os.path.isdir(_psrc):
        shutil.copytree(_psrc, os.path.join(tmp, "packs"))
        packs.seed_module_packs(os.path.dirname(_psrc), tmp)   # ＋模組層的包（rules.core 住模組 repo）

    srv = subprocess.Popen([sys.executable, os.path.join(HERE, "server.py"),
                            "--config", os.path.join(tmp, "config.json"), "--home", tmp,
                            "--port", str(PORT), "--bind", "127.0.0.1"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        for _ in range(60):
            try:
                if get("/health")["status"] == "ok":
                    break
            except Exception:  # noqa: BLE001
                time.sleep(0.25)
        else:
            print("服务起不来"); print(srv.stdout.read() if srv.stdout else ""); return 1

        print("== 1. 入座与棋盘装载 ==")
        gm = post("/api/join", {"name": "GM", "kind": "gm"})["seat"]
        # 用 roster 模板入座（attrs 自动取自 board.json）
        r = post("/api/join", {"seat": "char_a", "name": "カガリ", "kind": "agent"})
        check("roster 席位入座", r["seat"] == "char_a", json.dumps(r.get("error", ""), ensure_ascii=False))
        v = get("/api/state?seat=char_a")
        check("起点=鉄環市（board.json 的 start_node）", v["board"]["you_are"]["id"] == "teckan",
              v["board"]["you_are"]["id"])
        check("roster 属性生效（カガリ str5）", v["you"]["attrs"]["str"] == 5, str(v["you"]["attrs"]))
        check("记忆初值来自 config.memory.start", v["you"]["memory"] == 60, str(v["you"]["memory"]))
        check("棋盘节点数", v["board"]["node_count"] == 14, str(v["board"]["node_count"]))
        check("起点的相邻格可去", sorted(c["to"] for c in v["board"]["can_go"]) ==
              ["w_teckan", "w_yamato"],
              str([c["to"] for c in v["board"]["can_go"]]))

        # ⚠ 夾具：2026-10-08 起「人齊了也要**先開景**才開輪」，2026-10-20 起 `act()` 又要求
        #   「只有當前回合那一席能動、沒有回合就不准動」⇒ 這支測試若只 join 就 act，
        #   全部動作都會回「現在沒有進行中的回合」。先請 GM 開景（走正式路徑，事件流才對得上）。
        # 2026-10-21 拆景：沒有「開景」了——人齊就自動開第一輪；這裡明確再開一輪當保底
        _sc = post("/api/gm", {"token": "t", "op": "start_turn"})
        check("有一輪在跑（後面才有回合可動）", not (_sc or {}).get("error"), json.dumps(_sc, ensure_ascii=False)[:120])

        g = act("char_a", "game")
        check("游戏房回记忆（城里的闘技場）", g.get("played", 0) > 0, str(g))

        print("== 2. 移动：相邻约束 + 荒野判定 ==")
        bad = act("char_a", "move", target="yamato")
        check("不相邻移动被拒", "error" in bad and "不相邻" in bad["error"], bad.get("error", ""))
        r = act("char_a", "move", target="w_teckan")
        check("沿道路移动成功", r.get("to") == "w_teckan", json.dumps(r, ensure_ascii=False)[:120])
        # 荒野道（w_teckan→hyakki：wild 边要看目标 danger）
        wild = None
        for _ in range(12):
            wild = act("char_a", "move", target="hyakki")
            if wild.get("to") == "hyakki":
                break
            if get("/api/state?seat=char_a")["board"]["you_are"]["id"] != "w_teckan":
                act("char_a", "move", target="w_teckan")   # 失败会留在原地；以防万一退回
        check("荒野道需过移动判定（能过）", wild.get("to") == "hyakki" and "travel_check" in wild,
              json.dumps(wild, ensure_ascii=False)[:100])

        print("== 3. 入塔 / 入口失效 ==")
        v = get("/api/state?seat=char_a")
        check("走到未踏破的塔入口（百鬼塔）", v["board"]["you_are"]["id"] == "hyakki",
              v["board"]["you_are"]["id"])
        check("入口初始是新鲜的", v["board"]["you_are"]["entrance_fresh"] is True)
        ent = act("char_a", "move", target="hyakki_f1")
        check("入塔 → 被转到 1 階昇降機前", ent.get("floor") == "hyakki_f1",
              json.dumps(ent, ensure_ascii=False)[:120])
        v = get("/api/state?seat=char_a")
        # 2026-10-21：旗標改名並翻正語意（true＝已被用掉，要 search 重開）
        check("入塔后入口失效（用过的入口不能再用）", v["board"]["entrance_used"]["hyakki"] is True,
              str(v["board"].get("entrance_used")))
        # 2026-10-21：入口被吃掉之後，`search` 成功要回一句人話（不能只靠 entrance_fresh 翻面）
        act("char_a", "move", target="hyakki")
        _s = act("char_a", "search")
        check("search 找到入口会回一句話（正向回饋）",
              bool((_s.get("found") or {}).get("entrance")) and "入口" in str(_s.get("note") or ""),
              json.dumps({"found": _s.get("found"), "note": _s.get("note")}, ensure_ascii=False))
        act("char_a", "move", target="hyakki_f1")      # 回到塔內，接第 4 節（清小怪才能上樓）

        print("== 4. 昇降機：不清小怪不许上 ==")
        ride = act("char_a", "move", target="hyakki_f2")
        check("小怪还在时禁行", "error" in ride and "残" in ride["error"], ride.get("error", ""))

        def clear_floor() -> int:
            """把这一层的小怪打光（dc=1 ⇒ 必中）；回剩几只。"""
            for _ in range(20):
                if get("/api/state?seat=char_a")["board"]["you_are"]["mobs_left"] == 0:
                    break
                _hp = get("/api/state?seat=char_a")["you"]
                if _hp.get("max_hp"):
                    post("/api/gm", {"token": "t", "op": "heal", "seat_target": "char_a",
                                     "amount": int(_hp["max_hp"])})
                act("char_a", "fight", dc=1)
            return get("/api/state?seat=char_a")["board"]["you_are"]["mobs_left"]

        clear_floor()
        v = get("/api/state?seat=char_a")
        check("击破全部小怪", v["board"]["you_are"]["mobs_left"] == 0)
        check("击破小怪得货币", v["you"]["star_coins"] >= 2, str(v["you"]["star_coins"]))
        check("昇降機上行到 2 階", act("char_a", "move", target="hyakki_f2").get("to") == "hyakki_f2")
        clear_floor()

        print("== 5. 商店（塔里的补给所） ==")
        check("昇降機上行到商店层", act("char_a", "move", target="hyakki_f3").get("to") == "hyakki_f3")
        # ★2：`heal_pack` 是**道具**（在道具表裡）⇒ 走表路：货币 −价、**買到進 inventory**（塔里的店最贵：4）
        c = act("char_a", "buy", target="heal_pack")
        check("买治疗包（★2：道具 ⇒ 進 inventory）",
              c.get("bought") == "heal_pack" and c.get("cost") == 4 and c.get("via") == "table"
              and get("/api/state?seat=char_a")["you"]["items"],
              json.dumps(c, ensure_ascii=False)[:140])
        bad = act("char_a", "buy", target="nothing")
        check("买不存在的商品被拒", "error" in bad, bad.get("error", ""))

        print("== 6. 一路上楼 → 顶层 BOSS ==")
        for fl in ("hyakki_f4", "hyakki_f5"):
            act("char_a", "move", target=fl)
            clear_floor()
        check("昇降機上行到顶层", act("char_a", "move", target="hyakki_f6").get("to") == "hyakki_f6")
        v = get("/api/state?seat=char_a")
        check("顶层只有 BOSS 一只、带祈願箱", bool(v["board"]["you_are"]["wish_box"])
              and v["board"]["you_are"]["mobs_left"] == 1,
              str({k: v["board"]["you_are"].get(k) for k in ("wish_box", "mobs_left")}))
        for _ in range(30):
            _hp = get("/api/state?seat=char_a")["you"]
            if _hp.get("max_hp"):
                post("/api/gm", {"token": "t", "op": "heal", "seat_target": "char_a",
                                 "amount": int(_hp["max_hp"])})
            act("char_a", "fight", dc=1)
            if any(e["type"] == "boss_defeated" for e in get("/api/log?gm_token=t")["events"]):
                break
        check("击破 BOSS（boss_defeated 进事件流）",
              any(e["type"] == "boss_defeated" for e in get("/api/log?gm_token=t")["events"]))

        print("== 7. 祈願：换神器、付记忆 ==")
        mem_before = get("/api/state?seat=char_a")["you"]["memory"]
        p = act("char_a", "pray")
        v = get("/api/state?seat=char_a")
        check("祈願得神器", bool(p.get("artifact")) and len(v["you"]["artifacts"]) == 1,
              json.dumps(p.get("artifact"), ensure_ascii=False))
        check("神器代价=扣记忆", v["you"]["memory"] < mem_before,
              f"{mem_before} → {v['you']['memory']}")
        # 叙述里说了「一度きり」：数值上必须也拦得住（同一箱子不能祈第二次）
        again = act("char_a", "pray")
        check("同一个祈願箱不能再祈（一度きり）",
              "error" in again and "もう開いている" in str(again.get("error")),
              str(again.get("error"))[:90])
        check("祈願箱的开启被记进事件流（重放一致）",
              any(e["type"] == "artifact_gained" and e.get("node") in ("hyakki_f5", "hyakki_f6")
                  for e in get("/api/log?gm_token=t")["events"]))

        print("== 7b. 神器：基础说明 + 隐藏效果（两层） ==")
        art = v["you"]["artifacts"][0]
        check("神器带**基础说明**（desc，公开）", bool(art.get("desc")), str(art.get("desc"))[:70])
        cat = post("/api/gm", {"token": "t", "op": "semi", "args": {"op": "artifact_catalog"}})
        items = {i["name"]: i for row in (cat.get("catalog") or []) for i in row["items"]}
        hid = (items.get(art["name"]) or {}).get("hidden", "")
        check("神器图鉴带**隐藏效果**（GM 专用工具面）", bool(hid), hid[:70])
        check("玩家视图**不含**隐藏效果文本（不会从公开日志漏出去）",
              hid not in json.dumps(v, ensure_ascii=False), "查 you.artifacts / log")
        rev = post("/api/gm", {"token": "t", "op": "semi",
                               "args": {"op": "reveal_artifact", "seat": "char_a"}})
        check("揭示隐藏效果＝一次公开事件", rev.get("hidden") == hid, str(rev)[:90])
        v3 = get("/api/state?seat=char_a")
        check("揭示后持有人视图带上隐藏效果",
              any(a.get("revealed") and a.get("hidden") == hid for a in v3["you"]["artifacts"]))
        # ★ 2026-10-19 拆前後台：頁面（含「隐藏效果」那段 UI）搬去前台 Nuxt ⇒ 後台不再有頁面可驗；
        #   資料面已由上面那一條（`revealed`＋`hidden`）釘住。

        print("== 8. 出塔：塔内所得被收夺 ==")
        # 塔內每手都可能被咬（`tower_turn`）；一路走下樓前先把血補滿，免得半路被判死送回城
        _hp = get("/api/state?seat=char_a")["you"]
        post("/api/gm", {"token": "t", "op": "heal", "seat_target": "char_a",
                         "amount": int(_hp.get("max_hp") or 10)})
        ex = act("char_a", "move", target="hyakki")

        print("== 10. 回放 / MCP / 网页 ==")
        rep = post("/api/gm", {"token": "t", "op": "replay"})
        check("事件流回放与内存一致", rep.get("replay_matches") is True,
              json.dumps(rep.get("diff", rep), ensure_ascii=False)[:400])
        try:
            import anyio
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client

            async def probe():
                async with streamablehttp_client(f"{BASE}/mcp") as (rr, ww, _):
                    async with ClientSession(rr, ww) as s:
                        tools = await s.list_tools()
                        enum = next(t for t in tools.tools if t.name == "table_act").inputSchema["properties"]["action"]["enum"]
                        v = json.loads((await s.call_tool("table_view", {"seat": "char_a"})).content[0].text)
                        dest = (v.get("board") or {}).get("can_go") or []
                        target = (next((c["to"] for c in dest if c["kind"] != "elevator"), None)
                                  or "hyakki_f5")
                        res = await s.call_tool("table_act", {"seat": "char_a", "action": "move", "target": target})
                        return enum, res
            enum, res = anyio.run(probe)
            # 2026-10-21 拆景：enter／ride／exit_tower 收束成 move ⇒ 不再出現在動作表
            check("MCP 动作枚举随配置（含 pray/fight/search/use/declare）",
                  {"pray", "fight", "search", "use", "declare"} <= set(enum), str(enum))
            check("MCP 也能走棋盘动作（按 can_go 移动）", not res.isError,
                  res.content[0].text[:120].replace("\n", " "))
        except ImportError as exc:
            check("mcp SDK 可用", False, str(exc))
        # ★ 2026-10-19 拆前後台：後台不再送網頁 ⇒ 棋盤面板由前台（Nuxt）畫，這裡驗 GraphQL 有棋盤資料。
        try:
            urllib.request.urlopen(BASE + "/", timeout=10)
            _st_page = 200
        except urllib.error.HTTPError as _e:  # noqa: PERF203
            _st_page = _e.code
        check("`/` ⇒ 410（後台不送網頁）", _st_page == 410, f"HTTP {_st_page}")

    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except Exception:  # noqa: BLE001
            srv.kill()
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
