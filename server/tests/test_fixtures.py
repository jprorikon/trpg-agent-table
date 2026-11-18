#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**共用夾具件的機械驗收**——`test_support.py`。

    python3 test_fixtures.py

為什麼要有它：夾具的**形狀**決定了它擋不擋得住 bug（2026-10-07：把 `packs/` 抄進桌目錄 ⇒ 掩蓋了
「工具目錄永遠是空的」）。這支把「形狀對不對」變成**可執行**的斷言，還帶一條**負對照**證明它有牙齒。
"""

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo 根
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "vendor"))

from trpg.accounts import account_store as account_store                             # noqa: E402
from trpg.core import layout as layout                                    # noqa: E402
from trpg.api import mcp_api as mcp_api                                   # noqa: E402
from trpg.core import store as store                                     # noqa: E402
from trpg.service import table_factory as table_factory                             # noqa: E402
import test_support as TS                        # noqa: E402

OK, FAIL = [], []


def check(label, cond, detail=""):
    (OK if cond else FAIL).append(label)
    print(f"{'  PASS' if cond else '  FAIL'}  {label}" + (f"   〔{detail}〕" if detail and not cond else ""))


def main():
    root, home, cfg = TS.mk_table()
    _scn = layout.def_dir_of(home)

    print("\n== X1 形狀＝正式形狀（桌目錄 vs 劇本層）==")
    _sp = layout._split_state_home(home)
    check("X1a 桌目錄在 `state/tables/<模組>/<劇本>/<桌ID>/`（2026-10-19 起）",
          bool(_sp) and os.path.relpath(home, os.path.join(root, "state", "tables"))
          == os.path.join("hyakki_tower", "main", os.path.basename(home)), home)
    check("X1a2 模組目錄裡**沒有** `tables/`（桌的實體不住內容目錄）",
          not os.path.exists(os.path.join(os.path.dirname(_scn), layout.TABLES_DIR)),
          os.path.dirname(_scn))
    check("X1b 定義在劇本層（seats／board／scenario 都不在桌目錄）",
          all(os.path.exists(os.path.join(_scn, f)) for f in ("seats.json", "board.json", "scenario.json"))
          and not any(os.path.exists(os.path.join(home, f)) for f in ("seats.json", "board.json")), _scn)
    check("X1c `packs/` 在劇本層（不在桌目錄）",
          os.path.isdir(os.path.join(_scn, "packs")) and not os.path.isdir(os.path.join(home, "packs")), _scn)
    check("X1d 桌目錄裡只有 config（＋可選 runs）",
          sorted(os.listdir(home)) == ["config.json"], str(sorted(os.listdir(home))))

    print("\n== X2 定位／組裝 ==")
    rows = layout.scan(root)
    check("X2a `layout.scan` 找得到這一桌且 def_dir 指對",
          len(rows) == 1 and rows[0]["def_dir"] == _scn and rows[0]["home"] == home, str(rows))
    svc = table_factory.build(home, cfg, log=lambda *_a: None)
    check("X2b `table_factory.build(桌目錄)` 起得來（引擎讀定義層、寫桌目錄）",
          svc is not None and svc.def_dir == _scn and svc.table.home == _scn)

    print("\n== X3 夾具不碰線上（DB 後端被拔掉）==")
    check("X3 `account_store.configured(cfg)` 是 False ⇒ 不會寫到線上那顆 Postgres",
          not account_store.configured(cfg), str((cfg.get("auth") or {}).get("db_env"))[:40])

    print("\n== X4 **工具目錄非空**（上一輪被錯形狀掩蓋的那條）==")
    check("X4 svc.tools 是 7 格（＝`mcp_api.tools_for(cfg, def_dir)`）",
          len(svc.tools) == 7 and [t["name"] for t in svc.tools] == [t["name"] for t in mcp_api.tools_for(cfg, _scn)],
          str(len(svc.tools)))

    print("\n== X5 負對照：錯形狀為什麼會掩蓋 bug ==")
    check("X5a 正式形狀下 `def_dir_of(桌目錄) != 桌目錄` ⇒ 傳錯就一定壞（有牙齒）",
          layout.def_dir_of(home) != home, home)
    check("X5b 舊的「扁平形狀」（定義＝桌目錄）下 `def_dir_of(h) == h` ⇒ `tools_for(h)` 照樣過",
          layout.def_dir_of(_scn) == _scn, _scn)

    print("\n== X6 收尾／帶入某一局（夾具與線上桌解耦後）==")
    # 2026-10-07：清掉線上那張臨時桌（`hyakki_tower`）時才發現——夾具以前**依賴某一張桌還活著**，
    # 桌一沒就整批測試紅。現在 `mk_table(with_run=True)` 的語意＝「有現役桌就抄那一局；
    # 沒有就照常造一張空桌」。這裡直接拿**自造的來源桌**驗「抄一局」這支機制本身（不碰線上）。
    src_root = tempfile.mkdtemp(prefix="trpg_src_")
    src_home = os.path.join(src_root, "src")
    os.makedirs(os.path.join(src_home, "runs", "rX"), exist_ok=True)
    with open(os.path.join(src_home, "runs", "current"), "w", encoding="utf-8") as fh:
        fh.write("rX")
    with open(store.events_path(src_home, "rX"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"seq": 1, "type": "table_created"}, ensure_ascii=False) + "\n")
    root2, home2, _cfg2 = TS.mk_table(with_run=True)
    check("X6a 沒有現役桌時 `mk_table(with_run=True)` 不炸（優雅退化成空桌）",
          bool(layout.read_config(home2)), home2)
    _sid = store.copy_current_run(src_home, home2)
    check("X6b `store.copy_current_run` 抄得到那一局（指標指過去、events 有檔）",
          _sid == "rX" and os.path.isfile(store.events_path(home2, "rX")), _sid)
    shutil.rmtree(src_root, ignore_errors=True)
    TS.rm_root(root)
    TS.rm_root(root2)
    check("X6c `rm_root` 收得掉", not os.path.exists(root) and not os.path.exists(root2))

    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
