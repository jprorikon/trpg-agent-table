"""共用常數／工具（自 __init__.py 拆出）。"""

from __future__ import annotations
import json
import os
import random
from typing import Any, Dict, List, Optional
from trpg.core.errors import RuleError  # 与 engine.RuleError 同一个类（本模块不 import engine，避免循环依赖）

PLAYER_ACTIONS = ("declare", "show", "give", "wish", "pass")


RuleError_local = RuleError

