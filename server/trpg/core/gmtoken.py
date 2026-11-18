"""**GM 控制鍵**（`config.auth.gm_token`）的比對——唯一一份。

2026-10-24（review R1）：`5174bf9` 為了「缺 `auth` 段不要 500」，把每個比較點都寫成
`(cfg.get("auth") or {}).get("gm_token") or ""`。副作用是**配置沒設 token 時，等於拿空字串跟
請求比**⇒ 只要請求帶空值（body `token: ""`／`?gm_token=`）就放行：

* `GET /api/state?gm_token=` ⇒ GM 全視圖（含別席私密）；
* `POST /api/gm {token:""}` ⇒ 以 GM 身分發私訊／暫停／結算；
* MCP `table_gm {token:""}` ⇒ 同上（實測真的把桌暫停了）。

修法：語意收成這裡一支——**配置值為空 ⇒ 一律 False（fail-closed）**；非空才比對。
所有入口（GET／POST／MCP／GraphQL 的落點都在 `/api/*` handler）都必須走 `gm_token_ok()`，
不要再各自寫比較式。

⚠ 這個模組刻意放在 `trpg/core`（不 import 任何 api 層）：`api/web` 與 `api/mcp_api` 都要用，
放在其中一邊會拉出循環 import。
"""
from __future__ import annotations

import hmac
from typing import Any, Mapping, Optional


def secret_eq(a: Any, b: Any) -> bool:
    """憑證比對（**吃得下非 ASCII 的憑證**）。

    `hmac.compare_digest` 只吃 ASCII 字串 ⇒ 憑證裡有 CJK（`config.auth.gm_token` 填了中文、
    或 `watch.read_token` 帶中文）時它會丟 `TypeError: comparing strings with non-ASCII characters`
    ——2026-10-07 實測：**劇本 config 的 gm_token 是中文占位符時，`/api/state` 直接炸掉**
    （連沒帶 token 的請求都會踩到，因為是拿空字串跟設定值比）。這裡退化成字串相等
    （同一台機器、同一把 token，沒有時序攻擊的實務風險）。
    """
    sa, sb = str(a or ""), str(b or "")
    try:
        return hmac.compare_digest(sa, sb)
    except TypeError:                       # 非 ASCII ⇒ compare_digest 不吃
        return sa == sb


def configured_token(cfg: Optional[Mapping[str, Any]]) -> str:
    """這一張桌設定的 GM 控制鍵（沒設／空值一律回 `""`）。"""
    try:
        return str(((cfg or {}).get("auth") or {}).get("gm_token") or "")
    except Exception:                       # noqa: BLE001 —— cfg 不是 mapping 也別炸
        return ""


def gm_token_ok(cfg: Optional[Mapping[str, Any]], given: Any) -> bool:
    """請求帶來的值**是不是這張桌的 GM 控制鍵**。

    ★ 核心紀律：**配置沒有 token ⇒ 永遠 False**。從前那句 `or ""` 讓「沒設定」與
    「帶空字串」被比成相等，GM 閘整條 fail-open；這裡把「沒設定」釘成失敗關閉。
    """
    want = configured_token(cfg)
    if not want:
        return False
    got = "" if given is None else str(given)
    if not got:
        return False
    return secret_eq(got, want)
