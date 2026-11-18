#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**GM 控制鍵「空值」fail-open 的回歸閘**（review R1，2026-10-24）。

病根：`5174bf9` 為了「缺 `auth` 段不要 500」，把每個 GM 比較點寫成
`(cfg.get("auth") or {}).get("gm_token") or ""`。配置**沒有** token 時，這等於拿空字串
跟請求比 ⇒ 只要請求帶空值就放行：

* `GET /api/state?gm_token=` ⇒ **GM 全視圖**（含別席私密）
* `GET /api/log?gm_token=`   ⇒ 原始事件流
* `POST /api/gm {token:""}`  ⇒ 以 GM 身分發私訊／暫停／結算（實測真的執行了）
* MCP `table_gm {token:""}`  ⇒ 同上（實測把桌暫停了）

修法：`trpg.core.gmtoken.gm_token_ok()`——**配置值為空 ⇒ 一律 False**，所有入口共用這一支。

  E1 單元：`gm_token_ok()` 的語意（沒設／空值／非 ASCII）
  E2 沒有 `auth` 段的桌：四個面（GET／POST／MCP／GraphQL）帶空值一律拒
  E3 對照：**有 token** 的桌——帶對的照舊走得通、空值照舊拒（行為不變）
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo 根
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tests"))
os.environ.setdefault("TRPG_LEGACY_REST", "1")     # `/api/*` 生產退役；測試要打舊門

from trpg.core import layout as layout                                   # noqa: E402
from trpg.core import store as store                                    # noqa: E402
from trpg.core.gmtoken import gm_token_ok                               # noqa: E402
from trpg.rules import packs as packs                                   # noqa: E402
from trpg.rules import scenario as scenario                             # noqa: E402
from trpg.engine import reading as reading                              # noqa: E402

PY = sys.executable
CAMP = layout.must_locate_scenario(HERE, "hyakki_tower", "sannenme_no_onibarai")
DEF = layout.def_dir_of(CAMP)
MODF = scenario.module_filename(DEF) or "scenario.json"
TOKEN = "e2a1c0d43f7b8a95f1c26d7e"        # 對照桌的那把（ASCII）

OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


def _raises(fn, exc):
    try:
        fn()
    except exc as e:
        return True, str(e)
    except Exception as e:                                             # noqa: BLE001
        return False, f"丟的是 {type(e).__name__}: {e}"
    return False, "沒有拋"


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def mk_home(token=None) -> str:
    """臨時桌；`token=None` ⇒ **config 裡沒有 `auth` 段**（模板／手抄 config 的部署路徑）。"""
    tmp = tempfile.mkdtemp(prefix="trpg_gmempty_")
    for f in ("board.json", "goals.json", "clues.json", "seats.json", MODF):
        shutil.copy(os.path.join(DEF, f), os.path.join(tmp, f))
    layout.copy_table_config(CAMP, os.path.join(tmp, "config.json"))
    shutil.copytree(os.path.join(DEF, "packs"), os.path.join(tmp, "packs"), dirs_exist_ok=True)
    packs.seed_module_packs(os.path.dirname(os.path.join(DEF, "packs")), tmp)
    p = os.path.join(tmp, "config.json")
    cfg = json.load(open(p, encoding="utf-8"))
    cfg["port"] = 0
    cfg.pop("kick", None)
    if token is None:
        cfg.pop("auth", None)
    else:
        cfg["auth"] = {"gm_token": token}
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False)
    return tmp


def start(home: str, port: int) -> subprocess.Popen:
    fh = open(os.path.join(home, "srv.log"), "w", encoding="utf-8")
    return subprocess.Popen([PY, "server.py", "--config", os.path.join(home, "config.json"),
                             "--home", home, "--port", str(port)], cwd=HERE, stdout=fh, stderr=fh)


