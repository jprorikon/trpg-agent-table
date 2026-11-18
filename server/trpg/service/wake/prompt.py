"""prompt（自 KickMixin 拆出）。"""

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


class KickPromptMixin:
    def kick_prompt(self, seat: str, reason: str = "turn") -> str:
        v = self.table.view(seat=seat)
        you = v.get("you") or {}
        # 「你上次出手之后，桌上发生了什么」——**推**给席位，否则 agent 只知道自己的事
        last_own = self.table.adapter_view()["last_acted"].get(seat)
        digest = self.table.render_log_tail(seat=seat, since_seq=last_own, limit=18)
        lines = [
            # 抬頭要說實話：回合制下「不是你」的喚醒＝私訊，不能寫成「輪到你」
            # （2026-10-06：三號四號看到「轮到你」就出手，順序就散了）
            ("【TRPG 桌 · 點名】席位 %s（%s）（**有人当场点了你的名字**，所以现在就叫你——"
             "不必等這一輪结束）" % (seat, you.get("name")) if reason == "mention"
             else ("【TRPG 桌 · 私訊】席位 %s（%s）" % (seat, you.get("name"))
                   if self.pacing_mode == "turn" and (v.get("turn") or {})
                   and not ((v.get("turn") or {}).get("seat") == seat
                            or (v.get("turn") or {}).get("is_yours"))
                   else "【TRPG 桌 · 轮到你】席位 %s（%s）" % (seat, you.get("name")))),
            f"第 {int((v.get('round_no') or ((v.get('table') or {}).get('roundNo') or 0)))} 轮"
            f"你的卡：HP {you.get('hp')}/{you.get('max_hp')} 属性 {json.dumps(you.get('attrs'), ensure_ascii=False)}"
            f" 状态 {you.get('status')}",
        ]
        # ★2026-10-08（owner：「這涉及到 agent 自身的記憶，我需要 agent 明確知道這一局結束了」）：
        #   結局已判 ⇒ 抬頭直接把話說白＋結局名稱／類型＋戰果結算（agent 要記得住的那幾句）。
        if v.get("ended"):
            _ei = v.get("ended_info") or {}
            _sm = v.get("summary") or {}
            lines.insert(0,
                         f"⚠⚠ **這一局已經結束了**（結局：{_ei.get('kind') or v.get('ended')}"
                         f"／{_ei.get('name_cn') or _ei.get('name_jp') or ''}）"
                         "——**不要再出手、不要再宣告動作**；請把「這一局結束了」記進你自己的記憶。"
                         "下面是這一局最後的狀態：")
            if _sm:
                lines.append("戰果結算：" + json.dumps(
                    {k: _sm.get(k) for k in ("turns", "rounds", "seconds", "goals_achieved",
                                             "goals_total", "fragments_out")}, ensure_ascii=False))
        bd = (v.get("board") or {}).get("you_are")
        if bd:
            lines.append(f"你在：{bd['jp']}／{bd.get('cn')}（{bd['type']}）"
                         + (f" 记忆 {you.get('memory')}" if you.get("memory") is not None else "")
                         + (self.table.phrase("wake.coins", n=you.get("star_coins"))
                            if you.get("star_coins") else "")
                         + (f" 神器 {[a['name'] for a in you.get('artifacts') or []]}"
                            if you.get("artifacts") else ""))
            if bd.get("flavor"):
                lines.append(f"这里：{bd['flavor']}")
            # 槽位（2026-10-20）：投影給抽象名 `mob_left`；舊模組仍有 `mobs_left` 別名。
            _mob_left = bd.get("mob_left")
            if _mob_left is None:
                _mob_left = bd.get("mobs_left")
            if _mob_left:
                lines.append(self.table.phrase("wake.foes_left", n=_mob_left))
            if bd.get("wish_box"):
                lines.append(self.table.phrase("wake.wish_box", tier=bd["wish_box"]["tier"]))
            if bd.get("shop"):
                lines.append(self.table.phrase("wake.shop"))
            can = (v.get("board") or {}).get("can_go") or []
            if can:
                lines.append("可去：" + "、".join(
                    f"{c['to']}({c['cn'] or c['jp']}/{c['kind']})" for c in can))
        cur = v.get("turn") or {}
        if cur.get("seat") == seat or cur.get("is_yours"):
            lines.append(f"你的回合：第 {cur.get('index')} 手，剩 {cur.get('seconds_left')} 秒。"
                         + ("**出手即交棒**；不想出手就送 action=end_turn 結束自己的回合。"
                            if self.pacing_mode == "turn" else ""))
        elif not cur:
            lines.append("現在**沒有進行中的回合**（等一下就會開；人齊時引擎自己開第一輪）。"
                         "先看局面、想好要怎麼動，等被叫到再出手。")
        elif self.pacing_mode == "turn":
            # 回合制、但現在不是你的回合 ⇒ 這條喚醒只會是**私訊**（回合制下點名推送已關）。
            # 不能叫她在這裡出手：回合制的順序就是靠「只在自己的回合出手」維持的。
            # ⚠️ 設計者 2026-10-06 抓到：以前這裡寫「可以用 table_say／table_tell 回話」⇒ 她把**私訊**
            #    回在公開頻道（桌上其他人也看到）。私訊的回話只有一個出口＝`table_tell`。
            _dm_to = ""
            for _s in (v.get("your_secrets") or []):
                if _s.get("from") and _s.get("from") != seat:
                    _dm_to = str(_s["from"])
                    break
            lines.append(f"这是**回合制**的桌：现在轮到 {cur.get('seat')}，**不是你的回合**。"
                         "这条唤醒是**私讯**——回话**只用私讯工具**（工具行的 `table_tell`）"
                         + (f"，收件人＝`{_dm_to}`" if _dm_to else "，收件人＝发信给你的那一席")
                         + "；**不要用 `table_say` 回私讯**（那是全桌公开的发言，别人都看得到）。"
                         "也**不要宣告动作**（move／attack／check 等）；轮到你的回合时我会再叫你一次，"
                         "那时再出手或 action=end_turn 结束自己的回合。")

        # 半合作模式的附加投影（輪 / 私密愿望 / 碎片 / 节流 / 宣言）
        # ★ 2026-10-21 拆景：`stage`（景）沒有了，只剩輪號；敘事由 GM 每輪自己說一句。
        _rno = int(v.get("round_no") or 0)
        if _rno:
            lines.append(f"現在是第 {_rno} 輪（回合制：每人各出手一次＝一輪；結算在輪末）")
        g = v.get("your_goal")
        if g:
            prog = "条件已满足，可以用 wish 兑现" if g.get("ready") else \
                f"条件未满足（{json.dumps(g.get('progress'), ensure_ascii=False)}）"
            lines.append(f"你的私密愿望（只有你知道，可以暗示/交易，别直接念出来）：{g['title']} — {prog}")
        for x in (v.get("your_fragments") or []):
            lines.append(f"你手上的碎片 {x['id']}：{x['text']}（可用 show 出示给某人，note 填席位 id）")
        for x in (v.get("seen_by_you") or []):
            lines.append(f"别人给你看过的碎片 {x['id']}（来自 {'/'.join(x['from'])}）：{x['text']}")
        dec = v.get("your_declarations") or []
        if dec:
            lines.append("你已宣言（**輪末**揭晓）：" + "、".join(f"{x['kind']}@{x['node']}" for x in dec))
        w8 = v.get("round_waiting") or []
        if v.get("you_acted"):
            lines.append("你這一輪已經動過了。" + (f"**這一輪還沒結束**：{'、'.join(w8)}" if w8 else ""))
        elif cur.get("seat") == seat or cur.get("is_yours"):
            lines.append("這一輪你還沒動：做一次行動，或 action=end_turn／pass 明確收手。")
        th = v.get("throttle")
        if th:
            lines.append(f"本輪额度：act {th['used'].get('act', 0)}/{th['limits']['act']}，"
                         f"say {th['used'].get('say', 0)}/{th['limits']['say']}（用完就結束自己的回合，下一輪再動）")
        sec = v.get("your_secrets") or []
        if sec:
            # 只列最近 N 則（`view.secret_window`，預設 8）——私訊會無上限長出來，
            # 舊版把整個收件匣（現役桌 p2 已 9.9k 字）每次喚醒重送一次。
            # **投影不裁**：完整收件匣仍在（網頁 messenger／`table_view detail="full"`），
            # 這裡只是不把它塞進每一次喚醒。
            shown, hidden = _trim_secrets(sec, self.cfg)
            lines.append("只有你知道：" + " / ".join(
                (f"{s['text']}（来自 {s['from']}）" if s.get("from") else s["text"])
                for s in shown))
            if hidden:
                lines.append(f"（更早的 {hidden} 則私訊沒列在這裡；要看就用 table_view "
                             f"（detail=\"full\"）讀完整收件匣）")
        if digest:
            lines.append(("你上次出手之后，桌上发生了这些（公开信息）：" if last_own
                          else "桌上最近发生的（公开信息）：") + "\n  " + "\n  ".join(digest))
        party = ((v.get("board") or {}).get("party") or [])
        if party:
            lines.append("队友位置：" + "、".join(
                f"{p['name']}（{p['where'] or '位置不明'}）" if not p.get("hidden_floor")
                else f"{p['name']}（塔内·层数不明）" for p in party))
        # 席位ごとの上書き（owner 2026-10-05）：外部 agent の橋は MCP ではなく REST の道具を出す
        # （名前は `table_view` / `table_act` / `table_say`）⇒ p4 だけ prefix を空にする。
        # **設定驅動**（`seats.json` の `tool_prefix`）、既定は今まで通り `wake.tool_prefix`。
        _spec = (getattr(self.table, "seats_spec", {}) or {}).get(seat) or {}
        prefix = _spec.get("tool_prefix")
        if prefix is None:
            prefix = (self.cfg.get("wake", {}) or {}).get("tool_prefix", "mcp_table_")
        lines.append(f"你是席位 {seat}，所有工具调用都要带 seat=\"{seat}\"。")
        lines.append(f"用工具行动：{prefix}table_view 看局面，{prefix}table_act 行动，"
                     f"{prefix}table_say 说话（全桌可见），"
                     f"{prefix}table_tell 私訊（只给对方看）。"
                     f"数值一律由服务端产出，不要自己编骰子结果。")
        return "\n".join(lines)


    def table_base(self) -> str:
        """**這張桌的 API 在哪**（別的行程打得到的 URL）——一律是 hub 根（不帶桌前綴）。

        * `config.hub_base` ⇒ 用它（跨行程最可靠：GM 是**另一個**行程，讀同一份 config）；
          舊值若還帶著這張桌的 `public_prefix`，`layout.api_base()` 會剝掉。
        * hub 進程裡（env `TRPG_HUB_ROOT`）⇒ hub 的埠（`TRPG_HUB_PORT`）。
        * 都不是（單桌模式）⇒ `http://127.0.0.1:<這張桌的埠>`。

        ⚠ 2026-10-19 拆前後台：後台只剩 `POST /graphql`（桌是欄位）⇒ base 不能再帶桌前綴，
          否則被叫醒的 GM 查桌況會 404（「桌況這次查不到」）。
        """
        return layout.api_base(self.cfg or {}, port=(self.cfg or {}).get("port"))

