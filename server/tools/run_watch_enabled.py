#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""印「這一局開不開觀戰」（1／0）——給 `deploy/start*.sh` 用。

2026-10-20：「桌和 run 等价」⇒ 開關住 `runs/<run>/watch.json`（沒有檔才退回
`config.watch.enabled`）。shell 腳本不重寫這段判斷，統一叫這一支。

    python3 tools/run_watch_enabled.py <桌 home> [--default 0|1]

`--default`：這一局與 config 都沒有 `enabled` 時要回什麼（`start.sh` 用 0＝沒開就不起；
`start_watch.sh` 用 1＝沒寫＝可以手動起，沿用舊行為）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from trpg.service import run_watch                                     # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("home")
    ap.add_argument("--default", choices=("0", "1"), default="0")
    args = ap.parse_args()
    home = os.path.abspath(args.home)
    default = args.default == "1"
    cfgp = os.path.join(home, "config.json")
    try:
        with open(cfgp, encoding="utf-8") as fh:
            cfg = json.load(fh)
        default = bool((cfg.get("watch") or {}).get("enabled", default))
    except (OSError, ValueError):
        pass
    print("1" if run_watch.enabled(home, "", default=default) else "0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
