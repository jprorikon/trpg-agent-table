#!/bin/bash
# hyakki_tower / sannenme_no_onibarai 自检（模组自己的测试脚本；引擎单测仍走 trpg/tools/run_tests.sh）
set -u
cd "$(dirname "$0")/.." || exit 1
PY=python3
rc=0
echo "== 1/3 素材索引"
$PY tools/assets_index.py check || rc=1
echo "== 2/3 模组冒烟"
[ -f tools/smoke.py ] && ($PY tools/smoke.py || rc=1)
echo "== 3/3 真打一遍（move 四条路径＋用词）"
$PY ../../tools/module_playtest.py sannenme_no_onibarai || rc=1
exit $rc
