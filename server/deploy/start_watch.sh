#!/bin/sh
# 觀戰桌（`watch_server.py` 唯讀前門）啟動：跟上游桌**同一個 --home**，獨立埠（預設 8990）。
#
# 用法：  deploy/start_watch.sh [campaign] [port]
# 前提：  這一局的 `runs/<run>/watch.json` 開著（舊局沒有檔才看 config 的 watch.enabled）
#         要臨時起（例如試玩桌）就 TRPG_WATCH_FORCE=1。
#
# 唯讀：這一台沒有 act／say／join／enter／login／gm／mcp——觀戰者在這張桌上沒有席位，
#       不可能進回合、也不可能擋收景。設計＝docs/PLAN_spectate_table.md（C-a）。
set -e
HERE=$(cd "$(dirname "$0")/.." && pwd)
. "$HERE/deploy/home.sh"                  # `trpg_home`：桌的家目錄解析
CAMPAIGN=${1:-$(trpg_home "$HERE" hyakki_tower)}
PY=${TRPG_PY:-python3}
case "$CAMPAIGN" in
  /*) CFG="$CAMPAIGN/config.json" ;;          # 絕對路徑也吃（桌不在引擎樹裡時）
  *)  CFG="$HERE/$CAMPAIGN/config.json" ;;
esac
[ -f "$CFG" ] || { echo "找不到 $CFG" >&2; exit 1; }
ENABLED=$("$PY" "$HERE/tools/run_watch_enabled.py" "$(dirname "$CFG")" --default 1 2>/dev/null || echo 1)
DEFPORT=$("$PY" -c "import json;print((json.load(open('$CFG')).get('watch') or {}).get('port') or 8990)" 2>/dev/null || echo 8990)
PORT=${2:-${TRPG_WATCH_PORT:-$DEFPORT}}
if [ "$ENABLED" != "1" ] && [ "${TRPG_WATCH_FORCE:-0}" != "1" ]; then
  echo "這一局沒開觀戰（runs/<run>/watch.json 或 config.watch.enabled 是 false），或 TRPG_WATCH_FORCE=1 臨時起。" >&2
  exit 1
fi
LOGDIR="$HERE/logs"; mkdir -p "$LOGDIR"
# 一桌一份（**所有局同級**）：logs/<table_id>.watch.pid｜.watch.log——不再用「machine 一張 watch.pid」
TID=$("$PY" -c "import json;print(json.load(open('$CFG')).get('table_id') or '')" 2>/dev/null)
TID=${TID:-$(basename "$(dirname "$CFG")")}
PIDF="$LOGDIR/$TID.watch.pid"; LOG="$LOGDIR/$TID.watch.log"

# 埠守衛（同 start.sh：起前查 listener、起後核「必須是我的」）
port_holder() {
  command -v ss >/dev/null 2>&1 || return 0
  ss -ltnp 2>/dev/null | grep ":$PORT " | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2
}
if [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then
  echo "觀戰桌已经在跑（pid $(cat "$PIDF")）。先 deploy/stop_watch.sh" >&2
  exit 1
fi
HOLD=$(port_holder)
if [ -n "$HOLD" ]; then
  echo "埠 $PORT 已經被 pid $HOLD 占著 ⇒ 起來也不會生效。先處理它（kill $HOLD）。" >&2
  exit 1
fi

cd "$HERE"
setsid "$PY" watch_server.py --home "$CAMPAIGN" --port "$PORT" --pidfile "$PIDF" >>"$LOG" 2>&1 </dev/null &
for i in $(seq 1 40); do
  [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null && break
  sleep 0.2
done
if [ ! -f "$PIDF" ]; then
  echo "起不来，日志尾部：" >&2; tail -n 20 "$LOG" >&2; exit 1
fi
NEWPID=$(cat "$PIDF"); HOLD2=$(port_holder)
if [ -n "$HOLD2" ] && [ "$HOLD2" != "$NEWPID" ]; then
  echo "起來了（pid $NEWPID）但埠 $PORT 上的是 pid $HOLD2 ⇒ 沒有真的生效！" >&2
  exit 1
fi

TAIL=$(ip -4 addr show tailscale0 2>/dev/null | grep -o 'inet [0-9.]*' | awk '{print $2}' | head -1)
LAN=$(ip -4 route get 1.1.1.1 2>/dev/null | grep -o 'src [0-9.]*' | awk '{print $2}' | head -1)
echo "== 觀戰桌（**唯讀**）已起 pid=$NEWPID（log: $LOG）=="
echo "  tailnet : http://${TAIL:-<tailnet-ip>}:$PORT/"
echo "  LAN     : http://${LAN:-<lan-ip>}:$PORT/"
echo "  上游桌：$("$PY" -c "import json;print('http://127.0.0.1:'+str(json.load(open('$CFG')).get('port') or 8971))")"
