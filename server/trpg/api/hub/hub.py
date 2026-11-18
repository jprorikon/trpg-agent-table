"""hub（自 __init__.py 拆出）"""

from __future__ import annotations
import argparse
import atexit
import json
import os
import secrets
import socket
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from trpg.paths import ROOT as HERE  # repo 根（資料／vendor／docs 都在這裡）
from trpg.kick import receiver as kick_receiver                                                      # noqa: E402
from trpg.core import layout as layout                                                             # noqa: E402
from trpg.service import table_directory as tdir                                            # noqa: E402
from trpg.service import table_factory as table_factory                                                      # noqa: E402
from trpg.api import web as web_api                                                            # noqa: E402
from trpg.api.web import Handler as WebHandler                                 # noqa: E402

from .common import _pick_port, _rewrite_prefix, _seat_cap, _seat_counts

from .core import HubCoreMixin
from .runs import HubRunsMixin
from .tokens import HubTokensMixin

class Hub(HubCoreMixin, HubRunsMixin, HubTokensMixin):
    """這台機器上的桌（**一個進程一份**）：services／tick 執行緒／組裝設定。"""
    root = HERE
    services: Dict[str, Any] = {}
    runs: Dict[str, Dict[str, Any]] = {}          # run(＝session) → {tid, module, scenario, home, current}
    rows: Dict[str, Dict[str, Any]] = {}          # 桌 → discover 的原始列（scenario／cast／watch_port…）
    errors: Dict[str, str] = {}                   # 桌 → 組裝失敗原因（不上榜但看得見）
    _ticks: Dict[str, threading.Thread] = {}
    _stops: Dict[str, threading.Event] = {}
    lock = threading.RLock()                      # 只有「加桌／reload／rescan」會用到
    admin_token = ""
    token_file = ""
    gm_token = ""                                 # `/mcp` 的 GM 身分（最小權限；見 ensure_gm_token）
    gm_token_file = ""
