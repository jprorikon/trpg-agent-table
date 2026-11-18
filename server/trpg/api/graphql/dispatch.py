"""dispatch（自 __init__.py 拆出）"""

from __future__ import annotations
import json
from urllib.parse import quote
import os
import sys
from typing import Any, Dict, List, Optional, Tuple
from trpg.paths import ROOT as HERE  # repo 根（資料／vendor／docs 都在這裡）
from graphql import GraphQLError, build_schema, graphql_sync, parse          # noqa: E402
from trpg.service import assets as module_assets                                         # noqa: E402
from trpg.core import layout as layout                                                          # noqa: E402
from trpg.engine import projection as projection                                                      # noqa: E402
from trpg.core import store as store                                                           # noqa: E402
from trpg.accounts import auth as auth
from trpg.service import table_directory as tdir                                         # noqa: E402

from .common import schema
from .context import Ctx, _is_read_operation
from .resolvers import RESOLVERS

_ARCHIVE_ALLOW = {"setArchived", "newRun", "createTable", "login", "logout"}


def _value_node(v: Any, variables: Dict[str, Any]) -> Any:
    """AST 值節點 → Python 值（變數、字面量、null 都吃）。"""
    kind = getattr(v, "kind", "")
    if kind == "variable":
        return (variables or {}).get(getattr(getattr(v, "name", None), "value", ""))
    if kind == "string":
        return v.value
    if kind == "int":
        return int(v.value)
    if kind == "boolean":
        return bool(v.value)
    if kind == "null":
        return None
    return getattr(v, "value", None)


def _arg_value(node: Any, name: str, variables: Dict[str, Any]) -> Any:
    """從 AST 的欄位參數取**實際值**（字面量或變數都吃；變數沒給＝None）。"""
    for arg in (getattr(node, "arguments", None) or ()):
        if getattr(getattr(arg, "name", None), "value", None) != name:
            continue
        return _value_node(getattr(arg, "value", None), variables)
    return None


def _with_variable_defaults(doc: Any, variables: Dict[str, Any],
                            operation_name: Optional[str]) -> Dict[str, Any]:
    """補上 operation 宣告的**變數預設值**（呼叫端沒給時 GraphQL 會用它 ⇒ 閘也要用它）。

    不補的話有一條 bypass：`mutation($s: ID = "<archived run>"){ say(session:$s…) }`
    ——`variables` 裡沒有 `s`，閘會拿「現役局」去判，實際卻寫進封存局。
    """
    out = dict(variables or {})
    for d in (getattr(doc, "definitions", None) or ()):
        op = getattr(d, "operation", None)
        op = getattr(op, "value", op)
        if op != "mutation":
            continue
        if operation_name:
            nm = getattr(getattr(d, "name", None), "value", None)
            if nm and nm != operation_name:
                continue
        for vd in (getattr(d, "variable_definitions", None) or ()):
            vname = getattr(getattr(vd, "variable", None), "name", None)
            vname = getattr(vname, "value", "")
            if not vname or vname in out:
                continue
            dv = getattr(vd, "default_value", None)
            if dv is not None:
                out[vname] = _value_node(dv, out)
    return out


def _mutation_fields(doc: Any, operation_name: Optional[str]) -> List[Any]:
    """這個文件裡**會被執行**的 mutation 頂層欄位（含 inline fragment／fragment spread 展開）。"""
    ops: List[Any] = []
    frags: Dict[str, Any] = {}
    for d in (getattr(doc, "definitions", None) or ()):
        if getattr(d, "kind", "") == "fragment_definition":
            frags[getattr(getattr(d, "name", None), "value", "")] = d.selection_set
        else:
            _op = getattr(d, "operation", None)
            _op = getattr(_op, "value", _op)          # graphql-core 3.3：OperationType 是 Enum
            if _op == "mutation":
                ops.append(d)
    if not ops:
        return []
    if operation_name:
        exact = [o for o in ops
                 if getattr(getattr(o, "name", None), "value", None) == operation_name]
        if exact:
            ops = exact
    fields: List[Any] = []
    seen: set = set()

    def _walk(selection_set: Any, depth: int) -> None:
        if selection_set is None or depth > 8:
            return
        for s in (getattr(selection_set, "selections", None) or ()):
            kind = getattr(s, "kind", "")
            if kind == "field":
                fields.append(s)
            elif kind == "inline_fragment":
                _walk(getattr(s, "selection_set", None), depth + 1)
            elif kind == "fragment_spread":
                name = getattr(getattr(s, "name", None), "value", "")
                if name in frags and name not in seen:
                    seen.add(name)
                    _walk(frags[name], depth + 1)

    for o in ops:
        _walk(getattr(o, "selection_set", None), 0)
    return fields


def _homes_for(table_id: Any, svc: Any, root: str) -> List[str]:
    """這次 mutation 可能打到哪幾張桌的家目錄（`tableId` 給了就只找那一張）。

    ⚠ 效能：單桌端點（`svc` 已綁定、`tableId` 就是它）直接回它的 home，**不掃全 repo**；
    只有 hub 根端點（`svc=None`）或 `tableId` 指到別桌時才 `discover`。
    """
    out: List[str] = []
    tid = str(table_id or "")
    if svc is not None:
        cfg_tid = str((getattr(svc, "cfg", None) or {}).get("table_id") or "")
        if not tid or not cfg_tid or tid == cfg_tid:
            h = str(getattr(svc, "home", "") or "")
            if h:
                out.append(h)
            return out
    try:
        for row in tdir.discover(root):
            if tid and str(row.get("id")) != tid:
                continue
            h = str(row.get("home") or "")
            if h and h not in out:
                out.append(h)
    except Exception:                                                      # noqa: BLE001
        pass
    return out


