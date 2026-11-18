"""wake（套件出口：實作已按功用拆到同目錄各模組）。

除了本模組定義的函式，也保留原本 `wake.py` 轉出的 kick 常數／函式——
`service.py` 等呼叫端歷史上是 `from wake import GM_CLI, …`。
"""

from trpg.kick.sender import GM_CLI, GM_KICK_TOKEN_FILE, GM_KICK_URL, send_kick_to_gm  # noqa: F401
from trpg.core import layout as layout  # noqa: F401
from trpg.engine.projection import trim_secrets as _trim_secrets  # noqa: F401

from .keys import round_kick_key, round_pacing_line
from .kicks import maybe_kick_ending, maybe_kick_round
from .mixin import KickMixin

__all__ = ["round_kick_key", "round_pacing_line",
           "maybe_kick_round", "maybe_kick_ending", "KickMixin",
           "GM_CLI", "GM_KICK_TOKEN_FILE", "GM_KICK_URL", "send_kick_to_gm"]
