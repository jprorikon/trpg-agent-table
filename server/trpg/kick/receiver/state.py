"""state（自 __init__.py 拆出）"""

import argparse
import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import quote
from trpg.paths import ROOT as HERE  # repo 根（資料／vendor／docs 都在這裡）

from .common import DEFAULT_API_KEY_FILE, log_line, profile_env_path, read_api_key, session_name_for

class State:
    """運行態：token、GM agent 位址、冪等鍵（去重表持久化在 json）。"""

    def __init__(self, args) -> None:
        self.token_file = args.token_file
        self.state_file = args.state_file
        self.api = args.api.rstrip("/")
        # 桌的 config 先讀進來：kick.gm_api_profile 可以在 CLI 沒給時決定 profile（配置驅動；
        # 位置與 wake.seats 同一個檔）。
        _cfgp = str(getattr(args, "config", "") or "")
        self.config = json.load(open(_cfgp, encoding="utf-8")) if (_cfgp and os.path.exists(_cfgp)) else {}
        _kick = self.config.get("kick") or {}
        # 多 profile 多路复用（2026-10-06）：GM 用「只给桌工具」的瘦 profile 跑，
        # 它的路由是 `/p/<profile>/...`，而且**次级 profile 在 api_server 上是 fail-closed**，
        # 必须用该 profile 自己的 API_SERVER_KEY（不是 default 那把）。
        self.profile = ((getattr(args, "api_profile", "") or "").strip()
                        or str(_kick.get("gm_api_profile") or "").strip())
        self.api_prefix = f"/p/{self.profile}" if self.profile else ""
        key_file = getattr(args, "api_key_file", "") or ""
        if not key_file and self.profile:
            # 沒指定 key 檔又走了 profile ⇒ 讀那個 profile 自己的 .env（fail-closed 那把）
            key_file = profile_env_path(self.profile)
            if not os.path.exists(key_file):
                key_file = DEFAULT_API_KEY_FILE
        if not key_file:
            key_file = DEFAULT_API_KEY_FILE
        self.api_key_name = (getattr(args, "api_key_name", "") or "").strip() or (
            "API_SERVER_KEY" if self.profile else "HERMES_API_KEY")
        self.api_key = read_api_key(key_file, self.api_key_name)
        self.session_tpl = args.session
        self.log_file = args.log_file
        self.approval_choice = args.approval_choice
        self.approval_window_s = float(args.approval_window_s)
        self.approval_poll_s = float(args.approval_poll_s)
        # 對局歷史（2026-10-06 設計者定案「先改成 2」）：把這條 session 的**文字**歷史帶進每一輪，
        # 讓同一局的前後輪共享同一段前綴（provider 前綴快取命中）＋ GM 不必每輪從零重讀。
        # 只帶 user/assistant 的文字，**不帶工具輸出**（那量級會爆）。
        self.max_read_bytes = int(args.max_read_bytes)
        self.history_limit = int(args.history_limit)
        self.history_max_chars = int(args.history_max_chars)
        # 每輪的「臨時 system 指令」（不准探索的硬規矩）：檔案不存在就留空＝照舊。
        # **每輪重讀**（2026-10-06：原本只在啟動時讀一次 ⇒ 改了檔要重啟接收器才生效，容易踩到
        # 「我明明改了怎麼沒用」）。讀不到就退回啟動時的快照。
        self.instructions_file = getattr(args, "instructions_file", "") or ""
        self.instructions = ""
        self.reload_instructions()
        self.seen: dict = {}
        # 同一桌**同時只跑一輪**（實測 GM 一輪 30~100 秒，玩家手一快就堆出第二、第三輪 ⇒ 只會更慢）
        self.active: dict = {}      # table -> {"run_id":…, "since":…, "key":…}
        self.pending: dict = {}     # table -> [{"prompt","key","seat","session"}…]（跑這一輪時又來的，**排隊**）
        self.lock = threading.Lock()
        try:
            with open(self.state_file, encoding="utf-8") as fh:
                raw = json.load(fh)
            if isinstance(raw, dict) and isinstance(raw.get("seen"), dict):
                self.seen = raw.get("seen") or {}
                self.active = raw.get("active") or {}
                _pend = raw.get("pending") or {}
                # 舊格式（單槽 dict）相容：包成一則的佇列
                self.pending = {k: (v if isinstance(v, list) else [v]) for k, v in _pend.items()}
            elif isinstance(raw, dict):          # 舊格式＝就是一張 key→時刻 的表
                self.seen = raw
        except (OSError, ValueError):
            pass
        self.kick_count = 0
        # 同一桌「跑著時又來的」最多排幾封（滿了丟最舊，會記日誌）
        self.pending_max = max(1, int(getattr(args, "pending_max", 5) or 5))

    # -- token -----------------------------------------------------------------
    def token(self) -> str:
        with open(self.token_file, encoding="utf-8") as fh:
            return fh.read().strip()

    # -- 冪等 ------------------------------------------------------------------
    def _save(self) -> None:
        """把 seen／active／pending 一起落盤（呼叫端自己拿 lock；這裡不再進 lock）。"""
        tmp = self.state_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"seen": self.seen, "active": self.active, "pending": self.pending},
                      fh, ensure_ascii=False)
        os.replace(tmp, self.state_file)

    def first_time(self, key: str) -> bool:
        with self.lock:
            if key in self.seen:
                return False
            self.seen[key] = time.strftime("%Y-%m-%d %H:%M:%S")
            if len(self.seen) > 800:            # 只留最近 800 個鍵，別讓檔長大
                for old in sorted(self.seen, key=self.seen.get)[:200]:
                    self.seen.pop(old, None)
            self._save()
            return True

    def is_dry(self, payload: dict) -> bool:
        """body 的 `dry_run`（協作端 那側的通道自測格）：驗 token／kind／turn_seq／去重後回 200、**不叫醒**。"""
        return payload.get("dry_run") in (True, 1, "1", "true", "True", "yes")

    # -- GM agent API 小工具 ------------------------------------------------------
    def api_call(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            f"{self.api}{self.api_prefix}{path}", data=data,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}",
                     "Accept": "application/json"},
            method=method)
        with urllib.request.urlopen(req, timeout=30) as resp:
            # 讀滿：撈歷史那支（/api/sessions/…/messages）一次就好幾十 KB，
            # 只讀 8KB 會被截斷成壞 JSON（2026-10-06 實測踩到）。
            raw = resp.read(self.max_read_bytes).decode("utf-8", "replace")
        return json.loads(raw) if raw.strip() else {}

    def watch_run(self, run_id: str, table_id: str) -> None:
        """盯一輪：① 替它答 approval 閘；② 跑完時把合併進來的事件叫成下一輪。

        為什麼要盯：無人應答的喚醒會卡在 waiting_for_approval（2026-10-05 第一輪自測就卡住）；
        而 GM 一輪實測要 30~100 秒 ⇒ 同一桌同時只跑一輪，後到的先存 pending，這一輪跑完再合成
        一輪叫醒（kick prompt 本來就帶「你上次出手之後，桌上發生了什麼」的 digest ⇒ 一輪追得上多句）。
        """
        deadline = time.time() + self.approval_window_s
        while time.time() < deadline:
            time.sleep(self.approval_poll_s)
            try:
                status = self.api_call("GET", f"/v1/runs/{run_id}").get("status")
            except Exception as exc:  # noqa: BLE001
                log_line(self.log_file, f"run 狀態讀不到 {run_id}: {exc}")
                continue
            if status in ("completed", "failed", "canceled", "cancelled", "stopped", "error"):
                self.finish_run(table_id, run_id, status)
                return
            if status != "waiting_for_approval":
                continue
            try:
                res = self.api_call("POST", f"/v1/runs/{run_id}/approval",
                                    {"choice": self.approval_choice})
                log_line(self.log_file, f"run {run_id} 答閘 choice={self.approval_choice} "
                                        f"resolved={res.get('resolved')}")
            except Exception as exc:  # noqa: BLE001
                log_line(self.log_file, f"run {run_id} 答閘失敗: {exc}")
        log_line(self.log_file, f"run {run_id} 盯到 {self.approval_window_s:.0f}s 上限放掉（狀態未定）")
        # ★ 2026-10-20：**放掉也要收尾**。只放掉不收尾 ⇒ `active[table]` 永遠記著這個 run，
        #   而 active 會落盤、重啟再讀回來 ⇒ 那一桌之後每一腳都「桌上還有輪在跑」排隊，
        #   整桌靜默停住（實測 run_ddc8f3c2：14:09 就 run.completed 了，接收器仍當它在跑）。
        self.finish_run(table_id, run_id, "timeout")

    def recover_active(self) -> None:
        """啟動時把殘留的 `active` 對一次真值（2026-10-20）。

        為什麼要它：`active` 是落盤的（避免重啟後同一桌並發兩輪），但**盯它的線程活不過重啟**
        ⇒ 重啟之後那些 run 永遠算「在跑」，新的踢全部排隊（實測：GM 其實早就開完景，
        接收器還堵著）。這裡逐一問 `/v1/runs/<id>`：還在跑就把監看接回去；已結束／問不到就收尾
        （收尾會順便把排隊的下一輪叫起來）。
        """
        for table_id, cur in list((self.active or {}).items()):
            run_id = str((cur or {}).get("run_id") or "")
            status = ""
            if run_id:
                try:
                    status = str(self.api_call("GET", f"/v1/runs/{run_id}").get("status") or "")
                except Exception as exc:                                     # noqa: BLE001
                    log_line(self.log_file,
                             f"recover: run 狀態讀不到 {run_id}（{type(exc).__name__}）⇒ 當作已結束")
                    status = ""
            if status in ("running", "queued", "waiting_for_approval"):
                log_line(self.log_file, f"recover: run {run_id} 仍在跑 ⇒ 接回監看")
                threading.Thread(target=self.watch_run, args=(run_id, table_id),
                                 daemon=True).start()
                continue
            log_line(self.log_file, f"recover: run {run_id} 已結束（{status or '查不到'}）⇒ 收尾")
            self.finish_run(table_id, run_id, status or "stale")

    def finish_run(self, table_id: str, run_id: str, status: str) -> None:
        with self.lock:
            cur = self.active.get(table_id) or {}
            if cur.get("run_id") != run_id:
                return
            dur = round(time.time() - float(cur.get("since") or time.time()))
            self.active.pop(table_id, None)
            # 佇列 FIFO：這一輪跑完就叫**最早**排進來的那封（不是後來的蓋掉先來的）
            q = list(self.pending.get(table_id) or [])
            nxt = q.pop(0) if q else None
            if q:
                self.pending[table_id] = q
            else:
                self.pending.pop(table_id, None)
            left = len(q)
            self._save()
        log_line(self.log_file, f"run {run_id} 收尾 {status}（{dur}s）"
                                + (f"；把排隊的下一輪叫起來（佇列剩 {left}）" if nxt else ""))
        if nxt:
            try:
                self.start_gm(table_id, nxt.get("prompt") or "", nxt.get("key") or "",
                              session=nxt.get("session") or "")
            except Exception as exc:  # noqa: BLE001
                log_line(self.log_file, f"排隊輪叫不起來（桌上不受影響）: {exc}")

    # -- 對局歷史 ---------------------------------------------------------------
    def session_history(self, session: str) -> list:
        """這條 GM session 的文字歷史（給 /v1/runs 的 `conversation_history`）。

        `/v1/runs` **不會自己去讀 session 的對話**（橋那邊踩過同一格：不帶就每句都當全新一句），
        所以要我們自己撈 `/api/sessions/{id}/messages` 帶進去。
        - 只收 user／assistant 的**非空文字**；tool 訊息（及其輸出）一律不帶——那量級會爆
          （實測：250 條 tool 訊息合計 41.9 萬字，單條最大 5.6 萬字）。
        - 從最新往回撈到 `history_limit` 則或 `history_max_chars` 字為止；開頭若是 assistant 就丟掉
          （一段對話要從 user 起頭）。
        - 讀不到不算錯（第一次跑就還沒有 transcript）：這一輪照舊不帶歷史。
        """
        if not session or self.history_limit <= 0:
            return []
        try:
            page = self.api_call(
                "GET", f"/api/sessions/{quote(session, safe='')}/messages"
                       f"?limit={self.history_limit}&order=latest")
        except Exception as exc:  # noqa: BLE001 - 讀不到歷史不能擋這一輪
            log_line(self.log_file, f"history: {session} 讀不到（{exc}）⇒ 這輪不帶歷史")
            return []
        rows = page.get("data") if isinstance(page, dict) else None
        if not isinstance(rows, list):
            log_line(self.log_file, f"history: {session} 沒回訊息列表 ⇒ 這輪不帶歷史")
            return []
        out: list = []
        chars = 0
        for row in reversed(rows):            # order=latest ⇒ 最新在前
            if not isinstance(row, dict):
                continue
            role = str(row.get("role") or "")
            if role not in ("user", "assistant"):
                continue
            text = (row.get("content") or "").strip()
            if not text:
                continue
            if chars + len(text) > self.history_max_chars:
                break
            chars += len(text)
            out.append({"role": role, "content": text})
        while out and out[0]["role"] != "user":
            out.pop(0)
        return out

    # -- 叫醒 GM ---------------------------------------------------------------
    def start_gm(self, table_id: str, prompt: str, key: str = "",
                 session: str = "") -> dict:
        """叫醒 GM。`session` 空＝用自帶模板（body 有給就以 body 為準，2026-10-06 與 協作端 對齊）。"""
        session = session or self.session_tpl.replace("{table}", table_id).replace("{session}", "r0")
        history = self.session_history(session)
        payload: dict = {"input": prompt, "session_id": session}
        if history:
            payload["conversation_history"] = history
        if self.reload_instructions():
            # 每輪帶一段「臨時 system 指令」（--instructions-file，預設 gm_instructions.txt）：把「不准探索」
            # 寫死——實測慢的幾輪都是 GM 跑去 shell 探索（22 次工具呼叫 ⇒ 端到端 90 秒）。
            # **每輪重讀檔**（改了檔不用重啟接收器）。
            payload["instructions"] = self.instructions
        body = self.api_call("POST", "/v1/runs", payload)
        run_id = body.get("run_id") or body.get("id")
        if run_id:
            with self.lock:
                self.active[table_id] = {"run_id": str(run_id), "since": time.time(), "key": key}
                self._save()
            threading.Thread(target=self.watch_run, args=(str(run_id), table_id),
                             daemon=True).start()
        return {"woke": "gm", "session": session, "run_id": run_id,
                "history": len(history),
                "history_chars": sum(len(h["content"]) for h in history),
                "instructions": bool(self.instructions)}

    # -- 轉送別的席位 ----------------------------------------------------------
    def forward(self, seat: str, payload: dict) -> dict:
        seats = ((self.config.get("wake") or {}).get("seats") or {})
        cfg = seats.get(seat) or {}
        url = payload.get("_wake_url") or cfg.get("url")
        if not url:
            return {"forwarded": None, "why": f"no wake url for seat {seat}"}
        body = json.dumps({k: v for k, v in payload.items() if not k.startswith("_")}).encode()
        headers = {"Content-Type": "application/json"}
        if cfg.get("token"):
            headers["X-Table-Kick-Token"] = str(cfg["token"])
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return {"forwarded": seat, "url": url, "status": resp.status}
        except urllib.error.HTTPError as exc:
            return {"forwarded": seat, "url": url, "status": exc.code}
        except Exception as exc:  # noqa: BLE001
            return {"forwarded": seat, "url": url, "error": f"{type(exc).__name__}: {exc}"}

    # -- 一個 kick 進來 --------------------------------------------------------
    def reload_instructions(self) -> str:
        """重讀臨時指令檔（每輪呼叫）；讀不到就保留舊的（啟動時的快照）。"""
        if not self.instructions_file:
            return self.instructions
        try:
            with open(self.instructions_file, encoding="utf-8") as fh:
                txt = fh.read().strip()
        except OSError:
            return self.instructions
        if txt:
            self.instructions = txt
        return self.instructions

    def handle_kick(self, payload: dict) -> dict:
        table_id = str(payload.get("table_id") or "table")
        seat = str(payload.get("seat") or "")
        turn_seq = str(payload.get("turn_seq") or "")
        prompt = str(payload.get("prompt") or "")
        key = f"{table_id}:{seat}:{turn_seq}"
        # session 名：**body 優先**（引擎送 `session_name`＝這一副本的會話名；舊版送 `session` 這個
        # 名字本身，也吃）。都沒給才用自帶模板 `trpg-{table}-{session}-gm`。
        session, session_src = session_name_for(payload, table_id, self.session_tpl)
        self.kick_count += 1
        # 冪等：**先寫 seen 再判重**（與 協作端 同語意 ⇒ 重打同一顆 `dry_run` 會回 duplicate）
        if not self.first_time(key):
            log_line(self.log_file, f"kick dup {key} → 不重複叫醒")
            return {"ok": True, "duplicate": True, "key": key}
        if self.is_dry(payload):
            log_line(self.log_file, f"kick dry_run {key} → 驗過 token／去重，不叫醒"
                                    f"（session={session}，來源={session_src}）")
            return {"ok": True, "dry_run": True, "key": key, "session": session,
                    "session_src": session_src}
        # 收件人：引擎目前只送「桌:發話席位:私訊/公開發言」這條 GM 喚醒路（send_kick_to_gm），
        # payload 沒帶 to ⇒ 預設 gm。等引擎把 to 帶出來（p3 那種「喚醒收件人」）就自動分流。
        to = str(payload.get("to") or "gm")
        if to == "gm":
            dropped = None
            q_len = 0
            with self.lock:
                busy = (self.active.get(table_id) or {}).get("run_id")
                if busy:
                    # 佇列（2026-10-06）：以前是**單槽**，跑著時來的第二封會蓋掉第一封（靜默丟一封）
                    # ⇒ 排隊，跑完依序叫（kick prompt 自帶「你上次出手之後發生了什麼」的 digest）。
                    q = list(self.pending.get(table_id) or [])
                    q.append({"prompt": prompt, "key": key, "seat": seat,
                              "session": session, "session_src": session_src})
                    if len(q) > self.pending_max:
                        dropped = q.pop(0)          # 滿了丟**最舊**的一封（會記進日誌，不靜默）
                    self.pending[table_id] = q
                    q_len = len(q)
                    self._save()
            if busy:
                log_line(self.log_file, f"kick gm {key} → 桌上還有輪在跑（{busy}），"
                                        f"排進下一輪（佇列 {q_len}，不靜默丟）")
                if dropped:
                    log_line(self.log_file,
                             f"pending 佇列已滿（{self.pending_max}）⇒ 丟最舊的一封："
                             f"key={dropped.get('key')}（內容仍在桌的 your_secrets／公開日誌）")
                return {"ok": True, "key": key, "coalesced": True, "waiting_on": busy,
                        "queued": q_len, "dropped": (dropped or {}).get("key")}
            try:
                res = self.start_gm(table_id, prompt, key, session=session)
                log_line(self.log_file,
                         f"kick gm {key} → 起 session {res.get('session')}（來源={session_src}）"
                         f" run={res.get('run_id')}"
                         f"（帶歷史 {res.get('history')} 則／{res.get('history_chars')} 字"
                         f"，臨時指令={'有' if res.get('instructions') else '無'}）")
                return {"ok": True, "key": key, **res}
            except Exception as exc:  # noqa: BLE001
                log_line(self.log_file, f"kick gm {key} → 叫醒失敗（session={session}，"
                                        f"來源={session_src}；桌上不受影響）: "
                                        f"{type(exc).__name__}: {exc}")
                return {"ok": False, "key": key, "error": f"{type(exc).__name__}: {exc}"}
        res = self.forward(seat, payload)
        log_line(self.log_file, f"kick {seat} {key} → 轉送 {json.dumps(res, ensure_ascii=False)}")
        return {"ok": True, "key": key, **res}
