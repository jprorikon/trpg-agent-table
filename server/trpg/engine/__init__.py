"""L0 內核（裁決／事件／回放）＋ L2 投影／L4 句庫。

實作放在子模組；這裡**只做延遲轉出**（PEP 562），避免 `trpg.engine` 被匯入時
連帶把整個內核跑一遍——L1 的 `scenario`／`conditions` 只想要 L4 的句庫，
不該因此和 `packs` 形成循環匯入。

    from trpg.engine import Table          # 內核
    from trpg.engine import projection     # L2（子模組，不會拉進內核）
"""
import importlib

_LAZY = {
    "Table": ".table",
    "MemoryStore": ".table",
    "VIEW_LOG_TYPES": ".table",
    "normalize_actions": ".table",
    "action_modes": ".table",
    "visible_actions": ".table",
}


def __getattr__(name):
    mod = _LAZY.get(name)
    if mod:
        return getattr(importlib.import_module(mod, __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(list(globals()) + list(_LAZY))
