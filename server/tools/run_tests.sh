#!/bin/bash
# TRPG 測試的**唯一入口**（`tools/run_all_tests.sh` 也轉來這支）。
#
#   bash tools/run_tests.sh                     # 全部（tests/test_*.py）
#   bash tools/run_tests.sh test_skills         # 指定檔名／片段
#   bash tools/run_tests.sh tests/test_skills.py test_board.py
#   TRPG_PY=/usr/bin/python3.11 bash tools/run_tests.sh   # 換解釋器
#
# 2026-10-24（review R3）：從前這裡寫死部署機的工作目錄與那支 python
# ⇒ 換一台機器／換一個 clone 就整支不動（還可能悄悄跑到別人的樹上）。
# 現在：
#   * 路徑一律由**這支腳本自己的位置**推出來（`$HERE`），在哪個 clone 就測哪個 clone；
#   * 解釋器 `TRPG_PY` 可覆寫，否則依序找 部署機那支 → python3.11 → python3；
#   * 退出碼：0＝全綠、1＝有測試紅、**2＝環境前置不符**（找不到 python／版本 <3.10）
#     ——`tools/verify_clean_clone.sh` 靠這個分辨「樹有問題」與「這台機器不夠格」；
#   * `TRPG_LEGACY_REST=1` 在這裡 export（`/api/*` 2026-10-07 對外退役，生產不設 ⇒ 外部一律 410；
#     測試驗的是引擎的 HTTP 契約，所以測試期間放行舊門）。
#
# 前置：`modules/` 不在版控裡（模組各自一個 repo）——
#   `sh deploy/modules_up.sh`（第一次會 clone，之後 pull）。
set -u
HERE=$(cd "$(dirname "$0")/.." && pwd) || exit 1
cd "$HERE" || exit 1
export TRPG_LEGACY_REST=1

# ── 解釋器
if [ -z "${TRPG_PY:-}" ]; then
  for c in python3 \
           python3.11 python3 python; do
    if command -v "$c" >/dev/null 2>&1; then TRPG_PY=$c; break; fi
  done
fi
PY=${TRPG_PY:-}
if [ -z "$PY" ] || ! "$PY" -c 'import sys' >/dev/null 2>&1; then
  echo "找不到可用的 python（可用 TRPG_PY=/path/to/python3.11 指定）" >&2
  exit 2
fi
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  # ⚠ 雙引號裡別放反引號：shell 會把裡面當命令執行（從前這行會噴 `X: command not found`）
  echo "⚠ $PY 是 $("$PY" -V 2>&1)；這棵樹要 3.10+（X | None／typing.TypeGuard）。" >&2
  echo "  換一支：TRPG_PY=/path/to/python3.11 bash tools/run_tests.sh" >&2
  exit 2
fi

# ── 要跑哪些檔
if [ "$#" -eq 0 ]; then
  FILES=$(ls tests/test_*.py 2>/dev/null)
else
  FILES=""
  for want in "$@"; do
    for f in tests/test_*.py; do
      n=$(basename "$f" .py)
      case "$f|$n" in
        *"$want"*) case " $FILES " in *" $f "*) ;; *) FILES="$FILES $f" ;; esac ;;
      esac
    done
  done
  if [ -z "${FILES// /}" ]; then
    echo "沒有測試符合：$*（有的：$(ls tests/test_*.py | xargs -n1 basename | sed 's/\.py$//' | tr '\n' ' ')）" >&2
    exit 1
  fi
fi

[ -d modules ] || echo "⚠ 沒有 modules/（模組不在版控）——先跑 sh deploy/modules_up.sh，否則用到劇本的測試會紅" >&2
echo "== 測 $HERE（$(echo $FILES | wc -w) 支；解釋器 $("$PY" -V 2>&1)）"
PASS=0
FAIL=0
RED=""
for f in $FILES; do
  [ -f "$f" ] || continue
  out=$(timeout 300 "$PY" "$f" 2>&1)
  code=$?
  sum=$(printf '%s\n' "$out" | grep -E '結果|=====|PASS /' | tail -1)
  echo "$(basename "$f" .py)  exit=$code  | $sum"
  if [ "$code" -eq 0 ]; then
    PASS=$((PASS+1))
  else
    FAIL=$((FAIL+1))
    RED="$RED $(basename "$f" .py)"
    printf '%s\n' "$out" | grep -E '^  FAIL' | head -5
  fi
done
echo "===== 綠 $PASS / 紅 $FAIL ====="
[ -n "$RED" ] && echo "紅檔:$RED"
[ "$FAIL" -eq 0 ]
