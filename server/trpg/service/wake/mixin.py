"""mixin（自 __init__.py 拆出）"""

from __future__ import annotations
import json
import os
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional
from trpg.kick.sender import GM_CLI, GM_KICK_TOKEN_FILE, GM_KICK_URL, send_kick_to_gm
from trpg.core import layout as layout
from trpg.engine.projection import trim_secrets as _trim_secrets

from .keys import round_pacing_line
from .kicks import maybe_kick_ending, maybe_kick_round

from .prompt import KickPromptMixin

class KickMixin(KickPromptMixin):
    """`TableService` 的喚醒那一半（踢席位、踢 GM、節拍器）。"""
    def wake_gm(self, seat: str, text: str, kind: str, seq: int,
                turn_key: Optional[str] = None) -> Dict[str, Any]:
        """叫醒 GM 席（`kick_sender.send_kick_to_gm`）——并把**这一脚**记进事件流（2026-10-06）。

        以前这条路只有 stdout 一行、桌上什么都没有：「静默丢弃不许可」（铁律⑤）在
        **GM 唤醒路**上是破的——桌上只看到玩家说了话，没人知道 GM 到底被叫了没、成没成。
        现在每一脚都留一条 `kick` 事件（`public:true`，且在 `VIEW_LOG_TYPES` 白名单里
        ⇒ 公开投影看得到），带：谁触发的（`by`）、为什么（`reason`）、幂等键（`turn_seq`）、
        成没成（`ok`）与细节（`detail`：status／body／error）。

        席位踢（`Service.kick`）本来就是「成败都进日志」；这支是把它补到 GM 那一侧。
        ⚠ 事件是 append-only 的审计轨，不是状态：`_apply` 对 `kick` 只记日志、不改状态，
        所以 `op=replay` 仍然一致。
        """
        if self.table.adapter_view()["paused"]:
            # 整桌暫停（總閘，2026-10-06 設計者要的按鈕）：**不叫醒任何人——包括 GM**。
            # 腳不出去、也不留 kick 事件（pause 本身就是桌上那條公開事件，不是靜默）。
            # 恢復後節拍器會把「現在這一手」重新踢一次（見 tick_loop）。
            return {"sent": False, "skipped": "paused", "seat": seat, "reason": kind}
        res = send_kick_to_gm(self.cfg.get("table_id") or "table", seat, text, kind, seq,
                       port=self.cfg.get("port"), home=self.home, turn_key=turn_key,
                       url=self.kick_url, token_file=self.kick_token_file,
                       gm_cli=self.gm_cli, gm_tools=self.gm_tools, base=self.table_base(),
                       gm_token=(self.cfg.get("auth") or {}).get("gm_token"),
                       session=self._session_id())
        # 事件要跟其余落盘走同一把锁：`_emit` 的 seq 是 len(events)+1，两脚同时回来会撞号
        # （RLock ⇒ 与在锁内调 `_emit` 的既有路径相容）。
        with self.table.lock:
            self.table._emit("kick", public=False, seat="gm", by=seat, reason=kind,
                             round_no=self._round_no(),
                             turn_seq=res.get("turn_seq") or turn_key,
                             url=bool(res.get("url")), ok=bool(res.get("sent")), detail=res)
        return res
    def _session_id(self) -> str:
        fn = getattr(self.table, "session_id", None)
        try:
            return str(fn() if callable(fn) else (self.table.adapter_view().get("run_id") or ""))
        except Exception:                                                    # noqa: BLE001
            return ""
    def _session_name(self, seat: str, sess: str) -> str:
        tid = str(self.cfg.get("table_id") or "table")
        return f"trpg-{tid}-{sess or 'r0'}-{seat}"
    def kick(self, seat: str, turn_seq: int, force: bool = False,
             reason: str = "turn", manual: bool = False) -> Dict[str, Any]:
        # 整桌暫停（總閘）：**自動的腳一律不出去**（回合踢／開景／私訊／點名／收景）。
        # 只有 `manual=True`（GM 在 /api/gm op=kick 手動補踢）例外——那是人為的覆寫。
        # ⚠ 別用 `force` 當判據：`force` 的語意是「繞過冪等鍵」（私訊／點名／開景用它），
        #   拿它當「人的手」會讓暫停期間的私訊照樣叫醒她（2026-10-06 寫測試時抓到）。
        if self.table.adapter_view()["paused"] and not manual:
            return {"seat": seat, "turn_seq": turn_seq, "skipped": "paused"}
        # 幂等键带本局 id：重开一局后 scene1 不会和上一局的 scene1 撞车
        # （对方按 turn_seq 去重，撞车会被静默当成 duplicate 丢掉）
        run = self.table.adapter_view()["run_id"] or "r0"
        ts = str(turn_seq)
        if not ts.startswith(f"{run}:"):
            ts = f"{run}:{ts}"
        turn_seq = ts
        key = f"{seat}:{turn_seq}"
        if key in self.kicked and not force:
            return {"seat": seat, "turn_seq": turn_seq, "skipped": "already_kicked",
                    "result": self.kicked[key]}
        seat_cfg = (self.cfg.get("wake", {}).get("seats", {}) or {}).get(seat) or {}
        _seats = self.table.adapter_view()["seats"]
        seat_kind = (_seats.get(seat) or {}).get("kind")
        # ★ 2026-10-20（設計者定案）：**kick 按帳號，不按席位**——「每個 agent 端末（端末-角色區分）
        #   有一個帳號，這個帳號全桌可用，帳號綁定一個 wake url」。以前這三行（state 的 wake_url／
        #   wake_urls／`config.wake.seats.<席>.url`）全是「席位 → 端點」⇒ 實測踢到了沒參賽的橋
        #   （示範劇本 13:04：p4 由本機 GM agent 入座，kick 卻打到 外部 agent 橋並 200）。
        #   現在：seat → 本局該席的 account → 該帳號的 wake_url；沒有帳號就不踢（可見，不靜默）。
        #   席位寫死的 URL 只在 `wake.legacy_seat_url: true` 的遷移期臨時生效（事件標 legacy-seat）。
        tgt = self.wake_endpoint(seat)
        url = tgt.get("url") or ""
        token = (tgt.get("token") or seat_cfg.get("token")
                 or (self.cfg.get("wake", {}) or {}).get("token"))
        via = str(tgt.get("via") or "")
        if (not url and tgt.get("why") == "no-account"
                and bool((self.cfg.get("wake") or {}).get("legacy_seat_url"))):
            url = ((_seats.get(seat) or {}).get("wake_url") or self.wake_urls.get(seat)
                   or seat_cfg.get("url") or "")
            if url:
                via = "legacy-seat"
        prompt = self.kick_prompt(seat, reason=reason)
        # ★ session 規範（設計者 2026-10-07）：**一個 session 對應一個副本實例**。
        #   `session`＝這一副本的 id（＝引擎 run_id），寫進 body；收端據此開**這一副本專屬**的會話。
        sess = self._session_id()
        if sess:
            prompt += (f"\n【本副本 session】{sess}——呼叫寫入類工具（mcp_table_table_act／table_say／"
                       f"table_tell…）時要帶 session=\"{sess}\"；換一局（新副本）就是新的 session。")
        payload = {"kind": "trpg_turn", "table_id": self.cfg.get("table_id"), "seat": seat,
                   "session": sess,
                   "session_name": self._session_name(seat, sess),
                   # ★ 2026-10-20：`reason` 也送出去（`turn`／`私訊`／`開景`／`輪`／`終局`）。
                   #   橋拿它分岔：**回合踢不看下線**（回合是參賽者的義務），其他踢才走離線代理。
                   "reason": str(reason or ""),
                   "turn_seq": turn_seq, "prompt": prompt}
        res: Dict[str, Any]
        if not url:
            if seat_kind == "human":
                # 人类席位的"轮到你"由他自己的界面（网页/消息）承担，不需要外踢，也不记噪音
                self.kicked[key] = "human-seat:no-kick-needed"
                return {"seat": seat, "turn_seq": turn_seq, "skipped": "human seat"}
            # 按帳號踢之後，「沒端點」不再是一句籠統的錯誤，而是講清楚是哪一種：
            #   no-account（這席沒綁帳號 ⇒ 不踢，這正是「踢到沒參賽的人」的來源）／
            #   account-no-url（帳號在、但沒登記 wake_url）／no-store（registry 讀不到）。
            _why = str(tgt.get("why") or "no-wake-url")
            res = {"sent": False, "why": _why, "via": via, "account": tgt.get("account") or None,
                   "hint": ("把喚醒端點登記到帳號上：`python3 tools/set_wake.py --config <桌config> "
                            "<account> <url>`（或 DB 的 account.wake_url）；"
                            "席位寫死的 wake.seats.<seat>.url 已退役")}
        else:
            try:
                req = urllib.request.Request(
                    url, data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json",
                             **({"X-Table-Kick-Token": str(token)} if token else {})},
                    method="POST")
                with urllib.request.urlopen(req, timeout=float(self.cfg.get("wake", {}).get("timeout_seconds", 10))) as resp:
                    res = {"sent": True, "status": resp.status, "body": resp.read(400).decode("utf-8", "replace")}
            except urllib.error.HTTPError as exc:
                res = {"sent": False, "status": exc.code, "error": exc.read(300).decode("utf-8", "replace")}
            except Exception as exc:  # noqa: BLE001
                res = {"sent": False, "error": f"{type(exc).__name__}: {exc}"}
        self.kicked[key] = json.dumps(res, ensure_ascii=False)
        # 静默丢弃不许可：kick 成败都进日志——**但不对玩家公开**（2026-10-07 玩家平等原则：
        # 只有 agent 席位会被叫醒 ⇒ 公开看到 kick 就等于标出谁是 AI）。事件流照写，
        # GM 走 raw 那条／观战之外的裁判侧照看得到。
        # `via`＝這一腳照誰的端點出去的（`account:<id>`／`no-account`／`legacy-seat`）——出事時一眼看得出來。
        self.table._emit("kick", public=False, seat=seat, turn_seq=turn_seq, reason=reason,
                         round_no=(self._round_no()), via=via,
                         account=tgt.get("account") or None,
                         url=bool(url), ok=bool(res.get("sent")), detail=res)
        return {"seat": seat, "turn_seq": turn_seq, "result": res, "prompt": prompt}
    def _acct_store(self):
        """這一桌的 account registry（**延遲載入、快取一份**；kick 按帳號之後每次都要查）。"""
        acc = getattr(self, "_acct_cache", None)
        if acc is None:
            from trpg.accounts import account_store as _account_store          # noqa: PLC0415
            # ★ 帶這一局的 run_id：DB 後端的 `account_of_seat` 才會先讀 `run_seat`
            #   （這一局真正的參賽者），而不是桌級 `seat_assignment` 的上一局座次。
            _rid = ""
            try:
                _rid = str(self.table.session_id() or "")
            except Exception:                                                  # noqa: BLE001
                _rid = ""
            acc = _account_store.accounts_for(self.home, str(self.cfg.get("table_id") or ""),
                                              self.cfg, run_id=_rid)
            self._acct_cache = acc
        return acc
    def wake_endpoint(self, seat: str) -> Dict[str, Any]:
        """**按帳號**解析這個席位的喚醒端點（2026-10-20 設計者定案）。

        回 `{url, token, via, account, why}`：
          * `account:<id>`＋url ⇒ 踢；
          * `no-account`（這席沒綁帳號）／`account-no-url`（帳號沒登記端點）／`no-store` ⇒ 不踢，
            `why` 講清楚是哪一種（kick 事件會帶出去，不靜默）。
        token 從帳號的 `wake_token_file` 讀（600 檔）；沒有就回空字串、由呼叫端回退桌 config。
        """
        try:
            store = self._acct_store()
        except Exception as exc:                                             # noqa: BLE001
            return {"url": "", "token": "", "via": "no-store", "account": None,
                    "why": f"account-store:{type(exc).__name__}"}
        try:
            account = str(store.account_of_seat(seat) or "")
        except Exception as exc:                                             # noqa: BLE001
            return {"url": "", "token": "", "via": "no-store", "account": None,
                    "why": f"account-of-seat:{type(exc).__name__}"}
        if not account:
            return {"url": "", "token": "", "via": "no-account", "account": None, "why": "no-account"}
        try:
            w = store.wake_of(account) or {}
        except Exception:                                                    # noqa: BLE001
            w = {}
        url = str(w.get("url") or "")
        # token 也按帳號（2026-10-20 續）：檔案（600）優先，其次帳號上的明文值。
        token = self._read_token_file(str(w.get("token_file") or "")) or str(w.get("token") or "")
        if not url:
            return {"url": "", "token": token, "via": f"account:{account}", "account": account,
                    "why": "account-no-url"}
        return {"url": url, "token": token, "via": f"account:{account}", "account": account, "why": ""}
    @staticmethod
    def _read_token_file(path: str) -> str:
        """讀 kick token 檔（600）；沒有／空＝空字串（token 不進事件流、不進 prompt）。"""
        if not path:
            return ""
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError:
            return ""
    def _round_no(self) -> int:
        """当前轮号（2026-10-21 拆景后唯一的节拍单位）。"""
        return int(self.table.adapter_view().get("round_no") or 0)
    def wake_key(self, seat: str) -> Optional[str]:
        """GM 喚醒的收斂鍵＝**桌（預設）或坐席**＋輪＋**GM 最後一次出聲的序號**。

        原本只用「坐席＋景」，等於一個景裡同一個席位只喚得到一次——那一段一長，玩家後面說什麼
        都送不出去（2026-10-05 設計者的桌就這樣卡住：GM 沉默一小時，玩家只看到「思考中」）。
        加上 GM 的出聲序號後：**GM 沒回過就一直可以喚，回過一次就不重複喚**——
        收斂效果一樣（不會一句一喚），但不會靜默卡死。

        再加一層（2026-10-06，config `kick.coalesce_scope`，預設 "table"）：鍵**不含坐席**
        ⇒ 同一輪裡「GM 還沒回」這段時間，兩個玩家各丟一句只喚一次（第二句在喚醒訊息帶的
        桌況與桌面日誌裡，GM 那一輪看得到）。設 "seat" 可回到舊行為（每席各喚一次）。
        """
        if not seat:
            return None
        rno = self._round_no()
        last_gm = int(self.table.adapter_view().get("last_gm_seq") or 0)
        scope = str(((self.cfg.get("kick") or {}).get("coalesce_scope") or "table")).lower()
        who = seat if scope == "seat" else "table"
        return f"{self.cfg.get('table_id')}:喚醒:{who}:round{rno}:gm{last_gm}"
    def kick_on_mention(self, *, speaker: str, text: str, seq: int) -> List[Dict[str, Any]]:
        """玩家发言里**直接叫到某个 agent 席位的名字** → 立刻推一次（push）。

        这是「agent 收不到玩家发言」的正解：**不推每一句**（会把它炸掉），只推**点名**的那些；
        每輪每 agent 限 `config.wake.mention_kick_per_round` 次（默认 1；旧键 `mention_kick_per_scene` 也认），
        幂等键 = say<seq>。
        """
        wake = self.cfg.get("wake") or {}
        if not wake.get("mention_kick", True):
            return []
        per = int(wake.get("mention_kick_per_round", wake.get("mention_kick_per_scene", 1)))
        if per <= 0:
            return []
        rno = self._round_no()
        out = []
        _seats = self.table.adapter_view()["seats"]
        for sid, spec in self.table.seats_spec.items():
            if spec.get("kind") != "agent" or sid not in _seats:
                continue
            s = _seats[sid]
            names = {n for n in (sid, s.get("name"), spec.get("name"), spec.get("character")) if n}
            if not any(n in text for n in names):
                continue
            used = sum(1 for e in self.table.events
                       if e.get("type") == "kick" and e.get("reason") == "mention"
                       and e.get("seat") == sid and e.get("round_no") == rno)
            if used >= per:
                continue
            out.append(self.kick(sid, f"say{seq}", force=True, reason="mention"))
        return out
    def _wake_gm_on_turn_end(self) -> None:
        """每席的回合結束就叫 GM 一次（`wake.gm_on_turn_end: true`）。

        設計者 2026-10-06：「GM 不能做成 P1結束被叫、P2結束被叫…這樣？」
        ——可以，代價是 GM 一輪被叫 N 次（N＝玩家席數），而接收器是**一次跑一輪**（FIFO 佇列）
        ⇒ GM 寫得比玩家慢時會落後（會看到「落後幾手」的敘事）。

        所以只叫**真的有事**的那一手：這一手的區間裡既沒有 `acted`、也沒有公開發言 ⇒ 不叫
        （pass／超時／純發呆）。一次 tick 只處理**最後一筆** `turn_ended`（避免一次補叫一整串）。
        """
        evs = self.table.events
        since = int(getattr(self, "last_seatwake_seq", 0) or 0)
        done = [e for e in evs if e.get("type") == "turn_ended" and int(e.get("seq", 0)) > since]
        if not done:
            return
        e = done[-1]
        self.last_seatwake_seq = int(e.get("seq") or 0)
        end_seq = int(e.get("seq") or 0)
        start = 0
        for x in evs:                      # 這一手是從哪個 turn_started 開始的
            if x.get("type") == "turn_started" and int(x.get("seq", 0)) < end_seq:
                start = int(x.get("seq", 0))
        seat = e.get("seat") or ""
        win = [x for x in evs if start < int(x.get("seq", 0)) < end_seq]
        # 「這一手有事」＝這一手的區間裡有**該席位自己做過的事**。
        # 用**黑名單**（不是白名單）：動作事件種類很多（action_declared／roll／search_loot／
        # memory_change／say／tell…），白名單一定會漏（2026-10-06 線上踩到：p3 用 `search`
        # 出手的那一手只發了 action_declared＋roll，沒有 `acted` ⇒ 用白名單會靜默不叫）。
        # 「這一手有事嗎」的記帳型事件（不算內容）：輪界／踢腳／節流計數，以及半合作的
        # 「我這一輪不動（`ended`）」「這一輪出手過（`acted`）」——那兩個是記號，不是內容。
        _book = {"turn_started", "turn_ended", "turn_extended", "seat_timeout",
                 "round_started", "round_ended", "kick", "throttle_tick", "acted", "ended"}
        if not [x for x in win if x.get("seat") == seat and x.get("type") not in _book
                and x.get("public") is not False]:
            return                         # 空手收手（pass／超時／純發呆／被規則拒掉的那一手）：不叫
        digest = self.table.render_log_tail(since_seq=start, limit=20)
        text = (f"{seat} 的回合結束（{e.get('reason')}）。這一手發生的事：\n"
                + ("\n".join(digest) if digest else "（沒有公開動作）"))
        self.wake_gm(seat or "p1", text, "手", 0,
                     turn_key=f"{self.cfg.get('table_id') or 'table'}:手:{end_seq}")
    def tick_loop(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.table.tick()
                if self.table.adapter_view().get("ended"):
                    # 結局已判＝這一局結束（2026-10-07 設計者抓到「收尾了還在倒數」）：
                    # **不踢任何席位（含 GM）**——牌面與時間軸都停了（pacing.tick 已凍）。
                    # ★2026-10-08 唯一的例外：**一次性**把「這一局結束了」推給每個 agent 席位
                    #   （agent 的記憶是它自己的；冪等鍵在手，重啟也不補叫）。
                    maybe_kick_ending(self)
                    self.last_turn_key = None
                    stop.wait(2.0)
                    continue
                if self.table.adapter_view()["paused"]:
                    # 整桌暫停（總閘）：這一圈什麼都不踢（計時／超時已在 engine.tick 內凍住），
                    # 並把「現在這一手」的鍵忘掉 ⇒ **恢復後重新踢一次**（暫停期間那一腳被閘掉了，
                    # 不重踢的話她／GM 就永遠等不到這手）。
                    self.last_turn_key = None
                    stop.wait(2.0)
                    continue
                _ops = self.table.adapter_view()
                after = _ops["turn"]
                akey = f"{after['seat']}:{after['seq']}" if after else None
                # 只认「回合换了」这一个判据：无论回合是被 API、GM 还是节拍推进的，
                # 都在这里唯一地踢一次（幂等键 = seat:turn_seq）。
                if akey and akey != self.last_turn_key:
                    self.kick(after["seat"], after["seq"], reason="turn")
                self.last_turn_key = akey
                # 叫 GM 的節奏（2026-10-21 拆景後：**每輪一次**，只有一個來源）
                #   `wake.gm_on_turn_end: true`＝每席結束就叫（舊選項，預設關）——兩種**互斥**，
                #   同時開會同一件事叫兩次（實測過：一輪邊界收到 2 條）。
                #   預設走 `maybe_kick_round`：輪開始叫一次，鍵＝桌:round:<n>:<session>。
                if bool((self.cfg.get("wake") or {}).get("gm_on_turn_end")):
                    self._wake_gm_on_turn_end()
                else:
                    maybe_kick_round(self)
            except Exception as exc:  # noqa: BLE001
                print(f"[tick] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            stop.wait(2.0)
