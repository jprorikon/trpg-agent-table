#!/usr/bin/env python3
"""**L6｜桌的業務層**（`TableService`）——唯一持有引擎的物件。

從 `server.py` 切出來（2026-10-06 結構整理）。分工：
- **這一支**：`__init__`（**組合點**——把 `store.FileStore` 接上內核）與 `tell`（私訊，
  REST `/api/tell` 與 MCP `table_tell` 共用同一支：同一個行為、同一條喚醒契約）。
- `wake.py`（`KickMixin`）：踢席位／踢 GM／節拍器。
- `mcp_api.py`（`McpMixin`）：工具目錄與分派。

依賴方向：service → {store, engine, wake, mcp_api}；反過來沒有人 import service
（`web_api.py` 只在型別註解時提它，避免循環）。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from trpg.core import store as store

from trpg.engine import Table
from trpg.core.errors import TellError
from trpg.service import assets as assets
from trpg.service import audio as audio

from trpg.core import layout as layout

from trpg.api.mcp_api import McpMixin
from trpg.service.wake import GM_CLI, GM_KICK_TOKEN_FILE, GM_KICK_URL, KickMixin


def _seat_plan_of(def_dir: str) -> tuple:
    """`seats.json` 裡「這一局設計給玩家坐的席位」（human／agent；gm／npc 不算）。

    只回 `{seat, name, kind, character}`——投影只需要這四格。讀不到就回空 tuple
    （沒有 seats.json 的臨時桌／舊形狀 ⇒ 投影逐字不變）。
    """
    try:
        with open(os.path.join(def_dir, "seats.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return ()
    rows = []
    for r in (doc.get("seats") or []):
        if not isinstance(r, dict) or str(r.get("kind")) not in ("human", "agent"):
            continue
        if not r.get("seat"):
            continue
        rows.append({"seat": str(r["seat"]), "name": r.get("name") or str(r["seat"]),
                     "kind": str(r.get("kind")), "character": r.get("character")})
    return tuple(rows)


class TableService(KickMixin, McpMixin):
    """引擎 + 唤醒（kick） + 网页/API 共享的业务层。单进程唯一持有。"""

    def __init__(self, home: str, cfg: Dict[str, Any], *,
                 session: Optional[str] = None, run_id: Optional[str] = None,
                 def_dir: Optional[str] = None):
        self.cfg = cfg
        # `home`＝**這一桌自己的目錄**（`config.json`／`runs/`）；`def_dir`＝**劇本層的定義目錄**
        # （`seats.json`／`board.json`／`scenario.json`／`packs/`…，由 `layout.def_dir_of()` 從路徑推）。
        # 同一劇本可以有多張桌（設計者 2026-10-07）：定義**共享**、桌檔各自獨立——引擎只讀定義，
        # 落盤（`runs/<session>/`）留在桌目錄底下。
        self.home = home
        self.def_dir = def_dir or layout.def_dir_of(home)
        # L6 適配器的組合點（第 3 刀）：這裡才把「事件流落盤」接上內核——
        # 內核只吐事件（預設 MemoryStore），檔案是這一層注入的（`store.FileStore`）。
        # `session`／`run_id`：**換局**（`table_factory.new_run`）時指到新的 `runs/<session>/` 那一局；
        # 平常（開機／回放）＝None ⇒ 由 store 自己找現役那一局（`runs/current`／舊制 data/）。
        self.table = Table(self.def_dir, cfg, store=store.FileStore(home, session=session),
                           run_id=run_id)
        # ★ 素材（**模組級**，2026-10-09）：`modules/<模組>/assets/index.json` 的別名表。
        #   只認這一桌所屬模組的那一份 ⇒ **模組之間不互相污染**；沒有那個檔＝這一桌沒有圖，
        #   投影一格都不加（legacy／別模組逐字不變）。引擎只透過 `table.assets` 讀它（L2 用）。
        self.module = str(layout.where(self.home, layout.repo_root(self.home)).get("module") or "")
        self.table.module_id = self.module
        self.table.assets = assets.load(layout.repo_root(self.home), self.module)
        # ★ 音訊（**模組級**，2026-10-20）：`modules/<模組>/audio.json`——作者手寫的音訊表
        #   （有哪些 BGM／效果音、哪個事件自動放哪一首）。與圖同一條模組白名單 ⇒ 模組之間
        #   不互相污染；沒有那個檔＝這一桌沒有聲音（引擎不發任何音訊事件、投影不多任何一格）。
        #   引擎只做播放控制（`engine/audio.py`），真的出聲是前端的事。
        self.table.audio = audio.load(layout.repo_root(self.home), self.module)
        # ★ 席次表（2026-10-19）：劇本排了哪些席位（只 human／agent）——投影用它標「缺席」。
        #   讀定義層的 seats.json（桌目錄沒有它）；壞檔／沒有＝空 tuple（投影不加那一格）。
        self.table.seat_plan = _seat_plan_of(self.def_dir)
        self.kicked: Dict[str, str] = {}       # f"{seat}:{turn_seq}" -> 结果
        # 這一桌的**工具目錄**（`mcp_api.TOOLS` 的副本＋按本桌 config 調整）——由 `table_factory.build()`
        # 填入。一進程多桌（hub）時每桌各一份，A 桌的機制包不會漏到 B 桌（2026-10-07）。
        self.tools: list = []
        self.wake_urls: Dict[str, str] = {}    # seat -> url（join 时登记）
        # 引擎 → GM（協作端）喚醒連結：URL／token 檔可由 config.kick 覆寫（不寫死路徑）
        _kick = cfg.get("kick") or {}
        # 方向進名字：`kick.receiver_url`＝**GM 接收器**（發出去的腳）；
        # `wake.seats.<席>.url`＝各席位自己的接收器。舊名 `kick.url` 仍相容。
        self.kick_url: str = (_kick.get("receiver_url") or _kick.get("url") or GM_KICK_URL)
        self.kick_token_file: str = (_kick.get("receiver_token_file")
                                    or _kick.get("token_file") or GM_KICK_TOKEN_FILE)
        self.gm_cli: str = _kick.get("gm_cli") or GM_CLI   # 喚醒訊息裡給 GM 的那行工具（可跨機覆寫）
        # 給 GM 的介面：cli＝跑 tools/gm.py（要 terminal）；mcp＝用桌的 MCP 工具
        # （GM 的瘦 profile 沒有 terminal ⇒ 只能走 mcp；見 PLAN_turn_mode_and_gm_slim.md §2）
        self.gm_tools: str = str(_kick.get("gm_tools") or "cli").lower()
        # 節奏：scene＝場景制（開景就叫 GM／每位 agent 踢一次）；turn＝回合制
        # （走完 order 一輪才叫 GM 一次，見 PLAN §B）。**以桌子為準**（引擎會讓節拍包
        # 覆寫 config）——L6 自己再讀一份 config 就會與引擎不一致（那正是要避免的漂移）。
        self.pacing_mode: str = self.table.pacing_mode
        self.rio_kick_keys: set = set()        # 已喚過的 GM-wake 冪等鍵（坐席＋景）
        self.mcp_sessions: Dict[str, Dict[str, Any]] = {}
        self.events_seen = len(self.table.events)
        _ops = self.table.adapter_view()          # L6 的讀出口（L2）；不直接摸 state
        self.last_turn_key: Optional[str] = _ops["turn_key"]
        # ★2026-10-08（owner：「這涉及到 agent 自身的記憶，我需要 agent 明確知道這一局結束了」）：
        #   結局**已判**的局（＝重啟時載入的每一局）⇒ 先把「終局通知」的冪等鍵記成看過了：
        #   **不補叫**（不然每次重啟都會把已經結束的舊局又推一次給 agent）。
        #   真正要通知的時機＝本行程活著的時候看到 ending 出現（見 `wake.maybe_kick_ending`）。
        if _ops.get("ended"):
            _run0 = str(_ops.get("run_id") or "r0")
            for _sid0, _s0 in (_ops.get("seats") or {}).items():
                if str((_s0 or {}).get("kind") or "") == "agent":
                    self.rio_kick_keys.add(
                        f"{cfg.get('table_id') or 'table'}:終局:{_run0}:{_sid0}")
        # 重啟不補叫：把「已經發過的輪」當成看過了（state 是重放出來的，round_no 已經在）
        self.last_round_no: Optional[int] = _ops["round_no"] or None
        # 每席結束叫 GM（wake.gm_on_turn_end）：重啟不補叫——把已經發生過的「手」當看過了
        self.last_seatwake_seq: int = max((int(e.get("seq") or 0) for e in self.table.events
                                           if e.get("type") == "turn_ended"), default=0)

    def tell(self, seat: str, to: str, text: str) -> Dict[str, Any]:
        """私訊（玩家 ⇄ 玩家／GM）：只有收件人與寄件人看得到。

        **REST `/api/tell` 與 MCP `table_tell` 共用這一支**——同一個行為、同一條喚醒契約，
        不養第二份實作（2026-10-06 設計者發現「給 agent 私信後 agent 回不了私信」，根因就是
        MCP 面只給 GM 一把私信鑰匙：`table_secret` 要 token，玩家手上沒有私信出口）。

        喚醒契約（2026-10-05 協作端 §3；2026-10-06 修正）：**私訊只叫收件人**——
        對 GM ⇒ 叫 GM；對 agent 席位 ⇒ 叫那個席位自己（走它自己的 wake url）；對人 ⇒ 不叫
        （他的頁面就看得到）。

        驗證失敗丟 `TellError`（REST 依 status 回碼、MCP 轉成工具錯誤文字）。
        """
        seat = str(seat or "").strip()
        to = str(to or "").strip()
        text = str(text or "").strip()
        if not seat or not to or not text:
            raise TellError("seat / to / text 都是必填")
        _seats = self.table.adapter_view()["seats"]
        if seat not in _seats:
            raise TellError("還沒入座", 409)
        tgt = _seats.get(to)
        if not tgt:
            raise TellError("沒有這個席位", 404)
        if tgt.get("kind") not in ("human", "agent", "gm"):
            raise TellError("那個對象不能私訊", 403)
        ev = self.table.secret(to=to, text=text, frm=seat)
        self.table.secret(to=seat, text=text, frm=seat, peer=to)   # 回顯給自己（前端據 peer 分對話串）
        if to == "gm" or tgt.get("kind") == "gm":
            self.wake_gm(seat, text, "私訊", ev["seq"])
        elif tgt.get("kind") == "agent":
            self.kick(to, f"私訊:{ev['seq']}", force=True, reason="私訊")
        return {"ok": True, "seq": ev["seq"]}
