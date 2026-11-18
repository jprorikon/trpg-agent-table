"""rounds（回合制专用；2026-10-21 取代 scenes.py）。

**为什么把「景」整层删掉**（设计者定案）：引擎只跑回合制以后，一景＝一轮已经重叠成一个单位，
两层节拍（景＋回合）只会制造「开了景没人动」「一轮里两个人同时出手」这类问题。
现在唯一的节拍单位是**轮（round）**：

  * 轮开始：`start_turn` 第一席时把本轮的出手计数／结束标记清空。
  * 轮结束：最后一席 `end_turn` ⇒ ①揭晓本轮的争夺宣言（`declare`）②主动怪出手 ③发 `round_ended`
    ④自动接下一轮第一席。
  * GM 的「每轮一句」：轮末引擎叫 GM，GM 用 `say` 说一句（不再有 `scene_open(text)`）。
  * 半合作的 `show／give／wish` **随时可用**（不再需要开景）。

保留的语义：`pending_actors()`（谁还没用完这一轮的行动格）、`semic_mark_ended()`（pass＝这轮我不动）、
`wait_for_all_ends`（要全员表明结束才进下一轮）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from trpg.core.errors import RuleError  # 与 engine.RuleError 同一个类（本模块不 import engine，避免循环依赖）


class SemiCoopRoundsMixin:
    # ------------------------------------------------------------------ 轮界
    def semic_round_start(self) -> None:
        """新一轮开始：清空「这一轮谁出过手／谁明确结束」。"""
        if not getattr(self, "semic_enabled", False):
            return
        st = self.state.get("semi") or {}
        if not st:
            return
        st["acted"] = {}
        st["ended"] = {}

    def semic_round_resolve(self) -> Dict[str, Any]:
        """轮末结算：揭晓本轮宣言（各骰比大小）＋主动怪出手。回一份摘要。"""
        if not getattr(self, "semic_enabled", False):
            return {"resolved": [], "monster_attacks": {"attacks": 0}}
        st = self.state.get("semi") or {}
        resolved = self._resolve_pending() if callable(getattr(self, "_resolve_pending", None)) else []
        atk = self.board_scene_attacks() if hasattr(self, "board_scene_attacks") else {"attacks": 0}
        _ = st
        return {"resolved": resolved, "monster_attacks": atk}

    # ------------------------------------------------------------------ 谁还没完
    def pending_actors(self) -> List[str]:
        """这一轮还在等的人（human／agent；GM 与 NPC 不算）。

        * 默认（`rules.wait_for_all_ends` 关）：出手过一次就不算等。
        * 开启时：要嘛自己按「结束这一轮」（`pass`），要嘛行动格用完，才算结束。
        """
        st = self.state.get("semi") or {}
        out: List[str] = []
        wait_all = bool((getattr(self, "rules", {}) or {}).get("wait_for_all_ends"))
        for seat, spec in (getattr(self, "seats_spec", {}) or {}).items():
            if spec.get("kind") not in ("human", "agent"):
                continue
            if seat not in self.state.get("seats", {}):
                continue
            # 死亡送還：缺席中的人这一轮不等（不然永远收不了轮）
            if int((self.state.get("skip") or {}).get(seat) or 0) > 0:
                continue
            if wait_all:
                if (st.get("ended") or {}).get(seat):
                    continue
                used = int((((st.get("counters") or {}).get(seat)) or {}).get("act", 0))
                if used >= self._round_limit(seat, "act"):
                    continue                      # 次数用完＝没什么可做了
                out.append(seat)
            elif not (st.get("acted") or {}).get(seat):
                out.append(seat)
        return out

    # ------------------------------------------------------------------ 标记
    def semic_mark_ended(self, seat: str) -> None:
        """「这一轮我不动了」——玩家按「结束回合」或 pass。走事件，回放一致。"""
        if not getattr(self, "semic_enabled", False):
            return
        st = self.state.get("semi") or {}
        if not st:
            return
        if (st.get("ended") or {}).get(seat):
            return
        self._emit("ended", public=True, seat=seat, round_no=int(self.state.get("round_no") or 0) + 1)

    def semic_mark_acted(self, seat: str, note: Optional[str] = None) -> None:
        """记「这一轮此人已出手」（每轮每人只记一次；也走事件，回放一致）。"""
        if not getattr(self, "semic_enabled", False):
            return
        st = self.state.get("semi") or {}
        if not st:
            return
        if (st.setdefault("acted", {})).get(seat):
            return
        self._emit("acted", public=True, seat=seat, note=note,
                   round_no=int(self.state.get("round_no") or 0) + 1)

    # ------------------------------------------------------------------ 宣言结算
    def _resolve_pending(self) -> List[Dict[str, Any]]:
        st = self.state["semi"]
        out: List[Dict[str, Any]] = []
        groups: Dict[tuple, List[Dict[str, Any]]] = {}
        for p in list(st.get("pending") or []):
            groups.setdefault((p["kind"], p["node"]), []).append(p)
        for (kind, node), rows in groups.items():
            rolls = []
            for p in rows:
                if not self.state["seats"].get(p["seat"]):
                    continue
                r = self._roll(seat=p["seat"], kind=f"contest:{kind}",
                               reason=self.phrase("contest.rows", kind=kind, node=node, n=len(rows)),
                               mod=self._mod(self.attrs_of(p["seat"]), "wil"), dc=None)
                rolls.append((r["total"], r["roll_seq"], p, r))
            rolls.sort(key=lambda x: (-x[0], x[1]))
            winner = rolls[0][2]["seat"] if rolls else None
            for total, seq, p, r in rolls:
                ev = self._emit("declare_resolved", public=True, declare_seq=p["seq"],
                                seat=p["seat"], node=node, kind=kind, roll=r,
                                winner=winner, won=(p["seat"] == winner))
                out.append(ev)
            if winner:
                self._execute_declaration(winner, kind, node)
        return out

    def _execute_declaration(self, seat: str, kind: str, node: str) -> None:
        """胜者的宣言落地：这里是「祈愿」——付记忆、得神器（引擎出数）。"""
        if kind != "pray":
            return
        s = self.state["seats"][seat]
        if s.get("node") != node:
            self._emit("declare_failed", public=True, seat=seat, node=node,
                       reason=self.phrase("contest.left"))
            return
        try:
            out = self.board_act(seat=seat, action="pray", target=None, attr=None, dc=None, note=None)
            self._emit("declare_won", public=True, seat=seat, node=node,
                       artifact=out.get("artifact"))
        except Exception as exc:  # noqa: BLE001
            self._emit("declare_failed", public=True, seat=seat, node=node, reason=str(exc))


def _unused() -> None:  # 保住 RuleError 的 import（子类在某些分支里会用到）
    raise RuleError("unused")
