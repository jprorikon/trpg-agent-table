#!/usr/bin/env python3
"""入口：GM 喚醒接收器。實作在 `trpg/kick/receiver/`。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from trpg.kick.receiver import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
