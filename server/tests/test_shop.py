#!/usr/bin/env python3
"""**商店的自測**（★2，2026-10-07）——`packs/*/tables/shop.json`（作品層包）＋ `config.tower.shop`（legacy）。

    python3 test_shop.py

設計者拍「繼續按你的順序」（★2＝商店接上道具表）。以前買到的東西只給 `heal`／`memory`（不進庫存）
＝跟 `items.py` 是兩套物品概念；現在「賣什麼」變成一張表，買到＝**真的進 inventory**。

  U1 **表**：載得動（3 件）；schema 壞（缺 id／id 重複／price 亂寫／currency 亂寫／`stock` 或別的欄位／
     note 非字串）當場叫。
  U2 **載入守衛**：有 shop 表但沒有道具表 ⇒ **載入時就叫**；賣的道具不在道具表 ⇒ 也叫。
  U3 **legacy 路**（`config.tower.shop` 有寫）：全走 config——貨幣 −cost、`heal` 生效、**不進 inventory**，
     而且沒有 `shop_items`（投影不變）。
  U4 **表路**：買 ⇒ 貨幣 −price ＋ `inventory` +1（`shop_buy` → `item_changed` 兩筆事件）。
  U5 **沒錢** ⇒ 拒、不留事件（連 `shop_buy` 都不發）。
  U6 **沒這商品** ⇒ 拒（列出可選）。
  U7 **投影**：`you_are.shop_items`（價格／買不買得起／道具名）只在「這節點有店＋有表」時出現。
  U8 **節點閘**：沒店的地方 ⇒ 引擎既有的 `buy` 檢查擋住（不靜默）；兩邊都沒貨品 ⇒ 當場叫。
  U9 **事件溯源**：買過東西的流 `op=replay` 一致。
"""
from __future__ import annotations
from turn_helper import ensure_turn, drop_scene  # 拆景后的测试小工具

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo 根
sys.path.insert(0, HERE)
from trpg.core import layout as layout                                                          # noqa: E402
from trpg.rules import scenario as scenario

CAMP = layout.must_locate_scenario(HERE, "hyakki_tower", "sannenme_no_onibarai")
DEF = layout.def_dir_of(CAMP)          # 定義層（WS-B：桌目錄裡只剩 config／runs）
from trpg.rules import packs as packs                                                             # noqa: E402
MODF = scenario.module_filename(DEF) or "scenario.json"   # 劇本規則資料的檔名（WS-B：新名優先，別硬編）

from trpg.rules import shop as shop                                                              # noqa: E402
from trpg.core import store as store                                                             # noqa: E402
from trpg.engine import Table                                                 # noqa: E402

OK, FAIL = [], []
SPEC = ["d20core", "board", "semic", "mode.turn", "wound", "rules.core", "hyakki_world"]


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


def _raises(fn, exc=Exception):
    try:
        fn()
    except exc:
        return True
    except Exception as exc2:                                             # noqa: BLE001
        print(f"    （丟的是 {type(exc2).__name__}: {exc2}）")
        return False
    return False


def mk_home(*, spec=SPEC, cfg_patch=None, shop_at_start=False):
    """⚠ 現役 `board.json` 只有 **hyakki_f2** 有 `"shop": true`（ツルギ的便利店＝塔 2 階）；其他城市只有
    `shop` **標籤**（地圖圖示）而沒有欄位。測試要驗「在店裡買」的完整路徑，所以在**測試自己的**劇本檔上
    把起點 `teckan` 補上 `"shop": true`（不動現役劇本）。"""
    tmp = tempfile.mkdtemp(prefix="trpg_shop_")
    packs.seed_module_packs(DEF, tmp)   # 模組層的包（rules.core 住模組 repo；臨時桌推不出模組）
    for f in ("board.json", "goals.json", "clues.json", "seats.json", MODF):
        shutil.copy(os.path.join(DEF, f), os.path.join(tmp, f))
    layout.copy_table_config(CAMP, os.path.join(tmp, "config.json"))   # 旋鈕 ⇒ 桌目錄（定義在 DEF）
    if shop_at_start:
        bp = os.path.join(tmp, "board.json")
        bd = json.load(open(bp, encoding="utf-8"))
        for n in (bd.get("nodes") or bd.get("map") or []):
            if n.get("id") == "teckan":
                n["shop"] = True
        with open(bp, "w", encoding="utf-8") as fh:
            json.dump(bd, fh, ensure_ascii=False)
    shutil.copytree(os.path.join(DEF, "packs"), os.path.join(tmp, "packs"), dirs_exist_ok=True)
    cfg = json.load(open(os.path.join(tmp, "config.json"), encoding="utf-8"))
    cfg["port"] = 0
    cfg["packs"] = {"enabled": list(spec)}
    if cfg_patch:
        cfg_patch(cfg)
    with open(os.path.join(tmp, "config.json"), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False)
    return tmp, cfg


