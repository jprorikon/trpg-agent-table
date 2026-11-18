"""turns（自 Table 拆出）。"""

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


class TableTurnsMixin:
    def _alive_order(self) -> List[str]:
        """顺序表里还在场上（未 down）的席位——回合与轮判定都用它。"""
        return [x for x in self.state["order"]
                if x in self.state["seats"] and "down" not in self.state["seats"][x]["status"]]


    def _next_seat(self, cur: Optional[str]) -> Optional[str]:
        alive = self._alive_order()
        if not alive:
            return None
        if cur is None or cur not in alive:
            return alive[0]
        return alive[(alive.index(cur) + 1) % len(alive)]


    def _maybe_round_end(self, cur_seat: str) -> Optional[Dict[str, Any]]:
        """一輪＝``order`` 裡每個還在場上的席位各走完一次（**固定起點** ＝ ``alive[0]``）。

        所以「輪末」＝**最後一個還在場的席位**結束他的回合。中途有人 join/down 也不改起點
        （``alive`` 每次重算，起點永遠是 order 裡第一個還活著的席位）；``order`` 空 ⇒ 不判輪
        （降級成純回合制，只是永遠沒有「一輪」⇒ 不叫 GM，見 server 的節拍）。

        事件 ``round_ended`` 走 ``public=False``：不進公開日誌白名單、不給前端，只讓節拍器
        知道「可以叫 GM 了」；狀態只動 ``round_no``／``round_start_seq``，重放一致。
        """
        alive = self._alive_order()
        if not alive or cur_seat != alive[-1]:
            return None
        no = int(self.state.get("round_no", 0)) + 1
        end_seq = int(self.events[-1]["seq"]) if self.events else 0
        return self._emit("round_ended", public=False, no=no, last_seat=cur_seat,
                          start_seq=int(self.state.get("round_start_seq") or 0),
                          end_seq=end_seq, seats=list(alive))


    def start_turn(self, *, seat: Optional[str] = None, reason: str = "manual") -> Dict[str, Any]:
        with self.lock:
            cur = self.state["turn"]
            if cur:
                raise RuleError(f"回合未结束（{cur['seat']} 的 seq={cur['seq']}），先 end_turn")
            nxt = seat or self._next_seat(self.state.get("last_seat"))
            # 死亡送還（`death.py`）：缺席中的人**跳過**——發 `turn_skipped`（公開）後直接交下一席。
            # 每跳一次計數 −1 ⇒ 一定終止；全桌都在缺席也只是把每一席各跳掉一次。
            guard = 0
            while nxt and self.death_skip_turn(nxt) is not None:
                nxt = self._next_seat(nxt)
                guard += 1
                if guard > 512:            # 安全閥（正常到不了：skip_turns 有限、每次跳過扣 1）
                    break
            if not nxt:
                raise RuleError("还没有可行动的席位：先 join")
            self._require_seat(nxt)
            self.board_on_turn_start(nxt)
            # 逐席節拍（`turn.seconds_by_kind`）：沒寫就等於 `turn_cfg`（零變更）
            cfg = self.turn_cfg_for(nxt) if hasattr(self, "turn_cfg_for") else self.turn_cfg
            idx = int(self.state.get("turn_index", -1)) + 1
            # ★ 2026-10-20：`scene_no` 寫進事件（＝這一輪是在第幾景開的）。`_maybe_autostart`
            #   用它判斷「這一景已經開過輪沒有」——以前用 `turn_index >= 0` 當「開過局」的判據，
            #   結果**一局只開得到第一輪**：第 2 景之後引擎不再開輪、玩家動不了，GM 每次被叫起來
            #   只看到「景開著沒人動」就收景再開一景（實測一路空轉到第 8 景）。
            # ★ 2026-10-21（拆景）：一輪的第一席開始 ⇒ 「輪開始」的唯一時機
            _alive = self._alive_order()
            if _alive and idx % max(1, len(_alive)) == 0:
                if hasattr(self, "semic_round_start"):
                    self.semic_round_start()      # 清空「本輪出手／結束」標記
                self._emit("round_started", public=True,
                           no=int(self.state.get("round_no") or 0) + 1)
            ev = self._emit("turn_started", public=True, seat=nxt, index=idx,
                            deadline_at=self.now() + int(cfg["seconds"]), reason=reason)
            return ev


    def end_turn(self, *, reason: str = "manual", start_next: bool = True,
                 note: Optional[str] = None) -> Dict[str, Any]:
        with self.lock:
            cur = self.state["turn"]
            if not cur:
                raise RuleError("当前没有进行中的回合")
            # ⚠ 欄位名別叫 `seq`：`_emit` 會先塞事件自己的流水號，再被 kw 蓋掉
            # （2026-10-06 抓到：`turn_ended`/`seat_timeout`/`turn_extended` 的 `seq` 都被蓋成
            #  「那個回合的起點 seq」⇒ 事件流出現重號，客戶端拿 seq 當增量游標會漏事件）
            ev = self._emit("turn_ended", public=True, seat=cur["seat"], turn_seq=cur["seq"],
                            reason=reason, **({"note": str(note)} if note else {}))
            # 輪末判定在「交下一位」之前：順序＝turn_ended(末席) → 宣言結算 → round_ended → round_started → turn_started
            _round = self._maybe_round_end(cur["seat"])
            if _round:
                # 景已拆：輪末就是唯一的「結算點」——揭曉本輪宣言（爭奪祈願）＋主動怪出手
                if hasattr(self, "semic_round_resolve"):
                    try:
                        self.semic_round_resolve()
                    except Exception:          # noqa: BLE001
                        pass
            if start_next:
                nxt = self._next_seat(cur["seat"])
                if nxt:
                    self.start_turn(seat=nxt, reason="next")
            return ev

