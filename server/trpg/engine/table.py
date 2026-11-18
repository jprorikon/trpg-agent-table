#!/usr/bin/env python3
"""TRPG 桌引擎 —— 权威层（纯代码，零模型、零第三方依赖）。

三条铁律：
  1. 数值只由本文件产出：骰子 / HP / 状态。模型没有写权限，它只能"声明动作"。
  2. 一切状态由 append-only 事件日志推导（事件流 + 累加器），任意时刻可回放。
  3. 骰子可复现：random.Random(f"{seed}:{roll_seq}")，与进程、时间、机器无关。

并发约定：引擎只应被【唯一进程】持有（server.py）。任何 MCP 客户端 / 网页 / 席位适配器
都通过 server.py 的 HTTP 接口进来，不要多个进程直接写同一个 data 目录。
"""

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

from .lifecycle import TableLifecycleMixin
from .actions import TableActionsMixin
from .turns import TableTurnsMixin
from .reading import TableReadingMixin
from .audio import AudioMixin
from .damage import DamageMixin

try:  # 條件（部位受傷／異常狀態）：機制在這裡、內容在包的表；沒啟用帶表的包時全部 no-op
    from trpg.rules.conditions import ConditionsMixin
except Exception:  # noqa: BLE001
    class ConditionsMixin:  # type: ignore
        def conditions_init(self, home, cfg, tables=None): pass
        def conditions_apply(self, ev): pass
        def conditions_tick(self, seat): return []
        def conditions_mod(self, seat, kind): return 0
        def conditions_view(self, seat): return {}
        def condition_ids(self): return []

try:
    from trpg.rules.skills import SkillsMixin
except Exception:  # noqa: BLE001
    class SkillsMixin:  # type: ignore
        def skills_load(self, tables=None): self.skills_table = {}
        def skill_ids(self): return []
        def skills_of(self, seat): return []
        def cast(self, **kw): raise RuntimeError("skills 模組缺失")

try:
    from trpg.rules.advancement import AdvancementMixin
except Exception:  # noqa: BLE001
    class AdvancementMixin:  # type: ignore
        def advancement_load(self, tables=None): self.levels_table = []
        def advancement_apply(self, ev): pass
        def level_of(self, seat): return 1
        def exp_of(self, seat): return 0
        def progression_view(self, seat): return {}
        def exp_gain(self, **kw): raise RuntimeError("advancement 模組缺失")
        def advancement_baseline(self, seat): return None

try:
    from trpg.rules.items import ItemsMixin
except Exception:  # noqa: BLE001
    class ItemsMixin:  # type: ignore
        def items_init(self, tables=None): self.item_table = {}
        def items_apply(self, ev): pass
        def item_ids(self): return []
        def items_of(self, seat): return {}
        def items_view(self, seat): return []
        def item_add(self, **kw): raise RuntimeError("items 模組缺失")
        def item_take(self, **kw): raise RuntimeError("items 模組缺失")
        def use_item(self, **kw): raise RuntimeError("items 模組缺失")

try:
    from trpg.rules.monsters import MonstersMixin
except Exception:  # noqa: BLE001
    class MonstersMixin:  # type: ignore
        def monsters_init(self, tables=None): self.monster_rows = []
        def monster_ids(self): return []
        def monster_for(self, node): return None
        def monster_view(self, node): return None

try:
    from trpg.rules.death import DeathMixin
except Exception:  # noqa: BLE001
    class DeathMixin:  # type: ignore
        def death_init(self, home="", cfg=None): self.death_cfg = {}; self.death_enabled = False
        def death_apply(self, ev): pass
        def death_check(self, ev): return []
        def death_skip_of(self, seat): return 0
        def death_skip_turn(self, seat): return None
        def death_return_node(self, node): return None

try:
    from trpg.rules.shop import ShopMixin
except Exception:  # noqa: BLE001
    class ShopMixin:  # type: ignore
        def shop_init(self, tables=None): self.shop_table = {}
        def shop_ids(self): return []
        def shop_source(self): return "none"
        def shop_view(self, seat=None): return []
        def shop_buy(self, **kw): raise RuntimeError("shop 模組缺失")

try:
    from trpg.rules.rescue import RescueMixin
