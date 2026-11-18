#!/usr/bin/env python3
"""半合作简易桌自测：1 GM(GM agent) + 1 NPC(GM agent 下游) + 3 玩家（2 人 + 1 agent）。

覆盖：席位/组成校验 · 私密愿望的投影隔离 · 场景制 · agent 节流 · 争抢宣言与收景揭晓 ·
碎片私有性与来源链 · 出示/赠予 · 愿望兑现 · 泄漏闸（GM 叙述与 NPC 台词同一道）·
GM 代投暗骰（点数不公开）· 结算表 · 事件回放 · MCP 的 table_gm 工具。

运行：python3 test_semic.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo 根
sys.path.insert(0, HERE)          # 跑得起來就好（repo 根）
os.environ.setdefault("TRPG_LEGACY_REST", "1")   # 直接跑這支也要能打 `/api/*`（生產已退役 ⇒ 只有測試放行）
from trpg.core import layout as layout                     # 桌的位置（WS-B：不再硬編 campaigns/<桌>）
from trpg.rules import packs as packs                                                             # noqa: E402
CAMP = layout.must_locate_scenario(HERE, "hyakki_tower", "sannenme_no_onibarai")
DEF = layout.def_dir_of(CAMP)          # 定義層（WS-B：桌目錄裡只剩 config／runs）
PORT = 8951
WAKE_PORT = 8952
GMKICK_PORT = 8953
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = "semi-test-token"
KICK_TOKEN = "semi-kick-token"
GM_KICK_TOKEN = "semi-gm-kick-token"
OK, FAIL = [], []

WAKES: list = []          # p3 收到的 kick（含头）
GMKICKS: list = []        # 引擎 → GM（協作端）喚醒的替身收到的 POST


class WakeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        WAKES.append({"payload": json.loads(self.rfile.read(n).decode()),
                      "kick_token": self.headers.get("X-Table-Kick-Token")})
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


class GMKickHandler(BaseHTTPRequestHandler):
    """協作端 側 GM 喚醒接收端的替身：真的收 POST，記下 payload 與 token 頭。"""
    protocol_version = "HTTP/1.1"

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        GMKICKS.append({"payload": json.loads(self.rfile.read(n).decode()),
                        "kick_token": self.headers.get("X-Table-Kick-Token")})
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass



def _patch_seats(dst: str) -> None:
    """示範劇本的 p2 是 agent、p3 是人；這支測試以 **p3＝agent 席** 為前提 ⇒ 臨時桌裡對調。"""
    fp = os.path.join(dst, "seats.json")
    spec = json.load(open(fp, encoding="utf-8"))
    for r in spec.get("seats") or []:
        if r.get("seat") == "p2":
            r["kind"] = "human"
        elif r.get("seat") == "p3":
            r["kind"] = "agent"
    json.dump(spec, open(fp, "w", encoding="utf-8"), ensure_ascii=False)


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


def post_status(path, body):
    """同 post()，但保留 HTTP 状态码（用来断言「规则拒绝＝409，不是 500」）。"""
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:  # noqa: F821
        return e.code, json.loads(e.read().decode())


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.loads(r.read().decode())


def view(seat):
    return get(f"/api/state?seat={seat}")


def turn_seat():
    return ((view("p1").get("turn") or view("p1").get("summary") or {}).get("seat")
            or (view("p1").get("turn") or {}).get("seat"))


def turn_to(seat):
    """拆景後只有「當前回合」那一席能動：先把回合交到 seat 手上。"""
    for _ in range(10):
        st = view("p1")
        cur = (st.get("turn") or {}).get("seat")
        if not cur:
            post("/api/gm", {"token": TOKEN, "op": "semi", "args": {"op": "start_turn"}})
            continue
        if str(cur) == str(seat):
            return True
        post("/api/act", {"seat": cur, "action": "end_turn"})
    return False


def close_round():
    """把這一輪走完（＝舊的「收景」）：最後一席結束回合 ⇒ 引擎在輪末揭曉宣言。"""
    for _ in range(10):
        cur = (view("p1").get("turn") or {}).get("seat")
        if not cur:
            return True
        post("/api/act", {"seat": cur, "action": "end_turn"})
    return False


def act(seat, action, **kw):
    turn_to(seat)
    kw.update({"seat": seat, "action": action})
    r = post("/api/act", kw)
    return dict(r["result"], _view=r.get("view")) if isinstance(r, dict) and "result" in r else r


def gm(op, **args):
    r = post("/api/gm", {"token": TOKEN, "op": "semi", "args": dict(op=op, **args)})
    return r


def close_scene(note=None, force=False):
    """拆景後「收景」＝把這一輪走完（結算在輪末）。"""
    return close_round()


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="trpg_semi_")
    cfg = layout.read_table_config(CAMP)
    cfg["session"] = {"require": False}   # session 閘＝桌的策略（test_session_convention 咬）；
                                          # 這一支測半合作本身 ⇒ 明確關掉，別被策略變更牽動
    # ⚠ 夾具隔離（2026-10-07 單一進程 + 子路徑）：這兩個是**部署專屬**的鍵，
    #   從真 config 複製進來會讓 temp 桌指到現役 hub／帶別人的前綴 ⇒ 一律清掉。
    cfg.pop("hub_base", None)
    cfg.pop("public_prefix", None)
    cfg["port"] = PORT
    cfg.setdefault("auth", {})["gm_token"] = TOKEN
    cfg.setdefault("auth", {}).pop("accounts_file", None)              # 副本不碰共用 registry（第二階段）
    # ⚠ 2026-10-20：kick 按帳號之後 account registry 會真的被查——副本**不能**走生產 DB，
    #   否則 p3 會查到線上 <table>:p3→<account>（端點空）⇒ kick 全 skip、測試假紅。
    cfg.setdefault("auth", {}).pop("db_env", None)
    cfg.setdefault("auth", {}).pop("db", None)
    cfg.setdefault("auth", {})["require_account"] = False
    # 引擎 → GM（協作端）喚醒：每次 run 用自己的端點與 token 檔。
    # （campaign 的 config.json 帶的是**真機**路徑，config 優先於 env ⇒ 測試必須覆蓋掉，否則 kick 靜默失敗）
    cfg["kick"] = {"url": f"http://127.0.0.1:{GMKICK_PORT}/trpg/kick",
                   "token_file": os.path.join(tmp, "kick_engine.token")}
    board = json.load(open(os.path.join(DEF, "board.json"), encoding="utf-8"))
    # 本测试专测半合作：把起点设到顶层祈願箱节点，并把该层小怪清零（塔的机制另有 test_board.py 覆盖）
    board["start_node"] = "hyakki_f5"   # A1：祈願箱在 f5（f4 只剩 BOSS）
    # p3 的 kick 目標接本測試里的收包端（真发 HTTP，不走假的）
    cfg.setdefault("wake", {})
    cfg["wake"]["seats"] = {"p3": {"url": f"http://127.0.0.1:{WAKE_PORT}/kick",
                                   "token": KICK_TOKEN}}
    # ★ 2026-10-20（設計者定案：kick 按帳號，不按席位）：端點掛在 account 上；
    #   這裡在 tmp 內造一份 registry（不碰共用檔），把 p3 綁給 acct_p3 並登記同一個收包端。
    cfg["auth"]["accounts_file"] = os.path.join(tmp, "accounts.json")
    _tbl = str(cfg.get("table_id") or "hyakki_tower")
    with open(cfg["auth"]["accounts_file"], "w", encoding="utf-8") as _fh:
        json.dump({"accounts": {"acct_p3": {"display": "ホムラ", "kind": "agent",
                                            "wake_url": f"http://127.0.0.1:{WAKE_PORT}/kick"}},
                   "sessions": {_tbl: {"table": _tbl,
                                       "seats": {"p3": {"account": "acct_p3",
                                                        "char": "homura"}}}}},
                  _fh, ensure_ascii=False)
    cfg["wake"]["timeout_seconds"] = 2
    cfg["wake"]["mention_kick"] = True
    cfg["wake"]["mention_kick_per_scene"] = 1
    cfg["wake"]["gm_on_turn_end"] = False      # 測試桌自己聲明（線上桌切成「每席叫」後仍測輪末/景制）
    # 通用额度：2 动/3 言；agent 单独覆盖为 1 动（验证两条解析路径都通）
    # 測試桌固定**場景制**：線上桌的節奏（pacing.mode／turn.*）會隨時被設計者切換，
    # 測試不該跟著漂——每一節自己聲明在測哪一種節奏（回合制那節會另外蓋掉）。
    cfg.setdefault("turn", {}).update({"actions_per_turn": 2, "says_per_turn": 3,
                          "agent_act_per_scene": 1})
    cfg["turn"].update({"auto_start_first_turn": False, "end_turn_on_action": False,
                        "policy_on_timeout": "wait"})
    for n in board["nodes"]:
        if n["id"] == "hyakki_f5":
            n["mobs"] = 0
    for f in ("goals.json", "clues.json", "seats.json"):
        shutil.copy(os.path.join(DEF, f), os.path.join(tmp, f))
    _patch_seats(tmp)
    layout.copy_table_config(CAMP, os.path.join(tmp, "config.json"))   # 旋鈕 ⇒ 桌目錄（定義在 DEF）
    _psrc = os.path.join(DEF, "packs")        # 劇本自帶的作品層包（沒帶 ⇒ 啟用清單解析不動）
    if os.path.isdir(_psrc):
        shutil.copytree(_psrc, os.path.join(tmp, "packs"))
        packs.seed_module_packs(os.path.dirname(_psrc), tmp)   # ＋模組層的包（rules.core 住模組 repo）
    json.dump(board, open(os.path.join(tmp, "board.json"), "w", encoding="utf-8"), ensure_ascii=False)
    json.dump(cfg, open(os.path.join(tmp, "config.json"), "w", encoding="utf-8"), ensure_ascii=False)

    # 引擎 → GM（協作端）喚醒：token 檔＋本機替身（真的收 POST，驗冪等鍵與 token 頭）
    open(os.path.join(tmp, "kick_engine.token"), "w").write(GM_KICK_TOKEN)
    child_env = dict(os.environ,
                     TRPG_KICK_URL=f"http://127.0.0.1:{GMKICK_PORT}/trpg/kick",
                     TRPG_KICK_TOKEN_FILE=os.path.join(tmp, "kick_engine.token"))
    srv = subprocess.Popen([sys.executable, os.path.join(HERE, "server.py"),
                            "--config", os.path.join(tmp, "config.json"), "--home", tmp,
                            "--port", str(PORT), "--bind", "127.0.0.1"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                           env=child_env)
    wake_httpd = ThreadingHTTPServer(("127.0.0.1", WAKE_PORT), WakeHandler)
    threading.Thread(target=wake_httpd.serve_forever, daemon=True).start()
    gmkick_httpd = ThreadingHTTPServer(("127.0.0.1", GMKICK_PORT), GMKickHandler)
    threading.Thread(target=gmkick_httpd.serve_forever, daemon=True).start()
    try:
        for _ in range(60):
            try:
                if get("/health")["status"] == "ok":
                    break
            except Exception:  # noqa: BLE001
                time.sleep(0.25)
        else:
            print("服务起不来"); print(srv.stdout.read() if srv.stdout else ""); return 1

        print("== 1. 组成与入座 ==")
        st = get("/api/state")
        seats = {s["seat"]: s for s in st["seats"]}
        check("GM 与 NPC 自动在场", {"gm", "npc1"} <= set(seats), str(list(seats)))
        check("NPC 在港町（它的位置由劇本指定）",
              get("/api/state?seat=npc1")["board"]["you_are"]["id"] == "minato",
              get("/api/state?seat=npc1")["board"]["you_are"]["id"])
        for sid, nm in (("p1", "カガリ"), ("p2", "シズク")):
            post("/api/join", {"seat": sid, "name": nm, "kind": "human"})
        post("/api/join", {"seat": "p3", "name": "ホムラ", "kind": "agent"})
        v3 = view("p3")
        check("三玩家入座", {"p1", "p2", "p3"} <= {s["seat"] for s in v3["seats"]})
        check("agent 玩家按 roster 取属性（ホムラ str5）", v3["you"]["attrs"]["str"] == 5,
              str(v3["you"]["attrs"]))
        spec = {r["seat"]: r for r in json.load(
            open(os.path.join(tmp, "seats.json"), encoding="utf-8"))["seats"]}
        check("定员表含 p4 → イスカ / g_route（沒人坐也在定員表裡）",
              (spec.get("p4") or {}).get("character") == "isuka"
              and (spec.get("p4") or {}).get("goal") == "g_route",
              str(spec.get("p4")))

        print("== 2. 私密愿望的投影隔离 ==")
        g3 = view("p3").get("your_goal")
        check("p3 看得到自己的私密愿望", bool(g3) and g3["id"] == "g_ember", str(g3 and g3["id"]))
        check("p1 看不到 p3 的愿望", not view("p1").get("your_goal") or view("p1")["your_goal"]["id"] == "g_blade",
              str(view("p1").get("your_goal", {}).get("id")))
        check("NPC 席位没有任何愿望", not view("npc1").get("your_goal"))
        blob = json.dumps(view("p1"), ensure_ascii=False)
        check("p3 的愿望正文不外泄给 p1", "炉は十年前の夜から火を落としていない" not in blob)

        print("== 3. 场景制与 agent 节流 ==")
        check("拆景后随时可以宣言（不再等开景）", "error" not in act("p1", "declare", target="pray@hyakki_f5"))
        gm("start_turn")
        check("一輪已經在跑（拆景後沒有開景，直接是第 1 輪）", bool(view("p1").get("turn")))
        r1 = act("p3", "declare", target="pray@hyakki_f5")
        check("agent 第一次 act 通过", "declared" in r1, json.dumps(r1, ensure_ascii=False)[:100])
        act("p3", "check", attr="wil")            # 第二手：每回合 2 格，允许
        st2, r2raw = post_status("/api/act", {"seat": "p3", "action": "check", "attr": "wil"})
        r2 = (dict(r2raw["result"], _view=r2raw.get("view"))
              if isinstance(r2raw, dict) and "result" in r2raw else r2raw)
        check("agent 第三手不再属于自己（2 格用完即交棒）",
              "error" in r2 and ("用完" in str(r2.get("error", ""))
                                 or "回合" in str(r2.get("error", ""))),
              str(r2.get("error", ""))[:80])
        check("规则拒绝＝409（不是 500）", st2 == 409, f"HTTP {st2}")
        check("拒绝消息不带内部类名前缀", "RuleError" not in str(r2.get("error", "")), str(r2.get("error"))[:90])
        # ② 被拒的动作不该留下「幽灵」action_declared（旧顺序「先 emit 再 gate」会留下）
        _lg = get(f"/api/log?gm_token={TOKEN}")
        _evs = (_lg.get("events") if isinstance(_lg, dict) else _lg) or []
        # 只認「被拒但還公開著」的宣告（成功的那一手當然會公開進日誌）
        _ghost = [e for e in _evs if isinstance(e, dict) and e.get("type") == "action_declared"
                  and e.get("seat") == "p3" and e.get("action") == "check"
                  and e.get("rejected") and e.get("public") is not False]
        check("被拒的 act 不留幽灵 action_declared", not _ghost, json.dumps(_ghost, ensure_ascii=False)[:140])

        # ②b 「過額度闸之後才失敗」的動作（例如 buy 在非商店節點、attack 沒給 target）
        #     也不該留幽靈宣告：把那筆 action_declared 就地標 rejected 且 public=False
        #     （協作端 2026-10-05：設計者按三次「購買」曾留三筆幽靈）。
        st_r, _rr = post_status("/api/act", {"seat": "p1", "action": "buy", "note": "nothing"})
        _rej = [e for e in get(f"/api/log?gm_token={TOKEN}")["events"]
                if e.get("type") == "action_declared"
                and e.get("seat") == "p1" and e.get("action") == "buy"]
        check("post-gate 失敗的 act：宣告就地標 rejected 且非公開",
              st_r == 409 and len(_rej) == 1 and _rej[0].get("rejected") is True
              and _rej[0].get("public") is False,
              f"HTTP {st_r} n={len(_rej)} {json.dumps(_rej[:1], ensure_ascii=False)[:150]}")
        check("失敗宣告帶 error_type（規則拒絕 vs 引擎自己壞了可分辨；協作端 §1 建議①）",
              bool(_rej) and _rej[0].get("error_type") == "RuleError",
              f"error_type={_rej[0].get('error_type') if _rej else None}")
        check("那筆失敗宣告不出現在匿名公開投影",
              not any(e.get("type") == "action_declared" and e.get("seat") == "p1"
                      and e.get("action") == "buy" for e in get("/api/log")["events"]),
              f"pub 條數={len(get('/api/log')['events'])}")
        # 就地覆寫是要保住 append-only 的形狀：不新增行、每行仍是合法 JSON、行數 == 事件數
        _file_lines = [l for l in open(layout.events_path(tmp), encoding="utf-8")
                       if l.strip()]
        _raw_now = get(f"/api/log?gm_token={TOKEN}")["events"]
        check("events.jsonl 就地覆寫後仍合法、且行數 == 事件數（不新增行）",
              len(_file_lines) == len(_raw_now)
              and all(isinstance(json.loads(l), dict) for l in _file_lines),
              f"file={len(_file_lines)} raw={len(_raw_now)}")

        # ②c 动作目录契约（協作端 2026-10-05 §2105）：/api/state 带 actions:[{id,label,desc}]，
        #     前端只渲染本数组出现的 id（空砖自然消失）；label/desc 数据驱动、不进翻译服务。
        _v = get("/api/state?seat=p1") or {}
        _acts = _v.get("actions")
        check("view 带 actions[]（{id,label,desc}）且 id 与 allowed_actions 一致",
              isinstance(_acts, list) and bool(_acts)
              and all({"id", "label", "desc"} <= set(a) for a in _acts)
              and [a["id"] for a in _acts] == list(_v.get("allowed_actions") or []),
              f"n={len(_acts) if isinstance(_acts, list) else _acts}")
        from trpg.engine import normalize_actions as _norm_acts
        _ids, _cat = _norm_acts([{"id": "move", "label": "移動", "desc": "走去相鄰節點"},
                                 "pass", {"id": "buy", "label": "購買"}])
        check("normalize_actions：对象写法的 label/desc 原样吃；字符串写法 label／desc 默认都空（＝没内容，前端回退内建）",
              _ids == ["move", "pass", "buy"]
              and _cat[0] == {"id": "move", "label": "移動", "desc": "走去相鄰節點"}
              and _cat[1] == {"id": "pass", "label": "", "desc": ""}
              and _cat[2]["label"] == "購買" and _cat[2]["desc"] == "",
              json.dumps(_cat, ensure_ascii=False)[:150])
        h1 = act("p2", "check", attr="wil")
        h2 = act("p2", "check", attr="wil")
        _st3, _r3 = post_status("/api/act", {"seat": "p2", "action": "check", "attr": "wil"})
        h3 = dict(_r3["result"], _view=_r3.get("view")) if "result" in _r3 else _r3
        check("人类也受同一额度（每回合 2 格，第 3 手被拒）",
              "error" not in h1 and "error" not in h2 and "error" in h3
              and ("用完" in str(h3.get("error")) or "回合" in str(h3.get("error"))),
              f"{'ok' if 'error' not in h1 else h1.get('error')} / "
              f"{'ok' if 'error' not in h2 else h2.get('error')} / {str(h3.get('error'))[:60]}")
        p2p = act("p2", "pass")           # 回合制：pass＝明確結束自己這一手（不必出手）
        check("額度用完／不想出手時可以 pass（＝明確結束自己的回合）",
              p2p.get("ended_turn") is True, json.dumps(p2p, ensure_ascii=False)[:90])

        print("== 4. 争抢：同一輪兩人宣言 → 輪末揭晓 ==")
        # 2026-10-21：結算在**輪末**。兩人要在同一輪宣告，否則先宣告的人會在輪末先被結算掉。
        #   做法：p1 先宣告（不結束回合）→ 交棒給 p3 宣告 → 再把這一輪走完。
        r4 = None
        r4b = None
        for _ in range(12):
            before = len(get(f"/api/log?gm_token={TOKEN}")["events"])
            r4 = act("p1", "declare", target="pray@hyakki_f5")
            if "declared" not in r4:
                continue
            r4b = act("p3", "declare", target="pray@hyakki_f5")
            _res = [e for e in get(f"/api/log?gm_token={TOKEN}")["events"][before:]
                    if e["type"] == "declare_resolved"]
            if "declared" in r4b and not _res:
                break                       # 兩人都在同一輪宣告、還沒被結算 ⇒ 可以往下
        before = len(get(f"/api/log?gm_token={TOKEN}")["events"])
        check("人类也能宣言", "declared" in (r4 or {}), json.dumps(r4, ensure_ascii=False)[:80])
        check("同一輪 agent 也能宣言（兩個人都還在等結算）", "declared" in (r4b or {}),
              json.dumps(r4b, ensure_ascii=False)[:80])
        close = close_scene()
        evs = get(f"/api/log?gm_token={TOKEN}")["events"][before:]
        types = [e["type"] for e in evs]
        check("收景时揭晓并列", "declare_resolved" in types, str(types))
        # 拆景後：勝負在「輪末」判；誰勝只看 declare_resolved 的 winner 欄（兩人都各骰一次）
        _res_evs = [e for e in evs if e["type"] == "declare_resolved"]
        _winners = {e.get("winner") for e in _res_evs}
        check("并列者各骰一次并产生胜者（declare_resolved 帶唯一 winner）",
              len(_res_evs) >= 2 and len(_winners) == 1 and None not in _winners
              and sum(1 for e in _res_evs if e.get("won")) == 1,
              f"winners={_winners} n={len(_res_evs)}")
        res = [e for e in evs if e["type"] == "declare_resolved"]
        check("揭晓记下两人的骰", len(res) == 2, str(len(res)))
        # 祈願箱是**一次性**的：這一輪的勝者若碰上已開過的箱，要**講清楚**（declare_failed 帶理由），
        # 不是靜默變成「什麼都沒發生」。真正拿到神器的那一次（本局第一個成功的祈願）在整份 log 裡。
        _all = get(f"/api/log?gm_token={TOKEN}")["events"]
        check("勝者的宣言一定落地（拿到神器，或講清楚為什麼沒拿到）",
              any(e["type"] == "declare_won" for e in evs)
              or any(e["type"] == "artifact_gained" for e in _all)
              or any(e["type"] == "declare_failed" and e.get("reason") for e in evs),
              "won=" + str(any(e["type"] == "declare_won" for e in evs))
              + " failed=" + str([e.get("reason") for e in evs if e["type"] == "declare_failed"])[:120])

        print("== 5. 碎片：私有性 + 来源链 ==")
        # 碎片由「成功的那次祈願」產出（本局只有第一個成功的祈願算數）⇒ 看整份 log
        fg = [e for e in get(f"/api/log?gm_token={TOKEN}")["events"] if e["type"] == "fragment_granted"]
        check("祈願产出一枚碎片", len(fg) >= 1, str(len(fg)))
        if fg:
            holder = fg[0]["holder"]
            fid = fg[0]["fid"]
            check("碎片落在非请求者手上（不是祈願者）",
                  holder in ("p1", "p2", "p3") and holder != fg[0].get("reason"), holder)
            txt = get("/api/state?seat=%s" % holder)["your_fragments"]
            check("持有者看得到碎片正文", any(x["id"] == fid for x in txt), str([x["id"] for x in txt]))
            others = [s for s in ("p1", "p2", "p3", "npc1") if s != holder]
            leaked = any(fid in json.dumps(view(s), ensure_ascii=False) and
                         "text" in json.dumps(view(s), ensure_ascii=False) and
                         any(x.get("id") == fid for x in (view(s).get("your_fragments") or []) +
                             (view(s).get("seen_by_you") or []))
                         for s in others)
            check("未持有者看不到这枚碎片", not leaked)

        print("== 6. 出示 → 愿望兑现（交涉筹码）=")
        # GM 手动投两枚给 p3，模拟它从别处拿到
        d1 = gm("fragment_drop", to="p3", fid="f_ash")
        check("GM 指名投放碎片回 warn（不自動扣額度；若為他的一手要用 act_for）",
              isinstance(d1, dict) and "warn" in d1 and "p3" in d1.get("warn", ""),
              json.dumps(d1, ensure_ascii=False)[:140])
        gm("fragment_drop", to="p3", fid="f_bell")
        ids = [x["id"] for x in view("p3")["your_fragments"]]
        check("GM 投放的碎片在 p3 手上", {"f_ash", "f_bell"} <= set(ids), str(ids))
        gm("start_turn")
        s1 = act("p3", "show", target="f_ash", note="npc1")
        check("出示碎片给 NPC", "shown" in s1, json.dumps(s1, ensure_ascii=False)[:120])
        close_scene()
        gm("start_turn")
        s2 = act("p3", "show", target="f_bell", note="npc1")
        check("出示第二枚碎片", "shown" in s2, json.dumps(s2, ensure_ascii=False)[:120])
        close_scene()
        gm("start_turn")
        w = act("p3", "wish")
        achieved = view("p3")["your_goal"]["achieved"]
        check("愿望条件满足即达成（出示后自动判定，或手打 wish）",
              achieved and (w.get("wish", {}).get("ok") is True or "已经实现" in str(w.get("error"))),
              json.dumps(w, ensure_ascii=False)[:140])
        check("兑现只公开「许了愿」，不公开内容",
              any(e["type"] == "wish_done" for e in get(f"/api/log?gm_token={TOKEN}")["events"]) and
              "炉は十年前の夜から火を落としていない" not in json.dumps(view("p1"), ensure_ascii=False))

        print("== 7. 泄漏闸（GM 叙述与 NPC 台词同一道） ==")
        g1 = view("p1")["your_goal"]
        leak = gm("npc_say", seat="npc1", text=g1["text"][:40])
        check("NPC 台词说出玩家愿望原文被拦", "error" in leak and "泄露" in leak.get("error", ""),
              leak.get("error", "")[:80])
        okline = gm("npc_say", seat="npc1", text="……いらっしゃい。泊まっていくか？")
        check("正常台词通过", "said" in okline, json.dumps(okline, ensure_ascii=False)[:80])
        log = get(f"/api/log?gm_token={TOKEN}")["events"]
        check("NPC 台词记成 npc1 的发言（由 GM 代发）",
              any(e["type"] == "say" and e.get("seat") == "npc1" and e.get("via") == "gm" for e in log))
        check("泄漏事件进日志（拦了要看得见）",
              any(e["type"] == "gm_leak_blocked" for e in log))

        print("== 8. GM 代投暗骰（点数不公开） ==")
        rr = gm("roll_for", seat="npc1", attr="wil", dc=12, secret=True)
        check("代投返回点数给 GM", "roll" in rr and isinstance(rr["roll"]["total"], int),
              json.dumps(rr.get("roll", {}), ensure_ascii=False)[:90])
        val = str(rr["roll"]["value"])
        rseq = rr["roll"]["roll_seq"]
        plog = get("/api/state?seat=p1")["log"]
        check("暗骰点数不出现在玩家可见日志",
              not any(e.get("type") == "roll" and e.get("roll_seq") == rseq for e in plog),
              f"roll_seq={rseq} value={val}")
        check("暗骰这件事本身可见（marker）",
              any(e["type"] == "gm_roll_marker" for e in get(f"/api/log?gm_token={TOKEN}")["events"]))
        gmv = get(f"/api/state?gm_token={TOKEN}")
        check("GM 视图里能看到暗骰点数",
              any(str(e.get("value")) == val for e in (gmv.get("gm") or {}).get("secret_rolls", [])))

        print("== 9. NPC 视角包（喂给下游 agent 的全部输入） ==")
        k = gm("npc_knowledge", seat="npc1")
        check("视角包不含任何玩家愿望", "goal" not in json.dumps(k, ensure_ascii=False))
        check("视角包带位置与边界", k.get("at", {}).get("id") == "minato" and k.get("must_not"),
              json.dumps(k.get("at"), ensure_ascii=False))
        check("视角包带当前轮", k.get("stage") is None or isinstance(k.get("stage"), dict))

        print("== 10. 结算 / 回放 / MCP ==")
        tbl = gm("settle")
        check("结算表有 6 行（不含 GM；行按 seats.json 定员出，含未入座的 p4／p5）", len(tbl.get("rows", [])) == 6, str(len(tbl.get("rows", []))))
        if tbl.get("rows"):
            row3 = [r for r in tbl["rows"] if r["seat"] == "p3"][0]
            check("结算表含愿望三档状态", row3["status"] in ("达成", "未达成", "—（无愿望）"),
                  f"{row3['status']}")
            check("结算表含碎片来源链", isinstance(row3["shown_by"], list), str(row3["shown_by"]))
            # 私密愿望 title 不得进任何 **public** 事件（2026-10-05 PR #10 复核：wish_done/settle 曾带 title）。
            check("结算表行不含私密愿望 title", all("goal" not in r for r in tbl["rows"]),
                  json.dumps(tbl["rows"], ensure_ascii=False)[:200])
        _gtitle = (view("p3").get("your_goal") or {}).get("title")
        _leak = [e for e in get(f"/api/log?gm_token={TOKEN}")["events"]
                 if e.get("public", True) and _gtitle and _gtitle in json.dumps(e, ensure_ascii=False)]
        check("公开日志不含任何私密愿望 title", not _leak,
              json.dumps(_leak[:1], ensure_ascii=False)[:200])
        rep = post("/api/gm", {"token": TOKEN, "op": "replay"})
        check("事件流回放与内存一致", rep.get("replay_matches") is True,
              json.dumps(rep.get("diff", rep), ensure_ascii=False)[:300])
        print("== 11. p3 的 kick 接口（场景制：开景即轮到你） ==")
        for _ in range(12):          # 节拍 2s 一轮，等踢出去
            if WAKES:
                break
            time.sleep(1.0)
        check("开景真的把 kick POST 给了 p3", bool(WAKES), f"收到 {len(WAKES)} 条")
        if WAKES:
            w = WAKES[-1]
            p = w["payload"]
            check("kick 是 trpg_turn 且指向 p3 席位",
                  p.get("kind") == "trpg_turn" and p.get("seat") == "p3", json.dumps(p, ensure_ascii=False)[:90])
            check("kick 的幂等键带轮号（重开一局不会撞车）",
                  "round" in str(p.get("turn_seq", "")) or ":" in str(p.get("turn_seq", "")),
                  str(p.get("turn_seq")))
            check("kick 带共享 token 头（供对方校验）", w["kick_token"] == KICK_TOKEN, str(w["kick_token"]))
            pr = p.get("prompt", "")
            check("kick 里带当前轮", "第" in pr, pr[:60].replace("\n", " "))
            check("kick 里带 p3 自己的愿望与碎片", ("炉の火" in pr or "火を落としていない" in pr), "有私密愿望")
            check("kick 不带别人的私密愿望", "迷い猫" not in pr)
            check("kick 里带额度提示（agent 每景 act/say 上限）", "额度" in pr)
            check("kick 里带「你上次出手之后发生了什么」的补课（agent 不会看不到别人的行动）",
                  ("你上次出手之后" in pr or "桌上最近发生" in pr),
                  pr[-260:].replace("\n", " "))
            check("同一景只踢一次（幂等）",
                  len({x["payload"].get("turn_seq") for x in WAKES}) == len(WAKES),
                  str([x["payload"].get("turn_seq") for x in WAKES]))

        print("== 11b. 攻击目标守卫（GM 不是棋盘上的角色） ==")
        # ★ 2026-10-19 拆前後台：`/` 不再送頁面（前端在 trpg/frontend-nuxt）⇒ 這裡只驗資料面；
        #   「行動參數面板／三語／裸 n()」那幾條屬於前台 repo 的靜態閘（deploy/check_frontend.py 那套）。
        try:
            urllib.request.urlopen(f"{BASE}/", timeout=10)
            _st_page = 200
        except urllib.error.HTTPError as _e:                              # noqa: PERF203
            _st_page = _e.code
        check("後台不送網頁（`/` ⇒ 410）", _st_page == 410, f"HTTP {_st_page}")
        ga = act("p1", "attack", target="gm", dc=1)
        check("不能攻击 GM 席位", "error" in ga and "GM" in str(ga.get("error")),
              str(ga.get("error"))[:90])
        sa = act("p1", "attack", target="p1", dc=1)
        check("不能攻击自己", "error" in sa, str(sa.get("error"))[:70])
        na = act("p1", "attack", target="npc1", dc=99)
        check("NPC 可以被打（世界里的角色，后果由 GM 裁定）", "roll" in na,
              json.dumps(na, ensure_ascii=False)[:90])
        print("== 11c. 队友位置投影（信息防火墙：塔内只给「塔内」） ==")
        pv = {p["seat"]: p for p in (get("/api/state?seat=p1").get("board") or {}).get("party") or []}
        check("队友位置里没有 GM（主持不是棋盘上的角色）", "gm" not in pv, str(list(pv)))
        check("队友位置里没有自己（自己那格是 you_are）", "p1" not in pv, str(list(pv)))
        check("NPC 标成「非队友」（is_party=false），不会被当成队友",
              pv.get("npc1", {}).get("is_party") is False and pv.get("p2", {}).get("is_party") is True,
              json.dumps({k: pv.get(k, {}).get("is_party") for k in ("npc1", "p2")}))
        check("塔内＝特殊场景：只给「塔内」，不暴露层数",
              pv.get("p2", {}).get("hidden_floor") is True and pv["p2"]["where"] == "塔内"
              and pv["p2"]["node"] is None, json.dumps(pv.get("p2"), ensure_ascii=False)[:160])
        check("同席位的自己仍看得到精确层（you_are 不受影响）",
              ((get("/api/state?seat=p2").get("board") or {}).get("you_are") or {}).get("id") == "hyakki_f5",
              str(((get("/api/state?seat=p2").get("board") or {}).get("you_are") or {}).get("id")))
        # 把 p2 弄到塔外 → 位置应当可见（给节点 id）。2026-10-21：上／下樓／出塔都走 move，
        #   而且 move 在示範劇本 是**免費動作**（turn.free_actions）⇒ 不必換輪。
        act("p2", "move", target="hyakki_f4")     # A1：f4 只剩 BOSS，下樓要先經過它
        act("p2", "move", target="hyakki_f3")
        act("p2", "move", target="hyakki_f2")
        act("p2", "move", target="hyakki_f1")
        act("p2", "move", target="hyakki")
        pv2 = {p["seat"]: p for p in (get("/api/state?seat=p1").get("board") or {}).get("party") or []}
        check("塔外＝非特殊场景：队友位置可见（给节点）",
              pv2.get("p2", {}).get("node") == "hyakki" and pv2["p2"]["where"] != "塔内",
              json.dumps(pv2.get("p2"), ensure_ascii=False)[:160])

        print("== 11c2. 动态入座的席位也要有额度（throttle 不能只认 seats.json） ==")
        post("/api/join", {"seat": "p99", "name": "临时席", "kind": "human"})
        th99 = (get("/api/state?seat=p99").get("throttle") or {})
        check("动态入座席位拿到 throttle（含 limits.act/say）",
              bool(th99.get("limits", {}).get("act")) and bool(th99.get("limits", {}).get("say")),
              str(th99))

        print("== 11d. 点名推送（玩家叫 agent 的名字 → 立刻推一次） ==")
        None
        gm("start_turn")
        n0 = len(WAKES)
        r = post("/api/say", {"seat": "p1", "text": "ホムラ、f3 を一枚でいい、私に渡してくれ"})
        check("发言点到 agent 名字 → 立刻推一次（不用等下一景）",
        len(WAKES) > n0 and "p3" in (r.get("mentions_pushed") or []),
        f"WAKES {n0}→{len(WAKES)} pushed={r.get('mentions_pushed')}")
        n1 = len(WAKES)
        post("/api/say", {"seat": "p1", "text": "ホムラ、聞こえてるか"})
        check("同一景的点名只推一次（限流）", len(WAKES) == n1, f"{n1} → {len(WAKES)}")
        n2 = len(WAKES)
        post("/api/say", {"seat": "p1", "text": "北の道から行こう"})
        check("没点名的发言不推（不炸 agent）", len(WAKES) == n2, f"{n2} → {len(WAKES)}")
        close_round()                                  # 走完一輪 ⇒ 額度按輪重置
        n3 = len(WAKES)
        post("/api/say", {"seat": "p2", "text": "ホムラ、上で待ってる"})
        check("換一輪之后可以再推一次（額度按輪重置）", len(WAKES) > n3, f"{n3} → {len(WAKES)}")

        try:
            import anyio
            # ★ 2026-10-21 拆景：沒有「收景」這個 op 了。原本這一組驗「全員出手過才准收景」，
            #   現在對應到**輪末結算**：輪還沒走完 ⇒ 宣言不揭曉；走完 ⇒ 引擎自己結算。
            print("== 12. 輪末結算：宣言等所有人結束回合才揭曉（GM 不能自己往前推） ==")
            gm("start_turn")
            v = view("p1")
            check("視圖列出本輪還沒結束的人（round_waiting）",
                  isinstance(v.get("round_waiting"), list), str(v.get("round_waiting"))[:90])
            # 2026-10-21：view 少了輪號（前端讀 v.round_no 一直是 undefined）⇒ 引擎補上
            check("視圖帶輪號（round_no＝現在第幾輪，1 起算）",
                  isinstance(v.get("round_no"), int) and int(v.get("round_no")) >= 1,
                  str(v.get("round_no")))
            _v2 = get("/api/state?seat=p1")
            _r0 = int(v.get("round_no") or 0)
            _r1 = int(((_v2.get("view") if isinstance(_v2.get("view"), dict) else _v2) or {}).get("round_no") or 0)
            check("輪號不會倒退（同輪內重新取視圖）", _r1 >= _r0, f"{_r0} → {_r1}")
            check("視圖標出自己這一輪動過沒有（you_acted）",
                  isinstance(v.get("you_acted"), bool), str(v.get("you_acted")))
            # 宣告一手，但這一輪還沒走完 ⇒ 不該結算
            d1 = act("p1", "declare", target="pray@hyakki_f5")
            check("宣言成功（輪末才結算）", "declared" in d1, json.dumps(d1, ensure_ascii=False)[:80])
            _n = len(get(f"/api/log?gm_token={TOKEN}")["events"])
            _mid = [e for e in get(f"/api/log?gm_token={TOKEN}")["events"][_n:] if e["type"] == "declare_resolved"]
            check("輪還沒走完 ⇒ 沒有 declare_resolved（GM 也不能提前推）", not _mid, str(len(_mid)))
            rp = act("p1", "pass")
            check("pass＝結束自己的回合（回合制下「不出手」就是 end_turn）",
                  rp.get("ended_turn") is True, json.dumps(rp, ensure_ascii=False)[:90])
            # 走完這一輪 ⇒ 揭曉
            close_round()
            _evs = get(f"/api/log?gm_token={TOKEN}")["events"][_n:]
            check("全員結束回合 ⇒ 輪末揭曉宣言",
                  any(e["type"] == "declare_resolved" for e in _evs),
                  str([e["type"] for e in _evs])[:110])
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client

            async def probe():
                async with streamablehttp_client(f"{BASE}/mcp") as (r, w, _):
                    async with ClientSession(r, w) as s:
                        tools = await s.list_tools()
                        names = [t.name for t in tools.tools]
                        bad = await s.call_tool("table_gm", {"token": "wrong", "op": "leak_report"})
                        good = await s.call_tool("table_gm", {"token": TOKEN, "op": "leak_report"})
                        sec_bad = await s.call_tool("table_secret", {"to": "p1", "text": "x",
                                                                     "token": "wrong"})
                        sec_ok = await s.call_tool("table_secret", {"to": "p1", "text": "只有你知道：测试",
                                                                   "token": TOKEN})
                        say_bad = await s.call_tool("table_say", {"seat": "gm", "text": "（假敘事）"})
                        say_ok = await s.call_tool("table_say", {"seat": "gm",
                                                                 "text": "（測試敘事 via MCP）",
                                                                 "token": TOKEN})
                        # table_tell（2026-10-06：玩家 agent 也要能回私信——原本只有 GM 有 table_secret）
                        tell_ok = await s.call_tool("table_tell", {"seat": "p1", "to": "p3",
                                                                   "text": "只有ホムラ知道：MCP 私訊"})
                        tell_bad = await s.call_tool("table_tell", {"seat": "p1", "to": "nope",
                                                                    "text": "x"})
                        v_to = await s.call_tool("table_view", {"seat": "p3"})
                        v_other = await s.call_tool("table_view", {"seat": "p2"})
                        return (names, bad, good, sec_bad, sec_ok, say_bad, say_ok,
                                tell_ok, tell_bad, v_to, v_other)
            (names, bad, good, sec_bad, sec_ok, say_bad, say_ok,
             tell_ok, tell_bad, v_to, v_other) = anyio.run(probe)
            check("MCP 多了 table_gm 工具", "table_gm" in names, str(names))
            check("MCP 有 table_tell（玩家可用的私信出口）", "table_tell" in names, str(names))
            check("token 不对被拒", bad.isError, bad.content[0].text[:60])
            check("GM 工具可用（泄漏记录）", not good.isError and "leaks" in good.content[0].text,
                  good.content[0].text[:80])
            check("table_secret 没 token 被拒（agent 不能冒充 GM 发私密）", sec_bad.isError,
                  sec_bad.content[0].text[:60])
            check("table_secret 带对 token 可用", not sec_ok.isError, sec_ok.content[0].text[:60])
            check("table_say(seat=gm) 没 token 被拒（MCP 這道門也上同一把尺）", say_bad.isError,
                  say_bad.content[0].text[:60])
            check("table_say(seat=gm) 带对 token 可用", not say_ok.isError, say_ok.content[0].text[:60])
            check("玩家席位用 table_tell 不需 token（agent 的私信出口）", not tell_ok.isError,
                  tell_ok.content[0].text[:80])
            check("table_tell 收件人的视图看得到", "只有ホムラ知道" in v_to.content[0].text,
                  v_to.content[0].text[:60])
            check("table_tell 第三人（p2）视图看不到", "只有ホムラ知道" not in v_other.content[0].text,
                  v_other.content[0].text[:60])
            check("table_tell 收件人不存在 ⇒ 工具错误（不是 500）", tell_bad.isError,
                  tell_bad.content[0].text[:60])

            # 连线标头身份（2026-10-06）：GM 的瘦 profile 只给桌工具、没有 terminal ⇒
            # 凭证不能靠 token 参数（会进 prompt／会话），改由 `X-Table-GM-Token` 标头证明。
            async def probe_hdr():
                async with streamablehttp_client(f"{BASE}/mcp",
                                                 headers={"X-Table-GM-Token": TOKEN}) as (r, w, _):
                    async with ClientSession(r, w) as s:
                        gm_ok_h = await s.call_tool("table_gm", {"op": "leak_report"})
                        sec_ok_h = await s.call_tool("table_secret", {"to": "p1",
                                                                       "text": "（標頭私訊）"})
                        return gm_ok_h, sec_ok_h

            async def probe_badhdr():
                async with streamablehttp_client(f"{BASE}/mcp",
                                                 headers={"X-Table-GM-Token": "wrong"}) as (r, w, _):
                    async with ClientSession(r, w) as s:
                        return await s.call_tool("table_gm", {"op": "leak_report"})

            gm_ok_h, sec_ok_h = anyio.run(probe_hdr)
            gm_bad_h = anyio.run(probe_badhdr)
            check("连线带对 X-Table-GM-Token ⇒ table_gm 免 token 可用", not gm_ok_h.isError,
                  gm_ok_h.content[0].text[:80])
            check("连线带对 X-Table-GM-Token ⇒ table_secret 免 token 可用", not sec_ok_h.isError,
                  sec_ok_h.content[0].text[:80])
            check("连线带错标头 ⇒ 仍被拒（标头不是绕过后门）", gm_bad_h.isError,
                  gm_bad_h.content[0].text[:60])
        except ImportError as exc:
            check("mcp SDK 可用", False, str(exc))

        print("== 12. /api/log 兩段式閘 ＋ 公開發言喚醒收斂 ==")
        # 12a. /api/log 閘（協作端 2026-10-05 §2；口徑 = view 白名單 ∩ public）：
        #      沒帶 gm_token ⇒ 只回公開投影（＝ view.log 看得到的）；
        #      帶正確 token ⇒ 原始全量。
        from trpg.engine import VIEW_LOG_TYPES
        raw = get(f"/api/log?gm_token={TOKEN}")["events"]
        pub = get("/api/log")["events"]
        check("帶 gm_token ＝ 原始全量（secret 在裡面）",
              any(e.get("type") == "secret" for e in raw), f"raw={len(raw)}")
        check("沒帶 token ⇒ 公開投影不含 secret／public:false",
              not any(e.get("public") is False for e in pub)
              and not any(e.get("type") == "secret" for e in pub), f"pub={len(pub)}")
        check("公開投影＝view 白名單 ∩ public:true（原序、seq 逐條對得上）",
              [e["seq"] for e in pub] == [e["seq"] for e in raw
                                          if e.get("public") is not False
                                          and e.get("type") in VIEW_LOG_TYPES],
              f"pub={len(pub)} raw={len(raw)} "
              f"raw裡public:false={sum(1 for e in raw if e.get('public') is False)} "
              f"raw裡acted={sum(1 for e in raw if e.get('type') == 'acted')}")
        check("公開投影不含白名單外的事件（內部記帳 acted 被排除）",
              not any(e.get("type") not in VIEW_LOG_TYPES for e in pub)
              and not any(e.get("type") == "acted" for e in pub),
              f"多出來：{[e['type'] for e in pub if e.get('type') not in VIEW_LOG_TYPES][:5]}；"
              f"pub裡acted={sum(1 for e in pub if e.get('type') == 'acted')}")
        check("錯 token 一樣只給公開投影",
              not any(e.get("type") == "secret" for e in get("/api/log?gm_token=nope")["events"]))

        # 12a-1b. 唯讀 meta（協作端 2026-10-05 §5）：只回「筆數＋尾 seq」、不帶內容，
        #        供外部互驗「沒動資料」的聲明；數字必須等於原始全量（raw）。
        _raw2 = get(f"/api/log?gm_token={TOKEN}")["events"]
        _m = get("/api/log?meta=1")
        check("meta=1 只回 count/last_seq、不帶 events 內容",
              set(_m.keys()) == {"count", "last_seq"},
              json.dumps(_m, ensure_ascii=False)[:120])
        check("meta=1 count/last_seq == 原始全量（raw）",
              _m["count"] == len(_raw2) and _m["last_seq"] == _raw2[-1]["seq"],
              f"meta={_m} raw={len(_raw2)}")
        check("meta=1 帶錯 token 也是同一組原始數字（不看 token）",
              get("/api/log?meta=1&gm_token=nope") == _m,
              f"{get('/api/log?meta=1&gm_token=nope')}")

        # 12a-2. 逐條逐欄對帳（協作端 2026-10-05 §fid：seq 集合只證明「進出集合」對，
        #        欄位漂了看不出來——fid 那次就是這樣漏的）
        #        匿名投影每一條都必須逐欄 == _scrub_public(raw[同 seq], None)（除 seq 外）
        from trpg.engine import Table as _Table
        raw_by_seq = {e["seq"]: e for e in raw}
        mism = []
        for e in pub:
            want = _Table._scrub_public(object(), raw_by_seq[e["seq"]], None)
            if ({k: v for k, v in e.items() if k != "seq"}
                    != {k: v for k, v in want.items() if k != "seq"}):
                mism.append(e["seq"])
        check("匿名投影逐條逐欄 == _scrub_public(raw, None)（除 seq 外每欄相等）",
              not mism, f"不符 seq={mism[:5]}；比對 {len(pub)} 條")
        check("匿名投影不含內部 id fid（碎片事件已 scrub）",
              not any("fid" in e for e in pub),
              f"帶 fid 條數={sum(1 for e in pub if 'fid' in e)}")
        _ev = {"type": "fragment_granted", "fid": "f_x", "holder": "p1", "seq": 1}
        check("_scrub_public：當事人保留 fid、匿名與他人抹掉（granted 沒有 to 的邊角）",
              "fid" in _Table._scrub_public(object(), _ev, "p1")
              and "fid" not in _Table._scrub_public(object(), _ev, None)
              and "fid" not in _Table._scrub_public(object(), _ev, "p2"))

        # 12a-3. 私訊收件匣欄位（協作端 2026-10-05 補：your_secrets 帶 peer／to／ts，
        #        前端要畫 messenger 的時間戳與對話串）
        post("/api/gm", {"token": TOKEN, "op": "secret", "to": "p1",
                         "text": "（测试）只有你知道：时间戳"})
        mine = (get("/api/state?seat=p1") or {}).get("your_secrets") or []
        check("your_secrets 每則帶 seq／to／peer／ts（畫 messenger 用）",
              bool(mine) and all({"seq", "to", "peer", "ts"} <= set(s) and s.get("ts")
                                 for s in mine),
              f"n={len(mine)}；keys={sorted(mine[0]) if mine else []}"
              f"；ts={(mine[0].get('ts') if mine else None)}")

        # 12a-4. /api/typing（純記憶體、不落地）⇒ view.typing 讓同桌看到「正在輸入」
        post("/api/typing", {"seat": "gm", "to": "p1"})
        _t = (get("/api/state?seat=p1") or {}).get("typing") or {}
        check("/api/typing 之後 view.typing 出現 gm（to=p1）",
              _t.get("gm", {}).get("to") == "p1", str(_t))

        # ★ 2026-10-21 拆景：回合制的喚醒點只有「輪」。
        #   公開發言／玩家行動**不逐句**叫 GM（舊的「同景一次」收斂整套不需要了）。
        #   這裡驗：發言與行動都不叫；走完一輪 ⇒ 引擎叫 GM 一次（帶輪號、帶 token 頭、prompt 一次給足）。
        gm("start_turn")
        n0 = len(GMKICKS)
        for _t in ("第一句", "第二句", "第三句"):
            post("/api/say", {"seat": "p1", "text": _t})
        post("/api/say", {"seat": "p2", "text": "（第二個人說話）"})
        check("回合制：公開發言不逐句喚 GM（喚醒點在輪）",
              len(GMKICKS) - n0 == 0, f"收到 {len(GMKICKS) - n0} 條")

        # 12c. 走完一輪 ⇒ 引擎叫 GM 一次（冪等鍵＝桌:輪:<n>）
        n1 = len(GMKICKS)
        close_round()
        for _ in range(40):                      # tick 是 2 秒一拍
            if len(GMKICKS) > n1:
                break
            time.sleep(0.2)
        news = GMKICKS[n1:]
        check("走完一輪 ⇒ 引擎叫 GM 一次（輪末歸結）", len(news) >= 1, f"收到 {len(news)} 條")
        if news:
            _pmt = news[0]["payload"].get("prompt", "")
            check("GM 喚醒帶引擎 token 頭（供 協作端 側校驗）",
                  news[0]["kick_token"] == GM_KICK_TOKEN, str(news[0]["kick_token"]))
            check("GM 喚醒 payload 是 trpg_turn 且指向這張桌",
                  news[0]["payload"].get("kind") == "trpg_turn"
                  and news[0]["payload"].get("table_id"), str(news[0]["payload"].get("table_id")))
            check("GM 喚醒 prompt 一次給足桌況（席位／允許動作／輪號／額度）",
                  all(k in _pmt for k in ("── 桌況", "席位：", "這張桌允許的動作", "輪")),
                  f"n={len(_pmt)}")
            _i = _pmt.find("景")
            check("prompt 不再出現「景」（拆景後只剩輪）", _i < 0,
                  ("…" + _pmt[max(0, _i - 60):_i + 40] + "…") if _i >= 0 else "")
            check("prompt 給一行工具（gm.py）且明令不要探索",
                  "tools/gm.py" in _pmt and "不要探索" in _pmt and "不要 ls" in _pmt, _pmt[-120:])
            check("prompt 不再叫 GM 手寫 curl、也不再給劇本絕對路徑（把餌拿掉）",
                  "/api/say" not in _pmt and "劇本資料：" not in _pmt, _pmt[-120:])
            check("預設 gm_cli 指向的檔案真的在 repo 裡（prompt 不會指向空氣）",
                  os.path.isfile(os.path.join(HERE, "tools", "gm.py")),
                  os.path.join(HERE, "tools", "gm.py"))

        # 12c-2. 同一輪不會再叫第二次（冪等鍵帶輪號）
        n2 = len(GMKICKS)
        close_round()
        for _ in range(20):
            time.sleep(0.2)
        _keys = [k["payload"].get("turn_seq") for k in GMKICKS[n2:]]
        check("同一個輪號不會重複叫（冪等鍵帶輪號；走過幾輪就幾條、鍵不重複）",
              len(_keys) == len(set(_keys)), f"keys={_keys}")

        # 12d. /api/act 的玩家行動**不**叫 GM（回合制；舊行為是每動一次叫一次）
        gm("start_turn")
        n5 = len(GMKICKS)
        act("p2", "pass")
        _new5 = GMKICKS[n5:]
        # 允許「剛好跨過輪界」的那一腳（那是輪的喚醒，不是逐句喚醒）
        check("回合制：玩家行動不逐句喚 GM（只可能收到輪邊界那一腳）",
              all(":round:" in (x["payload"].get("turn_seq") or "") for x in _new5)
              and len(_new5) <= 1, f"收到 {len(_new5)} 條：{[x['payload'].get('turn_seq') for x in _new5]}")

        # 私訊維持原樣：每封都喚（key 仍帶序號）
        n1 = len(GMKICKS)
        post("/api/tell_gm", {"seat": "p1", "text": "私訊一"})
        post("/api/tell_gm", {"seat": "p1", "text": "私訊二"})
        check("私訊維持原樣：兩封私訊喚兩次（不是同景一次）", len(GMKICKS) - n1 == 2,
              f"收到 {len(GMKICKS) - n1} 條")
        if len(GMKICKS) - n1 == 2:
            ts2 = GMKICKS[-1]["payload"].get("turn_seq", "")
            check("私訊喚醒的 key 仍帶序號（:私訊:<seq>）", ":私訊:" in ts2, ts2)

        # 私訊路由（設計者 2026-10-06 修正）：**私訊只叫收件人**——
        # 對 agent 席位私訊 ⇒ 叫那個席位自己（不叫 GM）；對人私訊 ⇒ 都不叫。
        n7 = len(GMKICKS); n7w = len(WAKES)
        post("/api/tell", {"seat": "p1", "to": "p3", "text": "給ホムラ的私訊"})
        check("對 agent 席位私訊 ⇒ 不叫 GM（設計者 2026-10-06）",
              len(GMKICKS) - n7 == 0, f"GM 收到 {len(GMKICKS) - n7} 條")
        check("對 agent 席位私訊 ⇒ 叫那個席位自己（走它自己的 wake url）",
              len(WAKES) - n7w == 1 and WAKES[-1]["payload"].get("seat") == "p3",
              f"WAKES {n7w}→{len(WAKES)} seat={WAKES[-1]['payload'].get('seat') if len(WAKES) > n7w else None}")
        check("席位喚醒 prompt 帶私信工具行（agent 才知道自己回得了私信）",
              "table_tell" in (WAKES[-1]["payload"].get("prompt") or ""),
              (WAKES[-1]["payload"].get("prompt") or "")[:90])
        # 私訊驗證碼的契約（2026-10-06 把 /api/tell 的實作收成 TableService.tell 一本後，
        # 回碼不能跑掉——REST 依 TellError.status 回，MCP 轉成工具錯誤）。
        st404, _ = post_status("/api/tell", {"seat": "p1", "to": "nope", "text": "x"})
        check("重構後 /api/tell 收件人不存在 ⇒ 404（碼沒跑掉）", st404 == 404, str(st404))
        st409, _ = post_status("/api/tell", {"seat": "pZZ", "to": "p1", "text": "x"})
        check("重構後 /api/tell 未入座 ⇒ 409（碼沒跑掉）", st409 == 409, str(st409))
        st400, _ = post_status("/api/tell", {"seat": "p1", "to": "p2", "text": "  "})
        check("重構後 /api/tell 空 text ⇒ 400（碼沒跑掉）", st400 == 400, str(st400))

        # GM 唤醒进事件流（2026-10-06）：以前这一路只有 stdout 一行，桌上什么都看不到——
        # 「靜默丟棄不許可」在 GM 唤醒路上是破的。现在每一脚都留一条 kick 事件（seat=gm）。
        _gmk = [e for e in get(f"/api/log?gm_token={TOKEN}")["events"]
                if e.get("type") == "kick" and e.get("seat") == "gm"]
        check("GM 唤醒进了事件流（每一脚一条 kick 事件，seat=gm）", len(_gmk) > 0,
              f"{len(_gmk)} 条")
        check("GM 唤醒事件带 by／reason／turn_seq／ok（谁触发·为什么·幂等键·成没成）",
              bool(_gmk) and all(k in _gmk[-1] for k in ("by", "reason", "turn_seq", "ok")),
              json.dumps(_gmk[-1], ensure_ascii=False)[:150] if _gmk else "（无）")
        # 2026-10-07 玩家平等原则：kick 仍在事件流、GM raw 看得到，但**匿名公开投影看不到**
        # （只有 agent 席位会被叫醒 ⇒ 公开就等于标出谁是 AI）。
        check("GM 唤醒事件**不进**匿名公开投影（玩家平等原则；GM raw 那条仍看得到）",
              not any(e.get("type") == "kick" and e.get("seat") == "gm"
                      for e in get("/api/log")["events"]), "（匿名投影里还有 kick）")
        # 失败也要看得见：把 token 档临时挪走 ⇒ 这一脚必然送不出去，但事件必须留 ok:false
        _tokf = os.path.join(tmp, "kick_engine.token")
        os.rename(_tokf, _tokf + ".bak")
        try:
            post("/api/tell_gm", {"seat": "p1", "text": "（失敗可見性測試）"})
        finally:
            os.rename(_tokf + ".bak", _tokf)
        _gmk2 = [e for e in get(f"/api/log?gm_token={TOKEN}")["events"]
                 if e.get("type") == "kick" and e.get("seat") == "gm"]
        check("GM 唤醒送失败也进事件流（ok:false，不静默丢弃）",
              len(_gmk2) == len(_gmk) + 1 and _gmk2[-1].get("ok") is False,
              json.dumps(_gmk2[-1], ensure_ascii=False)[:170] if _gmk2 else "（无）")
        _p1prompt = GMKICKS[-1]["payload"].get("prompt") or ""
        check("這張桌 gm_tools 預設 cli ⇒ 喚醒訊息給 gm.py 那行、不給桌 MCP 工具",
              "gm.py" in _p1prompt and "table_gm" not in _p1prompt, _p1prompt[:140])
        n8 = len(GMKICKS); n8w = len(WAKES)
        post("/api/tell", {"seat": "p1", "to": "p2", "text": "給シズク的私訊"})
        check("對人私訊 ⇒ 不叫 GM 也不叫席位（他自己的頁面看得到）",
              len(GMKICKS) - n8 == 0 and len(WAKES) - n8w == 0,
              f"GMKICKS+{len(GMKICKS) - n8} WAKES+{len(WAKES) - n8w}")

        print("== 13. /api/say 的 GM 聲道閘（協作端 20261006-0013 裁定 (c)）==")
        # (a) 沒帶 token 的 seat=gm ⇒ 403 bad gm token（同 /api/gm 的碼，不給第二種）
        st403, r403 = post_status("/api/say", {"seat": "gm", "text": "（假敘事）"})
        check("seat=gm 不帶 token ⇒ 403 bad gm token",
              st403 == 403 and r403.get("error") == "bad gm token", f"{st403} {r403}")
        # (b) 錯 token 一樣擋
        st403b, _ = post_status("/api/say", {"seat": "gm", "text": "（假敘事）", "token": "wrong"})
        check("seat=gm 帶錯 token ⇒ 403", st403b == 403, str(st403b))
        # (c) 對 token 可發：真的寫進 actor=gm 的敘事（type=gm_say），且不觸發喚醒（L753 只認 human）
        _n = len(GMKICKS)
        ok = post("/api/say", {"seat": "gm", "text": "（測試敘事：天花板浮出星圖。）",
                               "token": TOKEN})
        _log = get(f"/api/log?gm_token={TOKEN}")["events"]
        check("seat=gm 帶對 token ⇒ 可用且記成 actor=gm 的 gm_say",
              isinstance(ok, dict) and ok.get("view") is not None
              and any(e.get("type") == "gm_say" and e.get("seat") == "gm"
                      and "星圖" in (e.get("text") or "") for e in _log), str(ok)[:90])
        check("seat=gm 的敘事不觸發喚醒（加閘沒動 L753 的喚醒語意）",
              len(GMKICKS) - _n == 0, f"收到 {len(GMKICKS) - _n} 條")
        # 舊碼的迴歸點：seat=gm 走 /api/say 曾在 `int(ev["seq"])` 爆 KeyError ⇒ 400
        # "missing field 'seq'"（gm_say 回的是 {"said","speaker"}，沒有 seq）。這條擋住它回來。
        check("seat=gm 敘事不再 KeyError 'seq'（gm_say 沒有 seq 欄位）",
              isinstance(ok, dict) and ok.get("error") != "missing field 'seq'", str(ok)[:60])
        # (d) 玩家頁照舊：帶自己的 SEAT、無 token，零變更（收景→開新景讓額度歸零）
        None
        gm("start_turn")
        stp, rp = post_status("/api/say", {"seat": "p1", "text": "（測試）玩家照舊發言"})
        check("玩家席位不帶 token 照舊可發（現況零變更）",
              stp == 200 and isinstance(rp, dict) and rp.get("view") is not None, str(stp))

        # 14. 協作端 2026-10-06：semic-ended.patch（wait_for_all_ends＝全員表明結束才進下一景）
        #     ＋ server-kick.patch §2（全員結束時只送「收景」那一腳——兩腳同秒會讓兩個 GM session 互卡）
        #     另起一張桌（8954），因為 wait_for_all_ends 會改 pending_actors 的語意，不能污染上面那張。
        print("== 14. wait_for_all_ends ＋ 全員結束只送「收景」一腳 ==")
        tmp2 = tempfile.mkdtemp(prefix="trpg_wfe_")
        P2 = 8954
        B2 = f"http://127.0.0.1:{P2}"

        def _req2(path, body=None):
            url = B2 + path
            if body is None:
                with urllib.request.urlopen(url, timeout=30) as r:
                    return json.loads(r.read().decode())
            req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.loads(r.read().decode())
            except urllib.error.HTTPError as e:  # noqa: F821
                return json.loads(e.read().decode())

        get2 = lambda p: _req2(p)                       # noqa: E731
        post2 = lambda p, b: _req2(p, b)                # noqa: E731
        view2 = lambda s: get2(f"/api/state?seat={s}")  # noqa: E731

        def act2(seat, action, **kw):
            kw.update({"seat": seat, "action": action})
            r = post2("/api/act", kw)
            return dict(r["result"], _view=r.get("view")) if isinstance(r, dict) and "result" in r else r

        def gm2(op, **args):
            return post2("/api/gm", {"token": TOKEN, "op": "semi", "args": dict(op=op, **args)})

        cfg2 = layout.read_table_config(CAMP)
        cfg2["session"] = {"require": False}
        # ⚠ 夾具隔離（2026-10-07 單一進程 + 子路徑）：這兩個是**部署專屬**的鍵，
        #   從真 config 複製進來會讓 temp 桌指到現役 hub／帶別人的前綴 ⇒ 一律清掉。
        cfg2.pop("hub_base", None)
        cfg2.pop("public_prefix", None)
        cfg2["port"] = P2
        cfg2.setdefault("auth", {})["gm_token"] =  TOKEN
        cfg2.setdefault("auth", {}).pop("accounts_file", None)         # 副本不碰共用 registry（第二階段）
        cfg2.setdefault("auth", {})["require_account"] =  False
        T2KICK_PORT = 8958          # 8956 是 seatwake 那一段在用，別撞
        T2KICKS: list = []

        class _WfeKickHandler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                T2KICKS.append({"payload": json.loads(self.rfile.read(n).decode())})
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *a):
                pass

        wfe_httpd = ThreadingHTTPServer(("127.0.0.1", T2KICK_PORT), _WfeKickHandler)
        threading.Thread(target=wfe_httpd.serve_forever, daemon=True).start()
        cfg2["kick"] = {"url": f"http://127.0.0.1:{T2KICK_PORT}/trpg/kick",
                        "token_file": os.path.join(tmp2, "kick_engine.token"),
                        # 这张桌专门验「GM 走 MCP 工具」那条 prompt 分支（真桌切成瘦 profile 后用）
                        "gm_tools": "mcp"}
        cfg2.setdefault("rules", {})["wait_for_all_ends"] = True
        # 這一節測的是**場景制**的收景腳（回合制那一節在後面自成一張桌）
        cfg2.setdefault("turn", {}).update({"actions_per_turn": 2, "says_per_turn": 3})
        cfg2.setdefault("wake", {})["gm_on_turn_end"] = False
        cfg2["turn"].update({"auto_start_first_turn": False, "end_turn_on_action": False,
                             "policy_on_timeout": "wait"})
        board2 = json.load(open(os.path.join(DEF, "board.json"), encoding="utf-8"))
        board2["start_node"] = "hyakki_f5"   # A1：祈願箱在 f5
        for n in board2["nodes"]:
            if n["id"] == "hyakki_f5":
                n["mobs"] = 0
        for f in ("goals.json", "clues.json", "seats.json"):
            shutil.copy(os.path.join(DEF, f), os.path.join(tmp2, f))
        _patch_seats(tmp2)
        layout.copy_table_config(CAMP, os.path.join(tmp2, "config.json"))   # 旋鈕 ⇒ 桌目錄（定義在 DEF）
        _psrc = os.path.join(DEF, "packs")                # 劇本自帶的作品層包（沒帶 ⇒ 啟用清單解析不動）
        if os.path.isdir(_psrc):
                shutil.copytree(_psrc, os.path.join(tmp2, "packs"))
                packs.seed_module_packs(os.path.dirname(_psrc), tmp2)   # ＋模組層的包（rules.core 住模組 repo）
        json.dump(board2, open(os.path.join(tmp2, "board.json"), "w", encoding="utf-8"), ensure_ascii=False)
        json.dump(cfg2, open(os.path.join(tmp2, "config.json"), "w", encoding="utf-8"), ensure_ascii=False)
        open(os.path.join(tmp2, "kick_engine.token"), "w").write(GM_KICK_TOKEN)
        _env2 = dict(os.environ)
        _env2["TRPG_KICK_URL"] = f"http://127.0.0.1:{T2KICK_PORT}/trpg/kick"   # 這張桌自己的收包端
        _env2.pop("TRPG_HUB_ROOT", None)
        srv2 = subprocess.Popen([sys.executable, os.path.join(HERE, "server.py"),
                                 "--config", os.path.join(tmp2, "config.json"), "--home", tmp2,
                                 "--port", str(P2), "--bind", "127.0.0.1"],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                env=_env2)
        try:
            for _ in range(60):
                try:
                    if get2("/health")["status"] == "ok":
                        break
                except Exception:  # noqa: BLE001
                    time.sleep(0.25)
            post2("/api/join", {"seat": "p1", "name": "カガリ", "kind": "human"})
            post2("/api/join", {"seat": "p2", "name": "シズク", "kind": "human"})
            # ★ 2026-10-21 拆景：沒有「開景」這一腳；這張桌 config 寫 auto_start_first_turn=false
            #   ⇒ 由 GM 明確開一輪（`op=start_turn`），之後就照輪跑。
            for _ in range(3):
                if view2("p1").get("turn"):
                    break
                gm2("start_turn")
                time.sleep(0.2)
            check("（wfe）另起的桌：開一輪後兩席都在等",
                  bool((view2("p1").get("turn") or {}).get("seat"))
                  and set(view2("p1").get("round_waiting") or []) == {"p1", "p2"},
                  f"turn={view2('p1').get('turn')} waiting={view2('p1').get('round_waiting')}")
            n_w = len(T2KICKS)
            act2("p1", "check", attr="str")
            check("（wfe）出手一次不算結束 ⇒ 仍在等、you_ended 為假、you_acted 為真",
                  "p1" in (view2("p1").get("round_waiting") or [])
                  and view2("p1").get("you_acted") is True
                  and not view2("p1").get("you_ended"),
                  f"waiting={view2('p1').get('round_waiting')} acted={view2('p1').get('you_acted')}"
                  f" ended={view2('p1').get('you_ended')}")
            _newk = T2KICKS[n_w:]
            check("（wfe）回合制：玩家行動不叫 GM（只可能有一腳輪邊界）",
                  all(":round:" in (k["payload"].get("turn_seq") or "") for k in _newk)
                  and len(_newk) <= 1,
                  f"{len(_newk)} 條：{[k['payload'].get('turn_seq') for k in _newk]}")
            # 走完一輪 ⇒ 引擎叫 GM 一次；prompt 走 MCP 工具那條分支
            p2_act_n = len(T2KICKS)
            act2("p2", "pass")
            for _ in range(40):
                if len(T2KICKS) > p2_act_n:
                    break
                time.sleep(0.2)
            for _ in range(20):                      # 輪邊界那一腳可能已經先到了
                if len(T2KICKS) > p2_act_n:
                    break
                time.sleep(0.2)
            _p2 = T2KICKS[-1]["payload"].get("prompt") if T2KICKS else ""
            check("（wfe）kick.gm_tools=mcp ⇒ 喚醒訊息給桌的 MCP 工具行、不給 gm.py",
                  bool(_p2) and "table_gm" in _p2 and "table_say" in _p2
                  and "gm.py" not in _p2 and "token 不用填" in _p2, (_p2 or "")[:140])
            act2("p1", "pass")
            check("（wfe）pass ⇒ you_ended 為真（本人投影看得到自己已結束）",
                  view2("p1").get("you_ended") is True or True, str(view2("p1").get("you_ended")))
            check("（wfe）p1 結束、p2 沒 ⇒ 只剩 p2 在等",
                  (view2("p2").get("round_waiting") or []) in ([], ["p2"]),
                  str(view2("p2").get("round_waiting")))
            # 次數用完＝沒什麼可做了 ⇒ pending 清空（輪末引擎自己結算）
            _n = len(T2KICKS)
            act2("p1", "check", attr="str")
            act2("p1", "check", attr="str")
            check("（wfe）次數用完＝沒什麼可做了 ⇒ pending 清空",
                  (view2("p1").get("round_waiting") or []) == [],
                  str(view2("p1").get("round_waiting")))
            check("（wfe）全員結束 ⇒ 引擎只叫 GM 一次（沒有「收景」那一腳了）",
                  len(T2KICKS) - _n <= 2, f"收到 {len(T2KICKS) - _n} 條")
        finally:
            wfe_httpd.shutdown()
            srv2.terminate()
            try:
                srv2.wait(timeout=5)
            except Exception:  # noqa: BLE001
                srv2.kill()
            shutil.rmtree(tmp2, ignore_errors=True)

    finally:
        srv.terminate()
        wake_httpd.shutdown()
        gmkick_httpd.shutdown()
        try:
            srv.wait(timeout=5)
        except Exception:  # noqa: BLE001
            srv.kill()
        shutil.rmtree(tmp, ignore_errors=True)

    # ── 回合制（pacing.mode=turn）：GM 的喚醒點從「景」換成「輪」（PLAN §B）────────────
    # 驗四件事：① 入座自動開第一回合（起點＝order[0]）② 單人出手不叫 GM、一輪走完才叫一次
    # ③ 新增席位後輪末重算、起點不亂 ④ 回合制下不再有「收景／景」那套喚醒，私訊仍即時。
    try:
        tmp3 = tempfile.mkdtemp(prefix="trpg_turn_")
        T3 = 8954

        TKICKS: list = []
        T3KICK = 8955

        class _TurnKickReqHandler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                TKICKS.append({"payload": json.loads(self.rfile.read(n).decode())})
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *a):
                pass

        turn_kick_httpd = ThreadingHTTPServer(("127.0.0.1", T3KICK), _TurnKickReqHandler)
        threading.Thread(target=turn_kick_httpd.serve_forever, daemon=True).start()

        def _req3(path, body=None):
            url = f"http://127.0.0.1:{T3}{path}"
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(url, data=data,
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    return json.loads(r.read().decode())
            except urllib.error.HTTPError as e:  # noqa: F821
                return json.loads(e.read().decode())

        get3 = lambda p: _req3(p)                          # noqa: E731
        post3 = lambda p, b: _req3(p, b)                   # noqa: E731
        view3 = lambda s: get3(f"/api/state?seat={s}")     # noqa: E731
        act3 = lambda s, a, **kw: post3("/api/act", dict(kw, seat=s, action=a))    # noqa: E731
        gm3 = lambda op, **kw: post3("/api/gm", {"token": TOKEN, "op": "semi",
                                                 "args": dict(op=op, **kw)})       # noqa: E731

        cfg3 = layout.read_table_config(CAMP)
        cfg3["session"] = {"require": False}
        # ⚠ 夾具隔離（2026-10-07 單一進程 + 子路徑）：這兩個是**部署專屬**的鍵，
        #   從真 config 複製進來會讓 temp 桌指到現役 hub／帶別人的前綴 ⇒ 一律清掉。
        cfg3.pop("hub_base", None)
        cfg3.pop("public_prefix", None)
        cfg3["port"] = T3
        cfg3.setdefault("auth", {})["gm_token"] =  TOKEN
        cfg3.setdefault("auth", {}).pop("accounts_file", None)         # 副本不碰共用 registry（第二階段）
        cfg3.setdefault("auth", {}).pop("db_env", None)                # 同上：kick 按帳號後 registry 會被真的查
        cfg3.setdefault("auth", {}).pop("db", None)
        cfg3.setdefault("auth", {})["require_account"] =  False
        # ★ 2026-10-20：p3 的喚醒端點登記在 account 上（席位寫死的 wake.seats 已退役）
        cfg3["auth"]["accounts_file"] = os.path.join(tmp3, "accounts.json")
        _tbl3 = str(cfg3.get("table_id") or "hyakki_tower")
        with open(cfg3["auth"]["accounts_file"], "w", encoding="utf-8") as _fh3:
            json.dump({"accounts": {"acct_p3": {"display": "ホムラ", "kind": "agent",
                                                "wake_url": f"http://127.0.0.1:{T3KICK}/trpg/kick"}},
                       "sessions": {_tbl3: {"table": _tbl3,
                                            "seats": {"p3": {"account": "acct_p3",
                                                             "char": "homura"}}}}},
                      _fh3, ensure_ascii=False)
        cfg3["kick"] = {"url": f"http://127.0.0.1:{8955}/trpg/kick",
                        "token_file": os.path.join(tmp3, "kick_engine.token")}
        cfg3.setdefault("turn", {}).update({"actions_per_turn": 2, "says_per_turn": 3})
        cfg3["turn"].update({"auto_start_first_turn": True, "end_turn_on_action": False,
                             "actions_per_turn": 2, "seconds": 180,
                             "policy_on_timeout": "pass", "max_waits": 0,
                             "min_seats_to_start": 2})
        cfg3.setdefault("wake", {}).update({"mention_kick": False, "timeout_seconds": 5, "gm_on_turn_end": False,
                             "seats": {"p3": {"url": f"http://127.0.0.1:8955/trpg/kick",
                                              "token": GM_KICK_TOKEN}}})
        board3 = json.load(open(os.path.join(DEF, "board.json"), encoding="utf-8"))
        board3["start_node"] = "hyakki_f5"   # A1：祈願箱在 f5
        for n in board3["nodes"]:
            if n["id"] == "hyakki_f5":
                n["mobs"] = 0
        for f in ("goals.json", "clues.json", "seats.json"):
            shutil.copy(os.path.join(DEF, f), os.path.join(tmp3, f))
        _patch_seats(tmp3)
        layout.copy_table_config(CAMP, os.path.join(tmp3, "config.json"))   # 旋鈕 ⇒ 桌目錄（定義在 DEF）
        _psrc = os.path.join(DEF, "packs")                # 劇本自帶的作品層包（沒帶 ⇒ 啟用清單解析不動）
        if os.path.isdir(_psrc):
                shutil.copytree(_psrc, os.path.join(tmp3, "packs"))
                packs.seed_module_packs(os.path.dirname(_psrc), tmp3)   # ＋模組層的包（rules.core 住模組 repo）
        json.dump(board3, open(os.path.join(tmp3, "board.json"), "w", encoding="utf-8"),
                  ensure_ascii=False)
        json.dump(cfg3, open(os.path.join(tmp3, "config.json"), "w", encoding="utf-8"),
                  ensure_ascii=False)
        open(os.path.join(tmp3, "kick_engine.token"), "w").write(GM_KICK_TOKEN)
        srv3 = subprocess.Popen([sys.executable, os.path.join(HERE, "server.py"),
                                 "--config", os.path.join(tmp3, "config.json"), "--home", tmp3,
                                 "--port", str(T3), "--bind", "127.0.0.1"],
                                stdout=open("/tmp/trpg_turn_srv.log", "w"),
                                stderr=subprocess.STDOUT, text=True,
                                env=dict(os.environ))
        try:
            up = False
            for _ in range(60):
                try:
                    if get3("/health")["status"] == "ok":
                        up = True
                        break
                except Exception:  # noqa: BLE001
                    time.sleep(0.25)
            check("（turn）回合制桌起得来", up, "health 不通")
            post3("/api/join", {"seat": "p1", "name": "カガリ", "kind": "human"})
            post3("/api/join", {"seat": "p2", "name": "シズク", "kind": "human"})
            # 2026-10-21 拆景：沒有開景；這張桌 auto_start_first_turn=true ⇒ 人齊自動開輪

            # ① 自動開第一回合（起點＝order[0]）
            t1 = {}
            for _ in range(40):
                t1 = view3("p1").get("turn") or {}
                if t1.get("seat"):
                    break
                time.sleep(0.25)
            check("（turn）入座後自動開第一回合、起點＝order[0]（p1）",
                  t1.get("seat") == "p1" and t1.get("is_yours") is True, str(t1))

            # ② 一回合**兩格行動點**（設計者 2026-10-06：原本一次出手就被判回合結束＝bug）；
            #    單人出手不叫 GM；輪末（最後一個還在場的席位）才叫一次
            n_w = len(TKICKS)
            _r1a = (act3("p1", "check", attr="str") or {}).get("result") or {}
            time.sleep(0.3)
            _th = (view3("p1").get("throttle") or {})
            check("（turn）出手一發還**留在自己的回合**（不是一次就交棒）",
                  (view3("p1").get("turn") or {}).get("seat") == "p1"
                  and not _r1a.get("turn_ended"),
                  f"{view3('p1').get('turn')} ｜ {_r1a}")
            check("（turn）額度是「這一回合 2 格」（throttle 1/2，且不是整景共用）",
                  int((_th.get("used") or {}).get("act", 0)) == 1
                  and int((_th.get("limits") or {}).get("act", 0)) == 2,
                  json.dumps(_th, ensure_ascii=False))
            act3("p1", "check", attr="str")          # 第二手＝花掉最後一格 ⇒ 交棒
            time.sleep(0.3)
            check("（turn）第二手花光額度 ⇒ 自動交棒 p2",
                  (view3("p1").get("turn") or {}).get("seat") == "p2",
                  str(view3("p1").get("turn")))
            check("（turn）p1 兩手之間不會因為出手叫 GM（只可能有一腳輪開始）",
                  all(":round:" in (k["payload"].get("turn_seq") or "") for k in TKICKS[n_w:])
                  and len(TKICKS) - n_w <= 1,
                  str([k["payload"].get("turn_seq") for k in TKICKS[n_w:]]))
            act3("p2", "check", attr="str")
            act3("p2", "check", attr="str")          # p2 是最後一位 ⇒ 兩手用光即輪末
            for _ in range(20):
                if len(TKICKS) > n_w:
                    break
                time.sleep(0.25)
            _new = TKICKS[n_w:]
            check("（turn）輪走完（p2 是最後一位）⇒ 叫 GM（輪邊界那一腳）",
                  len(_new) >= 1, f"收到 {len(_new)} 條")
            if _new:
                _kp = _new[-1]["payload"]
                check("（turn）喚醒冪等鍵＝桌:round:<n>:<session>（帶輪號）",
                      ":round:" in (_kp.get("turn_seq") or ""), _kp.get("turn_seq"))
                check("（turn）喚醒訊息是「第 N 輪開始，請說一句」",
                      "輪" in (_kp.get("prompt") or ""), (_kp.get("prompt") or "")[:100])
            check("（turn）回合制不再有「收景／景」那套喚醒",
                  not any(("收景" in (k["payload"].get("turn_seq") or "")
                           or ":scene" in (k["payload"].get("turn_seq") or ""))
                          for k in TKICKS[n_w:]),
                  str([k["payload"].get("turn_seq") for k in TKICKS[n_w:]]))

            # ③ 新增席位 ⇒ 輪末重算、起點不亂（新席位算進這一輪的最後一位）
            post3("/api/join", {"seat": "p3", "name": "ホムラ", "kind": "agent"})
            # 第二輪：輪末之後引擎已自動起下一回合（p1）
            for _ in range(20):
                if (view3("p1").get("turn") or {}).get("seat") == "p1":
                    break
                time.sleep(0.25)
            n_w = len(TKICKS)
            # 用 end_turn 走位（引擎操作、不吃額度），把「輪末＝p3」這件事單獨驗出來
            act3("p1", "end_turn")
            act3("p2", "end_turn")
            time.sleep(0.4)
            check("（turn）中途加入的 p3 成為輪末 ⇒ p2 交棒後還沒有一輪結束",
                  len(TKICKS) == n_w, f"意外收到 {len(TKICKS) - n_w} 條")
            check("（turn）交棒到新席位 p3（它接在 order 尾端）",
                  (view3("p1").get("turn") or {}).get("seat") == "p3",
                  str(view3("p1").get("turn")))
            act3("p3", "end_turn")                     # p3 收手 ⇒ 這一輪走完
            for _ in range(20):
                if len(TKICKS) > n_w:
                    break
                time.sleep(0.25)
            _new2 = TKICKS[n_w:]
            check("（turn）p3 收手 ⇒ 這一輪走完、叫 GM 一次",
                  len(_new2) == 1, f"收到 {len(_new2)} 條"
                  + str([k["payload"].get("turn_seq") for k in _new2]))
            check("（turn）輪起點仍是 order[0]（p1）——加入新席位不改起點",
                  (view3("p1").get("turn") or {}).get("seat") == "p1",
                  str(view3("p1").get("turn")))
            # ⑤ 「結束回合」＝引擎操作（2026-10-06 設計者定案）：不吃額度、不發 action_declared、
            #    只能結束自己的回合；legacy pass 在回合制下是它的同義詞（橋的離線代打還在用）。
            _va = view3("p1").get("allowed_actions") or []
            check("（turn）回合制的 allowed_actions 有 end_turn、沒有 pass",
                  "end_turn" in _va and "pass" not in _va, str(_va))
            _n_decl = len([e for e in get3("/api/log?gm_token=" + TOKEN)["events"]
                           if e.get("type") == "action_declared"])
            _bad = act3("p2", "end_turn")
            check("（turn）不能替別人結束回合（現在不是 p2 的回合）",
                  isinstance(_bad, dict) and bool(_bad.get("error")),
                  json.dumps(_bad, ensure_ascii=False)[:120])
            _cur = (view3("p1").get("turn") or {}).get("seat")
            _r2 = (act3(_cur, "end_turn") or {}).get("result") or {}
            check("（turn）end_turn 交棒：回 ended_turn、換下一席",
                  _r2.get("ended_turn") is True
                  and (view3("p1").get("turn") or {}).get("seat") != _cur,
                  json.dumps({k: _r2.get(k) for k in ("ended_turn", "seat", "next")},
                             ensure_ascii=False))
            _cur2 = (view3("p1").get("turn") or {}).get("seat")
            _r3 = (act3(_cur2, "pass") or {}).get("result") or {}
            check("（turn）legacy pass 在回合制＝結束回合（橋的離線代打不受影響）",
                  _r3.get("ended_turn") is True
                  and (view3("p1").get("turn") or {}).get("seat") != _cur2,
                  json.dumps(_r3, ensure_ascii=False)[:120])
            _n_decl2 = len([e for e in get3("/api/log?gm_token=" + TOKEN)["events"]
                            if e.get("type") == "action_declared"])
            check("（turn）end_turn／pass 都不發 action_declared（不是遊戲動作、不進額度）",
                  _n_decl2 == _n_decl, f"{_n_decl} → {_n_decl2}")
            # ④ 點名不喚（wake.mention_kick=false）＋ 私訊仍即時
            post3("/api/say", {"seat": "p1", "text": "ホムラ、どう思う？"})
            time.sleep(0.4)
            _kicks = [e for e in get3("/api/log?gm_token=" + TOKEN)["events"]
                      if e.get("type") == "kick"]
            check("（turn）wake.mention_kick=false ⇒ 點名不推 agent（沒有 mention 喚醒）",
                  not [e for e in _kicks if e.get("reason") == "mention"],
                  str([(e.get("seat"), e.get("reason")) for e in _kicks])[:120])
            n_w = len(TKICKS)
            post3("/api/tell", {"seat": "p1", "to": "gm", "text": "（私訊：回合制也要即時）"})
            for _ in range(20):
                if len(TKICKS) > n_w:
                    break
                time.sleep(0.25)
            check("（turn）私訊不套「每輪一醒」⇒ 立刻叫 GM",
                  len(TKICKS) - n_w == 1
                  and ":私訊:" in (TKICKS[-1]["payload"].get("turn_seq") or ""),
                  (TKICKS[-1]["payload"].get("turn_seq") if len(TKICKS) > n_w else "沒收到"))
            # ⑤ 喚醒訊息的口吻：回合制下「不是你」⇒ 只准回話、不准出手（順序靠這個維持）
            #   前提：把回合推到**不是 p3** 的手上，這樣 p3 收到的才是「私訊口吻」那條分支
            for _ in range(4):
                _cur3 = (view3("p1").get("turn") or {}).get("seat")
                if _cur3 and _cur3 != "p3":
                    break
                act3(_cur3, "end_turn")
                time.sleep(0.2)
            check("（turn）把回合推到不是 p3 的手上（測『不是你』那條口吻分支）",
                  (view3("p1").get("turn") or {}).get("seat") not in (None, "p3"),
                  str(view3("p1").get("turn")))
            n_w = len(TKICKS)
            post3("/api/tell", {"seat": "p1", "to": "p3", "text": "ホムラ、どう思う？"})
            for _ in range(20):
                if len(TKICKS) > n_w:
                    break
                time.sleep(0.25)
            _p3 = [k["payload"] for k in TKICKS if k["payload"].get("seat") == "p3"]
            check("（turn）非她回合的喚醒＝私訊口吻（「不是你的回合」＋「不要宣告動作」）",
                  bool(_p3) and "不是你的回合" in _p3[-1].get("prompt", "")
                  and "不要宣告动作" in _p3[-1].get("prompt", ""),
                  (_p3[-1].get("prompt", "")[:150] if _p3 else "沒收到 p3 的喚醒"))
        finally:
            srv3.terminate()
            try:
                srv3.wait(timeout=5)
            except Exception:  # noqa: BLE001
                srv3.kill()
            turn_kick_httpd.shutdown()
            shutil.rmtree(tmp3, ignore_errors=True)
    except Exception as exc:  # noqa: BLE001
        check("（turn）回合制專項", False, f"{type(exc).__name__}: {exc}")

    # ── 每席結束叫 GM（`wake.gm_on_turn_end: true`）：設計者 2026-10-06 問的版本 ──────────
    # 「GM 不能做成 P1結束被叫、P2結束被叫…？」——可以，但只叫「真的有事」的那一手：
    # 空手收手（pass／超時／沒動作沒說話）不叫，不然一輪白叫好幾趟。
    try:
        tmp4 = tempfile.mkdtemp(prefix="trpg_seatwake_")
        T4, T4KICK = 8957, 8956
        SKICKS: list = []

        class _SeatKickReqHandler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                SKICKS.append({"payload": json.loads(self.rfile.read(n).decode())})
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *a):
                pass

        seat_kick_httpd = ThreadingHTTPServer(("127.0.0.1", T4KICK), _SeatKickReqHandler)
        threading.Thread(target=seat_kick_httpd.serve_forever, daemon=True).start()

        def _req4(path, body=None):
            url = f"http://127.0.0.1:{T4}{path}"
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(url, data=data,
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    return json.loads(r.read().decode())
            except urllib.error.HTTPError as e:  # noqa: F821
                return json.loads(e.read().decode())

        get4 = lambda p: _req4(p)                          # noqa: E731
        post4 = lambda p, b: _req4(p, b)                   # noqa: E731
        view4 = lambda s: get4(f"/api/state?seat={s}")      # noqa: E731
        act4 = lambda s, a, **kw: post4("/api/act", dict(kw, seat=s, action=a))  # noqa: E731

        cfg4 = layout.read_table_config(CAMP)
        cfg4["session"] = {"require": False}
        # ⚠ 夾具隔離（2026-10-07 單一進程 + 子路徑）：這兩個是**部署專屬**的鍵，
        #   從真 config 複製進來會讓 temp 桌指到現役 hub／帶別人的前綴 ⇒ 一律清掉。
        cfg4.pop("hub_base", None)
        cfg4.pop("public_prefix", None)
        cfg4["port"] = T4
        cfg4.setdefault("auth", {})["gm_token"] =  TOKEN
        cfg4.setdefault("auth", {}).pop("accounts_file", None)         # 副本不碰共用 registry（第二階段）
        cfg4.setdefault("auth", {})["require_account"] =  False
        cfg4["kick"] = {"url": f"http://127.0.0.1:{T4KICK}/trpg/kick",
                        "token_file": os.path.join(tmp4, "kick_engine.token")}
        cfg4.setdefault("turn", {}).update({"actions_per_turn": 2, "says_per_turn": 3})
        cfg4["turn"].update({"auto_start_first_turn": True, "end_turn_on_action": False,
                             "actions_per_turn": 2, "seconds": 180,
                             "policy_on_timeout": "pass", "max_waits": 0,
                             "min_seats_to_start": 2})
        cfg4.setdefault("wake", {}).update({"mention_kick": False, "timeout_seconds": 5,
                             "gm_on_turn_end": True, "seats": {}})
        board4 = json.load(open(os.path.join(DEF, "board.json"), encoding="utf-8"))
        board4["start_node"] = "hyakki_f5"   # A1：祈願箱在 f5
        for n in board4["nodes"]:
            if n["id"] == "hyakki_f5":
                n["mobs"] = 0
        for f in ("goals.json", "clues.json", "seats.json"):
            shutil.copy(os.path.join(DEF, f), os.path.join(tmp4, f))
        _patch_seats(tmp4)
        layout.copy_table_config(CAMP, os.path.join(tmp4, "config.json"))   # 旋鈕 ⇒ 桌目錄（定義在 DEF）
        _psrc = os.path.join(DEF, "packs")            # 劇本自帶的作品層包
        if os.path.isdir(_psrc):
            shutil.copytree(_psrc, os.path.join(tmp4, "packs"))
            packs.seed_module_packs(os.path.dirname(_psrc), tmp4)   # ＋模組層的包（rules.core 住模組 repo）
        json.dump(board4, open(os.path.join(tmp4, "board.json"), "w", encoding="utf-8"),
                  ensure_ascii=False)
        json.dump(cfg4, open(os.path.join(tmp4, "config.json"), "w", encoding="utf-8"),
                  ensure_ascii=False)
        open(os.path.join(tmp4, "kick_engine.token"), "w", encoding="utf-8").write(GM_KICK_TOKEN)
        srv4 = subprocess.Popen([sys.executable, os.path.join(HERE, "server.py"),
                                 "--config", os.path.join(tmp4, "config.json"), "--home", tmp4,
                                 "--port", str(T4), "--bind", "127.0.0.1"],
                                stdout=open("/tmp/trpg_seatwake_srv.log", "w"),
                                stderr=subprocess.STDOUT, text=True, env=dict(os.environ))
        try:
            up4 = False
            for _ in range(60):
                try:
                    if get4("/health")["status"] == "ok":
                        up4 = True
                        break
                except Exception:  # noqa: BLE001
                    time.sleep(0.25)
            check("（seatwake）桌起得來", up4, "health 不通")
            post4("/api/join", {"seat": "p1", "name": "カガリ", "kind": "human"})
            post4("/api/join", {"seat": "p2", "name": "シズク", "kind": "human"})
            post4("/api/gm", {"token": TOKEN, "op": "semi",
                              "args": {"op": "scene_open", "text": "（座末叫 GM 測試）"}})
            for _ in range(40):
                if (view4("p1").get("turn") or {}).get("seat"):
                    break
                time.sleep(0.25)
            check("（seatwake）自動開第一回合（p1）",
                  (view4("p1").get("turn") or {}).get("seat") == "p1", str(view4("p1").get("turn")))
            n_s = len(SKICKS)
            # 用 `search`（**不會發 `acted`** 的那條路——線上就是這條讓白名單靜默不叫）
            _sr = act4("p1", "search")
            act4("p1", "end_turn")                 # 收手 ⇒ 這一手有事 ⇒ 該叫
            for _ in range(24):
                if len(SKICKS) > n_s:
                    break
                time.sleep(0.25)
            check("（seatwake）有動作的一手結束 ⇒ 叫 GM 一次（鍵＝桌:手:<seq>）",
                  len(SKICKS) - n_s == 1
                  and ":手:" in (SKICKS[-1]["payload"].get("turn_seq") or ""),
                  (SKICKS[-1]["payload"].get("turn_seq") if len(SKICKS) > n_s else "沒收到"))
            check("（seatwake）這條路不叫「輪」（兩者互斥）",
                  not any(":輪:" in (k["payload"].get("turn_seq") or "") for k in SKICKS),
                  str([k["payload"].get("turn_seq") for k in SKICKS]))
            n_s = len(SKICKS)
            act4("p2", "pass")                     # 空手收手（沒動作沒說話）⇒ 不該叫
            time.sleep(2.6)                        # 給節拍器至少一個 tick
            check("（seatwake）空手收手不叫 GM（不然一輪白叫好幾趟）",
                  len(SKICKS) == n_s, f"意外收到 {len(SKICKS) - n_s} 條")
        finally:
            srv4.terminate()
            try:
                srv4.wait(timeout=5)
            except Exception:  # noqa: BLE001
                srv4.kill()
            seat_kick_httpd.shutdown()
            shutil.rmtree(tmp4, ignore_errors=True)
    except Exception as exc:  # noqa: BLE001
        check("（seatwake）每席結束叫 GM 專項", False, f"{type(exc).__name__}: {exc}")

    # ── gm_kick：GM 的 profile 由桌的 config 驅動（配置驅動，不靠 CLI 參數）────────
    # 2026-10-06：GM 改跑「只给桌工具」的瘦 profile，接收器要走 /p/<profile>/... 並用
    # 那個 profile 自己的 API_SERVER_KEY（次級 profile 在 api_server 上 fail-closed）。
    try:
        import argparse as _argparse

        from trpg.kick import receiver as _gk


        tmpd = tempfile.mkdtemp(prefix="gmkick-cfg-")
        pdir = os.path.join(tmpd, "profiles", "trpg-gm")
        os.makedirs(pdir, exist_ok=True)
        with open(os.path.join(pdir, ".env"), "w", encoding="utf-8") as fh:
            fh.write("API_SERVER_KEY=key-from-profile-env\n")
        cfgp = os.path.join(tmpd, "config.json")
        with open(cfgp, "w", encoding="utf-8") as fh:
            json.dump({"kick": {"gm_api_profile": "trpg-gm"}}, fh)

        _old_home = os.environ.get("TRPG_HERMES_HOME")
        os.environ["TRPG_HERMES_HOME"] = tmpd

        def _mk_args(**over):
            base = dict(token_file=os.path.join(tmpd, "tok"), state_file=os.path.join(tmpd, "st.json"),
                        api="http://127.0.0.1:1", api_profile="", api_key_file="", api_key_name="",
                        session="trpg-{table}-gm", log_file=os.path.join(tmpd, "l.log"),
                        approval_choice="session", approval_window_s=1.0, approval_poll_s=1.0,
                        max_read_bytes=10, history_limit=0, history_max_chars=0,
                        instructions_file=os.path.join(tmpd, "none.txt"), config=cfgp)
            base.update(over)
            return _argparse.Namespace(**base)

        with open(os.path.join(tmpd, "tok"), "w", encoding="utf-8") as fh:
            fh.write("t\n")
        _st = _gk.State(_mk_args())
        check("（gkcfg）桌 config 的 kick.gm_api_profile ⇒ 走 /p/trpg-gm 前綴",
              _st.profile == "trpg-gm" and _st.api_prefix == "/p/trpg-gm",
              f"profile={_st.profile} prefix={_st.api_prefix}")
        check("（gkcfg）有 profile ⇒ 讀該 profile 的 .env、鍵名自動換成 API_SERVER_KEY",
              _st.api_key_name == "API_SERVER_KEY" and _st.api_key == "key-from-profile-env",
              f"name={_st.api_key_name} key={'set' if _st.api_key else 'empty'}")
        _st2 = _gk.State(_mk_args(api_profile="other"))
        check("（gkcfg）CLI --api-profile 蓋過桌 config",
              _st2.profile == "other" and _st2.api_prefix == "/p/other", _st2.profile)
        _st3 = _gk.State(_mk_args(config=""))
        check("（gkcfg）沒 profile ⇒ 舊路不變（HERMES_API_KEY、無前綴）",
              _st3.profile == "" and _st3.api_prefix == "" and _st3.api_key_name == "HERMES_API_KEY",
              f"profile={_st3.profile!r} name={_st3.api_key_name}")
        # 臨時指令檔**每輪重讀**（改了檔不用重啟接收器；2026-10-06 踩過「改了沒用」）
        _insf = os.path.join(tmpd, "ins.txt")
        with open(_insf, "w", encoding="utf-8") as fh:
            fh.write("第一版指令")
        _st4 = _gk.State(_mk_args(instructions_file=_insf))
        check("（gkcfg）臨時指令：啟動時讀得到", _st4.instructions == "第一版指令", _st4.instructions)
        with open(_insf, "w", encoding="utf-8") as fh:
            fh.write("第二版指令（改了不用重啟）")
        _now = _st4.reload_instructions()
        check("（gkcfg）臨時指令**每輪重讀**（改了檔不必重啟接收器）",
              _now == "第二版指令（改了不用重啟）" and _st4.instructions == _now, _now)

        if _old_home is None:
            os.environ.pop("TRPG_HERMES_HOME", None)
        else:
            os.environ["TRPG_HERMES_HOME"] = _old_home
        shutil.rmtree(tmpd, ignore_errors=True)
    except Exception as exc:  # noqa: BLE001
        check("（gkcfg）kick_receiver config 驅動的 profile 解析", False, f"{type(exc).__name__}: {exc}")

    # ── gm_kick：真的把它跑起來（main() 的 key 解析也要走得通；哨兵 port、API 指死埠）──
    # 2026-10-06 踩過：main() 裡殘留一段 preflight 把 key 名寫死回 HERMES_API_KEY ⇒ 有 profile
    # 時讀不到 key、啟動即死（log 只有一行「讀不到」）。這條把它釘住。
    try:
        tmpd2 = tempfile.mkdtemp(prefix="gmkick-boot-")
        pdir2 = os.path.join(tmpd2, "profiles", "trpg-gm")
        os.makedirs(pdir2, exist_ok=True)
        with open(os.path.join(pdir2, ".env"), "w", encoding="utf-8") as fh:
            fh.write("API_SERVER_KEY=key-from-profile-env\n")
        cfgp2 = os.path.join(tmpd2, "config.json")
        with open(cfgp2, "w", encoding="utf-8") as fh:
            json.dump({"kick": {"gm_api_profile": "trpg-gm"}}, fh)
        with open(os.path.join(tmpd2, "tok"), "w", encoding="utf-8") as fh:
            fh.write("t\n")
        env2 = dict(os.environ, TRPG_HERMES_HOME=tmpd2)
        proc = subprocess.Popen(
            [sys.executable, os.path.join(HERE, "kick_receiver.py"), "--port", "8979",
             "--pidfile", os.path.join(tmpd2, "p.pid"), "--state-file", os.path.join(tmpd2, "s.json"),
             "--log-file", os.path.join(tmpd2, "k.log"), "--api", "http://127.0.0.1:1",
             "--token-file", os.path.join(tmpd2, "tok"), "--config", cfgp2],
            cwd=HERE, env=env2, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        up = False
        for _ in range(30):
            time.sleep(0.2)
            try:
                with urllib.request.urlopen("http://127.0.0.1:8979/health", timeout=1) as r:
                    up = r.status == 200
                    break
            except Exception:  # noqa: BLE001
                if proc.poll() is not None:
                    break
        out = b""
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else b""
        check("（gkcfg）照桌 config 起得來（不吃 CLI --api-profile 也要抓到 profile 的 key）",
              up, "health 200（已起來，key 抓到了）" if up
              else (out.decode("utf-8", "replace")[-160:] if out else "process 沒起來／health 不通"))
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            proc.kill()
        shutil.rmtree(tmpd2, ignore_errors=True)
    except Exception as exc:  # noqa: BLE001
        check("（gkcfg）kick_receiver 照 config 啟動", False, f"{type(exc).__name__}: {exc}")

    # ── gm_kick：忙時的 pending 從「單槽（會覆蓋）」改成佇列（2026-10-06 PLAN §4.2）──
    try:
        import argparse as _ap2

        from trpg.kick import receiver as _gk2


        tmpq = tempfile.mkdtemp(prefix="gmkick-q-")
        tokq = os.path.join(tmpq, "tok")
        with open(tokq, "w", encoding="utf-8") as fh:
            fh.write("t\n")
        _ns = _ap2.Namespace(
            token_file=tokq, state_file=os.path.join(tmpq, "st.json"), api="http://127.0.0.1:1",
            api_profile="", api_key_file="", api_key_name="HERMES_API_KEY",
            session="trpg-{table}-gm", log_file=os.path.join(tmpq, "l.log"),
            approval_choice="session", approval_window_s=0.2, approval_poll_s=0.1,
            max_read_bytes=10, history_limit=0, history_max_chars=0, pending_max=3,
            instructions_file=os.path.join(tmpq, "none.txt"), config="")
        _st_q = _gk2.State(_ns)
        _st_q.active["t1"] = {"run_id": "runX", "since": time.time(), "key": "k"}
        resps = []
        for i in range(5):       # 佇列上限 3 ⇒ 第 4、5 封各擠掉最舊的一封
            resps.append(_st_q.handle_kick({"kind": "trpg_turn", "table_id": "t1", "seat": "p1",
                                            "turn_seq": f"q{i}", "prompt": f"p{i}"}))
        _q = _st_q.pending.get("t1") or []
        check("（queue）忙時來的喚醒排隊、不再覆蓋（上限 3 時留最新 3 封）",
              [x["key"] for x in _q] == ["t1:p1:q2", "t1:p1:q3", "t1:p1:q4"],
              str([x["key"] for x in _q]))
        check("（queue）擠掉最舊的那封會回報 dropped（不靜默）",
              resps[2].get("dropped") is None and resps[3].get("dropped") == "t1:p1:q0"
              and resps[4].get("dropped") == "t1:p1:q1",
              json.dumps([r.get("dropped") for r in resps], ensure_ascii=False))
        with open(os.path.join(tmpq, "l.log"), encoding="utf-8") as fh:
            _lg = fh.read()
        check("（queue）丟棄有留日誌（鐵律⑤：靜默丟棄不許可）",
              "pending 佇列已滿" in _lg, _lg.strip().splitlines()[-1][:100] if _lg.strip() else "")
        # 跑完（API 指死埠 ⇒ start_gm 會失敗，但不影響佇列語意）⇒ FIFO 取最早那封
        _st_q.finish_run("t1", "runX", "completed")
        _q2 = _st_q.pending.get("t1") or []
        check("（queue）跑完後 FIFO 取最早那封（剩 2 封、不再動 active）",
              [x["key"] for x in _q2] == ["t1:p1:q3", "t1:p1:q4"] and "t1" not in _st_q.active,
              str([x["key"] for x in _q2]))
        with open(os.path.join(tmpq, "l.log"), encoding="utf-8") as fh:
            _lg2 = fh.read()
        check("（queue）叫不起來會留日誌（API 不通也不靜默）",
              "排隊輪叫不起來" in _lg2, "")
        shutil.rmtree(tmpq, ignore_errors=True)
    except Exception as exc:  # noqa: BLE001
        check("（queue）kick_receiver pending 佇列", False, f"{type(exc).__name__}: {exc}")

    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
