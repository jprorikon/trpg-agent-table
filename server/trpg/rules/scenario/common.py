"""common（自 __init__.py 拆出）"""

from __future__ import annotations
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple
from trpg.rules import expr as expr
from trpg.engine import presentation as presentation

MAX_ROUNDS = 32          # 跑到定點的回合上限（防作者寫出互相觸發的迴圈）

MODULE_FILES = ("scenario.json", "module.json")     # **新名 → 舊名**（WS-B 改名；相容期都吃）

EFFECT_KINDS = ("emit", "fact_set", "ending", "difficulty_set",
                "condition_set",     # 部位受傷／異常狀態（機制住 conditions.py；內容住包的表）
                "attr_mod",          # 屬性加值（機制住 dsl.py；M2 原語）
                "resource_add",      # 通用資源：加減（dsl.py；下限 0）
                "resource_set",      # 通用資源：設成某值（dsl.py）
                "damage",            # 傷害（走引擎的命中／傷害算式；M3）
                "heal",              # 治療（走引擎的 hp 算式；M3）
                "item_grant",        # 給道具（機制住 items.py；★2）
                "item_take",         # 拿走道具（items.py）
                "star_set",          # 當場給星（機制住 stars.py；劇情自己判的升星走這條）
                "star_grant",        # ★ 給「某個渠道」的星（買星／完成任務／全體副任務；渠道由劇本宣告）
                "growth_gain",       # 給成長值（＝升級的燃料；讓劇本決定「什麼事值得成長」）
                "clock_tick",        # ★ 時鐘：填／退一格（2026-10-20；正面對「世界自己會走」）
                "clock_set",)        # ★ 時鐘：設成某值

EFFECT_SPECS: Dict[str, Dict[str, Any]] = {
    "emit":           {"required": ("type",), "optional": ("text_key", "facts")},
    "fact_set":       {"required": ("key",), "optional": ("value",)},
    "ending":         {"required": ("id",), "optional": ("kind",)},
    "difficulty_set": {"required": ("name",), "optional": ()},
    "condition_set":  {"required": ("id",), "optional": ("seat", "value", "turns", "note")},
    "attr_mod":       {"required": ("attr", "value"), "optional": ("seat", "note")},
    "resource_add":   {"required": ("name", "value"), "optional": ("seat", "note")},
    "resource_set":   {"required": ("name", "value"), "optional": ("seat", "note")},
    "damage":         {"required": ("seat",), "optional": ("die", "attr", "bonus", "note", "ally_bonus")},
    "heal":           {"required": ("seat",), "optional": ("die", "bonus", "note")},
    "item_grant":     {"required": ("id",), "optional": ("seat", "count", "note")},
    "item_take":      {"required": ("id",), "optional": ("seat", "count", "note")},
    "star_set":       {"required": (), "optional": ("seat", "star", "note")},
    # ★ 渠道給星：channel 必填（劇本 stars 表的 channels 名）；scope="all" ⇒ 全桌每人各加
    "star_grant":     {"required": ("channel",), "optional": ("value", "seat", "scope", "note")},
    "growth_gain":    {"required": ("amount",), "optional": ("seat", "note")},
    "clock_tick":     {"required": ("name",), "optional": ("value", "note")},
    "clock_set":      {"required": ("name", "value"), "optional": ("note")},
}

NUMERIC_PRESET_KEYS = ("dc_mod", "danger_mod", "actions_per_act", "says_per_scene")
