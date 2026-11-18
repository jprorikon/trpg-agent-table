#!/usr/bin/env python3
"""桌子自测：把「最小闭环」全部真跑一遍。

覆盖：
  1. REST 闭环：入座 / 设场景 / 回合自动流转 / 攻击结算（骰子与伤害可复算）
  2. 信息防火墙：GM 给 A 的私密信息不出现在 B 的 view 里
  3. 唤醒（kick）：轮到时真的 POST 出去了，payload 含该席位可见内容、不含他人秘密
  4. 事件日志回放：内存状态 == 从事件流重放出来的状态
  5. MCP(HTTP)：用官方 mcp SDK 当客户端，initialize / tools/list / tools/call 全通
  6. MCP(stdio)：table_mcp.py 桥的原始 JSON-RPC 握手
  9. 初期属性掷骰（rules.attr_roll）：/api/roll_attrs 唯讀可重問、join 不吃客户端属性

运行：python3 test_table.py
"""

from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo 根
os.environ.setdefault("TRPG_LEGACY_REST", "1")   # 直接跑這支也要能打 `/api/*`（生產已退役）
PORT = 8931
WAKE_PORT = 8932
ATTR_PORT = 8941
BASE = f"http://127.0.0.1:{PORT}"

OK, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (OK if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


def post(path: str, body: dict) -> dict:
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:  # noqa: F821
        return json.loads(e.read().decode())


def get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=20) as r:
        return json.loads(r.read().decode())


# --- 假唤醒接收端 ---------------------------------------------------------

WAKES: list = []


class WakeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        WAKES.append(json.loads(self.rfile.read(n).decode()))
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="trpg_test_")
    # ★ 2026-10-24（review R3）：從前讀**倉庫根目錄的 `config.json`**——那是本機的單桌開發檔，
    #   **不進版控** ⇒ 乾淨 clone 上這支直接 FileNotFoundError。改成讀版控裡的
    #   `config.example.json`（就是「最小單桌」的範本），再照測試自己的需要覆寫；
    #   沒有模組／包／節拍的那張桌是這支測試的前提，所以範本裡的那幾段一律拿掉。
    cfg = json.load(open(os.path.join(HERE, "config.example.json"), encoding="utf-8"))
    for _k in ("kick", "wake", "packs", "pacing"):
        cfg.pop(_k, None)
    cfg["port"] = PORT
    cfg["table_id"] = "selftest"
    cfg.setdefault("auth", {})["gm_token"] = "test-token"
    cfg["turn"].update({"auto_start_first_turn": False, "seconds": 6,
                        "policy_on_timeout": "wait", "wait_extra_seconds": 3, "max_waits": 1})
    cfg["seed"] = 777
    # ★ 2026-10-20 起 kick **按帳號**（`account.wake_url`）：這支測試沒有帳號層，
    #   所以打開遷移期的 `wake.legacy_seat_url`，讓「席位寫死的 wake_url」照舊生效
    #   （測的是「輪到了會不會真的 POST 出去」，不是帳號解析）。
    cfg.setdefault("wake", {})["legacy_seat_url"] = True
    cfg_path = os.path.join(tmp, "config.json")
    json.dump(cfg, open(cfg_path, "w", encoding="utf-8"), ensure_ascii=False)

    # 唤醒接收端
    wake_httpd = ThreadingHTTPServer(("127.0.0.1", WAKE_PORT), WakeHandler)
    threading.Thread(target=wake_httpd.serve_forever, daemon=True).start()

    print("== 起桌 ==")
    srv = subprocess.Popen([sys.executable, os.path.join(HERE, "server.py"),
                            "--config", cfg_path, "--home", tmp, "--port", str(PORT),
                            "--bind", "127.0.0.1"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        for _ in range(50):
            try:
                if get("/health")["status"] == "ok":
                    break
            except Exception:  # noqa: BLE001
                time.sleep(0.2)
        else:
            print("服务起不来：", srv.stdout.read() if srv.stdout else "")
            return 1

        print("== 1. REST 闭环 ==")
        gm = post("/api/join", {"name": "GM", "kind": "gm"})["seat"]
        human = post("/api/join", {"name": "深海", "kind": "human"})["seat"]
        agent = post("/api/join", {"name": "ムツキ", "kind": "agent",
                                   "attrs": {"str": 1, "agi": 4, "wil": 3},
                                   "wake_url": f"http://127.0.0.1:{WAKE_PORT}/kick"})["seat"]
        check("三个席位入座", (gm, human, agent) == ("seat1", "seat2", "seat3"),
              f"{gm}/{human}/{agent}")
        hp_expected = 8 + 2 * 1  # base + per_str*str（str=1）
        v = get(f"/api/state?seat={agent}")
        check("HP 由规则算出（8+2*str）", v["you"]["hp"] == hp_expected,
              f"hp={v['you']['hp']} expect={hp_expected}")

        # 2026-10-21 拆景：GM 不再開景，改成「每輪用 say 說一句」——那句話直接進公開日誌
        post("/api/say", {"token": "test-token", "seat": gm,
                          "text": "雾里的入り江，船舷上有人影。"})
        _lg = get(f"/api/state?seat={human}")
        check("GM 的开场叙述可见（say 進公開日誌）",
              "入り江" in json.dumps(_lg.get("log") or [], ensure_ascii=False)
              or "入り江" in json.dumps(_lg, ensure_ascii=False))

        post("/api/gm", {"token": "test-token", "op": "secret", "to": human,
                         "seat": gm, "text": "暗号是 0721"})
        vh = get(f"/api/state?seat={human}")
        va = get(f"/api/state?seat={agent}")
        check("私密信息送达持有者", any("0721" in s["text"] for s in vh["your_secrets"]))
        check("私密信息不泄漏给别的席位",
              "0721" not in json.dumps(va, ensure_ascii=False)
              and not va.get("your_secrets"))

        print("== 2. 回合与行动（骰子可复算） ==")
        post("/api/gm", {"token": "test-token", "op": "start_turn", "seat": human})
        st = get(f"/api/state?seat={human}")["turn"]
        check("回合开在指定席位", st["seat"] == human and st["is_yours"] is True,
              f"turn={st}")

        r = post("/api/act", {"seat": human, "action": "attack", "target": agent, "dc": 1})
        roll = r["result"]["roll"]
        exp_val = random.Random(f"777:{roll['roll_seq']}").randint(1, 20)
        check("攻击骰值可复算（seed:roll_seq）", roll["value"] == exp_val,
              f"logged={roll['value']} recomputed={exp_val}")
        dmg_roll_seq = roll["roll_seq"] + 1
        exp_dmg = max(1, random.Random(f"777:{dmg_roll_seq}").randint(1, 6) + 0)  # str 2? human 默认 2 → +2
        exp_dmg = max(1, random.Random(f"777:{dmg_roll_seq}").randint(1, 6) + 2)
        check("伤害 = damage die + str 修正", r["result"]["damage"] == exp_dmg,
              f"dm={r['result']['damage']} expect={exp_dmg}")
        check("HP 扣减与事件一致", get(f"/api/state?seat={agent}")["you"]["hp"] == hp_expected - exp_dmg)
        check("行动后回合自动交给下一席", r["result"].get("turn_ended") is True
              and get(f"/api/state")["turn"]["seat"] == agent)

        print("== 3. 唤醒（kick） ==")
        for _ in range(20):
            if WAKES:
                break
            time.sleep(0.3)
        check("轮到时 kick 真的 POST 出去", bool(WAKES), f"收到 {len(WAKES)} 条")
        if WAKES:
            p = WAKES[-1]
            check("kick payload 指向正确席位与回合", p["seat"] == agent and p["kind"] == "trpg_turn",
                  f"seat={p['seat']} turn_seq={p['turn_seq']}")
            check("kick 里带该席位可见局面", agent in p["prompt"] and "剩" in p["prompt"])
            check("kick 不带别人的暗号", "0721" not in p["prompt"])
        log = get("/api/log?gm_token=test-token")["events"]
        check("kick 成败进日志（没静默丢弃）",
              any(e["type"] == "kick" for e in log))

        print("== 4. 超时策略 ==")
        # 当前是 agent 的回合，deadline=6s，policy=wait 一次
        time.sleep(7.5)
        ext = [e for e in get("/api/log?gm_token=test-token")["events"] if e["type"] == "turn_extended"]
        check("超时先宽限（policy=wait）", len(ext) >= 1, f"extended={len(ext)}")
        time.sleep(4.5)
        to = [e for e in get("/api/log?gm_token=test-token")["events"] if e["type"] == "seat_timeout"]
        check("宽限用尽后按策略放行", len(to) >= 1, f"timeouts={len(to)}")

        print("== 4b. 整桌暂停（總閘；2026-10-06 設計者要的按鈕） ==")
        post("/api/gm", {"token": "test-token", "op": "start_turn", "seat": human})
        _t0 = get("/api/state")["turn"]
        _d0 = time.time() + _t0["seconds_left"]        # 暫停前的死線（近似＝現在＋剩餘）
        _p = post("/api/gm", {"token": "test-token", "op": "pause"})
        check("暫停：op=pause 回報已暫停，公開投影看得到",
              _p["pause"]["paused"] is True and get("/api/state")["paused"] is True
              and get(f"/api/state?seat={human}")["paused"] is True,
              json.dumps(_p.get("pause"), ensure_ascii=False)[:120])
        _log0 = len(get("/api/log?gm_token=test-token")["events"])
        _wake0 = len(WAKES)
        _ar = post("/api/act", {"seat": human, "action": "defend"})
        check("暫停中：牌面動作被拒（整桌凍結——連結束回合也不吃）",
              "error" in _ar and "暫停" in _ar["error"], json.dumps(_ar, ensure_ascii=False)[:120])
        _sr = post("/api/say", {"seat": human, "text": "暫停中也可以說話"})
        check("暫停中：說話仍可（凍的是牌面，不是對話）",
              "view" in _sr and any("暫停中也可以說話" in str(x) for x in _sr["view"].get("recent") or []),
              json.dumps(_sr, ensure_ascii=False)[:140])
        post("/api/tell", {"seat": human, "to": agent, "text": "暫停中也要說一句"})
        time.sleep(9)                     # ＞ deadline(6s)＋節拍(2s)：沒暫停早就逾時了
        _log1 = get("/api/log?gm_token=test-token")["events"]
        check("暫停中：計時／逾時整塊凍住（9 秒過去，沒有 seat_timeout／turn_extended）",
              not [e for e in _log1[_log0:] if e["type"] in ("seat_timeout", "turn_extended")],
              str([e["type"] for e in _log1[_log0:]]))
        _sl0 = (get("/api/state")["turn"] or {}).get("seconds_left")
        time.sleep(2.2)
        _sl1 = (get("/api/state")["turn"] or {}).get("seconds_left")
        check("暫停中：剩餘秒數停在暫停那一刻（2.2 秒過去，seconds_left 不動——設計者 2026-10-06）",
              _sl0 == _sl1 and _sl0 is not None, f"{_sl0} → {_sl1}")
        check("暫停中：回合還停在原地（沒人被推走）",
              (get("/api/state")["turn"] or {}).get("seq") == _t0["seq"],
              f"{(get('/api/state')['turn'] or {}).get('seq')} vs {_t0['seq']}")
        check("暫停中：私訊不叫醒（自動的腳全被總閘擋掉）", len(WAKES) == _wake0,
              f"WAKES {_wake0} → {len(WAKES)}")
        post("/api/gm", {"token": "test-token", "op": "kick", "seat": agent,
                         "turn_seq": _t0["seq"]})
        time.sleep(0.8)
        check("暫停中：GM 手動補踢（op=kick）仍然出去——總閘擋的是自動的腳",
              len(WAKES) > _wake0, f"WAKES {_wake0} → {len(WAKES)}")
        _r = post("/api/gm", {"token": "test-token", "op": "resume"})
        check("解除：paused 回 false，且回報凍掉了幾秒",
              _r["pause"]["paused"] is False and _r["pause"]["frozen_seconds"] >= 8,
              json.dumps(_r.get("pause"), ensure_ascii=False)[:140])
        _frozen = float(_r["pause"]["frozen_seconds"])
        _d1 = float(_r["pause"]["deadline_at"])
        # 死線＝暫停前的死線＋凍掉的那段**一次**（推兩倍的話會多出一個 frozen）
        check("解除後死線整段順延、且只順延一次（暫停不吃他的時間；不會推兩倍）",
              abs(_d1 - (_d0 + _frozen)) <= 1.0,
              f"deadline {_d1:.1f} vs 期望 {(_d0 + _frozen):.1f}（frozen={_frozen}）")
        _log2 = len(get("/api/log?gm_token=test-token")["events"])
        _hit = []
        for _ in range(30):
            _hit = [e for e in get("/api/log?gm_token=test-token")["events"][_log2:]
                    if e["type"] in ("turn_extended", "seat_timeout")]
            if _hit:
                break
            time.sleep(0.5)
        check("解除後：計時回來（順延的死線照樣走到逾時／寬限）",
              bool(_hit), str([e["type"] for e in _hit]))
        _ev = [e["type"] for e in get("/api/log?gm_token=test-token")["events"]]
        check("暫停／解除進事件流（公開；重放一致由第 5 節一起複驗）",
              "pacing_paused" in _ev and "pacing_resumed" in _ev)

        print("== 5. 事件日志回放 ==")
        rep = post("/api/gm", {"token": "test-token", "op": "replay"})
        check("重放状态与内存一致", rep.get("replay_matches") is True,
              json.dumps(rep, ensure_ascii=False))

        # 2026-10-21 拆景：`act` 只准当前回合那一席出手 ⇒ 先把回合推到 agent 手上
        for _ in range(12):
            _cur = (get("/api/state") or {}).get("turn") or {}
            if _cur.get("seat") == agent:
                break
            post("/api/gm", {"token": "test-token", "op": "end_turn", "reason": "test"})
        check("回合推到 agent 手上（MCP 那一段的前提）",
              ((get("/api/state") or {}).get("turn") or {}).get("seat") == agent,
              str((get("/api/state") or {}).get("turn")))

        print("== 6. MCP over HTTP（官方 SDK 当客户端） ==")
        try:
            import anyio
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client

            async def mcp_probe():
                async with streamablehttp_client(f"{BASE}/mcp") as (r, w, _):
                    async with ClientSession(r, w) as s:
                        init = await s.initialize()
                        tools = await s.list_tools()
                        names = sorted(t.name for t in tools.tools)
                        view = await s.call_tool("table_view", {"seat": agent})
                        vcompact = await s.call_tool("table_view", {"seat": agent, "detail": "compact"})
                        # 先打一個參數錯的（此時還輪到 agent）⇒ 規則錯誤、不消耗這一手；
                        # 再打 defend（拆景後出手只准當前席，順序反過來就會撞「不是你的回合」）
                        bad = await s.call_tool("table_act", {"seat": agent, "action": "attack"})
                        act = await s.call_tool("table_act", {"seat": agent, "action": "defend"})
                        say = await s.call_tool("table_say", {"seat": agent, "text": "……上船。"})
                        joined = await s.call_tool("table_join", {"name": "追加NPC", "kind": "npc"})
                        return init, names, view, vcompact, act, say, bad, joined

            init, names, view, vcompact, act, say, bad, joined = anyio.run(mcp_probe)
            check("MCP initialize 通", "trpg-table" in init.serverInfo.name)
            check("MCP 工具面 = 7 个（含 GM 工具与玩家私信出口）",
                  names == ["table_act", "table_gm", "table_join", "table_say",
                            "table_secret", "table_tell", "table_view"], str(names))
            check("MCP table_view 返回局面", "your_secrets" in view.content[0].text)
            # detail=compact（2026-10-06）：給 agent 省 token 用——省的是重複的日誌投影，
            # 判斷依據（自己／私訊／席位／額度／動作目錄）一樣不少
            _full_txt, _comp_txt = view.content[0].text, vcompact.content[0].text
            check("MCP table_view detail=compact 比 full 小",
                  len(_comp_txt) < len(_full_txt), f"compact={len(_comp_txt)} full={len(_full_txt)}")
            try:
                _cv = json.loads(_comp_txt)
            except Exception:  # noqa: BLE001
                _cv = {}
            check("compact 版保留關鍵欄位、且不帶重複的日誌尾巴",
                  all(k in _cv for k in ("you", "your_secrets", "seats", "allowed_actions",
                                         "actions", "table"))
                  and _cv.get("detail") == "compact" and "recent" not in _cv,
                  str(sorted(_cv.keys()))[:140])
            check("MCP table_act 生效（defend）", not act.isError and "defend" in act.content[0].text,
                  act.content[0].text[:80])
            check("MCP table_say 进日志", "上船" in json.dumps(
                get("/api/log?gm_token=test-token")["events"], ensure_ascii=False))
            check("参数错给的是 rule 错误（不是崩）", bool(bad.isError) and "target" in bad.content[0].text)
            check("MCP join 也能加席位", not joined.isError and "seat" in joined.content[0].text)
        except ImportError as exc:
            check("mcp SDK 可用", False, str(exc))

        print("== 7. stdio 桥（原始 JSON-RPC） ==")
        env = dict(os.environ, TRPG_TABLE_URL=f"{BASE}/mcp")
        p = subprocess.Popen([sys.executable, os.path.join(HERE, "table_mcp.py")],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env)
        spec = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "raw", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "table_view", "arguments": {"seat": agent}}},
        ]
        out, _ = p.communicate("\n".join(json.dumps(x) for x in spec) + "\n", timeout=30)
        replies = [json.loads(l) for l in out.splitlines() if l.strip()]
        ids = sorted(r["id"] for r in replies if "id" in r)
        check("stdio 桥：三条请求都有应答，通知无应答", ids == [1, 2, 3], str(ids))
        tl = [r for r in replies if r.get("id") == 2][0]
        check("stdio 桥：tools/list 有 7 个工具", len(tl["result"]["tools"]) == 7)

        print("== 8. 後台不送網頁（2026-10-19 拆前後台）==")
        try:
            urllib.request.urlopen(BASE + "/", timeout=10)
            _st_page = 200
        except urllib.error.HTTPError as _e:  # noqa: PERF203
            _st_page = _e.code
        check("`/` ⇒ 410（頁面在前台容器；後台只有 /graphql 與 /mcp）", _st_page == 410, f"HTTP {_st_page}")
        # 這張桌（root 設定）沒開 rules.attr_roll ⇒ /api/roll_attrs 必須回 409（不是 200 假裝有）
        try:
            urllib.request.urlopen(BASE + "/api/roll_attrs?seat=seat1", timeout=10)
            _st_off = 200
        except urllib.error.HTTPError as _e:  # noqa: PERF203
            _st_off = _e.code
        check("沒開 attr_roll 的桌 /api/roll_attrs 回 409", _st_off == 409, f"HTTP {_st_off}")

    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except Exception:  # noqa: BLE001
            srv.kill()
        wake_httpd.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)

    # ── 9. 初期屬性擲骰（rules.attr_roll；協作端 2026-10-05 §2325）────────────
    #    專為「初期屬性點只能用骰的」另起一張桌（attr_roll=true）端到端驗：
    #    端點唯讀可重問、join 不吃客戶端硬塞的屬性、原始流看得出是骰的。
    print("== 9. 初期属性掷骰（rules.attr_roll） ==")
    # 同一份範本（倉庫根的 `config.json` 不進版控；見上面第 1 節的說明）
    c2 = json.load(open(os.path.join(HERE, "config.example.json"), encoding="utf-8"))
    for _k in ("kick", "wake", "packs", "pacing"):
        c2.pop(_k, None)
    c2["port"] = ATTR_PORT
    c2["table_id"] = "attrroll"
    c2["auth"]["gm_token"] = "attr-token"
    c2["seed"] = 4242
    c2["rules"]["attr_roll"] = True
    tmp2 = tempfile.mkdtemp(prefix="trpg_attrroll_")
    cfg2_path = os.path.join(tmp2, "config.json")
    json.dump(c2, open(cfg2_path, "w", encoding="utf-8"), ensure_ascii=False)
    B2 = f"http://127.0.0.1:{ATTR_PORT}"

    def call2(path: str, body: dict | None = None, method: str = "GET"):
        req = urllib.request.Request(
            B2 + path, data=(json.dumps(body).encode() if body is not None else None),
            headers={"Content-Type": "application/json"}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:  # noqa: PERF203
            return e.code, json.loads(e.read().decode())

    srv2 = subprocess.Popen([sys.executable, os.path.join(HERE, "server.py"),
                             "--config", cfg2_path, "--home", tmp2, "--port", str(ATTR_PORT),
                             "--bind", "127.0.0.1"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        for _ in range(50):
            try:
                if call2("/health")[1]["status"] == "ok":
                    break
            except Exception:  # noqa: BLE001
                time.sleep(0.2)
        else:
            print("attr_roll 桌起不来：", srv2.stdout.read() if srv2.stdout else "")
            return 1
        lo2, hi2 = c2["rules"]["attr_range"]
        st, ra = call2("/api/roll_attrs?seat=seat1")
        check("GET /api/roll_attrs 回 200 且三屬性都落在 attr_range",
              st == 200 and set(ra["attrs"]) == set(c2["rules"]["attributes"])
              and all(lo2 <= v <= hi2 for v in ra["attrs"].values()), f"HTTP {st} {ra}")
        check("重問同一席＝同一組（seed＋席位＋屬性，不能重擲到滿意）",
              call2("/api/roll_attrs?seat=seat1")[1]["attrs"] == ra["attrs"], str(ra["attrs"]))
        st, jr = call2("/api/join", {"seat": "seat1", "name": "骰子", "kind": "human",
                                     "attrs": {"str": 5, "agi": 5, "wil": 5}}, "POST")
        check("attr_roll 开＝join 忽略客户端硬塞的属性（吃骰子值）",
              st == 200 and jr["view"]["you"]["attrs"] == ra["attrs"],
              f"client 5/5/5 -> {jr.get('view', {}).get('you', {}).get('attrs')}")
        sj = [e for e in call2("/api/log?gm_token=attr-token")[1]["events"]
              if e.get("type") == "seat_joined" and e.get("seat") == "seat1"]
        check("seat_joined 带 attrs_rolled=true（原始流看得出这席是骰的）",
              bool(sj) and sj[0].get("attrs_rolled") is True, str(sj[:1])[:160])
    finally:
        srv2.terminate()
        try:
            srv2.wait(timeout=5)
        except Exception:  # noqa: BLE001
            srv2.kill()
        shutil.rmtree(tmp2, ignore_errors=True)

    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    if FAIL:
        for f in FAIL:
            print("  FAILED:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
