#!/usr/bin/env python3
"""棋盘（board）机制 —— 「地图 + 塔」玩法，挂在引擎上。

设计原则（与引擎一致）：所有数值与判定都在这里由代码产出，模型只能声明动作。
数据来源：campaigns/<work>/board.json（节点/边/事件表/角色模板），全部配置驱动。

本模块由 engine.Table 通过多继承复用；不单独持锁（调用方持引擎的锁）。
"""

from __future__ import annotations

import json
import os
import random

from typing import Any, Dict, List, Optional, Sequence

from .state import BoardStateMixin
from .map import BoardMapMixin
from .act import BoardActMixin



class BoardMixin(BoardStateMixin, BoardMapMixin, BoardActMixin):
    pass


# 轉出：原本住在這一支的常數／工具（實作已搬到 base.py）
from .base import TOTAL_ACTIONS
