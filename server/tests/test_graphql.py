#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**GraphQL 客戶端面（P0）的機械驗收**——`graphql_api.py`。

    python3 test_graphql.py

為什麼要有：設計者 2026-10-07 拍「① 全替換，走 GraphQL」（一個端點、桌是欄位、session 必帶、
`/api/*` 之後退役）。這一層是**新介面**，壞掉的形式是「客戶端查得到、但語意跟 REST 不一樣」
——那比壞掉更難查。所以閘的重點是**兩界面同語意**：

* G1  契約（`docs/schema.graphql`）**合法**（`build_schema` ＋ `validate_schema` 無錯）
* G2  `tables`／`health` 的形狀與數字（module／scenario／session／events／ended）
* G3  `table(id)` 的純量＋`session`＋`capabilities`（能力目錄：包／節奏／屬性／棋盤）
* G4  **session 必帶**：`view` 沒帶 ⇒ 驗證錯；帶錯 ⇒ 明確的 session 錯；帶對 ⇒ 拿到投影
* G5  **mutation 必帶 session**（schema 就強制）＋錯 session 被拒（一副本一 session）
* G6  GM 面認 **`X-Table-GM-Token` 標頭**（帶對走得通、不帶被拒）
* G7  觀戰模式：mutation 一律拒（唯讀前門）
* G8  **同語意**（最重要）：同一份投影，GraphQL 與 REST **逐位元相同**；同一筆 `say`，
      兩條路寫進事件流的內容**一模一樣**（證明「換入口叫同一支 handler」不是口號）
* G9  `/graphql` 的錯誤**不是 HTTP 錯**（執行期錯走 `errors`，200）＋壞 body 才 400

不打真桌：臨時桌（抄現役桌 + `runs/` 那一局），`require_account` 關掉（認證另有 `test_auth`）。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

import test_support as TS

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo 根
sys.path.insert(0, HERE)
os.environ.setdefault("TRPG_LEGACY_REST", "1")   # 直接跑這支也要能打 `/api/*`（生產已退役 ⇒ 只有測試放行）
sys.path.append(os.path.join(HERE, "vendor"))

from trpg.accounts import account_store as account_store                                                    # noqa: E402
from trpg.api import graphql as graphql_api                                                      # noqa: E402
from trpg.core import layout as layout                                                           # noqa: E402
from trpg.core import store as store                                                            # noqa: E402
from trpg.service import table_directory as tdir                                          # noqa: E402
from trpg.service import table_factory as table_factory                                                    # noqa: E402

OK, FAIL = [], []


def check(name: str, cond: bool, detail: object = "") -> None:
    (OK if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))


def mk_table():
    """臨時桌：**共用件** `test_support.mk_table()`（正式形狀：定義在劇本層、桌目錄只有 config）。

    回 `(home, svc)`——`home`＝**桌目錄**（`config.json`／`runs/`／`accounts.json`）。
    """
    _root, home, cfg = TS.mk_table(with_run=True)
    svc = table_factory.build(home, cfg, log=lambda *_a: None)
    return home, svc


def _no_clock(obj):
    """把會走的時間戳拿掉（投影裡的 `now`／事件的 `ts`）——比對「同一份內容」時用。"""
    if isinstance(obj, dict):
        return {k: _no_clock(v) for k, v in obj.items() if k not in ("now", "ts")}
    if isinstance(obj, list):
        return [_no_clock(x) for x in obj]
    return obj


def gql(svc, doc, *, headers=None, watch=False, root=None):
    """跑一次 GraphQL，回 (http_status, payload)。

    ⚠ `root` **預設 None**：讓 `graphql_api` 從 `svc.table.home` 自己推 repo 根
    （＝**夾具那一棵樹**）。從前這裡傳 `HERE`（真倉庫）⇒ 「模組／桌一覽」那幾組其實在查**線上**，
    夾具造的第二張桌看不到（G17c 就是這樣紅的）。**夾具不准碰線上**是這支測試的紀律。
    """
    return graphql_api.handle({"query": doc}, svc=svc, root=root, headers=headers or {},
                              watch=watch)


def data_of(svc, doc, **kw):
    st, out = gql(svc, doc, **kw)
    if "errors" in out:
        raise AssertionError(f"{doc[:60]}… ⇒ {out['errors'][0]['message']}")
    return out["data"]


