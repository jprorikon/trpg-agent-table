#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**TRPG account 層（DB 後端）的維運工具**——建表／從 JSON registry 匯入／看／發憑證。

     python3 tools/trpg_db.py init                      # 建 schema 與表（幂等）
     python3 tools/trpg_db.py import-json --registry .secrets/accounts_registry.json
     python3 tools/trpg_db.py list                      # 各模組的錢包／實例／座次
     python3 tools/trpg_db.py issue --only acct_player1    # 換發帳號 token（明文寫檔交人）
     python3 tools/trpg_db.py verify <token>            # 這把 token 是誰
     python3 tools/trpg_db.py set-wallet acct_player1 hyakki_tower 18

**模組維度的遷移取捨（重要）**：舊 registry 的錢包與 char 成長**沒有模組欄**
（那是這次要收斂掉的東西）。匯入時：
  * `char_instance` 的模組＝**他在哪張桌坐了那個角色**（座次表推得出來，沒有猜）；
  * `wallet` 的模組＝**有 `coins_written`／`wallet_carried` 紀錄的那張桌**（錢真的在那裡動過）；
    都沒紀錄 ⇒ 放進他有座位的第一張桌（並在輸出裡標出來，讓設計者裁決）。
  ⇒ 不複製錢、也不靜默平分；匯入完 `list` 出來對帳。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)

from trpg.core import db as db                                                              # noqa: E402
from trpg.accounts import db as accounts_db                                                     # noqa: E402
from trpg.accounts.auth import _hash                                                 # noqa: E402


def _store(args) -> accounts_db.DbAccounts:
    cfg = {"auth": {}}
    if getattr(args, "dsn", None):
        cfg["auth"]["db"] = args.dsn
    if getattr(args, "env_file", None):
        cfg["auth"]["db_env"] = args.env_file
    conf = db.configure(cfg, default_env=True)      # CLI：沒指定就吃機器預設的 .secrets/trpg_db.env
    if not conf:
        print("✗ 沒有 DB 設定（--dsn 或 --env-file，或預設 .secrets/trpg_db.env）", file=sys.stderr)
        raise SystemExit(2)
    return accounts_db.DbAccounts(REPO, args.module or "cli", conf=conf,
                                  schema=(args.schema or db.SCHEMA))


# ------------------------------------------------------------------ init
def cmd_init(args) -> int:
    st = _store(args)
    db.ensure_schema(st.conf, schema=st.schema)
    print(f"✔ schema 就緒（{st.schema}）：account / account_login / login_session /"
          f" seat_assignment / wallet / char_instance / run / run_participation")
    return 0


