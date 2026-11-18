#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 TRPG DB 的設定檔 `.secrets/trpg_db.env`（600，不進版控）。

只做一次；已存在就不覆蓋（避免把已建好的容器密碼換掉 ⇒ 連不上）。
要換密碼＝先 `docker exec trpg-postgres psql ... ALTER USER`，再改這個檔。
"""
from __future__ import annotations

import os
import secrets
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENVF = os.path.join(REPO, ".secrets", "trpg_db.env")


def main() -> int:
    if os.path.exists(ENVF):
        print(f"（已存在，不覆蓋）{ENVF}")
        return 0
    os.makedirs(os.path.dirname(ENVF), exist_ok=True)
    pwd = secrets.token_hex(16)
    body = (
        "# TRPG PostgreSQL（deploy/db_up.sh 讀這份；600）\n"
        "TRPG_DB_HOST=127.0.0.1\n"
        "TRPG_DB_PORT=8433\n"
        "TRPG_DB_USER=trpg\n"
        "TRPG_DB_NAME=trpg\n"
        f"TRPG_DB_PASSWORD={pwd}\n"
    )
    with open(ENVF, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(ENVF, 0o600)
    print(f"✔ 寫好 {ENVF}（密碼不印）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
