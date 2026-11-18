"""act（自 SemiCoopMixin 拆出）。"""

from __future__ import annotations
import json
import os
import random
from typing import Any, Dict, List, Optional
from trpg.core.errors import RuleError  # 与 engine.RuleError 同一个类（本模块不 import engine，避免循环依赖）


class SemiCoopActMixin:
    def semic_act(self, *, seat: str, action: str, target: Optional[str] = None,
                  attr: Optional[str] = None, dc: Optional[int] = None,
                  note: Optional[str] = None) -> Dict[str, Any]:
        st = self.state["semi"]
        s = self.state["seats"][seat]

        if action == "declare":
            # target = 争夺目标：目前支持 "pray@<node>"
            if not target or "@" not in target:
                raise ValueError('declare 的 target 形如 "pray@<節點 id>"')
            kind, node = target.split("@", 1)
            if kind not in ("pray",):
                raise ValueError(f"不支持争夺的目标类型：{kind}")
            if s.get("node") != node:
                raise ValueError(f"你不在 {node}（现在在 {s.get('node')}）——宣言要在现场")
            if any(p["seat"] == seat and p["node"] == node for p in st["pending"]):
                raise ValueError("你已经对同一个目标宣言过了")
            ev = self._emit("declare", public=True, seat=seat, kind=kind, node=node)
            return {"declared": {"kind": kind, "node": node, "seq": ev["seq"]},
                    "note": "輪末揭晓：并列者各骰一次比大小（先手归胜者）"}

        if action == "show":
            fid = target or note
            if fid not in self.clues:
                raise ValueError(f"没有这枚碎片：{fid}")
            if st["frag_holder"].get(fid) != seat:
                raise ValueError("这枚碎片不在你手上")
            to = (note or "").strip() if note and note in self.state["seats"] else None
            if not to:
                raise ValueError("show 要说给谁看：note 填席位 id")
            self._emit("fragment_seen", public=True, seat=seat, to=to, fid=fid,
                       name=self._frag_title(fid))
            self._check_wish(seat)
            return {"shown": {"fid": fid, "to": to, "title": self._frag_title(fid)}}

        if action == "give":
            fid = target or ""
            to = (note or "").strip()
            if st["frag_holder"].get(fid) != seat:
                raise ValueError("这枚碎片不在你手上（神器赠送列在期 2）")
            if to not in self.state["seats"]:
                raise ValueError(f"没有这个席位：{to}")
            self._emit("fragment_given", public=True, seat=seat, to=to, fid=fid,
                       name=self._frag_title(fid))
            return {"given": {"fid": fid, "to": to}}

        if action == "wish":
            gid = self.goal_by_seat.get(seat)
            if not gid:
                raise ValueError("你没有私密愿望（NPC/GM 席位）")
            if st["goals"].get(seat, {}).get("achieved"):
                raise ValueError("你的愿望已经实现了")
            ok, detail = self._check_goal(seat, gid)
            if not ok:
                return {"wish": {"ok": False, "why": detail}}
            # 私密願望的 title **不進公開事件**（本人由 your_goal 私有投影看得到）——
            # 2026-10-05 PR #10 複核：原本帶 title 且 public:true ⇒ 全桌/未授權讀者可從 /api/state 拿到。
            self._emit("wish_done", public=True, seat=seat, id=gid)
            return {"wish": {"ok": True, "id": gid, "title": self.goals[gid]["title"],
                             "detail": detail}}

        if action == "pass":
            # 「這一輪我不動」＝明確結束自己的回合（輪末引擎自己結算）
            self.semic_mark_acted(seat, note="pass")
            self.semic_mark_ended(seat)
            return {"pass": True, "note": note or "这一轮什么都不做"}

        raise ValueError(f"未处理的 semi 动作：{action}")


    def _frag_title(self, fid: str):
        """碎片的**顯示名**：有 jp/cn 就回帶語言的字典，交給前端按語言挑；
        沒有就退回正文開頭。**絕不吐內部 id**（f_xxx 是後台鍵，玩家看到會出戲）。"""
        row = self.clues.get(fid) or {}
        loc = {k: row[k] for k in ("jp", "cn", "tw") if row.get(k)}
        return loc or (row.get("text") or fid)


    def _frags_seen_by(self, seat: str) -> Dict[str, List[str]]:
        return (self.state["semi"]["sources"].get(seat) or {})


    def _check_goal(self, seat: str, gid: str) -> tuple:
        """願望達成判定。**L1 有 AST 就用 AST**（`module.json` 的 `requirements[gid]`），
        沒有才退回 `goals.json` 那組固定判據（向後相容：沒帶 module.json 的劇本照舊跑）。

        AST 的失敗說明（`why`）直接當 `view.your_goal.progress` 餵給玩家看「還差什麼」。
        """
        mod = getattr(self, "module", None)
        if mod is not None:
            res = mod.goal_ok(self, seat, gid)
            if res is not None:
                return res
        g = self.goals[gid]
        req = g.get("required") or {}
        st = self.state["semi"]
        s = self.state["seats"][seat]
        detail: Dict[str, Any] = {}
        # 1) 需要某结论的碎片：自己持有，或有人向你出示过
        want_claims = set(req.get("claims") or [])
        if want_claims:
            mine = [fid for fid, h in st["frag_holder"].items() if h == seat]
            seen = list(self._frags_seen_by(seat).keys())
            got = set()
            for fid in set(mine) | set(seen):
                got |= set(self.clues[fid].get("supports") or [])
            if not want_claims & got:
                return False, {"need_claims": sorted(want_claims), "you_have_claims": sorted(got)}
            detail["claims"] = sorted(want_claims & got)
        # 2) 至少 N 个不同来源向你出示过碎片
        minsrc = int(req.get("min_fragment_sources") or 0)
        if minsrc:
            srcs = set()
            for fid, froms in self._frags_seen_by(seat).items():
                srcs |= {x for x in froms if x != seat}
            if len(srcs) < minsrc:
                return False, {"need_sources": minsrc, "have": sorted(srcs)}
            detail["sources"] = sorted(srcs)
        # 3) 必须在某些节点
        nodes = req.get("at_nodes")
        if nodes and s.get("node") not in nodes:
            return False, {"need_node": nodes, "you_are": s.get("node")}
        # 4) 在指定节点第一个祈願
        fpn = req.get("first_pray_at")
        if fpn and st["pray_first"].get(fpn) != seat:
            return False, {"need_first_pray_at": fpn, "actual": st["pray_first"].get(fpn)}
        # 5) 向某人出示 ≥N 枚
        if req.get("show_to"):
            to = req["show_to"]
            n = int(req.get("min_shown_fragments") or 1)
            cnt = len(self._frags_seen_by(to))
            # 要求是「你向 to 出示」的：sources 记的是 to 收到哪些，来源含 seat
            cnt = len([fid for fid, froms in self._frags_seen_by(to).items() if seat in froms])
            if cnt < n:
                return False, {"need_shown_to": to, "need": n, "have": cnt}
            detail["shown_to"] = {to: cnt}
        return True, detail


    def _check_wish(self, seat: str) -> None:
        gid = self.goal_by_seat.get(seat)
        if not gid:
            return
        st = self.state["semi"]
        if st["goals"].get(seat, {}).get("achieved"):
            return
        ok, _ = self._check_goal(seat, gid)
        if ok:
            # 同上：公開事件不帶私密願望 title（2026-10-05）。
            self._emit("wish_done", public=True, seat=seat, id=gid)


    def _drop_policy(self) -> Dict[str, Any]:
        """這一桌的投放策略（`clues.json` 的 `drop_policy`；沒寫＝空 dict＝舊行為）。"""
        return dict(getattr(self, "drop_policy", None) or {})


    def _pick_fragment_by_debt(self, cand: List[str]) -> str:
        """**按「欠帳」挑碎片**：哪一條結論出土得少，就先給支持它的那一枚。

        為什麼：以前是「在池子裡隨機抽一枚」。實測（2026-10-20，原型桌 那局 353 手）
        碎片只出土 8 枚、被看到 9 次——因為隨機永遠先去餵已經有很多枚的結論，
        而某幾條結論（玩家願望的必要條件）可能整局都沒出土過。

        欠帳的算法：這一枚支持的結論，**已經出土的碎片數**取最小；
        同分時「權重高的先出」（`weight` 大的通常是主線證據），最後才用 seed 隨機打散。
        """
        st = self.state["semi"]
        held = st.get("frag_holder") or {}
        out_count: Dict[str, int] = {}
        for _fid in held:
            for _claim in (self.clues.get(_fid) or {}).get("supports") or []:
                out_count[_claim] = out_count.get(_claim, 0) + 1

        def key(fid: str) -> tuple:
            row = self.clues.get(fid) or {}
            claims = list(row.get("supports") or [])
            debt = min((out_count.get(c, 0) for c in claims), default=0)
            return (debt, -int(row.get("weight") or 1), fid)

        rng = random.Random(f"{self.cfg['seed']}:frag:{len(self.events)}")
        best = sorted(cand, key=key)
        top = [f for f in best if key(f)[:2] == key(best[0])[:2]]
        return top[rng.randrange(len(top))]


    def grant_pray_drop(self, requester: str, *, pool: Optional[str] = "tower") -> List[Dict[str, Any]]:
        """祈願的「代價回到桌上」：一次祈願按 `drop_policy.count_per_pray` 投幾枚。

        策略全部來自資料（`clues.json`）：
          * `count_per_pray`（預設 1，上限 3）——一次祈願投幾枚
          * `on_pray_to: anyone_but_requester`——不給提出者（預設就是這樣）
          * `prefer_unheld`（預設 true）——優先給手上還沒有碎片的人
          * 碎片本身按欠帳挑（`_pick_fragment_by_debt`）
        """
        policy = self._drop_policy()
        try:
            n = int(policy.get("count_per_pray") or 1)
        except (TypeError, ValueError):
            n = 1
        n = max(1, min(3, n))
        out: List[Dict[str, Any]] = []
        for _ in range(n):
            got = self.grant_fragment(exclude=requester, pool=pool, reason=requester)
            if got:
                out.append(got)
        return out


    def grant_fragment(self, *, fid: Optional[str] = None, to: Optional[str] = None,
                       exclude: Optional[str] = None, pool: Optional[str] = None,
                       reason: str = "pray") -> Dict[str, Any]:
        st = self.state["semi"]
        held = set(st["frag_holder"])
        # ── 指名投放的驗證（Opus 2026-10-06 第二輪的錯 6）─────────────────────
        # 這裡以前是空的，三個後果都不會當場叫：
        #   ① `fid` 不在 clues ⇒ 事件已落地才在 `self.clues[fid]` 炸 KeyError
        #      ⇒ 留一筆**幽靈 fragment_granted**（跟「幽靈 action_declared」同一型：
        #      玩家手上有東西、`frag_holder` 卻沒有）
        #   ② 已經出土的碎片能再投一次 ⇒ 同一枚碎片兩個持有人（線索的私有性就破了）
        #   ③ `to` 不是席位也照發 ⇒ 持有人是個不存在的人
        # 驗證全部放在 `_emit` **之前**——拒絕的一手不留痕。
        if fid is not None and fid not in self.clues:
            raise RuleError(f"没有这枚碎片：{fid}"
                            "（fragment_drop 的 fid 要這張桌 clues 裡的 id）")
        if fid is not None and fid in held:
            raise RuleError(f"這枚碎片已經出土了（在 {st['frag_holder'].get(fid)} 手上），"
                            f"同一枚不能投第二次：{fid}")
        if to is not None and to not in self.state["seats"]:
            raise RuleError(f"没有这个席位：{to}")
        if fid is None:
            pool = pool or (self.clues and "tower")
            cand = [f for f in self.clues
                    if f not in held and (self.clues[f].get("pool") == pool or pool is None)]
            if not cand:
                cand = [f for f in self.clues if f not in held]
            if not cand:
                return {}
            fid = self._pick_fragment_by_debt(cand)
        if to is None:
            cands = [s for s, spec in self.seats_spec.items()
                     if spec.get("kind") in ("human", "agent") and s != exclude]
            cands = [s for s in cands if s in self.state["seats"]] or [s for s in self.state["seats"] if s != exclude]
            if not cands:
                return {}
            # `drop_policy.prefer_unheld`（預設 true）：優先給「手上還沒有碎片」的人——
            # 不然同一個人會一直抽到，資訊永遠不流動（實測：一局 353 手，碎片只被看到 9 次）。
            if self._drop_policy().get("prefer_unheld", True):
                st_holder = st["frag_holder"]
                counts = {s: 0 for s in cands}
                for _fid, _h in st_holder.items():
                    if _h in counts:
                        counts[_h] += 1
                least = min(counts.values())
                cands = [s for s in cands if counts[s] == least] or cands
            rng = random.Random(f"{self.cfg['seed']}:fragto:{len(self.events)}")
            to = cands[rng.randrange(len(cands))]
        ev = self._emit("fragment_granted", public=True, fid=fid, holder=to, reason=reason,
                        title="（一枚碎片）")
        out = {"fid": fid, "to": to, "seq": ev["seq"], "text": self.clues[fid]["text"]}
        # 節點對不對：碎片綁在哪個節點，跟「他現在在哪」不是同一件事。
        # 這條**只回警告、不拒絕**——有人把東西帶過去、寄過去都是合理的世界動作，
        # 而「這枚碎片不該在這裡出現」是敘事判斷，不是狀態一致性問題（改判權在 GM）。
        row = self.clues.get(fid) or {}
        seat = self.state["seats"].get(to) or {}
        if row.get("node") and seat.get("node") and seat["node"] != row["node"]:
            out["warn_node"] = (f"這枚碎片綁在 {row['node']}，而 {to} 現在在 {seat['node']}"
                                "——先確認這條線索出現得合理（不然玩家會拿到遠處的東西）")
        return out

