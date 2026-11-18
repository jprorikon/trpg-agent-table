"""packs（套件出口：實作已按功用拆到同目錄各模組）。

外部呼叫端一律 `from trpg.rules import packs as packs`，所以這裡把原本住在
`packs.py` 的公開面（含從 `pack_kernel` 轉出的池子／核對）**全部轉出**。
"""

from trpg.engine import presentation as presentation
from trpg.rules.pack_kernel import (                                        # noqa: F401
    IMPL_SOURCES, KERNEL_EFFECTS, KERNEL_MODES, KERNEL_SECTIONS, KERNEL_SIGNALS,
    KERNEL_STATE_KEYS, POOLS, PackError, effective_action_entries, log_types_for, verify_impl,
)

from .models import Pack, PackView
from .shared import _canon, _as_list
from .io import module_packs_dir, seed_module_packs, read_text_file, text_bank_for, registry
from .merge import (apply_defaults, resolve, _deep_fill, _merge_defaults, _union,
                    _merge_actions, _merge_params, _merge_texts, _topo)
from .common import PACK_FILE, TEXT_FILE, PACKS_DIR, PROVIDES_KEYS

__all__ = [
    "PACK_FILE", "TEXT_FILE", "PACKS_DIR", "PROVIDES_KEYS", "IMPL_SOURCES",
    "KERNEL_EFFECTS", "KERNEL_MODES", "KERNEL_SECTIONS", "KERNEL_SIGNALS",
    "KERNEL_STATE_KEYS", "POOLS", "PackError", "Pack", "PackView",
    "module_packs_dir", "seed_module_packs", "registry", "resolve",
    "read_text_file", "text_bank_for", "apply_defaults",
    "effective_action_entries", "log_types_for", "verify_impl",
]