# ------------------------------------------------------------- import-json
def cmd_import_json(args) -> int:
    st = _store(args)
    db.ensure_schema(st.conf, schema=st.schema)
    with open(args.registry, encoding="utf-8") as fh:
        reg = json.load(fh)
    accounts = reg.get("accounts") or {}
    sessions = reg.get("sessions") or {}
    legacy = reg.get("session")
    if legacy and not sessions:
        sessions = {st.table: legacy}

    # 座次 → (account, char) 落在哪個模組
    char_module: dict = {}
    seat_rows = []
    for module, s in sessions.items():
        for seat, b in ((s or {}).get("seats") or {}).items():
            aid, ch = (b or {}).get("account"), (b or {}).get("char")
            seat_rows.append((module, seat, aid, ch, b or {}))
            if aid and ch:
                char_module.setdefault((aid, ch), module)

    n_acc = n_inst = n_seat = n_wal = 0
    notes: list = []
    for aid, a in accounts.items():
        with st._cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO account (id, display, kind, note, token_sha256) VALUES (%s,%s,%s,%s,%s)"
                " ON CONFLICT (id) DO UPDATE SET display = EXCLUDED.display, kind = EXCLUDED.kind,"
                "  note = EXCLUDED.note,"
                "  token_sha256 = COALESCE(EXCLUDED.token_sha256, account.token_sha256)",
                (aid, a.get("display"), a.get("kind"), a.get("note"), a.get("token_sha256")))
            lg = a.get("login") or {}
            if lg.get("username") and lg.get("password_sha256"):
                cur.execute("INSERT INTO account_login (account, username, password_sha256)"
                            " VALUES (%s,%s,%s) ON CONFLICT (account, username) DO UPDATE SET"
                            "  password_sha256 = EXCLUDED.password_sha256",
                            (aid, lg["username"], lg["password_sha256"]))
            for sh in (a.get("sessions") or {}):
                cur.execute("INSERT INTO login_session (token_sha256, account, label)"
                            " VALUES (%s,%s,%s) ON CONFLICT (token_sha256) DO NOTHING",
                            (sh, aid, "imported"))
        n_acc += 1
        # char 實例：模組由座次推出
        for ch, inst in (a.get("chars") or {}).items():
            mod = char_module.get((aid, ch))
            if not mod:
                notes.append(f"{aid}.{ch}：沒有座次 ⇒ 無法判定模組，跳過（要就 set-wallet 般手動補）")
                continue
            with st._cursor(commit=True) as cur:
                cur.execute("INSERT INTO char_instance (account, module, char, level, exp, note)"
                            " VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (account, module, char)"
                            " DO UPDATE SET level = EXCLUDED.level, exp = EXCLUDED.exp,"
                            "  note = COALESCE(EXCLUDED.note, char_instance.note)",
                            (aid, mod, ch, int(inst.get("level") or 1), int(inst.get("exp") or 0),
                             inst.get("note")))
            n_inst += 1

    # 錢包：挑「錢真的動過」的那張桌
    for aid, a in accounts.items():
        total = int((a.get("wallet") or {}).get("star_coins") or 0)
        if not total:
            continue
        cands = [m for m, s in sessions.items()
                 for seat, b in ((s or {}).get("seats") or {}).items()
                 if (b or {}).get("account") == aid and (b or {}).get("coins_written") is not None]
        if not cands:                       # 沒有寫入紀錄 ⇒ 帶入紀錄（wallet_carried）
            cands = [m for m, s in sessions.items()
                     for seat, b in ((s or {}).get("seats") or {}).items()
                     if (b or {}).get("account") == aid and (b or {}).get("wallet_carried")]
        if not cands:                       # 都沒有 ⇒ 他有座位的第一張桌
            cands = sorted({m for m, s in sessions.items()
                            for seat, b in ((s or {}).get("seats") or {}).items()
                            if (b or {}).get("account") == aid})
            if cands:
                notes.append(f"{aid}：錢包沒有動用紀錄 ⇒ 全額 {total} 放進 {cands[0]}（請裁決）")
        if not cands:
            notes.append(f"{aid}：有錢包 {total} 但沒有任何座次 ⇒ 無處放，跳過")
            continue
        mod = sorted(set(cands))[0]
        with st._cursor(commit=True) as cur:
            cur.execute("INSERT INTO wallet (account, module, star_coins) VALUES (%s,%s,%s)"
                        " ON CONFLICT (account, module) DO UPDATE SET star_coins = EXCLUDED.star_coins",
                        (aid, mod, total))
        n_wal += 1

    # 座次（**按模組**寫；`st` 自己是 "cli" 模組，不能直接用它）
    for module, seat, aid, ch, b in seat_rows:
        if not aid:
            continue
        mst = st.peer(REPO, module)
        mst.assign_seat(seat, aid, ch or "")
        if b.get("wallet_carried"):
            mst.set_seat_field(seat, "wallet_carried", True)
        if b.get("coins_written") is not None:
            mst.set_seat_field(seat, "coins_written", int(b["coins_written"]))
        n_seat += 1

    print(f"✔ 匯入完成：account {n_acc}／char_instance {n_inst}／座次 {n_seat}／錢包 {n_wal}")
    # ── **已經寫回過的局**要一起搬：不然換到 DB 之後，下一筆請求看到 DB 裡沒有這個 `run`
    #    ⇒ 再寫一次（錢翻倍、成長覆蓋）。run_id 從該模組事件流最後一筆 `table_created` 讀。
    for module, s in sessions.items():
        rid = _current_run_id(module)
        mst = st.peer(REPO, module)
        if rid and (s or {}).get("written_back"):
            mst.mark_run_written_back(rid)
            print(f"  ↳ {module}：已寫回過 ⇒ DB 標記 run {rid} written_back（防重複寫入）")
        elif rid:
            mst.start_run(rid)
            print(f"  ↳ {module}：現役副本 {rid}（尚未寫回）")
    for n in notes:
        print(f"  ⚠ {n}")
    return 0


def _current_run_id(module: str) -> str:
    """這一張桌**現在這一局**的 run_id（事件流裡**最後一筆** `table_created` 的 `run_id`）。

    ⚠ 不能只看尾巴 64KB：現役 示範模組 的 4163 筆裡，`table_created` 離尾端很遠 ⇒ 會讀不到
    （第一版就是這樣，於是「已寫回」的局沒被標記、換 DB 後會再寫一次）。整檔串流掃、只留最後一筆。
    """
    from trpg.core import layout as layout                                                        # noqa: PLC0415
    row = layout.locate(REPO, module)
    p = layout.events_path(row["home"]) if row else os.path.join(REPO, "campaigns", module,
                                                                 "data", "events.jsonl")
    rid = ""
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or "table_created" not in line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:                                        # noqa: BLE001
                    continue
                if ev.get("type") == "table_created" and ev.get("run_id"):
                    rid = str(ev["run_id"])
    except OSError:
        return ""
    return rid