except Exception:  # noqa: BLE001
    class RescueMixin:  # type: ignore
        def rescue_init(self, tables=None): self.rescue_table = []
        def rescue_rule(self, rid=None): return None
        def rescue_ids(self): return []
        def down_seats(self): return []
        def rescue(self, **kw): raise RuntimeError("rescue 模組缺失")

try:
    from trpg.rules.stars import StarsMixin
except Exception:  # noqa: BLE001
    class StarsMixin:  # type: ignore
        def stars_init(self, tables=None): self.stars_table = []
        def stars_apply(self, ev): pass
        def stars_check(self, event): return []
        def star_of(self, seat): return 1
        def stars_baseline(self, seat): return None
        def stars_view(self, seat): return {}
        def unlocks_of(self, seat): return []
        def star_set(self, **kw): raise RuntimeError("stars 模組缺失")

try:
    from trpg.rules.dsl import DslMixin
except Exception:  # noqa: BLE001
    class DslMixin:  # type: ignore
        def dsl_apply(self, ev): pass
        def attrs_of(self, seat): return dict((self.state.get("seats", {}).get(seat) or {}).get("attrs") or {})
        def attr_mods_of(self, seat): return {}
        def resources_of(self, seat): return {}
        def set_attr_mod(self, **kw): raise RuntimeError("dsl 模組缺失")
        def add_resource(self, **kw): raise RuntimeError("dsl 模組缺失")

try:  # 地塊表（落格效果＝資料；沒有表＝no-op）
    from trpg.rules.lands import LandsMixin
except Exception:  # noqa: BLE001
    class LandsMixin:  # type: ignore
        def lands_init(self, tables=None): self.lands_table = {}
        def land_row(self, node): return None
        def land_effects(self, node, phase="stop"): return []
        def land_stop(self, **kw): return []

try:  # 棋盘机制（地图/塔玩法）；用不到时纯引擎照旧
    from trpg.rules.board import BoardMixin
except Exception:  # noqa: BLE001
    class BoardMixin:  # type: ignore
        def board_actions(self): return ()
        def board_init(self, home, cfg): pass
        def board_apply(self, ev): pass
        def board_view(self, seat): return {}
        def board_on_turn_start(self, seat): return []

try:  # 半合作模式（场景制 / 碎片 / 私密愿望 / GM 工具面）
    from trpg.rules.semic import SemiCoopMixin
except Exception:  # noqa: BLE001
    class SemiCoopMixin:  # type: ignore
        def semic_init(self, home, cfg): pass
        def semic_state_default(self): return {}
        def semic_actions(self): return ()
        def semic_apply(self, ev): pass
        def semic_view(self, seat): return {}
        def semic_gate(self, seat, kind): pass

# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------






# 公開日誌白名單（**L2 的家**：projection.VIEW_LOG_TYPES）——這裡只是 re-export，
# 讓 engine 內的 reducer 與外部（測試）照舊 `from engine import VIEW_LOG_TYPES`。


# 動作目錄的三個函式住在 **L1**（`action_catalog.py`）——這裡只是 re-export，
# 讓既有的 `from engine import normalize_actions, visible_actions` 照舊可用。