def _archived_write_block(query: str, variables: Dict[str, Any], ctx: "Ctx",
                          operation_name: Optional[str] = None) -> Optional[str]:
    """這一發是不是「對已封存的局寫入」⇒ 回錯誤字串（不是就回 None）。

    ★ 2026-10-20（review P2）：以前用正則找 `session: $變數`＋只比對**第一個**欄位名，三個洞：
      ① session 寫字面量（`session: "run"`）就放行；② 一個 mutation 多個欄位只驗第一個
      （`setArchived` 在白名單 ⇒ 後面的 `say` 照寫）；③ 例外時 fail-open。
    改成 `graphql.parse` 拿 AST：**逐個頂層 mutation 欄位**、用解析後的字面量／變數取值；
    解析失敗或讀不到封存狀態一律拒絕（fail-closed）。
    """
    try:
        doc = parse(str(query))
    except Exception as exc:                                               # noqa: BLE001
        return f"GraphQL 查詢解析失敗（拒絕寫入）：{type(exc).__name__}: {exc}"
    fields = _mutation_fields(doc, operation_name)
    if not fields:
        return None
    variables = _with_variable_defaults(doc, variables, operation_name)
    from trpg.service import run_archive as _ra                            # noqa: PLC0415
    for f in fields:
        field = getattr(getattr(f, "name", None), "value", "")
        if field in _ARCHIVE_ALLOW:
            continue
        sess = _arg_value(f, "session", variables)
        table_id = _arg_value(f, "tableId", variables)
        for home in _homes_for(table_id, ctx.svc, ctx.root):
            # 沒帶 session ⇒ 這一發寫的是**這一桌的現役局**（GraphQL 的 session 是選填）。
            # 以前這裡 `continue`（fail-open）⇒ 封存後不帶 session 的 mutation 照樣寫得進去。
            run = str(sess or "")
            if not run:
                try:
                    run = str(store.current_run(home) or "")
                except Exception:                                          # noqa: BLE001
                    run = ""
            if not run:
                continue
            try:
                archived = _ra.is_archived(home, run)
            except Exception as exc:                                       # noqa: BLE001
                return (f"讀不到封存狀態，拒絕寫入：{type(exc).__name__}: {exc}")
            if archived:
                return ("這一局已封存（Archive）：不能再發言／行動。"
                        "要繼續請 owner 解除封存，或開新的一局。")
    return None

def handle(body: Any, *, svc: Any = None, lookup: Any = None, root: str = "",
           headers: Any = None, watch: bool = False,
           new_run: Any = None, create_table: Any = None,
           set_watchable: Any = None) -> Tuple[int, Dict[str, Any]]:
    """處理一次 GraphQL 請求：回 `(http_status, payload)`。

    形狀不合（不是 dict／沒有 query）⇒ 400；GraphQL 層的錯（含 session 不對）⇒ 200 帶 `errors`
    （GraphQL 的慣例：**執行期的錯不是 HTTP 錯**，客戶端看 `errors`）。
    """
    if not isinstance(body, dict):
        return 400, {"errors": [{"message": "body 要是 JSON object"}]}
    q = body.get("query")
    if not q:
        return 400, {"errors": [{"message": "少了 query"}]}
    install_root_resolvers()
    ctx = Ctx(svc=svc, lookup=lookup, root=root, headers=headers, watch=watch, new_run=new_run,
              create_table=create_table, set_watchable=set_watchable,
              # ★ 按 query 做訪問控制（2026-10-19 設計者定案）：只有 `operation=query` 才算只讀，
              #   匿名也只拿得到這種（而且只對「宣告要 account ＋ 開了觀戰」的桌給公開投影）。
              read_only=_is_read_operation(str(q)))
    # 2026-10-19（設計者）：「Archive 狀態下也不再能發送信息」⇒ 在**執行前**擋掉所有寫入。
    # 2026-10-20（P2）：改用 AST 逐個頂層 mutation 欄位檢查（字面量／變數都算），fail-closed。
    _blk = _archived_write_block(q, body.get("variables") or {}, ctx,
                                 body.get("operationName") or None)
    if _blk is not None:
        return 200, {"errors": [{"message": _blk, "path": []}]}
    res = graphql_sync(schema(), str(q), variable_values=body.get("variables") or None,
                       operation_name=body.get("operationName") or None,
                       context_value=ctx, field_resolver=None,
                       root_value=None, middleware=None)
    out: Dict[str, Any] = {}
    if res.data is not None:
        out["data"] = res.data
    if res.errors:
        out["errors"] = [{"message": str(e.message),
                          "path": list(getattr(e, "path", []) or [])} for e in res.errors]
    return 200, out

_INSTALLED = False

def install_root_resolvers() -> None:
    """把 `RESOLVERS` 掛進 schema（`build_schema` 之後才做得到）——第一次進請求時掛一次。"""
    global _INSTALLED
    if _INSTALLED:
        return
    sch = schema()
    for tname, fields in RESOLVERS.items():
        t = sch.type_map.get(tname)
        if t is None:
            continue
        for fname, fn in fields.items():
            if fname in getattr(t, "fields", {}):
                t.fields[fname].resolve = fn
    _INSTALLED = True
