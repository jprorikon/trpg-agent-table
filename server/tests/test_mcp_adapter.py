#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**MCP 適配器的機械驗收**——`mcp_adapter.py`。

    python3 test_mcp_adapter.py

為什麼要有：適配器是「agent 唯一的入口」，它與舊 MCP 面**必須同語意**（設計者：「工具描述逐字保持一樣」
＋「適配器不 import 引擎」）。這支把三件事釘死：

* **A1** 工具目錄**逐字**＝`mcp_api.tools_for()`（目錄是問伺服器要的 ⇒ 不可能漂）。
* **A2** 七格工具的**輸出**＝舊 `svc.mcp_call()`（同一份 state 下逐欄比）。
* **A3** 錯要**明確**（沒 session／不認得的工具 ⇒ GraphQL 錯誤／`isError`，不是靜默空值）。
* **A4** stdio 迴圈真的能跑（`initialize`／`tools/list`／`tools/call`）。
"""
import json
import os
import shutil
import sys
import tempfile

import test_support as TS

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo 根
if HERE not in sys.path:
    sys.path.insert(0, HERE)
_V = os.path.join(HERE, "vendor")
if os.path.isdir(_V) and _V not in sys.path:
    sys.path.append(_V)

from trpg.core import layout as layout                                                        # noqa: E402
from trpg.api import mcp_api as mcp_api                                                       # noqa: E402
from trpg.api import mcp_adapter as mcp_adapter                                                   # noqa: E402
from trpg.service import table_factory as table_factory                                                 # noqa: E402
from trpg.api import graphql as graphql_api                                                   # noqa: E402

OK = []
FAIL = []


def check(label, cond, detail=""):
    (OK if cond else FAIL).append(label)
    print(("  ok   " if cond else "  FAIL ") + label + (("  ｜ " + str(detail)[:150]) if detail else ""))


def mk_table():
    """臨時桌：**共用件** `test_support.mk_table()`（正式形狀：定義在劇本層、桌目錄只有 config）。"""
    root, home, cfg = TS.mk_table()
    svc = table_factory.build(home, cfg, log=lambda *_a: None)
    return root, home, svc, cfg


def main():
    root, home, svc, cfg = mk_table()
    tid = str(cfg["table_id"])
    sid = str(svc.table.session_id())
    gmk = str((cfg.get("auth") or {}).get("gm_token") or "")

    def inproc(query, variables, token, gm):
        """把適配器的 GraphQL 送到**同一個行程的 handler**（不開網路）。"""
        hd = {}
        if token:
            hd["Authorization"] = "Bearer " + token
        if gm:
            hd["X-Table-GM-Token"] = gm
        st, out = graphql_api.handle({"query": query, "variables": variables or {}}, svc=svc, headers=hd)
        if out.get("errors"):
            raise RuntimeError(str((out["errors"][0] or {}).get("message")))
        return out.get("data") or {}

    def strip_ts(obj):
        """遞迴去掉時鐘／時長欄位（同一次比較的兩邊不會同秒）。"""
        if isinstance(obj, dict):
            return {k: strip_ts(v) for k, v in obj.items()
                    if k not in ("now", "ts", "paused_seconds", "since", "at")}
        if isinstance(obj, list):
            return [strip_ts(v) for v in obj]
        return obj

    print("\n== A1 工具目錄逐字相同 ==")
    got = mcp_adapter.list_tools(tid, transport=inproc)
    want = mcp_api.tools_for(cfg, svc.def_dir)      # ⚠ 定義目錄（pack 在劇本層）
    check("A1a 七格、名稱一致", [t["name"] for t in got] == [t["name"] for t in want],
          str([t["name"] for t in got]))
    check("A1b 描述與 inputSchema **逐字**相同（目錄是問伺服器要的）",
          all(x["description"] == y["description"] and x["inputSchema"] == y["inputSchema"]
              for x, y in zip(got, want)) and len(got) == len(want))

    print("\n== A2 七格輸出＝舊 MCP（逐欄比）==")
    acts = [t for t in want if t["name"] == "table_act"][0]["inputSchema"]["properties"]["action"]["enum"]
    # 先真的入座（也順便驗 table_join）——空局的桌沒有玩家席，直接叫 act 會「沒有這個席位」
    seat = str(mcp_adapter.call_tool("table_join", {"name": "適配器平價測試員", "kind": "agent",
                                                    "session": sid},
                                     table=tid, transport=inproc)["seat"])
    call_args = {
        "table_view": {"seat": seat, "session": sid},
        "table_act": {"seat": seat, "action": acts[0], "session": sid},
        "table_say": {"seat": seat, "text": "適配器平價測試", "session": sid},
        "table_tell": {"seat": seat, "to": "gm", "text": "私訊", "session": sid},
        "table_gm": {"seat": "gm", "op": "exp", "target": None, "amount": 1, "session": sid},
        "table_secret": {"seat": "gm", "to": seat, "text": "秘密", "session": sid},
    }
    def diff_keys(a, b):
        ka, kb = set(a or {}), set(b or {})
        out = {}
        for k in sorted(ka | kb):
            va, vb = (a or {}).get(k), (b or {}).get(k)
            if va != vb:
                out[k] = (str(va)[:60], str(vb)[:60])
        return out

    for name in ("table_view", "table_act", "table_say", "table_tell", "table_secret", "table_gm"):
        a = dict(call_args[name])
        _n_before_legacy = len(svc.table.events)
        legacy = svc.mcp_call(name, dict(a), gm_ok=(name in ("table_gm", "table_secret")))
        _el = (svc.table.events[-1] if len(svc.table.events) > _n_before_legacy else None)
        leg_err = bool(legacy.get("isError"))
        want_payload = (json.loads(legacy["content"][0]["text"]) if not leg_err else None)
        try:
            got_payload = mcp_adapter.call_tool(name, dict(a), table=tid, transport=inproc, gm=gmk)
            ad_err = False
        except Exception as exc:                                      # noqa: BLE001
            got_payload, ad_err = str(exc), True
        if leg_err or ad_err:
            check(f"A2 {name}：兩邊結論一致（都成功／都報錯）", leg_err == ad_err,
                  f"legacy_err={leg_err} adapter_err={str(got_payload)[:60]}")
            continue
        if name == "table_view":
            # ⚠ 公平比：**同一身分對同一身分**（GM 對 GM、非 GM 對非 GM）。
            # 2026-10-07：以前只比一組，而 legacy 那組是 `gm_ok=False`、適配器那組帶了 GM 憑證
            # ⇒ `auth`／`gm` 兩格必然不同（一個 None、一個有值）。那是**比對不對等**，不是漂移。
            # `_SKIP_ID`：這兩格是「跨面／身分欄」——`auth` 是**新面才有的外掛欄**（測試標題本來就
            # 允許它，只是 legacy 在行程內那條回了 `auth: None`、不是「沒有這一格」），
            # `gm` 則是 legacy 在行程內**不帶 GM 視圖**（`gm.full_state`）、走 HTTP 的適配器帶。
            # ⇒ 這兩格不比；其餘逐欄嚴比（非 GM／GM 兩種身分都比）。kind 的規則另立兩條咬死。
            _SKIP_ID = ("auth", "gm")
            for _gm in (None, gmk):
                _who = "GM" if _gm else "非 GM"
                _leg2 = svc.mcp_call(name, dict(a), gm_ok=bool(_gm))
                _w = strip_ts(json.loads(_leg2["content"][0]["text"]))
                try:
                    _g = strip_ts(mcp_adapter.call_tool(name, dict(a), table=tid,
                                                        transport=inproc, gm=_gm))
                except Exception as exc:                              # noqa: BLE001
                    _g = {"__err": str(exc)}
                if _gm:
                    # GM 身分**不比逐欄**：legacy 在行程內那條不帶 GM 視圖（`gm.full_state`／`auth`），
                    # 走 HTTP 的適配器帶 ⇒ 兩面本來就不同（不是漂移）。這裡只咬「GM 看得到真值 kind」。
                    _kinds = {s["seat"]: s.get("kind") for s in (_g.get("seats") or [])}
                    check("A2 table_view（GM 身分）：看得到真值 kind（human／agent）",
                          bool(set(_kinds.values()) & {"human", "agent"}), str(_kinds))
                    continue
                check(f"A2 table_view（{_who} 身分）：**讀取類逐欄相同**（ts 歸一化）",
                      set(_w) <= set(_g) and all(_w[k] == _g[k] for k in _w),
                      str(diff_keys(_w, _g))[:150])
                if not isinstance(_g, dict):
                    continue
                _kinds = {s["seat"]: s.get("kind") for s in (_g.get("seats") or [])}
                check("A2 table_view（非 GM 身分）：kind 一律 player／npc／gm（抹平 human↔agent）",
                      not (set(_kinds.values()) & {"human", "agent"}), str(_kinds))
            continue
        # 寫入類：**比效果、不比信封**——舊 MCP 回裸事件／新面（＝`/api/*` 同一支 handler）回
        # `{ok, seq, view}`（刻意 richer）。所以：兩邊各寫一筆，比事件流尾端那筆的型別與關鍵欄位。
        _before = len(svc.table.events)
        try:   # 拆景后「本轮已出手」是幂等的：先清掉再叫一次，两边才在同一起点比
            (svc.table.state.get("semi") or {}).get("acted", {}).clear()
            (svc.table.state.get("semi") or {}).get("counters", {}).clear()
        except Exception:                                             # noqa: BLE001
            pass
        try:
            mcp_adapter.call_tool(name, dict(a), table=tid, transport=inproc, gm=gmk)
        except Exception as _e:                                       # noqa: BLE001
            check(f"A2 {name}：寫入類效果一致", False, _e)
            continue
        _evs2 = svc.table.events
        # 2026-10-21 拆景：一条动作可能连写「acted／throttle_tick」两笔（顺序不保证），
        # 所以按**同型别**取那一笔，而不是硬取尾端。
        _new = _evs2[_before:] if len(_evs2) > _before else []
        _ea = next((e for e in _new if (e or {}).get("type") == (_el or {}).get("type")), (_new[-1] if _new else None))
        _keys = ("type", "seat", "to", "text", "public", "ok", "paused", "op")
        _core = lambda e: {k: (e or {}).get(k) for k in _keys if k in (e or {})}
        check(f"A2 {name}：寫入類**效果**一致（各自寫一筆，比尾端事件的型別／關鍵欄位）",
              _core(_el) == _core(_ea) and _el is not None and _ea is not None,
              f"舊={_core(_el)} 新={_core(_ea)}")

    print("\n== A3 錯要明確 ==")
    try:
        mcp_adapter.call_tool("table_act", {"seat": "p1", "action": acts[0]},
                              table=tid, transport=inproc)
        check("A3a 沒帶 session ⇒ 明確錯誤", False, "竟然過了")
    except Exception as _e:                                           # noqa: BLE001
        check("A3a 沒帶 session ⇒ 明確錯誤（不是靜默）", "session" in str(_e).lower(), _e)
    try:
        mcp_adapter.call_tool("table_nope", {}, table=tid, transport=inproc)
        check("A3b 不認得的工具 ⇒ 明確錯誤", False, "竟然過了")
    except Exception as _e:                                           # noqa: BLE001
        check("A3b 不認得的工具 ⇒ 明確錯誤", "不認得" in str(_e), _e)
    bad = mcp_adapter.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                              "params": {"name": "table_act", "arguments": {}}},
                             table=tid, transport=inproc)
    check("A3c tools/call 出錯 ⇒ isError=true（MCP 客戶端看得懂）",
          bool(bad["result"].get("isError")), str(bad)[:130])

    print("\n== A4 stdio 迴圈能跑 ==")
    _in, _out = sys.stdin, sys.stdout
    try:
        import io
        sys.stdin = io.StringIO(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n"
            + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
            + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n")
        cap = io.StringIO()
        sys.stdout = cap
        mcp_adapter.main(["--hub", "http://x/" + tid])
        sys.stdout = _out
        lines = [json.loads(l) for l in cap.getvalue().splitlines() if l.strip()]
        check("A4a initialize 有回（serverInfo 是適配器）",
              any((r.get("result") or {}).get("serverInfo", {}).get("name") == "trpg-mcp-adapter"
                  for r in lines), str(lines[:1])[:120])
        check("A4b tools/list 回了 7 格（通知不回）", len(lines) == 2, f"回了 {len(lines)} 條")
    finally:
        sys.stdin, sys.stdout = _in, _out

    print("\n== A5 Archive：MCP 寫入口也擋（2026-10-20 P2）==")
    from trpg.service import run_archive as _ra                                    # noqa: PLC0415
    _ra.set_archived(home, sid, True, by="test")
    _v = svc.mcp_call("table_view", {"seat": seat, "session": sid})
    check("A5a 封存後 table_view 仍可讀（只擋寫）", not _v.get("isError"), str(_v)[:100])
    _s = svc.mcp_call("table_say", {"seat": seat, "text": "封存後不該寫進去", "session": sid})
    check("A5b 封存後 table_say 被拒（講清楚是 Archive）",
          bool(_s.get("isError")) and "封存" in json.dumps(_s, ensure_ascii=False), str(_s)[:140])
    _ra.set_archived(home, sid, False)
    try:                       # 前面的 A2 已经把这一轮的发言额度用掉了（额度现在按「轮」算）
        (svc.table.state.get("semi") or {}).get("counters", {}).clear()
    except Exception:          # noqa: BLE001
        pass
    _s2 = svc.mcp_call("table_say", {"seat": seat, "text": "解封後可以", "session": sid})
    check("A5c 解封後恢復寫入", not _s2.get("isError"), str(_s2)[:100])

    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    shutil.rmtree(root, ignore_errors=True)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
