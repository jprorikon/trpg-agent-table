#!/bin/sh
# 停觀戰桌（唯讀前門）：按 pid 文件停。這張桌停掉不影響**任何別張桌**。
#
#   deploy/stop_watch.sh <campaign>     # 例：deploy/stop_watch.sh campaigns/hyakki_tower
#   deploy/stop_watch.sh                # 省略＝campaigns/hyakki_tower（只是預設值，不是特例）
HERE=$(cd "$(dirname "$0")/.." && pwd)
CAMPAIGN=${1:-campaigns/hyakki_tower}
case "$CAMPAIGN" in /*) CDIR="$CAMPAIGN" ;; *) CDIR="$HERE/$CAMPAIGN" ;; esac
CFG="$CDIR/config.json"
PY=${TRPG_PY:-python3}
TID=$("$PY" -c "import json;print(json.load(open('$CFG')).get('table_id') or '')" 2>/dev/null)
TID=${TID:-$(basename "$CDIR")}
PIDF="$HERE/logs/$TID.watch.pid"
[ -f "$PIDF" ] || { echo "沒有 pid 文件——$TID 的觀戰桌可能沒在跑（$PIDF）"; exit 0; }
PID=$(cat "$PIDF")
if ! kill -0 "$PID" 2>/dev/null; then echo "pid $PID 已經不在了，清掉 pid 文件"; rm -f "$PIDF"; exit 0; fi
kill "$PID" 2>/dev/null
for i in $(seq 1 30); do kill -0 "$PID" 2>/dev/null || break; sleep 0.2; done
if kill -0 "$PID" 2>/dev/null; then kill -9 "$PID" 2>/dev/null; sleep 0.3; fi
rm -f "$PIDF"
echo "$TID 的觀戰桌已停（pid $PID）"
