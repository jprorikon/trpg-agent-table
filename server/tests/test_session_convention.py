#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**session 規範的閘**（設計者 2026-10-07：「kick 和 MCP 規範：需要帶 session 號，
一個 session 對應一個副本實例」）。

    python3 test_session_convention.py

  S1 **kick body 一定帶** `session`（＝這一副本的實例 id）與 `session_name`（這一副本的會話名）；
     `prompt` 裡也點明要拿它去呼叫寫入類工具。
  S2 **一個 session 對應一個副本實例**：`session_name` 帶副本 id ⇒ 換一局（新 run）就是新的會話名；
     舊制會話名（`trpg-<桌>-gm`）仍吃得下（相容）；都沒有才用模板。
  S3 **MCP 工具面**：7 格都有 `session` 欄；寫入類（act／say／tell／secret／gm）在 require 下**必填**。
  S4 **MCP 閘**：`table_view` 不帶也能用、回應帶 `session`；寫入不帶／帶錯 ⇒ 工具錯誤；
     帶對 ⇒ 放行。
  S5 `config.session.require=false` ⇒ 相容期（不帶也放行，但帶錯還是擋）。
  S6 **GM 那條路**（`send_kick_to_gm`）也帶 session；沒帶時落回舊制會話名（行為不變）。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo 根
sys.path.insert(0, HERE)

from trpg.kick import receiver as kick_receiver                                                    # noqa: E402
from trpg.kick import sender as kick_sender                                                      # noqa: E402
from trpg.api import mcp_api as mcp_api                                                          # noqa: E402
from trpg.service import service as service                                                          # noqa: E402
from turn_helper import ensure_turn

WAKE_PORT = 18931
WAKES: list = []
OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


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


def mk_svc(tmp, require=True, table_id="sess_selftest", wake_url=""):
    # ★ 2026-10-24（review R3）：倉庫根的 `config.json` 不進版控 ⇒ 乾淨 clone 上會炸。
    #   改讀版控裡的 `config.example.json`（最小單桌範本），模組／包／節拍那幾段拿掉
    #   ——這支測的是 session 規範，不需要那張桌的機制包。
    cfg = json.load(open(os.path.join(HERE, "config.example.json"), encoding="utf-8"))
    for _k in ("kick", "wake", "packs", "pacing"):
        cfg.pop(_k, None)
    cfg["table_id"] = table_id
    cfg["port"] = 0
    cfg["session"] = {"require": bool(require)}
    cfg.setdefault("auth", {})["gm_token"] = "test-token"
    if wake_url:
        # ★ 2026-10-20（kick 按帳號）：端點掛在 account 上；p1 綁 acct_p1。
        cfg["auth"]["accounts_file"] = os.path.join(tmp, "accounts.json")
        with open(cfg["auth"]["accounts_file"], "w", encoding="utf-8") as fh:
            json.dump({"accounts": {"acct_p1": {"display": "A", "kind": "agent",
                                                "wake_url": wake_url}},
                       "sessions": {table_id: {"table": table_id,
                                               "seats": {"p1": {"account": "acct_p1",
                                                                "char": "a"}}}}},
                      fh, ensure_ascii=False)
    svc = service.TableService(tmp, cfg)
    svc.tools = mcp_api.tools_for(cfg, tmp)
    return svc


