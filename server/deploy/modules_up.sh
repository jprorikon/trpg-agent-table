#!/bin/sh
# モジュール repo を `modules/` に配置する（サーバー repo は modules/ を版管理しない）。
#
#   deploy/modules_up.sh                 # deploy/modules.list の全部
#   deploy/modules_up.sh hyakki_tower     # 1 個だけ
#   TRPG_GITEA_BASE=http://host:3000 deploy/modules_up.sh   # 認証込み URL を別に渡す場合
#
# 既に clone 済みなら `git pull --ff-only`（ローカルに commit があると止まる＝黙って壊さない）。
# 認証は**この repo の `gitea` remote の userinfo を再利用**する（新しい秘密を増やさない）。
# 反映: 新しい劇本を足しただけなら hub の `/__hub/rescan`、コード側の変更は hub 再起動。
set -e
HERE=$(cd "$(dirname "$0")/.." && pwd)
LIST="$HERE/deploy/modules.list"
[ -f "$LIST" ] || { echo "ない: $LIST" >&2; exit 1; }

BASE=${TRPG_GITEA_BASE:-}
if [ -z "$BASE" ]; then
  # remote 名：部署機上叫 `gitea`，一般 clone 叫 `origin`——依序找（2026-10-24：Mac 上的 clone 只有 origin）
  URL=$(git -C "$HERE" remote get-url gitea 2>/dev/null || git -C "$HERE" remote get-url origin 2>/dev/null || true)
  case "$URL" in
    http://*|https://*) BASE=$(printf '%s' "$URL" | sed -E 's#^(https?://[^/]*)/.*#\1#') ;;
    *) echo "gitea remote も TRPG_GITEA_BASE も無い" >&2; exit 1 ;;
  esac
fi

WANT=${1:-}
grep -v '^[[:space:]]*#' "$LIST" | while read -r ID REPO; do
  [ -n "$ID" ] || continue
  [ -z "$WANT" ] || [ "$WANT" = "$ID" ] || continue
  DIR="$HERE/modules/$ID"
  if [ -d "$DIR/.git" ]; then
    echo "== $ID: pull"
    git -C "$DIR" pull --ff-only --quiet origin main
  else
    echo "== $ID: clone $REPO"
    mkdir -p "$HERE/modules"
    git clone --quiet "$BASE/$REPO.git" "$DIR"
  fi
  printf '   %s  %s\n' "$(git -C "$DIR" rev-parse --short HEAD)" "$(git -C "$DIR" log -1 --format=%s)"
done
echo "（劇本を足しただけなら hub の /__hub/rescan。コード変更は hub 再起動）"
