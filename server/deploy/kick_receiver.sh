#!/bin/sh
# **GM 接收器（收）**：kick_receiver.py 的起停——與桌同一個生命週期，但不寫 systemd/cron（部署機鐵律）。
# 對面那支是 kick_sender.py（引擎把腳送出來）；這個檔只負責把接收器拉起來／收掉。
#
#   用法： deploy/kick_receiver.sh start|stop|status
#
# 為什麼要它：桌的 config.kick.receiver_url（GM 喚醒路）指到這個接收器；
# 接收器不在＝玩家的私訊/公開發言叫不醒 GM（而且不會有任何錯誤——只是沒人回應）。
# 所以它跟桌一起被 start.sh / stop.sh 帶著走。
set -e
HERE=$(cd "$(dirname "$0")/.." && pwd)
PY=${TRPG_PY:-python3}
LOGDIR="$HERE/logs"; mkdir -p "$LOGDIR"
PIDF="$LOGDIR/kick_receiver.pid"
LOG="$LOGDIR/kick_receiver.log"
PORT=${TRPG_GM_KICK_PORT:-8972}
CMD=${1:-status}

alive() { [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; }

case "$CMD" in
  start)
    if alive; then echo "GM 激活口已在跑（pid $(cat "$PIDF")，:$PORT）"; exit 0; fi
    cd "$HERE"
    setsid "$PY" kick_receiver.py --port "$PORT" --pidfile "$PIDF" >>"$LOGDIR/kick_receiver.stdout.log" 2>&1 </dev/null &
    for i in $(seq 1 25); do alive && break; sleep 0.2; done
    if alive; then
      echo "GM 激活口起來了（pid $(cat "$PIDF")，:$PORT）"
    else
      echo "GM 激活口起不来，看 $LOG" >&2; tail -n 10 "$LOG" >&2; exit 1
    fi
    ;;
  stop)
    if ! alive; then echo "GM 激活口沒在跑"; rm -f "$PIDF"; exit 0; fi
    P=$(cat "$PIDF"); kill "$P" 2>/dev/null || true
    for i in $(seq 1 25); do kill -0 "$P" 2>/dev/null || break; sleep 0.2; done
    kill -0 "$P" 2>/dev/null && kill -9 "$P" 2>/dev/null || true
    rm -f "$PIDF"; echo "GM 激活口已停（pid $P）"
    ;;
  status)
    if alive; then
      echo "GM 激活口在跑：pid $(cat "$PIDF")，:$PORT"
      curl -s -m 3 "http://127.0.0.1:$PORT/health" || true
      echo
    else
      echo "GM 激活口沒在跑（=$PORT）"
    fi
    ;;
  *) echo "用法： deploy/kick_receiver.sh start|stop|status" >&2; exit 2 ;;
esac
