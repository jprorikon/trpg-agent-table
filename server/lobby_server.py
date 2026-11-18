#!/usr/bin/env python3
"""入口：TRPG 大廳（開發／除錯用）。實作在 `trpg/api/lobby/`。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from trpg.api.lobby import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