def open_first_scene(t):
    """夾具：把「第一景開著」擺好，再讓人齊了開第一輪。

    ⚠ 2026-10-08 起 `_maybe_autostart` 要求**先開景**才會開輪（以前人一齊就開 ⇒ 沒有景卻在倒數）。
    而 2026-10-20 起 `act()` 又要求「只有當前回合那一席能動、沒有回合就不准動」——
    所以測試若只 `join` 就直接 `act()`，會撞「現在沒有進行中的回合」。這裡把兩件事一次做對：
    開景（`semi.scene` 有 no、沒 closed_at）→ 讓引擎自己開輪。
    """
    # 現役 config 的 `turn.min_seats_to_start` 是 2（那是一張真桌的設定），而測試只入座 1 人
    # ⇒ 人永遠「不齊」、輪永遠不開。這裡只為測試壓成 1（測的是商店／條件，不是開局規則）。
    t.cfg.setdefault("turn", {})["min_seats_to_start"] = 1
    t.cfg.setdefault("turn", {})["auto_start_first_turn"] = True     # 有些桌刻意關掉（測「不自動開輪」用）
    st = t.state
    if not isinstance(st.get("semi"), dict):
        st["semi"] = {}
    if st.get("turn"):
        return t                                   # 冪等：已經開好就不動（同一支測試可能呼叫兩次）
    _sc = (st.get("semi") or {}).get("scene") or {}
    if _sc and _sc.get("closed_at") is None:
        t._maybe_autostart()                       # 景開著但還沒開輪 ⇒ 只補開輪
        return t
    try:
        ensure_turn(t)          # 走正式路徑 ⇒ 有 `scene_open` 事件，回放才對得上
    except Exception:                             # noqa: BLE001
        drop_scene(type("_X", (), {"state": st})())
        t._maybe_autostart()
    assert st.get("turn"), "夾具失敗：開景後仍沒開輪（檢查 turn.min_seats_to_start 與 auto_start_first_turn）"
    # 把回合交給第一位玩家（測試都是一人入座）
    if (st.get("turn") or {}).get("seat") != "p1":
        try:
            t.end_turn(reason="end_turn")
        except Exception:
            pass
        t._maybe_autostart()
    return t


def mk_table(tmp, cfg):
    return Table(home=tmp, cfg=cfg, store=store.FileStore(tmp), clock=lambda: 1000.0)


def seat_up(t, sid="p1", name="カガリ", coins=None):
    t.join(seat=sid, name=name, kind="human",
           attrs={"str": 3, "agi": 3, "wil": 3, "luck": 2}, order=0)
    if coins is not None:
        t.state["seats"][sid]["star_coins"] = coins          # 測試直接鋪場景（不發事件：這一項不進回放比對）


