#!/usr/bin/env python3
"""入口：TRPG 後台（單一進程）。實作在 `trpg/api/hub/`（見該處 docstring）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import trpg.api.hub as _hub  # noqa: E402

# 前台以 `python hub_server.py` 起時，跨桌互斥（web_api._busy_tables）看到的
# 必須是**同一份** Hub；多數程式碼 `from trpg.api import hub as hub_server` 會拿到它。
sys.modules.setdefault("hub_server", _hub)

from trpg.api.hub import main  # noqa: E402

if __name__ == "__main__":
    main()
