#!/usr/bin/env bash
# 起 TRPG 的 PostgreSQL 容器（單一副本；本機 部署機）。
#
# 為什麼用容器：關係資料（account / wallet / char_instance / run）要真的資料庫；
# 依設計者 2026-10-07「需要用資料庫處理關係時，用 trim-cli 相關技能起 docker」。
#
# 用法（需要 sudo docker；本機走 tools/nas_sudo.py）：
#   sudo bash deploy/db_up.sh            # 沒有就建、有就 start（幂等）
#   sudo bash deploy/db_up.sh status
#   sudo bash deploy/db_up.sh psql       # 進去下 SQL（互動）
#
# 設定：`.secrets/trpg_db.env`（600；不進版控）
#   TRPG_DB_PORT=8433
#   TRPG_DB_USER=trpg
#   TRPG_DB_NAME=trpg
#   TRPG_DB_PASSWORD=<生成>
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$HERE")"
ENVF="$REPO/.secrets/trpg_db.env"
DATA_DIR="/srv/trpg-pgdata"
IMAGE="pgvector/pgvector:pg17"     # 本機已有此映像（mem0 在用）⇒ 不必重新拉
NAME="trpg-postgres"

[ -f "$ENVF" ] || { echo "✗ 找不到 $ENVF（先跑 tools/trpg_db_init.py 生成）" >&2; exit 1; }
# shellcheck disable=SC1090
. "$ENVF"

case "${1:-up}" in
  status)
    docker ps -a --filter "name=^/${NAME}$" --format '{{.Names}}|{{.Image}}|{{.Status}}|{{.Ports}}'
    exit 0;;
  psql)
    exec docker exec -it "$NAME" psql -U "$TRPG_DB_USER" -d "$TRPG_DB_NAME";;
esac

if docker ps -a --format '{{.Names}}' | grep -qx "$NAME"; then
  docker start "$NAME" >/dev/null && echo "✔ 已啟動既有容器 $NAME"
else
  mkdir -p "$DATA_DIR"
  chown 999:999 "$DATA_DIR"
  docker run -d --name "$NAME" --restart unless-stopped \
    -e POSTGRES_USER="$TRPG_DB_USER" -e POSTGRES_PASSWORD="$TRPG_DB_PASSWORD" \
    -e POSTGRES_DB="$TRPG_DB_NAME" \
    -p "127.0.0.1:${TRPG_DB_PORT}:5432" \
    -v "$DATA_DIR:/var/lib/postgresql/data" \
    "$IMAGE" >/dev/null
  echo "✔ 已建立並啟動 $NAME（127.0.0.1:${TRPG_DB_PORT}，資料在 ${DATA_DIR}）"
fi

# 等它真的能吃連線（不盲等固定秒數）
for i in $(seq 1 30); do
  if docker exec "$NAME" pg_isready -U "$TRPG_DB_USER" -d "$TRPG_DB_NAME" >/dev/null 2>&1; then
    echo "✔ DB 就緒（${i}s）"; exit 0
  fi
  sleep 1
done
echo "✗ DB 30 秒還沒就緒——看 docker logs $NAME" >&2; exit 1