def main() -> int:                                                        # noqa: C901
    print("== U1. 表 ==")
    rows = json.load(open(os.path.join(DEF, "packs", "hyakki_world", "tables", "shop.json"),
                          encoding="utf-8"))["rows"]
    check("U1a 商店表載得動（通用 rope ＋ 各店專屬；按節點賣貨）",
          len(rows) >= 10 and any(r["id"] == "rope" and r.get("nodes") is None for r in rows)
          and {"teckan", "minato", "yamato", "hyakki_f3"}
              <= {n for r in rows for n in (r.get("nodes") or [])},
          f"{len(rows)} 列")
    check("U1b 缺 id ⇒ 不合法（同 id 不同店是**正常**寫法 ⇒ 重複只由 load 的衝突檢查咬）",
          any("要有 id" in x for x in shop.validate([{"price": 1}]))
          and not shop.validate([{"id": "a", "nodes": ["x"]}, {"id": "a", "nodes": ["y"]}]))
    check("U1c price 亂寫／currency 亂寫 ⇒ 不合法",
          any("price" in x for x in shop.validate([{"id": "a", "price": "3"}]))
          and any("currency" in x for x in shop.validate([{"id": "a", "currency": "gold"}])))
    check("U1d `stock`（還沒做）／其他欄位 ⇒ 不合法（別以為寫了就有效）",
          any("stock" in x for x in shop.validate([{"id": "a", "stock": 3}])))
    check("U1e note 非字串 ⇒ 不合法",
          any("note" in x for x in shop.validate([{"id": "a", "note": 3}])))
    check("U1f `nodes` 亂寫 ⇒ 不合法（要是節點 id 的字串陣列）",
          any("nodes" in x for x in shop.validate([{"id": "a", "nodes": "teckan"}]))
          and any("nodes" in x for x in shop.validate([{"id": "a", "nodes": [1]}])))

    print("== U2. 載入守衛（大聲失敗，不等玩家按下去）==")
    tmp2, cfg2 = mk_home(spec=["d20core", "board", "semic", "mode.turn", "wound", "hyakki_world"])
    try:
        # 作品層包現在 **requires rules.core**（它的 shop 表賣的是規則書包的道具）⇒ 載入時就叫（第一道網）
        _err = None
        try:
            mk_table(tmp2, cfg2)
        except Exception as exc:                                          # noqa: BLE001
            _err = str(exc)
        check("U2a 作品層包 requires rules.core（賣道具）⇒ 沒開規則書＝載入時就叫",
              bool(_err) and "rules.core" in _err, f"{_err}")
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)
    # 第二道網（shop.py 自己的守衛）：真的出現「有 shop 表但沒有道具表」時也要叫
    class _Stub(shop.ShopMixin):
        item_table: Dict[str, Any] = {}
    check("U2b 有 shop 表但沒有道具表 ⇒ `shop_init` 當場叫（雙保險）",
          _raises(lambda: _Stub().shop_init([os.path.join(DEF, "packs", "hyakki_world",
                                                          "tables", "shop.json")]),
                  shop.ShopError))

    print("== U3–U6. 買（legacy 路 vs 表路）==")
    tmp, cfg = mk_home(shop_at_start=True)
    try:
        t = mk_table(tmp, cfg)
        seat_up(t, "p1", coins=5)
        open_first_scene(t)      # 開景⇒開輪（act 只准當前回合那一席動）
        check("U3a 這張桌的商店兩邊都有（道具表優先、其餘走 config 的 legacy 路）",
              t.shop_source() == "both", f"{t.shop_source()}")
        n0 = len(t.events)
        out = t.act(seat="p1", action="buy", target="heal_pack")
        types = [e.get("type") for e in t.events[n0:]]
        check("U4a 表路：貨幣 −price（teckan 店 2）＋買到的東西**真的進 inventory**",
              t.state["seats"]["p1"]["star_coins"] == 3 and t.items_of("p1").get("heal_pack") == 1
              and out.get("bought") == "heal_pack" and out.get("via") == "table",
              f"coins={t.state['seats']['p1']['star_coins']} items={t.items_of('p1')}")
        check("U4b 事件鏈：`shop_buy`（扣貨幣）→ `item_changed`（進庫存）",
              "shop_buy" in types and "item_changed" in types, f"{types}")
        n1 = len(t.events)
        # ⚠ 走 `act()` 的拒絕會留 `action_declared`／`rejected`（那是刻意的紀錄）——這裡看的是
        #   **「不留半套買賣」**：不能有 `shop_buy`／`item_changed`（＝沒扣錢、沒進貨）。
        _ok = _raises(lambda: t.act(seat="p1", action="buy", target="star_flare"), Exception)
        check("U5 貨幣不夠 ⇒ 拒，且不留半套（沒有 `shop_buy`／`item_changed`、貨幣沒動）",
              _ok and t.state["seats"]["p1"]["star_coins"] == 3
              and not any(e.get("type") in ("shop_buy", "item_changed") for e in t.events[n1:]),
              f"coins={t.state['seats']['p1']['star_coins']}")
        check("U6 沒這個商品 ⇒ 拒（列出可選）",
              _raises(lambda: t.act(seat="p1", action="buy", target="nope"), Exception))
        v = t.view(seat="p1")
        si = ((v.get("board") or {}).get("you_are") or {}).get("shop_items") or []
        _items_side = [x for x in si if not x["legacy"]]
        check("U7 投影 `shop_items`：**道具**（表路）＋**legacy 貨品**（config／包 defaults）都在清單裡",
              {x["item"] for x in _items_side} == {"heal_pack", "mana_drop", "star_flare", "arrow", "rope"}
              and any(x["item"] == "hot_soup" and x["legacy"] for x in si)   # 包 defaults 的熱湯
              and all(x["currency"] == "star_coins" for x in si)
              and any(x["affordable"] is False for x in si)                  # 貨幣剩 3 ⇒ star_flare(5) 買不起
              and any(x["name_jp"] == "回復の包" for x in _items_side),
              f"{[(x['item'], x['price'], x['affordable'], x['legacy']) for x in si]}")
        # ⚠ 測試自己鋪了貨幣（`seat_up(coins=)` 直接寫 state、沒有事件）⇒ 只比較其餘欄位
        _diff = t.replay_check().get("diff") or {}
        check("U9 買過東西的流回放一致（除了測試自己鋪的貨幣）",
              set(_diff) <= {"seats.p1.star_coins"}, f"{_diff}")
        # U10 按節點賣貨：不同店不同貨／不同價
        check("U10a teckan 賣的是它的專屬清單（通用 rope ＋ 城裡價的 heal_pack 等）",
              sorted(t.shop_ids("teckan")) == ["arrow", "heal_pack", "mana_drop", "rope", "star_flare"]
              and {r["id"]: r["price"] for r in t.shop_offers("teckan")}["heal_pack"] == 2,
              f"{[(r['id'], r['price']) for r in t.shop_offers('teckan')]}")
        check("U10b minato 賣的是另一組（星屑の閃光 4、heal_pack 3）",
              sorted(t.shop_ids("minato")) == ["heal_pack", "rope", "star_flare"]
              and {r["id"]: r["price"] for r in t.shop_offers("minato")}["star_flare"] == 4,
              f"{[(r['id'], r['price']) for r in t.shop_offers('minato')]}")
        n10 = len(t.events)
        check("U10c 那家店沒有的貨 ⇒ 拒（訊息列出**這家店**的可選）",
              _raises(lambda: t.act(seat="p1", action="buy", target="star_upgrade"), Exception)
              and not any(e.get("type") in ("shop_buy", "item_changed") for e in t.events[n10:]))
        # U3 legacy 路：**不在道具表裡的貨品**（包 defaults 的 hot_soup 等）照舊走 config 那條
        tmp3, cfg3 = mk_home(shop_at_start=True)
        try:
            t3 = mk_table(tmp3, cfg3)
            seat_up(t3, "p1", coins=5)
            open_first_scene(t3)      # 開景⇒開輪（act 只准當前回合那一席動）
            _m0 = int(t3.state["seats"]["p1"].get("memory") or 0)
            check("U3b 包 defaults 的貨品也在商店裡（hot_soup＝memory +5 的那種）",
                  "hot_soup" in (t3.tower_cfg.get("shop") or {}))
            o3 = t3.act(seat="p1", action="buy", target="hot_soup")
            check("U3c legacy 路一字不變：貨幣 −1、memory +5 生效、**不進 inventory**",
                  t3.state["seats"]["p1"]["star_coins"] == 4
                  and int(t3.state["seats"]["p1"].get("memory") or 0) == _m0 + 5
                  and t3.items_of("p1") == {} and o3.get("via") == "config",
                  f"coins={t3.state['seats']['p1']['star_coins']} memory={t3.state['seats']['p1'].get('memory')}")
            # 同一個 id 在兩邊都有時：**道具表優先**（heal_pack 在 items 表裡 ⇒ 買到的是道具）
            o3b = t3.act(seat="p1", action="buy", target="heal_pack")
            check("U3d 同 id 兩邊都有 ⇒ **道具表優先**（買到的是道具、進 inventory）",
                  o3b.get("via") == "table" and t3.items_of("p1").get("heal_pack") == 1,
                  f"via={o3b.get('via')} items={t3.items_of('p1')}")
        finally:
            shutil.rmtree(tmp3, ignore_errors=True)
        # U8：沒有店的地方（原樣劇本：起點沒有 shop 欄位）
        tmp8, cfg8 = mk_home()
        try:
            t8 = mk_table(tmp8, cfg8)
            seat_up(t8, "p1", coins=5)
            open_first_scene(t8)      # 開景⇒開輪（act 只准當前回合那一席動）
            t8.state["seats"]["p1"]["node"] = "w_teckan"        # 荒野（沒店）——測試鋪場景
            check("U8a 沒店的地方 buy ⇒ 引擎既有的節點檢查擋住（不靜默）",
                  _raises(lambda: t8.act(seat="p1", action="buy", target="heal_pack"), Exception))
        finally:
            shutil.rmtree(tmp8, ignore_errors=True)
        tmp9, cfg9 = mk_home(spec=["d20core", "board", "semic", "mode.turn"])
        try:
            cfg9.pop("shop", None)                     # 拆景後商店表可能來自 config ⇒ U8b 要「兩邊都沒有」
            (cfg9.get("tower") or {}).pop("shop", None)
            t9 = mk_table(tmp9, cfg9)
            seat_up(t9, "p1", coins=5)
            open_first_scene(t9)      # 開景⇒開輪（act 只准當前回合那一席動）
            check("U8b 兩邊都沒貨品（沒 shop 表、config 也沒寫）⇒ 當場叫（不是靜默沒反應）",
                  _raises(lambda: t9.act(seat="p1", action="buy", target="heal_pack"), Exception))
        finally:
            shutil.rmtree(tmp9, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