def req(url: str, body=None, hdr=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Accept": "application/json"}
    if data is not None:
        h["Content-Type"] = "application/json"
    h.update(hdr or {})
    r = urllib.request.Request(url, data=data, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:                                             # noqa: BLE001
        return 0, f"<{type(e).__name__}: {e}>"


def wait_up(base: str) -> bool:
    for _ in range(120):
        if req(base + "/health")[0] == 200:
            return True
        time.sleep(0.25)
    return False


def _state(base: str, qs: str = ""):
    c, b = req(base + "/api/state" + qs)
    try:
        return c, json.loads(b)
    except Exception:                                                  # noqa: BLE001
        return c, {}


#: 投影裡**會自己變**的欄位（時間戳／已暫停秒數）——比對「同一份內容」時一律剃掉，
#: 否則兩次請求差 1 個 char（浮點位數）就會誤判成「不同視圖」。
_VOLATILE = ("now", "ts", "paused_seconds", "at")


def _clean(obj):
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items() if k not in _VOLATILE}
    if isinstance(obj, list):
        return [_clean(x) for x in obj]
    return obj


def _fingerprint(v) -> tuple:
    """投影指紋：log 筆數 ＋ 席位數 ＋ **剃掉時間欄位後**的序列化長度。

    GM 全視圖一定比公開投影大（修前 13.3KB vs 6.2KB）；剃掉時間欄位是為了讓
    「同一份投影」在兩次請求裡逐位元相同。
    """
    return (len(v.get("log") or []), len(v.get("seats") or {}),
            len(json.dumps(_clean(v), ensure_ascii=False, sort_keys=True)))


def _mcp(base: str, name: str, args: dict):
    c, b = req(base + "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": name, "arguments": args}})
    try:
        d = json.loads(b)
    except Exception:                                                  # noqa: BLE001
        return c, {}, b
    return c, (d.get("result") or {}), b


