#!/bin/bash
# 跑全部引擎測試（只印摘要）。
#
#   bash tools/run_all_tests.sh                # 全部
#   bash tools/run_all_tests.sh skills board   # 名稱片段
#
# 2026-10-24（review R3）：這裡從前是**第二套實作**（自己 cd 死路徑、自己挑 python、自己 grep 摘要）
# ⇒ 跟 `tools/run_tests.sh` 兩邊會漂。現在只轉呼叫那一支（唯一入口）。
exec bash "$(cd "$(dirname "$0")" && pwd)/run_tests.sh" "$@"
