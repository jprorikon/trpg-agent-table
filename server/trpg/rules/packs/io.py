"""io（自 __init__.py 拆出）"""

from __future__ import annotations
import copy
import hashlib
import json
import os
from typing import Any, Dict, List, Optional, Tuple
from trpg.engine import presentation as presentation      # L1→L4 只讀句庫（`scenario.py` 也是這樣拿句庫鍵的）
from trpg.paths import ROOT
from trpg.rules.pack_kernel import (                                          # noqa: E402
    IMPL_SOURCES, KERNEL_EFFECTS, KERNEL_MODES, KERNEL_SECTIONS, KERNEL_SIGNALS,
    KERNEL_STATE_KEYS, POOLS, PackError, effective_action_entries, log_types_for, verify_impl,
)

from .models import Pack
from .common import PACKS_DIR, PACK_FILE, TEXT_FILE

def module_packs_dir(home: Optional[str]) -> str:
    """劇本目錄（`<模組>/scenarios/<劇本>`）⇒ 它所屬模組的 `<模組>/packs/`；看不出模組 ⇒ 空字串。

    為什麼要這一層：**作品的內容包**（例：`rules.core` 的技能／道具／星等表＝某一部作品的語彙）
    屬於模組 repo，不屬於引擎 repo；同一模組的每一齣劇本共用一份，不必在每個劇本的 `packs/` 各抄一份。
    只靠路徑形狀推（不讀 root）⇒ 不在 `scenarios/` 底下的臨時桌（測試、`/tmp` 探針）就沒有這一層——
    要用模組包的臨時桌，自己把 `<模組>/packs/` 複製進它的 `packs/`（`check_module.sh` 就是這樣做）。
    """
    if not home:
        return ""
    p = os.path.normpath(os.path.abspath(home))
    if os.path.basename(os.path.dirname(p)) != "scenarios":
        return ""
    return os.path.join(os.path.dirname(os.path.dirname(p)), PACKS_DIR)

def seed_module_packs(def_dir: str, home: str) -> None:
    """把劇本 `def_dir` 所屬模組的 `<模組>/packs/*` 複製進臨時桌 `home` 的 `packs/`（已有的同名包不覆蓋）。

    給**不在 `scenarios/` 底下**的臨時桌（測試夾具、`/tmp` 探針）用：它們的路徑推不出模組，
    `registry()` 看不到模組層；先把模組包放進它的劇本層，解析結果就跟真桌一樣。
    模組的句庫（`<模組>/text.json`）同理併進臨時桌的 `text.json`（臨時桌自己寫的鍵贏＝劇本層蓋模組層）。
    """
    import shutil                                                       # noqa: PLC0415
    src = module_packs_dir(def_dir)
    if not src:
        return
    if os.path.isdir(src):
        dst = os.path.join(home, PACKS_DIR)
        for pid in sorted(os.listdir(src)):
            if os.path.isdir(os.path.join(src, pid)) and not os.path.exists(os.path.join(dst, pid)):
                shutil.copytree(os.path.join(src, pid), os.path.join(dst, pid))
    mod_bank = read_text_file(os.path.join(os.path.dirname(src), TEXT_FILE))
    if mod_bank:
        own = os.path.join(home, TEXT_FILE)
        merged = {**mod_bank, **read_text_file(own)}
        with open(own, "w", encoding="utf-8") as fh:
            json.dump(merged, fh, ensure_ascii=False, indent=2)

def read_text_file(path: str) -> Dict[str, str]:
    """讀一份句庫檔（`{key: 文案}`；沒有＝空；`_` 開頭的鍵是註記，不進句庫）。最外層不是物件 ⇒ 當場叫。"""
    if not path or not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        bank = json.load(fh)
    if not isinstance(bank, dict):
        raise PackError(f"{path}: 最外層要是物件（key → 文案）")
    return {str(k): str(v) for k, v in bank.items() if not str(k).startswith("_")}

def text_bank_for(view: Optional["PackView"], home: Optional[str]) -> Dict[str, str]:
    """**這一桌的句庫**＝內核中性預設 ← 啟用包的 `text.json` ← `<模組>/text.json` ← `<劇本>/text.json`。

    一張桌一本（`hub_server.py` 一個行程裝 N 張桌，不能併進 `presentation.TEXT` 這本全域字典）。
    模組／劇本層的句庫是**措辭**，不是機制 ⇒ 不進 `packs_hash`、也不必在 `provides.text_keys` 宣告；
    鍵沿用內核那一套（`presentation.TEXT`）與動作目錄的 `action.<id>.label`／`action.<id>.desc`。
    """
    mdir = os.path.dirname(module_packs_dir(home)) if home else ""
    return presentation.make_bank(
        (view.texts if view is not None else None),
        read_text_file(os.path.join(mdir, TEXT_FILE)) if mdir else None,
        read_text_file(os.path.join(home, TEXT_FILE)) if home else None)

def registry(home: Optional[str] = None, root: Optional[str] = None) -> Dict[str, Pack]:
    """掃三層 `packs/`：引擎層（repo）→ 模組層（`<模組>/packs/`）→ 劇本層（劇本目錄）；同名時後者覆蓋前者。"""
    root = root or ROOT
    out: Dict[str, Pack] = {}
    for base in (os.path.join(root, PACKS_DIR),
                 module_packs_dir(home),
                 os.path.join(home, PACKS_DIR) if home else ""):
        if not base or not os.path.isdir(base):
            continue
        for pid in sorted(os.listdir(base)):
            p = os.path.join(base, pid, PACK_FILE)
            if not os.path.exists(p):
                continue
            with open(p, "r", encoding="utf-8") as fh:
                out[pid] = Pack(pid, json.load(fh), path=p)
    return out