# ------------------------------------------------------------------ list
def cmd_list(args) -> int:
    st = _store(args)
    print(f"schema={st.schema}  dsn={st.conf.get('host')}:{st.conf.get('port')}/{st.conf.get('database')}")
    with st._cursor() as cur:
        cur.execute("SELECT id, display, kind FROM account ORDER BY id")
        accs = cur.fetchall() or []
        for aid, display, kind in accs:
            cur.execute("SELECT module, char, level, exp FROM char_instance WHERE account = %s"
                        " ORDER BY module, char", (aid,))
            insts = cur.fetchall() or []
            cur.execute("SELECT module, star_coins FROM wallet WHERE account = %s ORDER BY module", (aid,))
            wals = cur.fetchall() or []
            # ★ 2026-10-20（P2）：座次的正式位置是 `run_seat`（副本維度）；`seat_assignment`
            #   只剩相容（table 維度）。兩個都印，避免維運看到過時的那一份。
            try:
                cur.execute("SELECT module, seat, char FROM run_seat WHERE account = %s"
                            " ORDER BY module, seat", (aid,))
                seats = cur.fetchall() or []
            except Exception:                                                 # noqa: BLE001
                seats = []
            cur.execute("SELECT module, seat, char FROM seat_assignment WHERE account = %s"
                        " ORDER BY module, seat", (aid,))
            seats_legacy = cur.fetchall() or []
            print(f"  {aid:28s} {str(display or ''):12s} {str(kind or ''):6s}"
                  f" seat={','.join(f'{m}:{s}' for m, s, _ in seats) or '—'}"
                  f" char={','.join(f'{m}:{c}(L{l}/e{e})' for m, c, l, e in insts) or '—'}"
                  f" wallet={','.join(f'{m}:{c}' for m, c in wals) or '—'}")
            if seats_legacy:
                print(f"  {'':28s} 相容表 seat_assignment（舊位置）="
                      f"{','.join(f'{m}:{s}' for m, s, _ in seats_legacy)}")
    return 0


# ------------------------------------------------------------------ issue
def cmd_issue(args) -> int:
    st = _store(args)
    targets = [x.strip() for x in (args.only or "").split(",") if x.strip()]
    if not targets:
        print("✗ 要 --only <帳號[,帳號]>", file=sys.stderr)
        return 2
    issued = []
    for aid in targets:
        with st._cursor(commit=True) as cur:
            cur.execute("SELECT 1 FROM account WHERE id = %s", (aid,))
            if cur.fetchone() is None:
                print(f"✗ 沒有這個帳號：{aid}", file=sys.stderr)
                continue
        tok = accounts_db.issue_token(st, aid)
        issued.append((aid, tok))
    if not issued:
        return 2
    out = args.out or os.path.join(REPO, ".secrets",
                                   "trpg_db_tokens_" + __import__("time").strftime("%Y%m%d-%H%M%S") + ".txt")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("# TRPG account token（DB 後端；registry 只存 sha256，這份只發一次）\n")
        for aid, tok in issued:
            fh.write(f"{aid}\t{tok}\n")
    os.replace(tmp, out)
    os.chmod(out, 0o600)
    print(f"✔ 發了 {len(issued)} 把（明文寫在 {out}，不印在終端）：" +
          ", ".join(a for a, _ in issued))
    return 0


# ------------------------------------------------------------------ verify
def cmd_verify(args) -> int:
    st = _store(args)
    who = st.resolve(args.token)
    if who is None:
        print("✗ 這把 token 不認得（或已換發）", file=sys.stderr)
        return 1
    print(json.dumps({k: who.get(k) for k in ("account", "display", "kind", "seat", "chars",
                                              "tables")}, ensure_ascii=False))
    return 0


def cmd_set_wallet(args) -> int:
    st = _store(args)
    with st._cursor(commit=True) as cur:
        cur.execute("INSERT INTO wallet (account, module, star_coins) VALUES (%s,%s,%s)"
                    " ON CONFLICT (account, module) DO UPDATE SET star_coins = EXCLUDED.star_coins",
                    (args.account, args.module, int(args.amount)))
    print(f"✔ {args.account} @ {args.module} ⇒ {args.amount}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="TRPG account 層（DB 後端）維運")
    ap.add_argument("--dsn", help="postgresql://user:pw@host:port/db（預設讀 .secrets/trpg_db.env）")
    ap.add_argument("--env-file", help="DB 設定檔路徑（TRPG_DB_*）")
    ap.add_argument("--schema", help=f"schema（預設 {db.SCHEMA}）")
    ap.add_argument("--module", help="（僅 list/verify 用不到；保留給單模組指令）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    p = sub.add_parser("import-json")
    p.add_argument("--registry", required=True, help="舊的 JSON registry 檔")
    sub.add_parser("list")
    p = sub.add_parser("issue")
    p.add_argument("--only", required=True, help="帳號 id，逗號分隔")
    p.add_argument("--out", help="憑證明文寫到哪（預設 .secrets/…txt，600）")
    p = sub.add_parser("verify")
    p.add_argument("token")
    p = sub.add_parser("set-wallet")
    p.add_argument("account")
    p.add_argument("module")
    p.add_argument("amount")
    args = ap.parse_args(argv)
    return {"init": cmd_init, "import-json": cmd_import_json, "list": cmd_list,
            "issue": cmd_issue, "verify": cmd_verify, "set-wallet": cmd_set_wallet}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
