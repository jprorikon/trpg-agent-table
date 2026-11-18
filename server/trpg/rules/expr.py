#!/usr/bin/env python3
"""**L1｜JSON AST 求值器**——劇本作者用**資料**寫條件，引擎不再為每種條件寫一個分支。

純函數：**不 `eval`、不讀時鐘、不隨機、不碰檔案／網路**（`test_layers` E 組機械擋）。
隨機與裁決留在 **L0 內核**（種子化亂數）；這一支只做「查值＋比較＋邏輯」。

## 節點格式（JSON）

- **字面值**：數字／字串／布林／null／陣列；**單鍵字典且鍵是算子**＝算子節點；
  其餘字典＝物件字面值（值會遞迴求值）。保留鍵 `label`／`why`／`doc` 只是註解與失敗說明。
- **查值**：`{"var": "me.node"}`（點號路徑；查不到回 `null`）、
  `{"get": [<物件>, <鍵>, <預設?>]}`、`{"exists": "world.pray_first.hyakki_f4"}`
- **邏輯**：`{"and": [...]}`、`{"or": [...]}`、`{"not": <expr>}`、
  `{"select": [<條件>, <真值>, <假值>]}`
- **比較**：`{"==": [a,b]}`／`!=`／`<`／`<=`／`>`／`>=`／`{"in": [x, 集合]}`／`{"nin": [...]}`／
  `{"contains": [字串或陣列, 子字串]}`（`<>` 遇上 `null` 一律回 false，不炸）
- **算術**：`{"+": [...]}`、`{"-": [a,b]}`、`{"*": [...]}`、`{"/": [a,b]}`、`{"%": [a,b]}`、
  `{"min": [...]}`、`{"max": [...]}`、`{"abs": <expr>}`、`{"floor": <expr>}`、`{"round": [<expr>, 位數?]}`
- **集合／聚合**：`{"len": <expr>}`、`{"count": <expr>}`、`{"sum": <expr>}`、
  `{"any": <expr>}`、`{"all": <expr>}`、`{"coalesce": [...]}`、`{"concat": [...]}`
- **容器走訪**：`{"values": <dict>}`、`{"keys": <dict>}`、
  `{"pluck": [<list>, "<每個元素的欄位>"]}`（例如「有人站上最頂」＝
  `{"in": ["hyakki_f5", {"pluck": [{"values": {"var": "seats"}}, "node"]}]}`）

## 失敗說明（給玩家看「還差什麼」）

節點上可掛 `why`：失敗時把它的值併進 detail（值本身可以是表達式，會求值）：

    {"in": ["c_cat_trace", {"var": "me.claims"}],
     "why": {"need_claims": "c_cat_trace", "you_have_claims": {"var": "me.claims"}}}

`evaluate_detail()` 會回 `(值, 失敗葉節點的 why 合併)`——`view` 的 `your_goal.progress` 就是它。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

MAX_DEPTH = 24
MAX_NODES = 400
META = ("label", "why", "doc")
_VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)*$")


class ExprError(ValueError):
    """AST 本身不合法（作者寫錯）——**在載入劇本時就要叫**，不是跑到一半才炸。"""


# --------------------------------------------------------------------------- 查值

def lookup(ctx: Any, path: str) -> Any:
    """點號路徑查值（`a.b.0.c`）；任一段缺失 ⇒ None。"""
    cur = ctx
    for part in str(path).split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, (list, tuple)):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if cur is None:
            return None
    return cur


def _num(v: Any, default: float = 0.0) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default


# --------------------------------------------------------------------------- 算子實作

def _o_and(args: List[Any]) -> bool:
    return all(bool(a) for a in args)


def _o_or(args: List[Any]) -> bool:
    return any(bool(a) for a in args)


def _cmp(op: str):
    def f(a: List[Any]) -> bool:
        x, y = a[0], a[1]
        if x is None or y is None:
            return False
        try:
            if op == "==":
                return x == y
            if op == "!=":
                return x != y
            if op == "<":
                return x < y
            if op == "<=":
                return x <= y
            if op == ">":
                return x > y
            return x >= y
        except TypeError:
            return False
    return f


def _o_in(a) -> bool:
    return a[0] in a[1] if isinstance(a[1], (list, tuple, set, str, dict)) else False


def _o_nin(a) -> bool:
    return not _o_in(a)


def _o_contains(a) -> bool:
    hay, needle = a[0], a[1]
    if isinstance(hay, str):
        return str(needle) in hay
    if isinstance(hay, (list, tuple, set, dict)):
        return needle in hay
    return False


def _o_plus(a: List[Any]) -> Any:
    total: Any = 0
    for x in a:
        if isinstance(x, str) or isinstance(total, str):
            total = ("" if total == 0 else total) + str(x)
        else:
            total += _num(x) if not isinstance(x, (int, float)) else x
    return total


def _o_minus(a) -> Any:
    return _num(a[0]) - _num(a[1])


def _o_mul(a) -> Any:
    out: Any = 1
    for x in a:
        out *= x
    return out


def _o_div(a) -> Any:
    if _num(a[1]) == 0:
        raise ExprError("除數是 0（劇本裡的除法要自己擋）")
    return a[0] / a[1]


def _o_mod(a) -> Any:
    if _num(a[1]) == 0:
        raise ExprError("取模的除數是 0")
    return a[0] % a[1]


def _o_concat(a) -> str:
    return "".join("" if x is None else str(x) for x in a)


def _o_len(a) -> int:
    x = a
    if x is None:
        return 0
    try:
        return len(x)
    except TypeError:
        raise ExprError(f"len 不吃 {type(x).__name__}")


def _o_sum(a) -> Any:
    return sum(_num(x) for x in (a or []))


def _o_coalesce(a) -> Any:
    for x in a:
        if x is not None:
            return x
    return None


# name -> (arity, fn)   arity: "n"=參數是運算式陣列（求值後成 list）, "1"=單一運算式
OPS: Dict[str, Tuple[str, Any]] = {
    "and": ("n", _o_and), "or": ("n", _o_or), "not": ("1", lambda x: not bool(x)),
    "==": ("n", _cmp("==")), "!=": ("n", _cmp("!=")), "<": ("n", _cmp("<")),
    "<=": ("n", _cmp("<=")), ">": ("n", _cmp(">")), ">=": ("n", _cmp(">=")),
    "in": ("n", _o_in), "nin": ("n", _o_nin), "contains": ("n", _o_contains),
    "+": ("n", _o_plus), "-": ("n", _o_minus), "*": ("n", _o_mul), "/": ("n", _o_div),
    "%": ("n", _o_mod), "min": ("n", lambda a: min(a)), "max": ("n", lambda a: max(a)),
    "concat": ("n", _o_concat), "coalesce": ("n", _o_coalesce),
    "select": ("n", lambda a: a[1] if a[0] else a[2]),
    "abs": ("1", lambda x: abs(_num(x))), "floor": ("1", lambda x: int(_num(x) // 1)),
    "round": ("n", lambda a: round(_num(a[0]), int(_num(a[1], 0)) if len(a) > 1 else 0)),
    "len": ("1", _o_len), "count": ("1", _o_len), "sum": ("1", _o_sum),
    "any": ("1", lambda x: any(bool(i) for i in (x or []))),
    "all": ("1", lambda x: all(bool(i) for i in (x or []))),
    "values": ("1", lambda x: list(x.values()) if isinstance(x, dict) else list(x or [])),
    "keys": ("1", lambda x: list(x.keys()) if isinstance(x, dict) else []),
    "pluck": ("n", lambda a: [expr_path(it, a[1]) for it in (a[0] or [])]),
}
_ARITY = {"n": None, "1": 1}
_BINARY_MIN = ("==", "!=", "<", "<=", ">", ">=", "in", "nin", "contains", "-", "/", "%")


def expr_path(item: Any, path: Any) -> Any:
    """從一個元素上取欄位（`pluck` 用）：`{"pluck": [{"values": {"var": "seats"}}, "node"]}`。"""
    return lookup(item, str(path))


def core_of(node: dict) -> dict:
    """去掉註解鍵（`label`／`why`／`doc`）後的本體。"""
    return {k: v for k, v in node.items() if k not in META}


# --------------------------------------------------------------------------- 求值

def _ev(node: Any, ctx: Any, depth: int, want: bool) -> Tuple[Any, Dict[str, Any]]:
    if depth > MAX_DEPTH:
        raise ExprError(f"AST 太深（>{MAX_DEPTH} 層）")
    if isinstance(node, dict):
        core = core_of(node)
        why = node.get("why")
        if len(core) == 1:
            key = next(iter(core))
            if key in OPS or key in ("var", "get", "exists"):
                val, det = _op(key, core[key], ctx, depth, want)
                if want and why is not None and not val:
                    det = dict(det)
                    det.update(_why_of(why, ctx, depth))
                return val, det
        return {k: _ev(v, ctx, depth + 1, want)[0] for k, v in node.items()}
    if isinstance(node, list):
        return [_ev(x, ctx, depth + 1, want)[0] for x in node], {}
    return node, {}


def _why_of(why: Any, ctx: Any, depth: int) -> Dict[str, Any]:
    if isinstance(why, dict):
        return {k: _ev(v, ctx, depth + 1, False)[0] for k, v in why.items()}
    return {"why": why}


def _op(key: str, arg: Any, ctx: Any, depth: int, want: bool) -> Tuple[Any, Dict[str, Any]]:
    """求一個算子節點 ⇒ **(值, 失敗說明)**。邏輯算子會順便收集子節點的 why。

    ⚠ 回傳值一定是「值」本身，**不可以把 (值, 說明) 整個當值傳出去**——那樣真值判斷會
    變成看元組空不空（2026-10-06 真的踩到：`and` 的結果變成 tuple ⇒ 一律為真）。
    """
    if key == "var":
        return lookup(ctx, arg), {}
    if key == "exists":
        return (lookup(ctx, arg) is not None), {}
    if key == "get":
        items = [_ev(x, ctx, depth + 1, False)[0] for x in arg]
        obj, k = items[0], items[1]
        default = items[2] if len(items) > 2 else None
        if isinstance(obj, dict):
            return obj.get(k, default), {}
        if isinstance(obj, (list, tuple)):
            try:
                return obj[int(k)], {}
            except (ValueError, IndexError, TypeError):
                return default, {}
        return default, {}
    if key in ("and", "or"):
        return _logic(key, arg, ctx, depth, want)
    arity, fn = OPS[key]
    if arity == "1":
        return fn(_ev(arg, ctx, depth + 1, False)[0]), {}
    if key == "select":                       # 兩邊都要短路求值，不能先全算
        cond = _ev(arg[0], ctx, depth + 1, False)[0]
        return _ev(arg[1] if cond else arg[2], ctx, depth + 1, want)[0], {}
    return fn([_ev(x, ctx, depth + 1, False)[0] for x in arg]), {}


def _logic(key: str, arg: Any, ctx: Any, depth: int, want: bool) -> Tuple[bool, Dict[str, Any]]:
    """`and`／`or`：短路，並把決定成敗那一側的 `why` 收起來。"""
    fails: Dict[str, Any] = {}
    for x in arg:
        v, d = _ev(x, ctx, depth + 1, want)
        if not v:
            fails.update(d)
            if key == "and":
                return False, fails
        elif key == "or":
            return True, {}
    return (True if key == "and" else False), fails


def evaluate(node: Any, ctx: Any) -> Any:
    """求值（不看 why）——條件式回真值，數值式回數字。"""
    return _ev(node, ctx, 0, False)[0]


def evaluate_detail(node: Any, ctx: Any) -> Tuple[Any, Dict[str, Any]]:
    """求值 ＋「為什麼沒過」：失敗葉節點的 `why` 合併（值已求值）。"""
    return _ev(node, ctx, 0, True)


# --------------------------------------------------------------------------- 靜態檢查

def validate(node: Any, *, path: str = "$", depth: int = 0,
             errors: Optional[List[str]] = None, count: Optional[List[int]] = None) -> List[str]:
    """回所有問題（空 list＝合法）。**載入劇本時跑**：作者寫錯要當場叫。"""
    errors = [] if errors is None else errors
    count = [0] if count is None else count
    count[0] += 1
    if count[0] > MAX_NODES:
        errors.append(f"{path}: 節點太多（>{MAX_NODES}）")
        return errors
    if depth > MAX_DEPTH:
        errors.append(f"{path}: 太深（>{MAX_DEPTH} 層）")
        return errors
    if isinstance(node, dict):
        core = core_of(node)
        if len(core) == 1:
            key = next(iter(core))
            if key in ("var", "exists"):
                if not isinstance(core[key], str) or not _VAR_RE.match(core[key]):
                    errors.append(f"{path}: {key} 要吃點號路徑字串，收到 {core[key]!r}")
                return errors
            if key == "get":
                if not isinstance(core[key], list) or len(core[key]) not in (2, 3):
                    errors.append(f"{path}: get 要吃 [物件, 鍵, 預設?]")
                else:
                    for i, x in enumerate(core[key]):
                        validate(x, path=f"{path}.get[{i}]", depth=depth + 1, errors=errors, count=count)
                return errors
            if key in OPS:
                arity, _fn = OPS[key]
                arg = core[key]
                if arity == "1":
                    validate(arg, path=f"{path}.{key}", depth=depth + 1, errors=errors, count=count)
                else:
                    if not isinstance(arg, list):
                        errors.append(f"{path}.{key}: 參數要吃陣列")
                    else:
                        if len(arg) < 2 and key in _BINARY_MIN:
                            errors.append(f"{path}.{key}: 至少要 2 個參數")
                        for i, x in enumerate(arg):
                            validate(x, path=f"{path}.{key}[{i}]", depth=depth + 1, errors=errors, count=count)
                return errors
            errors.append(f"{path}: 不認識的算子 {key!r}")
            return errors
        for k, v in node.items():
            if k in META:
                continue
            validate(v, path=f"{path}.{k}", depth=depth + 1, errors=errors, count=count)
        return errors
    if isinstance(node, list):
        for i, x in enumerate(node):
            validate(x, path=f"{path}[{i}]", depth=depth + 1, errors=errors, count=count)
    return errors


def validate_or_raise(node: Any, *, label: str = "") -> None:
    errs = validate(node)
    if errs:
        raise ExprError(f"{label or 'AST'} 不合法：" + "；".join(errs[:5]))


def explain(node: Any, depth: int = 0) -> str:
    """把 AST 印成人看得懂的一行（文件與除錯用）。"""
    pad = "  " * depth
    if isinstance(node, dict):
        core = core_of(node)
        if len(core) == 1:
            key = next(iter(core))
            if key == "var":
                return f"{pad}var {core[key]}"
            if key == "exists":
                return f"{pad}exists {core[key]}"
            if key in ("get", "and", "or", "not", "select", "coalesce", "concat"):
                inner = core[key] if isinstance(core[key], list) else [core[key]]
                body = "\n".join(explain(x, depth + 1) for x in inner)
                return f"{pad}{key}(\n{body}\n{pad})"
            if key in OPS:
                body = explain(core[key], depth + 1)
                return f"{pad}{key} {body.strip()}"
            return f"{pad}{key}"
        return f"{pad}{{...}}"
    return f"{pad}{node!r}"
