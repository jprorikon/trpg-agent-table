"""lifecycle（自 Table 拆出）。"""

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
from .base import MemoryStore, VIEW_LOG_TYPES, _system_clock

class TableLifecycleMixin:
    def __init__(
        self, home: str, cfg: Dict[str, Any], *,
        clock: Optional[Any] = None, store: Optional[Any] = None,
        run_id: Optional[str] = None,
    ) -> None:
        """時鐘與 sink 都是**注入的邊界值**（第 3 刀）。

        - `clock`：回傳「現在」的 callable；不給＝`_system_clock`（真時鐘）。
        - `store`：事件 sink（`load/append/size/replace_tail`）；不給＝`MemoryStore`
          ⇒ **內核一個檔案都不碰**。生產＝L6 的 `store.FileStore(home)`（server 注入）。
        """
        self.home = home
        self.cfg = cfg
        # **這一局的 id（＝對外的 session）**：換局時由呼叫端指定（`table_factory.new_run`），
        # 不給就自己生一個（`_new_run_id`，UUID4）。事件流空的桌才會用到它（見 `_load`）。
        self._pref_run_id = run_id
        self.clock = clock or _system_clock
        self.store = store if store is not None else MemoryStore()
        self.lock = threading.RLock()
        # L1 機制包（`config.packs` 的啟用清單）：拓撲／衝突／缺依賴／未知項**載入時就叫**。
        # 沒寫 `packs` ⇒ legacy（各層照舊走 config，行為一個字都不變）。
        # **先解析再補預設**：包宣告的機制預設值補進 config 的缺口（config 寫的一律贏）⇒
        # 下游（board／semic／rules／節拍）看到的都是「這一桌實際生效的那一份」，不必各問各的。
        self.packs = packs.resolve(self.cfg.get("packs"), home=home)
        self.cfg = packs.apply_defaults(self.cfg, self.packs)
        # **這一桌的句庫**（L4）：內核中性預設 ← 包的 text.json ← 模組／劇本的 text.json。
        # 一桌一本、不併進 `presentation.TEXT`（hub 一個行程裝 N 張桌，全域字典會讓措辭串桌）。
        self.text_bank: Dict[str, str] = packs.text_bank_for(self.packs, home)
        if not isinstance(self.cfg.get("rules"), dict):
            raise RuleError("config 沒有 rules，也沒有啟用提供 rules 預設的機制包"
                            "（見 packs/README.md：d20core 帶 minimal-d20 的預設）")
        self.events: List[Dict[str, Any]] = []
        self.state: Dict[str, Any] = self.state_default()
        self.board_init(home, self.cfg)
        # 棋盤的**內容表**由包補（`kind=artifacts`；`board.json` 有寫的一份優先）——與條件表同一套。
        self.board_load_tables(self.packs.tables)
        # ⚠ 怪物表要**先**載入：棋盤的初始狀態要把怪物展開成個體（HP／同格多隻，2026-10-19）
        self.monsters_init(self.packs.tables)
        self.state["board"] = self.board_state_default() if hasattr(self, "board_state_default") else {}
        self.semic_init(home, self.cfg)
        self.state["semi"] = self.semic_state_default() if hasattr(self, "semic_state_default") else {}
        # L1 劇本模組（`<home>/module.json`；沒有＝舊行為）。載入階段**不跑觸發**：
        # 重放與新局的狀態都只能由既存事件折出來，不能靠觸發補算（否則 op=replay 會飄）。
        # （機制包在 `__init__` 開頭就解析＋補預設了——見上面那一段。）
        # 公開日誌白名單＝內核白名單 ＋ 包宣告的事件（只增不減）
        self.log_types = packs.log_types_for(self.packs, VIEW_LOG_TYPES)
        # 可用的 effect＝啟用包宣告的那些（沒啟用任何包＝內核整池）
        self.allowed_effects = tuple(self.packs.effects) or tuple(scenario.EFFECT_KINDS)
        # 地塊表（落格效果＝資料）：節點都載好了、可用 effect 也定了才驗（載入時就叫）
        self.lands_init(self.packs.tables)
        # L1 條件表（`packs/<id>/tables/*.json` 裡 kind=conditions 的）：沒啟用帶表的包＝空表，
        # 整條條件機制變 no-op（legacy 零變更——test_packs P27 盯著這一條）。
        self.conditions_init(home, self.cfg, self.packs.tables)
        # L1 技能表／等級表（M3／M4）：沒啟用帶表的包＝空表，兩條機制都 no-op（legacy 零變更）
        self.skills_load(self.packs.tables)
        self.advancement_load(self.packs.tables)
        # L1 道具表（★2）：沒啟用帶道具表的包＝空表（動作目錄不會有 use_item、庫存永遠空的）
        self.items_init(self.packs.tables)
        # L1 星等表：沒啟用帶星等表的包＝空表（一個事件都不多發、投影不多欄位）
        self.stars_init(self.packs.tables)
        # L1 救起表（★1）：沒啟用帶救起表的包＝空表（動作目錄不會有 rescue、rescue() 當場叫）
        self.rescue_init(self.packs.tables)
        # L1 商店表（★2）：沒啟用帶 shop 表的包＝空表（買賣走 config 的 legacy 路，行為不變）
        self.shop_init(self.packs.tables)
        # L1 死亡與送還：沒這顆旋鈕（`rules.death`）＝完全 no-op（legacy 一字不變）
        self.death_init(home, self.cfg)
        self.module: Optional[Any] = None
        self._firing: bool = True
        self._load()
        # 本局的 id（来自 table_created 事件）：kick 的幂等键带上它，重开一局不会和上一局撞车
        self.state["run_id"] = next(
            (e.get("run_id") for e in self.events if e.get("type") == "table_created"), None) \
            or self._new_run_id()
        # L1：不合法會在這裡就 ModuleError（不拖到跑團中間）；可用的 effect 由啟用包決定
        self.module = scenario.Scenario.load(home, allowed_effects=self.allowed_effects,
                                             bank=self.text_bank)
        # L3 導演：**只提難度檔位**（`difficulty_set` 由本層代發）——沒有劇本／沒寫 director 區塊
        # 就是 None（＝沒有導演，行為與以前完全一樣）。
        self.director: Optional[Any] = director.Director(self.module) if self.module else None
        self._firing = False


    def _load(self) -> None:
        """從 sink 載入事件流並重放——**內核不知道那是檔案、記憶體還是別的東西**。

        空流（新局／沒有持久化）⇒ 自己發一筆 `table_created`（本局 id 由此而生）。
        """
        for ev in self.store.load() or []:
            self.events.append(ev)
            self._apply(ev)
        if not self.events:
            # ⚠ **開新局＝開一個 run 目錄**：先讓落盤切到 `runs/<rid>/`，再寫 `table_created`
            #   （順序顛倒 ⇒ 這筆會落到上一局／舊制 `data/`，一局一檔就破了）。
            #   記憶體 sink 沒「局」這個概念 ⇒ 用 `getattr` 探（L0 不認識 sink 的長相）。
            rid = self._pref_run_id or self._new_run_id()
            _begin = getattr(self.store, "begin_run", None)
            if callable(_begin):
                _begin(rid)
            # 啟用清單（ids＋hash）**凍進事件流**：同一份 events.jsonl 換一張清單就會飄，
            # 不凍起來的話 `op=replay` 才發現——那時已經晚了（`packs.as_dict()` 只放兩格）。
            self._emit("table_created", scenario=self.cfg.get("scenario", ""),
                       seed=self.cfg.get("seed"), rules=self.cfg["rules"].get("name"),
                       run_id=rid, **self.packs.as_dict())


    def _emit(self, etype: str, **kw) -> Dict[str, Any]:
        ev = {"seq": len(self.events) + 1, "ts": self.now(), "type": etype}
        ev.update(kw)
        self.events.append(ev)
        self.store.append(ev)          # 落盤＝sink 的事（L6）；內核只吐事件
        self._apply(ev)
        # L0 的「觸發跑到定點」：只在最外層那次 _emit 之後跑（`_firing` 擋住遞迴），
        # 效果本身也是事件 ⇒ 由事件折出狀態、replay 一致。
        # 沒有 module.json 也要跑（條件結算與劇本無關）；`_firing` 擋遞迴
        if (self.module is not None or getattr(self, "condition_table", None)
                or getattr(self, "death_enabled", False)
                # ★ 音訊（2026-10-20）：模組宣告的自動提示（`audio.json › cues`）也在這一拍跑。
                #   `audio` 物件在沒有音訊表時也存在（`EMPTY`）⇒ 這裡只看「有沒有注入」，
                #   要不要放由 `audio_cue` 自己判（沒有表／沒有 cue ⇒ 立刻回空）。
                or getattr(self, "audio", None) is not None) and not self._firing:
            self._firing = True
            try:
                self._run_triggers(ev)
            finally:
                self._firing = False
        return ev


    def _run_triggers(self, ev: Dict[str, Any]) -> List[Dict[str, Any]]:
        """把這筆事件踩到的觸發／結局**跑到定點**（新事件再排一輪，最多 `scenario.MAX_ROUNDS`）。

        回傳這一輪額外產生的事件（除錯／測試用）。**不吞例外**：劇本寫壞要當場看到。
        """
        made: List[Dict[str, Any]] = []
        queue: List[Dict[str, Any]] = [ev]
        rounds = 0
        while queue and rounds < scenario.MAX_ROUNDS:
            rounds += 1
            batch, queue = queue, []
            for e in batch:
                # 音訊（2026-10-20）：模組宣告的 BGM／效果音提示。與劇本觸發同一拍、
                # **同一個開關**（`_firing`）⇒ 回放不會把提示重播一次（重放只折既存事件）。
                queue += self.audio_cue(e)
                if self.module is not None:
                    for trig in self.module.triggers_for(self, e):
                        queue += self.module.fire(self, trig, e)
                    queue += self.module.check_endings(self, e)
                    # 升星（★）：**每一筆事件後判一次**（與結局同一拍；判據裡的 `me`＝那一席）
                    if getattr(self, "stars_table", None):
                        queue += self.stars_check(e)
                # 條件（部位受傷／異常狀態）：只在 `turn_started` 這一拍結算——先報、再扣血、
                # 再倒數／到期。事件的數值都寫在事件裡 ⇒ 回放不必重算（`op=replay` 一致）。
                if e.get("type") == "turn_started" and getattr(self, "condition_table", None):
                    queue += self.conditions_tick(e.get("seat"))
                # 死亡／送還（`death.py`）：**任何**傷害把人打到 HP ≤ 0 ⇒ 送回最近的城市 ＋ 缺席 N 回合
                if e.get("type") == "damage" and getattr(self, "death_enabled", False):
                    queue += self.death_check(e)
            # L3：導演看一輪（主線距離／偏離 → 難度提案）。它只回提案，
            # **`difficulty_set` 由這裡代發**（L3 的介面上沒有改 state 的鑰匙）。
            if self.director is not None:
                prop = self.director.review(
                    self.events, projection.adapter_view(self.state, self, now=self.now()),
                    self.state.get("difficulty"))
                if prop:
                    queue.append(self._emit("difficulty_set", public=False, name=prop["preset"],
                                            by="director", why=prop.get("why"),
                                            metrics=prop.get("metrics")))
            made += queue
        return made


    def difficulty_mod(self) -> int:
        """目前難度預設給的 **DC 修正**（L3 的懲罰／獎勵手段之一；沒有預設＝0，行為不變）。"""
        return self._difficulty_key("dc_mod")


    def difficulty_act_mod(self) -> int:
        """目前難度預設給的**行動格**加減（罰＝−1、賞＝+1；沒有預設＝0）。"""
        return self._difficulty_key("act_mod")


    def _difficulty_key(self, key: str) -> int:
        if self.module is None:
            return 0
        try:
            return int(self.module.active_preset(self).get(key) or 0)
        except (TypeError, ValueError):
            return 0


    def now(self) -> float:
        """「現在」＝**注入的時鐘**（L0 不准自己讀時間，見 `_system_clock`）。"""
        return float(self.clock())


    def _new_run_id(self) -> str:
        """一局的 id（＝對外的 **session**）：**UUID4（32 hex）**。

        設計者 2026-10-07：「session 号不需要好读，可以直接 UUID」。用途不變：
        ① kick 的冪等鍵要帶上它（否則重開一局時「scene1」會和上一局的「scene1」撞車，
        對手側會當成重複丢掉）；② 也是落盤的目錄名 `runs/<session>/`（WS-B 階段 1c：一局一檔）。
        """
        return uuid.uuid4().hex


    def session_id(self) -> str:
        """**這一副本實例的 session 號**（設計者 2026-10-07：「kick 和 MCP 規範：需要帶 session 號，
        一個 session 對應一個副本實例」）。

        值＝`state["run_id"]`（`table_created` 事件產生的那一顆，一副本一顆）。
        對外一律叫 `session`（kick body／MCP 工具參數／`/api/state` 都是這個名字）；
        內部欄位名沿用 `run_id`（事件流與 kick 冪等鍵已經在用），**同一個值、不要另生一個**。
        """
        return str(self.state.get("run_id") or "")


    @property
    def events_path(self) -> str:
        """（向後相容）事件檔路徑＝**sink 的事**；記憶體 sink 沒有路徑（回空字串）。"""
        return str(getattr(self.store, "path", "") or "")


    def state_default(self) -> Dict[str, Any]:
        """狀態的**初始值＝唯一正本**（`__init__` 與 `replay_check` 共用）。

        2026-10-06 加「整桌暫停」時抓到：`replay_check` 自己硬拷了一份 state 初始值 ⇒ 新增欄位
        只加了一邊，`op=replay` 立刻報 `{"paused": [null, false]}`。欄位一律從這裡出。
        （`board`／`semi`／`run_id` 由各自的初始化另外補上——它們不是常數。）
        """
        return {
            "seats": {},          # seat_id -> {name, kind, attrs, hp, max_hp, status, order}
            "order": [],          # 行动顺序（seat_id）
            "turn": None,         # {index, seat, seq, started_at, deadline_at, waits}
            "log": [],            # 公开事件 seq 列表
            "secrets": {},        # seat_id -> [secret_event...]
            "roll_seq": 0,
            "turn_seq": 0,
            "turn_index": -1,
            "last_seat": None,
            "round_no": 0,          # 回合制（pacing.mode=turn）：走完 order 一輪算一輪
            "round_start_seq": 0,   # 本輪起點＝上一次 round_ended 的序號（給 GM 的 digest 用）
            "paused": False,        # 整桌暫停：停叫醒（含 GM）／停計時／停超時代打／不自動開局
            "paused_at": 0.0,       # 暫停的那一刻（恢復時把死線整段順延＝暫停不吃他的時間）
            "facts": {},            # L1 事實（觸發／劇本寫進來的；一切都由 fact_set 事件折出）
            "star_ch": {},          # 星等**渠道計數**（stars.py；seat -> {channel: 已漲幾次}；2026-10-20）
            "clocks": {},           # ★ 時鐘（clocks）＝由 clock_changed 事件折出（0…max）
            "difficulty": None,     # L3 落腳點：目前生效的難度預設名（difficulty_set 事件）
            "ended": None,          # 已判定的结局 id（ending 事件；先搶先贏）
            "conditions": {},       # 部位受傷／異常狀態：seat -> {條件id: {since_seq, turns_left…}}
            "attr_mods": {},        # 屬性加值：seat -> {attr: 加值}（0＝拿掉；後續 effect 寫進來）
            "resources": {},        # 通用資源：seat -> {名: 值}（法術位／彈藥／體力…）
            "exp": {},              # 經驗：seat -> 累計值（M4）
            "level": {},            # 等級：seat -> 級數（M4）
            "inventory": {},        # 道具庫存：seat -> {道具id: 數量}（★2；一切由 item_changed 折出）
            "star": {},             # 星等：seat -> ★幾（升星＝劇情事件觸發；一切由 star_up 折出）
            "skip": {},             # 死亡送還：seat -> 還要被跳過幾個回合（death.py；沒啟用＝永遠空的）
            # ★ 音訊（2026-10-20）：播放控制的現況（兩個聲道＋最後一筆 seq；一切由 `audio` 事件折出）。
            #   沒有音訊表的模組永遠停在初始值（投影據「有沒有注入音訊表」決定露不露這一格）。
            "audio": {"bgm": None, "sfx": None, "seq": 0},
        }


    def _apply(self, ev: Dict[str, Any]) -> None:
        """把一条事件折进内存状态。纯函数式风格，无副作用（除了 state）。"""
        t = ev["type"]
        st = self.state
        if t == "seat_joined":
            st["seats"][ev["seat"]] = {
                "seat": ev["seat"], "name": ev["name"], "kind": ev["kind"],
                "attrs": ev["attrs"], "hp": ev["hp"], "max_hp": ev["hp"],
                "status": [], "order": ev.get("order"),
                "node": ev.get("node"), "memory": ev.get("memory"),
                "artifacts": [],          # 錢包鍵不寫死在這裡（按槽位 `slot("coin")` 補，見 join）
            }
            if ev.get("wake_url"):
                # wake_url 要进事件流：否则桌面一重启（重放事件）这条席位就"失联"
                st["seats"][ev["seat"]]["wake_url"] = ev["wake_url"]
            if ev.get("order") is not None:
                st["order"].append(ev["seat"])
                # ★ 2026-10-20（設計者：「不應該按照進 run 的順序，而是應該按照 seats 順序」）：
                #   排序鍵＝**劇本 `seats.json` 的席位順序**（p1→p4），事件的 `order` 只當
                #   「這個席有沒有進入行動順序」的旗標與動態席位的後備。
                #   以前只 append ⇒ 後入座的 agent 席位永遠排最後（實測 p2→p1→p4）；
                #   而且這樣**舊局重放也會被導正**（不必改事件流）。
                _spec_order = list((getattr(self, "seats_spec", {}) or {}).keys())
                st["order"].sort(key=lambda s: (
                    _spec_order.index(s) if s in _spec_order
                    else 1000 + int((st["seats"].get(s) or {}).get("order") or 0)))
        elif t == "clock_changed":
            # ★ 時鐘（2026-10-20）：`after` 是**結果值**（發事件時就算好）⇒ 回放不必重算，跟條件／資源同一套。
            _name = str(ev.get("name") or "")
            if _name:
                st.setdefault("clocks", {})[_name] = int(ev.get("after") or 0)
        elif t == "fact_set":
            # L1 的事實：由事件折出（`test_layers` E 組擋「effect 直接改 state」這種寫法）
            st.setdefault("facts", {})[str(ev.get("key"))] = ev.get("value")
        elif t == "difficulty_set":
            st["difficulty"] = ev.get("name")
        elif t == "ending":
            if not st.get("ended"):
                st["ended"] = ev.get("id")
        elif t == "turn_started":
            st["turn_seq"] = ev["seq"]
            st["turn_index"] = ev["index"]
            st["last_seat"] = ev["seat"]
            st["turn"] = {"index": ev["index"], "seat": ev["seat"], "seq": ev["seq"],
                          "started_at": ev["ts"], "deadline_at": ev["deadline_at"],
                          "waits": 0}
            for s in st["seats"].values():
                s["status"] = [x for x in s["status"] if x != "defending"]
            # 額度以**回合**為單位：每回合開始把該席的計數歸零
            semi = st.get("semi") or {}
            if semi:
                (semi.get("counters") or {}).pop(ev["seat"], None)
        elif t == "turn_ended":
            st["turn"] = None
        elif t == "round_ended":
            # 回合制的一輪（pacing.mode=turn）：public=False ⇒ 不進公開日誌、前端也不顯示，
            # 只有節拍器與 GM 的 digest 用它。純計數，重放一致。
            st["round_no"] = int(ev.get("no") or 0)
            st["round_start_seq"] = int(ev.get("end_seq") or st.get("round_start_seq") or 0)
        elif t == "pacing_paused":
            # 整桌暫停：狀態進事件流（重放一致）——節拍器／喚醒／超時都看這一格
            st["paused"] = True
            st["paused_at"] = float(ev.get("paused_at") or self.now())
        elif t == "pacing_resumed":
            # 恢復：把「暫停期間凍掉的那段時間」還給當前回合（死線順延，暫停不吃他的時間）
            st["paused"] = False
            st["paused_at"] = 0.0
            _cur = st.get("turn")
            if _cur:
                _cur["deadline_at"] = float(_cur.get("deadline_at") or 0) \
                    + float(ev.get("frozen_seconds") or 0)
        elif t == "turn_extended":
            if st["turn"]:
                st["turn"]["waits"] = ev["waits"]
                st["turn"]["deadline_at"] = ev["deadline_at"]
        elif t == "roll":
            st["roll_seq"] = ev["roll_seq"]
        elif t == "damage":
            s = st["seats"].get(ev["target"])
            if s:
                s["hp"] = ev["hp_after"]
                if s["hp"] <= 0 and "down" not in s["status"]:
                    s["status"].append("down")
        elif t == "status_set":
            s = st["seats"].get(ev["seat"])
            if s:
                if ev["value"] and ev["status"] not in s["status"]:
                    s["status"].append(ev["status"])
                elif not ev["value"]:
                    s["status"] = [x for x in s["status"] if x != ev["status"]]
        elif t == "heal":
            s = st["seats"].get(ev["seat"])
            if s:
                s["hp"] = ev["hp_after"]
                if s["hp"] > 0:
                    s["status"] = [x for x in s["status"] if x != "down"]
        elif t == "secret":
            if not ev.get("peer") and ev.get("frm") == ev.get("to"):
                ev["peer"] = self._infer_peer(ev)     # 舊資料的自回顯沒有 peer，補一個
            st["secrets"].setdefault(ev["to"], []).append(ev)
        # 其余事件（say / action_declared / seat_timeout / kick / ...）只进日志，不改状态

        self.board_apply(ev)
        self.semic_apply(ev)
        self.conditions_apply(ev)
        self.dsl_apply(ev)          # 屬性加值／通用資源（M2 原語）
        self.advancement_apply(ev)  # 經驗／等級（M4）
        self.items_apply(ev)        # 道具庫存（★2）
        self.stars_apply(ev)        # 星等（升星）
        self.death_apply(ev)        # 死亡／送還（倒下的那個人回到最近的城市、缺席 N 回合）
        self.audio_apply(ev)        # 音訊現況（bgm／sfx 兩個聲道；回放一致）

        # 白名單是**這一桌的**（內核 ＋ 本桌啟用的包宣告的事件；見 __init__）
        if ev.get("public", True) and t in self.log_types:
            st["log"].append(ev)

