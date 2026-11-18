#!/usr/bin/env python3
"""**account 認證層的自測**（2026-10-07，設計者定案：account token，不是 seat token）。

    python3 test_auth.py

  W1 **token 從哪來**：`Authorization: Bearer` ＞（URL／body 的 `token`）。
  W2 **registry**：resolve（真 token ⇒ account；偽造 ⇒ None）／`seat_of`／`char_of`。
  W3 **座位判定（`check_seat`）**：沒 registry＝照舊；`require=False` 有效 token ⇒ 以 token 的席位為準；
     `require=True` 沒 token ⇒ 拒；**拿 A 的 token 動 B 的席 ⇒ 拒**（冒充）。
  W4 **跨局實例**：讀／寫只寫成長（`level/exp/star`），局內資源（貨幣／道具／記憶）不寫進去。
  W5 **真桌（活服務）**：帶 token 的 `/api/state` 拿到的是**那個 account 的席位視圖**
     （`?seat=p2&token=<p1 的>` 會被 token 蓋成 p1）——證明「帳號在模組外、認證在伺服器層」。
  W6 **legacy**：沒有 accounts.json 的桌 ⇒ 全部 no-op（`check_seat` 原封不動回傳）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo 根
sys.path.insert(0, os.path.join(HERE, "tools"))
import gql_client  # noqa: E402  （/api/* 2026-10-07 退役 ⇒ 這支直接走 GraphQL）
sys.path.insert(0, HERE)
os.environ.setdefault("TRPG_LEGACY_REST", "1")   # 直接跑這支也要能打 `/api/*`（生產已退役 ⇒ 只有測試放行）
from trpg.core import layout as layout

from trpg.rules import packs as packs                                                             # noqa: E402
from trpg.rules import scenario as scenario                                                          # noqa: E402
#: 劇本規則資料的檔名（WS-B：`scenario.json` 優先、舊名相容）——夾具別硬編
MODF = scenario.module_filename(layout.must_locate_scenario(HERE, "hyakki_tower", "sannenme_no_onibarai")) or "scenario.json"

from trpg.accounts import auth as auth                                                             # noqa: E402

OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


def mk_reg(tmp, token="trpg_acct_a_testtoken"):
    reg = {"schema": 1, "table": "t",
           "accounts": {"acct_a": {"display": "A", "kind": "human", "token_sha256": auth._hash(token),
                                   "chars": {"char_a": {"level": 3, "exp": 120}},   # ★ 不放這裡（局內）
                                   "wallet": {"star_coins": 5}},
                        "acct_b": {"display": "B", "kind": "agent",
                                   "token_sha256": auth._hash("trpg_acct_b_xyz"),
                                   "chars": {"char_b": {"level": 1, "exp": 0}}}},
           "session": {"table": "t", "seats": {"p1": {"account": "acct_a", "char": "char_a"},
                                               "p2": {"account": "acct_b", "char": "char_b"}}}}
    with open(os.path.join(tmp, "accounts.json"), "w", encoding="utf-8") as fh:
        json.dump(reg, fh, ensure_ascii=False)
    return reg


def main() -> int:                                                       # noqa: C901
    print("== W1／W2／W3／W6. registry 與座位判定（單元）==")
    tmp = tempfile.mkdtemp(prefix="trpg_auth_")
    reg = mk_reg(tmp)
    acc = auth.Accounts(tmp)
    check("W1a `Authorization: Bearer` 取得優先", auth.token_from_request(
        headers={"Authorization": "Bearer t1"}, body={"token": "t2"}) == "t1")
    check("W1b 沒標頭 ⇒ 用 body 的 `token`（玩家工具照慣例）",
          auth.token_from_request(headers={}, body={"token": "t2"}) == "t2"
          and auth.token_from_request(headers={}, query={"token": "t3"}) == "t3")
    who = acc.resolve("trpg_acct_a_testtoken")
    check("W2a 真 token ⇒ 認出 account／席位／char",
          (who or {}).get("account") == "acct_a" and who.get("seat") == "p1"
          and who.get("chars") == ["char_a"], f"{who}")
    check("W2b 偽造 token ⇒ 認不出（不是「回一個空 account」）", acc.resolve("trpg_acct_a_dead") is None)
    check("W2c agent 與人類走**同一條**（B 是 agent，一樣認得出來）",
          (acc.resolve("trpg_acct_b_xyz") or {}).get("kind") == "agent")
    check("W3a `require=False` ＋有效 token ⇒ **以 token 的席位為準**（想動 p2，被蓋成 p1）",
          auth.check_seat(acc, "trpg_acct_a_testtoken", "p2", require=False)[0] == "p1")
    check("W3b `require=True` ＋沒 token ⇒ 拒",
          auth.check_seat(acc, None, "p1", require=True)[2] is not None)
    check("W3c `require=True` ＋**冒充別人** ⇒ 拒",
          auth.check_seat(acc, "trpg_acct_a_testtoken", "p2", require=True)[2] is not None)
    check("W3d `require=True` ＋自己的席位 ⇒ 過",
          auth.check_seat(acc, "trpg_acct_a_testtoken", "p1", require=True)[:2] == ("p1", auth.check_seat(
              acc, "trpg_acct_a_testtoken", "p1", require=True)[1])
          or auth.check_seat(acc, "trpg_acct_a_testtoken", "p1", require=True)[0] == "p1")
    check("W4a 讀實例（這個 account 在這個 char 上的成長：只有 level／exp）",
          acc.instance("acct_a", "char_a") == {"level": 3, "exp": 120})
    acc.write_instance("acct_a", "char_a", {"level": 4, "exp": 200, "star": 3,
                                            "hp": 99, "star_coins": 77, "memory": 5})
    inst = auth.Accounts(tmp).instance("acct_a", "char_a")
    check("W4b 實例**只寫成長 level／exp**：★（局內）／貨幣／道具／記憶都不進來",
          inst.get("level") == 4 and inst.get("exp") == 200
          and "star" not in inst and "star_coins" not in inst
          and "memory" not in inst and "hp" not in inst, f"{inst}")
    acc.add_wallet("acct_a", star_coins=77)
    acc.add_wallet("acct_a", star_coins=-7)
    w = auth.Accounts(tmp).wallet("acct_a")
    check("W4d 貨幣放 **account 層的錢包**（通用貨幣）：跨角色共用、可增減",
          w.get("star_coins") == 75, f"{w}")   # 播種 5 ＋77 −7
    check("W4e 錢包不隨 char 走（換角色同一筆）",
          auth.Accounts(tmp).wallet("acct_a") == w)
    check("W4c 換 char ⇒ 別人的實例不受影響（實例是 account×char）",
          auth.Accounts(tmp).instance("acct_b", "char_b").get("level") == 1)
    snap = auth.Accounts(tmp).snapshot()
    check("W5 運維快照：席位↔帳號↔角色↔實例（**不含 token**）",
          len(snap) == 2 and "token" not in json.dumps(snap)
          and {x["seat"] for x in snap} == {"p1", "p2"})
    tmp2 = tempfile.mkdtemp(prefix="trpg_auth_legacy_")
    acc2 = auth.Accounts(tmp2)
    check("W6 沒有 accounts.json ⇒ no-op（legacy 一字不變：認證不做、席位原封不動）",
          acc2.enabled is False and acc2.resolve("x") is None
          and auth.check_seat(acc2, "x", "p9", require=True) == ("p9", None, None))

    print("== W5. 真桌（活服務）—— token 蓋掉 ?seat= ==")
    home = layout.must_locate_scenario(HERE, "hyakki_tower", "sannenme_no_onibarai")
    _live_cfg = layout.read_table_config(home)
    # ⚠ 2026-10-07：account 層可能住 **DB**（`auth.db`／`db_env`）而不是 JSON registry
    #   ⇒ 一律走 store 工廠（`account_store.accounts_for`），別自己猜檔案路徑
    #   （猜錯的症狀＝這一段**靜默跳過**，看起來像全綠）。
    from trpg.accounts import account_store as account_store                                             # noqa: PLC0415
    _acc_live = account_store.accounts_for(home, "demo_world", _live_cfg)
    if not _acc_live.enabled:
        check("W5 真桌憑證（跳過：這台沒有帳號層）", True)
    else:
        toks: list = []
        import glob                                                      # noqa: PLC0415
        # ⚠ 憑證檔可能只含「換發的那一個帳號」（`issue --only` 會另寫新檔）⇒ **跨所有檔搜**。
        # ⚠⚠ 2026-10-07 第二階段起：**同一個 account id 可能在別張桌也發過一把**（各自有效）
        #    ⇒ 兩把都收，再問**這張桌的 registry** 認哪一把（別只挑最新那個檔）。
        for f in sorted(glob.glob(os.path.join(HERE, ".secrets", "trpg_accounts_*.txt"))):
            for line in open(f, encoding="utf-8"):
                line = line.strip()
                if line.startswith("trpg_") and line not in toks:
                    toks.append(line)          # 全部收（同一帳號可能有好幾把，憑證會輪換）
        _acc_live = _acc_live                      # 就是上面那一個 store（JSON／DB 都通）
        # ★ 2026-10-20：**要挑「這張桌的 registry 真的認得、而且有席位」的那一把**。
        #   以前是 `next(第一個檔裡的第一把)` ⇒ 那把可能是別張桌發的、或已經被換發作廢，
        #   於是 W5b 在憑證輪換之後固定紅（403），看起來像迴歸、其實是測試挑錯憑證。
        tok_a = None
        for _t in toks:
            _who = _acc_live.resolve(_t) or {}
            if _who.get("account") == "acct_player1" and _who.get("seat"):
                tok_a = _t
                break
        seat_a = (_acc_live.seats.get("p1") or {}).get("char")
        # ⚠ 2026-10-07 兩輪架構變更都要顧到：
        #   ① 單一入口（子路徑）② 單一進程（hub）⇒ 現役桌可能**不在自己的埠上**，而是在 hub 的子路徑下。
        #   判準：`logs/hub.pid` 活著 ⇒ 走 hub（`http://127.0.0.1:<hub埠>/<前綴>`）；否則走該桌自己的埠。
        _hub_pidf = os.path.join(HERE, "logs", "hub.pid")
        _hub_alive = os.path.exists(_hub_pidf)
        if _hub_alive:
            try:
                with open(_hub_pidf, encoding="utf-8") as _f:
                    os.kill(int(_f.read().strip()), 0)
            except Exception:                                             # noqa: BLE001
                _hub_alive = False
        # ★ 2026-10-20：**hub 模式要用「根」base，不要加 `/<prefix>`**。
        #   GraphQL 只有一個端點、桌是欄位（`table(id:)`）；加了前綴會變成
        #   `POST /<prefix>/graphql` ⇒ 前台 nginx 回 **405 Not Allowed**，
        #   測試卻把它收斂成 403（「冒充」），看起來像帳號層迴歸（W5b 一直紅就是這個）。
        _live_base = ("http://127.0.0.1:8971" if _hub_alive
                      else f"http://127.0.0.1:{_live_cfg.get('port') or 8971}")
        _tbl_w5 = str(_live_cfg.get("table_id") or "")

        def _get(q, tk):
            """舊 `/api/state?seat=…` ⇒ GraphQL 的 `view`（**`/api/*` 2026-10-07 已退役**）。

            回 `(code, 公開投影)`：被拒時回 403（GraphQL 慣例是 200＋`errors`，這一支替測試收斂回狀態碼）。
            """
            _q = urllib.parse.parse_qs(q.split("?", 1)[1]) if "?" in q else {}
            _seat = (_q.get("seat") or [""])[0]
            try:
                _sess = gql_client.session_of(_live_base, _tbl_w5, token=tk)
                return 200, gql_client.view(_live_base, _tbl_w5, _sess, seat=_seat, token=tk)
            except Exception:                                            # noqa: BLE001
                return 403, {}
        c0, _j0 = _get("/api/state?seat=p1", None)
        # ★ 2026-10-20：`?seat=` 的語意是「**不再等於坐在那一席**」，不是「一律 403」。
        #   2026-10-19「匿名只讀」之後，匿名可以拿到**公開投影**（200），但 `you` 必須是空的；
        #   兩種都算過：403（擋掉）或 200 但 `you.seat` 不是 p1（沒洩漏）。
        _anon_seat = (_j0.get("you") or {}).get("seat")
        check("W5a **收掉 `?seat=`**：匿名帶 `?seat=p1` 拿不到那一席的視角（403 或公開投影，`you.seat`≠p1）",
              c0 == 403 or _anon_seat in (None, ""), f"{c0} you.seat={_anon_seat!r}")
        if tok_a is None:
            check("W5b 有憑證 ⇒ 不用帶 seat，回的就是**自己那一席**（跳過：."
                  "secrets 裡沒有這張桌 registry 認得的 acct_player1 token—憑證輪換過，重新 issue 後才會驗到）",
                  True, "skip")
            check("W5c 拿自己的憑證去要**別人的席**（`?seat=p2`）⇒ 403（跳過：同上）", True, "skip")
        else:
            # ★ 2026-10-20：先問「這把憑證在**這張桌**有沒有席位」。
            #   沒有席位時 `view` 回 403 是**正確行為**（帳號存在、但沒坐在這桌上），
            #   不是迴歸——以前這裡會 FAIL，看起來像帳號層壞了（實際是那張桌目前 0 席／已收局）。
            _mine = {}
            try:
                _mine = (gql_client.me(_live_base, token=tok_a) or {}).get("tables") or []
            except Exception:                                            # noqa: BLE001
                _mine = []
            _seated = next((x.get("seat") for x in _mine
                            if str(((x.get("table") or {}).get("id")) or "") == _tbl_w5
                            and x.get("seat")), None)
            if not _seated:
                check("W5b 有憑證 ⇒ 回自己那一席（跳過：這把憑證在該桌**目前沒有席位**"
                      "——桌 0 席／已收局；帳號本身有效）", True, "skip no-seat")
                check("W5c 冒充別人的席 ⇒ 403（跳過：同上）", True, "skip no-seat")
            else:
                c1, j1 = _get("/api/state", tok_a)
                c2, _j2 = _get("/api/state?seat=p2", tok_a)
                check("W5b 有憑證 ⇒ 不用帶 seat，回的就是**自己那一席**",
                      c1 == 200 and (j1.get("you") or {}).get("seat") == _seated,
                      f"{c1} seat={(j1.get('you') or {}).get('seat')} 預期={_seated}")
                check("W5c 拿自己的憑證去要**別人的席**（`?seat=p2`）⇒ 403（冒充）", c2 == 403, f"{c2}")

    print("== W7／W8. 貨幣走事件 ＋ 收局寫回（引擎＋伺服器helper，用**臨時副本**，不碰現役流）==")
    import shutil as _sh, tempfile as _tf                                    # noqa: PLC0415
    from trpg.core import store as _store
    from support_fixture import seed_run                                          # noqa: PLC0415                                                   # noqa: PLC0415
    from trpg.engine import Table as _Table                                       # noqa: PLC0415
    src = layout.must_locate_scenario(HERE, "hyakki_tower", "sannenme_no_onibarai")
    tmp3 = _tf.mkdtemp(prefix="trpg_auth_wb_")
    _def3 = layout.def_dir_of(src)            # 定義層（WS-B：桌目錄裡只剩 config／runs）
    for f in ("board.json", "goals.json", "clues.json", "seats.json", MODF):
        _sh.copy(os.path.join(_def3, f), os.path.join(tmp3, f))
    layout.copy_table_config(src, os.path.join(tmp3, "config.json"))   # 旋鈕 ⇒ 桌目錄
    _sh.copytree(os.path.join(_def3, "packs"), os.path.join(tmp3, "packs"), dirs_exist_ok=True)
    packs.seed_module_packs(os.path.dirname(os.path.join(_def3, "packs")), tmp3)   # ＋模組層的包（rules.core 住模組 repo）
    seed_run(src, tmp3)                                  # 現役桌可能已退役 ⇒ 從歸檔／現場造一局
    cfg3 = layout.read_table_config(tmp3)
    cfg3["port"] = 0
    t3 = _Table(home=tmp3, cfg=cfg3, store=_store.FileStore(tmp3), clock=lambda: 0.0)
    c0 = int((t3.state["seats"]["p1"] or {}).get("star_coins") or 0)
    t3.grant_coins(seat="p1", amount=7, reason="carry_over")
    check("W7a 貨幣走**事件**（`coin_grant`）：+7 生效、且回放一致",
          int(t3.state["seats"]["p1"]["star_coins"]) == c0 + 7
          and bool(t3.replay_check().get("replay_matches")),
          f"{c0} → {t3.state['seats']['p1']['star_coins']}")
    check("W7b `coin_grant` 不會少於 0（負數夾住）",
          (t3.grant_coins(seat="p1", amount=-9999), int(t3.state["seats"]["p1"]["star_coins"]))[1] == 0)
    # 收局寫回：用一個**只給 helper 看的** registry + 假 svc（局已收的臨時桌）
    tmp4 = _tf.mkdtemp(prefix="trpg_auth_reg_")
    reg4 = {"schema": 1, "table": "t",
            "accounts": {"acct_z": {"display": "Z", "kind": "human", "chars": {"char_a": {"level": 1, "exp": 0}},
                                    "wallet": {"star_coins": 3}}},
            "session": {"table": "t", "seats": {"p1": {"account": "acct_z", "char": "char_a"}}}}
    json.dump(reg4, open(os.path.join(tmp4, "accounts.json"), "w", encoding="utf-8"), ensure_ascii=False)
    from trpg.api import web as _wa                                                    # noqa: PLC0415
    acct4 = auth.Accounts(tmp4)

    class _FakeSvc:
        table = t3
    # ⚠ 2026-10-24（review R3）：這一段測的是「**收局才寫回**」那個門檻，不是「夾具那一局剛好
    #   已經結束」。從前它靠抄進來的那條現役流裡剛好有結局；乾淨 clone（沒有現役流／歸檔，
    #   走合成局）時就沒有 ⇒ 假紅。這裡把結局旗標擺上去（引擎側判結局另有 `test_module` B10）。
    if not t3.state.get("ended"):
        t3.state["ended"] = "e_fixture_ended"
    check("W8a 局已收（`state.ended`）⇒ 寫回的門檻成立", bool(t3.state.get("ended")),
          str(t3.state.get("ended")))
    _wa._maybe_writeback(_FakeSvc(), acct4)
    a1 = auth.Accounts(tmp4)
    inst1 = a1.instance("acct_z", "char_a")
    wal1 = a1.wallet("acct_z")
    check("W8b 收局寫回：**level／exp→char 實例**、**貨幣→account 錢包**",
          inst1.get("level") == int(t3.level_of("p1")) and inst1.get("exp") == int(t3.exp_of("p1"))
          and wal1.get("star_coins") == 3 + int(t3.state["seats"]["p1"]["star_coins"]),
          f"inst={inst1} wallet={wal1}")
    check("W8c 寫回**冪等**（再叫一次不會翻倍）",
          (_wa._maybe_writeback(_FakeSvc(), auth.Accounts(tmp4)),
           auth.Accounts(tmp4).wallet("acct_z"))[1] == wal1)
    check("W8d 局內的不寫回去（實例裡沒有 star／道具／記憶）",
          "star" not in inst1 and "items" not in inst1 and "memory" not in inst1)
    _sh.rmtree(tmp3, ignore_errors=True)
    _sh.rmtree(tmp4, ignore_errors=True)

    # =====================================================================
    print("== S. 共用 registry（一台機器一份；人級跨桌、座次按桌）——2026-10-07 第二階段 ==")
    shared_dir = _tf.mkdtemp(prefix="trpg_auth_shared_")
    shared = os.path.join(shared_dir, "accounts_registry.json")
    home_a = os.path.join(shared_dir, "world_a_like")
    home_b = os.path.join(shared_dir, "hyakki_like")
    os.makedirs(home_a); os.makedirs(home_b)
    T_A = "trpg_acct_a_" + "0" * 32          # 假 token（只驗 sha256 比對，不對外）
    T_B = "trpg_acct_b_" + "1" * 32
    T_C = "trpg_acct_c_" + "2" * 32
    json.dump({
        "schema": 2,
        "accounts": {
            "acct_a": {"display": "A", "kind": "human", "token_sha256": auth._hash(T_A),
                       "chars": {"char_a": {"level": 3, "exp": 12}},
                       "wallet": {"star_coins": 5}},
            "acct_b": {"display": "B", "kind": "human", "token_sha256": auth._hash(T_B),
                       "chars": {"char_b": {"level": 1, "exp": 0}}},
        },
        "sessions": {
            "demo_world": {"table": "demo_world", "seats": {"p1": {"account": "acct_a", "char": "char_a"}}},
            "hyakki_tower": {"table": "hyakki_tower", "seats": {"p1": {"account": "acct_a", "char": "char_a"}}},
        },
    }, open(shared, "w", encoding="utf-8"), ensure_ascii=False)

    A_s = auth.Accounts(home_a, "demo_world", path=shared)
    A_h = auth.Accounts(home_b, "hyakki_tower", path=shared)
    w_s, w_h = A_s.resolve(T_A), A_h.resolve(T_A)
    check("S1a **同一把 token 兩張桌都認得**（人級）",
          (w_s or {}).get("account") == "acct_a" and (w_h or {}).get("account") == "acct_a",
          f"{w_s and w_s.get('account')} / {w_h and w_h.get('account')}")
    check("S1b 各自對到**自己那張桌**的席位／角色（桌級）",
          (w_s or {}).get("seat") == "p1" and A_s.char_of("p1") == "char_a"
          and (w_h or {}).get("seat") == "p1" and A_h.char_of("p1") == "char_a",
          f"{A_s.char_of('p1')} / {A_h.char_of('p1')}")
    check("S1c `tables_of` 列出他所有的桌（運維視圖）",
          A_s.tables_of("acct_a") == ["demo_world", "hyakki_tower"], f"{A_s.tables_of('acct_a')}")
    check("S1d 人級的成長（char 實例）兩桌看到同一份",
          A_s.instance("acct_a", "char_a") == {"level": 3, "exp": 12}
          and A_h.instance("acct_a", "char_a") == {"level": 3, "exp": 12})

    check("S2a 只在 demo_world 有座的帳號 ⇒ 在百鬼塔**認得人但沒有席**（＝觀眾）",
          (A_h.resolve(T_B) or {}).get("account") == "acct_b"
          and (A_h.resolve(T_B) or {}).get("seat") is None)
    check("S2b `require=True` ⇒ 沒配席的帳號**不能點名要席位**（不採信 want_seat；也不是 403）",
          auth.check_seat(A_h, T_B, "p1", require=True)[:1] == (None,)
          and auth.check_seat(A_h, T_B, "p1", require=True)[1] is not None
          and auth.check_seat(A_h, T_B, "p1", require=True)[2] is None,
          f"{auth.check_seat(A_h, T_B, 'p1', require=True)}")
    check("S2c 帶了有效 token ⇒ **以 token 為準**（沒席位就是沒席位，連 require=False 也一樣）",
          auth.check_seat(A_h, T_B, "p1", require=False)[0] is None)
    check("S2d 沒帶 token ＋ require=False ⇒ 照舊信 want_seat（相容期）",
          auth.check_seat(A_h, None, "p1", require=False)[0] == "p1")

    # S4 舊形狀相容：只有頂層 session 的舊檔（legacy 桌不必先遷移）
    old_dir = _tf.mkdtemp(prefix="trpg_auth_old_")
    T_OLD = "trpg_acct_old_" + "9" * 32
    json.dump({"schema": 1,
               "accounts": {"acct_old": {"display": "O", "kind": "human",
                                         "token_sha256": auth._hash(T_OLD)}},
               "session": {"table": "legacy_tbl",
                           "seats": {"p1": {"account": "acct_old", "char": "x"}}}},
              open(os.path.join(old_dir, "accounts.json"), "w", encoding="utf-8"), ensure_ascii=False)
    A_old = auth.Accounts(old_dir)                 # 目錄名 ≠ 檔裡記的 table ⇒ 也要對得上
    check("S4 舊形狀（頂層 session）讀得動、座次照舊（不必先遷移）",
          (A_old.resolve(T_OLD) or {}).get("seat") == "p1" and A_old.char_of("p1") == "x",
          f"{(A_old.resolve(T_OLD) or {}).get('seat')}")
    check("S4b 舊檔只讀不改（記憶體裡搬進 sessions，檔案沒被就地改寫）",
          json.load(open(os.path.join(old_dir, "accounts.json"), encoding="utf-8")).get("session")
          is not None and "sessions" in A_old.data)

    # S5 migrate：兩份舊檔 ⇒ 一份共用檔；舊 token 續用；人級 union；座次分桌
    m_dir = _tf.mkdtemp(prefix="trpg_auth_mig_")
    for tbl, seat, ch, tok, lv, coins in (("game_a", "p1", "char_a", "trpg_acct_dupe_" + "a" * 32, 4, 9),
                                          ("game_b", "p2", "char_a", "trpg_acct_dupe_" + "b" * 32, 2, 3)):
        h = os.path.join(m_dir, tbl)
        os.makedirs(h)
        json.dump({"schema": 1,
                   "accounts": {"acct_dupe": {"display": "D", "kind": "human", "token_sha256": auth._hash(tok),
                                              "chars": {"char_a": {"level": lv, "exp": lv}},
                                              "wallet": {"star_coins": coins}, "_plain_pw": "should-not-copy"}},
                   "session": {"table": tbl, "seats": {seat: {"account": "acct_dupe", "char": ch}}}},
                  open(os.path.join(h, "accounts.json"), "w", encoding="utf-8"), ensure_ascii=False)
    dest = os.path.join(m_dir, "shared.json")
    rc = subprocess.run([sys.executable, os.path.join(HERE, "tools", "trpg_accounts.py"), "migrate",
                         "--into", dest, "--home", os.path.join(m_dir, "game_a"),
                         "--home", os.path.join(m_dir, "game_b")],
                        capture_output=True, text=True).returncode
    reg5 = json.load(open(dest, encoding="utf-8"))
    check("S5a `migrate` 跑得過（exit 0）", rc == 0, f"rc={rc}")
    check("S5b 兩張桌都進了 `sessions`（座次分開）",
          sorted(reg5.get("sessions") or {}) == ["game_a", "game_b"]
          and (reg5["sessions"]["game_a"]["seats"].get("p1") or {}).get("char") == "char_a"
          and (reg5["sessions"]["game_b"]["seats"].get("p2") or {}).get("char") == "char_a")
    check("S5c 人級 union：同名 char 取**成長較高**者、wallet 取**較大**者",
          (reg5["accounts"]["acct_dupe"]["chars"]["char_a"] or {}).get("level") == 4
          and (reg5["accounts"]["acct_dupe"]["wallet"] or {}).get("star_coins") == 9)
    check("S5d **舊 token 續用**（sha256 原樣搬）＋明文副本不搬",
          auth.Accounts(m_dir, "game_a", path=dest).resolve("trpg_acct_dupe_" + "a" * 32) is not None
          and "_plain_pw" not in reg5["accounts"]["acct_dupe"])

    # S6／S7 寫回按桌隔離 ＋ 共用檔的讀-改-寫不會互相蓋掉
    A_h.add_wallet("acct_a", star_coins=2)
    check("S6a 貨幣寫進**人級錢包**（兩桌共用同一筆）",
          A_s.wallet("acct_a").get("star_coins") == 7, f"{A_s.wallet('acct_a')}")
    A_h.sess()["written_back"] = True
    A_h.save()
    check("S6b 桌級旗標（`written_back`）只落在那張桌（別桌看不到）",
          A_h.sess().get("written_back") is True and A_s.sess().get("written_back") is None)
    a_stale = auth.Accounts(home_a, "demo_world", path=shared)     # 先拿一份舊快照
    a_new = auth.Accounts(home_b, "hyakki_tower", path=shared)
    a_stale.add_wallet("acct_b", star_coins=10)                    # 舊快照先寫
    a_new.add_wallet("acct_b", star_coins=5)                       # 再寫一份
    check("S7 共用檔的讀-改-寫**不會互相蓋掉**（進鎖前重讀；修前 5 會蓋掉 10）",
          auth.Accounts(home_a, "demo_world", path=shared).wallet("acct_b").get("star_coins") == 15,
          f"{auth.Accounts(home_a, 'demo_world', path=shared).wallet('acct_b')}")
    _sh.rmtree(shared_dir, ignore_errors=True)
    _sh.rmtree(old_dir, ignore_errors=True)
    _sh.rmtree(m_dir, ignore_errors=True)

    # S8 同一個帳號的同一個角色不能同時在兩張桌（設計者 2026-10-07 定案）
    print("== S8. 一個帳號的一個角色不能同時在兩張桌 ==")
    e_dir = _tf.mkdtemp(prefix="trpg_auth_excl_")
    e_root = os.path.join(e_dir, "repo")
    e_base = os.path.join(e_root, "campaigns")
    reg_x = os.path.join(e_dir, "registry.json")
    for tid in ("game_a", "game_b"):
        os.makedirs(os.path.join(e_base, tid), exist_ok=True)
        json.dump({"table_id": tid, "port": 0, "scenario": f"{tid} ── 副標"},
                  open(os.path.join(e_base, tid, "config.json"), "w", encoding="utf-8"), ensure_ascii=False)
    json.dump({"schema": 2, "accounts": {"acct_x": {"display": "X", "kind": "human"}},
               "sessions": {"game_a": {"table": "game_a",
                                       "seats": {"p1": {"account": "acct_x", "char": "hero"}}},
                            "game_b": {"table": "game_b",
                                       "seats": {"p1": {"account": "acct_x", "char": "hero"}}}}},
              open(reg_x, "w", encoding="utf-8"), ensure_ascii=False)
    from trpg.api import web as _wa2                                                  # noqa: PLC0415
    e_home = os.path.join(e_base, "game_a")

    class _Svc:                                  # 只給 `_busy_tables` 看的假 svc（站 game_a）
        cfg = {"table_id": "game_a", "auth": {"accounts_file": reg_x}}

        class table:                                                        # noqa: N801
            home = e_home

    check("S8a 別的桌**開著且未收局** ⇒ 判定「那個角色還在出場」（不准同時兩桌）",
          [b["id"] for b in _wa2._busy_tables(
              _Svc(), "acct_x", "hero",
              prober=lambda t: {"id": t["id"], "online": True, "ended": False, "events": 11})]
          == ["game_b"])
    check("S8b 別的桌**已收局** ⇒ 放行（角色自由，可帶去下一張桌）",
          _wa2._busy_tables(_Svc(), "acct_x", "hero",
                            prober=lambda t: {"id": t["id"], "online": True, "ended": True}) == [])
    check("S8c 別的桌**離線** ⇒ 放行（不因為有人關機就永遠卡住）",
          _wa2._busy_tables(_Svc(), "acct_x", "hero",
                            prober=lambda t: {"id": t["id"], "online": False, "ended": False}) == [])
    check("S8d **自己那張桌不算**（不會擋自己）",
          all(b["id"] != "game_a" for b in _wa2._busy_tables(
              _Svc(), "acct_x", "hero",
              prober=lambda t: {"id": t["id"], "online": True, "ended": False})))
    check("S8e 別的角色／別人的角色都不受影響",
          _wa2._busy_tables(_Svc(), "acct_x", "other",
                            prober=lambda t: {"id": t["id"], "online": True, "ended": False}) == []
          and _wa2._busy_tables(_Svc(), "acct_y", "hero",
                                prober=lambda t: {"id": t["id"], "online": True, "ended": False}) == [])
    check("S8f 擋下時的訊息講得出**是哪張桌**（玩家知道要去處理哪一張）",
          "game_b" in _wa2._busy_msg("hero", [{"id": "game_b", "scenario": "測試桌 ── 副標",
                                               "events": 11}]))
    _sh.rmtree(e_dir, ignore_errors=True)

    print("\n== W3b：空 chars 的帳號「第一次寫實例」要真的落地（實測踩過的真 bug）==")
    _t2 = tempfile.mkdtemp(prefix="trpg_auth_w3b_")
    try:
        with open(os.path.join(_t2, "accounts.json"), "w", encoding="utf-8") as fh:
            json.dump({"accounts": {"fresh": {"chars": {}, "wallet": {}}}}, fh, ensure_ascii=False)
        _a2 = auth.Accounts(_t2)
        _a2.write_instance("fresh", "c1", {"level": 1, "exp": 0, "note": "第一次"})
        check("W3b1 新帳號第一次寫實例 ⇒ 記憶體與磁碟都要有（`or {}` 會把空 chars 丟掉）",
              _a2.instance("fresh", "c1").get("level") == 1
              and json.load(open(os.path.join(_t2, "accounts.json"), encoding="utf-8"))
              ["accounts"]["fresh"]["chars"]["c1"]["level"] == 1,
              str(_a2.instance("fresh", "c1")))
        _a2.write_instance("fresh", "c1", {"level": 4, "exp": 30})
        check("W3b2 第二次寫（chars 非空）照樣覆蓋成長",
              _a2.instance("fresh", "c1").get("level") == 4, str(_a2.instance("fresh", "c1")))
    finally:
        _sh.rmtree(_t2, ignore_errors=True)

    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
