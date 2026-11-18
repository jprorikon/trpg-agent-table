"""merge（自 __init__.py 拆出）"""

from __future__ import annotations
import copy
import hashlib
import json
import os
from typing import Any, Dict, List, Optional, Tuple
from trpg.engine import presentation as presentation      # L1→L4 只讀句庫（`scenario.py` 也是這樣拿句庫鍵的）
from trpg.paths import ROOT
from trpg.rules.pack_kernel import (                                          # noqa: E402
    IMPL_SOURCES, KERNEL_EFFECTS, KERNEL_MODES, KERNEL_SECTIONS, KERNEL_SIGNALS,
    KERNEL_STATE_KEYS, POOLS, PackError, effective_action_entries, log_types_for, verify_impl,
)

from .models import Pack, PackView
from .shared import _as_list, _canon, _deep_fill, _merge_actions, _merge_defaults, _union
from .io import registry



def apply_defaults(cfg: Dict[str, Any], view: "PackView") -> Dict[str, Any]:
    """把啟用包的預設值補進 config 的缺口 ⇒ **回一份新的 config**（不動傳進來的那份）。

    - 沒啟用任何包（legacy）⇒ 原樣回傳（**一個字都不變**——舊桌照舊）。
    - 有寫的鍵一律以 config 為準（＝「config 是旋鈕的權威」，包只補缺口）。
    """
    if not view or not view.enabled or not view.defaults:
        return cfg
    out = dict(cfg or {})
    for sec, blk in view.defaults.items():
        if not isinstance(blk, dict):
            continue
        base = out.get(sec)
        out[sec] = _deep_fill(base if isinstance(base, dict) else {}, blk)
    return out



def resolve(enabled: Any, *, home: Optional[str] = None, root: Optional[str] = None,
            verify: bool = True) -> PackView:
    """把 `config.packs`（清單，或 `{enabled, params}`）解析成 `PackView`。

    - 沒給（`None`／空清單）⇒ `PackView.legacy()`（＝舊桌行為，一個字都不變）。
    - `enabled` 必須**列全**：硬依賴不在清單裡 ⇒ 當場 `PackError`（不自動補——自動補會讓
      「這張桌開了什麼」看不出來）。
    - `verify=True` 順便核對「宣告的動作／事件在內核原始碼裡真的找得到」。
    """
    if enabled is None or enabled == [] or enabled == "":
        return PackView.legacy()
    cfg_params: Dict[str, Any] = {}
    if isinstance(enabled, dict):
        cfg_params = dict(enabled.get("params") or {})
        enabled = enabled.get("enabled")
    ids = _as_list(enabled)
    if not ids:
        return PackView.legacy()

    reg = registry(home=home, root=root)
    errs: List[str] = []
    miss = [i for i in ids if i not in reg]
    if miss:
        raise PackError(f"啟用清單裡的包不存在：{miss}（註冊表有：{sorted(reg)}）")
    for i in ids:
        errs += reg[i].validate()
    if errs:
        raise PackError("包不合法：\n  - " + "\n  - ".join(errs[:16]))

    chosen = {i: reg[i] for i in ids}
    # 硬依賴：清單裡必須有；順便檢查 optional 只影響排序
    for pid, p in chosen.items():
        missing = [d for d in p.requires if d not in chosen]
        if missing:
            raise PackError(f"包 {pid} 缺少硬依賴 {missing}（把他們加進 config.packs）")
    for pid, p in chosen.items():
        for other in p.conflicts:
            if other in chosen:
                raise PackError(f"包 {pid} 與 {other} 衝突（`conflicts` 宣告），不能同時啟用")

    order = _topo(chosen)
    packs = [chosen[i] for i in order]

    # 同一個動作被兩個包宣告成不同內容 ⇒ 作者搞混了（同內容＝去重，不是錯）
    seen: Dict[str, str] = {}
    for p in packs:
        for it in p.provides.get("actions") or []:
            aid = str(it.get("id") if isinstance(it, dict) else it)
            body = _canon(it)
            if aid in seen and seen[aid] != body:
                raise PackError(f"動作 {aid} 被兩個包宣告成不同內容（{p.id}）——請留一個家")
            seen[aid] = body

    modes = [p.id for p in packs if p.mode]
    if len(modes) > 1:
        raise PackError(f"一次只能啟用一個節拍包，啟用了 {modes}")

    view = PackView(packs, params=_merge_params(packs, cfg_params), texts=_merge_texts(packs))
    if verify:
        verify_impl(view, root=root)      # 包只能宣告「內核真的有」的東西
    return view

def _merge_params(packs: List[Pack], cfg_params: Dict[str, Any]) -> Dict[str, Any]:
    """參數＝所有包的 `params` 併起來 ＋ **config 覆寫**（這一桌改常數寫 config，不改包）。

    AST 用 `{"var": "pack.params.<鍵>"}` 取（係數外部化）。跨包同鍵不同值 ⇒ 當場叫。
    """
    out: Dict[str, Any] = {}
    for p in packs:
        for k, v in p.params.items():
            if k in out and out[k] != v:
                raise PackError(f"參數 {k!r} 被兩個包寫成不同值（{p.id}）——請留一個家")
            out[k] = v
    out.update(cfg_params or {})
    return out

def _merge_texts(packs: List[Pack]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for p in packs:
        for k, v in p.text_bank().items():
            if k in out and out[k] != v:
                raise PackError(f"句庫鍵 {k!r} 被兩個包寫成不同文案（{p.id}）")
            out[k] = v
    return out

def _topo(chosen: Dict[str, Pack]) -> List[str]:
    """依賴拓撲排序（依賴在前；`requires` ＋ `optional` 都算邊，只有 enabled 的才算）。"""
    order: List[str] = []
    seen: Dict[str, int] = {}         # 0=走訪中 1=完成

    def visit(pid: str, stack: List[str]) -> None:
        if seen.get(pid) == 1:
            return
        if seen.get(pid) == 0:
            raise PackError(f"包的依賴成環：{' → '.join(stack + [pid])}")
        seen[pid] = 0
        p = chosen[pid]
        for dep in p.requires + [d for d in p.optional if d in chosen]:
            visit(dep, stack + [pid])
        seen[pid] = 1
        order.append(pid)

    for pid in sorted(chosen):        # 同層依 id 排序 ⇒ 結果可重現（hash 才穩）
        visit(pid, [])
    return order