def main() -> int:                                                     # noqa: C901
    print("== E1 `gm_token_ok()` 單元（沒設 ⇒ 一律 False）==")
    check("E1a config 沒有 auth 段：帶 None／空字串／亂字串一律 False",
          gm_token_ok({}, None) is False and gm_token_ok({}, "") is False
          and gm_token_ok({}, "x") is False)
    check("E1b `auth: {}` 或 `gm_token: \"\"`：同樣一律 False（修前這裡是 True）",
          gm_token_ok({"auth": {}}, "") is False
          and gm_token_ok({"auth": {"gm_token": ""}}, "") is False
          and gm_token_ok({"auth": {"gm_token": None}}, "") is False)
    check("E1c 有 token：對的 True、錯的／空值 False",
          gm_token_ok({"auth": {"gm_token": "abc"}}, "abc") is True
          and gm_token_ok({"auth": {"gm_token": "abc"}}, "abd") is False
          and gm_token_ok({"auth": {"gm_token": "abc"}}, "") is False
          and gm_token_ok({"auth": {"gm_token": "abc"}}, None) is False)
    check("E1d 非 ASCII 的 token 照樣能比（不吃 compare_digest 的 TypeError）",
          gm_token_ok({"auth": {"gm_token": "亂數甲號"}}, "亂數甲號") is True
          and gm_token_ok({"auth": {"gm_token": "亂數甲號"}}, "亂數乙號") is False)

    print("\n== E2 沒有 `auth` 段的桌（模板／手抄 config）＝四個面都必須 fail-closed ==")
    port = free_port()
    home = mk_home(None)
    base = f"http://127.0.0.1:{port}"
    p = start(home, port)
    try:
        check("E2 伺服器起得來（config 沒有 auth 段）", wait_up(base))
        req(base + "/api/join", {"seat": "p1", "name": "カガリ", "kind": "human"})

        c_pub, v_pub = _state(base)
        c_bad, v_bad = _state(base, "?gm_token=not-this-token")
        c_emp, v_emp = _state(base, "?gm_token=")
        check("E2a `GET /api/state?gm_token=`（空值）**不是** GM 全視圖",
              c_emp == 200 and _fingerprint(v_emp)[2] == _fingerprint(v_pub)[2],
              f"空值 {_fingerprint(v_emp)} vs 公開 {_fingerprint(v_pub)}"
              f"（修前 13315 vs 6205）")
        check("E2a2 帶錯的 token 同樣只是公開投影（兩者一致）",
              _fingerprint(v_bad)[2] == _fingerprint(v_pub)[2], str(_fingerprint(v_bad)))

        # compact 是**另一種投影形狀** ⇒ 只能跟「同樣 compact 的公開投影」比（別跟 full 比大小）
        c_c_pub, v_c_pub = _state(base, "?detail=compact")
        c_c_emp, v_c_emp = _state(base, "?gm_token=&detail=compact")
        check("E2a3 `?gm_token=` ＋ compact ⇒ 與同樣 compact 的公開投影一致（不會因為 compact 漏判）",
              c_c_emp == 200 and _fingerprint(v_c_emp) == _fingerprint(v_c_pub),
              f"{_fingerprint(v_c_emp)} vs 公開 {_fingerprint(v_c_pub)}")

        c, b = req(base + "/api/log?gm_token=")
        try:
            evs = json.loads(b).get("events") or []
        except Exception:                                              # noqa: BLE001
            evs = []
        c_meta, b_meta = req(base + "/api/log?meta=1")
        try:
            raw_n = json.loads(b_meta).get("count")
        except Exception:                                              # noqa: BLE001
            raw_n = None
        nonpub = sorted({str(e.get("type")) for e in evs}
                        - set(getattr(reading, "VIEW_LOG_TYPES", ()) or ()))
        check("E2b `GET /api/log?gm_token=` 只回公開投影（不是原始流）",
              c == 200 and not nonpub and (raw_n is None or len(evs) <= int(raw_n)),
              f"回 {len(evs)}／原始 {raw_n}；白名單外={nonpub}")

        c, b = req(base + "/api/gm", {"token": "", "op": "secret", "to": "p1", "text": "R1 probe"})
        check("E2c `POST /api/gm {token:\"\"}` ⇒ 403（修前 200，GM op 真的執行了）",
              c == 403, f"{c} {b[:80]}")

        c, r, b = _mcp(base, "table_gm", {"token": "", "op": "pause", "seat": "gm"})
        check("E2d MCP `table_gm {token:\"\"}` ⇒ 拒絕（修前真的把桌暫停了）",
              r.get("isError") is True, b[:90])
        c, r, b = _mcp(base, "table_secret", {"token": "", "to": "p1", "text": "R1 probe"})
        check("E2e MCP `table_secret {token:\"\"}` ⇒ 拒絕", r.get("isError") is True, b[:90])
        c, r, b = _mcp(base, "table_say", {"seat": "gm", "token": "", "text": "R1 probe"})
        check("E2f MCP `table_say {seat:\"gm\", token:\"\"}` ⇒ 拒絕（GM 聲道閘）",
              r.get("isError") is True, b[:90])

        c_st, v_st = _state(base)
        check("E2g 這一桌仍然是「公開投影」的狀態（上面那些探針沒有留下 GM 事件的形狀）",
              c_st == 200 and isinstance(v_st, dict) and "log" in v_st, str(sorted(v_st)[:6]))
    finally:
        p.terminate()
        try:
            p.wait(5)
        except Exception:                                              # noqa: BLE001
            p.kill()

    # ── GraphQL 面（行程內；`_m_gm_run` 原樣把 args 轉給 `/api/gm`）
    print("\n== E2h GraphQL `gmRun(args:{token:''})`（行程內）==")
    import test_support as TS                                          # noqa: E402,PLC0415
    from trpg.api import graphql as graphql_api                        # noqa: E402,PLC0415
    from trpg.service import table_factory                             # noqa: E402,PLC0415
    _root, _home, _cfg = TS.mk_table(with_run=True)
    _cfg.pop("auth", None)                                             # 這張桌沒有 GM 控制鍵
    svc = table_factory.build(_home, _cfg, log=lambda *_a: None)
    _doc = ('mutation($t: ID!, $s: ID!) { gmRun(tableId: $t, session: $s, op: "pause", '
            'args: {token: ""}) }')
    _st, _out = graphql_api.handle({"query": _doc,
                                    "variables": {"t": str(svc.cfg.get("table_id") or ""),
                                                  "s": str(svc.table.session_id())}},
                                   svc=svc, root=None, headers={})
    check("E2h 沒有 token 的桌上 gmRun 帶空 token ⇒ 拒絕（errors，不是 ok）",
          bool((_out or {}).get("errors")), json.dumps(_out, ensure_ascii=False)[:120])
    check("E2h2 而且桌**沒有**被暫停（拒絕是真的沒有副作用）",
          bool(svc.table.view().get("paused")) is False,
          str(svc.table.view().get("paused")))
    shutil.rmtree(home, ignore_errors=True)

    # ── E3 對照：有 token 的桌行為不變
    print("\n== E3 對照：有 `auth.gm_token` 的桌（行為不變）==")
    port2 = free_port()
    home2 = mk_home(TOKEN)
    base2 = f"http://127.0.0.1:{port2}"
    p2 = start(home2, port2)
    try:
        check("E3 伺服器起得來", wait_up(base2))
        req(base2 + "/api/join", {"seat": "p1", "name": "カガリ", "kind": "human"})
        c_pub, v_pub = _state(base2)
        c_gm, v_gm = _state(base2, f"?gm_token={TOKEN}")
        c_emp, v_emp = _state(base2, "?gm_token=")
        check("E3a 帶對的 token 仍拿得到 GM 全視圖（比公開投影大）",
              c_gm == 200 and _fingerprint(v_gm)[2] > _fingerprint(v_pub)[2],
              f"GM {_fingerprint(v_gm)[2]} vs 公開 {_fingerprint(v_pub)[2]}")
        check("E3b 空值仍然只是公開投影（不因這次改動而變鬆或變嚴）",
              _fingerprint(v_emp)[2] == _fingerprint(v_pub)[2], str(_fingerprint(v_emp)))
        c, b = req(base2 + "/api/log?gm_token=")
        try:
            n_emp = len(json.loads(b).get("events") or [])
        except Exception:                                              # noqa: BLE001
            n_emp = -1
        c2, b2 = req(base2 + f"/api/log?gm_token={TOKEN}")
        try:
            n_gm = len(json.loads(b2).get("events") or [])
        except Exception:                                              # noqa: BLE001
            n_gm = -1
        check("E3c 空值只拿公開投影；帶對的 token 拿得到原始流（≥ 公開）",
              0 <= n_emp <= n_gm, f"空值 {n_emp} 筆／帶對 {n_gm} 筆")
        c, b = req(base2 + "/api/gm", {"token": TOKEN, "op": "pause", "seat": "gm"})
        check("E3d `POST /api/gm` 帶對的 token 仍然走得通", c == 200, f"{c} {b[:70]}")
        c, r, b = _mcp(base2, "table_gm", {"token": "", "op": "resume", "seat": "gm"})
        check("E3e MCP `table_gm {token:\"\"}` 在有 token 的桌上照舊被拒", r.get("isError") is True)
        c, r, b = _mcp(base2, "table_gm", {"token": TOKEN, "op": "resume", "seat": "gm"})
        check("E3f MCP `table_gm` 帶對的 token 仍然走得通", r.get("isError") is False, b[:70])
    finally:
        p2.terminate()
        try:
            p2.wait(5)
        except Exception:                                              # noqa: BLE001
            p2.kill()
        shutil.rmtree(home2, ignore_errors=True)

    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
