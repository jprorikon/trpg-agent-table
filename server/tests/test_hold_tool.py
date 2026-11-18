#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_hold_tool.py — `/hold-trpg` 那支工具（`tools/hold_trpg.py`）的機械驗收。

為什麼要有：這支是**設計者親手用的暫停開關**（`/hold-trpg` → 整桌暫停／`/hold-trpg off` → 恢復）。
它壞掉的形式很討厭——按了沒反應、或按了送錯命令（線上真的發生過：一開始把 `on` 直接當桌子的 op
送出去 ⇒ `HTTP 400 unknown op on`），而**桌子照樣在跑**，只有要暫停的人會發現。

2026-10-07 追加（設計者：「hold-trpg 现在需要选取到session才可用」）：**一個 session ＝ 一個副本（局）**，
暫停是對某一局做的事 ⇒ 多了 H8–H12 那一組選局閘（多局時不猜、要 `--session`）。

不打真桌：用 `unittest.mock` 換掉 `_get`／`_post`，`TRPG_CAMPAIGNS_DIR` 指向臨時劇本目錄。
最後一行 `HOLD-TOOL: PASS` / `HOLD-TOOL: FAIL`。

    python3 test_hold_tool.py
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo 根
sys.path.insert(0, os.path.join(HERE, "tools"))

import hold_trpg                                                          # noqa: E402

FAILED: list[str] = []
HEALTH: dict = {}          # port → {"session", "ended", "dead"}


def ok(name: str, cond: bool, detail: object = "") -> None:
    tail = f"  {str(detail) if detail else ''}"
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{tail}")
    if not cond:
        FAILED.append(name)


def mk_campaign(root: str, name: str, *, table_id: str, port: int, token=None, events=0,
                ended=False, session=None, dead=False) -> str:
    d = os.path.join(root, name)
    os.makedirs(os.path.join(d, "data"), exist_ok=True)
    cfg = {"table_id": table_id, "port": port, "auth": {} if token is None else {"gm_token": token}}
    with open(os.path.join(d, "config.json"), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh)
    with open(os.path.join(d, "data", "events.jsonl"), "w", encoding="utf-8") as fh:
        fh.write('{"seq":1,"type":"table_created"}\n' * max(1, events))
    HEALTH[port] = {"session": session or f"sess{port}", "ended": ended, "dead": dead}
    return d


def run_main(argv, *, health_ok=True, post_result=None):
    """跑一次 main()，回 (rc, 印出來的文字, POST 過去的 body 清單)。"""
    posts: list = []
    state = {"paused": False, "paused_seconds": 0,
             "turn": {"seat": "p1", "index": 3, "seconds_left": 120}}

    def fake_get(url, timeout=3.0):
        if url.endswith("/health"):
            if not health_ok:
                raise OSError("connection refused")
            try:
                port = int(url.split("//", 1)[1].split("/", 1)[0].split(":")[1])
            except Exception:                                            # noqa: BLE001
                port = -1
            h = HEALTH.get(port) or {}
            if h.get("dead"):
                raise OSError("connection refused")
            return {"status": "ok", "session": h.get("session", ""),
                    "ended": bool(h.get("ended"))}
        return dict(state)

    def fake_post(url, obj, timeout=8.0):
        posts.append(obj)
        if post_result is not None:
            return post_result
        if obj.get("op") == "pause":
            state["paused"] = True
            return {"ok": True, "pause": {"ok": True, "paused": True, "seq": 11}}
        state["paused"] = False
        return {"ok": True, "pause": {"ok": True, "paused": False, "seq": 12,
                                      "frozen_seconds": 6.4, "deadline_at": 999.0}}

    def fake_sess(base, table, **kw):                                       # gql_client.session_of
        return "s-test"

    def fake_gmop(base, table, session, op, args=None, *, gm="", **kw):      # gql_client.gm_op
        posts.append({"op": op, "args": dict(args or {}), "token": gm, "gm": gm,
                      "session": session, "table": table})
        if post_result is not None:
            return post_result
        if op == "pause":
            state["paused"] = True
            return {"ok": True, "pause": {"ok": True, "paused": True, "seq": 11}}
        state["paused"] = False
        return {"ok": True, "pause": {"ok": True, "paused": False, "seq": 12,
                                      "frozen_seconds": 6.4, "deadline_at": 999.0}}

    def fake_view(base, table, session, **kw):                              # gql_client.view
        return dict(state)

    old = sys.argv
    sys.argv = ["hold_trpg.py"] + list(argv)
    buf = io.StringIO()
    try:
        with mock.patch.object(hold_trpg, "_get", fake_get), \
                mock.patch.object(hold_trpg, "_post", fake_post), \
                mock.patch.object(hold_trpg.gql_client, "session_of", fake_sess), \
                mock.patch.object(hold_trpg.gql_client, "gm_op", fake_gmop), \
                mock.patch.object(hold_trpg.gql_client, "view", fake_view), \
                redirect_stdout(buf):
            rc = hold_trpg.main()
    finally:
        sys.argv = old
    return rc, buf.getvalue(), posts


