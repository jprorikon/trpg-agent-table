#!/usr/bin/env python3
"""**L6｜POST 的 account 認證政策**（GraphQL 與 REST 共用同一份）——`post_auth()`。

從 `web_api.py` 的 `do_POST` 原樣搬出來（2026-10-20 補齊重構漏掉的檔案）：
政策只有一份，`HandlerPostMixin.do_POST` 與 GraphQL 的 `_Shim` 都走這裡。

回傳值：`True` ＝可以繼續；`False` ＝**已經回過 403**，呼叫端直接 return（不要再回第二次）。
"""
from __future__ import annotations

from typing import Any, Dict

from trpg.accounts import auth

from .base import gm_token_ok, _accounts_of

_AUTH_PATHS = ("/api/act", "/api/say", "/api/tell", "/api/pick_char")


def post_auth(self: Any, body: Dict[str, Any], svc: Any) -> bool:
    path = self.path.split("?")[0]          # 與 do_POST 同一個取法
    if path not in _AUTH_PATHS:
        return True                          # 不是寫入端點：不關這一支的事（legacy 一字不變）

    # ── account 認證（★account 層）：有帶有效 token 就**以 token 的席位為準**；
    #    `config.auth.require_account` 開了 ⇒ 沒 token／冒充別人 ⇒ 403。
    #    沒有 accounts.json 的桌 ⇒ 這一整段 no-op（legacy 一字不變）。
    _acct = _accounts_of(svc)
    _req = bool((svc.cfg.get("auth") or {}).get("require_account"))
    _tok = auth.token_from_request(headers=self.headers, body=body)

    # ★ **GM 那條路**（`config.auth.gm_token`＝這一桌的**控制鍵**）：與 GET 一樣
    #   **不受 account 認證影響**——`tools/gm.py`、GM 面板握的都是這把，不是 account token。
    #   ⚠ 2026-10-07 修：require_account 的桌上，這些寫入端點以前只認 account token ⇒
    #   **GM 的 CLI 一律 403**（示範模組 的 GM 走 MCP 所以沒被發現；hyakki 走 CLI 就中了）。
    # ★ 2026-10-24（review R1）：本來就 `bool(_gmtok) and …`（fail-closed），這裡換成同一支 helper 只為「只有一份」。
    _gm_ok = gm_token_ok(svc.cfg, body.get("gm_token") or body.get("token"))
    _gmtok = (svc.cfg.get("auth") or {}).get("gm_token")

    if _gm_ok:
        body["seat"] = "gm"
        # 手下的端點（`/api/say`／`/api/gm`）讀的是 `body["token"]`＝桌控制鍵 ⇒ 正規化，
        # 免得「閘門放行了、端點又說 bad gm token」（2026-10-07 實測踩到）。
        body["token"] = _gmtok
        return True

    if path == "/api/pick_char" and _acct.enabled:
        # ⚠ `/api/pick_char` 是**還沒有席位時**要用的那一格（選角色 ⇒ 才有席位）⇒
        #    這一格**只驗「你是誰」**，不要求「已經有唯一的席位」。
        #    （2026-10-07 端到端驗收抓到：沒席位的帳號在一張新桌上根本選不了角色。）
        _who = _acct.resolve(_tok)
        if _who is None:
            if _req:
                self._json(403, {"error": "這個請求沒有帶有效的 account token"
                                          "（這張桌要求 account 認證）"})
                return False
        else:
            body["_account"] = _who
        return True

    _seat, _who, _err = auth.check_seat(_acct, _tok, body.get("seat"), require=_req)
    if _err:
        self._json(403, {"error": _err})
        return False
    if _seat:
        body["seat"] = _seat
    elif _who:
        # ★有認證、但這一局沒席位 ⇒ **寫入面直接擋**：不能照客戶端自己報的席位動手（＝冒充）。
        #   要下場先入座（`/api/enter` 或 `pickChar`）。讀（`/api/state`／`/api/log`）走旁觀那條。
        self._json(403, {"error": "你這一局還沒有席位（先在這一桌入座，再動作）"})
        return False
    if _who:
        body["_account"] = _who
    return True
