#!/bin/sh
# 把引擎倉的 SCENARIO_FORMAT.md 同步過來（本檔在骨架倉裡是**生成物**，別手改）。
#
#   用法：  tools/sync_scenario_format.sh                 # 用預設路徑找引擎倉
#           TRPG_ENGINE_REPO=/path/to/trpg tools/sync_scenario_format.sh
#
# 引擎倉＝有 `docs/SCENARIO_FORMAT.md` 的那棵樹（本機預設＝部署機上的 workspace/trpg）。
set -e
HERE=$(cd "$(dirname "$0")/.." && pwd)
ENGINE=${TRPG_ENGINE_REPO:-/srv/trpg}
SRC="$ENGINE/docs/SCENARIO_FORMAT.md"
DST="$HERE/SCENARIO_FORMAT.md"
if [ ! -f "$SRC" ]; then
  echo "找不到正本：$SRC" >&2
  echo "（用 TRPG_ENGINE_REPO=<引擎倉路徑> 指定）" >&2
  exit 1
fi

gen() {
  echo "> **本檔是生成物，別手改**：正本＝引擎倉（\`trpg/server\`）的 \`docs/SCENARIO_FORMAT.md\`。"
  echo "> 同步：\`TRPG_ENGINE_REPO=<引擎倉路徑> tools/sync_scenario_format.sh\`。"
  echo "> 你只要讀這一份就能寫劇本；要動引擎機制才需要看正本。"
  echo
  cat "$SRC"
}

# --check：只驗「骨架這份還跟得上正本嗎」（驗收／CI 用；不寫檔）。引擎那邊改了格式、忘了同步 ⇒ 這裡會叫。
if [ "${1:-}" = "--check" ]; then
  TMP=$(mktemp)
  gen > "$TMP"
  if cmp -s "$TMP" "$DST"; then
    echo "OK：骨架的 SCENARIO_FORMAT.md 與正本一致（$(wc -l < "$SRC") 行）"
    rm -f "$TMP"; exit 0
  fi
  echo "❌ 骨架的 SCENARIO_FORMAT.md 已落後正本 ⇒ 跑 tools/sync_scenario_format.sh 重生成" >&2
  rm -f "$TMP"; exit 1
fi

gen > "$DST"
echo "已同步：$DST"
echo "  來源：$SRC（$(wc -l < "$SRC") 行）"
