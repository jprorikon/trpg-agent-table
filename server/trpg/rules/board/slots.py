#!/usr/bin/env python3
"""**棋盤機制的泛用槽位**（2026-10-20 設計者：「改成泛用的槽位，具體名字由模組級別確定」）。

引擎只認**四個槽位名**，實際字串由模組／劇本宣告：

| 槽位 | 語意 | 沒有宣告時的預設 |
|---|---|---|
| `mob` | 節點上的小怪（數量欄位／擊破事件） | `mobs`／`mob_killed` |
| `coin` | 這一桌的貨幣（席位錢包欄位） | `star_coins` |
| `lift` | 層與層之間的載具（事件型別） | `elevator` |
| `sanctum` | 記憶見底時的恢復房間（事件型別） | `white_room` |

宣告位置（**近的贏**）：

```jsonc
// modules/<模組>/module.json   ← 模組級（設計者說的「模組級別確定」）
// scenarios/<劇本>/scenario.json ← 劇本級（同一模組裡不同劇本要不同詞時用）
"slots": {
  "mob":     {"key": "mobs", "event": "mob_killed", "name_jp": "小怪", "name_cn": "小怪"},
  "coin":    {"key": "star_coins",  "name_jp": "貨幣", "name_cn": "货币"},
  "lift":    {"key": "elevator",    "name_jp": "昇降機", "name_cn": "升降机"},
  "sanctum": {"key": "white_room",  "name_jp": "塔外", "name_cn": "白色房间"}
}
```

**紀律**：
* 缺項 ⇒ 用預設（`SLOT_DEFAULTS`）——沒有 `slots` 段的舊模組**一個字都不變**（legacy 零變更）。
* 宣告了就要合法（`key` 非空字串；給了 `event` 也非空）——壞掉**載入時就叫**，不靜默。
* 引擎內部**不再出現** `mobs`／`star_coins`／`elevator`／`white_room` 的字面量（`test_slots` 咬住）。
* **投影／事件流的欄位名跟著槽位走**（＝模組宣告的 key）⇒ 前端讀到的仍是模組的詞；換了詞的模組自然給新詞。
* ⚠ **賬號層的錢包欄位（`accounts` 的 `star_coins`）不在槽位範圍**：那是跨局的通用錢包，
  跟「這一桌的幣叫什麼」解耦（桌內用 `slot("coin")`，跨局入賬時按同一個數字搬）。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

#: 槽位 → 預設（key＝state／投影／事件欄位用的字串；event＝事件型別）
SLOT_DEFAULTS: Dict[str, Dict[str, str]] = {
    "mob": {"key": "mobs", "event": "mob_killed"},
    "coin": {"key": "star_coins"},
    "lift": {"key": "elevator", "event": "elevator"},
    "sanctum": {"key": "white_room", "event": "white_room"},
}

#: 認得的槽位（寫錯的名字要在載入時叫，不要默默當成沒寫）
SLOT_NAMES = tuple(SLOT_DEFAULTS)

#: 一個槽位宣告認得的欄位
SLOT_FIELDS = ("key", "event", "name_jp", "name_cn", "note")


class SlotError(ValueError):
    """槽位宣告不合法（載入時就叫）。"""


def _merge(base: Dict[str, Dict[str, str]], raw: Any, where: str) -> Dict[str, Dict[str, str]]:
    if raw is None:
        return base
    if not isinstance(raw, dict):
        raise SlotError(f"{where}: slots 要是物件")
    out = {k: dict(v) for k, v in base.items()}
    for name, spec in raw.items():
        if name not in SLOT_DEFAULTS:
            raise SlotError(f"{where}: 不認識的槽位 {name!r}（只有 {'／'.join(SLOT_NAMES)}）")
        if not isinstance(spec, dict):
            raise SlotError(f"{where}: slots.{name} 要是物件（{{key, event?, name_jp?, name_cn?}}）")
        for k in spec:
            if k not in SLOT_FIELDS:
                raise SlotError(f"{where}: slots.{name}.{k} 不認識（只有 {'／'.join(SLOT_FIELDS)}）")
        merged = out[name]
        for k, v in spec.items():
            if k in ("key", "event"):
                if not isinstance(v, str) or not v.strip():
                    raise SlotError(f"{where}: slots.{name}.{k} 要是非空字串")
                if k == "event" and name == "coin":
                    # 貨幣沒有專門的事件型別（走 `coin_changed`／`shop_buy`）——宣告它沒有意義，直接擋
                    raise SlotError(f"{where}: slots.coin 不需要 event（貨幣走 coin_changed／shop_buy）")
                merged[k] = v.strip()
            else:
                merged[k] = str(v)
    return out


def load(module_doc: Optional[Dict[str, Any]] = None,
         scenario_doc: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, str]]:
    """合出這一桌的槽位表：預設 ← 模組級 `module.json` ← 劇本級 `scenario.json`（近的贏）。"""
    out = {k: dict(v) for k, v in SLOT_DEFAULTS.items()}
    out = _merge(out, (module_doc or {}).get("slots"), "module.json")
    out = _merge(out, (scenario_doc or {}).get("slots"), "scenario.json")
    return out


def load_for(def_dir: str, scenario_doc: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, str]]:
    """從**定義目錄**（`<root>/modules/<模組>/scenarios/<劇本>`）往上找 `module.json`，再合劇本層。"""
    module_doc: Dict[str, Any] = {}
    try:
        # def_dir = <root>/modules/<模組>/scenarios/<劇本>
        mod_dir = os.path.dirname(os.path.dirname(os.path.abspath(def_dir)))
        p = os.path.join(mod_dir, "module.json")
        with open(p, "r", encoding="utf-8") as fh:
            got = json.load(fh)
        if isinstance(got, dict):
            module_doc = got
    except (OSError, ValueError):
        module_doc = {}
    return load(module_doc, scenario_doc)


def key_of(slots: Dict[str, Dict[str, str]], name: str) -> str:
    """槽位名 → 實際欄位名（state／投影／事件的 key）。"""
    spec = (slots or {}).get(name) or SLOT_DEFAULTS.get(name) or {}
    return str(spec.get("key") or SLOT_DEFAULTS.get(name, {}).get("key") or name)


def event_of(slots: Dict[str, Dict[str, str]], name: str) -> str:
    """槽位名 → 事件型別（貨幣沒有事件型別，回 `coin_changed`）。"""
    spec = (slots or {}).get(name) or SLOT_DEFAULTS.get(name) or {}
    if name == "coin":
        return "coin_changed"
    return str(spec.get("event") or SLOT_DEFAULTS.get(name, {}).get("event") or name)


def event_aliases(slots: Dict[str, Dict[str, str]], name: str) -> tuple:
    """**認得的**事件型別：模組宣告的名字 ＋ 預設名字（舊局的 `mob_killed` 一定要認）。

    事件流是 append-only 的資料契約 ⇒ 換了名字之後，舊 run 重放仍要對得上。
    """
    got = [event_of(slots, name), SLOT_DEFAULTS.get(name, {}).get("event") or name]
    return tuple(dict.fromkeys(x for x in got if x))
