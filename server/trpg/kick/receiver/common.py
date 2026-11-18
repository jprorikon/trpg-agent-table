"""common（自 __init__.py 拆出）"""

import argparse
import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import quote
from trpg.paths import ROOT as HERE  # repo 根（資料／vendor／docs 都在這裡）

LOG_LOCK = threading.Lock()

def log_line(path: str, text: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{stamp} {text}"
    with LOG_LOCK:
        print(line, flush=True)
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass

def read_api_key(api_key_file: str, name: str = "HERMES_API_KEY") -> str:
    """读一把 Bearer。`name` 可换：次级 profile 用的是它自己的 `API_SERVER_KEY`
    （环境变量优先，其次档案）。

    ★ 2026-10-24（review R3）：**档案不存在 ⇒ 当作没有**（回 `""`）。
    从前的 `open()` 没有兜底 ⇒ 新部署／别人的 clone（默认那把
    `<repo>/../docker/agent-bridge/.env` 根本不在那里）会在构造接收器时
    直接 FileNotFoundError，整支 kick 起不来。空的 key 在 api_server 上自然会 401
    ——那是清楚的失败，比「进程起不来」好查。
    """
    key = os.environ.get(name, "").strip()
    if key:
        return key
    try:
        with open(api_key_file, encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if raw.startswith(name + "="):
                    return raw.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        return ""
    return ""

DEFAULT_API_KEY_FILE = os.path.join(HERE, "..", "docker", "agent-bridge", ".env")

def profile_env_path(profile: str) -> str:
    """次级 profile 的 .env 路径（放它的 API_SERVER_KEY）。"""
    home = (os.environ.get("TRPG_HERMES_HOME") or os.environ.get("HERMES_HOME")
            or "/srv/hermes")
    return os.path.join(home, "profiles", profile, ".env")

def session_name_for(payload: dict, table_id: str, tpl: str) -> tuple:
    """依 kick body 決定**會話名**（一個 session 對應一個副本實例；設計者 2026-10-07）。

    優先序（與 協作端 對齊：同一顆 body 餵兩邊要落進同一個 session 名）：
      1. `session_name`（新引擎送的**這一副本專屬**會話名）——直接用。
      2. `session` 看起來像會話名（含 `-` 且不是副本 id 那種 `rXXXXXX`）⇒ 舊制相容。
      3. 都沒有 ⇒ 用模板 `trpg-{table}-{session}-gm`（`{session}` 代副本 id）。
    回 `(session, 來源說明)`。
    """
    _sname = str(payload.get("session_name") or "").strip()
    _sess = str(payload.get("session") or "").strip()
    if _sname:
        return _sname, "body.session_name"
    if _sess and ("-" in _sess) and not _sess.startswith("r"):
        return _sess, "body.session（舊制會話名）"
    return (str(tpl).replace("{table}", table_id).replace("{session}", _sess or "r0"), "模板")
