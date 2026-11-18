"""models（自 __init__.py 拆出）"""

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

from .shared import _as_list, _canon, _merge_actions, _merge_defaults, _union
from .common import PROVIDES_KEYS, TEXT_FILE



class Pack:
    """一份 `pack.json`（薄資料物件；不持有桌子、不知道誰啟用了它）。"""

    def __init__(self, pid: str, data: Dict[str, Any], path: str = "") -> None:
        self.id = pid
        self.data = data or {}
        self.path = path                 # pack.json 的絕對路徑（tables／text 相對它解析）
        self.dir = os.path.dirname(path) if path else ""

    # ------------------------------------------------------------------ 讀
    @property
    def requires(self) -> List[str]:
        return _as_list(self.data.get("requires"))

    @property
    def optional(self) -> List[str]:
        return _as_list(self.data.get("optional"))

    @property
    def conflicts(self) -> List[str]:
        return _as_list(self.data.get("conflicts"))

    @property
    def provides(self) -> Dict[str, Any]:
        raw = self.data.get("provides")
        return raw if isinstance(raw, dict) else {}

    @property
    def params(self) -> Dict[str, Any]:
        raw = self.data.get("params")
        return dict(raw) if isinstance(raw, dict) else {}

    @property
    def defaults(self) -> Dict[str, Any]:
        """這一包給的**機制預設值**（`provides.defaults`：段名 → 那一格的預設）。

        語意＝「config 沒寫時用這個」——**config 寫的一律贏**（一個旋鈕一個權威）；
        住在這裡的都是「換一張桌還會一樣」的機制常數（規則／棋盤／條件…），不是這一桌的內容。
        """
        raw = self.provides.get("defaults")
        return dict(raw) if isinstance(raw, dict) else {}

    @property
    def mode(self) -> Optional[Dict[str, Any]]:
        """這一包提供的節拍（`{"name": "turn", "pacing": {...}, "turn": {...}}`）。"""
        raw = self.provides.get("mode")
        if raw is None:
            return None
        if isinstance(raw, str):
            return {"name": raw}
        return dict(raw) if isinstance(raw, dict) else {"name": str(raw)}

    def text_bank(self) -> Dict[str, str]:
        """包自帶的句庫（`<包>/text.json`；沒有＝空）。"""
        p = os.path.join(self.dir, TEXT_FILE) if self.dir else ""
        if not p or not os.path.exists(p):
            return {}
        with open(p, "r", encoding="utf-8") as fh:
            bank = json.load(fh)
        if not isinstance(bank, dict):
            raise PackError(f"{self.id}: {TEXT_FILE} 最外層要是物件（key → 文案）")
        return {str(k): str(v) for k, v in bank.items()}

    # ------------------------------------------------------------------ 驗
    def validate(self) -> List[str]:
        """schema 級檢查（跨包的依賴／衝突由 `resolve()` 管）。回所有問題，空＝合法。"""
        e: List[str] = []
        d = self.data
        if not isinstance(d, dict):
            return [f"{self.id}: 最外層要是物件"]
        if not self.id:
            e.append("包沒有 id（＝目錄名）")
        for key in ("requires", "optional", "conflicts"):
            raw = d.get(key)
            if raw is not None and not isinstance(raw, (str, list)):
                e.append(f"{self.id}.{key}: 要是字串或陣列")
        if d.get("provides") is not None and not isinstance(d.get("provides"), dict):
            e.append(f"{self.id}.provides: 要是物件")
        for key in self.provides:
            if key not in PROVIDES_KEYS:
                e.append(f"{self.id}.provides: 不認識的鍵 {key!r}"
                         f"（只有 {'/'.join(PROVIDES_KEYS)}）")
        for key in ("effects", "signals", "state_keys"):
            for x in _as_list(self.provides.get(key)):
                if x not in self._pool(key):
                    e.append(f"{self.id}.provides.{key}: 內核沒有這個 {x!r}"
                             f"（可用的見 packs.KERNEL_{key.upper()}）")
        # text_keys：內核句庫 ∪ 這一包自帶的 text.json（缺＝錯字當場叫）
        _bank = self.text_bank()
        for k in _as_list(self.provides.get("text_keys")):
            if k not in _bank and k not in presentation.TEXT:
                e.append(f"{self.id}.provides.text_keys: 句庫沒有 {k!r}"
                         "（內核句庫與本包 text.json 都沒有）")
        m = self.mode
        if m is not None:
            if str(m.get("name") or "") not in KERNEL_MODES:
                e.append(f"{self.id}.provides.mode.name: 內核沒有這個節拍 {m.get('name')!r}"
                         f"（有的是 {list(KERNEL_MODES)}；新的節拍＝先工程支援）")
            for k in ("pacing", "turn"):
                if m.get(k) is not None and not isinstance(m.get(k), dict):
                    e.append(f"{self.id}.provides.mode.{k}: 要是物件（覆寫 config 的那一段）")
        for act in self.provides.get("actions") or []:
            if isinstance(act, str):
                if not act.strip():
                    e.append(f"{self.id}.provides.actions: 空字串不是動作 id")
                continue
            if not isinstance(act, dict) or not str(act.get("id") or "").strip():
                e.append(f"{self.id}.provides.actions: 每個動作要 {{id,label,desc}}"
                         "（或直接寫 id 字串）")
        for sec, blk in self.defaults.items():
            if sec not in KERNEL_SECTIONS:
                e.append(f"{self.id}.provides.defaults: 不認識的段 {sec!r}"
                         f"（有的是 {list(KERNEL_SECTIONS)}）")
            elif not isinstance(blk, dict):
                e.append(f"{self.id}.provides.defaults.{sec}: 要是物件（config 那一格的預設）")
        for t in _as_list(self.provides.get("tables")):
            if not os.path.exists(os.path.join(self.dir, t)):
                e.append(f"{self.id}.provides.tables: 找不到表 {t!r}（相對包目錄）")
        declared = set(_as_list(self.provides.get("text_keys")))
        bank = set(self.text_bank())
        if self.dir and not os.path.exists(os.path.join(self.dir, TEXT_FILE)):
            if declared:
                e.append(f"{self.id}.provides.text_keys: 宣告了句庫鍵卻沒有 {TEXT_FILE}")
        elif bank - declared:
            e.append(f"{self.id}: {TEXT_FILE} 有沒宣告的鍵 {sorted(bank - declared)}"
                     "（provides.text_keys 要是完整表面）")
        if self.params and not isinstance(self.params, dict):
            e.append(f"{self.id}.params: 要是物件")
        return e

    @staticmethod
    def _pool(key: str) -> Tuple[str, ...]:
        """「內核已實作」的池子（**事件不算在內**：事件由 `verify_impl` 對原始碼核字面）。"""
        return POOLS.get(key, ())

