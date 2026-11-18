"""common（自 __init__.py 拆出）"""

from __future__ import annotations
import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List
from trpg.core import layout as layout                                  # 桌的位置／模組命名（與桌子共用同一支）
from trpg.service import table_directory as tdir                 # L6：掃桌／探活／收局判定（與桌子共用同一支）
from trpg.paths import ROOT as HERE  # repo 根（資料／vendor／docs 都在這裡）

LOBBY: Dict[str, Any] = {"root": HERE, "timeout": 1.5}
