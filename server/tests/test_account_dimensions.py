#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**帳號分層（P2）的閘**——設計者 2026-10-20 定案：

    帳號＝引擎層（跨桌跨模組）／模組層＝帳號→角色實例 1:N／座次由劇本決定、不直接綁帳號

    python3 tests/test_account_dimensions.py

  D1 帳號本體不帶 module（`account` 表沒有這個欄）
  D2 **同模組換劇本**（兩個 table 同 module）⇒ 角色實例是同一個（跨桌成長帶著走）
  D3 **同模組兩桌**的錢包是同一個（相加），不同模組互不影響
  D4 座次是**副本**維度：`run_seat` 按 run_id 隔離（A 局 p2 不影響 B 局 p2）
  D5 同帳號同模組可以有**多個不同角色**實例（1:N），但同角色不可重複

沒有 DB 設定 ⇒ SKIP（回 0），不假紅。
"""
from __future__ import annotations

import os
import secrets
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from trpg.accounts import db as accounts_db                                   # noqa: E402
from trpg.core import db as db                                                # noqa: E402

OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


def main() -> int:
    conf = db.configure({}, default_env=True)
    if not conf:
        print("SKIP：沒有 DB 設定—— 這條閘需要真的 PostgreSQL")
        return 0
    schema = "trpg_dim_" + secrets.token_hex(4)
    print(f"== 帳號分層 P2 · schema={schema} ==")
    tmp = tempfile.mkdtemp(prefix="trpg_dim_")
    try:
        db.ensure_schema(conf, schema=schema)
        # 同一個「模組 demo_world」下的兩個劇本／兩張桌
        cfg_a = {"module": "demo_world", "scenario": "scenario_b", "table_id": "t_b_2"}
        cfg_b = {"module": "demo_world", "scenario": "scenario_a", "table_id": "t_a_1"}
        cfg_c = {"module": "hyakki_tower", "scenario": "sannenme", "table_id": "t_hyakki_1"}
        A = accounts_db.DbAccounts(tmp, "t_b_2", conf=conf, schema=schema, cfg=cfg_a,
                                   run_id="run_a")
        B = accounts_db.DbAccounts(tmp, "t_a_1", conf=conf, schema=schema, cfg=cfg_b,
                                   run_id="run_b")
        C = accounts_db.DbAccounts(tmp, "t_hyakki_1", conf=conf, schema=schema, cfg=cfg_c,
                                   run_id="run_c")
        with A._cursor(commit=True) as cur:
            cur.execute("INSERT INTO account (id, display, kind) VALUES ('acct_x','X','human')")
            cur.execute("SELECT column_name FROM information_schema.columns"
                        " WHERE table_schema=%s AND table_name='account'", (schema,))
            cols = [r[0] for r in (cur.fetchall() or [])]
        check("D1 帳號本體不帶 module（引擎層）", "module" not in cols, str(cols))

        # D2：同模組、不同劇本 ⇒ 同一個角色實例
        A.write_instance("acct_x", "sane", {"level": 3, "exp": 40})
        got = B.instance("acct_x", "sane")
        check("D2 同模組換劇本仍看得到同一份成長", got.get("level") == 3 and got.get("exp") == 40,
              str(got))
        other = C.instance("acct_x", "sane")
        check("D2b 不同模組是另一份（不存在）", other == {}, str(other))

        # D3：錢包＝(帳號 × 模組)
        A.add_wallet("acct_x", star_coins=5)
        B.add_wallet("acct_x", star_coins=7)
        wa = A.wallet("acct_x")
        wc = C.wallet("acct_x")
        check("D3 同模組兩桌共用同一個錢包（相加 12）", wa.get("star_coins") == 12, str(wa))
        check("D3b 不同模組的錢包是空的", wc == {}, str(wc))

        # D4：座次是副本維度（run_seat 按 run_id）
        A.assign_seat("p2", "acct_x", "sane")
        B.assign_seat("p2", "acct_x", "char_b")
        sa = (A.seats or {}).get("p2") or {}
        sb = (B.seats or {}).get("p2") or {}
        check("D4 座次按副本隔離（A 局 p2＝sane）", sa.get("char") == "sane", str(sa))
        check("D4b B 局 p2 不受影響（＝char_b）", sb.get("char") == "char_b", str(sb))
        with A._cursor() as cur:
            cur.execute("SELECT module FROM run_seat WHERE run_id='run_a' AND seat='p2'")
            r = cur.fetchone()
        check("D4c run_seat 記的是**真模組名**", bool(r) and r[0] == "demo_world", str(r))

        # D5：1:N 角色實例（同模組多個角色），同角色不可重複
        A.write_instance("acct_x", "char_b", {"level": 2})
        with A._cursor() as cur:
            cur.execute("SELECT count(*) FROM char_instance WHERE account='acct_x' AND module='demo_world'")
            n = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM char_instance WHERE account='acct_x'"
                        " AND module='demo_world' AND char='char_b'")
            n2 = cur.fetchone()[0]
        check("D5 同帳號同模組多個角色實例（1:N）", int(n) == 2, str(n))
        check("D5b 同角色不會有兩份（PK 擋住）", int(n2) == 1, str(n2))
    finally:
        try:
            db.drop_schema(conf, schema)
        except Exception:                                                     # noqa: BLE001
            pass
    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
