"""kicks（自 __init__.py 拆出；2026-10-21 拆景：只剩「輪開始」與「終局」兩腳）。"""

from __future__ import annotations
import json
from typing import Any, Dict, List, Optional

from .keys import round_kick_key, round_pacing_line


def maybe_kick_ending(svc) -> list:
    """結局已判 ⇒ **每個 agent 席位推一次**「這一局結束了」（一次性、冪等）。

    為什麼要有這一支：`tick_loop` 見到 `ended` 就不踢任何席位（含 GM）——那是為了
    「收尾了還在倒數」；代價是 **agent 席位永遠不知道這一局結束了**（只能等下次被私訊叫醒
    才發現）。agent 的記憶是它自己的 ⇒ 這一件事必須明確推給它（owner 2026-10-08）。

    冪等鍵＝`<桌>:終局:<run_id>:<席>`；**重啟不補叫**（`service.__init__` 已把既有結局記成看過）。
    """
    ops = svc.table.adapter_view()
    if not ops.get("ended"):
        return []
    run = str(ops.get("run_id") or "r0")
    out = []
    for sid, s in sorted((ops.get("seats") or {}).items()):
        if str((s or {}).get("kind") or "") != "agent":
            continue
        key = f"{svc.cfg.get('table_id') or 'table'}:終局:{run}:{sid}"
        if key in svc.rio_kick_keys:
            continue
        svc.rio_kick_keys.add(key)
        # `manual=True`：這一腳是「收局後的告知」，暫停閘不該把它吃掉（局已經結束了）。
        res = svc.kick(sid, f"ending:{run}", force=True, reason="終局", manual=True)
        print(f"[wake] 終局通知 → {sid}：{json.dumps(res, ensure_ascii=False)[:160]}", flush=True)
        out.append(res)
    return out


def maybe_kick_round(svc) -> dict:
    """**每一輪開始叫 GM 說一句**（2026-10-21 拆景後的唯一喚醒點）。

    判據：這局還沒結束、玩家已入座、當前輪還沒踢過（冪等鍵帶輪號＋session）。
    失敗不留鍵（下次還能重試）。
    """
    ops = svc.table.adapter_view()
    if ops.get("ended"):
        return {"sent": False, "skipped": "ended"}
    seats = ops.get("seats") or {}
    n_players = len([x for x in seats.values() if str(x.get("kind")) in ("human", "agent")])
    if n_players < int((svc.cfg.get("turn") or {}).get("min_seats_to_start", 2)):
        return {"sent": False, "skipped": "not_all_seated"}
    key = round_kick_key(svc)
    if key in svc.rio_kick_keys:
        return {"sent": False, "skipped": "already_kicked"}
    svc.rio_kick_keys.add(key)
    res = svc.wake_gm("p1", round_pacing_line(svc), "輪開始", 0, turn_key=key)
    if not (res or {}).get("sent"):
        svc.rio_kick_keys.discard(key)
    return res
