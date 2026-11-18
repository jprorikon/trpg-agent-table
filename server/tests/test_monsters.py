#!/usr/bin/env python3
"""**遭遇／怪物表的自測**（★4，2026-10-07）——`packs/hyakki_world/tables/monsters.json`。

    python3 test_monsters.py

設計者拍「繼續做 4」：把塔裡小怪的數值從程式搬到表（作者能換敵人）。

  V1 **表**：載得動（3 種）；schema 壞（缺 id／id 重複／數值亂寫／`nodes` 亂寫／`boss` 非 bool／
     `depth_min > depth_max`／不認識的欄位）當場叫。
  V2 **沒表＝legacy**：fight 走舊的 config／包預設（數值一模一樣）；投影不加 `monster` 欄位。
  V3 **選擇規則**：`nodes` 命中 ＞ `depth_min/max` 命中 ＞ 通用（用假節點直接咬）。
  V4 **fight 真的用表**：`hyakki_f4`＝塔の主 ⇒ DC 17、掉落 coins 6／exp 20、失敗傷害 ≤ 8、
     事件帶 `monster`／`name`；打光該格 ⇒ `boss_defeated`。
  V5 **投影**：塔內樓層露 `you_are.monster`（名字／DC／掉落）；城裡（沒深度、沒小怪）不露。
  V6 **收窄**：沒啟用帶怪物表的包 ⇒ 投影沒有 `monster`、fight 走舊路（不報錯、不亂發事件）。
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

from trpg.rules import monsters as monsters                                                         # noqa: E402
from trpg.core import store as store                                                            # noqa: E402
from trpg.engine import Table                                                # noqa: E402

OK, FAIL = [], []
SPEC = ["d20core", "board", "semic", "mode.turn", "wound", "rules.core", "hyakki_world"]
LEGACY = ["d20core", "board", "semic", "mode.turn"]


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


def _raises(fn, exc=Exception):
    try:
        fn()
    except exc:
        return True
    except Exception as exc2:                                            # noqa: BLE001
        print(f"    （丟的是 {type(exc2).__name__}: {exc2}）")
        return False
    return False


def mk_home(*, spec=SPEC):
    tmp = tempfile.mkdtemp(prefix="trpg_mon_")
    packs.seed_module_packs(DEF, tmp)   # 模組層的包（rules.core 住模組 repo；臨時桌推不出模組）
    for f in ("board.json", "goals.json", "clues.json", "seats.json", MODF):
        shutil.copy(os.path.join(DEF, f), os.path.join(tmp, f))
    layout.copy_table_config(CAMP, os.path.join(tmp, "config.json"))   # 旋鈕 ⇒ 桌目錄（定義在 DEF）
    shutil.copytree(os.path.join(DEF, "packs"), os.path.join(tmp, "packs"), dirs_exist_ok=True)
    cfg = json.load(open(os.path.join(tmp, "config.json"), encoding="utf-8"))
    cfg["port"] = 0
    cfg["packs"] = {"enabled": list(spec)}
    with open(os.path.join(tmp, "config.json"), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False)
    return tmp, cfg


def mk_table(tmp, cfg):
    return Table(home=tmp, cfg=cfg, store=store.FileStore(tmp), clock=lambda: 1000.0)


def seat_up(t, sid="p1", name="カガリ"):
    return t.join(seat=sid, name=name, kind="human",
                  attrs={"str": 3, "agi": 3, "wil": 3, "luck": 2}, order=0)


def open_first_scene(t):
    """夾具：開第一景 ⇒ 讓人齊了開第一輪（2026-10-08 起先開景才開輪；2026-10-20 起只有當前回合那一席能 act）。"""
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
    ensure_turn(t)
    assert st.get("turn"), "夾具失敗：開景後仍沒開輪（檢查 turn.min_seats_to_start／pacing.mode）"
    return t


def main() -> int:                                                       # noqa: C901
    print("== V1. 表 ==")
    rows = json.load(open(os.path.join(DEF, "packs", "hyakki_world", "tables", "monsters.json"),
                          encoding="utf-8"))["rows"]
    check("V1a 怪物表載得動（9 種：通用／分層／節點專屬／BOSS）",
          [r["id"] for r in rows] == ["yabusame", "hone_musha", "gaki", "oni_sho", "hyakki_no_aruji",
                                      "hone_yumi", "onibi", "jorougumo", "hitotsume"],
          f"{[r['id'] for r in rows]}")
    check("V1b 缺 id／id 重複 ⇒ 不合法",
          any("要有 id" in x for x in monsters.validate([{"dc": 13}]))
          and any("重複" in x for x in monsters.validate([{"id": "a"}, {"id": "a"}])))
    check("V1c 數值亂寫／check 非字串 ⇒ 不合法",
          any("dc" in x for x in monsters.validate([{"id": "a", "dc": "13"}]))
          and any("check" in x for x in monsters.validate([{"id": "a", "check": 3}])))
    check("V1d `nodes` 亂寫／`boss` 非 bool ⇒ 不合法",
          any("nodes" in x for x in monsters.validate([{"id": "a", "nodes": "teckan"}]))
          and any("boss" in x for x in monsters.validate([{"id": "a", "boss": "yes"}])))
    check("V1e `depth_min > depth_max` ⇒ 不合法（那永遠不會命中）",
          any("depth_min" in x for x in monsters.validate([{"id": "a", "depth_min": 4, "depth_max": 2}])))
    check("V1f 不認識的欄位 ⇒ 不合法（`hp` 已經是認得的欄位，改用真的不認識的）",
          any("不認識的欄位" in x for x in monsters.validate([{"id": "a", "hpp": 10}])))
    check("V1g `hp`／`count`／`active` 的形狀檢查",
          any("hp" in x for x in monsters.validate([{"id": "a", "hp": 0}]))
          and any("count" in x for x in monsters.validate([{"id": "a", "count": 0}]))
          and any("active" in x for x in monsters.validate([{"id": "a", "active": 1}]))
          and not monsters.validate([{"id": "a", "hp": 3, "count": 2, "active": {"chance": 0.5}}]))

    print("== V3. 選擇規則（nodes ＞ depth ＞ 通用）==")
    tmp, cfg = mk_home()
    try:
        t = mk_table(tmp, cfg)
        n_x5 = {"id": "x_f5", "depth": 5}          # 沒有任何列點名的節點（只看深度）
        n_f3 = {"id": "hyakki_f3", "depth": 3}
        n_f6 = {"id": "hyakki_f6", "depth": 6}
        n_city = {"id": "teckan", "depth": None}
        check("V3a `nodes` 指定 ⇒ 最優先（hyakki_f6＝百鬼夜行の主）",
              (t.monster_for(n_f6) or {}).get("id") == "hyakki_no_aruji")
        check("V3b 深度範圍 ⇒ 次之（3～4 階＝餓鬼；5～6 階＝鬼将）",
              (t.monster_for(n_f3) or {}).get("id") == "gaki"
              and (t.monster_for(n_x5) or {}).get("id") == "oni_sho")
        check("V3c 都不命中 ⇒ 通用（城裡也是通用的野伏せの鬼，但投影不會露——V5）",
              (t.monster_for(n_city) or {}).get("id") == "yabusame")
        print("== V4. fight 真的用表 ==")
        seat_up(t, "p1")
        open_first_scene(t)
        t.state["seats"]["p1"]["node"] = "hyakki_f6"          # 測試鋪場景（下面不比對回放的 node 欄位）
        t.state["board"]["mobs"]["hyakki_f6"] = 1
        out = t.act(seat="p1", action="fight")
        roll = out.get("roll") or {}
        check("V4a 檢定 DC 用表的 19（＋這一格的 danger；不是舊的 13）",
              roll.get("dc") == 19 + int(t.node("hyakki_f6").get("danger", 0)), f"dc={roll.get('dc')}")
        evs = [e for e in t.events if e.get("type") == "mob_killed"]
        if evs:
            check("V4b 擊破 ⇒ coins 用表的 10、事件帶 `monster`／`name`",
                  out.get("star_coins") == 10 and evs[-1].get("monster") == "hyakki_no_aruji"
                  and evs[-1].get("name") == "百鬼夜行の主",
                  f"coins={out.get('star_coins')} ev={evs[-1].get('monster')}")
            check("V4c 打光該格（`hyakki_f6`）⇒ `boss_defeated`（表的 `boss: true` 那條路也通）",
                  any(e.get("type") == "boss_defeated" for e in t.events))
        else:
            check("V4b 沒打倒（BOSS hp 24）⇒ 傷害用表的 die 10（≤ 10＋danger）",
                  (out.get("hit_by_mob") or 0) <= 10 + int(t.node("hyakki_f6").get("danger", 0)), f"dmg={out.get('hit_by_mob')}")
            check("V4c 沒擊破 ⇒ 沒有 `boss_defeated`",
                  not any(e.get("type") == "boss_defeated" for e in t.events))
        v = t.view(seat="p1")
        ya = (v.get("board") or {}).get("you_are") or {}
        check("V5a 塔內樓層露 `you_are.monster`（名字／DC／掉落）",
              (ya.get("monster") or {}).get("id") == "hyakki_no_aruji"
              and (ya.get("monster") or {}).get("dc") == 19,
              f"{ya.get('monster')}")
        t.state["seats"]["p1"]["node"] = "teckan"
        ya2 = ((t.view(seat="p1").get("board") or {}).get("you_are") or {})
        check("V5b 城裡（沒深度、沒小怪）不露 `monster`（不會無緣無故顯示怪物）",
              "monster" not in ya2)
        print("== V2／V6. 沒表＝legacy ==")
        tmp2, cfg2 = mk_home(spec=LEGACY)
        try:
            t2 = mk_table(tmp2, cfg2)
            seat_up(t2, "p1")
            open_first_scene(t2)
            t2.state["seats"]["p1"]["node"] = "hyakki_f1"     # 塔內（board.json 給 3 隻）
            out2 = t2.act(seat="p1", action="fight")
            check("V2a 沒表 ⇒ DC 走舊的 `mob_dc`(13)（＋danger）＝一字不變",
                  (out2.get("roll") or {}).get("dc") == 13 + int(t2.node("hyakki_f1").get("danger", 0)),
                  f"dc={(out2.get('roll') or {}).get('dc')}")
            _k = [e for e in t2.events if e.get("type") == "mob_killed"]
            if _k:
                check("V2b 沒表 ⇒ coins 走舊的 `star_coin_per_kill`(2)＋luck、事件不帶 monster",
                      out2.get("star_coins") == 2 + max(0, 2 - int(t2.rules.get("attr_default") or 0))
                      and _k[-1].get("monster") is None,
                      f"coins={out2.get('star_coins')}")
            else:
                check("V2b 沒表 ⇒ 失手傷害走舊的 die 4（≤ 4＋danger）",
                      (out2.get("hit_by_mob") or 0) <= 4)
            check("V6 沒表 ⇒ 投影沒有 `monster` 欄位",
                  "monster" not in (((t2.view(seat="p1").get("board") or {}).get("you_are") or {})))
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("== B1 BOSS 血量：一個人一回合打不死（協作誘因） ==")
    _mf = os.path.join(DEF, "packs", "hyakki_world", "tables", "monsters.json")
    if os.path.exists(_mf):
        _rows = json.load(open(_mf, encoding="utf-8")).get("rows") or []
        _boss = next((r for r in _rows if r.get("boss")), {})
        _hp = int(_boss.get("hp") or 1)
        # 一回合最多打幾下＝actions_per_turn；一擊最多多少＝傷害骰面 ＋ 屬性加值（寬估 3）
        _acts = 2
        _max_hit = 6 + 3
        check("B1a BOSS 有血量（不是一擊必殺）", _hp > 1, f"hp={_hp}")
        check("B1b 一回合的最大輸出打不死 BOSS（逼合作）",
              _hp > _max_hit * _acts,
              f"hp={_hp} vs 單人上限≒{_max_hit * _acts}")
        check("B1c BOSS 會主動攻擊（同層玩家有機會被咬）",
              bool(_boss.get("active")), str(_boss.get("active")))
    else:
        print("  （這一齣沒有怪物表，跳過）")

    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

