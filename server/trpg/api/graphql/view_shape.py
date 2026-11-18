"""_view_shape（自 queries 拆出）。"""

from __future__ import annotations
import json
from urllib.parse import quote
import os
import sys
from typing import Any, Dict, List, Optional, Tuple
from trpg.paths import ROOT as HERE  # repo 根（資料／vendor／docs 都在這裡）
from graphql import GraphQLError, build_schema, graphql_sync          # noqa: E402
from trpg.service import assets as module_assets                                         # noqa: E402
from trpg.core import layout as layout                                                          # noqa: E402
from trpg.engine import projection as projection                                                      # noqa: E402
from trpg.core import store as store                                                           # noqa: E402
from trpg.accounts import auth as auth
from trpg.service import table_directory as tdir                                         # noqa: E402
from .common import _tid
from .context import Ctx, _anon_watch, _need_session, _table_info, _watch_of, call


def _view_shape(proj: Dict[str, Any], *, session: str) -> Dict[str, Any]:
    """把 `/api/state` 的投影**逐欄型別化**成 `View`（欄名 snake_case ⇒ GraphQL camelCase）。

    ⚠ **契約不縮水**：沒型別化的部分（棋盤／結局／摘要／回合／舞台／typing…）一律原樣留在 `raw`，
    那也正是 MCP 工具的輸出契約 ⇒ 兩個面看到的世界一模一樣。
    """
    t = proj.get("table") or {}
    a = proj.get("auth") or {}
    you = proj.get("you")
    return {
        "session": str(session),
        "table": {"tableId": str(t.get("table_id") or ""), "scenario": str(t.get("scenario") or ""),
                  "rules": str(t.get("rules") or ""),
                  "now": float(t.get("now") or 0.0),
                  # ★ 素材（2026-10-09；純附加）：**只有這一模組有素材表**時才非 null。
                  #   素材是模組級的（`modules/<模組>/assets/`）⇒ 同模組的多張桌共用同一份。
                  "module": t.get("module"), "assets": t.get("assets") or None},
        "paused": bool(proj.get("paused")),
        "pausedSeconds": float(proj.get("paused_seconds") or 0.0),
        "seats": [{"seat": str(s.get("seat") or ""), "name": s.get("name"), "kind": s.get("kind"),
                   "hp": s.get("hp"), "maxHp": s.get("max_hp"), "status": list(s.get("status") or []),
                   "isTurn": bool(s.get("is_turn")), "star": s.get("star"), "level": s.get("level"),
                   "resources": s.get("resources") or {},
                   # ★ 素材（2026-10-09；純附加）：沒有素材的模組 ⇒ null／空
                   "portrait": s.get("portrait"), "avatar": s.get("avatar"),
                   "statusIcons": s.get("status_icons") or {}} for s in (proj.get("seats") or [])],
        "you": None if not you else {
            "seat": str(you.get("seat") or ""), "name": you.get("name"), "kind": you.get("kind"),
            "hp": you.get("hp"), "maxHp": you.get("max_hp"), "node": you.get("node"),
            "memory": you.get("memory"), "maxMemory": you.get("max_memory"),
            "maxMana": you.get("max_mana"), "starCoins": you.get("star_coins"),
            "attrs": dict(you.get("attrs") or {}), "status": list(you.get("status") or []),
            "resources": you.get("resources") or {}, "skills": list(you.get("skills") or []),
            "artifacts": list(you.get("artifacts") or []), "roster": you.get("roster"),
            # ★ 素材（2026-10-09；純附加）：立繪／頭像／道具清單／狀態圖示
            "portrait": you.get("portrait"), "avatar": you.get("avatar"),
            "items": list(you.get("items") or []), "statusIcons": you.get("status_icons") or {}},
        "log": [{"seq": int(e.get("seq") or 0), "ts": float(e.get("ts") or 0.0),
                 "type": str(e.get("type") or ""),
                 "data": {k: v for k, v in e.items() if k not in ("seq", "ts", "type")}}
                for e in (proj.get("log") or [])],
        "actions": [{"id": str(x.get("id") or ""), "label": x.get("label"), "desc": x.get("desc")}
                    for x in (proj.get("actions") or [])],
        "allowedActions": list(proj.get("allowed_actions") or []),
        "rulesAttrs": list(proj.get("rules_attrs") or []),
        "yourSecrets": list(proj.get("your_secrets") or []),
        "recent": list(proj.get("recent") or []),
        "turn": proj.get("turn"),
        "stage": proj.get("stage"),
        "settled": proj.get("settled"),
        "board": proj.get("board"),
        # ★ 音訊（2026-10-20；純附加）：只有模組放了 `audio.json` 才非 null。
        #   `{bgm, sfx, seq, library, (GM: available, cues)}`——前端據 seq 換曲／播提示。
        "audio": proj.get("audio"),
        "ended": proj.get("ended"),
        "endedInfo": proj.get("ended_info"),
        "summary": proj.get("summary"),
        "typing": proj.get("typing"),
        "auth": {"hasAccounts": bool(a.get("has_accounts")), "required": bool(a.get("required")),
                 "seatLink": bool(a.get("seat_link"))},
        "raw": proj,
    }
