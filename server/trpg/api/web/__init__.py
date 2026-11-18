#!/usr/bin/env python3
"""**L6｜HTTP 介面**——人類網頁 + REST 兜底 + MCP over HTTP（`Handler`）。

從 `server.py` 切出來（2026-10-06 結構整理）。`Handler.service` 由 `server.main()`
起服務時指派（class attribute ⇒ 不靠全域可變狀態傳遞）。

紀律：路由**只做參數搬運與回碼**——判斷與裁決一律在 `TableService`／引擎；
回應一律 `Cache-Control: no-store`（否則玩家可能拿到舊頁面，見 §6.15）。
"""
from __future__ import annotations

import hmac
import json

from trpg.accounts import account_store as account_store                     # 挑後端：有設 DB ⇒ 關聯式；沒設 ⇒ JSON registry（legacy）
from trpg.service import assets as assets                            # L6 資產層：模組級素材表（`modules/<模組>/assets/`）
from trpg.accounts import auth as auth                              # L6 account 認證層（token 是 account token，不是 seat token）
from trpg.core import layout as layout                            # L6 桌的位置（素材面的 repo 根由這裡解）
from trpg.service import table_directory as tdir           # L6 掃桌／探活（判斷「角色還在別張活桌出場」）
import email.utils
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from trpg.core.errors import RuleError, TellError
from trpg.service.wake import maybe_kick_round, round_kick_key

























if TYPE_CHECKING:                       # 只給型別註解；執行期不 import（免循環）
    from trpg.service.service import TableService

from trpg.paths import ROOT as HERE  # repo 根（資料／vendor／docs 都在這裡）

from .get import HandlerGetMixin
from .post import HandlerPostMixin




class Handler(BaseHTTPRequestHandler, HandlerGetMixin, HandlerPostMixin):
    protocol_version = "HTTP/1.1"
    service: TableService = None  # type: ignore
    WATCH_GET = ("/api/state", "/api/log", "/api/map", "/health")


# ---------------------------------------------------------------------------


# 轉出：原本住在這一支的常數／工具（實作已搬到 base.py）
from .base import _secret_eq, _watch_token_ok, _auth_info, _is_api_path, _legacy_rest_ok, _watch_json, _maybe_writeback, _chars_available, _pick_char, _busy_tables, _busy_msg, _accounts_of, asset_response
