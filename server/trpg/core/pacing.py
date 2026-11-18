#!/usr/bin/env python3
"""**L0｜節拍閘**（`PacingMixin`）——整桌暫停／解除、超時策略、自動開局。

從 `engine.py` 切出來（2026-10-06 結構整理）：這一組管的是**時間軸**，與裁決、投影分開擺。

紀律不變：
- **不讀時鐘**——`now` 由參數或注入的 `self.now()` 來（暫停/超時全部可重放、可餵假時鐘）。
- **不碰檔案／網路**——事件只交給 `self.store`（sink），那是 L6 的事。
- **不產人話**——超時代打那句話走 L4 句庫（`self.phrase`＝這一桌的那一本）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from trpg.core.errors import RuleError  # 规则拒绝的共用异常


class PacingMixin:
        # -- 整桌暫停（總閘，2026-10-06 設計者要的按鈕） -----------------------------

        def pause(self, *, reason: str = "manual", by: str = "") -> Dict[str, Any]:
            """整桌暫停：**凍結節拍**——不再叫醒任何席位（含 GM）、不再計時、超時不代打、不自動開局。

            進事件流（`public=True`）⇒ 重放一致、前端與 app 都看得到「已暫停」。桌上已有的狀態
            （回合、景、牌面）一律不動：暫停是**時間軸**上的閘，不是把桌子拆掉。
            """
            with self.lock:
                if self.state.get("paused"):
                    return {"ok": True, "paused": True, "already": True}
                ev = self._emit("pacing_paused", public=True, reason=reason, by=by,
                                paused_at=self.now())
                return {"ok": True, "paused": True, "seq": ev.get("seq"), "event": ev}

        def resume(self, *, reason: str = "manual", by: str = "") -> Dict[str, Any]:
            """解除暫停：把暫停期間凍掉的時間**還給當前回合**（死線整段順延）⇒ 恢復後不會立刻被判超時。

            恢復後由 server 的節拍器把「現在這一手」**重新踢一次**（暫停期間那一腳被閘掉了）。
            """
            with self.lock:
                if not self.state.get("paused"):
                    return {"ok": True, "paused": False, "already": True}
                paused_at = float(self.state.get("paused_at") or 0)
                frozen = max(0.0, self.now() - paused_at) if paused_at else 0.0
                ev = self._emit("pacing_resumed", public=True, reason=reason, by=by,
                                frozen_seconds=round(frozen, 1))
                # ⚠ 死線的順延**只做一次**：`_emit` 內部的 `_apply` 已經照事件把 dead_line 推後了
                #   （重放要一致 ⇒ 必須由事件驅動）。這裡再推一次就會**推兩倍**
                #   （2026-10-06 線上實測：暫停 14 秒，`seconds_left` 回來變成 +28）。
                return {"ok": True, "paused": False, "frozen_seconds": round(frozen, 1),
                        "deadline_at": (self.state.get("turn") or {}).get("deadline_at"),
                        "seq": ev.get("seq"), "event": ev}

        def tick(self, now: Optional[float] = None) -> List[Dict[str, Any]]:
            """由 server 的节拍线程调用：超时策略 + 自动开局。"""
            out: List[Dict[str, Any]] = []
            with self.lock:
                if self.state.get("paused"):     # 整桌暫停：計時／超時／自動開局全凍（時間軸停住）
                    return out
                if self.state.get("ended"):
                    # **這一局已經判了結局 ⇒ 時間軸停住**（2026-10-07 設計者抓到：收尾後還在倒數／還在換手／
                    # 還在踢 agent）。跟暫停同一立場：凍的是**牌面與時間軸**，不是對話（say／tell 照常）。
                    return out
                self._maybe_autostart()
                cur = self.state["turn"]
                if not cur:
                    return out
                now = now or self.now()
                if now < cur["deadline_at"]:
                    return out
                # 逐席節拍：等待／超時也照「這一手是誰的」決定（`turn.*_by_kind`）
                cfg = (self.turn_cfg_for(cur["seat"]) if hasattr(self, "turn_cfg_for")
                       else self.turn_cfg)
                policy = cfg.get("policy_on_timeout", "wait")
                if policy == "wait" and cur["waits"] < int(cfg.get("max_waits", 1)):
                    dl = now + int(cfg.get("wait_extra_seconds", 60))
                    out.append(self._emit("turn_extended", public=True, seat=cur["seat"],
                                          turn_seq=cur["seq"], waits=cur["waits"] + 1, deadline_at=dl))
                elif policy in ("pass", "wait"):
                    # `seat_timeout` 不對玩家公開（2026-10-07 玩家平等原則）：誰被系統跳過＝誰的節奏差異，
                    # 公開等於提示「這一席不是人」。事件流照寫，GM 走 raw 那條照看得到。
                    out.append(self._emit("seat_timeout", public=False, seat=cur["seat"],
                                          turn_seq=cur["seq"], policy="pass"))
                    self.end_turn(reason="timeout")
                elif policy == "default_act":
                    out.append(self._emit("seat_timeout", public=False, seat=cur["seat"],
                                          turn_seq=cur["seq"], policy="default_act"))
                    self.act(seat=cur["seat"], action="defend",
                                         note=self.phrase("timeout.defend_note"))
                else:
                    raise RuleError(f"未知超时策略：{policy}")
            return out

        def _maybe_autostart(self) -> None:
            cfg = self.turn_cfg              # config.turn ＋ 節拍包的覆寫
            if self.state.get("ended"):
                return                        # 結局已判：不再自動開局
            if not cfg.get("auto_start_first_turn", True):
                return
            if self.state["turn"]:
                return
            if len(self._alive_order()) < int(cfg.get("min_seats_to_start", 2)):
                return
            # ★ 2026-10-21（設計者定案：拆景）：節拍只剩「輪」——人齊了就開第一輪，
            #   不再等 GM 開景（GM 的工作改成「每輪說一句」）。
            self.start_turn(reason="autostart")
