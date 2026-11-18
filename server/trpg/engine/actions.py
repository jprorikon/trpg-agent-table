"""actions（自 Table 拆出）。"""

from __future__ import annotations
import json
import random
import threading
import time
import uuid
from typing import Any, Dict, List, Optional
from trpg.rules import packs as packs               # L1 機制包（模組邊界宣告＋啟用清單＋合併視圖）
from trpg.rules import conditions as conditions          # L1 條件（部位受傷／異常狀態；內容在包的表）
from trpg.rules import dsl as dsl                 # L1 動作 DSL 的兩個原語（屬性加值／通用資源；M2）
from trpg.rules import skills as skills              # L1 技能表＋cast 檢定鏈（M3；內容在包的表）
from trpg.rules import advancement as advancement         # L1 經驗／等級（M4；內容在包的表）
from trpg.rules import items as items               # L1 道具／庫存（★2；內容在包的表）
from trpg.rules import stars as stars               # L1 星等／升星（2026-10-07：升星＝劇情事件觸發，不是數字門檻）
from trpg.rules import rescue as rescue              # L1 救起／救護（★1：把倒地的人拉回來——一個檢定＋一張表）
from trpg.rules import shop as shop                # L1 商店（★2：賣什麼＝一張表；買到＝真的進 inventory）
from trpg.rules import monsters as monsters            # L1 遭遇／怪物表（★4：這一格遇到誰、多強、打死給什麼）
from trpg.rules import death as death               # L1 死亡與送還（倒下 ⇒ 送回最近的城市 ＋ 缺席 N 回合）
from trpg.engine import director as director            # L3 導演（主線距離／偏離／難度：只提難度檔位）
from trpg.core import pacing as pacing              # L0 節拍閘（暫停／超時／自動開局）
from trpg.rules import scenario as scenario            # L1 劇本模組（module.json：願望判據／結局／觸發／難度）
from trpg.engine import presentation as presentation        # L4 句庫（唯一產人話的地方；L0 只交事實給它）
from trpg.engine import projection as projection          # L2 投影（唯一讀出口；view() 只是薄殼）
from trpg.core import replay as replay              # L0 回放比對（取證用）
from trpg.rules import action_catalog as action_catalog      # L1 動作目錄（config 驅動）
from trpg.core.errors import RuleError  # 规则拒绝的共用异常；与 engine 同一个类（避免 500＋类名前缀）

from .social import TableActionsSocialMixin
from .act import TableActionsActMixin


class TableActionsMixin(TableActionsSocialMixin, TableActionsActMixin):
    pass

