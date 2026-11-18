"""main（自 __init__.py 拆出）"""

from __future__ import annotations
import argparse
import atexit
import json
import os
import secrets
import socket
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from trpg.paths import ROOT as HERE  # repo 根（資料／vendor／docs 都在這裡）
from trpg.kick import receiver as kick_receiver                                                      # noqa: E402
from trpg.core import layout as layout                                                             # noqa: E402
from trpg.service import table_directory as tdir                                            # noqa: E402
from trpg.service import table_factory as table_factory                                                      # noqa: E402
from trpg.api import web as web_api                                                            # noqa: E402
from trpg.api.web import Handler as WebHandler                                 # noqa: E402

from .hub import Hub
from .handler import Handler

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8971, help="**唯一對外埠**")
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--root", default=HERE, help="引擎倉根目錄（掃 campaigns/*/config.json）")
    ap.add_argument("--pidfile", default=None)
    ap.add_argument("--no-local-kick", action="store_true",
                    help="不要裝本機接收器（預設裝；桌的 config.kick.receiver_url=inproc:// 會用到）")
    ap.add_argument("--reload-interval", type=float, default=0.0,
                    help=">0 ⇒ 背景每 N 秒 rescan 一次（收新桌／移除已刪；預設 0＝不自動）")
    args = ap.parse_args()

    if args.pidfile:
        with open(args.pidfile, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
        atexit.register(lambda p=args.pidfile: os.path.exists(p) and os.remove(p))

    # ★ 2026-10-20：**先裝接收器，再載入桌**。以前是 `Hub.load()`（桌一上線節拍就 tick）
    #   之後才 install_local ⇒ 那一瞬間的 inproc:// 踢全部失敗（實測：景收了、催 GM 開下一景
    #   的那一腳 sent:false，整桌停在「沒景、沒輪」）。這裡先用 discover 找一張桌的 config 裝好。
    _local_state = None
    if not args.no_local_kick:
        _cfg0 = ""
        try:
            for _row in tdir.discover(args.root):
                _cand = os.path.join(str(_row.get("home") or ""), "config.json")
                if os.path.exists(_cand):
                    _cfg0 = _cand
                    break
        except Exception:                                              # noqa: BLE001
            _cfg0 = ""
        try:
            _local_state = kick_receiver.install_local(config=_cfg0)
            _prof = getattr(_local_state, "profile", "") or "（default）"
            print(f"[hub] 本機 GM 接收器已裝（載入桌之前）：profile={_prof}；"
                  f"桌 config={_cfg0 or '（讀不到）'}", flush=True)
        except Exception as exc:                                       # noqa: BLE001
            print(f"[hub] 本機接收器裝不起來（inproc:// 那條會失敗）：{type(exc).__name__}: {exc}",
                  flush=True)

    Hub.load(args.root)
    Hub.ensure_token()
    Hub.ensure_gm_token()                            # `/mcp`（多桌共用 MCP 前門）的 GM 身分
    print(f"[hub] /mcp GM 憑證：{Hub.gm_token_file}（600）", flush=True)
    os.environ["TRPG_HUB_PORT"] = str(args.port)     # 桌要知道「別的行程打得到我」的埠（hub 的埠）
    if not args.no_local_kick:
        try:
            # 桌的 config 由 hub 自己解析（kick_receiver 的預設路徑在搬家後已失效——2026-10-08 實測踩到：
            # 「本機接收器裝不起來 ⇒ inproc:// 的 kick 全部送不出去 ⇒ GM 永遠叫不到」）。
            # ⚠ 2026-10-08 二修：`Hub.rows[tid]` 是 **dict**（discover 的原始列），不是物件
            #   ⇒ `getattr(_row, "home")` 永遠是空的 ⇒ 接收器拿不到桌 config ⇒ **`kick.gm_api_profile`
            #   讀不到 ⇒ GM 跑在 default profile**（實測：那一輪的 GM 拿到 `mcp__tabletop__*` 與
            #   `terminal`／`read_file`——**瘦 profile 的隔離整組失效**，也開不了景）。
            #   現在從 `Hub.services[tid].home`（service 自己記的那一份）拿。
            _cfg = ""
            try:
                _svc = next(iter(Hub.services.values()), None)
                _home = str(getattr(_svc, "home", "") or "")
                _cand = os.path.join(_home, "config.json")
                if _home and os.path.exists(_cand):
                    _cfg = _cand
            except Exception:                                          # noqa: BLE001
                _cfg = ""
            st = kick_receiver.install_local(config=_cfg)
            _prof = getattr(st, "profile", "") or "（default）"
            _has_key = bool(getattr(st, "api_key", ""))
            print(f"[hub] 本機 GM 接收器已裝（session 模板 {st.session_tpl}；"
                  f"桌 config={_cfg or '（讀不到！）'}；GM profile={_prof}；憑證={_has_key}；"
                  f"init 到 {st.log_file}）", flush=True)
            if _cfg and not getattr(st, "profile", ""):
                print("[hub] ⚠ 桌 config 有讀到但 `kick.gm_api_profile` 是空的 ⇒ GM 會跑在 default "
                      "profile（沒有瘦 profile 的隔離）", flush=True)
            if not _has_key:
                print("[hub] ⚠ 接收器讀不到 API key ⇒ inproc:// 的 kick 會失敗（喊不出 GM）", flush=True)
            # ★ 2026-10-20：重啟後 `active`（落盤）會把「盯它的線程已死」的舊 run 當成還在跑
            #   ⇒ 那一桌之後每一腳都排隊。這裡對一次真值：還在跑就接回監看，已結束就收尾。
            try:
                st.recover_active()
            except Exception as exc:                                       # noqa: BLE001
                print(f"[hub] ⚠ 殘留 run 復原失敗（不影響起桌）：{type(exc).__name__}: {exc}",
                      flush=True)
        except Exception as exc:                                          # noqa: BLE001
            print(f"[hub] 本機接收器裝不起來（inproc:// 那條會失敗）：{type(exc).__name__}: {exc}",
                  flush=True)

    if args.reload_interval and args.reload_interval > 0:
        def _loop() -> None:
            while True:
                time.sleep(float(args.reload_interval))
                try:
                    Hub.rescan()
                except Exception:                                         # noqa: BLE001
                    pass
        threading.Thread(target=_loop, daemon=True).start()

    httpd = ThreadingHTTPServer((args.bind, args.port), Handler)
    print(f"[hub] 後台（單一進程）已起： http://{args.bind}:{args.port}/  "
          f"桌={sorted(Hub.services)}  管理 token 檔={Hub.token_file}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for st in list(Hub._stops.values()):
            st.set()
