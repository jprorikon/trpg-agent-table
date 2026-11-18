#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_player_equality.py — **玩家平等原則**（設計者 2026-10-07 定案）。

原話：「至少在盤面上 agent 不知道誰是人，人也不知道誰是 agent。」

三條線，各自釘死：

  A. **席位種類的對外值**：非 GM 視角一律 `player`（`human`／`agent` 不可區分）；
     `npc`／`gm` 照舊（NPC 是世界的角色、GM 是桌子，不是玩家）；
     **GM 看真值**（它要決定叫醒誰、演 NPC）。
  B. **kick／seat_timeout 不出現在公開日誌**：只有 agent 席位會被叫醒、也只有
     人／agent 的差別讓「被跳過」有意義 ⇒ 公開就等於標出誰是 AI。兩者**仍寫進事件流**
     （鐵律⑤的可觀測性沒丟），GM 走 raw 那條照樣看得到。
  C. **回放不受影響**（只動投影與 `public` 位，不動狀態）。
"""
import json
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo 根
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.chdir(HERE)

from trpg.service import service as SVC                                                    # noqa: E402
import test_support as TS                                                # noqa: E402

FAIL = []
CAP = []


def ok(name: str, cond: bool, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  " + str(detail)) if detail else ""))
    if not cond:
        FAIL.append(name)


class _Cap(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):                                                   # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        CAP.append(json.loads(self.rfile.read(n).decode("utf-8")))
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


def free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def _has_public_true_emit(fname: str, ev_type: str) -> bool:
    """原始碼層閘：某檔裡還有沒有 `_emit("<型別>", public=True` 這種寫法。

    為什麼要靜態閘：「這兩型不對玩家公開」靠的是**發射點的 `public=False`**（白名單那層不動，
    留給 `test_packs` 的結構不變式）⇒ 有人把 public 改回 True 時，這條當場紅。
    """
    src = TS.source_text(fname)
    return f'_emit("{ev_type}", public=True' in src or f"_emit('{ev_type}', public=True" in src


def mk():
    """臨時桌：一個 GM 席 ＋ 一個人類席 ＋ 一個 agent 席（wake_url 指向本機假接收端）＋ 一個 NPC 席。"""
    cap_port = free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", cap_port), _Cap)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    root, home, cfg = TS.mk_table(module="hyakki_tower", scenario="main", table_id="eq")
    cfg = json.loads(json.dumps(cfg))
    svc = SVC.TableService(home, cfg)
    gm = svc.table.join(name="GM", kind="gm")
    human = svc.table.join(name="甲", kind="human")
    agent = svc.table.join(name="乙", kind="agent",
                           wake_url=f"http://127.0.0.1:{cap_port}/kick")
    svc.wake_urls[agent] = f"http://127.0.0.1:{cap_port}/kick"
    # ★ 2026-10-20（kick 按帳號）：把 agent 席的喚醒端點登記在 account 上（席位寫死已退役）；
    #   registry 預設住 <home>/accounts.json ⇒ 不必改 cfg。
    _tbl = str(cfg.get("table_id") or "eq")
    with open(os.path.join(home, "accounts.json"), "w", encoding="utf-8") as fh:
        json.dump({"accounts": {"acct_agent": {"display": "乙", "kind": "agent",
                                               "wake_url": f"http://127.0.0.1:{cap_port}/kick"}},
                   "sessions": {_tbl: {"table": _tbl,
                                       "seats": {agent: {"account": "acct_agent",
                                                         "char": "b"}}}}},
                  fh, ensure_ascii=False)
    npc = svc.table.join(name="ツルギ", kind="npc")
    return root, svc, {"gm": gm, "human": human, "agent": agent, "npc": npc, "httpd": httpd}


def kinds(view) -> "dict[str, str]":
    return {s["seat"]: s["kind"] for s in (view.get("seats") or [])}


def t_view() -> None:
    print("== A. 席位種類的對外值 ==")
    root, svc, S = mk()
    try:
        others = kinds(svc.table.view(seat=S["human"]))
        ok("A1 人類席看別人：人類席與 agent 席都投影成 player",
           others[S["human"]] == "player" and others[S["agent"]] == "player", others)
        own = kinds(svc.table.view(seat=S["agent"]))
        ok("A2 agent 席看別人／自己：一律 player（agent 認不出誰是人）",
           set(own[k] for k in (S["human"], S["agent"], S["gm"])) <= {"player", "gm"}, own)
        ok("A3 NPC 照舊顯示 npc（抹平只管 human／agent）", others[S["npc"]] == "npc", others)
        g = kinds(svc.table.view(gm=True))
        ok("A4 GM 看真值（引擎要決定叫醒誰、演 NPC）",
           (g[S["human"]], g[S["agent"]], g[S["npc"]], g[S["gm"]])
           == ("human", "agent", "npc", "gm"), g)
        ok("A5 匿名公開投影也是 player（沒登入的人看不出誰是 AI）",
           set(kinds(svc.table.view(seat=None)).values()) - {"player", "npc", "gm"} == set())
        p = svc.kick_prompt(S["agent"], reason="turn")
        ok("A6 席位喚醒訊息裡沒有任何席位標著（human）／（agent）",
           "（human）" not in p and "（agent）" not in p)
    finally:
        TS.rm_root(root)
        S["httpd"].shutdown()


def t_log() -> None:
    print("== B. kick／seat_timeout 不進公開日誌（但仍進事件流） ==")
    root, svc, S = mk()
    try:
        CAP.clear()
        svc.kick(S["agent"], 1)                       # 真路徑：kick 一個 agent 席
        evs = [e for e in svc.table.events if e.get("type") == "kick"]
        ok("B1 kick 真的發生且寫進事件流（可觀測性沒丟）", bool(evs) and bool(CAP), len(evs))
        pub = [e for e in svc.table.public_events() if e.get("type") == "kick"]
        ok("B2 kick 不出現在公開日誌", pub == [], pub)
        # seat_timeout：把回合期限撥到過去再 tick（真路徑）
        svc.cfg["turn"] = {"seconds": 1, "policy_on_timeout": "pass", "max_waits": 0,
                           "auto_start_first_turn": True, "min_seats_to_start": 2,
                           "actions_per_turn": 2}
        if not svc.table.state.get("turn"):
            svc.table.state["turn"] = {"seat": S["human"], "seq": 0, "waits": 0,
                                       "deadline_at": 0, "order": [S["human"], S["agent"]],
                                       "started_at": 0}
        svc.table.state["turn"]["deadline_at"] = svc.table.now() - 1
        svc.table.tick()
        to = [e for e in svc.table.events if e.get("type") == "seat_timeout"]
        ok("B3 seat_timeout 真的發生且寫進事件流", bool(to), len(to))
        pub2 = [e for e in svc.table.public_events() if e.get("type") == "seat_timeout"]
        ok("B4 seat_timeout 不出現在公開日誌", pub2 == [], pub2)
        ok("B5 這兩型的**發射點**都寫 public=False（原始碼層閘；白名單那層不動、留給 test_packs）",
           not _has_public_true_emit("wake.py", "kick")
           and not _has_public_true_emit("pacing.py", "seat_timeout"))
    finally:
        TS.rm_root(root)
        S["httpd"].shutdown()


def t_replay() -> None:
    print("== C. 回放不受影響 ==")
    root, svc, S = mk()
    try:
        svc.kick(S["agent"], 2)
        r = svc.table.replay_check()
        ok("C1 事件流回放與記憶體狀態一致（改的只是投影與 public 位）",
           bool(r.get("ok", r.get("match", True))), r)
        ok("C2 公開日誌縮了但事件流沒縮（kick 仍在 events 裡）",
           any(e.get("type") == "kick" for e in svc.table.events)
           and not any(e.get("type") == "kick" for e in svc.table.public_events()))
    finally:
        TS.rm_root(root)
        S["httpd"].shutdown()


def main() -> int:
    print("test_player_equality.py — 玩家平等原則（盤面上 human／agent 不可區分）")
    t_view()
    t_log()
    t_replay()
    print()
    print("結果: %s  （%d 失敗）" % ("全部通過" if not FAIL else "、".join(FAIL), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
