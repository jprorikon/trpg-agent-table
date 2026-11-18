#!/bin/sh
# **TRPG 後台（單一進程）**的起停：一個 process 裝下所有桌（設計者 2026-10-07「trpg后台还是做成单一进程吧」）。
#
#   deploy/start_hub.sh              # 起（預設埠 **18971**，把所有非 hidden 的桌都帶起來）
#   ※ 2026-10-19 拆前後台：對外 8971 是**前台容器（nginx）**；後台退到 loopback/內網 18971。
#   deploy/start_hub.sh --stop       # 停（一停全停——它本來就是一個進程）
#   deploy/start_hub.sh status       # 問 /health（各桌狀態）
#   TRPG_HUB_PORT=18971 TRPG_HUB_RELOAD=5 deploy/start_hub.sh   # 每 5 秒自動 rescan（收新桌／移除已刪）
#
# 路由：`/`＝大廳、`/<桌>/…`＝該桌、`/<桌>/watch/…`＝觀戰（唯讀）、`/<桌>/mcp`＝agent、
#       `/health`＝各桌狀態、`/__hub/reload|rescan`＝管理面（token 在 logs/hub.token）。
# 桌的 config：`public_prefix`（對外子路徑）、`kick.receiver_url: "inproc://"`（GM 喚醒走同進程）。
# 部署机铁律：不寫 systemd/cron、不動開機項。
set -e
HERE=$(cd "$(dirname "$0")/.." && pwd)
PY=${TRPG_PY:-python3}
PORT=${TRPG_HUB_PORT:-18971}          # 前台容器（nginx）站 8971；後台只在內網 18971
RELOAD=${TRPG_HUB_RELOAD:-0}
LOGDIR="$HERE/logs"; mkdir -p "$LOGDIR"
PIDF="$LOGDIR/hub.pid"; LOG="$LOGDIR/hub.log"
CMD=${1:-start}

alive() { [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; }

if [ "$CMD" = "--stop" ] || [ "$CMD" = "stop" ]; then
  if ! alive; then echo "hub 沒在跑"; rm -f "$PIDF"; exit 0; fi
  P=$(cat "$PIDF"); kill "$P" 2>/dev/null || true
  for i in $(seq 1 40); do kill -0 "$P" 2>/dev/null || break; sleep 0.25; done
  kill -0 "$P" 2>/dev/null && kill -9 "$P" 2>/dev/null || true
  rm -f "$PIDF"; echo "hub 已停（pid $P）"; exit 0
fi

if [ "$CMD" = "status" ]; then
  "$PY" - "$PORT" <<'PYX'
import json, sys, urllib.request
port = sys.argv[1]
try:
    d = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=8))
    print(f"hub：{d.get('status')}（{len(d.get('tables') or [])} 張桌）")
    for t in d.get("tables", []):
        st = "已收局" if t.get("ended") else ("開著" if t.get("online") else "離線")
        print(f"  {t['prefix']:<22} {st:<5} 席次 {t.get('seats')} 事件 {t.get('events')}"
              + (f"  ⚠ {t['error']}" if t.get("error") else ""))
except Exception as exc:
    print(f"（/health 讀不到：{exc}）")
PYX
  exit 0
fi

if alive; then echo "hub 已在跑（pid $(cat "$PIDF")，:$PORT）"; exit 0; fi
port_holder() {
  command -v ss >/dev/null 2>&1 || return 0
  ss -ltnp 2>/dev/null | grep ":$PORT " | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2
}
HOLD=$(port_holder)
if [ -n "$HOLD" ]; then
  echo "埠 $PORT 已經被 pid $HOLD 占著 ⇒ 先處理它再起 hub" >&2
  exit 1
fi
cd "$HERE"
setsid "$PY" hub_server.py --port "$PORT" --root "$HERE" --pidfile "$PIDF" \
        --reload-interval "$RELOAD" >>"$LOG" 2>&1 </dev/null &
for i in $(seq 1 60); do alive && break; sleep 0.25; done
if ! alive; then echo "hub 起不來，日誌尾巴：" >&2; tail -n 25 "$LOG" >&2; exit 1; fi
NEW=$(cat "$PIDF"); H2=$(port_holder)
if [ -n "$H2" ] && [ "$H2" != "$NEW" ]; then
  echo "起來了（pid $NEW）但埠 $PORT 上的是 pid $H2 ⇒ 沒有真的生效" >&2; exit 1
fi
TAIL=$(ip -4 addr show tailscale0 2>/dev/null | grep -o 'inet [0-9.]*' | awk '{print $2}' | head -1)
LAN=$(ip -4 route get 1.1.1.1 2>/dev/null | grep -o 'src [0-9.]*' | awk '{print $2}' | head -1)
echo "後台（單一進程）已起：pid $NEW，對外**唯一**埠 $PORT（log=logs/hub.log；管理 token=logs/hub.token）"
# ★ 2026-10-19 拆前後台：後台**不送網頁**（`/` 回 410）——頁面在前台容器（trpg/frontend-nuxt）。
echo "  API（前台／agent 都打這個）："
echo "    http://${TAIL:-<tailnet-ip>}:$PORT/graphql          ← 唯一客戶端端點（桌是欄位）"
echo "    http://${LAN:-<lan-ip>}:$PORT/mcp                   ← agent 的 MCP 前門"
echo "  頁面（大廳／桌面／觀戰）由前台容器提供，不在這個埠上。"
"$PY" - "$PORT" <<'PYX'
import json, sys, urllib.request
port = sys.argv[1]
try:
    d = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=8))
    for t in d.get("tables", []):
        st = "已收局" if t.get("ended") else ("開著" if t.get("online") else "離線")
        print(f"  {t['prefix']:<22} {st:<5} 席次 {t.get('seats')} 事件 {t.get('events')}")
except Exception as exc:
    print(f"  （/health 讀不到：{exc}）")
PYX
