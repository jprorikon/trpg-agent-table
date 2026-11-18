"""resolvers（自 __init__.py 拆出）"""

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

from .common import GM_OPS, REST_OPS
from .context import _who_id
from .queries import _r_health, _r_me, _r_modules, _r_t_capabilities, _r_t_entry, _r_t_log, _r_t_map, _r_t_scalar, _r_t_session, _r_t_tools, _r_t_view, _r_table, _r_tables, _table_archived, _table_owner
from .mutations import (_m_create_char, _m_create_table, _m_gm_run, _m_new_run,
                        _m_request_gm, _m_set_archived, _m_set_paused,
                        _m_set_watchable, _mut)

RESOLVERS: Dict[str, Dict[str, Any]] = {
    "Query": {"tables": _r_tables, "modules": _r_modules, "health": _r_health,
              "table": _r_table, "me": _r_me},
    "ModuleInfo": {"id": lambda r, i: r["id"], "name": lambda r, i: r["name"],
                   "tableCount": lambda r, i: r["tableCount"],
                   "scenarios": lambda r, i: r["scenarios"]},
    "ScenarioInfo": {"id": lambda r, i: r["id"], "name": lambda r, i: r["name"],
                     "tableCount": lambda r, i: r["tableCount"],
                     "tables": lambda r, i: r.get("tables") or [],
                     "difficulties": lambda r, i: r.get("difficulties") or []},
    "DifficultyInfo": {"id": lambda r, i: r["id"], "label": lambda r, i: r["label"],
                       "label_jp": lambda r, i: r.get("label_jp") or r["label"],
                       "doc": lambda r, i: r.get("doc") or "",
                       "default": lambda r, i: bool(r.get("default"))},
    "TableInfo": {"id": lambda r, i: r["id"], "module": lambda r, i: r["module"],
                  "scenario": lambda r, i: r["scenario"], "name": lambda r, i: r["name"],
                  "online": lambda r, i: r["online"], "ended": lambda r, i: r["ended"],
                  "seats": lambda r, i: r["seats"], "events": lambda r, i: r["events"],
                  "seatCap": lambda r, i: r.get("seatCap"),
                  "watchable": lambda r, i: r["watchable"], "session": lambda r, i: r["session"],
                  "owner": lambda r, i: r.get("owner"), "isOwner": lambda r, i: bool(r.get("isOwner"))},
    "Table": {"id": lambda r, i: r["id"], "module": _r_t_scalar("module"),
              "scenario": _r_t_scalar("scenario"), "name": _r_t_scalar("name"),
              "online": _r_t_scalar("online"), "ended": _r_t_scalar("ended"),
              "seats": _r_t_scalar("seats"), "events": _r_t_scalar("events"),
              "watchable": _r_t_scalar("watchable"), "session": _r_t_session,
              "capabilities": _r_t_capabilities, "tools": _r_t_tools,
              "owner": lambda r, i: _table_owner(r),
              "archived": lambda r, i: _table_archived(r),
              "isOwner": lambda r, i: bool(_table_owner(r) and _table_owner(r) == _who_id(i.context)),
              "view": _r_t_view, "log": _r_t_log,
              "map": _r_t_map, "entry": _r_t_entry},
    "Mutation": {n: _mut(n) for n in list(REST_OPS) + list(GM_OPS) + ["login"]},
    # `me` 的 resolver 把清單放在 `_memberships`（底線＝內部鍵）；以前沒接這一格 ⇒ `me { tables }` 一律
    # 「Cannot return null for non-nullable field Identity.tables」（2026-10-19 大廳改版時發現）。
    "Identity": {"tables": lambda r, i: r.get("_memberships") or []},
}

# ⚠ **非表內操作**：`newRun` 生出新 session（所以不帶 session）、且要在前台層做 ⇒ 不走 `_mut`。
RESOLVERS["Mutation"]["newRun"] = _m_new_run
RESOLVERS["Mutation"]["gmRun"] = _m_gm_run
RESOLVERS["Mutation"]["createTable"] = _m_create_table
RESOLVERS["Mutation"]["setWatchable"] = _m_set_watchable
RESOLVERS["Mutation"]["setArchived"] = _m_set_archived
RESOLVERS["Mutation"]["setPaused"] = _m_set_paused
# ⚠ `createChar` 也不走 `_mut`：它是**兩步寫入**（綁席位 ＋ 開實例）＋有分工拒絕。
RESOLVERS["Mutation"]["createChar"] = _m_create_char
# ⚠ `requestGm` 也不走 `_mut`：它叫的是桌端的喚醒那一支（`wake.maybe_kick_round`）。
RESOLVERS["Mutation"]["requestGm"] = _m_request_gm
