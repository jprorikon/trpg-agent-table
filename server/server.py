#!/usr/bin/env python3
"""TRPG 桌服务（**单桌模式**）—— 一张桌一个进程（测试／开发用；production 走 `hub_server.py`）。

一个服务，三种入口（都是薄适配器，不是第二条业务线）：
  * 人类：GET /            → 网页桌子（手机也能开）
  * agent：POST /mcp       → HTTP MCP（Streamable HTTP），工具面 7 个
  * 席位唤醒：出向 POST    → 「轮到你」kick（可指向 GM agent webhook / 桥 / 任意 HTTP 端点）
另有 REST 兜底：/api/*（网页在用，也是不挂 MCP 的客户端/脚本的入口）。

**分層與分工**（2026-10-20 模組化；這一份只負責「讀 config → 起服務 → 印位置」）：
    trpg/engine/        L0 內核（裁決／事件／回放）＋ L2 投影 ＋ L4 句庫
    trpg/core/          L0 共用（節拍／回放／持久化 sink／桌的位置）
    trpg/rules/         L1 規則（packs／scenario／board／semic／各機制）
    trpg/service/       L6 業務層（TableService／table_factory／wake／assets）
    trpg/accounts/      L6 account 層（JSON registry／DB 後端）
    trpg/api/           L6 入口（web／graphql／mcp_api／hub／lobby／watch）
    trpg/kick/          L6 喚醒（sender／receiver）
    tests/              全部 test_*.py（含靜態分層閘）

用法：
  python3 server.py --config config.json --home . --port 8971
"""
from __future__ import annotations

import argparse
import atexit
import os
import sys
import threading
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from trpg.service import table_factory as table_factory                                                     # noqa: E402
from trpg.api.web import Handler                                              # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"))
    ap.add_argument("--home", default=HERE, help="数据目录的父目录（data/ 建在里面）")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--bind", default=None)
    ap.add_argument("--public-prefix", default=None,
                    help="對外的子路徑前綴（單一入口模式，例：/hyakki_tower）；省略＝讀 config 的 public_prefix")
    ap.add_argument("--pidfile", default=None,
                    help="把进程号写到这里（部署脚本用来 start/stop/status；进程退出时自动删）")
    args = ap.parse_args()
    if args.pidfile:
        with open(args.pidfile, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
        atexit.register(lambda p=args.pidfile: os.path.exists(p) and os.remove(p))

    cfg = table_factory.load_cfg(args.config)
    port = args.port or int(cfg.get("port", 8971))
    bind = args.bind or cfg.get("bind", "0.0.0.0")
    if args.public_prefix is not None:              # 單一入口（子路徑）：CLI 蓋 config
        cfg["public_prefix"] = str(args.public_prefix).strip().rstrip("/")

    svc = table_factory.build(args.home, cfg)       # 服務 ＋ 這一桌的工具目錄 ＋ gm/npc 入座
    Handler.service = svc
    stop = threading.Event()
    table_factory.start_tick(svc, stop)

    httpd = ThreadingHTTPServer((bind, port), Handler)
    print(f"[table] 桌子已起： http://{bind}:{port}/  (MCP: POST /mcp)  data={svc.table.events_path}",
          flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()


if __name__ == "__main__":
    main()
