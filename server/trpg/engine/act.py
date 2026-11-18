"""act（自 TableActionsMixin 拆出）。"""

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


class TableActionsActMixin:
    def act(self, *, seat: str, action: str, target: Optional[str] = None,
            attr: Optional[str] = None, dc: Optional[int] = None,
            note: Optional[str] = None, skill: Optional[str] = None,
            item: Optional[str] = None, gm_override: bool = False,
            req: Optional[str] = None) -> Dict[str, Any]:
        """一席一手（唯一入口：玩家／agent／GM 代打／hook 都走這裡）。

        `req`（2026-10-20 設計者：「動作要請求冪等」）：客戶端給的**請求 id**（重試時同一個）。
        同一局內 `seat:req` 只結算一次——重送回前一手的宣告 seq，**不再擲骰、不再扣額度**。
        索引就是事件流本身（`action_declared.req`）⇒ 重啟／回放／跨進程都認得，不必另存一份。
        沒帶 `req` ⇒ 舊行為（向後相容）；前端與 MCP 一律帶。
        """
        with self.lock:
            _req_key = f"{seat}:{str(req).strip()[:128]}" if str(req or "").strip() else ""
            if _req_key:
                # 只在「真的結算過」的宣告上判重：失敗被就地標 rejected 的那筆不算
                # （同一個請求重試一次是合法的——上一次根本沒成立）。
                for _ev in reversed(self.events):
                    if (_ev.get("type") == "action_declared" and _ev.get("req") == _req_key
                            and not _ev.get("rejected")):
                        return {"duplicate": True, "req": str(req), "seat": seat,
                                "action": _ev.get("action"), "seq": _ev.get("seq"),
                                "note": "同一個請求已經結算過（重送不重複）——結果看 table_view"}
            if self.state.get("paused"):
                # 整桌暫停（總閘）：**牌面完全凍結**——連 end_turn 也不吃（回合也是牌面）。
                # 對話（say／tell）不受影響：凍的是節拍與牌面，不是他跟她說話。
                raise RuleError("整桌已暫停（暫停中牌面不動）——在 app 上按「繼續」之後再動作")
            if self.state.get("ended"):
                # 判了結局＝這一局結束（2026-10-07 設計者抓到「收尾了還在倒數」）：牌面不再動，
                # **對話照常**（say／tell／私信都還能用）——與暫停同一立場。
                raise RuleError(f"這一局已經結束（{self.state['ended']}）——牌面不再動；"
                                "要說話／道別照常（say、tell）")
            s = self._require_seat(seat)
            # 「結束回合」＝**引擎操作**，不是遊戲動作（2026-10-06 設計者定案）：
            #   回合制下「我不出手」就是結束自己的回合 ⇒ 不走半合作閘、不進額度、
            #   也不發 action_declared（幽靈宣告的來源）；只把回合交給下一席，
            #   事件用既有的 turn_ended（公開且在 VIEW_LOG_TYPES 裡，重放一致）。
            #   `pass`（場景制的「這一景我不動」）在回合制下當同義詞吃——橋的離線代打還在送它，
            #   但 UI／工具列舉在回合制下不再顯示 pass（見 visible_actions）。
            if action == "end_turn" or action == "pass":
                if action == "pass":
                    self.semic_mark_ended(seat)          # 明确表示这一轮不再动作
                cur = self.state["turn"]
                if not cur:
                    raise RuleError("現在沒有進行中的回合（沒人可結束）")
                if cur["seat"] != seat:
                    raise RuleError(f"現在是 {cur['seat']} 的回合，不是 {seat}"
                                    "（只能結束自己的回合；GM 要代收請走 gm_op）")
                ev = self.end_turn(reason="end_turn", note=note)
                return {"ended_turn": True, "seat": seat, "seq": ev.get("seq"),
                        "next": (self.state["turn"] or {}).get("seat"), "note": note or ""}
            # ★ 2026-10-20（設計者：「角色不應該被允許在非自己的 phase 行動」）：
            #   回合制下 `act`＝**只有當前回合那一席**能做（以前只有 end_turn 檢查回合，
            #   search／move／enter 這些誰都能在任何時候出手 ⇒ 一輪裡兩個人同時動、額度也亂）。
            #   沒回合（景開著但輪沒開）也不准動——那是引擎/ GM 的事，不是玩家偷跑。
            #   `say／tell` 不受影響（對話不算行動，仍可隨時講）。
            #   ⚠ `gm_override=True`：GM 的 `gm_op act_for` 代某席送機械動作（NPC 不在回合順序裡，
            #   永遠不可能是當前回合）⇒ 只有 GM 這條路可以跳過「回合歸屬」檢查，其餘閘門照走。
            if not gm_override:
                _cur = self.state.get("turn")
                if not _cur:
                    raise RuleError("現在沒有進行中的回合——等引擎開下一輪（人齊就會自動開輪）")
                if str(_cur.get("seat")) != str(seat):
                    raise RuleError(f"現在是 {_cur['seat']} 的回合，不是 {seat}"
                                    "——非自己的 phase 不能行動（要說話可用 say／tell）")
            if action not in self.action_ids:
                raise RuleError(f"动作 {action} 不在允许列表：{self.action_ids}")
            if "down" in s["status"]:
                raise RuleError(f"{seat} 已倒下（down），需要治疗才能行动")
            # ★ 2026-10-20：`pacing.free_actions`（例 `["move","ride"]`）＝**不佔行動格、也不結束回合**。
            #   實測（原型桌 那局 353 手）：移動＋升降機吃掉 47.8% 的手——走路不該跟「調查、戰鬥、
            #   祈願」搶同一份額度。預設空清單＝舊行為，一張桌要開才開。
            _free_actions = set((self.turn_cfg or {}).get("free_actions")
                                or (self.pacing_cfg or {}).get("free_actions") or ())
            _is_free = action in _free_actions and action != "pass"
            if action != "pass" and not _is_free:      # 「不动」／免費動作不受額度限制
                self.semic_gate(seat, "act")   # ② 额度闸在 emit 之前：额度被拒的动作不留「幽灵」
            # ③ 宣告仍先寫進公開日誌（保住「宣告 → 結果」的順序），但動作本身若在
            #    「過了額度闸之後」才失敗（例：買不到東西、attack 沒給 target），
            #    就把這筆宣告就地標成 rejected 且不進公開投影——不再留一筆沒發生過的宣告
            #    （協作端 2026-10-05：設計者按三次「購買」留了三筆幽靈 action_declared）。
            _off = self._events_size()
            _decl = self._emit("action_declared", public=True, seat=seat, action=action,
                               target=target, note=note,
                               # ★ 2026-10-21：技能／道具併進 `use` 之後，宣告本身要帶上
                               #   `skill`／`item`，公開日誌才寫得出「施放 星屑斬り → 王」，
                               #   不然人類只看到一句「使用了某樣東西」（事件折狀態不看這兩格）。
                               **({"skill": str(skill)} if skill else {}),
                               **({"item": str(item)} if item else {}),
                               **({"req": _req_key} if _req_key else {}))
            try:
                if action in self.board_actions():
                    try:
                        out = self.board_act(seat=seat, action=action, target=target,
                                             attr=attr, dc=dc, note=note)
                    except RuleError:
                        raise
                    except ValueError as exc:
                        raise RuleError(str(exc)) from exc
                    self._maybe_end_turn(seat, out, free=_is_free)
                    if action != "pass" and not _is_free:        # 「不动」／免費動作不花行动额度
                        self.semic_count(seat, "act")
                    self.semic_mark_acted(seat, note=action)
                    return out
                if action in self.semic_actions():
                    try:
                        out = self.semic_act(seat=seat, action=action, target=target,
                                             attr=attr, dc=dc, note=note)
                    except RuleError:
                        raise
                    except ValueError as exc:
                        raise RuleError(str(exc)) from exc
                    self._maybe_end_turn(seat, out, free=_is_free)
                    if action != "pass" and not _is_free:        # 「不动」／免費動作不花行动额度
                        self.semic_count(seat, "act")
                    self.semic_mark_acted(seat, note=action)
                    return out
                out: Dict[str, Any] = {"seat": seat, "action": action}
                if action == "attack":
                    if not target:
                        raise RuleError("attack 必须给 target")
                    t = self._require_seat(target)
                    # 不是棋盘上的角色就不能被打：GM 席位（主持）与「自己打自己」都拒掉。
                    # NPC 可以（它是世界里的角色，由 GM 裁定后果）。
                    if target == seat:
                        raise RuleError("不能攻击自己")
                    if t.get("kind") == "gm":
                        raise RuleError("不能攻击 GM 席位——主持不在棋盘上，不是可攻击的目标")
                    # ★ 2026-10-20（設計者：「送還中＝不在場」走 (b)）：缺席中的人已經在城裡，
                    #   打不到他——不拒的話會出現「同一個人死兩次」（death_check 沒有早退時更明顯）。
                    self.damage_guard(target)
                    cur = self.state["turn"]
                    bonus = int(self.rules["defend_bonus"]) if "defending" in t["status"] else 0
                    dc_use = (int(dc if dc is not None else self.rules["check"]["dc_default"])
                              + bonus + self.difficulty_mod())
                    roll = self._roll(seat=seat, kind="attack", reason=f"attack {target}",
                                      mod=self._mod(self.attrs_of(seat), "agi")
                                      + self.conditions_mod(seat, "attack"), dc=dc_use)
                    out["roll"] = roll
                    if roll["success"]:
                        dmg_cfg = self.rules["damage"]
                        dseq = self.state["roll_seq"] + 1
                        drng = random.Random(f"{self.cfg['seed']}:{dseq}")
                        dval = drng.randint(1, int(dmg_cfg["die"]))
                        dmod = int(self.attrs_of(seat).get(dmg_cfg["attr"], 0)) \
                            * int(dmg_cfg["attr_multiplier"])
                        dmg = max(1, dval + dmod)
                        self._emit("roll", public=True, roll_seq=dseq, seat=seat, kind="damage",
                                   die=int(dmg_cfg["die"]), value=dval, mod=dmod, total=dmg,
                                   dc=None, success=None, reason=f"damage -> {target}")
                        # ★ 2026-10-20：扣血走唯一出口（`hp_after` 下限 0 只在 damage_apply 算）
                        _dev = self.damage_apply(seat=target, amount=dmg, src=seat)
                        hp_after = _dev["hp_after"]
                        out["damage"] = dmg
                        out["target_hp"] = hp_after
                    out["target"] = target
                elif action == "check":
                    attr_use = attr or "wil"
                    if attr_use not in self.rules["attributes"]:
                        raise RuleError(f"属性 {attr_use} 不存在：{self.rules['attributes']}")
                    dc_use = (int(dc if dc is not None else self.rules["check"]["dc_default"])
                              + self.difficulty_mod())
                    out["roll"] = self._roll(seat=seat, kind="check", reason=note or "check",
                                             mod=self._mod(self.attrs_of(seat), attr_use)
                                             + self.conditions_mod(seat, "check"), dc=dc_use)
                elif action == "defend":
                    self._emit("status_set", public=True, seat=seat, status="defending", value=True)
                elif action in ("cast", "use_item"):
                    # 2026-10-21（設計者：合併）：技能與道具都收在 `use` 底下；
                    # 舊名字 `cast`／`use_item` 仍照收（同義），對外只宣告 `use`。
                    if action == "cast":
                        out.update(self.cast(seat=seat, skill=skill, target=target,
                                             dc=dc, note=note))
                    else:
                        out.update(self.use_item(seat=seat, item=item, target=target, note=note))
                    action = "use"
                elif action == "rescue":
                    # ★1：救起一個倒地的人（`rescue.py`；檢定與 check／cast 同一條算式）
                    out.update(self.rescue(seat=seat, target=target, note=note))
                elif action == "use_item":
                    # ★2：用一件道具（`items.py`；先扣 1、成功才套 use.effects）
                    out.update(self.use_item(seat=seat, item=item, target=target, note=note))
                elif action == "use":
                    # ★ 2026-10-21 合併：`use` 統一吃「用道具」與「放技能」——
                    #   `use(skill=<技能 id>, target=<對象>)`｜`use(item=<道具 id>, target=<對象>)`
                    #   ｜`use(target=<道具或技能 id>)`（只有一個欄位時先認道具表、再認技能表）
                    #   ｜都沒認得 ⇒ 當「泛用使用」（要 note），不硬擋。
                    _item = str(item or "").strip()
                    _skill = str(skill or "").strip()
                    _t = str(target or "").strip()
                    _item_ids = self.item_ids() if hasattr(self, "item_ids") else ()
                    _skill_ids = self.skill_ids() if hasattr(self, "skill_ids") else ()
                    if not _item and not _skill:
                        if _t and _t in _item_ids:
                            _item, _t = _t, ""
                        elif _t and _t in _skill_ids:
                            _skill, _t = _t, ""
                    if _item:
                        out.update(self.use_item(seat=seat, item=_item, target=_t or None, note=note))
                    elif _skill:
                        out.update(self.cast(seat=seat, skill=_skill, target=_t or None, dc=dc, note=note))
                    elif not note:
                        raise RuleError("use 需要說清楚用了什麼（`skill`／`item` 給 id，或 `note` 寫一句）")
                elif action in ("move", "other"):
                    pass
                self._maybe_end_turn(seat, out, free=_is_free)
                if action != "pass" and not _is_free:        # 通用动作也要计额度（免費動作不算）
                    self.semic_count(seat, "act")
                self.semic_mark_acted(seat, note=action)
                return out
            except Exception as exc:  # 失敗的動作不留幽靈宣告（就地標 rejected＋不進公開投影）
                self._void_declaration(_decl, _off, exc)
                raise


    def _events_size(self) -> int:
        """事件流目前位元組長度（＝sink 的游標）——動作失敗時把尾端那行覆寫回去用。

        內核只問 sink「現在多長」，不知道那是檔案還是記憶體（L6 的事）。
        """
        return self.store.size()


    def _void_declaration(self, ev: Dict[str, Any], offset: int, exc: BaseException) -> None:
        """動作失敗時，把剛寫進 events.jsonl 的那筆 action_declared 就地改成 rejected
        且 public=False，並覆寫檔案尾端那一行（append-only 的唯一例外：同一筆就地標記、
        不新增行、不留幽靈）。前提：動作驗證先行 ⇒ 失敗時這筆宣告必定是檔案最後一筆
        （動作本身一個事件都還沒 emit）；若它已不是最後一筆，代表動作已部分落地，
        此時不冒險回捲，交由上層照舊報錯。"""
        if not (self.events and self.events[-1] is ev):
            return
        ev["public"] = False
        ev["rejected"] = True
        ev["error"] = str(exc)[:200]
        # 区分「规则拒绝」（RuleError，正常业务）与「引擎自己坏了」（其它异常）——
        # 否则两者在原始流里长得一模一样（協作端 2026-10-05 §1 建议①）。
        ev["error_type"] = type(exc).__name__
        # _apply 會把「public 且在白名單」的事件直接以**同一個 dict 參照**塞進 state["log"]
        # ⇒ 就地改 public=False 後，必須把那一筆從 state["log"] 拿掉（用 is 比身分，
        #    別用 == 誤刪同形的另一筆），否則重放比對（replay_check）會看到多一筆。
        lg = self.state.get("log")
        if isinstance(lg, list):
            for i in range(len(lg) - 1, -1, -1):
                if lg[i] is ev:
                    del lg[i]
                    break
        # 尾行就地覆寫＝append-only 的唯一例外；**怎麼覆寫是 sink（L6）的事**，
        # 失敗必須自己留痕（`store.FileStore` 會寫 stderr，見那裡的契約）。
        self.store.replace_tail(offset, ev)


    def _new_seat_id(self) -> str:
        n = 1
        while f"seat{n}" in self.state["seats"]:
            n += 1
        return f"seat{n}"


    def _require_seat(self, seat: str) -> Dict[str, Any]:
        s = self.state["seats"].get(seat)
        if not s:
            raise RuleError(f"没有这个席位：{seat}")
        return s


    def _mod(self, attrs: Dict[str, int], attr: str) -> int:
        return int(attrs.get(attr, self.rules["attr_default"])) * int(self.rules["check"]["attr_multiplier"])


    def _roll(self, *, seat: str, kind: str, reason: str,
              mod: int, dc: Optional[int], public: bool = True) -> Dict[str, Any]:
        seq = self.state["roll_seq"] + 1
        rng = random.Random(f"{self.cfg['seed']}:{seq}")
        die = int(self.rules["check"]["die"])
        value = rng.randint(1, die)
        total = value + mod
        success = None if dc is None else bool(total >= dc)
        ev = self._emit("roll", public=public, roll_seq=seq, seat=seat, kind=kind,
                        die=die, value=value, mod=mod, total=total, dc=dc,
                        success=success, reason=reason,
                        rng=f"seed:{self.cfg['seed']}:{seq}")
        return ev


    def node_mates(self, seat: str) -> List[str]:
        """跟 `seat` 站在同一個節點上的其他席位（協力技用：站在一起打才划算）。"""
        me = (self.state["seats"].get(seat) or {}).get("node")
        if me is None:
            return []
        return [sid for sid, ss in (self.state.get("seats") or {}).items()
                if sid != seat and (ss or {}).get("node") == me]

    def roll_damage(self, *, seat: str, die: int = 0, bonus: int = 0,
                    src: Optional[str] = None, attr: Optional[str] = None,
                    note: Optional[str] = None) -> Dict[str, Any]:
        """骰式傷害（一次性動作：技能表／效果走這條）——與 `attack` 的傷害段**同一條算式**。

        `attr` 給了就加上出手者該屬性 × `rules.damage.attr_multiplier`（與 attack 同口徑）。
        骰值寫進 `roll` 事件、結果寫進 `damage` 事件 ⇒ 回放不必重算。
        """
        with self.lock:
            t = self._require_seat(seat)
            d = int(die or 0)
            v = 0
            if d > 0:
                dseq = self.state["roll_seq"] + 1
                drng = random.Random(f"{self.cfg['seed']}:{dseq}")
                v = drng.randint(1, d)
            b = int(bonus or 0)
            if attr and src and src in self.state["seats"]:
                b += int(self.attrs_of(src).get(attr, 0)) \
                    * int(self.rules["damage"]["attr_multiplier"])
            total = max(1, v + b)
            if d > 0:
                self._emit("roll", public=True, roll_seq=self.state["roll_seq"] + 1, seat=src or seat,
                           kind="damage", die=d, value=v, mod=b, total=total, dc=None,
                           success=None, reason=note or f"damage -> {seat}")
            # ★ 2026-10-20：送還中＝不在場（(b)）；扣血走唯一出口（hp_after 下限 0）
            self.damage_guard(seat)
            ev = self.damage_apply(seat=seat, amount=total, src=src, note=note)
            return {"damage": total, "target_hp": ev["hp_after"], "event": ev}


    def roll_heal(self, *, seat: str, die: int = 0, bonus: int = 0,
                  note: Optional[str] = None) -> Dict[str, Any]:
        """骰式治療（技能表／效果走這條）；骰值與結果都寫進事件。"""
        with self.lock:
            s = self._require_seat(seat)
            d = int(die or 0)
            v = 0
            if d > 0:
                dseq = self.state["roll_seq"] + 1
                drng = random.Random(f"{self.cfg['seed']}:{dseq}")
                v = drng.randint(1, d)
            total = max(0, v + int(bonus or 0))
            if d > 0:
                self._emit("roll", public=True, roll_seq=self.state["roll_seq"] + 1, seat=seat,
                           kind="heal", die=d, value=v, mod=int(bonus or 0), total=total,
                           dc=None, success=None, reason=note or f"heal -> {seat}")
            hp_after = min(s["max_hp"], s["hp"] + total)
            self._emit("heal", public=True, seat=seat, amount=total, hp_after=hp_after,
                       note=note)
            return {"heal": total, "hp_after": hp_after}


    def heal(self, *, seat: str, amount: int) -> Dict[str, Any]:
        with self.lock:
            s = self._require_seat(seat)
            hp_after = min(s["max_hp"], s["hp"] + int(amount))
            return self._emit("heal", public=True, seat=seat, amount=int(amount), hp_after=hp_after)


    def turn_action_budget(self) -> int:
        """回合制下「一回合幾格行動點」（2026-10-06 設計者：原本一次出手就被判回合結束＝bug）。

        `turn.actions_per_turn` 優先；沒寫就沿用 `pacing.actions_per_scene`（同一組數字，不用兩個旋鈕）；
        再沒有就 1。寫 `turn.end_turn_on_action: true` 則維持舊語意＝**出手即交棒**（1 動/回合）。
        """
        t = self.turn_cfg                     # config.turn ＋ 節拍包的覆寫
        if t.get("end_turn_on_action") is True:
            return 1
        v = t.get("actions_per_turn")
        if v is None:                        # 遷移期：舊劇本把數字寫在 pacing.actions_per_scene
            v = (self.cfg.get("pacing") or {}).get("actions_per_scene")
        try:
            return max(1, int(v)) if v is not None else 1
        except (TypeError, ValueError):
            return 1


    def _maybe_end_turn(self, seat: str, out: Dict[str, Any], *, free: bool = False) -> None:
        """動作交接：行動者就是當前回合的人時，決定要不要把回合交給下一席。

        - 回合制：**花完這一回合的額度才交棒**（`turn_action_budget()`）；還有行動點就留在自己手上。
          想提早收手＝`action=end_turn`。計數在 `_maybe_end_turn` **之後**才 +1（`semic_count`），
          所以這裡用 `used + 1 >= budget` 判「這一手花掉最後一點」。
        - 場景制：照舊看 `turn.end_turn_on_action`（出手即交棒）。
        - `free=True`（`pacing.free_actions` 的動作，例移動）：**不花額度、也就不交棒**——
          玩家可以連續走幾格，然後還留著自己的行動格做別的事。
        """
        if free:
            return
        cur = self.state["turn"]
        if not cur or cur["seat"] != seat:
            return
        budget = self.turn_action_budget()
        used = int((((self.state.get("semi") or {}).get("counters") or {})
                    .get(seat) or {}).get("act", 0))
        if used + 1 >= budget:
            self.end_turn(reason="action")
            out["turn_ended"] = True
