#!/usr/bin/env python3
"""半合作模式（semi-coop）—— 目标层 + 场景制 + 碎片/愿望 + GM 工具面。

与 board.py 一样是挂在 engine.Table 上的 mixin，不含叙事、不含数值生成以外的自由发挥。

设计对应：
  * 场景制（§9.1/§10.1）：GM 开景收景；景内自由行动；**争夺类动作同时宣言、收景揭晓**（并列各骰比大小）
  * agent 节流（§10.2）：agent 席位每景 act/say 有上限，人类不受限
  * 碎片（§2）：祈願的代价以碎片还回桌上，落在非请求者手上；私有投影 + 出示/赠予来源链
  * 私密愿望（§1/§10.5）：长在角色身上，筹码多元（碎片来源 / 先手 / 交涉）
  * 权威分离（§11.1）：GM 只能念引擎结论 + 只能叙述；唯一入口 gm_say 会做**泄漏比对**
  * 泄漏防线（§11.2）：GM/NPC 的发言与未公开的私密文本做字面比对，命中即拦
"""

from __future__ import annotations

import json
import os

import random
from typing import Any, Dict, List, Optional

from trpg.core.errors import RuleError  # 与 engine.RuleError 同一个类（本模块不 import engine，避免循环依赖）

from .state import SemiCoopStateMixin
from .rounds import SemiCoopRoundsMixin
from .limits import SemiCoopLimitsMixin
from .act import SemiCoopActMixin
from .gm import SemiCoopGmMixin



class SemiCoopMixin(SemiCoopStateMixin, SemiCoopRoundsMixin, SemiCoopLimitsMixin, SemiCoopActMixin, SemiCoopGmMixin):
    pass


# 规则拒绝的异常统一用 errors.RuleError（与 engine 同一个类）。
# 本模块早期自带一个 RuleError_local；为兼容旧引用保留这个别名。
RuleError_local = RuleError


# 轉出：原本住在這一支的常數／工具（實作已搬到 base.py）
from .base import PLAYER_ACTIONS, RuleError_local
