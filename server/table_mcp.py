#!/usr/bin/env python3
"""入口：stdio ↔ HTTP 的 MCP 橋。實作在 `trpg/service/table_mcp.py`。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from trpg.service.table_mcp import main  # noqa: E402

if __name__ == "__main__":
    main()