def main() -> None:
    print("== H1 參數對映（人打的字 → 內部動作）==")
    ok("H1 on／pause／暫停 ⇒ on",
       all(hold_trpg.parse_want(x) == "on" for x in ("on", "pause", "暫停", "暂停", "hold")))
    ok("H1 off／resume／繼續 ⇒ resume",
       all(hold_trpg.parse_want(x) == "resume" for x in ("off", "resume", "繼續", "恢復", "go")))
    ok("H1 status ⇒ status", hold_trpg.parse_want("status") == "status")
    ok("H1 亂打 ⇒ None（印用法、不動桌子）", hold_trpg.parse_want("亂打") is None)

    tmp = tempfile.mkdtemp(prefix="holdtool-")
    old_env = os.environ.get("TRPG_CAMPAIGNS_DIR")
    os.environ["TRPG_CAMPAIGNS_DIR"] = tmp
    try:
        print("\n== H2 找不到活著的桌 ⇒ 不動作、回 1 ==")
        mk_campaign(tmp, "dead", table_id="dead", port=1, token="tk")     # port 1 一定連不上
        rc, out, posts = run_main(["on"], health_ok=False)
        ok("H2 回 1 且說找不到桌", rc == 1 and "找不到活著的桌" in out, f"rc={rc}")
        ok("H2 沒有送出任何 POST", not posts)

        print("\n== H3 有桌但 config 沒有 gm_token ⇒ 回 1、不送 POST ==")
        shutil.rmtree(os.path.join(tmp, "dead"), ignore_errors=True)   # 只留 notoken 這一局（否則多局會被選局閘先擋）
        mk_campaign(tmp, "notoken", table_id="notoken", port=8987, token=None, events=5)
        rc, out, posts = run_main(["on"])
        ok("H3 回 1 且指出缺 token", rc == 1 and "gm_token" in out, f"rc={rc}")
        ok("H3 沒有送出任何 POST", not posts)

        print("\n== H4 送出的 op 名字要是桌子的名字（回歸：曾直接送 'on' ⇒ HTTP 400）==")
        shutil.rmtree(os.path.join(tmp, "notoken"), ignore_errors=True)
        mk_campaign(tmp, "live", table_id="live", port=8988, token="tk", events=9)
        rc, out, posts = run_main(["on"])
        ok("H4 只有一局 ⇒ on 直接生效、op='pause'",
           rc == 0 and posts and posts[0].get("op") == "pause", f"posts={posts}")
        ok("H4 token 由 config 帶上（不是空字串）", bool(posts and posts[0].get("token")), "")
        ok("H4 輸出說已暫停且**印出 session**",
           "已暫停" in out and "sess8988" in out, out.strip().splitlines()[:2])
        rc, out, posts = run_main(["off", "--session", "sess8988"])
        ok("H4 off ⇒ op='resume' 且回報凍結秒數",
           rc == 0 and posts and posts[0].get("op") == "resume" and "凍了 6.4 秒" in out, f"{posts}")

        print("\n== H5 status ⇒ 只讀、列出每一局的 session ==")
        rc, out, posts = run_main(["status"])
        ok("H5 status 回 0、無 POST", rc == 0 and not posts, out.strip()[:40])
        ok("H5 印出 session／暫停狀態", "sess8988" in out and "暫停中" in out, out.strip()[:60])

        print("\n== H8–H12 選局（設計者：要**選到 session** 才可用）==")
        mk_campaign(tmp, "big", table_id="big", port=8989, token="tk", events=500,
                    session="sessBIG-aaaa")
        rc, out, posts = run_main(["on"])
        ok("H8 兩局都開著、沒給 --session ⇒ **拒絕**且不動手",
           rc == 1 and not posts and "請用 `--session" in out.replace("**", ""), out.strip()[:80])
        ok("H8 拒絕時把每一局列出來（含 session id）",
           "sess8988" in out and "sessBIG-aaaa" in out, out.strip().splitlines()[:3])
        rc, out, posts = run_main(["on", "--session", "sessBIG-aaaa"])
        ok("H9 給完整 session ⇒ 操作那一局（op=pause）",
           rc == 0 and posts and posts[0].get("op") == "pause" and "sessBIG-aaaa" in out,
           f"{posts} / {out.strip().splitlines()[:1]}")
        rc, out, posts = run_main(["on", "--session", "sessBIG"])
        ok("H10 唯一字首也選得到", rc == 0 and posts and "sessBIG-aaaa" in out, out.strip()[:60])
        rc, out, posts = run_main(["on", "--session", "nope"])
        ok("H11 session 找不到 ⇒ 拒絕、不動手",
           rc == 1 and not posts and "找不到 session" in out, out.strip()[:60])
        rc, out, posts = run_main(["status", "--session", "sessBIG"])
        ok("H12 status 也能只報那一局", rc == 0 and "sessBIG-aaaa" in out, out.strip()[:60])

        print("\n== H13 已收局的不會被自動挑中（多局時）==")
        mk_campaign(tmp, "old", table_id="old", port=8991, token="tk", events=9000,
                    ended=True, session="sessOLD-done")
        rc, out, posts = run_main(["on"])
        ok("H13 兩局開著＋一局已收局 ⇒ 仍然拒絕（不猜）",
           rc == 1 and not posts and "sessOLD-done" in out, out.strip()[:80])
        rc, out, posts = run_main(["on", "--session", "sessOLD"])
        ok("H13b 指名已收局的局 ⇒ 照做（人明確要）",
           rc == 0 and posts and "sessOLD-done" in out, out.strip()[:60])
        shutil.rmtree(os.path.join(tmp, "big"), ignore_errors=True)
        shutil.rmtree(os.path.join(tmp, "old"), ignore_errors=True)
        rc, out, posts = run_main(["on"])
        ok("H13c 只剩一局開著（其他都收局）⇒ 自動挑那一局、印出 session",
           rc == 0 and posts and "sess8988" in out, out.strip()[:60])

        print("\n== H6 --campaign 可以縮小範圍 ==")
        mk_campaign(tmp, "big2", table_id="big2", port=8992, token="tk", events=500,
                    session="sessBIG2")
        rc, out, posts = run_main(["on", "--campaign", "live"])
        ok("H6 --campaign live ⇒ 只動 live",
           rc == 0 and posts and "桌 live" in out, f"{out.strip().splitlines()[:1]}")

        print("\n== H7 本來就是該狀態 ⇒ 說「already」不重複送事件 ==")
        rc, out, posts = run_main(["on", "--campaign", "live"],
                                  post_result={"ok": True, "pause": {"ok": True, "already": True}})
        ok("H7 回報 already", rc == 0 and "本來就是這個狀態" in out, out.strip().splitlines()[:1])
    finally:
        if old_env is None:
            os.environ.pop("TRPG_CAMPAIGNS_DIR", None)
        else:
            os.environ["TRPG_CAMPAIGNS_DIR"] = old_env
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + ("HOLD-TOOL: PASS" if not FAILED else f"HOLD-TOOL: FAIL {FAILED}"))
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
