#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**測試夾具的共用零件**——一律**唯讀地**從現役桌取樣，且形狀**跟正式的一模一樣**。

## 為什麼要有這一支（2026-10-07 兩次教訓換來的）

`tables/<桌ID>/`（**桌目錄**：`config.json`／`runs/`）與劇本層（**定義**：`seats.json`／`board.json`／
`scenario.json`／`packs/`）分開之後：

1. **~25 支測試各寫一次「從現役桌抄檔」** ⇒ 形狀一變就全部要改一次（那天炸了 25 支）。
2. **抄錯形狀會掩蓋 bug**：有夾具把 `packs/` 抄進桌目錄 ⇒ `mcp_api.tools_for(cfg, home)` 用桌目錄
   找包也能過 ⇒「**工具目錄永遠是空的**」那個真 bug 在夾具裡看不出來（是 live 冒烟才逮到）。

⇒ 這裡提供**唯一一份**取樣器，形狀固定成正式的那個（定義在劇本層、桌目錄只有 config／runs）。

## 用法

    from test_support import mk_table           # 造一張臨時桌（正式形狀）
    root, home, cfg = mk_table()                # root=臨時 repo 根；home=桌目錄；cfg=設定
    ...
    rm_root(root)                               # 收尾（或交給 atexit 自動清）

