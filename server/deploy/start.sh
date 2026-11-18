#!/bin/sh
# 起一張桌（**所有局同級**：任何劇本都走這一支；沒有「主桌」，也沒有專屬某一張桌的路徑）。
#
#   deploy/start.sh <桌的家目錄> [埠]        # 例：deploy/start.sh modules/hyakki_tower/scenarios/main
#   deploy/start.sh                        # 省略＝用 layout 解 hyakki_tower（**只是預設值，不是特例**）
#   TRPG_PY=/path/python TRPG_PORT=9000 deploy/start.sh <路徑>
#
# 一桌一份 pid／log／（埠）：logs/<table_id>.pid、logs/<table_id>.log、埠預設讀該桌 config.json 的 port。
# 順手做兩件**跟哪張桌無關**的事：
#   ① 確保 GM 接收器在（kick_receiver.py 是**機器級**的收端——不是某張桌的附屬品）；
#   ② 這一局的 `watch.json` 開著（舊局看 config.watch.enabled）⇒ 起**它自己的**觀戰桌（唯讀、獨立埠）。
#
# 部署机铁律：本腳本**不寫 systemd/cron**、不改任何開機項。要開機自啟自己決定
#           （deploy/trpg-table.service 只是模板，沒裝也沒 enable）。
set -e
HERE=$(cd "$(dirname "$0")/.." && pwd)
. "$HERE/deploy/home.sh"                       # `trpg_home`：桌的家目錄解析（不再硬編 campaigns/<桌>）
CAMPAIGN=${1:-$(trpg_home "$HERE" hyakki_tower)}
case "$CAMPAIGN" in /*) CDIR="$CAMPAIGN" ;; *) CDIR="$HERE/$CAMPAIGN" ;; esac
[ -f "$CDIR/config.json" ] || { echo "沒有 $CDIR/config.json" >&2; exit 1; }
PY=${TRPG_PY:-python3}
ID=$("$PY" -c "import json;print(json.load(open('$CDIR/config.json')).get('table_id') or '')")
[ -n "$ID" ] || { echo "$CDIR/config.json 沒有 table_id" >&2; exit 1; }
PORT=${2:-${TRPG_PORT:-}}
[ -n "$PORT" ] || PORT=$("$PY" -c "import json;print(json.load(open('$CDIR/config.json')).get('port') or 8986)")
LOGDIR="$HERE/logs"; mkdir -p "$LOGDIR"
PIDF="$LOGDIR/$ID.pid"
LOG="$LOGDIR/$ID.log"

# GM 接收器（kick_receiver.py，收）：各桌的 config.kick.receiver_url（GM 喚醒路）都指到它，
# 所以它必須在——不在的話玩家私訊／公開發言會「叫不醒 GM」，而且不會有任何錯誤。已在跑＝no-op。
"$(dirname "$0")/kick_receiver.sh" start || \
  echo "（警告：GM 接收器沒起來——玩家私訊會叫不醒 GM；見 logs/kick_receiver.log）" >&2

if [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then
  echo "$ID 已经在跑（pid $(cat "$PIDF")）。先 deploy/stop.sh $CAMPAIGN" >&2
  exit 1
fi

# ⚠ 2026-10-07 踩到：pidfile 不在、但**埠被別的實例占著**（前一輪 ad-hoc 起的 server.py）⇒
#   新碼起不來卻照印「已起」，症狀是「改了引擎卻看不到效果」。⇒ 先查 listener，佔著就當場叫。
port_holder() {
  command -v ss >/dev/null 2>&1 || return 0
  ss -ltnp 2>/dev/null | grep ":$PORT " | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2
}
HOLD=$(port_holder)
if [ -n "$HOLD" ]; then
  echo "埠 $PORT 已經被 pid $HOLD 占著（不是本 pidfile 那隻）⇒ 起來也不會生效。" >&2
  echo "先處理它：kill $HOLD（或 deploy/stop.sh <那張桌>），再重跑本腳本。" >&2
  exit 1
fi

cd "$HERE"
# 引擎 → GM 喚醒連結（**送出端**的目標）：URL 與 token 檔。值不進 repo；
# config.kick.receiver_url／receiver_token_file 若設會蓋過這兩個 env（新名優先，舊名 url/token_file 相容）。
export TRPG_GM_KICK_URL="${TRPG_GM_KICK_URL:-http://127.0.0.1:8893/trpg/kick}"
export TRPG_GM_KICK_TOKEN_FILE="${TRPG_GM_KICK_TOKEN_FILE:-$HERE/../.secrets/trpg_kick_receiver.token}"
# 让服务把 pid 自己写下来（比靠 $! 可靠）；setsid 脱离当前会话/控制终端
setsid "$PY" server.py --config "$CDIR/config.json" --home "$CDIR" \
      --port "$PORT" --pidfile "$PIDF" >>"$LOG" 2>&1 </dev/null &

for i in $(seq 1 40); do
  [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null && break
  sleep 0.2
done
if [ ! -f "$PIDF" ]; then
  echo "起不来，日志尾部：" >&2
  tail -n 20 "$LOG" >&2
  exit 1
fi
# 起來之後再核一次：埠上的 listener 必須是**我們剛起的那隻**（否則等於沒生效）
NEWPID=$(cat "$PIDF")
HOLD2=$(port_holder)
if [ -n "$HOLD2" ] && [ "$HOLD2" != "$NEWPID" ]; then
  echo "起來了（pid $NEWPID）但埠 $PORT 上的是 pid $HOLD2 ⇒ 沒有真的生效！" >&2
  exit 1
fi
echo "$ID 已起：pid $NEWPID，埠 $PORT，log=logs/$ID.log"

# 這一局若開了觀戰（`runs/<run>/watch.json`；舊局看 config.watch.enabled）⇒ 順手把它也起起來。
# 沒開 ⇒ 這裡一行都不做。
if [ "$("$PY" "$HERE/tools/run_watch_enabled.py" "$CDIR" 2>/dev/null || echo 0)" = "1" ]; then
  "$(dirname "$0")/start_watch.sh" "$CAMPAIGN" || \
    echo "（警告：$ID 的觀戰桌沒起來——見 logs/$ID.watch.log；不影響這一桌與其他桌）" >&2
fi

"$(dirname "$0")/status.sh" "$CAMPAIGN"
