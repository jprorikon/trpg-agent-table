"""tokens（自 Hub 拆出）。"""

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


class HubTokensMixin:
    @classmethod
    def ensure_token(cls) -> str:
        if cls.admin_token:
            return cls.admin_token
        p = os.path.join(cls.root, "logs", "hub.token")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        try:
            if os.path.exists(p):
                cls.admin_token = open(p, encoding="utf-8").read().strip()
        except OSError:
            cls.admin_token = ""
        if not cls.admin_token:
            cls.admin_token = secrets.token_urlsafe(24)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(cls.admin_token + "\n")
            os.chmod(p, 0o600)
        cls.token_file = p
        return cls.admin_token


    @classmethod
    def token_ok(cls, token: str) -> bool:
        return bool(cls.admin_token) and secrets.compare_digest(str(token), cls.admin_token)


    @classmethod
    def ensure_gm_token(cls) -> str:
        if cls.gm_token:
            return cls.gm_token
        p = os.path.join(cls.root, "logs", "hub_gm.token")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        try:
            if os.path.exists(p):
                cls.gm_token = open(p, encoding="utf-8").read().strip()
        except OSError:
            cls.gm_token = ""
        if not cls.gm_token:
            cls.gm_token = secrets.token_urlsafe(24)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(cls.gm_token + "\n")
            os.chmod(p, 0o600)
        cls.gm_token_file = p
        return cls.gm_token


    @classmethod
    def gm_token_ok(cls, token: str) -> bool:
        return bool(cls.gm_token) and secrets.compare_digest(str(token), cls.gm_token)

