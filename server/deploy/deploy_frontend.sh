#!/bin/sh
# 把 trpg/frontend 的最新版**拉下来 → 校验 → 上线到本机桌 → 自测**（失败自动回滚）。
#
# 用法：
#   deploy/deploy_frontend.sh              # 拉远端 main、上线、跑三套测试
#   deploy/deploy_frontend.sh --rollback    # 回到最近一次备份
#   FRONTEND_REPO=/path BRANCH=dev deploy/deploy_frontend.sh
#
# 触发方式：Gitea 的 trpg/frontend push webhook（協作端 推前端）→ 桥 → GM agent run 里执行本脚本。
# 说明：本脚本只做机械步骤；**上线后的浏览器冒烟由 agent 负责**（页面红条/元素是否正常）。
set -eu
HERE=$(cd "$(dirname "$0")/.." && pwd)
REPO=${FRONTEND_REPO:-/srv/trpg-frontend}
BRANCH=${BRANCH:-main}
PY=${TRPG_PY:-python3}
LIVE="$HERE/web/index.html"
BAKDIR="$HERE/deploy/backup"
STAMP=$(date +%Y%m%d-%H%M%S)

if [ "${1:-}" = "--rollback" ]; then
  LAST=$(ls -1t "$BAKDIR"/index.html.* 2>/dev/null | head -1 || true)
  [ -n "$LAST" ] || { echo "没有备份可回滚" >&2; exit 1; }
  cp "$LAST" "$LIVE"
  echo "已回滚到 $LAST"
  LSTAMP=${LAST##*index.html.}
  if [ -f "$BAKDIR/lobby.html.$LSTAMP" ]; then cp "$BAKDIR/lobby.html.$LSTAMP" "$HERE/web/lobby.html"; echo "大廳一併回滚到 $LSTAMP"; fi
  exit 0
fi

echo "== 1/5 拉取 $REPO（$BRANCH）=="
git -C "$REPO" fetch -q origin "$BRANCH"
BEFORE=$(git -C "$REPO" rev-parse HEAD)
git -C "$REPO" checkout -q "$BRANCH" 2>/dev/null || git -C "$REPO" checkout -q -b "$BRANCH" "origin/$BRANCH"
git -C "$REPO" merge -q --ff-only "origin/$BRANCH"
AFTER=$(git -C "$REPO" rev-parse HEAD)
echo "   $BEFORE → $AFTER"
git -C "$REPO" log -1 --pretty='   %h %ad %an: %s' --date=short

SRC="$REPO/web/index.html"

echo "== 2/5 静态闸（钩子/标签/裸 n() =="
"$PY" "$HERE/deploy/check_frontend.py" "$SRC"
# 大廳頁（2026-10-19 起住前端 repo 的 web/lobby.html；hub 的 `/` 送它）——有就一起過閘、一起上線
LOBBY_SRC="$REPO/web/lobby.html"
LOBBY_LIVE="$HERE/web/lobby.html"
if [ -f "$LOBBY_SRC" ]; then "$PY" "$HERE/deploy/check_frontend.py" "$LOBBY_SRC"; fi
# 仓库自带的私密防线检查器（協作端 的 tools/check_tx_private.py）——有就跑，没跳过
if [ -f "$REPO/tools/check_tx_private.py" ]; then
  "$PY" "$REPO/tools/check_tx_private.py" "$SRC" || { echo "❌ 私密防线检查未过（翻译不得触碰私密字段）" >&2; exit 1; }
fi

echo "== 3/5 备份并上线 =="
mkdir -p "$BAKDIR"
if [ -f "$LIVE" ]; then cp "$LIVE" "$BAKDIR/index.html.$STAMP"; echo "   备份 → deploy/backup/index.html.$STAMP"; fi
cp "$SRC" "$LIVE"
echo "   上线 → web/index.html（$(wc -c < "$LIVE") 字节）"
if [ -f "$LOBBY_SRC" ]; then
  if [ -f "$LOBBY_LIVE" ]; then cp "$LOBBY_LIVE" "$BAKDIR/lobby.html.$STAMP"; fi
  cp "$LOBBY_SRC" "$LOBBY_LIVE"
  echo "   上线 → web/lobby.html（$(wc -c < "$LOBBY_LIVE") 字节；大廳＝hub 的 /）"
fi

echo "== 4/5 自测（三套，会真的取线上页面）=="
cd "$HERE"
FAIL=0
# ⚠ `TRPG_LEGACY_REST=1`：`/api/*` 2026-10-07 起對外 410（契約＝`POST /graphql`），而這三套測試還是走舊
#   REST 面（`tools/run_tests.sh` 同一個旋鈕）——不帶這個變數 ⇒ 三套**必紅**、每推一次前端就自動回滾
#   （2026-10-08 實測：`KeyError: 'seat'`，頁面被退回到舊版）。
export TRPG_LEGACY_REST=1
for t in test_table.py test_board.py test_semic.py; do
  if "$PY" "$t" >"/tmp/deploy_$t.log" 2>&1; then
    printf '   %-14s %s\n' "$t" "$(grep -Eo '[0-9]+ PASS / [0-9]+ FAIL' "/tmp/deploy_$t.log" | tail -1)"
  else
    printf '   %-14s ❌ 失败（日志 /tmp/deploy_%s.log）\n' "$t" "$t"; FAIL=1
  fi
done

if [ "$FAIL" = "1" ]; then
  echo "== 5/5 自测失败 → 自动回滚 =="
  cp "$BAKDIR/index.html.$STAMP" "$LIVE"
  if [ -f "$BAKDIR/lobby.html.$STAMP" ]; then cp "$BAKDIR/lobby.html.$STAMP" "$LOBBY_LIVE"
  elif [ -f "$LOBBY_SRC" ]; then rm -f "$LOBBY_LIVE"; fi   # 這次才第一次上大廳 ⇒ 拿掉，hub 退回舊大廳
  echo "   已回滚到 $STAMP；请修好后再推。"
  exit 1
fi

echo "== 5/5 完成 =="
echo "   commit=$AFTER  bytes=$(wc -c < "$LIVE")"
echo "   下一步（agent）：浏览器冒烟 —— 打开线上页面看有没有红条、日志/席位/地图是否正常。"
