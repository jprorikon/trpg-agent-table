#!/bin/sh
# 起桌（前台运行，Ctrl-C 停）。日志走标准输出，需要留档就重定向。
cd "$(dirname "$0")" || exit 1
exec python3 server.py "$@"
