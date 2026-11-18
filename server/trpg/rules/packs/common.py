"""common（自 __init__.py 拆出）"""

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

PACK_FILE = "pack.json"

TEXT_FILE = "text.json"

PACKS_DIR = "packs"

PROVIDES_KEYS = ("actions", "effects", "events", "text_keys", "state_keys", "signals",
                 "tables", "mode", "defaults")

__all__ = ["PACK_FILE", "TEXT_FILE", "PACKS_DIR", "PROVIDES_KEYS", "IMPL_SOURCES",
           "KERNEL_EFFECTS", "KERNEL_MODES", "KERNEL_SECTIONS", "KERNEL_SIGNALS",
           "KERNEL_STATE_KEYS",
           "POOLS", "PackError", "Pack", "PackView", "module_packs_dir", "seed_module_packs", "registry", "resolve",
           "read_text_file", "text_bank_for",
           "apply_defaults", "verify_impl", "effective_action_entries", "log_types_for"]
