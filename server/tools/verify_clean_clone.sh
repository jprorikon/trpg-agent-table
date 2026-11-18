#!/bin/bash
# **「乾淨 clone 就能跑」的驗證器**（review R3，2026-10-24）。
#
#   bash tools/verify_clean_clone.sh              # 全部測試
#   bash tools/verify_clean_clone.sh test_skills  # 只跑符合的
#   TRPG_KEEP=1 bash tools/verify_clean_clone.sh  # 留著臨時樹（紅了要進去看時）
#
# 為什麼要有它：這棵樹的測試以前**暗暗依賴本機狀態**——倉庫根的 `config.json`（不進版控）、
# `state/tables/` 那一局的事件流、`state/_retired/` 的歸檔。這些東西在寫的人機器上都有，
# 在別人的 clone 上一樣都沒有 ⇒ 28/54 支紅，而且紅的原因看不出來。
# 這支腳本模擬「別人 clone 下來」的完整流程：
#   1. `git archive HEAD`（**只拿進版控的檔案**：沒有 state／logs／config.json）
#   2. `deploy/modules_up.sh`（模組各自一個 repo，不在這棵樹裡）
#   3. `tools/run_tests.sh`
# 全綠＝這棵樹真的自足；紅了就把臨時目錄留著（自動印出路徑）。
set -u
HERE=$(cd "$(dirname "$0")/.." && pwd) || exit 1

TMP=$(mktemp -d "${TMPDIR:-/tmp}/trpg_clean_XXXXXX") || exit 1
cleanup() {
  if [ "${TRPG_KEEP:-0}" = "1" ] || [ "${_KEEP:-0}" = "1" ]; then
    echo "（臨時樹留著：$TMP）"
  else
    rm -rf "$TMP"
  fi
}
trap cleanup EXIT

echo "== 1/3 只拿進版控的檔案 → $TMP"
git -C "$HERE" archive HEAD | tar -x -C "$TMP" || { echo "git archive 失敗" >&2; _KEEP=1; exit 1; }
if [ -e "$TMP/state" ] || [ -e "$TMP/config.json" ]; then
  echo "⚠ 臨時樹裡竟然有 state/ 或 config.json（版控漏了東西）" >&2; _KEEP=1; exit 1
fi
echo "   進版控檔案 $(git -C "$HERE" ls-files | wc -l) 個；模組目錄不在其中（正常）"

echo "== 2/3 把模組 repo 擺進來（deploy/modules_up.sh）"
BASE=${TRPG_GITEA_BASE:-}
if [ -z "$BASE" ]; then
  # remote 名：部署機上叫 `gitea`，一般 clone 叫 `origin`——依序找（2026-10-24：Mac 上的 clone 只有 origin）
  URL=$(git -C "$HERE" remote get-url gitea 2>/dev/null || git -C "$HERE" remote get-url origin 2>/dev/null || true)
  case "$URL" in
    http://*|https://*) BASE=$(printf '%s' "$URL" | sed -E 's#^(https?://[^/]*)/.*#\1#') ;;
  esac
fi
if [ -z "$BASE" ]; then
  echo "沒有 gitea／origin remote（http/https）也沒有 TRPG_GITEA_BASE ⇒ 模組 clone 不了（測試會大量紅）" >&2
  _KEEP=1
  exit 1
fi
( cd "$TMP" && TRPG_GITEA_BASE="$BASE" sh deploy/modules_up.sh ) || { echo "模組擺不進來" >&2; _KEEP=1; exit 1; }

echo "== 3/3 跑測試（工具鏈用這支樹自己的 tools/run_tests.sh）"
( cd "$TMP" && bash tools/run_tests.sh "$@" )
CODE=$?
if [ "$CODE" -eq 2 ]; then
  # run_tests.sh 的退出碼 2＝環境前置不符（沒有 python／版本 <3.10）——**不是**樹的問題，別誤報
  echo "✗ 這台機器的環境不夠格（見上面 run_tests.sh 的訊息）——測試根本沒跑，不代表樹有本機狀態依賴" >&2
elif [ "$CODE" -ne 0 ]; then
  _KEEP=1
  echo "✗ 乾淨 clone 上紅了——這棵樹還有本機狀態依賴" >&2
else
  echo "✓ 乾淨 clone 全綠（＝別人 clone 下來也跑得起來）"
fi
exit "$CODE"