def main() -> int:                                                       # noqa: C901
    httpd = ThreadingHTTPServer(("127.0.0.1", WAKE_PORT), WakeHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    tmp = tempfile.mkdtemp(prefix="trpg_sess_")
    try:
        svc = mk_svc(tmp, wake_url=f"http://127.0.0.1:{WAKE_PORT}/kick")
        sess = svc.table.session_id()
        check("S0 引擎有副本實例 id（`session_id()` 非空）", bool(sess), sess)
        svc.table.join(seat="p1", name="A", kind="agent",
                       wake_url=f"http://127.0.0.1:{WAKE_PORT}/kick")
        svc.kick("p1", 1)
        body = WAKES[-1] if WAKES else {}
        check("S1a kick body 帶 `session`＝這一副本的 id",
              body.get("session") == sess, f"{body.get('session')} vs {sess}")
        check("S1b kick body 帶 `session_name`（一張桌一條會話／一副本）",
              body.get("session_name") == f"trpg-sess_selftest-{sess}-p1",
              str(body.get("session_name")))
        check("S1c prompt 點明寫入類工具要帶 session",
              f'session="{sess}"' in str(body.get("prompt") or ""))

        n1, s1 = kick_receiver.session_name_for(
            {"session": sess, "session_name": f"trpg-t-{sess}-gm"}, "t", "trpg-{table}-{session}-gm")
        n2, _ = kick_receiver.session_name_for({"session": "trpg-old_table-gm"}, "t",
                                               "trpg-{table}-{session}-gm")
        n3, _ = kick_receiver.session_name_for({"session": "rABC123"}, "t", "trpg-{table}-{session}-gm")
        check("S2a `session_name` 優先（新引擎）", n1 == f"trpg-t-{sess}-gm", n1)
        check("S2b 舊制會話名（body.session 是名字）仍吃得下", n2 == "trpg-old_table-gm", n2)
        check("S2c 只有副本 id 時套模板（{session} 代進去）", n3 == "trpg-t-rABC123-gm", n3)

        tools = {t["name"]: t for t in svc.tools}
        check("S3a 7 格工具都有 `session` 欄",
              all("session" in (t["inputSchema"].get("properties") or {}) for t in tools.values()),
              ",".join(sorted(tools)))
        writable = ("table_act", "table_say", "table_tell", "table_secret", "table_gm")
        check("S3b 寫入類在 require 下列進 required",
              all("session" in (tools[n]["inputSchema"].get("required") or []) for n in writable))
        check("S3c 讀（table_view）不列 required",
              "session" not in (tools["table_view"]["inputSchema"].get("required") or []))

        v = svc.mcp_call("table_view", {"seat": "p1"})
        vj = json.loads(v["content"][0]["text"])
        check("S4a `table_view` 不帶 session 也能用，且回應帶 session",
              (not v.get("isError")) and vj.get("session") == sess, str(vj.get("session")))

        e = svc.mcp_call("table_act", {"seat": "p1", "action": "defend"})
        check("S4b 寫入不帶 session ⇒ 工具錯誤（訊息點明要帶）",
              bool(e.get("isError")) and "session" in e["content"][0]["text"])
        e2 = svc.mcp_call("table_act", {"seat": "p1", "action": "defend", "session": "rOLD999"})
        check("S4c session 帶錯（舊副本）⇒ 擋下",
              bool(e2.get("isError")) and "rOLD999" in e2["content"][0]["text"])
        ensure_turn(svc.table)          # 拆景后：出手要求「有进行中的回合、而且是自己的」
        okv = svc.mcp_call("table_act", {"seat": "p1", "action": "defend", "session": sess})
        check("S4d 帶對 ⇒ 放行", not okv.get("isError"), okv["content"][0]["text"][:80])

        svc2 = mk_svc(tempfile.mkdtemp(prefix="trpg_sess2_"), require=False)
        svc2.table.join(seat="p1", name="B", kind="agent")
        ensure_turn(svc2.table)
        loose = svc2.mcp_call("table_act", {"seat": "p1", "action": "defend"})
        wrong = svc2.mcp_call("table_act", {"seat": "p1", "action": "defend", "session": "rOLD"})
        check("S5a require=false ⇒ 不帶也放行（相容期）", not loose.get("isError"))
        check("S5b require=false 但帶錯 ⇒ 還是擋（不靜默做在錯的副本上）", bool(wrong.get("isError")))

        WAKES.clear()
        tokf = os.path.join(tmp, "kick.token")
        with open(tokf, "w", encoding="utf-8") as fh:
            fh.write("dummy-kick-token\n")
        kick_sender.send_kick_to_gm("sess_selftest", "p1", "hi", "測試", 1, token_file=tokf,
                                    url=f"http://127.0.0.1:{WAKE_PORT}/kick", session=sess)
        gm_body = WAKES[-1] if WAKES else {}
        check("S6a GM 那條路也帶 session／session_name",
              gm_body.get("session") == sess
              and gm_body.get("session_name") == f"trpg-sess_selftest-{sess}-gm",
              f"{gm_body.get('session')} / {gm_body.get('session_name')}")
        WAKES.clear()
        kick_sender.send_kick_to_gm("sess_selftest", "p1", "hi", "測試", 2, token_file=tokf,
                                    url=f"http://127.0.0.1:{WAKE_PORT}/kick")
        old = WAKES[-1] if WAKES else {}
        check("S6b 沒帶 session ⇒ 落回舊制會話名（舊接收器行為不變）",
              old.get("session_name") == "trpg-sess_selftest-gm", str(old.get("session_name")))
    finally:
        httpd.shutdown()

    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
