"""social（自 TableActionsMixin 拆出）。"""

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


class TableActionsSocialMixin:
    def join(self, *, seat: Optional[str] = None, name: str, kind: str,
             attrs: Optional[Dict[str, int]] = None, order: Optional[int] = None,
             node: Optional[str] = None, wake_url: Optional[str] = None) -> str:
        with self.lock:
            seat = seat or self._new_seat_id()
            if seat in self.state["seats"]:
                raise RuleError(f"席位已存在：{seat}（换个 seat id，或直接 view）")
            if kind not in ("human", "agent", "gm", "npc"):
                raise RuleError("kind 只能是 human / agent / gm / npc")
            lo, hi = self.rules["attr_range"]
            base = int(self.rules["attr_default"])
            a = {}
            tmpl = self.roster.get(seat, {}) if hasattr(self, "roster") else {}
            if not tmpl and hasattr(self, "seats_spec"):
                # seats.json 里用 p1/p2/p3 这类席位名，角色在 character 字段 → 用 roster 的角色模板
                ch = (self.seats_spec.get(seat) or {}).get("character")
                tmpl = self.roster.get(ch, {}) if ch else {}
                if tmpl:
                    name = name or tmpl.get("name") or name
            if attrs is None and tmpl.get("attrs"):
                attrs = dict(tmpl["attrs"])
            rolled = self.roll_attrs(seat) if bool(self.rules.get("attr_roll")) else None
            for attr in self.rules["attributes"]:
                if rolled is not None:
                    v = int(rolled[attr])
                else:
                    v = int((attrs or {}).get(attr, base))
                    if not (lo <= v <= hi):
                        raise RuleError(f"属性 {attr}={v} 超出 {lo}~{hi}")
                a[attr] = v
            hp_cfg = self.rules["hp"]
            hp = int(hp_cfg["base"]) + int(hp_cfg["per_str"]) * a.get("str", base)
            if kind in ("gm", "npc"):
                order = None            # GM 与 NPC 不进行动顺序（NPC 由 GM 驱动）
            elif order is None:
                # ★ 2026-10-20（設計者：「順序不應該是 P1，P2，P3，P4 麼」）：行動順序＝**劇本排的席位順序**
                #   （`seats.json` 的 p1→p4），不是「誰先入座誰先手」。以前一律 `len(order)+1`
                #   ⇒ 實測第一輪是 p2→p1→p4（p2 先入座，p1 是後進來的 agent 席位）。
                _spec = list((getattr(self, "seats_spec", {}) or {}).keys())
                order = (_spec.index(seat) + 1) if seat in _spec else (len(self.state["order"]) + 1)
            start_node = node if node is not None else getattr(self, "start_node", None)
            mem_start = int(getattr(self, "mem_cfg", {}).get("start", 0)) or None
            if kind in ("gm", "npc"):
                start_node = None      # GM 不在棋盘上
            if kind == "gm":
                mem_start = None
            elif start_node is None:
                start_node = node
            self._emit("seat_joined", public=True, seat=seat, name=name, kind=kind,
                       attrs=a, attrs_rolled=rolled is not None,
                       hp=hp, order=order, node=start_node, memory=mem_start,
                       roster=tmpl.get("name"), wake_url=wake_url)
            # M4：有啟用等級表時，開局自動給第 1 級（＝起始資源）；沒啟用＝一個事件都不多發。
            # ⚠ **只給玩家席（human／agent）**——GM／NPC 不升級、不升星（owner 2026-10-08「GM不应该升级和升星」）：
            #   他們的數值由劇本／GM 當場定；開局那一筆 level_up／star_up 對他們沒有意義，還會讓投影憑空多出星等。
            if kind in ("human", "agent"):
                self.advancement_baseline(seat)
                self.stars_baseline(seat)  # 有啟用星等表時：開局記 ★1（玩家一開始就看得到自己的星）
            self._maybe_autostart()
            return seat


    def roll_attrs(self, seat: str) -> Dict[str, int]:
        """角色建立擲骰（唯讀、重問同答案）：值＝seed＋席位＋屬性。

        「初期屬性點只能用骰的」——所以 join 不接受客戶端給的值，一律用這支。
        確定性（同 seed＋同席位＝同結果）讓它在重放時不必額外存檔，也防止一直重擲到滿意。
        """
        lo, hi = self.rules["attr_range"]
        return {a: random.Random(f"{self.cfg['seed']}:join:{seat}:{a}").randint(lo, hi)
                for a in self.rules["attributes"]}


    def say(self, *, seat: str, text: str) -> Dict[str, Any]:
        with self.lock:
            self._require_seat(seat)
            kind = (getattr(self, "seats_spec", {}) or {}).get(seat, {}).get("kind") \
                or self.state["seats"][seat].get("kind")
            if kind in ("gm", "npc") and hasattr(self, "gm_say"):
                return self.gm_say(seat=seat, text=text)   # GM/NPC 发言先过泄漏比对
            self.semic_gate(seat, "say")
            ev = self._emit("say", public=True, seat=seat, text=text)
            self.semic_count(seat, "say")
            self.semic_mark_acted(seat, note="say")
            return ev


    def secret(self, *, to: str, text: str, frm: str = "gm",
               peer: str = "") -> Dict[str, Any]:
        with self.lock:
            self._require_seat(to)
            return self._emit("secret", public=False, to=to, frm=frm, text=text,
                              peer=peer or "")


    def _infer_peer(self, ev: Dict[str, Any]) -> str:
        """舊資料：玩家送出的回顯（frm==to）沒帶 peer，補不出對話對象就對不到對話串。
        用「同一句話、同時送給別人的那一則」把對象推回來（只在記憶體補，不動存檔）。"""
        seq = int(ev.get("seq") or 0)
        for x in self.events:
            if (x.get("type") == "secret" and x.get("frm") == ev.get("frm")
                    and x.get("to") and x["to"] != ev.get("to")
                    and x.get("text") == ev.get("text")
                    and abs(int(x.get("seq") or 0) - seq) <= 3):
                return x["to"]
        return ""


    def mark_typing(self, *, seat: str, to: str = "") -> None:
        """誰正在打字（只在記憶體、不進事件流）：前端每幾秒 ping 一次，view 讓同桌看到。"""
        with self.lock:
            if not hasattr(self, "typing"):
                self.typing = {}
            self.typing[seat] = {"ts": self.now(), "to": to or ""}

