#!/bin/sh
# 看桌子的狀態。
#
#   deploy/status.sh                      # **預設問後台（hub）**：一次列出所有桌（開著／已收局／離席）
#   deploy/status.sh --single             # 單桌模式：平掃桌的位置（新制 modules/*/scenarios/*、舊制 campaigns/*）
#   deploy/status.sh campaigns/hyakki_tower   # 單桌模式下的單張桌細節（入口／進程／健康／席位／日誌尾巴）
#
# 2026-10-07 起 production＝**單一進程後台**（`hub_server.py`／`deploy/start_hub.sh`）⇒ 預設就問它。
HERE=$(cd "$(dirname "$0")/.." && pwd)
PY=${TRPG_PY:-python3}
PIDF="$HERE/logs/hub.pid"
PORT=${TRPG_HUB_PORT:-18971}          # 2026-10-19：前台容器站 8971，後台在 18971

if [ "$1" = "--single" ] || [ -n "$1" ]; then
  [ "$1" = "--single" ] && shift
  echo "== 單桌模式：掃 modules/*/scenarios/* 與 campaigns/*（走 layout.scan）=="
  "$PY" - "$HERE" "$@" <<'PYX'
import json, os, socket, subprocess, sys, urllib.request
root, want = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "")
sys.path.insert(0, root)
from trpg.core import layout as layout           # 桌的位置（新制 modules/<模組>/scenarios/<劇本>；舊制 campaigns/<桌>）
rows = []
for row in layout.scan(root):
    name, home = os.path.basename(row["home"]), row["home"]
    cfg = layout.read_config(home)
    if cfg is None:
        print(f"  {name}: config 讀不動")
        continue
    tid = str(cfg.get("table_id") or name)
    if want and name not in (want, tid) and os.path.basename(want) not in (name, tid):
        continue
    rows.append((tid, cfg, home))
for tid, cfg, home in rows:
    port = cfg.get("port")
    pidf = os.path.join(root, "logs", f"{tid}.pid")
    pid = open(pidf).read().strip() if os.path.exists(pidf) else "-"
    health = ""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{int(port)}/health", timeout=3) as r:
            h = json.loads(r.read().decode("utf-8"))
            health = f"健康 席次 {h.get('seats')} 回合 {h.get('turn')}"
    except Exception:
        health = "（連不上）"
    print(f"  {tid:<16} 埠 {port}  pid {pid}  {health}")
PYX
  exit 0
fi

# 預設：問 hub
if [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then
  echo "== 後台（單一進程，pid $(cat "$PIDF")）：http://0.0.0.0:$PORT/ =="
else
  echo "== 後台（hub）沒在跑（$PIDF 不存在）——下面試著問 $PORT =="
fi
"$PY" - "$PORT" <<'PYX'
import json, sys, urllib.request
port = sys.argv[1]
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=6) as r:
        d = json.loads(r.read().decode("utf-8"))
    print(f"  {d.get('status')}（{len(d.get('tables') or [])} 張桌）")
    for t in d.get("tables", []):
        st = "已收局" if t.get("ended") else ("開著" if t.get("online") else "離線")
        print(f"  {t['prefix']:<22} {st:<5} 席次 {t.get('seats')} 事件 {t.get('events')}"
              + (f"  ⚠ {t['error']}" if t.get("error") else ""))
except Exception as exc:
    print(f"  （/health 讀不到：{exc}）")
PYX
