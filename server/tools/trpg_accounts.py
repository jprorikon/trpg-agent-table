#!/usr/bin/env python3
"""**account 憑證工具**（2026-10-07 設計者定案：account token，不是 seat token）。

    python3 tools/trpg_accounts.py init    --home modules/hyakki_tower/scenarios/sannenme_no_onibarai [--table hyakki_tower]
    python3 tools/trpg_accounts.py issue   --home modules/hyakki_tower/scenarios/sannenme_no_onibarai [--only acct_player1]
    python3 tools/trpg_accounts.py list    --home modules/hyakki_tower/scenarios/sannenme_no_onibarai
    python3 tools/trpg_accounts.py verify  --home modules/hyakki_tower/scenarios/sannenme_no_onibarai --token trpg_xxx
    python3 tools/trpg_accounts.py migrate --into <共用 registry> --home modules/hyakki_tower/scenarios/sannenme_no_onibarai \\
                                           --home modules/hyakki_tower/scenarios/sannenme_no_onibarai

模型（見 `docs/PLAN_account_model_v2.md`＋分層定案 `docs/PLAN_account_layering.md`）：

    account（**人級、跨桌唯一**：token／密碼／char 成長／貨幣錢包）
      └── sessions.<桌>.seats（**桌級**：他在這張桌坐哪一席、綁哪個 char）

* **registry 住哪**：`--registry <檔>` ＞ 該桌 `config.json` 的 `auth.accounts_file`
  （＝多張桌共用的那一份）＞ `<home>/accounts.json`（一桌一份的舊寫法）。
* **token 明文只發一次**：印在 stdout ＋寫到 `.secrets/trpg_accounts_<時間>_<帳號>.txt`（600）；
  registry **只存 sha256**，而且**不進版控**。
* `init` 從劇本的 `seats.json`（＋事件流現況）**播種**：人級條目（成長／錢包）**不覆蓋**已有的，
  只補這一桌的座次 ⇒ 同一張桌跑第二次不會把成長蓋掉。
* `migrate`：把各桌舊的 `accounts.json` 併進**共用檔**（人級 union、座次按桌搬），
  **token 的 sha256 原樣搬 ⇒ 已發出去的憑證不必重發**。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
import time

from accounts_common import (_hash, _load, _registry_path, _save, _sess, _sessions,
                             _state_growth, _state_star_coins, _table_id, _tables_of)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:            # 讀事件流折成長時要 import engine（工具從 tools/ 跑也要找得到）
    sys.path.insert(0, ROOT)

#: 需要播種的席位種類（gm／npc 也算 account——設計者：「把 agent 當作人類同級的存在」）
KINDS = ("human", "agent", "gm", "npc")








# --------------------------------------------------------------------------- 位置解析











# --------------------------------------------------------------------------- 種子





def cmd_init(args) -> int:
    home = os.path.abspath(args.home)
    path = _registry_path(args)
    table = _table_id(args, home)
    reg = _load(path)
    reg.setdefault("schema", 2)
    reg.setdefault("accounts", {})
    sess = _sess(reg, table)

    _overrides = {}
    for pair in (args.names or "").split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            _overrides[k.strip()] = v.strip()
    seats_doc = _load(os.path.join(home, "seats.json"))
    rows = seats_doc.get("seats") or []
    made = 0
    for row in rows:
        seat = str(row.get("seat") or "").strip()
        kind = str(row.get("kind") or "human")
        if not seat or kind not in KINDS:
            continue
        char = row.get("character")
        # account id：**由「誰」決定**（who 是這一席的身份；真人／agent 都一樣走這裡）。
        # ⚠ 設計者 2026-10-07 更正：現役桌 **p1＝player1、p2＝player2**（`seats.json` 的 who 是舊標籤）
        #   ⇒ 用 `--names "p1=player1,p2=player2"` 蓋掉（真值只認設計者）。
        who = str(_overrides.get(seat) or row.get("who") or seat).strip()
        acct_id = f"acct_{who}"
        reg["accounts"].setdefault(acct_id, {
            "display": who, "kind": kind, "chars": {}, "note": f"由 seats.json 的 {seat} 播種",
        })
        reg["accounts"][acct_id]["kind"] = kind
        if char:
            inst = reg["accounts"][acct_id]["chars"].setdefault(char, {})
            if not inst:
                g = _state_growth(home, seat)
                # 局外只放**成長**（level／exp）；★（`star`）是**局內**的，不播進實例。
                inst.update({k: v for k, v in g.items() if k in ("level", "exp")})
                inst["note"] = "跨局成長（★／道具／記憶是局內；貨幣是 account 層的錢包）"
                made += 1
            _coins = _state_star_coins(home, seat)
            if _coins:
                w = reg["accounts"][acct_id].setdefault("wallet", {})
                w["star_coins"] = max(int(w.get("star_coins") or 0), int(_coins))
        # 人類入口：**帳號＋密碼**（設計者 2026-10-07）——沒有的話自動配一組（密碼只在生成時印一次）
        if kind == "human" and not (reg["accounts"][acct_id].get("login") or {}).get("password_sha256"):
            _u = who
            _pw = secrets.token_urlsafe(9)
            reg["accounts"][acct_id]["login"] = {"username": _u, "password_sha256": _hash("pw:" + _pw)}
            reg["accounts"][acct_id]["_plain_pw"] = _pw           # 只在這一輪印出／寫檔，之後可刪
        sess["seats"][seat] = {"account": acct_id, "char": char}
    _save(path, reg)
    _plain = [(k, (v.get("login") or {}).get("username"), v.get("_plain_pw"))
              for k, v in sorted(reg["accounts"].items()) if v.get("_plain_pw")]
    if _plain:
        print("🔑 人類入口（帳號／密碼；**只在這裡印一次**）：")
        for k, u, pw in _plain:
            print(f"   {k:22s} username={u}  password={pw}")
    print(f"✅ {path}（table={table}）")
    for seat, b in sorted(sess["seats"].items()):
        print(f"   {seat} ← {b['account']}（char={b['char']}）")
    print(f"   播種了 {made} 個 char 實例的成長；已有 token 的帳號："
          f"{sorted(k for k, v in reg['accounts'].items() if v.get('token_sha256'))}")
    print("   下一步：`issue` 發憑證（明文只顯示一次）")
    return 0


def cmd_issue(args) -> int:
    path = _registry_path(args)
    reg = _load(path)
    if not reg.get("accounts"):
        print("✗ 還沒有 registry——先跑 `init`", file=sys.stderr)
        return 2
    only = [x for x in (args.only or "").split(",") if x]
    issued = []
    for aid, a in sorted(reg["accounts"].items()):
        if only and aid not in only:
            continue
        tok = f"trpg_{aid}_{secrets.token_hex(16)}"
        a["token_sha256"] = _hash(tok)
        a["token_issued_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        issued.append((aid, a.get("display"), a.get("kind"), tok))
    if not issued:
        print("（沒有要發的帳號）")
        return 0
    _save(path, reg)
    outdir = os.path.join(ROOT, ".secrets")
    os.makedirs(outdir, exist_ok=True)
    # ⚠ 2026-10-07 踩到：檔名只到「秒」⇒ **同一秒連發兩次會覆寫**（先發 A、再發 B ⇒ 協作端 的明文沒了，
    #    而 registry 只留 sha256 ⇒ 那把憑證救不回來）。⇒ 檔名帶「發了哪些帳號」＋撞名自動加序號。
    _ids = [str(x[0]) if isinstance(x, (tuple, list)) else str(x) for x in issued]   # issued 是 tuple 串
    _tag = "+".join(sorted(_ids)) if len(_ids) <= 4 else "batch"
    _stem = f"trpg_accounts_{time.strftime('%Y%m%d-%H%M%S')}_{_tag}"
    outp = os.path.join(outdir, _stem + ".txt")
    _n = 1
    while os.path.exists(outp):
        _n += 1
        outp = os.path.join(outdir, f"{_stem}-{_n}.txt")
    with open(outp, "w", encoding="utf-8") as fh:
        fh.write(f"# TRPG account 憑證（registry={reg.get('schema') and path}）"
                 f"　發放 {time.strftime('%Y-%m-%d %H:%M')}\n")
        fh.write("# 一人一行：帳號｜身份｜哪幾張桌的哪些席｜token（**只發一次**，registry 只存 sha256）\n")
        fh.write("# agent＝用 Authorization: Bearer *** 連 MCP；人類＝login UI 貼同一把\n\n")
        for aid, name, kind, tok in issued:
            fh.write(f"{aid} | {kind} | {name} | tables={','.join(_tables_of(reg, aid)) or '（還沒配席）'}\n")
            fh.write(f"   {tok}\n\n")
    os.chmod(outp, 0o600)
    print(f"✅ 發了 {len(issued)} 把憑證（registry 只存 sha256）")
    print(f"   明文寫在：{outp}（權限 600，**不進版控**）")
    for aid, name, kind, _tok in issued:
        print(f"   - {aid:22s} {kind:6s} {name}　tables={('/'.join(_tables_of(reg, aid)) or '（還沒配席）')}")
    return 0


def cmd_passwords(args) -> int:
    """看／重設人類入口的帳號密碼（`--reset` ＝ 重配一組並印出；`--only acct_x` 只動那幾個）。"""
    path = _registry_path(args)
    reg = _load(path)
    if not reg.get("accounts"):
        print("✗ 還沒有 registry——先跑 `init`", file=sys.stderr)
        return 2
    only = [x for x in (args.only or "").split(",") if x]
    out = []
    for aid, a in sorted(reg["accounts"].items()):
        if only and aid not in only:
            continue
        if str(a.get("kind")) != "human" and not args.all:
            continue
        lg = a.get("login") or {}
        if args.reset or not lg.get("username") or not lg.get("password_sha256"):
            _u = lg.get("username") or a.get("display") or aid
            _pw = args.password or secrets.token_urlsafe(9)
            a["login"] = {"username": _u, "password_sha256": _hash("pw:" + _pw)}
            out.append((aid, _u, _pw, True))
        else:
            out.append((aid, lg.get("username"), None, False))
    _save(path, reg)
    for aid, u, pw, isnew in out:
        print(f"{aid:22s} username={u:16s} " +
              (f"password={pw}  ← 剛重設（舊的失效）" if isnew else "password=（不顯示；要換就 --reset）"))
    return 0


def cmd_list(args) -> int:
    path = _registry_path(args)
    reg = _load(path)
    if not reg:
        print("（還沒有 registry）")
        return 0
    print(f"registry={path}")
    tables = sorted(_sessions(reg))
    print(f"桌（{len(tables)}）：{', '.join(tables) or '—'}")
    for aid, a in sorted(reg["accounts"].items()):
        seats = []
        for t in _tables_of(reg, aid):
            ss = sorted(s for s, b in (_sess(reg, t).get("seats") or {}).items()
                        if (b or {}).get("account") == aid)
            seats.append(f"{t}:{'/'.join(ss)}")
        chars = ",".join(sorted(a.get("chars") or {})) or "—"
        print(f"  {aid:24s} {str(a.get('kind')):6s} {str(a.get('display')):16s} "
              f"seat={' '.join(seats) or '—':26s} char={chars:22s} "
              f"wallet={int((a.get('wallet') or {}).get('star_coins') or 0):4d} "
              f"token={'✓' if a.get('token_sha256') else '✗'}")
    return 0


def cmd_verify(args) -> int:
    path = _registry_path(args)
    reg = _load(path)
    h = _hash(args.token)
    for aid, a in (reg.get("accounts") or {}).items():
        if a.get("token_sha256") and secrets.compare_digest(a["token_sha256"], h):
            tables = _tables_of(reg, aid)
            seats = {t: sorted(s for s, b in (_sess(reg, t).get("seats") or {}).items()
                               if (b or {}).get("account") == aid) for t in tables}
            print(json.dumps({"account": aid, "display": a.get("display"), "kind": a.get("kind"),
                              "tables": tables, "seats": seats,
                              "chars": sorted(a.get("chars") or {}),
                              "wallet": a.get("wallet") or {}}, ensure_ascii=False))
            return 0
    print("✗ 這把 token 不在 registry 裡（或已換發）", file=sys.stderr)
    return 1


def cmd_migrate(args) -> int:
    """把各桌舊的 `accounts.json` 併進**共用 registry**（人級 union、座次按桌搬）。

    **token 的 sha256 原樣搬 ⇒ 已發出去的憑證不必重發**（協作端／p4／player1 的照樣有效）。
    人級衝突的取法（都寫在下面的註解裡）：`chars` 同名取成長較高者、`wallet` 取較大者、
    `login`／`token_sha256` 以**已有的**為準（`setdefault`）。
    """
    dest = os.path.abspath(os.path.expanduser(args.into))
    reg = _load(dest)
    reg.setdefault("schema", 2)
    reg.setdefault("accounts", {})
    reg.setdefault("sessions", {})
    for home in args.home:
        home = os.path.abspath(home)
        src = _load(os.path.join(home, "accounts.json"))
        if not src:
            print(f"（跳過 {home}：沒有 accounts.json）")
            continue
        tbl = str((src.get("session") or {}).get("table") or _table_id(args, home))
        n_acc = len(src.get("accounts") or {})
        for aid, a in (src.get("accounts") or {}).items():
            dst = reg["accounts"].setdefault(aid, {})
            for k, v in (a or {}).items():
                if k == "chars":
                    dch = dst.setdefault("chars", {})
                    for ch, inst in (v or {}).items():
                        cur = dch.get(ch)
                        if not cur:
                            dch[ch] = dict(inst or {})
                            continue
                        ni, ci = (inst or {}), cur
                        if (int(ni.get("level") or 1), int(ni.get("exp") or 0)) > \
                           (int(ci.get("level") or 1), int(ci.get("exp") or 0)):
                            ci.update({k2: ni[k2] for k2 in ("level", "exp") if k2 in ni})
                        if tbl not in (ci.setdefault("merged_from", [])):
                            ci["merged_from"].append(tbl)
                elif k == "wallet":
                    dw = dst.setdefault("wallet", {})
                    dw["star_coins"] = max(int(dw.get("star_coins") or 0),
                                           int((v or {}).get("star_coins") or 0))
                elif k == "sessions":            # 人級底下那把一把的 session token（登入發的）
                    ds = dst.setdefault("sessions", {})
                    for h2, info in (v or {}).items():
                        ds.setdefault(h2, info)
                elif k == "_plain_pw":
                    continue                     # 明文密碼副本不搬（交接檔才有）
                else:
                    dst.setdefault(k, v)
        # 座次：整格搬進 sessions[<該桌>]
        old = src.get("session") or {}
        tgt = reg["sessions"].setdefault(tbl, {"table": tbl, "seats": {}})
        tgt["table"] = tbl
        tgt.setdefault("seats", {})
        for seat, b in (old.get("seats") or {}).items():
            tgt["seats"].setdefault(seat, b)
        for k in ("written_back", "wallet_carried"):
            if k in old:
                tgt.setdefault(k, old[k])
        print(f"併入 {home}（table={tbl}：{len(old.get('seats') or {})} 席／{n_acc} 帳號）")
    _save(dest, reg)
    print(f"✅ 共用 registry：{dest}（桌：{', '.join(sorted(reg['sessions']))}；"
          f"帳號 {len(reg['accounts'])}）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="TRPG account 憑證工具")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("init", cmd_init), ("issue", cmd_issue), ("list", cmd_list),
                     ("verify", cmd_verify), ("passwords", cmd_passwords),
                     ("migrate", cmd_migrate)):
        p = sub.add_parser(name)
        if name == "migrate":
            p.add_argument("--into", required=True, help="共用 registry 檔（會被寫入）")
            p.add_argument("--home", action="append", required=True,
                           help="要併進來的劇本目錄（可重複）")
            p.add_argument("--table", default=None, help="蓋掉 table id（少用）")
        else:
            p.add_argument("--home", required=True, help="桌的家目錄（modules/<模組>/scenarios/<劇本>）")
        p.add_argument("--registry", default=None,
                       help="registry 檔（省略＝該桌 config 的 auth.accounts_file，再沒有＝<home>/accounts.json）")
        if name == "init":
            p.add_argument("--table", default=None)
            p.add_argument("--names", default=None,
                           help='蓋掉 seats.json 的 who 標籤，例 "p1=player1,p2=player2"')
        if name == "issue":
            p.add_argument("--only", default=None, help="只發這幾個帳號（逗號分隔）")
        if name == "verify":
            p.add_argument("--token", required=True)
        if name == "passwords":
            p.add_argument("--reset", action="store_true", help="重配一組並印出（舊的即刻失效）")
            p.add_argument("--only", default=None, help="只動這幾個帳號（逗號分隔）——只動某一個帳號、不要碰別人時用")
            p.add_argument("--all", action="store_true", help="連 agent／gm／npc 帳號也一起看")
            p.add_argument("--password", default=None, help="指定密碼（省略＝亂數）")
        p.set_defaults(fn=fn)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
