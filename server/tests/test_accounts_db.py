#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**account 層（DB 後端）的自測**——四層關係鍵的閘（2026-10-07 設計者定案）。

    python3 test_accounts_db.py

  D1 **建表幂等**：`ensure_schema` 跑兩次都成功。
  D2 **人級 account**：token ⇒ account（假 token ⇒ None）；帳號＋密碼登入 ⇒ session token 同效。
  D3 **說法 2｜錢包＝(帳號 × 模組)**：同一帳號在兩個模組各一筆，互不影響。
  D4 **說法 3｜角色實例＝(帳號 × 模組 × 角色)**：同名角色在兩個模組是**兩份獨立成長**。
  D5 **桌級座次**：`assign_seat`／`seat_record`／`set_seat_field`／`seats_with`（跨模組查）。
  D6 **說法 4｜副本**：`run` ＋ `run_participation`（含 snapshot）；`written_back` 掛在**這一局**。
  D7 **後端挑選**：`account_store.accounts_for` 有設 DB ⇒ DB；沒設 ⇒ JSON（legacy）。
  D8 **介面齊**：消費端會呼叫的方法，兩個後端**都有**（同一件事不做兩份）。
  D9 **收局寫回（run 幂等）**：`_maybe_writeback` 同一局跑兩次 ⇒ 成長／錢包只寫一次；
     換一個 `run_id` ⇒ 會再寫（不是桌級一次鎖死）。
  D10 **legacy 一字不變**：JSON 後端 `run_*` 落回桌級 `written_back`（舊行為）。