class PackView:
    """**一份已解析的啟用清單**（凍結的合併視圖）。各層只讀它，不再各問各的。"""

    def __init__(self, packs: List[Pack], params: Optional[Dict[str, Any]] = None,
                 texts: Optional[Dict[str, str]] = None) -> None:
        self.packs = packs                     # 拓撲序（依賴在前）
        self.ids = [p.id for p in packs]
        self.enabled = bool(packs)
        self.params = dict(params or {})
        self.texts = dict(texts or {})
        self.mode: Optional[Dict[str, Any]] = next((p.mode for p in packs if p.mode), None)
        self.defaults: Dict[str, Any] = _merge_defaults(packs)
        self.actions = _merge_actions(packs)
        self.effects = _union(packs, "effects")
        self.events = _union(packs, "events")
        self.text_keys = _union(packs, "text_keys")
        self.state_keys = _union(packs, "state_keys")
        self.signals = _union(packs, "signals")
        self.tables = [os.path.join(p.dir, t) for p in packs
                       for t in _as_list(p.provides.get("tables"))]
        self.hash = hashlib.sha1(_canon({
            "ids": self.ids, "actions": self.actions, "effects": list(self.effects),
            "events": list(self.events), "text_keys": list(self.text_keys),
            "state_keys": list(self.state_keys), "signals": list(self.signals),
            "mode": self.mode, "params": self.params, "defaults": self.defaults,
        }).encode("utf-8")).hexdigest()[:16]

    @classmethod
    def legacy(cls) -> "PackView":
        """沒有啟用任何包（**舊桌／沒寫 `packs`**）——各層照舊走 config，行為不變。"""
        return cls([])

    def as_dict(self) -> Dict[str, Any]:
        """凍進事件流的那一小塊（**只放 ids ＋ hash**：事件流不塞整份 provides）。"""
        return {"packs": list(self.ids), "packs_hash": self.hash}

    def summary(self) -> str:
        return f"{self.ids} hash={self.hash}" if self.enabled else "(legacy: 沒有啟用任何包)"
