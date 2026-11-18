"""cli（自 __init__.py 拆出）"""

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

from .common import log_line
from .state import State
from .http import Handler

def _parser() -> argparse.ArgumentParser:
    """CLI 的參數面。`main()` 拿它開埠；`install_local()` 拿它的**預設值**組一台**不開埠**的接收器
    （hub／單桌行程的 `config.kick.receiver_url = "inproc://"` 走這條——不再需要 8972）。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8972)
    ap.add_argument("--bind", default="127.0.0.1",
                    help="只給本機（引擎的 config.kick.receiver_url 指 127.0.0.1）；"
                         "對外要開請明寫 --bind 0.0.0.0")
    ap.add_argument("--pidfile", default="",
                    help="寫下自己的 pid（deploy/kick_receiver.sh 用；沒有就不寫）")
    ap.add_argument("--token-file", default=os.path.join(HERE, "..", ".secrets",
                                                        "trpg_kick_receiver.token"))
    ap.add_argument("--state-file", default=os.path.join(HERE, "logs", "kick_receiver_seen.json"))
    ap.add_argument("--log-file", default=os.path.join(HERE, "logs", "kick_receiver.log"))
    ap.add_argument("--api", default="http://127.0.0.1:18642")
    ap.add_argument("--api-profile", default="",
                    help="次级 profile 名（例 trpg-gm）⇒ 走 /p/<profile>/... 且用该 profile 自己的 "
                         "API_SERVER_KEY（环境变量或 --api-key-file）。空＝default 那个 listener")
    ap.add_argument("--api-key-name", default="",
                    help="要读的键名。空＝有 --api-profile 时用 API_SERVER_KEY，否则 HERMES_API_KEY")
    ap.add_argument("--api-key-file", default="",
                    help="放 Bearer 的檔。空＝有 profile 時讀 profiles/<profile>/.env，否則讀 "
                         "docker/agent-bridge/.env")
    ap.add_argument("--session", default="trpg-{table}-{session}-gm",
                    help="GM session 名模板（body 沒給 `session_name` 時的後備；"
                         "{table}＝table_id、{session}＝副本實例 id ⇒ **一副本一條會話**）")
    # ⚠ 預設**不猜**桌 config（舊版 `campaigns/<桌>/config.json` 在 2026-10-07 搬家後不存在 ⇒
    #   `install_local()` 直接 FileNotFoundError、整個 inproc:// 接收器裝不起來、GM 永遠叫不到）。
    #   要桌 config 就明給（hub 會自己解析第一張桌的 config 傳進來），沒有就只靠 CLI／profile。
    ap.add_argument("--config", default="",
                    help="桌的 config.json（可省略；hub 會自動解析第一張桌的 config 傳進來。"
                         "轉送別的席位時查 wake.seats.<seat>.url）")
    ap.add_argument("--approval-choice", default="session",
                    choices=["session", "once", "always", "deny"],
                    help="無人應答的喚醒要怎麼答 approval 閘（session＝只管這一輪）")
    ap.add_argument("--approval-window-s", type=float, default=900.0)
    ap.add_argument("--approval-poll-s", type=float, default=3.0)
    ap.add_argument("--max-read-bytes", type=int, default=8_000_000,
                    help="GM agent API 回應最多讀幾 byte（撈歷史會好幾十 KB）")
    ap.add_argument("--history-limit", type=int, default=300,
                    help="每輪帶幾則歷史訊息（user/assistant 文字；0＝不帶）")
    ap.add_argument("--history-max-chars", type=int, default=40000,
                    help="歷史的字數上限（超過就只留最近的）")
    ap.add_argument("--pending-max", type=int, default=5,
                    help="同一桌跑著時最多排幾封喚醒（滿了丟最舊的一封，會記日誌）")
    ap.add_argument("--instructions-file", default=os.path.join(HERE, "gm_turn_instructions.txt"),
                    help="每輪給 GM 的臨時 system 指令（不准探索的硬規矩）；檔案不存在＝不帶")
    return ap

def main() -> int:
    args = _parser().parse_args()
    # profile 與 key 檔一律由 State 決定（CLI 優先、其次桌 config 的 kick.gm_api_profile；
    # 有 profile ⇒ 讀那個 profile 自己的 .env 與 API_SERVER_KEY）。

    state = State(args)
    if not state.api_key:
        print(f"{state.api_key_name} 讀不到（--api-key-file／profile 的 .env／環境變數）"
              f"{'（profile=' + state.profile + '）' if state.profile else ''}", flush=True)
        return 2
    Handler.state = state
    if args.pidfile:
        with open(args.pidfile, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
    # 重啟不孤兒：接手還掛著的那一輪（答閘＋跑完撿合併的下一輪），並把沒叫的合併事件叫起來。
    for tid, rec in list(state.active.items()):
        rid = (rec or {}).get("run_id")
        if rid:
            log_line(state.log_file, f"重啟：接手盯 {tid} 的 run {rid}")
            threading.Thread(target=state.watch_run, args=(rid, tid), daemon=True).start()
    for tid, pend in list(state.pending.items()):
        if tid not in state.active:
            # 佇列：只補叫**最早**那封，其餘原樣留在佇列（跑著時會自動接上）
            q = pend if isinstance(pend, list) else [pend]
            if not q:
                state.pending.pop(tid, None)
                continue
            first = q[0]
            rest = q[1:]
            if rest:
                state.pending[tid] = rest
            else:
                state.pending.pop(tid, None)
            state._save()
            log_line(state.log_file, f"重啟：有排隊沒叫的事件（{tid}，佇列 {len(q)}），先叫最早那封"
                                     f"（session={first.get('session') or '模板'}）")
            try:
                state.start_gm(tid, first.get("prompt") or "", first.get("key") or "",
                               session=first.get("session") or "")
            except Exception as exc:  # noqa: BLE001
                log_line(state.log_file, f"重啟補叫失敗（桌上不受影響）: {exc}")
    log_line(state.log_file, f"起動 :{args.port} session={state.session_tpl} "
                             f"api={state.api} seen={len(state.seen)} "
                             f"active={list(state.active)} pending={list(state.pending)}")
    try:
        ThreadingHTTPServer((args.bind, args.port), Handler).serve_forever()
    finally:
        if args.pidfile and os.path.exists(args.pidfile):
            try:
                os.remove(args.pidfile)
            except OSError:
                pass
    return 0


def local_receiver(**kw: Any) -> State:
    """組一台**不開埠**的接收器（用 CLI 的預設值，可用 kw 覆寫任何參數）。

    hub（`hub_server.py`）與單桌行程都用它把「GM 喚醒」收進**同一個進程**：
    桌的 `config.kick.receiver_url = "inproc://"` ⇒ `kick_sender` 直接把 payload 交給它，不發 HTTP。
    """
    args = _parser().parse_args([])
    for k, v in kw.items():
        if v is not None:
            setattr(args, k, v)
    return State(args)

def install_local(**kw: Any) -> State:
    """把本機接收器裝到 `kick_sender.LOCAL_RECEIVER`（`inproc://` 那條路會用它）。"""
    st = local_receiver(**kw)
    from trpg.kick import sender as kick_sender

    kick_sender.LOCAL_RECEIVER = st
    return st
