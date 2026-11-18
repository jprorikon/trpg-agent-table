"""共用常數／工具（自 __init__.py 拆出）。"""

from __future__ import annotations
import json
import os
import random
from typing import Any, Dict, List, Optional, Sequence

# 2026-10-21（設計者定案）：進塔／上塔／下塔／出塔**全部收束成 move**——
# 動作表只留 move／search／fight／pray／buy／rest／game（舊的 enter／ride／exit_tower 不再對外宣告，
# 引擎內部仍接受同義呼叫，讓舊客戶端不會一按就爆）。
TOTAL_ACTIONS = ("move", "search", "fight", "pray", "buy", "rest", "game")