class Table(ConditionsMixin, DslMixin, SkillsMixin, AdvancementMixin, ItemsMixin, StarsMixin, RescueMixin, ShopMixin, MonstersMixin, DeathMixin, LandsMixin, BoardMixin, SemiCoopMixin, AudioMixin, DamageMixin, pacing.PacingMixin, TableLifecycleMixin, TableActionsMixin, TableTurnsMixin, TableReadingMixin):
    module_id: str = ""
    assets: Optional[Any] = None
    seat_plan: tuple = ()
    @property
    def rules(self) -> Dict[str, Any]:
        return self.cfg["rules"]
    @property
    def pacing_mode(self) -> str:
        """節奏：**config 是權威**（`pacing.mode`），節拍包只在 config 沒寫時當預設。

        為什麼不是「包優先」：一個旋鈕只能有一個權威。包若偷偷蓋掉 config，作者改了 config
        卻看到行為不變（2026-10-06 真的踩到：測試把 `pacing.mode` 改成 scene，卻被啟用中的
        `mode.turn` 包蓋回 turn ⇒ 一票喚醒測試假紅）。不一致時 `tools/check_campaign.py`
        會提醒（載入時不叫：方向永遠是「config 贏」，不會有兩種解讀）。

        值本身仍必須是內核認得的（`packs.KERNEL_MODES`）——新的節拍（例如 DND 先攻制）
        要先在內核支援，才有包可以宣告它。
        """
        # ★ 2026-10-21 設計者定案：**引擎只跑回合制**（`pacing.mode` 的 scene 分支整條拆掉）。
        #   這一層留成屬性只是為了不讓既有呼叫端炸掉；值恆為 "turn"。
        #   舊 config 若還寫著 `pacing.mode: "scene"` ⇒ 當場叫，附遷移提示（不要靜默改成 turn）。
        cfg_mode = (self.cfg.get("pacing") or {}).get("mode")
        pk_mode = (self.packs.mode or {}).get("name") if self.packs.mode else None
        mode = str(cfg_mode or pk_mode or "turn").lower()
        if mode != "turn":
            raise RuleError(
                f"pacing.mode={mode!r} 已停用：引擎只跑回合制（turn）。"
                "把 config.example.json 的 pacing 段拿掉（要免費動作就寫 turn.free_actions），"
                "並把 scenario 的收尾判據改成 round_no")
        return "turn"
    def _pack_defaults(self, section: str, base: Dict[str, Any]) -> Dict[str, Any]:
        """把節拍包在 `pacing`／`turn` 給的值當**預設**補進缺口：**config 有寫的一律以 config 為準**。"""
        out = dict(base or {})
        if self.packs.mode:
            for k, v in (self.packs.mode.get(section) or {}).items():
                out.setdefault(k, v)
        return out
    def term_name(self, key: str = "fragment") -> str:
        """半合作那幾個詞的**這一桌叫法**（2026-10-21 設計者：碎片是 示範模組 的專有名詞，
        每個模組可以自己定——例：星屑／御札／线索）。模組沒宣告 ⇒ 內核預設。

        寫法（`module.json`）：
        ```jsonc
        "terms": {"fragment": {"jp": "星屑", "cn": "星屑"}}
        ```
        """
        _default = {"fragment": "碎片"}
        mod = getattr(self, "module", None)
        terms = (getattr(mod, "terms", None) or {}) if mod is not None else {}
        v = terms.get(str(key))
        if isinstance(v, dict):
            return str(v.get("cn") or v.get("jp") or v.get("en") or _default.get(key, key))
        if isinstance(v, str) and v.strip():
            return v.strip()
        return _default.get(str(key), str(key))

    @property
    def pacing_cfg(self) -> Dict[str, Any]:
        """這一桌實際生效的節奏設定＝config.pacing（權威）＋ 節拍包補缺口（沒有包＝原樣）。"""
        return self._pack_defaults("pacing", self.cfg.get("pacing") or {})
    @property
    def turn_cfg(self) -> Dict[str, Any]:
        """這一桌實際生效的回合設定＝config.turn（權威）＋ 節拍包補缺口（沒有包＝原樣）。"""
        return self._pack_defaults("turn", self.cfg.get("turn") or {})

    def turn_cfg_for(self, seat: str) -> Dict[str, Any]:
        """**逐席**的回合設定：`turn` 之上再套 `<key>_by_kind`（依席位種類）。

        config 寫法（全部可選、只寫要改的那一個）：
        ```jsonc
        "turn": {"seconds": 180, "max_waits": 2,
                 "seconds_by_kind": {"agent": 90},          // agent 只有 90 秒
                 "max_waits_by_kind": {"agent": 1},
                 "wait_extra_seconds_by_kind": {"agent": 30}}
        ```
        為什麼（實測 2026-10-20）：一局的超時率 57%，其中 p1／p2 兩個真人席位佔 160 次——
        人與 agent 用同一組秒數時，agent 慢就讓人陪等、或者人慢就讓 agent 空轉。
        沒有寫 `*_by_kind` ⇒ 回傳與 `turn_cfg` 完全相同（零行為變更）。
        """
        base = dict(self.turn_cfg)
        # 先看**實際上怎麼入座的**（`state.seats.kind`），再看劇本的 `seats.json`：
        # 同一席可以被用不同 kind 入座，而節拍要照顧的是「現在坐著的那個人」。
        kind = ((self.state.get("seats") or {}).get(seat) or {}).get("kind") \
            or ((getattr(self, "seats_spec", {}) or {}).get(seat) or {}).get("kind")
        if not kind:
            return base
        for key in ("seconds", "max_waits", "wait_extra_seconds", "policy_on_timeout"):
            by_kind = base.get(f"{key}_by_kind")
            if isinstance(by_kind, dict) and kind in by_kind:
                base[key] = by_kind[kind]
        return base
    @property
    def action_entries(self) -> List[Any]:
        """**有效動作目錄**＝`config.rules.actions` ＋ 機制包宣告的動作（config 文案優先）。

        包宣告的是「我需要這個動作」；要不要開、怎麼顯示仍是這張桌的 config 說了算
        （`packs.effective_action_entries`；同一 id 以 config 為準）。
        """
        return packs.effective_action_entries(self.rules.get("actions"), self.packs, self.text_bank)
    @property
    def action_ids(self) -> List[str]:
        """有效動作目录归一成 id 列表（对象写法也吃）——**规则闸**用，不过滤节拍。

        `pass` 之类的场景制动作即使在这一拍看不到，闸门仍收（桥的离线代打还在送它），
        要看的「UI 该显示什么」用 `visible_action_ids`。
        """
        return normalize_actions(self.action_entries)[0]
    @property
    def visible_action_ids(self) -> List[str]:
        """这一拍（scene／turn）该显示给玩家的动作 id（带 modes 的条目按节拍过滤）。"""
        return visible_actions(self.action_entries, self.pacing_mode)[0]
    @property
    def actions_catalog(self) -> List[Dict[str, str]]:
        """[{id,label,desc}]——给 /api/state 的 actions[]（前端只认 id，文案数据驱动）。"""
        return visible_actions(self.action_entries, self.pacing_mode)[1]
    def replay_check(self) -> Dict[str, Any]:
        """从事件日志重放一遍，与内存状态比对（取证用）。

        本體（重放＋逐欄比對）住在 **`replay.py`**；這裡只負責鎖與造一顆「同環境、
        events 空的」`fresh` 桌。時鐘與 sink 都是**注入值** ⇒ 直接沿用本桌的
        （重放跑的是記憶體裡的事件，不會再寫檔）。
        """
        with self.lock:
            fresh = Table.__new__(Table)
            fresh.home = self.home
            fresh.cfg = self.cfg
            fresh.clock = self.clock
            fresh.store = self.store
            fresh.module = self.module      # 重放也要看得到劇本（判據一樣），但**不跑觸發**
            fresh.director = self.director  # L3 同理：重放不看導演（難度由既存的 difficulty_set 折出）
            fresh.packs = self.packs        # 啟用清單同一份（白名單／可用 effect 才對得上）
            fresh.log_types = self.log_types
            fresh.allowed_effects = self.allowed_effects
            fresh._firing = True            #   ⇒ 狀態只由既存事件折出
            fresh.lock = threading.RLock()
            fresh.events = []
            fresh.board_init(self.home, self.cfg)
            fresh.lands_init(self.packs.tables)
            fresh.semic_init(self.home, self.cfg)
            fresh.conditions_init(self.home, self.cfg, self.packs.tables)
            fresh.skills_load(self.packs.tables)
            fresh.advancement_load(self.packs.tables)
            fresh.items_init(self.packs.tables)
            fresh.stars_init(self.packs.tables)
            fresh.rescue_init(self.packs.tables)
            fresh.shop_init(self.packs.tables)
            fresh.monsters_init(self.packs.tables)
            fresh.death_init(self.home, self.cfg)
            fresh.state = fresh.state_default()          # 初始值與 __init__ 同一份（別再硬拷）
            fresh.state["board"] = fresh.board_state_default()
            fresh.state["semi"] = fresh.semic_state_default()
            fresh.state["run_id"] = self.state.get("run_id")
            return replay.replay_diff(self, fresh)


# 轉出：原本住在這一支的常數／工具（實作已搬到 base.py）
from .base import _system_clock, MemoryStore, VIEW_LOG_TYPES, normalize_actions, action_modes, visible_actions
