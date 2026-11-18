"""reading（自 Table 拆出）。"""

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
from .base import VIEW_LOG_TYPES

class TableReadingMixin:
    def phrase(self, key: str, **facts: Any) -> str:
        """L4 句庫取一句（**這一桌的**那一本：模組可以換掉內核的中性措辭）。各層要人話一律走這支。"""
        return presentation.text(key, self.text_bank, **facts)


    def _name_of(self, sid: Optional[str]) -> str:
        s = self.state["seats"].get(sid or "") or {}
        return s.get("name") or sid or "?"


    def _place_of(self, nid: Optional[str]) -> str:
        n = (self.nodes.get(nid) if hasattr(self, "nodes") else None) or {}
        return n.get("cn") or n.get("jp") or (nid or "?")


    def render_log_tail(self, *, seat: Optional[str] = None, since_seq: Optional[int] = None,
                        limit: int = 16) -> List[str]:
        """公開事件流 → 「人話」補課行（**L4**：`presentation.render_log_tail`）。

        這一層只把 lookup（席位名、地點名）交出去；句子長相一律在 `presentation.py`
        ——「同一個事件在兩處各寫一份措辭」正是這一刀要收掉的東西。

        用途：kick prompt 的「你上次出手之後發生了什麼」digest、`view.recent`。
        碎片正文永不出現——只寫「一枚碎片落到 X 手上」。
        """
        return presentation.render_log_tail(self.state["log"], name_of=self._name_of,
                                            place_of=self._place_of,
                                            since_seq=since_seq, limit=limit, bank=self.text_bank,
                                            slot_events=getattr(self, "slot_event_map", lambda: {})(),
                                            term=self.term_name("fragment"))


    def _recent_lines(self, limit: int = 12) -> List[str]:
        """`view.recent` ＝ L4 的人話尾巴（投影只把它擺進去，不自己產句子）。"""
        return presentation.render_log_tail(self.state["log"], name_of=self._name_of,
                                            place_of=self._place_of, limit=limit,
                                            bank=self.text_bank, slot_events=getattr(self, "slot_event_map", lambda: {})())


    def public_events(self) -> List[Dict[str, Any]]:
        """匿名 `GET /api/log` 的公开投影（協作端 2026-10-05 §2 拍 ＋ §fid 补）：

        = **`VIEW_LOG_TYPES` 白名单 ∩ `public:true`**，与 `view.log` 同一支常数，
          并且同样过 `_scrub_public(ev, seat=None)`（碎片事件的内部 id `fid` 只留当事人）
          ⇒ 匿名端点既不会比 view 多看到事件，也不会多看到字段。
        带对 `gm_token` 的那条路仍然回 `self.events` 原始全量（GM／除错）。

        ⚠ 已知落差（2026-10-07 記，**未改**：這是 協作端 那側的契約）：`view.log` 吃的是**這一桌**的
        白名單（內核 ＋ 本桌啟用包宣告的事件），這裡吃的是內核常數 ⇒ **包宣告的事件
        （`death`／`turn_skipped`／`skill_used`／`exp_gained`／`star_up`／`item_changed`…）不會進匿名流**
        ——但玩家／觀戰頁讀的是 `view.log`（`/api/state`），所以畫面上看得到；只有「靠 `/api/log`
        做外部核對」的人會少這幾種。要改＝動契約（`docs/API.md` 升版戳 ＋ 通知 協作端），先問設計者。
        """
        return [self._scrub_public(e, None) for e in self.events
                if e.get("public") is not False and e.get("type") in VIEW_LOG_TYPES]


    def view(self, *, seat: Optional[str] = None, gm: bool = False,
             detail: str = "full") -> Dict[str, Any]:
        """看桌子。``detail="compact"``＝**给 agent 的省 token 版**（2026-10-06）：

        完整的 `view` 一輪回 30,065 字元（其中公開日誌窗口 60 條＝13,8k 字元、
        `recent` 又是同一個尾巴的第二次投影）≈ 1.9 萬 token——GM 每次「看桌子」都付這筆。
        compact 只收**窗口類**欄位（log 收到 log_window_compact 條、不重複給 `recent`），
        **桌況／自己／私訊／棋盤／額度／動作目錄一律不動**：省的是重複的歷史，不是判斷依據。
        """
        with self.lock:
            # 投影一律走 **L2**（`view(state, viewer)`）：回傳物件全部歸呼叫端所有
            # （不參照 state、不做淺拷貝）、時鐘由這裡注入（`now`）⇒ 投影是純函數。
            # 人話尾巴由 **L4** 產好當參數帶進去——L2 自己不出句子。
            return projection.project(self.state, self, viewer=seat, gm=gm, detail=detail,
                                      now=self.now(), recent=self._recent_lines(limit=12))


    def _scrub_public(self, ev: Dict[str, Any], seat: Optional[str]) -> Dict[str, Any]:
        """（向後相容的殼）scrub 規則住在 **L2**：`projection.scrub_public`。"""
        return projection.scrub_public(ev, seat)


    def adapter_view(self) -> Dict[str, Any]:
        """**L6 適配器的讀出口**（薄殼；本體住 L2 `projection.adapter_view`）。

        為什麼要有它：adapter 以前直接讀 `self.table.state[...]`（kick digest／路由用的
        kind／health…），等於**繞過 L2**——分層規定「唯一讀出口」之後，L6 一律走這裡
        （`test_layers` F 組機械擋 L6 檔案裡的 `.state[`）。
        """
        with self.lock:
            return projection.adapter_view(self.state, self, now=self.now())

