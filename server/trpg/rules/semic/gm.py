"""gm（自 SemiCoopMixin 拆出）。"""

from __future__ import annotations
import json
import os
import random
from typing import Any, Dict, List, Optional
from trpg.core.errors import RuleError  # 与 engine.RuleError 同一个类（本模块不 import engine，避免循环依赖）


class SemiCoopGmMixin:
    def gm_op(self, *, op: str, **kw) -> Dict[str, Any]:
        """GM 的**全部**权限都在这里：只能叙述/投放/开收景/结算/改判；没有任何写数值的工具。"""
        st = self.state["semi"]
        if op == "roll_for":
            # GM 代某个席位投骰（NPC 的暗骰、或代玩家保密检定）。
            # 数值仍由引擎产出；GM 只拿到结果去叙述，改不了它。
            return self.gm_roll_for(seat=kw["seat"], attr=kw.get("attr", "wil"),
                                    dc=kw.get("dc"), secret=bool(kw.get("secret", True)),
                                    reason=kw.get("reason") or kw.get("text"))
        if op == "act_for":
            # GM 把某席位的動作送進引擎——**玩家宣告的實質動作也走這條**（不是只給 NPC）。
            # 走完整閘門：rules.actions → semic_gate（額度）→ action_declared → board/semic_act
            # → semic_count（成功才計數）。只用 npc_say 敘事＝憑空核發（額度不動、事件流沒那筆）。
            # 協作端 2026-10-05 §2115：GM.md 已把玩家宣告導到 act_for；kick prompt 也寫進去了。
            # ⚠ `gm_override=True`：NPC 不在回合順序（`order`）裡，普通回合闸會讓 GM 永遠
            #   無法代 NPC 出手。GM 代打仍走額度／規則／事件閘，只跳過「必須是當前回合那一席」。
            return self.act(seat=kw["seat"], action=kw["action"], target=kw.get("target"),
                            attr=kw.get("attr"), dc=kw.get("dc"), note=kw.get("note") or kw.get("text"),
                            gm_override=True, req=kw.get("req"))
        if op == "start_turn":
            # 2026-10-21 拆景：GM 顯式開一輪（沒有「開景」了；引擎人齊也會自己開）
            return self.start_turn(reason="gm")
        if op == "audio_play":
            # 播放控制（2026-10-20）：引擎只認「哪個聲道、哪一首、淡入幾秒」——
            # 曲目本身住模組的 `audio.json`，`key` 打錯會當場列出可用的。
            return self.audio_play(ch=str(kw.get("ch") or "bgm"), key=str(kw.get("key") or ""),
                                   gain=(float(kw["gain"]) if kw.get("gain") is not None else None),
                                   loop=(bool(kw["loop"]) if kw.get("loop") is not None else None),
                                   fade=(float(kw["fade"]) if kw.get("fade") is not None else None),
                                   by=kw.get("seat") or "gm")
        if op == "audio_stop":
            return self.audio_stop(ch=str(kw.get("ch") or "bgm"),
                                   fade=(float(kw["fade"]) if kw.get("fade") is not None else None),
                                   by=kw.get("seat") or "gm")
        if op == "audio_list":
            # GM 要換曲前先看有什麼（曲名／聲道／音量；不含 URL）
            return self.audio_catalog()
        if op == "reveal_artifact":
            return self.reveal_artifact(seat=kw.get("seat") or "", name=kw.get("name"))
        if op == "artifact_catalog":
            return {"catalog": self.artifact_catalog()}
        if op == "fragment_drop":
            res = self.grant_fragment(fid=kw.get("fid"), to=kw.get("to"),
                                      pool=kw.get("pool"), reason=kw.get("reason") or "gm")
            if kw.get("to") and res:
                # 兩條提醒合成一個 `warn`：① 節點對不上（錯 6 的新驗證）② 沒扣額度（原本就有）
                res = dict(res)
                notes = [res.pop("warn_node", None),
                         f"沒扣 {kw['to']} 的額度；若這是他自己的一手，請先用 act_for 送那一手"]
                res["warn"] = "；".join(n for n in notes if n)
            return res
        if op == "contest_override":
            # 改判：指定某个并列宣言的胜者
            seq = int(kw["declare_seq"])
            winner = kw["seat"]
            for p in list(st["pending"]):
                if p["seq"] == seq:
                    p["force_winner"] = winner
            return {"override": {"declare_seq": seq, "winner": winner}}
        if op == "settle":
            return self.settle(note=kw.get("note"))
        if op == "npc_knowledge":
            return self.npc_knowledge(kw["seat"])
        if op == "npc_say":
            return self.gm_say(seat=kw.get("gm_seat", "gm"), text=kw["text"],
                               as_seat=kw["seat"])
        if op == "leak_report":
            return {"leaks": st.get("leaks", [])}
        raise ValueError(f"未知 GM 操作：{op}")


    def gm_roll_for(self, *, seat: str, attr: str = "wil", dc: Optional[int] = None,
                    secret: bool = True, reason: Optional[str] = None) -> Dict[str, Any]:
        s = self.state["seats"].get(seat)
        if not s:
            raise ValueError(f"没有这个席位：{seat}")
        if attr not in self.rules["attributes"]:
            raise ValueError(f"属性 {attr} 不存在：{self.rules['attributes']}")
        r = self._roll(seat=seat, kind="gm_roll", reason=reason or f"GM 代投（{seat}）",
                       mod=self._mod(self.attrs_of(seat), attr), dc=dc, public=not secret)
        if secret:
            # 暗骰：结果不公开，但**这件事本身可见**（日志只记"投了一次暗骰"，不记点数）
            self._emit("gm_roll_marker", public=True, seat=seat, dc=dc, reason=r["reason"])
        return {"roll": r, "secret": secret,
                "note": ("暗骰：点数只有你（GM）知道，用你看到的这个结果去叙述；"
                         "不要把它写进公开日志。" if secret else "公开骰。")}


    def gm_say(self, *, seat: str, text: str, as_seat: Optional[str] = None) -> Dict[str, Any]:
        """GM/NPC 的**唯一**对外发言入口：先做泄漏比对，命中即拦下（不落日志、不发给玩家）。

        `as_seat` 用于「NPC 台词」：NPC 是 GM 的下游 agent，只能被动收到 GM 喂的视角包，
        没有任何桌子工具；它的台词由 GM 用本入口代发 —— 因此**GM 叙述与 NPC 台词共用同一个泄漏闸**。
        """
        speaker = as_seat or seat
        hit = self.leak_scan(text, speaker=speaker)
        if hit:
            self._emit("gm_leak_blocked", public=True, seat=speaker, hit=hit["what"],
                       snippet=hit["snippet"])
            raise ValueError(
                f"这句话会泄露别席位的私密内容（命中：{hit['what']}）——请换一种说法，"
                f"只描述现象，不要说出原文。")
        if as_seat:
            # NPC 台词：记成该 NPC 的发言（日志统一渲染），并标注由 GM 代发
            self._emit("say", public=True, seat=speaker, text=text, via=seat, npc=True)
        else:
            self._emit("gm_say", public=True, seat=seat, text=text)
        return {"said": text, "speaker": speaker}


    def leak_scan(self, text: str, *, speaker: str, ng: int = 8) -> Optional[Dict[str, str]]:
        """机械判据：与「尚未公开的私密文本」做字符 n-gram 重叠检查。"""
        st = self.state["semi"]
        public = set(st.get("public") or [])
        priv: List[tuple] = []   # (kind, key, 文本)
        for seat, gid in self.goal_by_seat.items():
            if gid in public or st["goals"].get(seat, {}).get("achieved"):
                continue
            g = self.goals[gid]
            priv.append(("别的席位的私密愿望", f"goal:{gid}:text", g.get("text", "")))
            priv.append(("别的席位的私密愿望", f"goal:{gid}:title", g.get("title", "")))
        for fid, holder in st["frag_holder"].items():
            seen = st["frag_seen_by"].get(fid) or []
            if fid in public or len(seen) >= 2:
                continue
            priv.append(("别人手上的碎片正文", f"frag:{fid}", self.clues[fid].get("text", "")))
        t = "".join(ch for ch in text if not ch.isspace())
        for kind, key, raw in priv:
            s = "".join(ch for ch in (raw or "") if not ch.isspace())
            if len(s) < ng:
                continue
            for i in range(0, len(s) - ng + 1):
                gram = s[i:i + ng]
                if gram in t:
                    return {"what": f"{kind}（{key}）", "snippet": gram}
        return None


    def npc_knowledge(self, seat: str) -> Dict[str, Any]:
        """NPC 的**视角包**（GM 喂给下游 agent 的全部输入）：他在哪、他能知道的、他能说的方向。

        NPC 是 GM 的下游 agent，只有被动接收；它没有桌子工具、不能查全局状态。
        这个包就是「确保视角稳定」的载体。
        """
        spec = self.seats_spec.get(seat) or {}
        s = self.state["seats"].get(seat) or {}
        node = s.get("node")
        node_info = None
        if getattr(self, "board_enabled", False) and node in getattr(self, "nodes", {}):
            n = self.nodes[node]
            node_info = {"id": node, "jp": n["jp"], "cn": n.get("cn"), "type": n["type"]}
        # NPC 的專屬知識住 **L1**（`module.json` 的 `entities.<character>.knowledge`）——
        # 引擎不再認識任何 NPC 的名字（`test_layers` E 組機械擋）。
        mod = getattr(self, "module", None)
        ent = mod.entity(spec.get("character") or "") if mod is not None else {}
        allowed = list(ent.get("knowledge") or [])
        return {"seat": seat, "role": spec.get("character"), "at": node_info,
                "policy": spec.get("knowledge_policy", "minimal"),
                "you_may_know": allowed,
                "must_not": ["他の席の私密愿望", "他人が持つ碎片の正文", "GM 才知道的隐藏条件"],
                "note": ent.get("note") or "只说以上范围内的内容；被问到范围外的事，就含糊过去。",
                "fragments_shown_to_you": list(self._frags_seen_by(seat).keys()),
                "stage": None,
                "task": "只回台词/反应（1~3 句）。不做判定、不查状态、不索取全局信息；不确定就含糊过去。",
                "transport": "由 GM 直接喂给你（你没有桌子工具，视角只来自这个包）"}


    def settle(self, *, note: Optional[str] = None) -> Dict[str, Any]:
        st = self.state["semi"]
        if st.get("settled"):
            raise RuleError("已经结算过了")
        rows = []
        for seat, spec in self.seats_spec.items():
            if spec.get("kind") == "gm":
                continue
            s = self.state["seats"].get(seat) or {}
            gid = self.goal_by_seat.get(seat)
            g = self.goals.get(gid) if gid else None
            ok = bool(gid and st["goals"].get(seat, {}).get("achieved"))
            if not gid:
                status = "—（无愿望）"
            elif ok:
                status = "达成"
            else:
                _, detail = self._check_goal(seat, gid) if seat in self.state["seats"] else (False, {})
                status = "未达成"
            rows.append({
                "seat": seat, "character": spec.get("character"), "kind": spec.get("kind"),
                # 不帶私密願望 title：settle 是 public 事件，帶了等於全桌公告每個人的願望（2026-10-05）。
                # 願望有無/達成與否看 status 三檔；title 只有本人從 your_goal 看得到。
                "status": status,
                "memory": s.get("memory"), "hp": s.get("hp"),
                "artifacts": [a.get("name") for a in s.get("artifacts", [])],
                "fragments": [fid for fid, h in st["frag_holder"].items() if h == seat],
                "shown_by": sorted({x for froms in (st["sources"].get(seat) or {}).values()
                                    for x in froms if x != seat}),
            })
        # 表層判據也住 **L1**（`module.json` 的 `surface`）：`hyakki_f4`／`hyakki_f5` 這種
        # 戰役專屬的東西不該寫在引擎裡。沒有 module.json ⇒ 退回舊寫法（向後相容）。
        surface: Dict[str, Any] = {"round_no": int(self.state.get("round_no") or 0) + 1}
        mod = getattr(self, "module", None)
        if mod is not None and mod.surface_expr("tower_cleared") is not None:
            surface["tower_cleared"] = bool(mod.check(mod.surface_expr("tower_cleared"), self))
            surface["reached_top"] = bool(mod.check(mod.surface_expr("reached_top"), self))
        else:
            # 舊劇本（沒有 module.json）的回退也不准寫死戰役節點：改用**棋盤上的性質**——
            # 「有人先手過祈願箱」＝tower_cleared；「有人站在最深的一層」＝reached_top。
            surface["tower_cleared"] = bool(st["pray_first"])
            deepest = self._deepest_nodes() if hasattr(self, "_deepest_nodes") else set()
            surface["reached_top"] = any((self.state["seats"].get(s) or {}).get("node") in deepest
                                         for s in self.state["seats"])
        table = {"surface": surface, "rows": rows, "note": note,
                 "leaks_blocked": len(st.get("leaks") or [])}
        self._emit("settle", public=True, table=table)
        return table

