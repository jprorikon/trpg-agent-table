"""common（自 __init__.py 拆出）"""

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

_VENDOR = os.path.join(HERE, "vendor")

SCHEMA_PATH = os.path.join(HERE, "docs", "schema.graphql")

_SCHEMA = None

SESSION_EXEMPT = ("login",)

GM_OPS = {
    "gmSecret": "secret", "gmStartTurn": "start_turn",
    "gmEndTurn": "end_turn", "gmHeal": "heal", "gmKick": "kick", "gmReplay": "replay",
    "gmPause": "pause", "gmResume": "resume", "gmExp": "exp", "gmStar": "star",
    "gmItem": "item", "gmCondition": "condition", "gmAttrMod": "attr_mod",
    "gmResource": "resource", "gmRollFor": "roll_for", "gmActFor": "act_for",
    "gmNpcSay": "npc_say", "gmLeakReport": "leak_report",
}

ARG_ALIASES: Dict[str, Dict[str, str]] = {"pickChar": {"charId": "char"}}

REST_OPS = {
    "pickChar": "/api/pick_char", "enter": "/api/enter", "join": "/api/join",
    "act": "/api/act", "say": "/api/say", "tell": "/api/tell",
    "tellGm": "/api/tell_gm", "typing": "/api/typing",
}

ARG_ALIAS = {"tableId": "table_id", "charId": "char_id", "wakeUrl": "wake_url"}

def schema():
    """契約（`docs/schema.graphql`）——**唯一正本**，載入一次。"""
    global _SCHEMA
    if _SCHEMA is None:
        with open(SCHEMA_PATH, encoding="utf-8") as fh:
            _SCHEMA = build_schema(fh.read())
    return _SCHEMA

def _tid(svc: Any) -> str:
    return str((svc.cfg or {}).get("table_id") or "")

def _count_events(home: str) -> int:
    p = store.events_path(home)
    try:
        with open(p, encoding="utf-8") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0
