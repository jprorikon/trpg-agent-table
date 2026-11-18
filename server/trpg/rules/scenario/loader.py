"""loader（自 __init__.py 拆出）"""

from __future__ import annotations
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple
from trpg.rules import expr as expr
from trpg.engine import presentation as presentation

from .common import MODULE_FILES

def resolve_module_path(home: str) -> Tuple[Optional[str], bool]:
    """劇本規則資料的實際路徑：**新名 `scenario.json` 優先，退回舊名 `module.json`**。

    回 `(path, 用了舊名嗎)`；兩個都沒有 ⇒ `(None, False)`。
    用了舊名才回 True（呼叫端該留一行 warn——權威名字是 `scenario.json`，
    它住在「劇本」層，叫 module 在模組層出現後會誤導）。
    """
    for i, name in enumerate(MODULE_FILES):
        p = os.path.join(home, name)
        if os.path.exists(p):
            return p, (i > 0)
    return None, False

def module_filename(home: str) -> Optional[str]:
    """這一張桌的劇本規則資料叫什麼（給自檢器／文件用；沒有 ⇒ None）。"""
    p, _ = resolve_module_path(home)
    return os.path.basename(p) if p else None

class ModuleError(ValueError):
    """劇本模組本身不合法（載入時就叫，不拖到跑團中間）。"""

def resolve_seat(want: Any, ctx: Dict[str, Any]) -> Optional[str]:
    """effect 裡的 `seat` 怎麼解：`me`／`self`＝這一輪出手的人、`target`＝被指定的目標、
    其他＝當成席位 id。**技能表與 module.json 的觸發共用這一支**（口徑只有一份）。
    """
    w = str(want or "me").strip()
    if w in ("me", "self"):
        return ((ctx.get("me") or {}).get("seat")) or None
    if w == "target":
        return ((ctx.get("target") or {}).get("seat")) or None
    return w or None

def _pack_ctx(table: Any) -> Dict[str, Any]:
    """機制包在 AST 裡看得到的部分（`pack.ids`／`pack.params`／`pack.mode`）。

    這讓「係數外部化」（v2 規劃第 8 條）不必改程式：包把常數放 `params`，作者用
    `{"var": "pack.params.<鍵>"}` 引用。沒有啟用任何包＝空殼（不影響既有 AST）。
    """
    pv = getattr(table, "packs", None)
    return {
        "ids": list(getattr(pv, "ids", []) or []),
        "params": dict(getattr(pv, "params", {}) or {}),
        "mode": ((getattr(pv, "mode", None) or {}) or {}).get("name"),
    }

def _node_of(raw: Any) -> Any:
    """`{expr, why}` 容器或裸 AST → 裸 AST。"""
    if isinstance(raw, dict) and "expr" in raw and len(raw) <= 3:
        return raw["expr"]
    return raw
