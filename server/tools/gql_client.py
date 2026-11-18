#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**工具用的 GraphQL 小客戶端**——`/api/*` 退役（2026-10-07）之後，工具一律走這裡。

為什麼要一支共用的：三個工具（`gm.py`／`hold_trpg.py`／`probe_kick_env.py`）原本各寫一次
「POST /api/xxx」；換面的時候**只改這支**，工具那側的呼叫點一字不動。

* 憑證走**標頭**（`Authorization: Bearer`／`X-Table-GM-Token`），不進 body、不進 logs。
* `view()` 回**原樣投影**（`view { raw }`）＝舊 `/api/state` 的那份，呼叫端不用改解析。
* 其餘 mutation 回伺服器端那支 handler 的 payload（與舊 `/api/*` 同形狀）。
"""

import json
import urllib.request

DEFAULT_TIMEOUT = 10.0


def gql(base: str, query: str, variables: dict = None, *, token: str = "", gm: str = "",
        timeout: float = DEFAULT_TIMEOUT) -> dict:
    """送一次 GraphQL，回 `data`（**有 errors 就 raise**，讓呼叫端看得見）。

    `base`＝桌的網址（`http://主機:埠` 或前台的 `http://主機:8971/<桌>`）。
    """
    url = base.rstrip("/") + "/graphql"
    hdr = {"Content-Type": "application/json"}
    if token:
        hdr["Authorization"] = "Bearer " + str(token)
    if gm:
        hdr["X-Table-GM-Token"] = str(gm)
    req = urllib.request.Request(url, data=json.dumps({"query": query, "variables": variables or {}}).encode(),
                                 headers=hdr, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        j = json.loads(r.read().decode("utf-8") or "{}")
    if j.get("errors"):
        raise RuntimeError(str((j["errors"][0] or {}).get("message") or "GraphQL error"))
    return j.get("data") or {}


def session_of(base: str, table: str, *, token: str = "", gm: str = "", timeout: float = DEFAULT_TIMEOUT) -> str:
    """這一局（副本）的 session——**每一格桌內操作都要帶**。"""
    d = gql(base, "query($t: ID!){ table(id:$t){ session { id } } }", {"t": table},
            token=token, gm=gm, timeout=timeout)
    return str(((d.get("table") or {}).get("session") or {}).get("id") or "")


def view(base: str, table: str, session: str, *, seat: str = "", detail: str = "",
         token: str = "", gm: str = "", timeout: float = DEFAULT_TIMEOUT) -> dict:
    """回**原樣投影**（舊 `/api/state` 那份 JSON）。"""
    q = ("query($t: ID!, $s: ID!, $seat: ID, $detail: Detail)"
         "{ table(id:$t){ view(session:$s, seat:$seat, detail:$detail) { raw } } }")
    d = gql(base, q, {"t": table, "s": session, "seat": seat or None, "detail": detail or None},
            token=token, gm=gm, timeout=timeout)
    return ((d.get("table") or {}).get("view") or {}).get("raw") or {}


def mutate(base: str, field: str, args: dict, *, table: str = "", session: str = "",
           token: str = "", gm: str = "", timeout: float = DEFAULT_TIMEOUT) -> dict:
    """桌內 mutation（`field`＝GraphQL 欄位名；`table`／`session` 會自動補）。"""
    a = dict(args or {})
    if table:
        a["tableId"] = table
    if session:
        a["session"] = session
    decl, argl = [], []
    for k, v in a.items():
        # ⚠ `tableId`／`session` 在 schema 裡是 **ID!**（不可空）——宣告成 `ID` 會被 GraphQL 拒。
        # ⚠ 2026-10-10 修：**必填字串**同理（`say(text:String!)`）⇒ 只判斷那兩個鍵不夠，
        #   `gm.py say` 因此一直是壞的。規則：**有值就宣告不可空**（不可空可放進可空位置，反之不行）；
        #   值為 `None` 才維持可空（schema 裡可選的參數照樣能省）。
        _t = "ID!" if k in ("tableId", "session") else _gtype(k, v)
        if not _t.endswith("!") and v is not None:
            _t += "!"
        decl.append(f"${k}: {_t}")
        argl.append(f"{k}: ${k}")
    d = gql(base, "mutation(" + ", ".join(decl) + "){ " + field + "(" + ", ".join(argl) + ") }",
            a, token=token, gm=gm, timeout=timeout)
    return d.get(field) or {}


def gm_op(base: str, table: str, session: str, op: str, args: dict = None, *, gm: str = "",
          timeout: float = DEFAULT_TIMEOUT) -> dict:
    """GM op 直通（`gmRun`）——`op／args` 原樣交給舊 `/api/gm` 那支 handler。"""
    a = dict(args or {})
    if str(op) != "semi":
        a.pop("op", None)                     # 非 semi：args 不該帶同名鍵（維持原樣）
    else:
        # ⚠ 2026-10-10 修：`semi` 的動作名**就在 args["op"]**，丢掉它＝整條路壞（見 graphql_api 那段）
        pass
    a.pop("token", None)
    a.pop("gm_token", None)
    d = gql(base,
            "mutation($t: ID!, $s: ID!, $op: String!, $a: JSON)"
            "{ gmRun(tableId: $t, session: $s, op: $op, args: $a) }",
            {"t": table, "s": session, "op": op, "a": a}, gm=gm, timeout=timeout)
    return d.get("gmRun") or {}


def _gtype(k: str, v) -> str:
    if k in ("tableId", "session", "seat", "to", "target", "charId", "skill"):
        return "ID"
    if k in ("args", "attrs"):
        return "JSON"
    if k == "amount" or k == "dc":
        return "Int"
    if k == "kind":
        return "JoinKind"
    return "String"
