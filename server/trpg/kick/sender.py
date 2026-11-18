#!/usr/bin/env python3
"""**L6｜叫醒 GM（送）**——出向 POST 給 GM 的接收器（`send_kick_to_gm`）＋工具行模板與 token 檔位置。

命名：**`kick_sender.py`（送）↔ `kick_receiver.py`（收）**——兩端成對，方向看名字就知道。

從 `wake.py` 切出來（2026-10-06 結構整理）：這一支只管「**怎麼把 GM 叫起來**」
（URL／token 檔／payload／失敗處理），不管「什麼時候該叫」（那是 `wake.KickMixin`
的節拍與冪等鍵）。

憑證紀律：token **不進 repo、不進 prompt**——值放 `.secrets/trpg_kick_engine.token`
（600），或用 `config.kick.token_file` 覆寫。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from trpg.paths import ROOT as HERE  # repo 根（資料／vendor／docs 都在這裡）


GM_KICK_URL = os.environ.get("TRPG_GM_KICK_URL", "http://127.0.0.1:8893/trpg/kick")
GM_KICK_TOKEN_FILE = os.environ.get(
    "TRPG_GM_KICK_TOKEN_FILE",
    os.path.join(os.path.dirname(HERE), ".secrets", "trpg_kick_engine.token"))

# GM 那一行工具的指令模板。預設指到**本 repo 自己的** tools/gm.py（它會自我定位桌端目錄）⇒
# 「引擎與 GM 同一台」免設定就能用（預設值裡的 {here}／{table} 在 send_kick_to_gm 內替換）。
# 跨機（引擎在 A、GM 在 B，例如 peer_trial 的 kick 指到 協作端 那台）用
# config.kick.gm_cli 或 env TRPG_GM_CLI 覆寫成 B 那台自己的指令。
# **同進程（hub／單桌）**：`config.kick.receiver_url = "inproc://"` ⇒ 不發 HTTP，直接把同一份 payload
# 交給**這個進程裡**的接收器（`kick_receiver.install_local()` 裝進來的 `State`）。
# 跨機的接收器（例如 協作端 那台）仍走 HTTP——兩條並存，由 config 決定。
LOCAL_RECEIVER: Any = None

GM_CLI = os.environ.get("TRPG_GM_CLI") or "python3 {here}/tools/gm.py {table}"


def send_kick_to_gm(table_id, seat, text, kind, seq, port=None, home=None,
             turn_key=None, url=None, token_file=None, gm_cli=None, gm_tools=None,
             base=None, gm_token=None, session=None):
    """玩家有事 → 叫醒 GM 席（協作端）。失敗絕不影響桌上。

    prompt 是給被叫醒的 GM 看的：**桌的位址與劇本目錄一定要帶自己那張桌的值**。
    寫死 8975／peer_trial 的版本會讓 rio_test 這類第二桌被指向別張桌子（2026-10-05 實測踩到）。
    ``base``：這張桌**現在真的在哪**（別的行程打得到的 URL）。單桌模式＝`http://127.0.0.1:<port>`；
    **單一進程（hub）模式**＝`http://127.0.0.1:<hub埠>/<前綴>`（桌自己的埠沒在聽）——2026-10-07 加。
    省略＝用 ``port`` 拼（舊行為）。

    ``turn_key``：冪等鍵（協作端 側據此去重）。省略＝舊制 ``{table_id}:{kind}:{seq}``；
    回合制（2026-10-21 改版後唯一的節拍）＝引擎每輪叫一次 GM（鍵帶輪號＋session）。
    ``gm_cli``：喚醒訊息裡那行工具指令的模板（含 ``{here}``／``{table}``）；省略＝``GM_CLI``。
    """
    def _blurb(b: str, seat):
        """桌況快照＋亮燈。桌端自己亮燈、自己把該看的塞進訊息——省掉 GM 的每一趟探索（2026-10-06）。

        「一趟 API 只要 2–7 秒，一次喚醒卻跑了 27 個工具呼叫」是實測過的帳（gm-rio_test，00:20）：
        它踩到一個真 bug 之後就開始讀 server.py、grep、curl 各端點，花了 2 分 49 秒。
        所以這裡把「它會自己去查的每一格」一次給足：輪號／席位／允許的動作／誰這一輪還沒結束／額度／最近三則。
        """
        # ⚠ 2026-10-07：桌端 `/api/*` 退役 ⇒ 這兩發改走 **GraphQL**（`tools/gql_client.py`，共用的那支）。
        import os as _os
        import sys as _sys
        _sys.path.insert(0, _os.path.join(HERE, "tools"))
        import gql_client as _gq                              # noqa: PLC0415
        try:
            _sess = _gq.session_of(b, table_id, gm=gm_token)
            _gq.mutate(b, "typing", {"seat": "gm", "to": seat},
                       table=table_id, session=_sess, gm=gm_token)
        except Exception:                                     # 亮燈失敗不影響喚醒
            pass
        try:
            _sess = locals().get("_sess") or _gq.session_of(b, table_id, gm=gm_token)
            v = _gq.view(b, table_id, _sess, seat="gm", gm=gm_token)
            ya = v.get("you_are") or {}
            seats = "、".join(f"{s.get('seat')}={s.get('name')}（{s.get('kind')}）"
                              for s in (v.get("seats") or []))
            acts = "、".join(v.get("allowed_actions") or [])
            lines = [
                f"你是：{ya.get('duty') or '這張桌的 GM（敘述世界、演 NPC、不下場當玩家）'}",
                f"這一局 session：{v.get('session') or (session or '')}（**每一次叫桌工具都要帶**）",
                f"席位：{seats}",
                f"這張桌允許的動作（act 只能送這幾個）：{acts}",
                # ★ 2026-10-21 改版：節拍只剩「輪」（每人各出手一次＝一輪；結算在輪末）
                f"輪：第 {int(v.get('round_no') or 0) + 1} 輪（回合制；每輪開始你會被告知一次）",
                f"這一輪還沒結束的人：{v.get('round_waiting') or '無'}",
                f"額度：{v.get('throttle') if v.get('throttle') is not None else '（無）'}",
                "最近三則：",
            ]
            for r in (v.get("log") or [])[-3:]:
                lines.append(f"・{r.get('seat') or 'GM'}：{str(r.get('text') or r.get('note') or '')[:70]}")
            return "\n".join(lines)
        except Exception:
            return "（桌況這次查不到——就照訊息本文回應，不要為了查它去讀檔或看 server.py）"

    try:
        import urllib.request as _ur
        _base = (str(base).rstrip("/") if base
                 else f"http://127.0.0.1:{int(port or 8975)}")   # 這張桌現在真的在哪（hub 模式＝hub 埠＋前綴）
        # 給 GM 的工具行：兩種介面
        #   cli ＝ 跑本 repo 的 tools/gm.py（要 terminal；token 由它自己從 config 讀）
        #   mcp ＝ 用桌的 MCP 工具（**GM 的瘦 profile 走這條**：沒有 terminal，token 由連線標頭帶）
        _mode = str(gm_tools or "cli").lower()
        if _mode == "mcp":
            _how = (
                "── 怎麼做（用桌的 MCP 工具；seat 一律填 \"gm\"，**token 不用填**——連線標頭已帶）──\n"
                "  table_say    {seat:\"gm\", text:\"敘事文字\"}                ← 世界的聲音（顯示為 GM）\n"
                "  table_gm     {op:\"npc_say\", seat:\"npc1\", text:\"台詞\"}   ← 只有那個 NPC 真的開口\n"
                "  table_secret {to:\"p1\", text:\"文字\"}                      ← 只給他一個人看\n"
                "  table_gm     {op:\"act_for\", seat:\"p1\", action:\"move\", note:\"說明\"}"
                "  ← 玩家宣告的實質動作（走引擎、扣他的額度）\n"
                "  ⓘ 2026-10-21 改版：不需要開場或收場。引擎每輪叫你一次，\n"
                "    你只要用 table_say 說一句公開敘述；敘事不要掛在某個 NPC 名下。\n"
                "  table_gm     {op:\"audio_play\", ch:\"bgm\", key:\"bgm:…\"}  ← 換 BGM（sfx 同理；"
                "{op:\"audio_list\"} 看有哪些）\n"
                "  ⚠ **每一次呼叫都要帶 `session`**——上面「桌況」那一行有給（＝這一局的 id）。"
                "桌端靠它認出你這一局；多張桌共用一個 MCP 前門時，沒有它分不出你在哪一張桌。\n"
            )
        else:
            _cli = (gm_cli or GM_CLI).format(here=HERE, table=table_id)
            _how = (
                "── 怎麼做（用這支工具，它自己帶 token；第一個參數是桌 id）──\n"
                f"  {_cli} say \"敘事文字\"        ← 世界的聲音（顯示為 GM）\n"
                f"  {_cli} npc npc1 \"台詞\"        ← 只有那個 NPC 真的開口\n"
                f"  {_cli} secret {seat} \"文字\"   ← 只給他一個人看\n"
                f"  {_cli} bgm bgm:…             ← 換 BGM（sfx 同理；sounds 看有哪些）\n"
            )
        prompt = (
            f"【TRPG 桌 {table_id}｜{kind}｜觸發席位 {seat}】\n{text}\n\n"
            "── 桌況（桌端已查好，直接採用；不要再去查桌、讀檔或看 server.py）──\n"
            + _blurb(_base, seat) + "\n\n"
            + _how
            + (
                f"  {_cli} act {seat} move \"說明\"  ← 玩家宣告的實質動作（走引擎、扣他的額度）\n"
                "  ⓘ 2026-10-21 改版：不需要開場或收場。引擎每輪叫你一次，\n"
                "    你只要用 say 說一句公開敘述。\n"
                "規矩：敘事一律走 say（不要掛在某個 NPC 名下）；玩家宣告的動作走 act"
                "（只用對話敘事＝憑空核發，額度不動、事件流也沒那筆）；\n"
                if _mode != "mcp" else
                "規矩：敘事一律走 table_say（不要掛在某個 NPC 名下）；玩家宣告的動作走 act_for"
                "（走引擎、扣額度；只 npc_say＝憑空核發）；\n"
            )
            + "有人這一輪的額度用盡就照實說「這一輪你已經動完了」；短、像 GM 講話、推進劇情、不替玩家決定行動；"
            "日文台詞前端會自動翻譯。\n"
            "── 不要探索 ──\n"
            "上面就是全部：工具用法、桌況、席位、允許的動作都在這則訊息裡。\n"
            "不要先跑 --help 試工具、不要 ls／cat／find／讀 server.py、不要探測席位。\n"
            "直接照上面的格式把該送的東西送出去（通常一到兩條指令），然後回一句「待命」。"
        )
        # ★ session 規範（設計者 2026-10-07）：`session`＝**這一副本的 id**（一副本一 session），
        #   `session_name`＝收端據以開的會話名（帶副本 id ⇒ 一副本一條 GM 會話）。
        #   沒帶 session（舊呼叫端）⇒ 沿用舊制名，且 `session` 也塞舊名（舊接收器讀得到、行為不變）。
        _sess = str(session or "").strip()
        _legacy_name = "trpg-" + str(table_id) + "-gm"
        _sname = (f"trpg-{table_id}-{_sess}-gm" if _sess else _legacy_name)
        body = json.dumps({"kind": "trpg_turn", "table_id": table_id, "seat": seat,
                           "turn_seq": turn_key or f"{table_id}:{kind}:{seq}",
                           # session 名必須＝GM 接收器的會話名（下劃線原樣保留）：
                           # 曾寫成 "trpg-" + id.replace("_","-")（少了 -gm、把 _ 換成 -）⇒
                           # kick_receiver 用 body 的 session 優先，撈歷史打 /api/sessions/trpg-hyakki-tower/messages
                           # 得 404 → 靜默降級成「每輪不帶對局歷史」（2026-10-06 實測：06:52 帶 32 則/11662 字
                           # → 08:10 帶 0 則）。改動前先確認接收器模板 --session 的值。
                           "session": _sess,
                           "session_name": _sname,
                           "prompt": prompt}).encode()
        _k = turn_key or f"{table_id}:{kind}:{seq}"
        if str(url or GM_KICK_URL).startswith("inproc://"):
            # **同進程**（hub／單桌；config.kick.receiver_url = "inproc://"）：不發 HTTP、不看 token
            # （接收器就在同一個進程裡），把**同一份 payload**直接交給它 ⇒ session 名／冪等鍵／
            # 「失敗不影響桌上」全都與 HTTP 那條一字不差。
            if LOCAL_RECEIVER is None:
                print(f"[kick] inproc:// 但沒裝本機接收器（kind={kind} seat={seat} key={_k}）", flush=True)
                return {"sent": False, "error": "inproc:// 但這個進程沒有裝本機接收器"
                                                "（kick_receiver.install_local）", "turn_seq": _k, "url": False}
            try:
                out = LOCAL_RECEIVER.handle_kick(json.loads(body.decode("utf-8")))
                print(f"[kick] 送出（同進程） kind={kind} seat={seat} key={_k} → "
                      f"{json.dumps(out, ensure_ascii=False)[:200]}", flush=True)
                return {"sent": True, "status": 200, "body": json.dumps(out, ensure_ascii=False),
                        "turn_seq": _k, "url": "inproc"}
            except Exception as exc:  # noqa: BLE001
                print(f"[kick] 同進程處理失敗 kind={kind} seat={seat} key={_k} → "
                      f"{type(exc).__name__}: {exc}", flush=True)
                return {"sent": False, "error": f"{type(exc).__name__}: {exc}",
                        "turn_seq": _k, "url": "inproc"}
        # HTTP 那條**才需要 token**：檔案不在就照舊失敗（不靜默——`test_semic` 有一條契約就驗這個）。
        # （名字原本寫成不存在的 `KICK_TOKEN_FILE` ⇒ 這裡其實一直是 NameError；2026-10-07 正名）
        with open(token_file or GM_KICK_TOKEN_FILE) as fh:
            tok = fh.read().strip()
        req = _ur.Request(url or GM_KICK_URL, data=body,       # 舊碼寫 `KICK_URL`（不存在）＝潛在 NameError；
                          headers={"Content-Type": "application/json",
                                   "X-Table-Kick-Token": tok}, method="POST")
        # 成敗都留一行（2026-10-05 協作端 來信 §1：全靜默時只能從「玩家抱怨」發現；
        # 有這行，`duplicate`／token 不符／連不上都會出現在 logs/table.stdout.log）。
        _k = turn_key or f"{table_id}:{kind}:{seq}"
        try:
            with _ur.urlopen(req, timeout=4) as resp:
                code, echoed = resp.status, resp.read(400).decode("utf-8", "replace")
            print(f"[kick] 送出 kind={kind} seat={seat} key={_k} → {code} "
                  f"{echoed.strip()}", flush=True)
            return {"sent": True, "status": code, "body": echoed.strip(),
                    "turn_seq": _k, "url": True}
        except Exception as exc:  # noqa: BLE001
            print(f"[kick] 送出失敗 kind={kind} seat={seat} key={_k} → "
                  f"{type(exc).__name__}: {exc}", flush=True)
            return {"sent": False, "status": getattr(exc, "code", None),
                    "error": f"{type(exc).__name__}: {exc}", "turn_seq": _k, "url": True}
    except Exception as e:
        print(f"[kick] 失敗（不影響桌上）: {type(e).__name__}: {e}", flush=True)
        return {"sent": False, "error": f"{type(e).__name__}: {e}", "url": False,
                "turn_seq": turn_key or f"{table_id}:{kind}:{seq}"}
