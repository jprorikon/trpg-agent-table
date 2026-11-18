"""util（自 __init__.py 拆出）"""

from __future__ import annotations
import contextlib
import hashlib
import json
import os
import secrets
import time
from typing import Any, Dict, Iterator, List, Optional


def _hash(tok: str) -> str:
    return hashlib.sha256(tok.encode("utf-8")).hexdigest()

def registry_path(home: str, cfg: Optional[Dict[str, Any]] = None) -> str:
    """registry 檔住哪：**`config.auth.accounts_file` 優先**（＝多張桌共用的那一份），
    沒寫就 `<home>/accounts.json`（一桌一份的舊寫法 ⇒ **legacy 一字不變**）。
    `~` 會展開；相對路徑以行程 cwd 為準（建議寫絕對路徑）。
    """
    p = str((((cfg or {}).get("auth") or {}).get("accounts_file")) or "").strip()
    if p:
        return os.path.abspath(os.path.expanduser(p))
    return os.path.join(home, "accounts.json")

class AccountsError(ValueError):
    """registry 壞掉（載入時就叫，不靜默）。"""

def token_from_request(*, headers: Any = None, query: Any = None, body: Any = None) -> Optional[str]:
    """從三種地方找 token（優先序：Authorization → query.token → body.token）。"""
    hdr = ""
    try:
        hdr = (headers or {}).get("Authorization") or (headers or {}).get("authorization") or ""
    except Exception:                                                   # noqa: BLE001
        hdr = ""
    if str(hdr).lower().startswith("bearer "):
        return str(hdr)[7:].strip()
    if isinstance(query, dict) and query.get("token"):
        return str(query["token"])
    if isinstance(body, dict) and body.get("token"):
        return str(body["token"])
    return None

def check_seat(accounts: Accounts, token: Optional[str], want_seat: Optional[str], *,
               require: bool) -> tuple[Optional[str], Optional[Dict[str, Any]], Optional[str]]:
    """回 `(seat, who, err)`：

    * 沒 registry ⇒ `(want_seat, None, None)`（legacy 照舊）
    * `require=False`：有效 token ⇒ 以 token 的席位為準；無效 token ⇒ 當作沒帶（相容）
    * `require=True`：無效 token ⇒ 錯誤；`want_seat` 與 token 的席位不符 ⇒ 錯誤（冒充）
    """
    if not accounts.enabled:
        return want_seat, None, None
    who = accounts.resolve(token)
    if who is None:
        if require:
            return None, None, "這個請求沒有帶有效的 account token（這張桌要求 account 認證）"
        return want_seat, None, None
    seat = who.get("seat")
    if seat is None:
        # ★這一局還沒席位 ⇒ 一律回 **None**（＝旁觀）：公開投影、`you` 缺席。
        #   ⚠ **不能回 `want_seat`**——帶了有效 token 卻照客戶端自己報的席位放行＝**冒充**
        #   （2026-10-08 `test_auth` S2b 抓到：舊寫法讓「登入但沒入座」的帳號點名要 p1 就拿到 p1；
        #     寫入面更糟——等於替那個席位的人動手）。這裡的判據是「**token 說了算**」：
        #     有 token ⇒ 以 token 認出的席位為準；它這一局沒有席位 ⇒ 就是沒有。
        #   ⚠ 也不回錯誤（不是 403）：owner 2026-10-08「刷新也没看到参加者」——有登入但還沒入座時，
        #     連公開牌面（席位一覽）都看不到。**讀**走這裡（只給公開投影）；
        #     **寫**由各自的呼叫端擋（`web_api`／`mcp_api`：沒席位不能 act／say／tell）。
        return None, who, None
    if require and want_seat and str(want_seat) != str(seat):
        return None, who, (f"token 屬於 {seat}（{who.get('display')}），不能替 {want_seat} 行動")
    return seat, who, None
