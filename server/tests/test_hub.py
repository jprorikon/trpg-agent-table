#!/usr/bin/env python3
"""**後台單一進程（hub）自測**——一個 process 裝所有桌（設計者 2026-10-07「trpg后台还是做成单一进程吧」）。

    python3 test_hub.py

用**臨時 root**（三張桌：兩張可見＋一張 hidden，都是複本）跑**同一個進程**的 hub：
  H1  `/health`：只列可見的桌（hidden 不載入）
  H2  `/`＝大廳，連結走子路徑
  H3  `/<桌>/`：頁面 ＋ 注入 `window.TRPG_BASE`（**每桌自己的前綴**）
  H4  `/<桌>/api/state`：與**同一份** service 直接算出來的投影逐欄一致（分派真的指到那一張桌）
  H5  `POST /<桌>/api/join`：只動**那一張桌**（別桌席位不變）＝一進程裡的隔離
  H6  `/<桌>/mcp`：tools/list 給的是**那一桌自己**的機制包（`TOOLS` 不再被別桌污染）
  H7  `/<桌>/watch/`：唯讀頁（`__WATCH__`＋`TRPG_BASE=<前綴>/watch`）
  H8  `/<桌>/watch/api/state`：公開投影；`POST`／`/api/me` ⇒ 404（沒有寫入面、不認身分）
  H9  `hidden: true` 的桌**不載入**（404）
  H10 未知桌 ⇒ 404 ＋ 可用桌清單
  H11 壞請求（不是 JSON）⇒ 不帶走別桌（另一桌仍 200）
  H12 管理面：沒 token ⇒ 403；帶 token ⇒ `reload` **無損**（事件數不變、狀態還在）
  H13 `rescan` 收新桌（新目錄）＋移除已刪的
  H14 同進程 kick：`kick.receiver_url="inproc://"` ⇒ 不發 HTTP，payload 直接落到本機接收器
  H16 `/mcp`（多桌共用的 MCP 前門）：由呼叫帶的 session／table 選桌；GM 身分走 `X-Hub-GM-Token`
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo 根
sys.path.insert(0, HERE)
os.environ.setdefault("TRPG_LEGACY_REST", "1")   # 直接跑這支也要能打 `/api/*`（生產已退役 ⇒ 只有測試放行）
import test_support as TS                                              # noqa: E402
from trpg.rules import packs as _packs                     # noqa: E402  （`mk_table(packs=…)` 的參數名會遮住 `packs`）
SRC_DEF, SRC_CFG = TS.fixture_source()   # 有現役桌就用它；沒有退 repo 定義層（夾具不依賴線上桌）
TMP = os.path.join(HERE, "_trial_hub")

OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


def free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def req(url, body=None, method=None, headers=None, timeout=10):
    data = json.dumps(body).encode() if body is not None else None
    hdr = {"Content-Type": "application/json"}
    hdr.update(headers or {})
    r = urllib.request.Request(url, data=data, method=method, headers=hdr)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as exc:                                              # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}"


def mk_table(root, name, *, packs=None, hidden=False, port=None, watch=True):
    home = os.path.join(root, "campaigns", name)
    os.makedirs(home, exist_ok=True)
    # ⚠ WS-B：定義（seats／board／scenario／packs…）住**定義層**，桌目錄裡只有 config／runs ⇒
    #   夾具要從 def_dir 抄定義（不然臨時桌起不來：沒有劇本可載）。
    _def = SRC_DEF
    for f in os.listdir(_def):
        if f in ("data", "runs") or f.startswith("data_") or f.startswith("accounts") or f == "config.json":
            continue
        s, d = os.path.join(_def, f), os.path.join(home, f)
        shutil.copytree(s, d) if os.path.isdir(s) else shutil.copy(s, d)
    _packs.seed_module_packs(_def, home)          # ＋模組層的包（rules.core 住模組 repo）
    cfg = json.loads(json.dumps(SRC_CFG))
    cfg.update({"table_id": name, "port": port or free_port(), "public_prefix": "/" + name,
                "bind": "127.0.0.1", "auth": {"gm_token": "t"},
                # ★ 同進程 kick（不發 HTTP、不用 8972）
                "kick": {"receiver_url": "inproc://", "receiver_token_file": ""}})
    if packs:
        cfg["packs"] = packs
    if hidden:
        cfg["hidden"] = True
    # 2026-10-19：觀戰要桌自己開（`watch.enabled`）——夾具預設開，關的那張另外建
    cfg["watch"] = {"enabled": bool(watch), "port": free_port()}
    json.dump(cfg, open(os.path.join(home, "config.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    return home


def main() -> int:                                                        # noqa: C901
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(os.path.join(TMP, "campaigns"), exist_ok=True)
    base_port = free_port()
    mk_table(TMP, "tbl_a")                                    # 樓塔向（有 board）
    mk_table(TMP, "tbl_b", packs={"enabled": ["d20core"]})    # 抽象桌（沒有機制包）
    mk_table(TMP, "tbl_nowatch", watch=False)                  # 關了觀戰的桌（測閘）
    mk_table(TMP, "tbl_hidden", hidden=True)

    from trpg.api import hub as hub_server

    from trpg.kick import sender as kick_sender

    from trpg.service import table_factory as table_factory

    from trpg.api.web import _busy_tables                                     # noqa: F401（同進程 hook 用得到）

    # 乾淨起（class 級狀態）
    hub_server.Hub.services = {}
    hub_server.Hub.rows = {}
    hub_server.Hub.errors = {}
    hub_server.Hub._stops = {}
    hub_server.Hub._ticks = {}
    hub_server.Hub.load(TMP)
    hub_server.Hub.ensure_token()
    httpd = ThreadingHTTPServer(("127.0.0.1", base_port), hub_server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{base_port}"
    for _ in range(80):
        if req(base + "/health", timeout=2)[0] == 200:
            break
        time.sleep(0.25)

    print("== H1. /health（只列可見的桌）==")
    tdir_discover_port = int((hub_server.Hub.rows.get("tbl_a") or {}).get("port") or 0)
    c, body = req(base + "/health")
    h = json.loads(body)
    ids = [t["id"] for t in h["tables"]]
    check("H1a 可見桌都上線（hidden 沒載入；tbl_nowatch 是關了觀戰那張）",
          c == 200 and ids == ["tbl_a", "tbl_b", "tbl_nowatch"], f"{ids}")
    check("H1b 一進程＝一張 service 一張桌（Hub.services 就這三張）",
          sorted(hub_server.Hub.services) == ["tbl_a", "tbl_b", "tbl_nowatch"])

    def gql(query, variables=None, headers=None):
        _c, _b = req(base + "/graphql", {"query": query, "variables": variables or {}},
                     headers=headers or {})
        try:
            return _c, json.loads(_b)
        except Exception:                                                  # noqa: BLE001
            return _c, {"_raw": _b}

    print("== H2. 後台不送網頁（2026-10-19 拆前後台：只留 /graphql＋/mcp）==")
    c, body = req(base + "/")
    check("H2a `/` ⇒ 410 ＋ 講清楚只有 /graphql／/mcp（頁面在前台容器）",
          c == 410 and "graphql" in body and "前台" in body, f"HTTP {c}")
    c, body = req(base + "/index.html")
    check("H2b `/index.html` 一樣 410（後台沒有 HTML）", c == 410, f"HTTP {c}")
    c, body = req(base + "/tbl_a/")
    check("H2c 舊桌前綴 `/tbl_a/` ⇒ 404（不再 301、也不再送頁）", c == 404, f"HTTP {c}")

    svc_a = hub_server.Hub.services["tbl_a"]
    svc_b = hub_server.Hub.services["tbl_b"]
    _sid_a = str(svc_a.table.session_id())
    _sid_b = str(svc_b.table.session_id())
    _runs = hub_server.Hub.index_runs()
    _run_a = next((k for k, v in _runs.items() if v.get("tid") == "tbl_a" and v.get("current")), "")

    print("== H3. 根 `/graphql`：桌是欄位（一發問兩張桌，各是各的）==")
    c, j = gql('{ a: table(id: "tbl_a"){ id session{id} } b: table(id: "tbl_b"){ id session{id} } }')
    _d = j.get("data") or {}
    check("H3a 一發查兩張桌，各自回自己的 id／session（不互串）",
          c == 200 and (_d.get("a") or {}).get("id") == "tbl_a"
          and ((_d.get("a") or {}).get("session") or {}).get("id") == _sid_a
          and (_d.get("b") or {}).get("id") == "tbl_b"
          and ((_d.get("b") or {}).get("session") or {}).get("id") == _sid_b,
          json.dumps(_d, ensure_ascii=False)[:120])
    c, j = gql('{ table(id: "no_such_table"){ id } }')
    check("H3b 不存在的桌 ⇒ 明確的錯（不是靜默 null）",
          bool(j.get("errors")) and "no_such_table" in json.dumps(j, ensure_ascii=False),
          json.dumps(j, ensure_ascii=False)[:110])

    print("== H4. `view.raw` 與同一份 service 直接算的投影逐欄一致 ==")
    want = svc_a.table.view(seat=None, gm=False)
    c, j = gql('query($t: ID!, $s: ID!){ table(id:$t){ view(session:$s){ raw } } }',
               {"t": "tbl_a", "s": _sid_a})
    got = ((((j.get("data") or {}).get("table") or {}).get("view") or {}).get("raw")) or {}
    check("H4a 逐欄一致（席次／回合／棋盤）",
          c == 200 and got.get("seats") == want.get("seats") and got.get("turn") == want.get("turn")
          and bool(got.get("board")) == bool(want.get("board")), f"HTTP {c}")

    print("== H5. 一進程裡的隔離：寫一張桌不影響別張 ==")
    na0 = len(svc_a.table.state["seats"])
    nb0 = len(svc_b.table.state["seats"])
    c, j = gql('mutation($t: ID!, $s: ID!, $seat: ID, $n: String!, $k: JoinKind){'
               ' join(tableId:$t, session:$s, seat:$seat, name:$n, kind:$k) }',
               {"t": "tbl_a", "s": _sid_a, "seat": "p1", "n": "隔離測試", "k": "human"})
    na1 = len(svc_a.table.state["seats"])
    nb1 = len(svc_b.table.state["seats"])
    check("H5a join 只動那一張桌（別桌席位不變）",
          c == 200 and not j.get("errors") and na1 == na0 + 1 and nb1 == nb0,
          f"a {na0}→{na1} b {nb0}→{nb1} {str(j.get('errors'))[:60]}")

    print("== H6. `/mcp` tools/list ＝ 標頭 X-Table-Id 指到那一桌的目錄 ==")
    def mcp_tools(tid):
        c2, b2 = req(base + "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                     headers={"Accept": "application/json, text/event-stream",
                              "X-Table-Id": tid})
        for ln in b2.splitlines():
            ln = ln.strip()
            if ln.startswith("{"):
                try:
                    return json.loads(ln)
                except Exception:                                         # noqa: BLE001
                    continue
        try:
            return json.loads(b2)
        except Exception:                                                 # noqa: BLE001
            return {}
    ta = json.dumps(mcp_tools("tbl_a"), ensure_ascii=False)
    tb = json.dumps(mcp_tools("tbl_b"), ensure_ascii=False)
    check("H6a A 桌（有棋盤＋機制包）的描述含棋盤字樣", "棋盘制" in ta or "机制包" in ta, ta[:110])
    check("H6b B 桌（抽象桌、只開 d20core）**沒有** A 桌的棋盤字樣（TOOLS 不再互漏）",
          "棋盘制" not in tb, tb[:110])

    print("== H7. 匿名只讀：桌級 watch 端點已經沒有了，讀走根 /graphql ==")
    if _run_a:
        c, _b = req(base + "/watchtables/" + _run_a)
        check("H7a `/watchtables/<run>` ⇒ 404（觀戰頁在前台，後台不送頁）", c == 404, f"HTTP {c}")
        c, _b = req(base + "/tables/" + _run_a + "/graphql",
                    {"query": "{ tables { id } }"})
        check("H7b `/tables/<run>/graphql` ⇒ 404（桌級端點收掉，只剩根 /graphql）",
              c == 404, f"HTTP {c}")
    c, _b = req(base + "/tbl_a/api/state")
    check("H7c 桌級 REST（`/tbl_a/api/state`）⇒ 404（連路都沒有了，不是 410 退役）",
          c == 404, f"HTTP {c}")

    print("== H8. 匿名讀得到、寫不進去（按 query 做訪問控制）==")
    c, j = gql('query($t: ID!, $s: ID!){ table(id:$t){ view(session:$s){ raw } } }',
               {"t": "tbl_a", "s": _sid_a})
    check("H8a 匿名 query 讀得到公開投影（沒有 you）",
          c == 200 and not j.get("errors")
          and "you" not in ((((j.get("data") or {}).get("table") or {}).get("view") or {}).get("raw") or {}),
          json.dumps(j.get("errors") or "ok", ensure_ascii=False)[:100])
    c, j = gql('mutation($t: ID!, $s: ID!){ say(tableId:$t, session:$s, text:"匿名寫入", seat:"p1") }',
               {"t": "tbl_a", "s": _sid_a})
    check("H8b 沒宣告 require_account 的桌：匿名寫入仍是舊行為（席位制；宣告的桌才擋，見 test_graphql G22）",
          c == 200 and not j.get("errors"), json.dumps(j.get("errors") or "ok", ensure_ascii=False)[:100])

    print("== H9/H10. hidden／未知 ＝ 明確的錯（也不在大廳）==")
    c, j = gql('{ table(id: "tbl_hidden"){ id } }')
    check("H9 `hidden: true` 的桌不載入 ⇒ 查不到", bool(j.get("errors")), json.dumps(j, ensure_ascii=False)[:100])
    c, j = gql('{ tables { id } }')
    _ids = [t["id"] for t in (((j.get("data") or {}).get("tables")) or [])]
    check("H10 大廳清單只有可見桌（hidden 不在裡面）",
          "tbl_hidden" not in _ids and "tbl_a" in _ids, f"{_ids}")

    print("== H11. 壞請求不帶走別桌 ==")
    r = urllib.request.Request(base + "/graphql", data=b"{not-json",
                               headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(r, timeout=8)
    except Exception:                                                     # noqa: BLE001
        pass
    c, j = gql('{ table(id: "tbl_b"){ id } }')
    check("H11 一發壞 JSON 之後，別桌照常答",
          c == 200 and ((j.get("data") or {}).get("table") or {}).get("id") == "tbl_b", f"HTTP {c}")

    print("== H12. 管理面（token）＋ reload 無損 ==")
    c, _b = req(base + "/__hub/reload", {"table": "tbl_a"}, method="POST")
    check("H12a 沒 token ⇒ 403", c == 403, f"HTTP {c}")
    before = len(svc_a.table.events)
    nseats = len(svc_a.table.state["seats"])
    c, body = req(base + "/__hub/reload", {"table": "tbl_a"}, method="POST",
                  headers={"X-Hub-Token": hub_server.Hub.admin_token})
    j = json.loads(body) if c == 200 else {}
    after = len(hub_server.Hub.services["tbl_a"].table.events)
    nseats2 = len(hub_server.Hub.services["tbl_a"].table.state["seats"])
    c2, _j2 = gql('{ table(id: "tbl_a"){ id } }')
    check("H12b 帶 token ⇒ 只重建那一張桌、**事件數與席位都不變**（從 events.jsonl 重播）",
          c == 200 and j.get("lossless") and before == after and nseats == nseats2 and c2 == 200,
          f"{before}→{after} seats {nseats}→{nseats2}")

    print("== H13. rescan（收新桌／移除已刪）==")
    mk_table(TMP, "tbl_new")
    c, body = req(base + "/__hub/rescan", {}, method="POST",
                  headers={"X-Hub-Token": hub_server.Hub.admin_token})
    j = json.loads(body) if c == 200 else {}
    c2, j2 = gql('{ table(id: "tbl_new"){ id } }')
    check("H13a 新目錄 ⇒ rescan 後查得到",
          c == 200 and "tbl_new" in (j.get("added") or [])
          and ((j2.get("data") or {}).get("table") or {}).get("id") == "tbl_new",
          f"added={j.get('added')}")
    shutil.rmtree(os.path.join(TMP, "campaigns", "tbl_new"), ignore_errors=True)
    c, body = req(base + "/__hub/rescan", {}, method="POST",
                  headers={"X-Hub-Token": hub_server.Hub.admin_token})
    j = json.loads(body) if c == 200 else {}
    c2, j2 = gql('{ table(id: "tbl_new"){ id } }')
    check("H13b 目錄刪掉 ⇒ rescan 後查不到（還在一起的桌不受影響）",
          "tbl_new" in (j.get("removed") or []) and bool(j2.get("errors"))
          and not gql('{ table(id: "tbl_a"){ id } }')[1].get("errors"),
          f"removed={j.get('removed')}")

    print("== H14. 同進程 kick（inproc://，不發 HTTP）==")
    class _StubKick:
        got = []
        def handle_kick(self, payload):
            _StubKick.got.append(payload)
            return {"ok": True, "session": "trpg-" + payload.get("table_id") + "-gm", "stub": True}
    old = kick_sender.LOCAL_RECEIVER
    kick_sender.LOCAL_RECEIVER = _StubKick()
    try:
        res = kick_sender.send_kick_to_gm("tbl_a", "p1", "測試文字", "私訊", 99,
                                          url="inproc://", port=base_port, home=TMP)
    finally:
        kick_sender.LOCAL_RECEIVER = old
    check("H14a inproc:// ⇒ sent／url=inproc，且**沒有**任何 HTTP 目標",
          res.get("sent") is True and res.get("url") == "inproc", json.dumps(res, ensure_ascii=False)[:130])
    got = _StubKick.got[0] if _StubKick.got else {}
    check("H14b 接收器收到的 payload 帶對的桌／席位／session",
          got.get("table_id") == "tbl_a" and got.get("seat") == "p1"
          and got.get("session") == "" and got.get("session_name") == "trpg-tbl_a-gm",
          json.dumps(got, ensure_ascii=False)[:130])
    # ★ session 規範（設計者 2026-10-07）：帶了副本 id ⇒ 會話名帶副本 id（一副本一條會話）
    _StubKick.got.clear()
    try:
        kick_sender.LOCAL_RECEIVER = _StubKick()
        kick_sender.send_kick_to_gm("tbl_a", "p1", "x", "私訊", 101,
                                    url="inproc://", port=base_port, home=TMP, session="rABC123")
    finally:
        kick_sender.LOCAL_RECEIVER = old
    got2 = _StubKick.got[0] if _StubKick.got else {}
    check("H14b2 帶 session ⇒ `session`＝副本 id、`session_name` 帶副本 id（一副本一會話）",
          got2.get("session") == "rABC123" and got2.get("session_name") == "trpg-tbl_a-rABC123-gm",
          json.dumps(got2, ensure_ascii=False)[:130])
    kick_sender.LOCAL_RECEIVER = None
    res2 = kick_sender.send_kick_to_gm("tbl_a", "p1", "x", "私訊", 100, url="inproc://")
    check("H14c 沒裝本機接收器 ⇒ **明確失敗**（不是靜默）", res2.get("sent") is False,
          json.dumps(res2, ensure_ascii=False)[:110])

    print("== H15. GM 喚醒的 prompt 要**看得到桌**（hub 模式：桌況查詢走 hub 的子路徑）==")
    class _CapKick:
        got = None
        def handle_kick(self, payload):
            _CapKick.got = payload
            return {"ok": True, "stub": True}
    old2 = kick_sender.LOCAL_RECEIVER
    kick_sender.LOCAL_RECEIVER = _CapKick()
    try:
        r15 = kick_sender.send_kick_to_gm("tbl_a", "p1", "（H15 測試）", "私訊", 77, url="inproc://",
                                          base=f"http://127.0.0.1:{base_port}",
                                          gm_token="t", home=TMP)
    finally:
        kick_sender.LOCAL_RECEIVER = old2
    _pr = (_CapKick.got or {}).get("prompt") or ""
    check("H15a 桌況查得到（prompt 有席位／景，不是「查不到」的 fallback）",
          r15.get("sent") is True and "席位：" in _pr and "查不到" not in _pr, _pr[:150])
    check("H15b prompt 不含桌自己的死埠（hub 模式下那個埠沒在聽）",
          str(tdir_discover_port) not in _pr, f"port={tdir_discover_port}")

    print("== H15. `/mcp`：多桌共用的 MCP 前門（owner 2026-10-08 選 (b)）==")

    def _mcp_call(body, headers=None):
        _c, _b = req(f"{base}/mcp", body,
                     headers={"Accept": "application/json, text/event-stream", **(headers or {})})
        _d = {}
        for _ln in _b.splitlines():
            _ln = _ln.strip()
            if _ln.startswith("{"):
                try:
                    _d = json.loads(_ln)
                    break
                except Exception:                                          # noqa: BLE001
                    continue
        if not _d:
            try:
                _d = json.loads(_b)
            except Exception:                                              # noqa: BLE001
                _d = {"_raw": _b[:200]}
        return _c, _d

    def _tool_json(_d):
        try:
            return json.loads((((_d.get("result") or {}).get("content") or [{}])[0].get("text") or "{}"))
        except Exception:                                                  # noqa: BLE001
            return {}

    _gm_tok = hub_server.Hub.ensure_gm_token()
    _c, _d = _mcp_call({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    _names = sorted(t.get("name") for t in ((_d.get("result") or {}).get("tools") or []))
    check("H16a `/mcp` 的 tools/list 通（還沒指定桌也答得出來——用唯一在線那張桌的目錄）",
          _c == 200 and _names == ["table_act", "table_gm", "table_join", "table_say",
                                   "table_secret", "table_tell", "table_view"], f"HTTP {_c} {_names}")
    _sid_a = str(svc_a.table.session_id())
    _svc_b = hub_server.Hub.services["tbl_b"]
    _sid_b = str(_svc_b.table.session_id())

    # `table_view` 的 `seat` 是必填（GM 一律填 "gm"，憑證走連線標頭）⇒ 三發都帶 gm 標頭。
    _hdr_gm = {"X-Hub-GM-Token": _gm_tok}

    def _view_via_mcp(_sess=None, _table=None):
        _args = {"seat": "gm"}
        if _sess:
            _args["session"] = _sess
        if _table:
            _args["table"] = _table
        return _mcp_call({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                          "params": {"name": "table_view", "arguments": _args}}, headers=_hdr_gm)

    _c, _d = _view_via_mcp(_sid_a)
    _v = _tool_json(_d)
    check("H16b 帶 A 桌的 session ⇒ 落到 A 桌（回的是 A 的投影）",
          _c == 200 and ((_v.get("table") or {}).get("table_id") == "tbl_a"), f"HTTP {_c} {str(_v)[:90]}")
    _c, _d = _view_via_mcp(_sid_b)
    _v = _tool_json(_d)
    check("H16b2 帶 B 桌的 session ⇒ 落到 B 桌", _c == 200 and ((_v.get("table") or {}).get("table_id") == "tbl_b"),
          f"HTTP {_c} {str(_v)[:90]}")
    _c, _d = _view_via_mcp("nope-not-a-session")
    check("H16c 認不出來的 session ⇒ 400 ＋ 講清楚（不是靜默挑一張桌）",
          _c == 400 and "session" in json.dumps(_d, ensure_ascii=False), f"HTTP {_c} {str(_d)[:120]}")
    _c, _d = _view_via_mcp(_table="tbl_b")
    _v = _tool_json(_d)
    check("H16d `arguments.table` 也能指定（不必帶 session）",
          _c == 200 and ((_v.get("table") or {}).get("table_id") == "tbl_b"), f"HTTP {_c} {str(_v)[:90]}")
    _c, _d = _view_via_mcp(_table="沒有這張")
    check("H16e 指定的桌不存在 ⇒ 明講（400）", _c == 400 and "不存在的桌" in json.dumps(_d, ensure_ascii=False),
          f"HTTP {_c} {str(_d)[:120]}")
    check("H16f 沒帶 X-Hub-GM-Token ⇒ 不是 GM（帶錯的會被 403）",
          _mcp_call({"jsonrpc": "2.0", "id": 5, "method": "tools/list"},
                    headers={"X-Hub-GM-Token": "wrong"})[0] == 403)
    _c, _d = _mcp_call({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                        "params": {"name": "table_say",
                                   "arguments": {"session": _sid_a, "seat": "gm", "text": "前門測試"}}},
                       headers={"X-Hub-GM-Token": _gm_tok})
    _s = _tool_json(_d)
    _said = bool(_s.get("said") or _s.get("seq") or _s.get("ok"))
    check("H16g 帶 X-Hub-GM-Token ⇒ 在 A 桌以 GM 身分發言（多桌前門的 GM 憑證）", _c == 200 and _said,
          f"HTTP {_c} {str(_s)[:110]}")
    _c2, _d2 = _mcp_call({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                          "params": {"name": "table_say",
                                     "arguments": {"session": _sid_a, "seat": "gm", "text": "沒憑證"}}})
    check("H16h 不帶憑證 ⇒ 同一發被拒（GM 面沒有開口）",
          _c2 != 200 or "error" in json.dumps(_d2, ensure_ascii=False).lower(),
          f"HTTP {_c2} {str(_d2)[:110]}")

    print("== H20. 正規 URL：/tables/<run>、/watchtables/<run>（2026-10-19）==")
    _runs = hub_server.Hub.index_runs()
    _cur = {v["tid"]: k for k, v in _runs.items() if v.get("current")}
    _run_a = _cur.get("tbl_a") or ""
    _run_b = _cur.get("tbl_b") or ""
    check("H20a run 索引從各桌 home/runs 掃出來（fixture 的層級也吃）",
          bool(_run_a) and bool(_run_b), f"{_runs}")
    # ★ 2026-10-19 拆前後台：桌級子路徑**全部收掉**（頁面在前台容器、讀寫走根 /graphql）。
    #   這裡逐條釘住「404」，免得有人把舊路徑補回來卻以為還在服務。
    _gone = [("/tables/" + _run_a if _run_a else "/tables/x"),
             ("/watchtables/" + _run_a if _run_a else "/watchtables/x"),
             ("/tables/" + (_run_a or "x") + "/graphql"),
             ("/watchtables/" + (_run_a or "x") + "/graphql"),
             ("/tables/" + (_run_a or "x") + "/mcp"),
             "/tables/", "/tables/no-such-run", "/tbl_a/api/state", "/tbl_a/watch/"]
    _codes = []
    for _u in _gone:
        _codes.append(req(base + _u)[0])
    check("H20b 桌級路徑一律 404（/tables/<run>、/watchtables/<run>、…/graphql、…/mcp、/<桌>/api/*）",
          all(x == 404 for x in _codes), f"{list(zip(_gone, _codes))}")

    print("== H21. 後台只剩四個面（/graphql、/mcp、/assets、/health）==")
    _ok_health = req(base + "/health")[0] == 200
    _ok_gql = req(base + "/graphql", {"query": "{ tables { id } }"}, method="POST")[0] == 200
    _ok_mcp_get = req(base + "/mcp")[0] == 405          # MCP 只吃 POST
    _ok_assets = req(base + "/assets/nope/index.json")[0] == 404
    check("H21a /health 200、/graphql 吃 POST、/mcp 只吃 POST（GET 405）、/assets 有回應",
          _ok_health and _ok_gql and _ok_mcp_get and _ok_assets,
          f"health={_ok_health} gql={_ok_gql} mcpGET={_ok_mcp_get} assets={_ok_assets}")

    print("== H22. /health 帶 run 與兩種前綴 ==")
    c, body = req(base + "/health")
    h2 = json.loads(body) if c == 200 else {}
    _rows = {t["id"]: t for t in (h2.get("tables") or [])}
    _ra = _rows.get("tbl_a") or {}
    check("H22a 每張桌都有 run／prefix／watch_prefix／legacy_prefix",
          bool(_ra.get("run")) and str(_ra.get("prefix", "")).startswith("/tables/")
          and str(_ra.get("watch_prefix", "")).startswith("/watchtables/")
          and str(_ra.get("legacy_prefix", "")).startswith("/tbl_a"),
          f"{_ra.get('run')} {_ra.get('prefix')} {_ra.get('watch_prefix')}")

    print("== H24. 席次只算玩家（GM／NPC 不算；2026-10-19）==")
    c, body = req(base + "/health")
    h3 = json.loads(body) if c == 200 else {}
    _row = next((t for t in (h3.get("tables") or []) if t["id"] == "tbl_a"), {})
    _pl, _all = _row.get("seats"), _row.get("seats_total")
    check("H24a /health 分開報：seats（玩家）與 seats_total（含 GM／NPC）",
          isinstance(_pl, int) and isinstance(_all, int) and _pl <= _all, f"{_pl}/{_all}")
    _svc = hub_server.Hub.services["tbl_a"]
    _kinds = [str((b or {}).get("kind") or "") for b in (_svc.table.state.get("seats") or {}).values()]
    check("H24b seats 只數 human／agent，且真的有 gm／npc 被排除",
          _pl == sum(1 for k in _kinds if k in ("human", "agent")) and _all == len(_kinds)
          and any(k in ("gm", "npc") for k in _kinds), f"{_kinds}")

    print("== H23. 觀戰開關（watch.enabled）：現在管的是「匿名讀得到嗎」，不再有觀戰 URL ==")
    def _watchable(tid):
        _c, _j = gql('{ tables { id watchable } }')
        return next((t.get("watchable") for t in (((_j.get("data") or {}).get("tables")) or [])
                     if t.get("id") == tid), None)

    _nw0 = _watchable("tbl_nowatch")
    ok_ = hub_server.Hub.set_watchable("tbl_nowatch", True)
    _nw1 = _watchable("tbl_nowatch")
    ok2 = hub_server.Hub.set_watchable("tbl_nowatch", False)
    _nw2 = _watchable("tbl_nowatch")
    check("H23a 旗標在 GraphQL `tables{watchable}` 上即時生效（false→true→false，不用重啟）",
          _nw0 is False and ok_ and _nw1 is True and ok2 and _nw2 is False,
          f"{_nw0} → {_nw1} → {_nw2}")

    # ★ 2026-10-20（設計者：「所有的都是局」）：開關是局級——改這一局不影響別局。
    from trpg.service import run_watch as _rw                                     # noqa: PLC0415
    _h23 = str(hub_server.Hub.services["tbl_nowatch"].home)
    _cur23 = str(hub_server.Hub.services["tbl_nowatch"].table.session_id() or "")
    _rw.set_enabled(_h23, "other-run-23", True, by="test")
    hub_server.Hub.set_watchable("tbl_nowatch", False, session=_cur23)
    check("H23b 只改這一局：本局=false、別局=true",
          _rw.read(_h23, _cur23) is False and _rw.read(_h23, "other-run-23") is True,
          (_rw.read(_h23, _cur23), _rw.read(_h23, "other-run-23")))

    httpd.shutdown()
    shutil.rmtree(TMP, ignore_errors=True)
    # ⚠ wiring 閘：前台以 `python hub_server.py` 起時，`__main__` 必須註冊成 `hub_server`——
    #   否則 `web_api._busy_tables`（跨桌互斥：同帳號同角色不能同時兩張活桌）會 import 到
    #   **另一份空 Hub**、靜默失效（實測踩到：第二張桌把同一角色放行了）。
    _hs = TS.source_text("hub_server.py")
    check("H9 前台把 `__main__` 註冊成 `hub_server`（跨桌互斥才看得到同一份 Hub）",
          'sys.modules.setdefault("hub_server", _hub)' in _hs,
          "hub_server.py 少了這一行")

    print(f"\n== 結果：{len(OK)} PASS / {len(FAIL)} FAIL ==")
    for x in FAIL:
        print("  FAIL " + x)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
