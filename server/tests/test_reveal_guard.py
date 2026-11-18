#!/usr/bin/env python3
"""碎片指名投放守护（協作端 20261006-1248 §一 err6 patch）的本地验收。

对齐 協作端 的 test_reveal_guard 口径：**拒绝必须不留事件**（对 /api/log?meta=1 前后笔数）。
另加两条：已出土不能再投（私有性）／warn_node 只在 clues 带 node 时才可能出现。
运行：python3 test_reveal_guard.py
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
PORT = 8961
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = "reveal-guard-token"
OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


def post_status(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.loads(r.read().decode())


def evcount():
    return get(f"/api/log?gm_token={TOKEN}&meta=1")["count"]


def gm(op, **args):
    return post_status("/api/gm", {"token": TOKEN, "op": "semi", "args": dict(op=op, **args)})


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="trpg_reveal_")
    cfg = layout.read_table_config(CAMP)
    # ⚠ 夾具隔離（2026-10-07 單一進程 + 子路徑）：這兩個是**部署專屬**的鍵，
    #   從真 config 複製進來會讓 temp 桌指到現役 hub／帶別人的前綴 ⇒ 一律清掉。
    cfg.pop("hub_base", None)
    cfg.pop("public_prefix", None)
    cfg["port"] = PORT
    cfg.setdefault("auth", {})["gm_token"] = TOKEN
    # 绝不碰真机：kick/wake 全部指向本机未开端口
    cfg["kick"] = {"url": "http://127.0.0.1:9/trpg/kick",
                   "token_file": os.path.join(tmp, "kick_engine.token")}
    # ⚠ 2026-10-07 第二階段：`config.auth.accounts_file` 可能指到**共用的真 registry**，
    #   而 `require_account` 是 true ⇒ 這張臨時桌的每個請求都要 token（測試會全 403）。
    #   這支測的不是帳號層 ⇒ **明確關掉**（也不去碰真 registry）。
    cfg.setdefault("auth", {}).pop("accounts_file", None)
    cfg.setdefault("auth", {})["require_account"] = False
    cfg.setdefault("wake", {})
    cfg["wake"]["seats"] = {}
    cfg["wake"]["gm_on_turn_end"] = False
    cfg.setdefault("turn", {}).update({"actions_per_turn": 2, "says_per_turn": 3,
                          "agent_act_per_scene": 1})
    cfg["turn"].update({"auto_start_first_turn": False, "end_turn_on_action": False,
                        "policy_on_timeout": "wait"})
    for f in ("goals.json", "seats.json", "board.json"):        # 定義 ⇒ 定義層
        shutil.copy(os.path.join(DEF, f), os.path.join(tmp, f))
    layout.copy_table_config(CAMP, os.path.join(tmp, "config.json"))   # 旋鈕 ⇒ 桌目錄
    _psrc = os.path.join(DEF, "packs")        # 劇本自帶的作品層包（沒帶 ⇒ 啟用清單解析不動）
    if os.path.isdir(_psrc):
        shutil.copytree(_psrc, os.path.join(tmp, "packs"))
        packs.seed_module_packs(os.path.dirname(_psrc), tmp)   # ＋模組層的包（rules.core 住模組 repo）
    # 只给 f_ledger 挂一个 node（验证 warn_node 分支；出货剧本的 fragments 没有 node ⇒ 该分支不触发）
    clues = json.load(open(os.path.join(DEF, "clues.json"), encoding="utf-8"))
    for fr in clues["fragments"]:
        if fr["id"] == "f_ledger":
            fr["node"] = "hyakki_f1"
    json.dump(clues, open(os.path.join(tmp, "clues.json"), "w", encoding="utf-8"), ensure_ascii=False)
    json.dump(cfg, open(os.path.join(tmp, "config.json"), "w", encoding="utf-8"), ensure_ascii=False)
    open(os.path.join(tmp, "kick_engine.token"), "w").write("x")

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

        for sid, nm in (("p1", "カガリ"), ("p2", "シズク")):
            post_status("/api/join", {"seat": sid, "name": nm, "kind": "human"})
        post_status("/api/join", {"seat": "p3", "name": "ホムラ", "kind": "agent"})

        n0 = evcount()

        # ① fid 不存在 ⇒ 409，不留事件（旧行为：_emit 先落地才 KeyError，留幽靈 fragment_granted）
        st, b = gm("fragment_drop", to="p1", fid="nope")
        check("① fid 不存在 ⇒ 409 rule", st == 409 and b.get("kind") == "rule", f"{st} {b}")
        check("① 拒绝不留事件", evcount() == n0, f"{n0}→{evcount()}")
        check("① 没有幽灵 fragment_granted",
              not any(e["type"] == "fragment_granted" for e in
                      get(f"/api/log?gm_token={TOKEN}")["events"]), "")

        # ② 正常投放一枚
        st, b = gm("fragment_drop", to="p1", fid="f_edge")
        check("② 正常投放 ⇒ 200 且落到 p1", st == 200 and b.get("to") == "p1", f"{st} {b}")
        n1 = evcount()
        check("② 正常投放确实+1（对照：不是计数没动）", n1 == n0 + 1, f"{n0}→{n1}")

        # ③ 已出土再投 ⇒ 409（同一枚碎片两个持有人＝私有性破）
        st, b = gm("fragment_drop", to="p2", fid="f_edge")
        check("③ 已出土再投 ⇒ 409 且讯息讲出在谁手上",
              st == 409 and "p1" in str(b.get("error", "")), f"{st} {b}")
        check("③ 拒绝不留事件", evcount() == n1, f"{n1}→{evcount()}")
        check("③ 持有人没被改写（frag_holder 仍是 p1）",
              any(x["id"] == "f_edge" for x in get("/api/state?seat=p1")["your_fragments"]) and
              not any(x["id"] == "f_edge" for x in get("/api/state?seat=p2").get("your_fragments") or []), "")

        # ④ to 不是席位 ⇒ 409
        st, b = gm("fragment_drop", to="ghost", fid="f_teeth")
        check("④ 席位不存在 ⇒ 409", st == 409 and b.get("kind") == "rule", f"{st} {b}")
        check("④ 拒绝不留事件", evcount() == n1, f"{n1}→{evcount()}")

        # ⑤ 两枚都不该落地：f_edge 只落一次、f_teeth 一次都没有
        fg = [e for e in get(f"/api/log?gm_token={TOKEN}")["events"] if e["type"] == "fragment_granted"]
        check("⑤ 全程只有 1 笔 fragment_granted（拒绝的一手不留痕）",
              len(fg) == 1 and fg[0]["fid"] == "f_edge" and fg[0]["holder"] == "p1", str(fg))

        # ⑥ warn 合成：node 对不上（只警告不拒绝）＋额度提醒（原本那条）合成一个字串
        st, b = gm("fragment_drop", to="p3", fid="f_ledger")
        w = str(b.get("warn", ""))
        check("⑥ node 对不上只警告不拒绝 ⇒ 200", st == 200, f"{st} {b}")
        check("⑥ warn 带 node 讯息（f_ledger@hyakki_f1 vs p3 现在的位置）",
              "hyakki_f1" in w and "而 p3 現在在" in w, w)
        check("⑥ warn 同时保留额度提醒（test_semic 那条断言的字串没变）",
              "warn" in b and "p3" in w and "額度" in w, w)

        # ⑦ node 对得上（不触发 node 警告）——投一枚无 node 的碎片
        st, b = gm("fragment_drop", to="p1", fid="f_tongs")
        check("⑦ 无 node 绑定的碎片只回额度提醒", st == 200 and "hyakki_f" not in str(b.get("warn", "")),
              f"{st} {b.get('warn')}")
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=10)
        except Exception:  # noqa: BLE001
            srv.kill()
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
