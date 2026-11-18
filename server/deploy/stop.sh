#!/bin/sh
# 停一張桌（**所有局同級**：任何劇本都走這一支）。
#
#   deploy/stop.sh <桌的家目錄>       # 例：deploy/stop.sh modules/hyakki_tower/scenarios/main
#   deploy/stop.sh                  # 省略＝用 layout 解 hyakki_tower（只是預設值，不是特例）
#   deploy/stop.sh --all            # 停掉這台機器上**所有**在跑的桌（＋它們的觀戰桌）
#
# 一桌一份 pid：logs/<table_id>.pid（跟 start.sh 同一套命名）。
# ⚠ GM 接收器是**機器級**的：只有在**已經沒有其他桌在跑**時才順手停它
#   ——以前不分青紅皂白停掉，會讓「還開著的另一張桌」的 GM 從此叫不醒而且沒有錯誤。
# 資料不用管——狀態在該桌的 data/events.jsonl（階段 1c 之後是 runs/<session>/events.jsonl），重啟即從事件流恢復。
HERE=$(cd "$(dirname "$0")/.." && pwd)
. "$HERE/deploy/home.sh"                  # `trpg_home`：桌的家目錄解析
PY=${TRPG_PY:-python3}

stop_pidfile() {                       # $1=pidfile $2=顯示名
  [ -f "$1" ] || { echo "  $2：沒有 pid 文件（可能沒在跑，或不是用 deploy/start.sh 起的）"; return 0; }
  PID=$(cat "$1")
  if ! kill -0 "$PID" 2>/dev/null; then echo "  $2：pid $PID 已經不在了，清掉 pid 文件"; rm -f "$1"; return 0; fi
  kill "$PID" 2>/dev/null
  for i in $(seq 1 30); do kill -0 "$PID" 2>/dev/null || break; sleep 0.2; done
  if kill -0 "$PID" 2>/dev/null; then echo "  $2：SIGTERM 沒停住，用 SIGKILL"; kill -9 "$PID" 2>/dev/null; sleep 0.5; fi
  rm -f "$1"; echo "  $2：已停（pid $PID）"
}

ALIVE=""
for f in "$HERE"/logs/*.pid; do
  [ -f "$f" ] || continue
  case "$f" in */kick_receiver.pid|*/lobby.pid) continue ;; esac      # 機器級服務另論
  kill -0 "$(cat "$f")" 2>/dev/null && ALIVE="$ALIVE $f"
done

if [ "${1:-}" = "--all" ]; then
  echo "== 停掉這台機器上所有在跑的桌 =="
  for f in $ALIVE; do
    ID=$(basename "$f" .pid); stop_pidfile "$f" "$ID"
    WA="${HERE}/logs/${ID}.watch.pid"; [ -f "$WA" ] && stop_pidfile "$WA" "${ID}（觀戰桌）"
  done
  ALIVE=""
else
  CAMPAIGN=${1:-$(trpg_home "$HERE" hyakki_tower)}
  case "$CAMPAIGN" in /*) CDIR="$CAMPAIGN" ;; *) CDIR="$HERE/$CAMPAIGN" ;; esac
  ID=$("$PY" -c "import json;print(json.load(open('$CDIR/config.json')).get('table_id') or '')" 2>/dev/null)
  [ -n "$ID" ] || ID=$(basename "$CDIR")
  stop_pidfile "$HERE/logs/$ID.pid" "$ID"
  WA="$HERE/logs/$ID.watch.pid"; [ -f "$WA" ] && stop_pidfile "$WA" "$ID（觀戰桌）"
  # 這張桌停掉之後，還有別張桌在跑嗎？
  ALIVE=""
  for f in "$HERE"/logs/*.pid; do
    [ -f "$f" ] || continue
    case "$f" in */"$ID.pid") continue ;; */kick_receiver.pid|*/lobby.pid) continue ;; esac
    kill -0 "$(cat "$f")" 2>/dev/null && ALIVE="$ALIVE $f"
  done
fi

if [ -n "$ALIVE" ]; then
  echo "（還有桌在跑：$(for f in $ALIVE; do basename "$f" .pid; echo -n ' '; done)——GM 接收器留著）"
else
  "$(dirname "$0")/kick_receiver.sh" stop >/dev/null 2>&1 || true
  echo "（已無桌在跑 ⇒ GM 接收器一起停）"
fi
