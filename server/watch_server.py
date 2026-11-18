#!/usr/bin/env python3
"""入口：TRPG 觀戰桌（唯讀、獨立埠）。實作在 `trpg/api/watch.py`。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from trpg.api.watch import main  # noqa: E402

if __name__ == "__main__":
    main()