`mk_table(module="other_world")` 換來源模組；`mk_table(with_run=True)` 連現役那一局一起抄。
"""

import atexit
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo 根
sys.path.insert(0, HERE)

from trpg.core import layout as layout                                    # noqa: E402
from trpg.core import store as store                                     # noqa: E402

_TMP: list = []                                  # 造過的臨時樹（atexit 統一收）


def live_table(name: str = "hyakki_tower") -> str:
    """現役那一桌的**桌目錄**（找不到就大聲死——不要讓測試偷偷跑在錯的東西上）。"""
    return layout.must_locate(HERE, name)


def fixture_source(module: str = "hyakki_tower", scenario: str = "main") -> "tuple[str, dict]":
    """夾具來源：**有現役桌就用它**（定義目錄 ＋ 那張桌的 `config.json`），
    沒有就退「repo 定義層 ＋ `config.example.json`」。

    為什麼要有它（2026-10-07）：清掉線上那張臨時桌之後，`layout.must_locate` 直接 `SystemExit`
    ⇒ `test_hub`／`test_lobby` 整支陪葬。**夾具與線上桌解耦**之後，測試才不會因為「線上有沒有桌」紅。
    回 `(定義目錄, config dict)`。
    """
    src = live_table_or_none(module)
    if src:
        return def_dir(src), json.loads(json.dumps(layout.read_config(src) or {}))
    d, cfgp = repo_def_dir(module, scenario)
    with open(cfgp, encoding="utf-8") as fh:
        return d, json.loads(fh.read() or "{}")


def live_table_or_none(name: str = "hyakki_tower") -> "str | None":
    """現役那一桌（沒有就回 `None`）。

    為什麼要有：夾具**不該依賴某一張桌還活著**（2026-10-07 清掉 hyakki_tower 那張臨時桌時，
    整個 `mk_table` 就找不到來源而炸掉 ⇒ 所有用到夾具的測試全紅）。沒有現役桌時，
    就退回「repo 裡的模組／劇本定義層 ＋ `config.example.json`」當底稿——**測試與線上桌解耦**。
    """
    row = layout.locate(HERE, name)        # 用會回 None 的那支：`must_locate` 是 SystemExit，
    return row["home"] if row else None    # `except Exception` 接不住它（2026-10-07 踩到）


def repo_def_dir(module: str, scenario: str = "main") -> "tuple[str, str]":
    """repo 裡的定義層（沒有現役桌時用）：回 `(劇本層目錄, config 底稿路徑)`。

    劇本名對不上就用該模組底下的第一個劇本（`scenario` 只決定臨時樹的路徑，不決定內容）。
    """
    scn_root = os.path.join(HERE, layout.MODULES_DIR, module, layout.SCENARIOS_DIR)
    cands = sorted(d for d in (os.listdir(scn_root) if os.path.isdir(scn_root) else [])
                   if os.path.isdir(os.path.join(scn_root, d)))
    if not cands:
        raise RuntimeError(f"找不到模組 {module} 的劇本目錄：{scn_root}")
    scn = scenario if scenario in cands else cands[0]
    d = os.path.join(scn_root, scn)
    return d, os.path.join(d, "config.example.json")


def def_dir(src: str) -> str:
    """那一桌的**定義目錄**（劇本層）——`board.json`／`seats.json`／`packs/` 在這裡。"""
    return layout.def_dir_of(src)


def mk_table(module: str = "hyakki_tower", *, scenario: str = "main", table_id: str = None,
             with_run: bool = False, require_account: bool = False, keep: bool = False) -> tuple:
    """造一張**正式形狀**的臨時桌，回 `(root, home, cfg)`。

    形狀：`<root>/modules/<模組>/scenarios/<劇本>/`＝定義層（抄現役的 board／seats／scenario／goals／
    clues／`packs/` 與模組層的 `module.json`／`characters.json`／`packs/`）；
    `<root>/state/tables/<模組>/<劇本>/<桌ID>/`＝桌目錄（**2026-10-19 起桌的實體搬出模組目錄**），
    只放 `config.json`（＋`with_run` 時抄現役那一局的 `runs/`）。

    `config.json` 以現役的為底稿，但**拔掉 DB 後端**（`db_env`）並指向自己的 JSON registry
    ⇒ 夾具**永遠不會寫到線上那顆 Postgres**（這條有閘：`test_fixtures` X3）。
    """
    src = live_table_or_none(module) or ""
    if src:
        _def = def_dir(src)
        _cfg_base = src
    else:
        _def, _cfg_base = repo_def_dir(module, scenario)      # 沒有現役桌 ⇒ 退回 repo 定義層
    root = tempfile.mkdtemp(prefix="trpg_fix_")
    if not keep:
        _TMP.append(root)
    mod_dir = os.path.join(root, layout.MODULES_DIR, module)
    scn_dir = os.path.join(mod_dir, layout.SCENARIOS_DIR, scenario)
    os.makedirs(scn_dir, exist_ok=True)
    tid = str(table_id or (os.path.basename(src) or module))
    if not os.path.exists(src):
        tid = module
    home = layout.table_home(root, module, scenario, tid)
    os.makedirs(home, exist_ok=True)
    # ① 定義層（只抄檔案；桌目錄不收）
    for f in sorted(os.listdir(_def)):
        p = os.path.join(_def, f)
        if f in (layout.TABLES_DIR, "runs", "data", ".secrets") or f.startswith("data_"):
            continue
        if os.path.isfile(p) and not f.startswith(("config.json.bak", "accounts.json")):
            shutil.copy(p, os.path.join(scn_dir, f))
    _pk = os.path.join(_def, "packs")
    if os.path.isdir(_pk) and not os.path.exists(os.path.join(scn_dir, "packs")):
        shutil.copytree(_pk, os.path.join(scn_dir, "packs"))
    # ② 模組層（世界層：命名／角色表）——來源是**現役模組目錄**（劇本層再上一層）
    _live_mod = os.path.dirname(os.path.dirname(_def))
    for f in ("module.json", "characters.json", "WORLD.md"):
        q = os.path.join(_live_mod, f)
        if os.path.isfile(q):
            shutil.copy(q, os.path.join(mod_dir, f))
    _mpk = os.path.join(_live_mod, "packs")              # 模組層的包（作品內容包，例 rules.core）
    if os.path.isdir(_mpk) and not os.path.exists(os.path.join(mod_dir, "packs")):
        shutil.copytree(_mpk, os.path.join(mod_dir, "packs"))
    # ③ repo 根的標記（`layout.repo_root()` 靠它上溯）
    open(os.path.join(root, "hub_server.py"), "w", encoding="utf-8").close()
    # ④ 桌目錄：config（拔 DB 後端）＋（可選）現役那一局
    if src:
        cfg = json.loads(json.dumps(layout.read_config(src) or {}))
    else:
        with open(_cfg_base, encoding="utf-8") as fh:            # 沒有現役桌 ⇒ 用 config.example.json
            cfg = json.loads(fh.read() or "{}")
    cfg["table_id"] = tid
    cfg["port"] = 0
    a = dict(cfg.get("auth") or {})
    for k in ("db", "db_env", "db_env_doc", "dbs", "dsn"):
        a.pop(k, None)
    a["accounts_file"] = os.path.join(home, "accounts.json")
    a["require_account"] = bool(require_account)
    # ★ GM 控制鍵：現役桌的 config 有（hub 建的會生成），但**沒有現役桌**的模組（例 hyakki_tower）
    #   退回 `config.example.json`，那裡是 `REPLACE_ME` 或根本沒有 ⇒ 測試要有一個可用的控制鍵，
    #   否則 `?gm_token=` 那條路一走就 KeyError（2026-10-20 之前一直靠現役桌的存在才沒炸）。
    if not a.get("gm_token") or "REPLACE_ME" in str(a.get("gm_token")):
        a["gm_token"] = "test-gm-token"
    cfg["auth"] = a
    with open(os.path.join(home, "config.json"), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=1)
    if with_run and src:
        store.copy_current_run(src, home)
    return root, home, cfg


def rm_root(root: str) -> None:
    """收掉臨時樹（不在清單裡也照收）。"""
    shutil.rmtree(root, ignore_errors=True)
    for i, r in enumerate(list(_TMP)):
        if r == root:
            _TMP.pop(i)


def _cleanup() -> None:
    for r in list(_TMP):
        shutil.rmtree(r, ignore_errors=True)


atexit.register(_cleanup)


# ---------------------------------------------------------------------------
# 靜態閘用的原始碼取樣（2026-10 模組化之後：舊檔名 → 新套件位置）
# ---------------------------------------------------------------------------
_SOURCE_MAP = {
    "engine.py": "trpg/engine",
    "pacing.py": "trpg/core/pacing.py",
    "projection.py": "trpg/engine/projection",
    "presentation.py": "trpg/engine/presentation.py",
    "director.py": "trpg/engine/director.py",
    "board.py": "trpg/rules/board",
    "semic.py": "trpg/rules/semic",
    "packs.py": "trpg/rules/packs",
    "pack_kernel.py": "trpg/rules/pack_kernel.py",
    "scenario.py": "trpg/rules/scenario",
    "expr.py": "trpg/rules/expr.py",
    "conditions.py": "trpg/rules/conditions.py",
    "service.py": "trpg/service/service.py",
    "wake.py": "trpg/service/wake",
    "kick_sender.py": "trpg/kick/sender.py",
    "kick_receiver.py": "trpg/kick/receiver",
    "web_api.py": "trpg/api/web",
    "graphql_api.py": "trpg/api/graphql",
    "mcp_api.py": "trpg/api/mcp_api",
    "hub_server.py": "hub_server.py",
    "lobby_server.py": "trpg/api/lobby",
    "watch_server.py": "trpg/api/watch.py",
    "auth.py": "trpg/accounts/auth",
    "accounts_db.py": "trpg/accounts/db",
}


def source_text(name: str) -> str:
    """回一份「舊檔名」對應的原始碼（拆成套件時把同目錄的 .py 串起來）。

    `from __future__` 在串接後只能出現在最前面 ⇒ 其餘檔案的這一行先拿掉
    （只給靜態閘讀，不執行）。
    """
    rel = _SOURCE_MAP.get(name, name)
    p = os.path.join(HERE, rel)
    if not os.path.isdir(p):
        return open(p, encoding="utf-8").read()
    chunks = []
    skip = {"presentation.py", "director.py"} if name == "engine.py" else set()
    for fn in sorted(os.listdir(p)):
        if not fn.endswith(".py") or fn == "__init__.py" or fn in skip:
            continue
        text = open(os.path.join(p, fn), encoding="utf-8").read()
        text = text.replace("from __future__ import annotations\n", "")
        chunks.append(text)
    return "\n\n".join(chunks)
