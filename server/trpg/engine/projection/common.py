"""common（自 __init__.py 拆出）"""

from __future__ import annotations
from typing import Any, Dict, List, Optional

VIEW_LOG_TYPES = (
    "seat_joined", "say", "action_declared", "roll",
    "damage", "heal", "status_set", "turn_started", "turn_ended",
    "turn_extended", "seat_timeout", "table_created",
    # ★ 2026-10-21 拆景：輪界（`round_started`／`round_ended`）接手原本 `scene_open`／
    #   `scene_close` 的位置——公開事件（人要看得懂「這一輪開始了」）。
    "round_started", "round_ended",
    # ⚠ `kick`／`seat_timeout` **仍在白名單裡**（它們是**包宣告**的機制事件，動這裡會破
    #   `test_packs` 的結構不變式）——但**永遠不會 public**（2026-10-07 玩家平等原則：
    #   只有 agent 席位會被叫醒、也只有人／agent 的差別讓「被跳過」有意義）。
    #   真正的閘是**發射點**：`wake.py` 兩處 kick、`pacing.py` 兩處 seat_timeout 都寫 `public=False`；
    #   行為閘＝`test_player_equality` B 組（把 public 改回來會當場紅）。
    "move", "entrance_found", "entered_tower", "elevator", "mob_killed",
    "boss_defeated", "artifact_gained", "memory_change", "tower_exit",
    "white_room", "assimilated", "shop_buy", "search_loot", "kick",
    "declare", "declare_resolved", "declare_won",
    "declare_failed", "fragment_granted", "fragment_seen", "fragment_given",
    "wish_done", "settle", "gm_say", "gm_leak_blocked",
    "gm_roll_marker",
    # ★ 音訊（2026-10-20）：播放控制（`audio` 事件）進公開日誌——人要看得懂「這一段為什麼在放這首」。
    #   前端真正的播放依據是投影的 `audio` 現況（含 seq），不是日誌。
    "audio",
)

SCRUB_TYPES = ("fragment_granted", "fragment_seen", "fragment_given")

PLAYER_KINDS = ("human", "agent")