沒有 DB 設定（`.secrets/trpg_db.env` 不存在且沒 `--dsn`）⇒ **SKIP**（回 0），不假紅。
"""
from __future__ import annotations

import os
import secrets
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo 根
sys.path.insert(0, HERE)

from trpg.accounts import account_store as account_store                                                    # noqa: E402
from trpg.accounts import db as accounts_db                                                      # noqa: E402
from trpg.accounts import auth as auth                                                             # noqa: E402
from trpg.core import db as db                                                               # noqa: E402
from trpg.api import web as web_api                                                          # noqa: E402

OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


class _Table:
    """假的桌子（只給 `_maybe_writeback` 要的那幾格）。"""

    def __init__(self, run_id, ended=True, seats=None, star=None, items=None):
        self.state = {"ended": "e1" if ended else None,
                      "seats": seats or {"p1": {"star_coins": 4, "memory": 7, "node": "n1"}},
                      "star": star or {"p1": 2},
                      "inventory": items or {"p1": {"heal_pack": 1}},
                      "run_id": run_id}
        self._run = run_id
        self._ended = ended

    def adapter_view(self):
        return {"run_id": self._run, "ended": ("e1" if self._ended else None)}

    def level_of(self, seat):
        return {"p1": 3}.get(seat, 1)

    def exp_of(self, seat):
        return {"p1": 44}.get(seat, 0)


class _Svc:
    def __init__(self, table, home="."):
        self.table = table
        self.home = home
        self.cfg = {"table_id": "mod_a"}


def main() -> int:                                                       # noqa: C901
    conf = db.configure({}, default_env=True)          # 這條閘本身就用機器預設的 .secrets/trpg_db.env
    if not conf:
        print("SKIP：沒有 DB 設定（.secrets/trpg_db.env 或 --dsn）—— 這條閘需要真的 PostgreSQL")
        return 0
    schema = "trpg_test_" + secrets.token_hex(4)
    print(f"== account 層（DB 後端）· schema={schema} ==")
    tmp = tempfile.mkdtemp(prefix="trpg_db_")
    try:
        db.ensure_schema(conf, schema=schema)
        db.ensure_schema(conf, schema=schema)                             # 幂等
        check("D1 `ensure_schema` 跑兩次都成功（幂等）", True)

        A = accounts_db.DbAccounts(tmp, "mod_a", conf=conf, schema=schema)
        B = accounts_db.DbAccounts(tmp, "mod_b", conf=conf, schema=schema)
        with A._cursor(commit=True) as cur:
            cur.execute("INSERT INTO account (id, display, kind, token_sha256) VALUES (%s,%s,%s,%s)",
                        ("acct_a", "阿", "human", auth._hash("trpg_acct_a_x")))
            cur.execute("INSERT INTO account (id, display, kind) VALUES (%s,%s,%s)",
                        ("acct_b", "B", "agent"))
        check("D2a token ⇒ account（假 token ⇒ None）",
              (A.resolve("trpg_acct_a_x") or {}).get("account") == "acct_a"
              and A.resolve("nope") is None)
        A.set_login("acct_a", "阿", "pw123")
        li = A.login("阿", "pw123")
        _tok = (li or {}).get("token") or ""
        check("D2b 帳號＋密碼登入 ⇒ 發 session token，且同一條 resolve 認得",
              bool(_tok) and (A.resolve(_tok) or {}).get("account") == "acct_a")
        check("D2c 錯的密碼 ⇒ None", A.login("阿", "bad") is None)

        A.add_wallet("acct_a", star_coins=7)
        B.add_wallet("acct_a", star_coins=3)
        check("D3 錢包＝(帳號 × 模組)：兩個模組各一筆、互不影響",
              A.wallet("acct_a") == {"star_coins": 7} and B.wallet("acct_a") == {"star_coins": 3},
              f"{A.wallet('acct_a')} / {B.wallet('acct_a')}")
        A.add_wallet("acct_a", star_coins=5)
        check("D3b 同模組再 += 5 ⇒ 12（不是覆蓋）", A.wallet("acct_a") == {"star_coins": 12})

        A.write_instance("acct_a", "hero", {"level": 5, "exp": 120})
        B.write_instance("acct_a", "hero", {"level": 1, "exp": 0})
        check("D4 角色實例＝(帳號 × 模組 × 角色)：同名 'hero' 在兩模組是兩份成長",
              A.instance("acct_a", "hero")["level"] == 5
              and B.instance("acct_a", "hero")["level"] == 1,
              f"{A.instance('acct_a','hero')} / {B.instance('acct_a','hero')}")
        A.write_instance("acct_a", "hero", {"level": 6})                   # 只寫成長、只動 level
        check("D4b 只寫成長：level 蓋掉、exp 不變",
              A.instance("acct_a", "hero") == {"level": 6, "exp": 120})
        A.write_instance("acct_a", "hero", {"level": 6, "exp": 130, "star": 9, "coins": 99})
        check("D4c 局內的東西（star／coins）寫不進來",
              A.instance("acct_a", "hero") == {"level": 6, "exp": 130})

        A.assign_seat("p1", "acct_a", "hero")
        A.set_seat_field("p1", "wallet_carried", True)
        check("D5a 座次 ＋ 記帳欄",
              A.seat_record("p1") == {"account": "acct_a", "char": "hero",
                                      "wallet_carried": True, "coins_written": None})
        check("D5b `seats_with` 查得到跨模組的座次（互斥檢查用）",
              A.seats_with("acct_a", "hero") == [{"module": "mod_a", "seat": "p1"}])
        check("D5c 沒坐過的角色 ⇒ 空（不會亂擋）",
              A.seats_with("acct_a", "nobody") == [] and B.seats_with("acct_a", "nobody") == [])

        A.start_run("r1")
        A.join_run("acct_a", "hero", "r1", "p1")
        check("D6a 副本參與列：`(帳號, 模組, 角色, 副本)` → 席位",
              A.participation("acct_a", "hero", "r1")["seat"] == "p1")
        check("D6b `written_back` 一開始是 False", A.run_written_back("r1") is False)
        A.mark_run_written_back("r1")
        check("D6c 標記後為 True", A.run_written_back("r1") is True)
        A.finish_run("r1", {"hero": {"star": 2, "items": {"heal_pack": 1}}})
        pt = A.participation("acct_a", "hero", "r1")
        check("D6d 收局：參與列 ended ＋ 局內快照留下來（★／道具）",
              pt.get("ended") is True and (pt.get("snapshot") or {}).get("star") == 2,
              str(pt.get("snapshot")))

        # ★ 2026-10-20（review P4）：換局後 kick 要讀「這一局」的座次，不是桌級舊座次。
        A.join_run("acct_b", "hero2", "r2", "p1")
        A2 = accounts_db.DbAccounts(tmp, "mod_a", conf=conf, schema=schema, run_id="r2")
        check("D6e 有 run_id ⇒ account_of_seat 讀 run_seat（這一局的人）",
              A2.account_of_seat("p1") == "acct_b" and A.account_of_seat("p1") == "acct_a",
              f"r2={A2.account_of_seat('p1')} / 桌級={A.account_of_seat('p1')}")

        check("D7a 有設 DB ⇒ 挑到 DB 後端",
              account_store.accounts_for(tmp, "mod_a", {"auth": {"db": "postgresql://u:p@h:1/d"}})
              .backend == "db")
        check("D7b 沒設 DB ⇒ JSON 後端（legacy 一字不變）",
              account_store.accounts_for(tmp, "mod_a", {}).backend == "json")

        surface = ["resolve", "who", "seats", "seat_of", "char_of", "instance", "write_instance",
                   "wallet", "add_wallet", "snapshot", "login", "set_login", "enabled",
                   "assign_seat", "seat_record", "set_seat_field", "seats_with", "start_run",
                   "join_run", "finish_run", "run_written_back", "mark_run_written_back",
                   "coins_written", "set_coins_written", "peer", "save", "refresh", "_lock"]
        miss_db = [m for m in surface if not hasattr(accounts_db.DbAccounts, m)]
        miss_json = [m for m in surface if not hasattr(auth.Accounts, m)]
        check("D8 消費端會用的方法，兩個後端都有（同一件事不做兩份）",
              not miss_db and not miss_json, f"db缺={miss_db} json缺={miss_json}")

        # D9 收局寫回：同一局兩次只寫一次；換 run_id 會再寫
        t1 = _Table("rA")
        web_api._maybe_writeback(_Svc(t1), A)
        first = A.instance("acct_a", "hero")
        w1 = A.wallet("acct_a")
        web_api._maybe_writeback(_Svc(t1), A)                             # 第二次
        same = (A.instance("acct_a", "hero") == first and A.wallet("acct_a") == w1)
        check("D9a 同一局寫兩次 ⇒ 成長／錢包只寫一次（幂等）", same,
              f"inst={A.instance('acct_a','hero')} wallet={A.wallet('acct_a')}")
        check("D9b 錢包有把局內的貨幣帶出來（4）", w1.get("star_coins") == 12 + 4, str(w1))
        web_api._maybe_writeback(_Svc(_Table("rB")), A)
        check("D9c 換一個 `run_id` ⇒ 會再寫一筆（不是桌級一次鎖死）",
              A.wallet("acct_a").get("star_coins") == 12 + 4 + 4,
              str(A.wallet("acct_a")))

        # D10 JSON 後端：run_* 落回桌級 written_back
        jtmp = tempfile.mkdtemp(prefix="trpg_json_")
        jreg = {"accounts": {"acct_a": {"display": "A", "kind": "human"}}, "sessions": {}}
        import json as _json
        with open(os.path.join(jtmp, "accounts.json"), "w", encoding="utf-8") as fh:
            _json.dump(jreg, fh)
        J = auth.Accounts(jtmp, "mod_a")
        check("D10  JSON 後端 `run_written_back` 落回桌級 `written_back`（legacy）",
              J.run_written_back("whatever") is False
              and (J.mark_run_written_back("whatever") or J.run_written_back("anything") is True))
    finally:
        try:
            db.drop_schema(conf, schema)
        except Exception:                                                 # noqa: BLE001
            pass

    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
