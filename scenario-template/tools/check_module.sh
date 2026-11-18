#!/bin/sh
# 劇本／模組自檢的一鍵入口——把「引擎倉在哪、python 用哪隻」包起來，你只指一個目錄。
#
#   用法：
#     tools/check_module.sh <模組目錄>                          # 自檢（每個劇本各造一張臨時桌驗）
#     tools/check_module.sh <劇本目錄> [--module <模組目錄>]     # 自檢「這一齣戲」
#     tools/check_module.sh --serve <目錄> [埠]                 # 再起一張臨時桌給你玩
#
# 判據＝起桌會用的那一套（引擎 `tools/check_campaign.py`），不是這裡另寫一份。
set -u

ENGINE="${TRPG_ENGINE:-$(cd "$(dirname "$0")/../.." 2>/dev/null && pwd)/trpg}"
[ -f "$ENGINE/tools/check_campaign.py" ] || ENGINE="${TRPG_ENGINE:-$(cd "$(dirname "$0")/../.." 2>/dev/null && pwd)/server}"
[ -f "$ENGINE/tools/check_campaign.py" ] || ENGINE="${TRPG_ENGINE:-/srv/trpg}"
PY="${TRPG_PY:-python3}"
[ -x "$PY" ] || PY="python3"

SERVE=0; DIR=""; MODULE_ARG=""; PORT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --serve) SERVE=1; shift ;;
    --module) MODULE_ARG="${2:-}"; shift 2 ;;
    -*) shift ;;
    *) if [ -z "$DIR" ]; then DIR="$1"; else PORT="$1"; fi; shift ;;
  esac
done
DIR="${DIR:-.}"

hdr() { printf '\n\033[1m== %s\033[0m\n' "$1"; }

# ── 造一張臨時桌（複製定義 ＋ config.example.json → config.json）───────────────
mk_table() {   # $1=定義目錄 $2=桌ID [$3=模組目錄] → 印出臨時桌路徑
  _t=$(mktemp -d /tmp/trpg_probe.XXXXXX)
  cp -r "$1"/. "$_t"/ 2>/dev/null
  rm -rf "$_t/tables"
  # 模組層的包（`<模組>/packs/<id>/`：作品的內容包，例 rules.core）——臨時桌不在 scenarios/ 底下，
  # 引擎推不出模組 ⇒ 抄進臨時桌的 packs/；劇本層已有同名包就不蓋（劇本層優先，與引擎同一套順序）。
  if [ -n "${3:-}" ] && [ -d "$3/packs" ]; then
    mkdir -p "$_t/packs"
    for _p in "$3"/packs/*/; do
      _p=${_p%/}                     # 去掉尾斜線：BSD cp 對「src/」會只抄內容
      [ -d "$_p" ] || continue
      [ -e "$_t/packs/$(basename "$_p")" ] || cp -r "$_p" "$_t/packs/" 2>/dev/null
    done
  fi
  [ -f "$_t/config.example.json" ] && cp "$_t/config.example.json" "$_t/config.json"
  [ -f "$_t/config.json" ] && "$PY" - "$_t/config.json" "$2" <<'EOF'
import json, sys
p, tid = sys.argv[1], sys.argv[2]
c = json.load(open(p, encoding="utf-8"))
c["table_id"] = tid
c["port"] = 0
c.pop("hub_base", None); c.pop("public_prefix", None)
json.dump(c, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
EOF
  echo "$_t"
}

# ── 劇本目錄 ⇒ 先湊一個模組把它放進去 ─────────────────────────────────────────
mk_module_for() {   # $1=劇本目錄 → 印出**臨時**模組目錄（永遠不碰你給的那個模組）
  _m=$(mktemp -d /tmp/trpg_mod.XXXXXX)
  if [ -n "$MODULE_ARG" ]; then
    cp -r "$MODULE_ARG"/. "$_m"/ 2>/dev/null
    rm -rf "$_m"/scenarios/*/tables "$_m"/tables   # 別把別人的「桌」帶進來
    mkdir -p "$_m/scenarios/x"
    echo "$_m"; return
  fi
  cp "$1"/board.json "$1"/seats.json "$1"/scenario.json "$_m"/scenarios/x/ 2>/dev/null || {
    mkdir -p "$_m/scenarios/x"; cp "$1"/board.json "$1"/seats.json "$1"/scenario.json "$_m/scenarios/x"/ 2>/dev/null; }
  "$PY" - "$1/seats.json" "$_m" <<'EOF'
import json, os, sys
seats = json.load(open(sys.argv[1], encoding="utf-8"))
mod = sys.argv[2]
os.makedirs(os.path.join(mod, "scenarios", "x"), exist_ok=True)
cs = []
for r in (seats.get("seats") or []):
    cid = r.get("character")
    if cid and all(c["id"] != cid for c in cs):
        cs.append({"id": cid, "class": r.get("kind") or "player", "name": r.get("name") or cid})
json.dump({"id": "probe", "name": "（臨時模組）"}, open(os.path.join(mod, "module.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
json.dump({"module": "probe", "characters": cs}, open(os.path.join(mod, "characters.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
EOF
  echo "$_m"
}

run_checks() {   # $1=定義目錄 $2=桌ID [$3=模組目錄]
  _t=$(mk_table "$1" "$2" "${3:-}")
  if [ "$SERVE" = "1" ]; then
    hdr "起桌（臨時）：$_t"
    ( cd "$ENGINE" && exec "$PY" server.py --home "$_t" --config "$_t/config.json" ${PORT:+--port "$PORT"} )
  else
    ( cd "$ENGINE" && "$PY" tools/check_campaign.py "$_t" ) || true
  fi
  rm -rf "$_t"
}

if [ -f "$DIR/module.json" ] && [ -f "$DIR/characters.json" ]; then
  hdr "模組：$DIR"
  for _s in "$DIR"/scenarios/*/; do
    [ -d "$_s" ] || continue
    hdr "劇本：$(basename "$_s")"
    run_checks "$_s" "$(basename "$DIR")_$(basename "$_s")_1" "$DIR"
  done
elif [ -f "$DIR/board.json" ] || [ -f "$DIR/scenario.json" ]; then
  hdr "劇本：$DIR"
  _mod=$(mk_module_for "$DIR")
  _sdir="$_mod/scenarios/x"
  cp "$DIR"/board.json "$DIR"/seats.json "$DIR"/scenario.json "$_sdir"/ 2>/dev/null
  cp "$DIR"/goals.json "$DIR"/clues.json "$DIR"/config.example.json "$_sdir"/ 2>/dev/null
  cp "$DIR"/*.md "$_sdir"/ 2>/dev/null
  cp "$DIR"/scene*.json "$_sdir"/ 2>/dev/null
  [ -d "$DIR/packs" ] && cp -r "$DIR/packs" "$_sdir"/ 2>/dev/null
  run_checks "$_sdir" "probe_x_1" "$_mod"
  rm -rf "$_mod"
else
  echo "看不出這是模組還是劇本目錄（要 module.json ＋ characters.json，或 board.json／scenario.json）：$DIR" >&2
  exit 2
fi
