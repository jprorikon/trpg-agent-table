#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**帳號的喚醒端點**（kick 按帳號，2026-10-20 設計者定案）。

    # 看這張桌每個席位現在坐哪個帳號、端點登記了沒
    python3 tools/set_wake.py --config state/tables/hyakki_tower/sannenme_no_onibarai/hyakki_tower_demo_1/config.json --list

    # 登記／改一個帳號的喚醒端點（全桌共用一個；跨桌同一個帳號也共用）
    python3 tools/set_wake.py --config <桌 config.json> acct_peer http://<host>:8893/trpg/kick \
        --token-file .secrets/trpg_kick_rio.token

    # 把桌上舊的 wake.seats.<席>.url 遷到「該席現在坐的帳號」（先 --dry-run 看對照）
    python3 tools/set_wake.py --config <桌 config.json> --migrate-seat-urls --dry-run

為什麼要有這支：kick 的端點以前按**席位**寫死（`config.wake.seats.<席>.url`），
結果踢到了沒參賽的帳號（2026-10-20 示範劇本：p4 由本機 GM agent 入座，kick 卻打到 外部 agent 橋）。
現在端點掛在**帳號**上：`account.wake_url`（DB 後端）／`accounts.<id>.wake_url`（JSON 後端），
由 `account_store.accounts_for()` 自動選後端——這支工具不自己決定後端。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from trpg.accounts import account_store          # noqa: E402


def _load_cfg(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _store(cfg: dict, config_path: str):
    home = os.path.dirname(os.path.abspath(config_path))
    table = str(cfg.get("table_id") or os.path.basename(home.rstrip("/")))
    return account_store.accounts_for(home, table, cfg)


def cmd_list(store) -> int:
    seats = store.seats or {}
    if not seats:
        print("（這張桌沒有座次資料）")
        return 0
    print(f"{'席位':<6} {'帳號':<26} {'喚醒端點':<52} token檔")
    for seat in sorted(seats):
        acct = store.account_of_seat(seat) or ""
        w = store.wake_of(acct) if acct else {}
        print(f"{seat:<6} {(acct or '（未綁）'):<26} {((w or {}).get('url') or '—'):<52} "
              f"{((w or {}).get('token_file') or '—')}")
    return 0


def cmd_set(store, account: str, url: str, token_file: str, token: str) -> int:
    # 後端無關的存在性檢查：DB 版沒有 `.data`（那是 JSON 版的內部結構），一律走 who()
    try:
        exists = bool((store.who(account) or {}).get("account"))
    except Exception:                                                    # noqa: BLE001
        exists = False
    if not exists:
        print(f"沒有這個帳號：{account}", file=sys.stderr)
        return 2
    out = store.set_wake(account, url, token_file, token)
    print(f"{account} ⇒ {out.get('url')}"
          + (f"（token file: {out.get('token_file')}）" if out.get("token_file") else "")
          + ("（token: 已設）" if out.get("token") else ""))
    return 0


def cmd_migrate(store, cfg: dict, dry: bool) -> int:
    """把 `wake.seats.<席>.url` 遷到該席現在坐的帳號（帳號已登記過就跳過、不覆蓋）。"""
    seats_cfg = ((cfg.get("wake") or {}).get("seats") or {})
    if not seats_cfg:
        print("這張桌的 config 沒有 wake.seats.*（沒東西可遷）")
        return 0
    print(f"{'席位':<6} {'帳號':<26} {'動作':<10} 端點")
    rc = 0
    for seat in sorted(seats_cfg):
        url = str((seats_cfg.get(seat) or {}).get("url") or "")
        if not url:
            continue
        token_file = str((seats_cfg.get(seat) or {}).get("token_file") or "")
        token = str((seats_cfg.get(seat) or {}).get("token") or "")
        acct = store.account_of_seat(seat) or ""
        if not acct:
            print(f"{seat:<6} {'（未綁）':<26} {'跳過':<10} 這席沒有帳號 ⇒ 不遷（kick 也不會踢它）")
            continue
        cur = (store.wake_of(acct) or {}).get("url") or ""
        if cur and cur != url:
            print(f"{seat:<6} {acct:<26} {'保留原值':<10} {cur}（config 是 {url}）")
            continue
        if cur == url:
            print(f"{seat:<6} {acct:<26} {'已一致':<10} {url}")
            continue
        print(f"{seat:<6} {acct:<26} {'寫入' if not dry else '待寫':<10} {url}")
        if not dry:
            store.set_wake(acct, url, token_file, token)
    if dry:
        print("\n（--dry-run：沒有寫入任何東西）")
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description="帳號的喚醒端點（kick 按帳號）")
    ap.add_argument("--config", required=True, help="這一桌的 config.json（決定後端與 table id）")
    ap.add_argument("account", nargs="?", default="", help="帳號 id（省略＝只做 --list／--migrate）")
    ap.add_argument("url", nargs="?", default="", help="喚醒端點 URL")
    ap.add_argument("--token-file", default="", help="kick token 檔（600）；不給＝不改")
    ap.add_argument("--token", default="", help="kick token 明文（寫進帳號；不想留明文就用 --token-file）")
    ap.add_argument("--list", action="store_true", help="列出每個席位 → 帳號 → 端點")
    ap.add_argument("--migrate-seat-urls", action="store_true", help="把 wake.seats.<席>.url 遷到帳號")
    ap.add_argument("--dry-run", action="store_true", help="只印對照，不寫")
    args = ap.parse_args()

    cfg = _load_cfg(args.config)
    store = _store(cfg, args.config)
    if args.list:
        return cmd_list(store)
    if args.migrate_seat_urls:
        return cmd_migrate(store, cfg, args.dry_run)
    if not args.account or not args.url:
        ap.error("要嘛 --list／--migrate-seat-urls，要嘛給 <account> <url>")
    return cmd_set(store, args.account, args.url, args.token_file, args.token)


if __name__ == "__main__":
    sys.exit(main())