def main() -> int:
    print("== G1 契約（docs/schema.graphql）合法 ==")
    sch = graphql_api.schema()
    from graphql import validate_schema                                  # noqa: E402
    errs = validate_schema(sch)
    check("G1 schema 建得起來且 validate 無錯", not errs, str(errs[:2]))
    check("G1 一個端點：Query 有 tables／modules／health／table／me（桌是欄位）",
          set(sch.query_type.fields) == {"tables", "modules", "health", "table", "me"},
          str(sorted(sch.query_type.fields)))
    check("G1 36 格 mutation（逐一開欄位；含前臺層 newRun／createChar／createTable／"
          "setWatchable／setArchived／setPaused、適配器直通 gmRun、玩家按鈕 requestGm）",
          len(sch.mutation_type.fields) == 36, str(len(sch.mutation_type.fields)))
    check("G1b 契約裡有 requestGm（「要求 GM 就位」那一顆按鈕的入口）",
          "requestGm" in sch.mutation_type.fields, str(sorted(sch.mutation_type.fields)))

    tmp, svc = mk_table()
    _root = layout.repo_root(tmp)                    # 夾具的 repo 根（別數 dirname：會差一層）
    try:
        tid = str(svc.cfg["table_id"])
        sid = str(svc.table.session_id())
        check("G2 tables／health：列出桌並帶 module／scenario／session／events",
              all(k in data_of(svc, "{ tables { id module scenario session events ended } }")
                  ["tables"][0] for k in ("id", "module", "scenario", "session", "events", "ended")))
        h = data_of(svc, "{ health { status tables { id online session } } }")["health"]
        check("G2b health.status＝ok 且列出這張桌（online）",
              h["status"] == "ok" and any(t["id"] == tid and t["online"] for t in h["tables"]),
              json.dumps(h["tables"], ensure_ascii=False)[:90])
        t = data_of(svc, '{ table(id: "%s") { id module scenario seats events session { id module ended } '
                          'capabilities { mode packs attributes board } } }' % tid)["table"]
        check("G3 table(id)：純量＋session.id＝現役那一局", t["session"]["id"] == sid, t["session"]["id"])
        check("G3b capabilities：節奏＋啟用包＋屬性（agent 靠它知道能做什麼）",
              t["capabilities"]["mode"] in ("turn", "scene") and t["capabilities"]["packs"]
              and t["capabilities"]["attributes"], json.dumps(t["capabilities"], ensure_ascii=False)[:110])

        print("\n== G11 modules：桌子一覽按模組分類（設計者：「大厅的桌子一览可能需要按模组分类」）==")
        mods = data_of(svc, "{ modules { id name tableCount characters "
                           "scenarios { id tableCount tables { id module scenario } } } }")["modules"]
        flat = data_of(svc, "{ tables { id module } }")["tables"]
        check("G11 modules 非空，且劇本層底下的每一張桌都屬於那一組（module 欄一致）",
              bool(mods) and all(all(t["module"] == m["id"] for s in m["scenarios"] for t in s["tables"])
                                 for m in mods),
              json.dumps([m["id"] for m in mods], ensure_ascii=False))
        check("G11b tableCount 對得上，且把平表完整覆蓋（不吞桌）",
              sum(m["tableCount"] for m in mods) == len(flat)
              and all(m["tableCount"] == sum(s["tableCount"] for s in m["scenarios"]) for m in mods),
              f"{sum(m['tableCount'] for m in mods)} vs {len(flat)}")
        check("G11b2 每組都有顯示名（模組自己給的命名；沒有 module.json ⇒ 退回 id）",
              all(isinstance(m["name"], str) and m["name"] for m in mods),
              json.dumps([m["name"] for m in mods], ensure_ascii=False))
        check("G11c 每張桌在 modules 裡找得到（id 對得上）",
              {m["id"] for m in mods} == {t["module"] for t in flat},
              str(sorted({t["module"] for t in flat})))

        print("\n== G12 entry：進場判定（建桌／進場流程第 2 步）==")
        ent = data_of(svc, '{ table(id: "%s") { entry { session account hasInstance importable '
                            '{ seat charId } creatable { seat charId kind } takenSeats { seat } '
                            'spectateOk watchPath reason } } }' % tid)["table"]["entry"]
        check("G12 entry.session＝現役那一局（進場要用它）", ent["session"] == sid, ent["session"])
        check("G12b 三個籃子都有（importable／creatable／takenSeats）且 spectateOk 是布林",
              isinstance(ent["importable"], list) and isinstance(ent["creatable"], list)
              and isinstance(ent["takenSeats"], list) and isinstance(ent["spectateOk"], bool))
        _opts = ent["importable"] + ent["creatable"] + ent["takenSeats"]
        check("G12c 每一位玩家席位都被歸到某一籃（不吞席）且帶 seat／charId",
              bool(_opts) and all(o["seat"] and o["charId"] for o in _opts),
              str([(o["seat"], o["charId"]) for o in _opts]))
        _w = bool((svc.cfg.get("watch") or {}).get("enabled"))
        check("G12f watchPath 是 run 級新形態（開觀戰時＝/watchtables/<run>，不再是 /<桌>/watch/）",
              (str(ent.get("watchPath") or "").startswith("/watchtables/")
               and str(ent.get("watchPath") or "").endswith(sid)) if _w else ent.get("watchPath") is None,
              f"watch={_w} watchPath={ent.get('watchPath')!r}")
        # 「要不要憑證」看這一桌的**宣告**（`auth.require_account`）——夾具為了方便關掉了它，
        # 所以這裡兩種都驗：宣告要憑證 ⇒ 沒帶要明說；宣告不要 ⇒ 不需要提示。
        _au = svc.cfg.setdefault("auth", {})
        _au["require_account"] = True
        _r2 = data_of(svc, '{ table(id: "%s") { entry { reason } } }' % tid)["table"]["entry"]["reason"]
        _au["require_account"] = False
        _r3 = data_of(svc, '{ table(id: "%s") { entry { reason } } }' % tid)["table"]["entry"]["reason"]
        check("G12d 宣告要憑證而沒帶 ⇒ 明說要看得到「我的角色」得貼憑證（不是靜默空清單）",
              bool(_r2) and not _r3, f"要憑證:{_r2[:30]} / 不要:{_r3!r}")
        _ent2 = data_of(svc, '{ table(id: "%s") { entry { rosterSize creatable { charId className '
                             'name inRoster } } } }' % tid)["table"]["entry"]
        check("G12e **角色的類由模組定**：每個可選角色都帶模組角色表的 class／name 且 inRoster",
              bool(_ent2["creatable"]) and all(o["inRoster"] and o["className"] and o["name"]
                                               for o in _ent2["creatable"]),
              json.dumps(_ent2["creatable"], ensure_ascii=False)[:110])
        check("G12f rosterSize＝模組角色表的大小",
              _ent2["rosterSize"] == len(layout.characters(_root, "hyakki_tower")),
              f"{_ent2['rosterSize']} vs {len(layout.characters(_root, 'hyakki_tower'))}")

        print("\n== G13–G14 newRun：換局（建桌嚮導的「開新局」）==")
        _st, out = gql(svc, 'mutation { newRun(tableId: "%s") }' % tid)
        check("G13 單桌／沒有前台 hook ⇒ **明確拒絕**（不要偷偷重建、變成半死狀態）",
              "errors" in out and "前台" in out["errors"][0]["message"],
              out["errors"][0]["message"][:60])
        _old = str(svc.table.session_id())
        _fresh = table_factory.new_run(tmp, json.load(open(os.path.join(tmp, "config.json"),
                                                            encoding="utf-8")))
        _new = str(_fresh.table.session_id())
        check("G14 換局生新 session（UUID4）且與舊局不同",
              _new and _new != _old and len(_new) == 32, f"{_old} → {_new}")
        check("G14b 舊局目錄**原地留著**（一局一檔＝副本，可回放）",
              os.path.isfile(os.path.join(tmp, "runs", _old, "events.jsonl")))
        check("G14c `runs/current` 指到新局", store.current_run(tmp) == _new, str(store.current_run(tmp)))
        check("G14d 新局只有開局那一批事件（舊局牌面沒被帶過來）＋首筆是 table_created",
              bool(_fresh.table.events) and _fresh.table.events[0].get("type") == "table_created"
              and len(_fresh.table.events) <= 12, f"{len(_fresh.table.events)} 筆")
        check("G14e 新局回放一致", bool(_fresh.table.replay_check().get("replay_matches")))

        print("\n== G15 createChar：建新角色＝該帳號在該模組下的新實例（角色的類由模組定）==")
        from trpg.accounts import auth as _auth                                               # noqa: PLC0415
        _TOK = "trpg_gql_acct_z_token"
        with open(os.path.join(tmp, "accounts.json"), "w", encoding="utf-8") as fh:
            json.dump({"schema": 1, "table": tid,
                       "accounts": {"acct_z": {"display": "Z", "kind": "human",
                                               "token_sha256": _auth._hash(_TOK),
                                               "chars": {}, "wallet": {}}},
                       "session": {"table": tid, "seats": {}}}, fh, ensure_ascii=False)
        _cfg2 = json.load(open(os.path.join(tmp, "config.json"), encoding="utf-8"))
        check("G15a0 夾具**不會碰到線上 DB**（拔掉 db_env；否則測試會寫進真 Postgres）",
              not account_store.configured(_cfg2), str((_cfg2.get("auth") or {}).get("db_env")))
        svc2 = table_factory.build(tmp, _cfg2, log=lambda *_a: None)
        H = {"Authorization": "Bearer " + _TOK}
        # ⚠ 用**另一個變數**：G13/G14 換過局、G15 又重建了 svc2 ⇒ 不要覆蓋共用的 `sid`
        #   （覆蓋的話後面幾組（G6…）會拿新 session 去打舊實例，看起來像 session 規則壞了）。
        _sid2 = str(svc2.table.session_id())

        print("\n== G20 大廳的根端點（2026-10-19 大廳改版：頁面在前端 repo，資料全走根 /graphql）==")
        def _root_q(doc, headers=None):
            return graphql_api.handle({"query": doc}, svc=None, root=_root, headers=headers or {},
                                      lookup=lambda _t: svc2 if str(_t) == tid else None)
        _st, out = _root_q("{ me { account } }")
        check("G20a 根端點、沒憑證 ⇒ me＝null（不是錯誤；大廳據此顯示「登入」）",
              "errors" not in out and out["data"]["me"] is None, json.dumps(out, ensure_ascii=False)[:100])
        _st, out = _root_q("{ me { account display tables { seat charId table { id roundNo paused waiting } } } }", H)
        _me = (out.get("data") or {}).get("me") or {}
        check("G20b 根端點、帶憑證 ⇒ 認得帳號，且 Identity.tables 有值（以前這一格沒接 resolver，一律炸）",
              "errors" not in out and _me.get("account") == "acct_z"
              and any(m["table"]["id"] == tid for m in (_me.get("tables") or [])),
              json.dumps(out, ensure_ascii=False)[:140])
        _st, out = _root_q("{ modules { id assets { module locale } scenarios { tables { id roundNo paused waiting } } } }")
        _tb = [tb for m in ((out.get("data") or {}).get("modules") or []) for sc in m["scenarios"] for tb in sc["tables"]]
        check("G20c TableInfo 帶 roundNo／paused／waiting，ModuleInfo 有 assets 欄（沒素材＝null）",
              "errors" not in out and _tb and all(isinstance(tb["roundNo"], int) and isinstance(tb["paused"], bool)
                                                  and isinstance(tb["waiting"], list) for tb in _tb),
              json.dumps(out, ensure_ascii=False)[:140])
        _st, out = _root_q('mutation { login(username: "nobody", password: "x") { token } }')
        check("G20d 根端點的 login 會去問各桌的帳號層（帳密不對＝明講，不再是「要指定 tableId」）",
              "errors" in out and "tableId" not in out["errors"][0]["message"],
              out.get("errors", [{}])[0].get("message", "")[:80])
        _m = 'mutation { createChar(tableId: "%s", session: "%s"' % (tid, _sid2)
        _st, out = gql(svc2, _m + ', charId: "char_b") }')
        check("G15a 沒憑證 ⇒ 明確拒絕（不是靜默建一個別人的角色）",
              "errors" in out and "token" in out["errors"][0]["message"],
              out["errors"][0]["message"][:60])
        _st, out = gql(svc2, _m + ', charId: "no_such_char") }', headers=H)
        check("G15b 角色不在**模組角色表** ⇒ 拒（角色的類由模組定）",
              "errors" in out and "角色表" in out["errors"][0]["message"],
              out["errors"][0]["message"][:60])
        _st, out = gql(svc2, _m + ', charId: "char_b") }', headers=H)
        _inst = (out.get("data") or {}).get("createChar", {}).get("instance") or {}
        check("G15c 建新實例成功：綁席位 ＋ 寫 level 1 ＋ 回 instance",
              _inst.get("level") == 1 and bool((out["data"]["createChar"].get("seat"))),
              json.dumps(out, ensure_ascii=False)[:130])
        _st, out = gql(svc2, _m + ', charId: "char_b") }', headers=H)
        check("G15d 已經有實例 ⇒ 拒並**指引用 pickChar**（兩條路互不越界）",
              "errors" in out and "pickChar" in out["errors"][0]["message"],
              out["errors"][0]["message"][:70])
        _st, out = gql(svc2, 'mutation { pickChar(tableId: "%s", session: "%s", charId: "char_a") }'
                       % (tid, _sid2), headers=H)
        check("G15e pickChar 仍是「匯入」那條路（走得通；兩者分工）",
              bool((out.get("data") or {}).get("pickChar")), json.dumps(out, ensure_ascii=False)[:150])
        _ent3 = data_of(svc2, '{ table(id: "%s") { entry { importable { charId } creatable { charId } } } }'
                        % tid, headers=H)["table"]["entry"]
        check("G15f 建完之後 entry 把它歸到「匯入已有」（兩格口徑一致）",
              "char_b" in [o["charId"] for o in _ent3["importable"]],
              json.dumps(_ent3, ensure_ascii=False)[:110])

        print("\n== G16 同一劇本多桌：桌目錄各自、定義共享（設計者 2026-10-07 第 3 條）==")
        _root16 = layout.repo_root(tmp)          # 上溯找 repo 根（有五層深，別數 dirname）
        # ⚠ 夾具的 `tmp` **就是劇本目錄**（也是第一張桌＝舊制形狀）⇒ 第二張桌掛在它底下的 `tables/`。
        #   `_scn` 取 `dirname(tmp)` 會高一層（＝`scenarios/`），定義就找不到（PackError）——實測踩過。
        _scn = layout.def_dir_of(tmp)                       # 劇本層（定義在那）
        _t2 = os.path.join(_scn, layout.TABLES_DIR, "second")      # ★ 同一劇本的**第二張桌**
        os.makedirs(_t2, exist_ok=True)
        _cfg2c = json.load(open(os.path.join(tmp, "config.json"), encoding="utf-8"))
        _cfg2c["port"] = 0
        _cfg2c["table_id"] = "hyakki_tower_2"
        with open(os.path.join(_t2, "config.json"), "w", encoding="utf-8") as fh:
            json.dump(_cfg2c, fh, ensure_ascii=False)
        store.copy_current_run(tmp, _t2)                  # 第二張桌也有自己的一局
        _s2 = table_factory.build(_t2, _cfg2c, log=lambda *_a: None)
        _seats2 = _s2.table.adapter_view()["seats"]
        check("G16a 從**桌目錄**建 ⇒ 定義讀到劇本層（seats／board 都在 `<劇本>/`，不在桌目錄裡）",
              _s2.def_dir == _scn and _s2.table.home == _scn
              and os.path.exists(os.path.join(_s2.def_dir, "seats.json"))
              and not os.path.exists(os.path.join(_t2, "seats.json")) and len(_seats2) >= 1,
              f"def_dir={_s2.def_dir} seats={sorted(_seats2)}")
        check("G16b 事件流落在**桌自己的目錄**（`<桌>/runs/<session>/`）",
              layout.events_path(_t2).startswith(_t2)
              and os.path.exists(layout.events_path(_t2)), layout.events_path(_t2))
        _rows16 = layout.scan(_root16)                     # ⚠ scan 只給位置；**id 要讀那一桌自己的 config**
        _ids16 = sorted(str((layout.read_config(r["home"]) or {}).get("table_id") or "")
                        for r in _rows16)
        check("G16c `layout.scan` 同劇本的兩張桌都列出來（不吞桌）",
              _ids16 == ["hyakki_tower", "hyakki_tower_2"], str(_ids16))
        check("G16d 兩張桌的 `def_dir` 相同（定義共享：一處改、兩桌都吃到）",
              all(r["def_dir"] == _scn for r in layout.scan(_root16)),
              str([r["def_dir"] for r in layout.scan(_root16)]))

        print("\n== G17 createTable／newRun 是**前台層**操作 ==")
        _st, out = gql(svc, 'mutation { createTable(module: "hyakki_tower", scenario: "main") }')
        check("G17a 單桌模式（沒有 registry）⇒ **明確拒絕**，不偷偷生一個沒人服務的目錄",
              "errors" in out and "前台" in out["errors"][0]["message"],
              out["errors"][0]["message"][:60])
        _st, out = gql(svc, 'mutation { newRun(tableId: "%s") }' % tid)
        check("G17b newRun 同理（同一條規則）",
              "errors" in out and "前台" in out["errors"][0]["message"],
              out["errors"][0]["message"][:60])
        _mods0 = data_of(svc, '{ modules { id name tableCount characters scenarios { id tableCount '
                             'tables { id scenario } } } }')["modules"]
        _m0 = [m for m in _mods0 if m["id"] == "hyakki_tower"][0]
        _flat = [t["id"] for s in _m0["scenarios"] for t in s["tables"]]
        check("G17c `modules` 的劇本層：scenarios[].tables 覆蓋所有桌（不吞桌）",
              sorted(_flat) == sorted(t["id"] for t in data_of(
                  svc, '{ tables { id } }')["tables"]), str(_flat))
        # ⚠ 角色數**不可寫死**（2026-10-19 百鬼塔 3→10 時這條假紅）：以模組的 characters.json 為準
        _roster_n = len(json.load(open(os.path.join("modules", "hyakki_tower", "characters.json"),
                                       encoding="utf-8"))["characters"])
        check("G17d 每個劇本的 `tableCount` 與它底下的桌數一致 ＋ 模組帶角色數（照 characters.json）",
              all(s["tableCount"] == len(s["tables"]) for s in _m0["scenarios"])
              and _m0["characters"] == _roster_n, json.dumps(_m0, ensure_ascii=False)[:110])

        _stt, _ty = gql(svc, 'mutation { typing(tableId: "%s", session: "%s", seat: "p1", to: "gm") }' % (tid, sid), headers=H)
        check("G17e typing（以某席位打字）回 payload 而不是純量（型別寫錯會當場炸）",
              _stt and ((_ty.get("data") or {}).get("typing")) is not None and not _ty.get("errors"),
              str(_ty)[:110])

        print("\n== G18–G20 view 逐欄型別化（P0b）：對得上投影、raw 不縮水、seat 真傳下去 ==")
        _st, _vv = gql(svc, '{ table(id: "%s") { view(session: "%s") { session table { tableId scenario rules } '
                            'paused pausedSeconds seats { seat name hp maxHp isTurn star level } '
                            'actions { id label } allowedActions rulesAttrs auth { hasAccounts required seatLink } '
                            'log { seq ts type data } raw } } }' % (tid, sid))
        _c, _pr = graphql_api.call(svc, "GET", "/api/state")
        _tv = ((_vv.get("data") or {}).get("table") or {}).get("view") or {}
        _pr2 = json.loads(json.dumps(_pr, ensure_ascii=False))
        check("G18a 型別化欄位逐欄對得上 `/api/state`（桌／暫停／席次／動作／權限）",
              _tv.get("table", {}).get("tableId") == _pr2["table"]["table_id"]
              and _tv.get("paused") == bool(_pr2["paused"])
              and _tv.get("allowedActions") == list(_pr2.get("allowed_actions") or [])
              and _tv.get("rulesAttrs") == list(_pr2.get("rules_attrs") or [])
              and [s["seat"] for s in _tv.get("seats", [])] == [s["seat"] for s in _pr2.get("seats", [])]
              and _tv.get("auth", {}).get("required") == bool((_pr2.get("auth") or {}).get("required")),
              json.dumps(_tv.get("table"), ensure_ascii=False)[:110])
        check("G18b `raw` ＝整份投影原樣（**契約不縮水**，MCP 工具的輸出就靠它）",
              isinstance(_tv.get("raw"), dict) and sorted(_tv["raw"]) == sorted(_pr2)
              and _tv["raw"]["table"]["table_id"] == _pr2["table"]["table_id"],
              f"raw 鍵數 {len(_tv.get('raw') or {})} / 投影 {len(_pr2)}")
        _lg = _tv.get("log") or []
        check("G19 log 逐筆型別化且 `data` 帶回原始欄位（seq／ts／type 之外都在）",
              bool(_lg) and _lg[0]["seq"] == (_pr2.get("log") or [{}])[0]["seq"]
              and _lg[0]["type"] == (_pr2.get("log") or [{}])[0]["type"]
              and "rules" in (_lg[0].get("data") or {}), json.dumps(_lg[0], ensure_ascii=False)[:110])
        # 夾具不再依賴現役那一局（2026-10-07 清掉線上臨時桌後）⇒ 需要座位的檢查**自己入座**。
        if not (svc.table.state.get("seats") or {}).get("p1"):
            svc.table.join(seat="p1", name="甲", kind="human")
        _st, _pv = gql(svc, '{ table(id: "%s") { view(session: "%s", seat: "p1") { you { seat name hp attrs } } } }'
                       % (tid, sid), headers=H)
        _you = ((((_pv.get("data") or {}).get("table") or {}).get("view") or {}).get("you"))
        check("G20 帶 seat 的 view 真的有 `you`（**證明 seat 真的傳下去了**，不是回公開投影）",
              bool(_you) and _you.get("seat") == "p1" and isinstance(_you.get("attrs"), dict),
              json.dumps(_you, ensure_ascii=False)[:110])

        print("\n== G22 按 query 做訪問控制（2026-10-19 設計者定案：匿名只讀／寫面要憑證）==")
        def _root2(doc, headers=None):
            return graphql_api.handle({"query": doc}, svc=None, root=_root, headers=headers or {},
                                      lookup=lambda _t: svc2 if str(_t) == tid else None)

        _was_watch = bool((svc2.cfg.get("watch") or {}).get("enabled"))
        _was_require = bool((svc2.cfg.get("auth") or {}).get("require_account"))
        svc2.cfg.setdefault("watch", {})["enabled"] = True
        # ⚠ 這四條驗的是「宣告要 account 的桌」⇒ 夾具的 config 沒寫這一格，這裡顯式打開（事後還原）。
        svc2.cfg.setdefault("auth", {})["require_account"] = True
        _q = ('{ table(id: "%s") { view(session: "%s") { auth { hasAccounts required } raw } } }'
              % (tid, _sid2))
        _st, _o = _root2(_q)
        _raw = ((((_o.get("data") or {}).get("table") or {}).get("view")) or {}).get("raw")
        check("G22a 匿名 query ⇒ 公開投影（沒有 you、沒有 errors）",
              _st == 200 and not _o.get("errors") and isinstance(_raw, dict) and "you" not in _raw,
              json.dumps(_o.get("errors") or sorted(_raw or {})[:6], ensure_ascii=False)[:110])
        _auth = ((((_o.get("data") or {}).get("table") or {}).get("view")) or {}).get("auth") or {}
        check("G22a2 匿名只讀的 `View.auth` 仍是權威的（require_account ⇒ required=true，不因公開投影而丟）",
              bool(_auth.get("required")) is True, json.dumps(_auth, ensure_ascii=False)[:90])
        _st, _o = _root2('mutation { say(tableId: "%s", session: "%s", text: "匿名", seat: "p1") }'
                         % (tid, _sid2))
        check("G22b 匿名 mutation ⇒ 拒（寫面不在匿名範圍）", bool(_o.get("errors")),
              json.dumps(_o.get("errors") or "沒擋", ensure_ascii=False)[:100])
        svc2.cfg.setdefault("watch", {})["enabled"] = False
        _st, _o = _root2(_q)
        check("G22c 沒開觀戰的桌 ⇒ 匿名連只讀都要憑證（維持原要求）",
              bool(_o.get("errors")) and not _o.get("data", {}).get("table"),
              json.dumps(_o.get("errors") or "放行了", ensure_ascii=False)[:110])
        svc2.cfg.setdefault("watch", {})["enabled"] = _was_watch
        _st, _o = _root2(_q, headers={"Authorization": "Bearer " + _TOK})
        check("G22d 帶憑證 ⇒ 照舊走正常路徑（不吃匿名那條；seat 由 token 決定）",
              not _o.get("errors"), json.dumps(_o.get("errors") or "ok", ensure_ascii=False)[:110])
        svc2.cfg.setdefault("auth", {})["require_account"] = _was_require
        print("\n== G15 requestGm：「要求 GM 就位」那一顆按鈕的入口（owner 2026-10-08）==")
        _keep_mode = getattr(svc, "pacing_mode", None)
        svc.pacing_mode = "turn"          # 只在這一格裡試（夾具桌不一定是回合制；下面還原）
        _st15, _o15 = gql(svc, 'mutation { requestGm(tableId: "%s", session: "%s") }' % (tid, sid))
        _d15 = ((_o15.get("data") or {}).get("requestGm")) or {}
        check("G15a requestGm 回 {ok,sent,skipped,reason}（按鈕靠 `reason` 講話）",
              all(k in _d15 for k in ("ok", "sent", "skipped", "reason")),
              json.dumps(_d15, ensure_ascii=False)[:120] or json.dumps(_o15, ensure_ascii=False)[:120])
        check("G15b 不該叫／叫過了**不是錯誤**（`skipped` 說明原因；按鈕可以多按）",
              "errors" not in _o15, json.dumps(_o15, ensure_ascii=False)[:120])
        _st15b, _o15b = gql(svc, 'mutation { requestGm(tableId: "%s", session: "nope") }' % tid)
        check("G15c 錯 session ⇒ 被擋（一副本一 session）",
              "errors" in _o15b, json.dumps(_o15b, ensure_ascii=False)[:110])
        svc.pacing_mode = _keep_mode
        _mcp = {"table_view": "view", "table_act": "act", "table_say": "say", "table_join": "join",
                "table_tell": "tell", "table_secret": "yourSecrets", "table_gm": "gmScene"}
        _sch = graphql_api.schema()
        _have = (set(_sch.mutation_type.fields) | set(_sch.get_type("Table").fields)
                 | set(_sch.get_type("View").fields))
        check("G21 MCP 七格工具在 GraphQL 面都有對應（適配器做得起來；描述仍以 mcp_api 為準）",
              all(v in _have for v in _mcp.values()), str(sorted(k for k, v in _mcp.items() if v not in _have)))

        print("\n== G4–G5 session 必帶／必對 ==")
        _st, out = gql(svc, '{ table(id: "%s") { view { session } } }' % tid)
        check("G4 view 沒帶 session ⇒ GraphQL 驗證錯（schema 就擋）",
              "errors" in out and "session" in out["errors"][0]["message"], out["errors"][0]["message"][:70])
        _st, out = gql(svc, '{ table(id: "%s") { view(session: "nope") { session } } }' % tid)
        check("G4b view 帶錯 session ⇒ 明確的 session 錯（不是靜默給別局）",
              "errors" in out and "session 不對" in out["errors"][0]["message"],
              out["errors"][0]["message"][:80])
        _st, out = gql(svc, 'mutation { say(tableId: "%s", text: "x") }' % tid)
        check("G5 mutation 沒帶 session ⇒ 驗證錯（schema 強制）",
              "errors" in out and "session" in out["errors"][0]["message"], out["errors"][0]["message"][:70])
        _st, out = gql(svc, 'mutation { say(tableId: "%s", session: "nope", text: "x") }' % tid)
        check("G5b mutation 帶错 session ⇒ 拒（一副本一 session）",
              "errors" in out and "session 不對" in out["errors"][0]["message"],
              out["errors"][0]["message"][:80])

        print("\n== G6 GM 控制鍵走標頭 ==")
        # 夾具：這一桌宣告一個 GM 控制鍵（2026-10-20 起 auth 由部署端供給，臨時桌不一定有）
        _gm_tok = "test-gm-token"
        svc.cfg.setdefault("auth", {})["gm_token"] = _gm_tok
        _st, out = gql(svc, 'mutation { gmReplay(tableId: "%s", session: "%s") }' % (tid, sid))
        check("G6 沒帶 GM 標頭 ⇒ 拒", "errors" in out, out["errors"][0]["message"][:60])
        _st, out = gql(svc, 'mutation { gmReplay(tableId: "%s", session: "%s") }' % (tid, sid),
                       headers={"X-Table-GM-Token": _gm_tok})
        check("G6b 帶對 GM 標頭 ⇒ 走得通（gmReplay 回 replay_matches）",
              "data" in out and bool(out["data"]["gmReplay"].get("replay_matches")),
              json.dumps(out, ensure_ascii=False)[:110])

        print("\n== G7 觀戰＝唯讀前門 ==")
        _st, out = gql(svc, 'mutation { say(tableId: "%s", session: "%s", text: "x") }' % (tid, sid),
                       watch=True)
        check("G7 觀戰模式：mutation 一律拒", "errors" in out and "唯讀" in out["errors"][0]["message"],
              out["errors"][0]["message"][:60])
        got = data_of(svc, '{ table(id: "%s") { view(session: "%s") { session raw } } }' % (tid, sid), watch=True)
        check("G7b 觀戰模式：讀得到（公開投影）", bool(got["table"]["view"]))

        print("\n== G8 兩界面同語意（同一支 handler 的硬證據）==")
        _c, rest_view = graphql_api.call(svc, "GET", "/api/state")
        g_view = data_of(svc, '{ table(id: "%s") { view(session: "%s") { session raw } } }'
                         % (tid, sid))["table"]["view"]
        check("G8 同一份投影：GraphQL `view.raw` 與 REST **逐位元相同**（時間戳除外——兩次呼叫時鐘本來就會走）",
              _no_clock(rest_view) == _no_clock(g_view["raw"]),
              f"rest {len(json.dumps(rest_view))} 字 / gql.raw {len(json.dumps(g_view['raw']))} 字")
        seat = "p1"                       # 這張臨時桌的玩家席（gm／npc1／p1）
        n0 = len(svc.table.events)
        _c, rest_say = graphql_api.call(svc, "POST", "/api/say",
                                        body={"seat": seat, "text": "REST 說一句"})
        ev_rest = next(e for e in reversed(svc.table.events)
                       if e.get("type") == "say")      # 之後可能還有 acted（半合作記號）
        data_of(svc, 'mutation { say(tableId: "%s", session: "%s", seat: "%s", text: "GQL 說一句") }'
                % (tid, sid, seat))
        ev_gql = next(e for e in reversed(svc.table.events)
                      if e.get("type") == "say")
        _n_say = len([e for e in svc.table.events[n0:] if e.get("type") == "say"])
        same = (_n_say == 2
                and ev_rest.get("type") == ev_gql.get("type") == "say"
                and ev_rest.get("seat") == ev_gql.get("seat") == seat
                and {k: v for k, v in ev_rest.items() if k not in ("seq", "ts", "text")}
                == {k: v for k, v in ev_gql.items() if k not in ("seq", "ts", "text")}
                and ev_rest.get("text") == "REST 說一句" and ev_gql.get("text") == "GQL 說一句")
        check("G8b 同一筆 say：兩條路寫進事件流的**欄位結構一模一樣**（只有文字／序號不同）", same,
              json.dumps({"rest": ev_rest, "gql": ev_gql}, ensure_ascii=False)[:150])

        print("\n== G9 HTTP 狀態語意 ==")
        st, out = gql(svc, '{ table(id: "%s") { view(session: "nope") { session } } }' % tid)
        check("G9 執行期的錯 ⇒ **200** ＋ errors（GraphQL 慣例）", st == 200 and "errors" in out, str(st))
        st, out = graphql_api.handle("not a dict", svc=svc)               # type: ignore[arg-type]
        check("G9b body 不是 JSON object ⇒ 400", st == 400 and "errors" in out, str(st))
        st, out = graphql_api.handle({}, svc=svc)
        check("G9c 少了 query ⇒ 400", st == 400, str(st))

        print("\n== G18 `gm.py` 的 CLI 路（2026-10-10 修的三處；先前全壞）==")
        # ① `gmRun(op="semi", args={"op": <動作>})`：`/api/gm` 的 semi 分支是 `gm_op(**args)`，
        #    外層 `op` 若把 `args["op"]` 蓋掉 ⇒ `gm_op()` 少必要參數直接 TypeError
        #    （`tools/gm.py` 除 `say`／`state` 外全壞，2026-10-07 `24f82f0` 起）。
        _gt = str((svc.cfg.get("auth") or {}).get("gm_token") or "")
        _HG = {"X-Table-GM-Token": _gt}
        _st, out = gql(svc, 'mutation { gmRun(tableId: "%s", session: "%s", op: "semi",'
                            ' args: {op: "leak_report"}) }' % (tid, sid), headers=_HG)
        _ev = (out.get("data") or {}).get("gmRun") or {}
        # 2026-10-21 拆景：GM 沒有「開景」了（`op=scene_open` 已不存在）⇒ 改驗 leak_report：
        #   args.op 若被外層 op 蓋掉，gm_op 會因為缺參數/未知操作直接炸。
        check("G18a gmRun(op=semi) 的 args.op 真的傳進去（不是被外層 op 蓋掉）",
              "leaks" in json.dumps(_ev, ensure_ascii=False),
              json.dumps(out, ensure_ascii=False)[:120])

        # ② `gql_client.gm_op`（＝`gm.py` 走的那支）不能把 `args["op"]` pop 掉
        sys.path.insert(0, os.path.join(HERE, "tools"))     # `gql_client` 住 tools/（本檔原本不在 sys.path）
        import gql_client as _gqlc                                          # noqa: PLC0415
        _seen: dict = {}
        _orig_gql = _gqlc.gql
        _gqlc.gql = lambda base, q, variables=None, **kw: (_seen.update(variables or {}) or {"gmRun": {}})
        try:
            _gqlc.gm_op("http://x", tid, sid, "semi", {"op": "scene_close", "force": True}, gm=_gt)
        finally:
            _gqlc.gql = _orig_gql
        check("G18b gql_client.gm_op：`semi` 的動作名留在 args 裡（先前被 pop＝整條 CLI 路壞）",
              (_seen.get("a") or {}).get("op") == "scene_close", json.dumps(_seen, ensure_ascii=False)[:120])

        # ③ `say(seat:"gm")` 在 **require_account** 的桌上要認 **GM 控制鍵**
        #    （handler 的閘讀 body 裡的 token；REST_OPS 那條先前沒把標頭轉進去 ⇒ `gm.py say` 403）
        _r3, _h3, _c3 = TS.mk_table(require_account=True, with_run=True)
        try:
            _svc3 = table_factory.build(_h3, _c3, log=lambda *_a: None)
            _tid3 = str(_c3.get("table_id") or "")
            _sid3 = str(_svc3.table.session_id())
            _gt3 = str((_c3.get("auth") or {}).get("gm_token") or "")
            _m3 = ('mutation { say(tableId: "%s", session: "%s", seat: "gm", text: "G18 抽查") }'
                   % (_tid3, _sid3))
            _st, out = gql(_svc3, _m3, headers={"X-Table-GM-Token": _gt3})
            check("G18c require_account 的桌上，GM 帶控制鍵可以 say（修前是 403 account）",
                  "errors" not in out and bool((out.get("data") or {}).get("say")),
                  json.dumps(out, ensure_ascii=False)[:130])
            _st, out = gql(_svc3, _m3)
            check("G18d 反面：不帶控制鍵 ⇒ 明確拒（不是靜默當成 GM）",
                  "errors" in out, json.dumps(out, ensure_ascii=False)[:120])
            check("G18e 查得到的副作用：那一筆真的寫進事件流（type=gm_say／seat=gm）",
                  any(e.get("type") == "gm_say" and e.get("seat") == "gm" for e in _svc3.table.events),
                  str(len(_svc3.table.events)))
        finally:
            shutil.rmtree(_r3, ignore_errors=True)

        # ④ `gql_client.mutate`：**有值**的參數要宣告成不可空（`say(text: String!)` 宣告 `String` 會被 GraphQL 拒）
        _seen2: dict = {}
        _orig_gql = _gqlc.gql
        _gqlc.gql = lambda base, q, variables=None, **kw: (_seen2.update({"q": q}) or {"say": {"ok": True}})
        try:
            _gqlc.mutate("http://x", "say", {"seat": "gm", "text": "t"}, table=tid, session=sid)
        finally:
            _gqlc.gql = _orig_gql
        check("G18f gql_client.mutate 把有值的參數宣告成不可空（`$text: String!`）",
              "$text: String!" in str(_seen2.get("q")), str(_seen2.get("q"))[:110])

        print("\n== G10 找不到桌＝明確的錯（不是空資料）==")
        _st, out = gql(svc, '{ table(id: "no_such_table") { id } }')
        check("G10 桌不存在 ⇒ 明講（不靜默回 null）",
              "errors" in out and "找不到桌" in out["errors"][0]["message"],
              out["errors"][0]["message"][:70])
    finally:
        shutil.rmtree(_root, ignore_errors=True)


    print("== G21 Archive（2026-10-19：owner 才能封存；封存後不進選桌、不能寫）==")
    _tmp3, _svc3 = mk_table()
    _root3 = layout.repo_root(_tmp3)
    try:
        from trpg.service import run_archive as _ra

        from trpg.service import run_owner as _ro

        _tid3 = str(_svc3.cfg["table_id"])
        _sid3 = str(_svc3.table.session_id())
        _home3 = str(_svc3.home)

        def _q3(doc, var=None, tok="Bearer x"):
            return graphql_api.handle({"query": doc, "variables": var or {}},
                                      svc=_svc3, root=_root3,
                                      headers={"Authorization": tok} if tok else {})

        check("G21a 封存前：tables 裡看得到、archived=false",
              any(x["id"] == _tid3 for x in
                  (_q3("{ tables { id archived } }")[1]["data"]["tables"])))
        _ro.set_owner(_home3, _sid3, "acct_test_owner")
        _ra.set_archived(_home3, _sid3, True, by="acct_test_owner")
        check("G21b 封存後：tables 不再列出這一局",
              all(x["id"] != _tid3 for x in (_q3("{ tables { id } }")[1]["data"]["tables"])))
        _st3, _out3 = _q3('mutation($t: ID!, $s: ID!){ say(tableId:$t, session:$s, seat:"gm", text:"hi") }',
                          {"t": _tid3, "s": _sid3})
        _msg3 = str((((_out3 or {}).get("errors") or [{}])[0]).get("message") or "")
        check("G21c 封存後寫入被拒，且訊息講清楚是 Archive", "封存" in _msg3, _msg3[:50])
        _st4, _out4 = _q3('query($t: ID!, $s: ID!){ table(id:$t){ archived view(session:$s){ stage } } }',
                          {"t": _tid3, "s": _sid3})
        check("G21d 封存後讀取照舊（archived=true、view 拿得到）",
              _st4 == 200 and (_out4.get("data") or {}).get("table", {}).get("archived") is True)
        _st5, _out5 = _q3('mutation($t: ID!, $s: ID!){ setArchived(tableId:$t, session:$s, archived:false) }',
                          {"t": _tid3, "s": _sid3}, tok="Bearer 別人的")
        _msg5 = str((((_out5 or {}).get("errors") or [{}])[0]).get("message") or "")
        check("G21e 非 owner 不能解封", "owner" in _msg5, _msg5[:50])
        _ra.set_archived(_home3, _sid3, False)
        check("G21f 解除封存後 tables 又看得到",
              any(x["id"] == _tid3 for x in (_q3("{ tables { id } }")[1]["data"]["tables"])))

        # ★ 2026-10-20（P2）：三個原本能繞過的老洞——session 寫字面量／一個 mutation 多個欄位／
        #   MCP（另見 test_mcp_adapter A5）。現在都要被擋，且 setArchived 解封仍可用。
        _ra.set_archived(_home3, _sid3, True, by="acct_test_owner")
        _stg, _outg = _q3('mutation { say(tableId:"%s", session:"%s", seat:"gm", text:"literal") }'
                          % (_tid3, _sid3))
        _msgg = str((((_outg or {}).get("errors") or [{}])[0]).get("message") or "")
        check("G21g session 寫字面量也擋（不再只看 $變數）", "封存" in _msgg, _msgg[:60])

        _sth, _outh = _q3(
            'mutation($t: ID!, $s: ID!){ setArchived(tableId:$t, session:$s, archived:true)'
            ' say(tableId:$t, session:$s, seat:"gm", text:"after") }',
            {"t": _tid3, "s": _sid3})
        _msgh = str((((_outh or {}).get("errors") or [{}])[0]).get("message") or "")
        check("G21h 多欄位 mutation：第一個是白名單也不能放行後面的 say",
              "封存" in _msgh, _msgh[:60])

        _stk, _outk = _q3(
            'mutation($t: ID!, $s: ID = "%s"){ say(tableId:$t, session:$s, seat:"gm", text:"default") }'
            % _sid3, {"t": _tid3})          # 故意不給 $s：GraphQL 會用變數預設值
        _msgk = str((((_outk or {}).get("errors") or [{}])[0]).get("message") or "")
        check("G21k session 走**變數預設值**也擋（不能只看 variables）", "封存" in _msgk, _msgk[:60])

        _called = []

        def _new_run_hook(tid, watchable=None):
            _called.append(tid)
            return {"ok": True, "session": "new-run-id", "old_session": _sid3}

        _sti, _outi = graphql_api.handle(
            {"query": 'mutation { newRun(tableId:"%s") }' % _tid3},
            svc=_svc3, root=_root3, headers={}, new_run=_new_run_hook)
        _msgi = str((((_outi or {}).get("errors") or [{}])[0]).get("message") or "")
        check("G21i 匿名 newRun 被拒、且沒有真的換局",
              "憑證" in _msgi and _called == [], (_msgi[:50], _called))

        _ra.set_archived(_home3, _sid3, False)
        _stj, _outj = _q3('mutation($t: ID!, $s: ID!){ setArchived(tableId:$t, session:$s, archived:false) }',
                          {"t": _tid3, "s": _sid3}, tok="Bearer 別人的")
        check("G21j 解封走的是 owner 權限（非 owner 仍被拒，不是被 Archive 閘擋）",
              "owner" in str(((_outj or {}).get("errors") or [{}])[0].get("message") or ""))
    finally:
        shutil.rmtree(_root3, ignore_errors=True)

    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    for f in FAIL:
        print("  FAILED: " + f)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
