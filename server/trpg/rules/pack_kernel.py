#!/usr/bin/env python3
"""**L1｜機制包 ↔ 內核的對照面**——「包只能宣告內核真的有的東西」那些表與核對。

從 `packs.py` 切出來（2026-10-06 結構整理：單檔別過長）。分工：
  * `packs.py`      包**本身**：schema、註冊表、解析（拓撲／衝突／缺依賴）、合併視圖。
  * **這一支**      包**與內核的對照**：已實作池（`KERNEL_*`）、實作核對（`verify_impl`）、
                    以及兩個「把包併進既有白名單」的薄函式（動作目錄／公開日誌白名單）。

依賴方向：`packs.py → pack_kernel.py`（單向；這裡**不 import packs**，`view` 是鴨子型別）。
這一支不讀時鐘、不隨機、不碰網路；只讀原始碼做字面核對。

❗ 這幾張池子與實作**必須同步**（`test_packs.py` P3 組逐項咬住，漂移＝當場紅）：
   `KERNEL_EFFECTS` ↔ `scenario.EFFECT_KINDS`／`KERNEL_SIGNALS` ↔ `director.signals()`／
   `KERNEL_STATE_KEYS` ↔ `state_default()`＋board／semic 的初始值。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from trpg.rules import scenario as scenario          # L1 內單向：只為拿「已實作 effect 的池子」（唯一正本在 scenario）
from trpg.paths import ROOT

#: 包的「動作／事件有沒有實作」的核對來源：**內核（含可選機制）＋L6 適配器**的原始碼
#: （字面比對，不猜）。加了新的機制檔（例：`conditions.py`）就要登記進來，否則包宣告它會被擋。
IMPL_SOURCES: Tuple[str, ...] = ("trpg/engine", "trpg/rules/board", "trpg/rules/semic",
                                 "trpg/service/wake", "trpg/rules/conditions.py",
                                 "trpg/rules/dsl.py", "trpg/rules/skills.py",
                                 "trpg/rules/advancement.py", "trpg/rules/items.py",
                                 "trpg/rules/stars.py", "trpg/rules/rescue.py",
                                 "trpg/rules/shop.py", "trpg/rules/monsters.py",
                                 "trpg/rules/death.py", "trpg/rules/lands.py")


class PackError(ValueError):
    """包本身／啟用清單不合法（**載入時就叫**，不拖到跑團中間）。"""


# ---------------------------------------------------------------------------
# 內核的「已實作池」——包只能宣告這裡面的東西。
# 這幾張表與實作**必須同步**：`test_packs.py` P3 組逐項咬住（漂移＝當場紅）。
# ---------------------------------------------------------------------------

#: 已實作的 effect（正本在 `scenario.EFFECT_KINDS`，這裡只是轉手）
KERNEL_EFFECTS: Tuple[str, ...] = tuple(scenario.EFFECT_KINDS)

#: 內核認得的節拍值（**新的節拍＝工程先做**，例如之後要加的 DND 先攻制）
KERNEL_MODES: Tuple[str, ...] = ("turn",)   # 2026-10-21 設計者定：引擎只跑回合制，scene 節拍停用

#: 包可以**補預設值**的 config 段（`provides.defaults.<段>`；段名寫錯＝載入時就叫）。
#: 判據：這些段是「機制常數」（換一張桌還會一樣），不是這一桌的內容。
KERNEL_SECTIONS: Tuple[str, ...] = ("rules", "board", "semi", "tower", "memory", "pacing", "turn", "view")

#: L3 導演算得出的指標（正本在 `director.signals()`；P3 咬住兩邊一致）
KERNEL_SIGNALS: Tuple[str, ...] = (
    "hands", "progress", "stall_hands", "idle_hands", "idle_streak", "recent_idle",
    "progress_recent", "window", "move_lag", "rounds", "players", "agents",
    "down", "difficulty",
)

#: state 的頂層欄位（正本在 `state_default()` ＋ board／semic 的初始值；P3 咬住不漂）
KERNEL_STATE_KEYS: Tuple[str, ...] = (
    "seats", "order", "turn", "log", "secrets", "roll_seq", "turn_seq",
    "turn_index", "last_seat", "round_no", "round_start_seq", "paused", "paused_at",
    "facts", "clocks", "difficulty", "ended",      # clocks＝時鐘（clock_tick／clock_set；2026-10-20）
    "board", "semi", "run_id",
    "conditions",           # 部位受傷／異常狀態（conditions.py；沒啟用帶表的包＝永遠空的）
    "attr_mods",            # 屬性加值（dsl.py；M2 原語）
    "resources",            # 通用資源（dsl.py；M2 原語）
    "exp",                  # 經驗（advancement.py；M4）
    "level",                # 等級（advancement.py；M4）
    "inventory",            # 道具庫存（items.py；★2）
    "star",                 # 星等（stars.py；升星＝劇情事件觸發）
    "star_ch",              # 星等**渠道計數**（stars.py；shop／goal／side 各漲幾次；2026-10-20）
    "skip",                 # 死亡送還：seat -> 還要被跳過幾個回合（death.py）
    "audio",                # 音訊現況（audio.py：bgm／sfx 兩個聲道＋seq；2026-10-20）
)

#: `provides` 裡「要對池子」的三格（事件不算在內：事件由 `verify_impl` 對原始碼核字面）
POOLS: Dict[str, Tuple[str, ...]] = {"effects": KERNEL_EFFECTS, "signals": KERNEL_SIGNALS,
                                     "state_keys": KERNEL_STATE_KEYS}


def verify_impl(view: PackView, *, root: Optional[str] = None) -> None:
    """宣告的**動作／事件**必須在內核（＋L6 適配器）原始碼裡真的找得到。

    這是「包不能發明機制」那條紀律的機械版：宣告了一個沒人實作的動作，玩家一送就變成
    「什麼都沒發生的一手」——寧可在載入時就叫。新機制＝工程先在內核接上，再回包裡宣告。
    """
    root = root or ROOT
    text = ""
    for name in IMPL_SOURCES:
        p = os.path.join(root, name)
        if os.path.isdir(p):
            for dirpath, _dirs, files in os.walk(p):
                for fn in sorted(files):
                    if fn.endswith(".py"):
                        with open(os.path.join(dirpath, fn), "r", encoding="utf-8") as fh:
                            text += fh.read()
        elif os.path.exists(p):
            with open(p, "r", encoding="utf-8") as fh:
                text += fh.read()
    missing_act = [a["id"] for a in view.actions if f'"{a["id"]}"' not in text]
    missing_ev = [e for e in view.events if f'"{e}"' not in text]
    if missing_act or missing_ev:
        raise PackError(
            "包宣告了內核沒有實作的東西（包不能發明機制）："
            f"動作={missing_act} 事件={missing_ev}；"
            f"要新機制請先請工程做成「資料可配」")


def effective_action_entries(raw_actions: Any, view: Optional[PackView] = None,
                             bank: Optional[Dict[str, str]] = None) -> List[Any]:
    """**有效動作目錄**＝`config.rules.actions` ＋ 機制包宣告的動作（並集）。

    config 的那份**文案優先**（同一 id 以 config 為準）：包宣告的是「我需要這個動作」，
    要不要開、怎麼顯示仍是這張桌的 config 說了算。
    `bank`＝這一桌的句庫：**包宣告的**動作若有 `action.<id>.label`／`action.<id>.desc` 就換成那一句
    （引擎包只寫中性措辭，作品的叫法由模組的 `text.json` 給；config 寫的照舊不動）。
    """
    out: List[Any] = list(raw_actions or [])
    have = set()
    for it in out:
        have.add(str(it.get("id") if isinstance(it, dict) else it))
    for ent in (view.actions if view is not None else []):
        if ent["id"] not in have:
            ent = dict(ent)
            for f in ("label", "desc"):
                if bank and f"action.{ent['id']}.{f}" in bank:
                    ent[f] = bank[f"action.{ent['id']}.{f}"]
            out.append(ent)
            have.add(ent["id"])
    return out


def log_types_for(view: Optional[PackView], kernel: Tuple[str, ...]) -> Tuple[str, ...]:
    """公開日誌白名單＝內核白名單 ＋ 包宣告的事件（**只增不減**）。"""
    out = list(kernel)
    for e in (view.events if view is not None else ()):
        if e not in out:
            out.append(e)
    return tuple(out)
