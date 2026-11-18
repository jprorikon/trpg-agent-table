#!/usr/bin/env python3
"""**機制包（`packs/`）的自測**——邊界宣告／啟用清單／合併視圖的閘。

    python3 test_packs.py

九組：
  P1 **解析**：拓撲排序、缺硬依賴、不存在的包、兩個節拍包、衝突、成環、未知 provides 鍵。
  P2 **合併**：動作／效果／事件／state 欄位／指標／參數／句庫／表。
  P3 **與內核一致**（漂移閘）：`KERNEL_*` 池 vs 實作；`verify_impl` 咬「包不能發明機制」。
  P4 **行為等價**：同一份凍結事件流，**config 全寫（無包）** vs **config 只留覆寫（啟用包）**
     ⇒ 視圖／尾巴／回放逐字相同（＝「機制常數搬進包」這件事對玩家是透明的）。
  P5 **凍結**：`table_created` 帶 ids＋hash；換清單 ⇒ 換 hash；重開桌讀得回同一組。
  P6 **消費端**：動作目錄並集、節拍包覆寫、effect 收窄、`pack.*` 詞彙表、日誌白名單只增不減。
  P7 **作者入口**：`tools/check_campaign.py` 對包做同一套判據（不另養第二份規則）。
  S  **機制預設值**（`provides.defaults`，2026-10-07 批次 2）：深合併（config 只寫覆寫）／
     段名寫錯當場叫／兩包撞同一鍵當場叫／進 hash／沒有 rules 又沒包 ⇒ 當場叫。
  T  **作品層包**（`<劇本>/packs/<id>/`，2026-10-07 批次 3）：真用例（現役那一包）／
     包的表（`kind=artifacts`）／同名覆蓋的權威鏈（config ＞ 作品層 ＞ 引擎層）。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo 根
sys.path.insert(0, HERE)
from trpg.core import layout as layout                                                          # noqa: E402
CAMP = layout.must_locate_scenario(HERE, "hyakki_tower", "sannenme_no_onibarai")
DEF = layout.def_dir_of(CAMP)          # 定義層（WS-B：桌目錄裡只剩 config／runs）
FROZEN = "/srv/workspace/_layer_frozen"

from trpg.engine import director as director                                                          # noqa: E402
from trpg.rules import packs as packs                                                             # noqa: E402
from trpg.engine import presentation as presentation                                                      # noqa: E402
from trpg.engine import projection as projection                                                        # noqa: E402
from trpg.rules import scenario as scenario                                                          # noqa: E402
MODF = scenario.module_filename(DEF) or "scenario.json"   # 劇本規則資料的檔名（WS-B：新名優先，別硬編）
from trpg.core import store as store                                                             # noqa: E402
from trpg.engine import Table                                                 # noqa: E402
from trpg.core.errors import RuleError                                                # noqa: E402

OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


def _raises(fn, exc):
    try:
        fn()
    except exc:
        return True
    except Exception as exc2:                                              # noqa: BLE001
        print(f"    （丟的是 {type(exc2).__name__}: {exc2}）")
        return False
    return False


def mk_pack(tmp, pid, data):
    """在臨時劇本目錄的**作品層**放一個包（`<劇本>/packs/<id>/pack.json`）。"""
    d = os.path.join(tmp, "packs", pid)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "pack.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    return d


def mk_home(*, packs_spec=None, data_from=FROZEN, files=True):
    """臨時劇本目錄：JSON 檔照抄現役劇本；事件流預設抄**凍結流**（不會一邊跑一邊長）。

    `data_from=None` ⇒ 不抄事件流（＝新局：桌子自己發 `table_created`）。
    """
    tmp = tempfile.mkdtemp(prefix="trpg_packs_")
    packs.seed_module_packs(DEF, tmp)   # 模組層的包（rules.core 住模組 repo；臨時桌推不出模組）
    if files:
        for f in ("board.json", "goals.json", "clues.json", "seats.json", MODF):
            shutil.copy(os.path.join(DEF, f), os.path.join(tmp, f))
        layout.copy_table_config(CAMP, os.path.join(tmp, "config.json"))   # 旋鈕 ⇒ 桌目錄（定義在 DEF）
    if data_from:
        store.copy_current_run(data_from, tmp)     # 現役那一局（一局一檔；舊制 data/ 也吃）
    cfg = json.load(open(os.path.join(tmp, "config.json"), encoding="utf-8"))
    cfg["port"] = 0
    # ⚠ 現役劇本的 config.json **已經有** packs 了（本輪落地的）⇒ `None` 必須=「明確拿掉」，
    #   否則「legacy 對照組」其實也啟用了包（P30 就是這樣抓到的）。
    if packs_spec is None:
        cfg.pop("packs", None)
    else:
        cfg["packs"] = packs_spec
    with open(os.path.join(tmp, "config.json"), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False)
    return tmp, cfg


def mk_table(tmp, cfg):
    return Table(home=tmp, cfg=cfg, store=store.FileStore(tmp), clock=lambda: 1000.0)


# 現役劇本應該長的那一組（＝等價基準；含 wound＝部位受傷／異常狀態）
LIVE_PACKS = ["d20core", "board", "semic", "mode.turn", "wound", "rules.core"]
#: 內核自己會發的事件（不是包宣告的）——★account 層的 `coin_grant` 在這裡
KERNEL_EVENTS = ("coin_grant",)


def fill_defaults(cfg, view):
    """把包的機制預設值**顯式寫進** config（＝「手寫桌／legacy」對照組的寫法）。

    刻意走**另一條路**（測試自己寫進 config 檔），不是叫 `packs.apply_defaults`——
    那樣子兩邊都是同一段程式，測不到「合併是透明的」。
    """
    out = json.loads(json.dumps(cfg))
    for sec, blk in (view.defaults or {}).items():
        base = out.get(sec)
        out[sec] = packs._deep_fill(base if isinstance(base, dict) else {}, blk)   # noqa: SLF001
    return out


def main() -> int:
    print("== P1. 解析（拓撲／缺依賴／衝突／成環／未知項）==")
    legacy = packs.resolve(None)
    check("P1 沒寫 packs ⇒ legacy（一個包都沒啟用，各層照舊走 config）",
          not legacy.enabled and legacy.ids == [] and legacy.hash
          and packs.resolve([]).ids == [] and packs.resolve("").ids == [])
    v = packs.resolve(LIVE_PACKS, home=DEF)
    check("P2 正常解析：拓撲序（依賴在前）＋ 節拍包認出來了",
          v.ids == LIVE_PACKS and v.enabled
          and (v.mode or {}).get("name") == "turn", f"{v.ids}")
    check("P3 硬依賴不在清單裡 ⇒ 當場叫（不自動補）",
          _raises(lambda: packs.resolve(["example"]), packs.PackError))
    check("P4 清單裡的包不存在 ⇒ 當場叫（列出註冊表）",
          _raises(lambda: packs.resolve(["d20core", "nope"]), packs.PackError))
    check("P5 兩個節拍包 ⇒ 當場叫（一次只能一個）",
          _raises(lambda: packs.resolve(["d20core", "mode.turn", "mode.scene"]),
                  packs.PackError))

    t1, _ = mk_home()
    try:
        mk_pack(t1, "a_a", {"id": "a_a", "conflicts": ["a_b"], "provides": {}})
        mk_pack(t1, "a_b", {"id": "a_b", "provides": {}})
        check("P6 衝突宣告 ⇒ 不能同時啟用",
              _raises(lambda: packs.resolve(["a_a", "a_b"], home=t1), packs.PackError)
              and packs.resolve(["a_a"], home=t1).ids == ["a_a"])
        for pid, dep in (("c_a", "c_b"), ("c_b", "c_a")):
            mk_pack(t1, pid, {"id": pid, "requires": [dep], "provides": {}})
        check("P7 依賴成環 ⇒ 當場叫（附環路）",
              _raises(lambda: packs.resolve(["c_a", "c_b"], home=t1), packs.PackError))
        mk_pack(t1, "d_bad", {"id": "d_bad", "provides": {"nope": [1]}})
        check("P8 未知 provides 鍵 ⇒ 包不合法（加新的一格＝工程）",
              _raises(lambda: packs.resolve(["d_bad"], home=t1), packs.PackError))
        mk_pack(t1, "d_eff", {"id": "d_eff", "provides": {"effects": ["spell_slot"]}})
        check("P9 effect 不在內核池裡 ⇒ 包不合法（包不能發明機制）",
              _raises(lambda: packs.resolve(["d_eff"], home=t1), packs.PackError))
        mk_pack(t1, "d_mode", {"id": "d_mode", "provides": {"mode": {"name": "initiative"}}})
        check("P10 內核沒有的節拍名 ⇒ 包不合法（新的節拍要先工程支援）",
              _raises(lambda: packs.resolve(["d_mode"], home=t1), packs.PackError))
        mk_pack(t1, "d_key", {"id": "d_key", "provides": {"state_keys": ["wound_state"]}})
        check("P11 沒有的 state 欄位 ⇒ 包不合法（state 的唯一正本是 state_default）",
              _raises(lambda: packs.resolve(["d_key"], home=t1), packs.PackError))
    finally:
        shutil.rmtree(t1, ignore_errors=True)

    print("== P2. 合併（動作／效果／事件／欄位／指標／參數／句庫／表）==")
    check("P12 動作並集：config 的優先，包補上 config 沒有的（還原成 config 順序在前）",
          [a["id"] for a in packs.effective_action_entries(
              [{"id": "attack", "label": "（config 的）"}], v)][:2] == ["attack", "check"]
          and packs.effective_action_entries([{"id": "attack", "label": "x"}], v)[0]["label"] == "x")
    # ⚠ 每加一種 effect／事件／state 欄位（＝加機制），這裡的數字要跟著動：
    #   **那是故意的**——「包宣告的東西剛好等於內核實作」是這條在咬的。
    check("P13 效果＝內核那一整池（含 condition_set／attr_mod／resource_*）；"
          "事件＝白名單＋包宣告的那四條；state 欄位含 board／semi／conditions／attr_mods／resources",
          set(v.effects) == set(scenario.EFFECT_KINDS)
          and {"board", "semi", "conditions", "attr_mods", "resources", "exp", "level"}
          <= set(v.state_keys),
          f"events={len(v.events)} effects={len(v.effects)}")
    check("P14 參數合併：包 params ＋ config 覆寫（config 贏）",
          packs.resolve({"enabled": ["d20core"], "params": {"k": 2}}).params == {"k": 2}
          and packs.resolve(["example", "d20core"]).params.get("example_bonus") == 1)
    t2, _ = mk_home()
    try:
        mk_pack(t2, "e_a", {"id": "e_a", "provides": {"actions": [{"id": "check", "label": "甲"}]}})
        mk_pack(t2, "e_b", {"id": "e_b", "provides": {"actions": [{"id": "check", "label": "乙"}]}})
        check("P15 同一動作被兩個包宣告成不同內容 ⇒ 當場叫（別留兩個家）",
              _raises(lambda: packs.resolve(["e_a", "e_b"], home=t2), packs.PackError))
    finally:
        shutil.rmtree(t2, ignore_errors=True)
    _vx = packs.resolve(["d20core", "example"])
    _txt = {"example.hello": "（樣板包）{who}，こんにちは。"}
    _bk = packs.text_bank_for(_vx, None)
    check("P16 句庫：包帶的 text.json 疊進**這一桌的**句庫（宣告要完整；疊完叫得出來；不漏進全域）",
          _vx.texts == _txt and "example.hello" in _bk and "example.hello" not in presentation.TEXT
          and presentation.text("example.hello", _bk, who="p1").endswith("p1，こんにちは。"))
    t2b = tempfile.mkdtemp(prefix="trpg_packs_")
    try:
        for _pid, _w in (("t_a", "甲"), ("t_b", "乙")):
            _d = mk_pack(t2b, _pid, {"id": _pid, "provides": {"text_keys": ["t.same"]}})
            with open(os.path.join(_d, "text.json"), "w", encoding="utf-8") as fh:
                json.dump({"t.same": _w}, fh, ensure_ascii=False)
        check("P17 同一個句庫鍵被兩個包寫成不同文案 ⇒ 當場叫（不是後面蓋前面）",
              _raises(lambda: packs.resolve(["d20core", "t_a", "t_b"], home=t2b), packs.PackError))
    finally:
        shutil.rmtree(t2b, ignore_errors=True)
    t2c = tempfile.mkdtemp(prefix="trpg_packs_")
    try:
        with open(os.path.join(t2c, "text.json"), "w", encoding="utf-8") as fh:
            json.dump({"white_room.text": "（劇本層）別的字", "_doc": "註記"}, fh, ensure_ascii=False)
        _kern = presentation.TEXT["white_room.text"]
        _lv = packs.PackView.legacy()
        _b1, _b0 = packs.text_bank_for(_lv, t2c), packs.text_bank_for(_lv, None)
        check("P17b 劇本／模組層的 text.json 蓋內核措辭——**只蓋這一桌**（全域 TEXT 不動、別桌照舊）",
              _b1["white_room.text"] == "（劇本層）別的字" and "_doc" not in _b1
              and _b0["white_room.text"] == _kern == presentation.TEXT["white_room.text"])
    finally:
        shutil.rmtree(t2c, ignore_errors=True)
    check("P18 表：路徑真的存在（相對包目錄）",
          all(os.path.exists(p) for p in packs.resolve(["d20core", "example"]).tables))
    t3, _ = mk_home()
    try:
        mk_pack(t3, "f_tbl", {"id": "f_tbl", "provides": {"tables": ["tables/nope.json"]}})
        check("P19 表不在 ⇒ 包不合法",
              _raises(lambda: packs.resolve(["f_tbl"], home=t3), packs.PackError))
    finally:
        shutil.rmtree(t3, ignore_errors=True)

    print("== P3. 與內核一致（漂移閘）==")
    check("P20 KERNEL_EFFECTS 就是 scenario 的實作池（唯一正本）",
          tuple(packs.KERNEL_EFFECTS) == tuple(scenario.EFFECT_KINDS))
    check("P21 KERNEL_SIGNALS 就是 L3 真的算得出的指標（兩邊一致）",
          set(packs.KERNEL_SIGNALS) == set(director.Director(None).signals([], {})),
          f"差={set(packs.KERNEL_SIGNALS) ^ set(director.Director(None).signals([], {}))}")
    t4, cfg4 = mk_home()
    t4t = mk_table(t4, cfg4)
    try:
        # board／semi 是**單一頂層欄位**（裡面那本字典才是子欄位），別把子欄位當頂層
        real = set(t4t.state_default()) | {"board", "semi", "run_id"}
        check("P22 KERNEL_STATE_KEYS 就是 state 真的有的頂層欄位（漂移＝當場紅）",
              real == set(packs.KERNEL_STATE_KEYS),
              f"只有 state 有={sorted(real - set(packs.KERNEL_STATE_KEYS))} "
              f"只有清單有={sorted(set(packs.KERNEL_STATE_KEYS) - real)}")
    finally:
        shutil.rmtree(t4, ignore_errors=True)
    # 內核白名單 ⊆ 包宣告的事件；「多出來的」只能是**包自己宣告的型別**
    # （新事件型別由包宣告，不進 projection.VIEW_LOG_TYPES——那條由 test_layers H11 咬）
    _packed_ev = set(packs.resolve(["d20core", "board", "semic"]).events)
    check("P23 d20core＋board＋semic：內核白名單一個不少，多出來的只有包宣告的",
          set(projection.VIEW_LOG_TYPES) <= _packed_ev
          # ★ 2026-10-20：`clock_changed` 也是包（d20core）宣告的內核原語事件
          # ★ 2026-10-21：`monster_damaged`＝棋盤包的戰鬥事件（怪物有血量之後才存在；
          #   不進內核白名單，否則沒開棋盤的桌也會收到它）
          and _packed_ev - set(projection.VIEW_LOG_TYPES) == {"attr_mod_set", "resource_changed",
                                                             "clock_changed", "monster_damaged"},
          f"多={sorted(_packed_ev - set(projection.VIEW_LOG_TYPES))} "
          f"少={sorted(set(projection.VIEW_LOG_TYPES) - _packed_ev)}")
    _allsk = packs.resolve(["d20core", "board", "semic", "wound", "rules.core",
                            "death"], home=DEF).state_keys
    check("P24 引擎層＋模組層（rules.core）的包宣告的 state 欄位並集＝KERNEL_STATE_KEYS（每個欄位都有家）",
          set(_allsk) == set(packs.KERNEL_STATE_KEYS),
          f"沒人宣告={sorted(set(packs.KERNEL_STATE_KEYS) - set(_allsk))}")
    t5, _ = mk_home()
    try:
        mk_pack(t5, "g_ghost", {"id": "g_ghost", "provides": {"actions": ["frobnicate"],
                                                              "events": ["no_such_event"]}})
        check("P25 宣告內核沒實作的動作／事件 ⇒ verify_impl 當場叫（包不能發明機制）",
              _raises(lambda: packs.resolve(["g_ghost"], home=t5), packs.PackError))
        check("P26 verify=False 可以只做結構檢查（作者工具／快路徑用）",
              packs.resolve(["g_ghost"], home=t5, verify=False).ids == ["g_ghost"])
    finally:
        shutil.rmtree(t5, ignore_errors=True)

    print("== P4. 行為等價：config 全寫（無包） vs 只留覆寫（啟用包）——同一份凍結事件流 ==")
    _v4 = packs.resolve(LIVE_PACKS, home=DEF)
    snap = {}
    for label, spec, full in (("legacy", None, True), ("packed", LIVE_PACKS, False)):
        tmp, cfg = mk_home(packs_spec=spec)
        if full:                       # 手寫桌：機制常數全寫在 config 裡（沒有包可以補）
            cfg = fill_defaults(cfg, _v4)
            with open(os.path.join(tmp, "config.json"), "w", encoding="utf-8") as fh:
                json.dump(cfg, fh, ensure_ascii=False)
        try:
            t = mk_table(tmp, cfg)
            out = {"events": [e.get("type") for e in t.events], "n": len(t.events),
                   "replay": bool(t.replay_check().get("replay_matches")),
                   "actions": t.action_ids,
                   "catalog": t.actions_catalog, "mode": t.pacing_mode,
                   "log_types": sorted(t.log_types), "packs": t.packs.ids}
            for name, kw in (("p1", {"seat": "p1"}), ("p4", {"seat": "p4"}), ("anon", {}),
                             ("gm", {"gm": True}), ("p1_c", {"seat": "p1", "detail": "compact"})):
                try:
                    out[name] = t.view(**kw)
                except Exception as exc:                                   # noqa: BLE001
                    out[name] = {"__error__": f"{type(exc).__name__}: {exc}"}
            out["tail"] = t.render_log_tail(limit=200)
            out["tail_p1"] = t.render_log_tail(seat="p1", limit=60)
            snap[label] = out
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    # ⚠ `log_types` 不算在「逐字相同」裡：它本來就該**只增不減**（包宣告的事件型別加進來；
    #   沒有事件就不會出現在日誌 ⇒ 視圖與尾巴仍然一字不差）。單獨用 P27b 咬它。
    # ⚠ 動作目錄也不算在「逐字相同」裡：包**可以宣告「內核有這個動作」**（例 `rules.core`
    #   宣告 `cast`），config 只管「顯示哪些／什麼順序」⇒ 多出來的只能是包宣告的新動作。
    #   只增不減、順序不動這條另外咬（P27c）。
    # 頂層的 `actions`／`catalog` 也跳過：它們就是「動作目錄」，差的那一項由 P27c 單獨咬。
    _skip = ("packs", "log_types", "actions", "catalog")
    _new_acts = ("rescue",)   # 包宣告的新動作（2026-10-21 拆景：cast／use_item 收進 use）

    def _strip_new_actions(node):
        """把「包宣告的新動作」從視圖裡的動作目錄拿掉——**只准差這幾項，其餘逐字相同**。"""
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                if "action" in str(k).lower() and isinstance(v, list):
                    # 任何「動作」清單（`actions`／`allowed_actions`／`your_actions`…）
                    keep = []
                    for x in v:
                        xid = x.get("id") if isinstance(x, dict) else x
                        if xid not in _new_acts:
                            keep.append(x)
                    out[k] = keep
                else:
                    out[k] = _strip_new_actions(v)
            return out
        if isinstance(node, list):
            return [_strip_new_actions(x) for x in node]
        return node

    def _strip_created(node):
        """剔掉 `table_created`（run_id／packs／packs_hash 是這一局的身分證，兩邊本來就不同）。

        P28 另外咬「事件型別序列只剩 table_created 有差」——這裡只負責讓**其餘內容**逐字可比。
        """
        if isinstance(node, dict):
            return {k: (_strip_created(v) if k != "log" else
                        [e for e in (v or []) if not (isinstance(e, dict)
                                                      and e.get("type") == "table_created")])
                    for k, v in node.items()
                    if k != "run_id"}          # 每一局的 UUID（身分證，不是行為）
        if isinstance(node, list):
            return [_strip_created(x) for x in node]
        return node

    same = {k: _strip_created(_strip_new_actions(snap["legacy"][k]))
            for k in snap["legacy"] if k not in _skip}
    packed = {k: _strip_created(_strip_new_actions(snap["packed"][k]))
              for k in snap["packed"] if k not in _skip}
    check("P27 視圖／尾巴／動作目錄／節拍**逐字相同**（＝機制常數搬進包對玩家透明）",
          json.dumps(same, sort_keys=True, ensure_ascii=False)
          == json.dumps(packed, sort_keys=True, ensure_ascii=False),
          f"legacy {len(json.dumps(same))} 字元 vs packed {len(json.dumps(packed))}")
    _lg_extra = [a for a in snap["packed"]["actions"] if a not in snap["legacy"]["actions"]]
    _lg_extra = [a if isinstance(a, str) else a.get("id") for a in _lg_extra]
    # ⚠ config 也可以自己把包宣告的動作列進去（現役桌就是這樣）⇒ 只多出「config 沒列、包宣告的」那些
    _cfg_acts = [a if isinstance(a, str) else a.get("id") for a in (cfg["rules"].get("actions") or [])]
    _expect_extra = [a for a in _new_acts if a not in _cfg_acts]
    def _is_subseq(sub: list, full: list) -> bool:
        """legacy 的相對順序在 packed 裡不變（包可以插新動作，但不得打亂 config 定的順序）。"""
        it = iter(full)
        return all(any(x == y for y in it) for x in sub)

    check("P27c 動作目錄只多出「config 沒列、包宣告的」新動作（只增不減、舊順序不動）",
          _lg_extra == _expect_extra
          and _is_subseq(snap["legacy"]["actions"], snap["packed"]["actions"]),
          f"多出={_lg_extra}／expect={_expect_extra}／new_acts={_new_acts}／cfg_acts={_cfg_acts}")
    check("P27b 日誌白名單只增不減（包宣告的型別加進來，舊的一個不少）",
          set(snap["legacy"]["log_types"]) <= set(snap["packed"]["log_types"])
          and set(snap["packed"]["log_types"]) - set(snap["legacy"]["log_types"])
          # ★ 2026-10-20：`star_grant`（星等渠道計數；**不公開**）也是 rules.core 宣告的型別
          == {"condition_set", "condition_tick", "attr_mod_set", "resource_changed", "star_grant",
              "clock_changed",
              "skill_used", "exp_gained", "level_up", "item_changed", "star_up",
              "rescue_attempt",
              # ★ 2026-10-21：棋盤包的怪物戰鬥事件（怪物有血量之後人類才看得到傷害）
              "monster_damaged"},
          f"多出={sorted(set(snap['packed']['log_types']) - set(snap['legacy']['log_types']))}")
    check("P28 事件流**型別序列**也一樣（唯一的差別是 table_created 多帶 packs／hash）",
          snap["legacy"]["events"] == snap["packed"]["events"]
          and snap["legacy"]["n"] == snap["packed"]["n"],
          f"{snap['legacy']['n']} vs {snap['packed']['n']}")
    check("P29 兩邊回放都一致（包沒有讓狀態多出『非事件來源』的東西）",
          snap["legacy"]["replay"] and snap["packed"]["replay"])
    check("P30 啟用清單看得出來（legacy＝空、packed＝那四包）",
          snap["legacy"]["packs"] == [] and snap["packed"]["packs"] == LIVE_PACKS)

    print("== P5. 凍結（table_created 帶 ids＋hash）==")
    tmp, cfg = mk_home(packs_spec=LIVE_PACKS, data_from=None)   # 新局：桌子自己發 table_created
    try:
        t = mk_table(tmp, cfg)
        ev = next(e for e in t.events if e.get("type") == "table_created")
        check("P31 table_created 帶 packs／packs_hash",
              ev.get("packs") == LIVE_PACKS and isinstance(ev.get("packs_hash"), str)
              and len(ev["packs_hash"]) == 16)
        check("P32 同一份清單 ⇒ 同一個 hash（可重現）",
              ev["packs_hash"] == packs.resolve(LIVE_PACKS, home=DEF).hash)
        check("P33 換一份清單 ⇒ 換一個 hash（重放才分得出兩張桌）",
              packs.resolve(["d20core"]).hash != packs.resolve(LIVE_PACKS, home=DEF).hash)
        check("P34 重開同一張桌（同一份流）讀得回同一組包",
              mk_table(tmp, cfg).packs.ids == LIVE_PACKS)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("== P6. 消費端（動作目錄／節拍／effect 收窄／pack 詞彙表／日誌白名單）==")
    # ① 包宣告的動作會進「有效動作目錄」與 MCP／前端的列舉
    tmp6, cfg6 = mk_home(packs_spec=["d20core", "h_extra"], data_from=None)
    try:
        # 包的動作條目不能與別的**已啟用**包撞成不同內容（P15），所以這裡宣告 board 的 move
        mk_pack(tmp6, "h_extra", {"id": "h_extra",
                                 "provides": {"actions": [{"id": "move", "label": "移動（包）"}]}})
        cfg6["rules"]["actions"] = ["attack"]              # config 只開 attack
        with open(os.path.join(tmp6, "config.json"), "w", encoding="utf-8") as fh:
            json.dump(cfg6, fh, ensure_ascii=False)
        t6b = mk_table(tmp6, cfg6)
        # 語意：**動作只增不減**——包宣告的一定可用（config 沒開也一樣）；config 的在前、
        # 同 id 以 config 的文案為準（包是「我需要這個動作」，不是「我要覆寫它的顯示」）。
        check("P35 包的動作併進 action_ids（config 的在前、config 文案優先）",
              # ★ 2026-10-21 拆景：`pass` 不再是引擎宣告的動作（回合制「不出手」＝end_turn）
              t6b.action_ids == ["attack", "check", "defend", "use", "other",
                                 "end_turn", "move"]
              and [a["label"] for a in t6b.actions_catalog if a["id"] == "move"] == ["移動（包）"]
              and [a["label"] for a in t6b.actions_catalog if a["id"] == "attack"] == [""],
              f"{t6b.action_ids}")
    finally:
        shutil.rmtree(tmp6, ignore_errors=True)

    # ② 節拍包：config 沒寫的旋鈕由包補缺口；**config 有寫的一律 config 贏**（一個旋鈕一個權威）
    tmp7, cfg7 = mk_home(packs_spec=["d20core", "i_slow"], data_from=None)
    try:
        mk_pack(tmp7, "i_slow", {"id": "i_slow",
                                "provides": {"mode": {"name": "turn",
                                                      "turn": {"seconds": 99, "brand_new_knob": 1}}}})
        cfg7.pop("pacing", None)                       # config 沒寫節拍 ⇒ 包當預設
        t7 = mk_table(tmp7, cfg7)
        check("P36 節拍包補缺口（config 沒寫的鍵用包的；有寫的 config 贏）",
              t7.pacing_mode == "turn" and t7.turn_cfg["seconds"] == int(cfg7["turn"]["seconds"])
              and t7.turn_cfg["brand_new_knob"] == 1,
              f"mode={t7.pacing_mode} seconds={t7.turn_cfg['seconds']}")
        cfg7b = json.loads(json.dumps(cfg7))
        cfg7b["pacing"] = {"mode": "scene"}     # 舊旋鈕：引擎只跑回合制 ⇒ 當場拒
        _err = ""
        try:
            _t7b = mk_table(tmp7, cfg7b)
            _t7b.pacing_mode                      # 這個屬性現在當場判（延遲求值）
        except RuleError as exc:
            _err = str(exc)
        check("P36b config 寫 pacing.mode=scene ⇒ 當場拒（帶遷移提示，不靜默改成 turn）",
              "pacing.mode" in _err and "turn" in _err, _err[:80])
    finally:
        shutil.rmtree(tmp7, ignore_errors=True)

    # ③ effect 收窄：沒啟用會用到該 effect 的包 ⇒ 劇本當場被擋
    tmp8, _cfg8 = mk_home(packs_spec=["d20core"], data_from=None)
    try:
        for _n in scenario.MODULE_FILES:      # 合成模組寫權威名；先清掉另一個名字（真劇本會把它 shadow 掉）
            _p = os.path.join(tmp8, _n)
            if os.path.exists(_p):
                os.remove(_p)
        with open(os.path.join(tmp8, MODF), "w", encoding="utf-8") as fh:
            json.dump({"meta": {"id": "t"},
                       "triggers": [{"id": "x", "on": "say",
                                     "then": [{"ending": {"id": "e1"}}]}]}, fh)
        _err = ""
        try:
            scenario.Scenario.load(tmp8, allowed_effects=["emit", "fact_set"])
        except scenario.ModuleError as exc:
            _err = str(exc)
        check("P37 包沒宣告的 effect ⇒ 劇本**載入時**就叫（收窄真的生效）",
              "ending" in _err and "機制包" in _err, _err.splitlines()[-1][:80])
        sc2 = scenario.Scenario.load(tmp8, allowed_effects=list(scenario.EFFECT_KINDS))
        check("P38 內核整池（legacy 語意）⇒ 同一份劇本合法",
              sc2 is not None and sc2.validate() == [])
    finally:
        shutil.rmtree(tmp8, ignore_errors=True)

    # ④ pack.* 詞彙表（係數外部化：作者不必改程式）
    tmp9, cfg9 = mk_home(packs_spec=["d20core", "j_const"], data_from=None)
    try:
        mk_pack(tmp9, "j_const", {"id": "j_const", "params": {"low_memory": 7}})
        t9 = mk_table(tmp9, cfg9)
        ctx = t9.module.ctx(t9, seat="p1")
        check("P39 AST 看得到 pack.params／ids／mode（係數外部化）",
              ctx["pack"]["params"].get("low_memory") == 7
              and ctx["pack"]["ids"] == ["d20core", "j_const"],
              f"{ctx['pack']}")
    finally:
        shutil.rmtree(tmp9, ignore_errors=True)

    # ⑤ 日誌白名單只增不減：包宣告的事件才進公開日誌
    tmp10, cfg10 = mk_home(packs_spec=["d20core", "k_log"], data_from=None)
    try:
        mk_pack(tmp10, "k_log", {"id": "k_log", "provides": {"events": ["throttle_tick"]}})
        t10 = mk_table(tmp10, cfg10)
        check("P40 包宣告的事件進白名單（throttle_tick 原本不在白名單裡）",
              "throttle_tick" in t10.log_types
              and "throttle_tick" not in projection.VIEW_LOG_TYPES)
        t10._emit("throttle_tick", public=True, props={"a": 1})
        _tmp_lg, _cfg_lg = mk_home(packs_spec=None, data_from=None)
        _lg = mk_table(_tmp_lg, fill_defaults(_cfg_lg, packs.resolve(LIVE_PACKS, home=DEF)))
        check("P41 宣告了才進公開日誌（沒宣告的桌照舊不進）",
              any(e.get("type") == "throttle_tick" for e in t10.state["log"])
              and all(e.get("type") != "throttle_tick" for e in _lg.state["log"]))
    finally:
        shutil.rmtree(tmp10, ignore_errors=True)

    print("== P7. 作者入口（同一套判據）==")
    try:
        sys.path.insert(0, os.path.join(HERE, "tools"))
        import check_campaign as ck                                        # noqa: E402
        tmp11, _ = mk_home()
        try:
            mk_pack(tmp11, "m_ghost", {"id": "m_ghost", "requires": ["d20core"],
                                       "provides": {"actions": ["frobnicate"]}})
            errs, warns = ck.check(tmp11)
            check("P42 自檢器把「啟用了沒實作的包」當錯誤（與引擎同一套判據）",
                  any("frobnicate" in e or "實作" in e for e in errs), f"{errs[:2]}")
            shutil.rmtree(os.path.join(tmp11, "packs", "m_ghost"))   # packs/ 裡還有模組層抄來的包，只拔鬼包
            # 向後相容＝**手寫桌**（自己把 config 全寫滿、不啟用任何包）照舊 0 error；
            # 不是「精簡 config 又不啟用包」——那種桌沒有機制常數的來源，本來就該叫（S7）。
            _full11 = fill_defaults(json.load(open(os.path.join(tmp11, "config.json"),
                                                   encoding="utf-8")),
                                    packs.resolve(LIVE_PACKS, home=DEF))
            with open(os.path.join(tmp11, "config.json"), "w", encoding="utf-8") as fh:
                json.dump(_full11, fh, ensure_ascii=False)
            errs2, _ = ck.check(tmp11)
            check("P43 手寫桌（config 全寫、不啟用包）照舊 0 error（向後相容）",
                  not errs2, f"{errs2[:2]}")
        finally:
            shutil.rmtree(tmp11, ignore_errors=True)
    except Exception as exc:                                               # noqa: BLE001
        check("P42/P43 自檢器整合", False, f"{type(exc).__name__}: {exc}")

    print("== S. 機制預設值（provides.defaults：config 只寫覆寫）==")
    # ① 精簡 config ＋啟用包 ⇒ 生效值＝預期（**常數在測試裡字面寫死**，防包本身漂移）
    tmpS, cfgS = mk_home(packs_spec=LIVE_PACKS, data_from=None)
    try:
        for _s in ("rules", "board", "semi"):
            if _s in cfgS and _s != "rules":
                cfgS.pop(_s, None)          # 只留 rules.actions（順序權威）
        tS = mk_table(tmpS, cfgS)
        _r = tS.rules
        # ⚠ `attributes` 是**config 的權威**（現役 config 已寫 `["str","agi","wil","luck"]`＝示範模組
        #   四屬性；舊的三個名字保留、新增 `luck`）⇒ 這裡跟著 config，不是包預設。
        check("S1 機制常數由包補齊（rules／board／semi 的預設都生效）",
              (_r["name"], _r["attributes"], _r["attr_range"])
              == ("minimal-d20", ["str", "agi", "wil", "luck"], [1, 5])
              and _r["hp"] == {"base": 8, "per_str": 2}
              and _r["check"] == {"die": 20, "attr_multiplier": 2, "dc_default": 12}
              and _r["damage"] == {"die": 6, "attr": "str", "attr_multiplier": 1}
              and _r["defend_bonus"] == 3
              and tS.board_cfg["entrance_search_dc"] == 12
              and tS.board_cfg["travel_dc_by_danger"] == [8, 11, 14, 17]
              and tS.board_cfg["visibility"]["hide_gm"] is True
              and tS.semi_cfg["goals_file"] == "goals.json"
              and tS.semic_enabled and tS.board_enabled,
              f"rules={sorted(_r)} board={sorted(tS.board_cfg)} semi={sorted(tS.semi_cfg)}")

        # ② 深合併：config 只寫「子鍵」⇒ 同層其他鍵不能被吃掉
        cfgS2 = json.loads(json.dumps(cfgS))
        cfgS2["rules"] = {"actions": cfgS["rules"]["actions"],
                          "check": {"dc_default": 15}}
        tS2 = mk_table(tmpS, cfgS2)
        check("S2 深合併：config 只覆寫 rules.check.dc_default ⇒ 同層的 die／attr_multiplier 還在",
              tS2.rules["check"] == {"die": 20, "attr_multiplier": 2, "dc_default": 15},
              f"{tS2.rules['check']}")

        # ③ 段名寫錯 ⇒ 載入時就叫（不拖到跑團中間）
        mk_pack(tmpS, "n_sec", {"id": "n_sec", "provides": {"defaults": {"rulez": {"x": 1}}}})
        check("S3 defaults 的段名不在內核認得的清單裡 ⇒ 包不合法",
              _raises(lambda: packs.resolve(["d20core", "n_sec"], home=tmpS), packs.PackError))
        mk_pack(tmpS, "o_sec", {"id": "o_sec", "requires": ["d20core"],
                                "provides": {"defaults": {"rules": {"defend_bonus": 9}}}})
        check("S4 兩個包把同一格預設寫成不同值 ⇒ 當場叫（一個旋鈕一個家）",
              _raises(lambda: packs.resolve(["d20core", "o_sec"], home=tmpS), packs.PackError))

        # ④ 預設值進 hash（換預設＝換一張桌，重放才分得出）
        mk_pack(tmpS, "q_ok", {"id": "q_ok", "requires": ["d20core"],
                               "provides": {"defaults": {"rules": {"attr_roll": True}}}})
        _h1 = packs.resolve(["d20core"], home=tmpS).hash
        _h2 = packs.resolve(["d20core", "q_ok"], home=tmpS).hash
        check("S5 預設值算進 packs_hash（換預設 ⇒ 換 hash）", _h1 != _h2, f"{_h1} vs {_h2}")
        _tq = mk_table(tmpS, dict(cfgS, packs={"enabled": ["d20core", "q_ok"]}))
        check("S5b 包給的新鍵也真的生效（attr_roll 由 q_ok 補進來）",
              _tq.rules.get("attr_roll") is True)

        # ⑤ 節拍包的 pacing／turn 也走同一條（view.defaults 看得到）
        mk_pack(tmpS, "p_turn", {"id": "p_turn", "requires": ["d20core"],
                                 "provides": {"mode": {"name": "turn",
                                                       "turn": {"seconds": 42}}}})
        _vp = packs.resolve(["d20core", "p_turn"], home=tmpS)
        check("S6 節拍包的 pacing／turn 併進 defaults（同一條補缺口的路）",
              _vp.defaults.get("turn", {}).get("seconds") == 42, f"{_vp.defaults.get('turn')}")
    finally:
        shutil.rmtree(tmpS, ignore_errors=True)

    # ⑦ 「換整套規則」＝兩個互斥的規則包（設計者 2026-10-07 拍：走「獨立包 ＋ conflicts」）
    tmpR, cfgR = mk_home(packs_spec=["rules.alt"], data_from=None)
    try:
        mk_pack(tmpR, "rules.alt", {
            "id": "rules.alt", "name": "替代規則包（示範）",
            "conflicts": ["d20core"],
            "provides": {"defaults": {"rules": {
                "name": "alt-d20",
                "attributes": ["str", "dex", "con", "int", "wis", "cha"],
                "attr_default": 1, "attr_range": [1, 6],
                "hp": {"base": 6, "per_str": 0},
                "check": {"die": 20, "attr_multiplier": 1, "dc_default": 10},
                "damage": {"die": 8, "attr": "str", "attr_multiplier": 1},
                "defend_bonus": 2}}}})
        check("S8 規則包宣告 conflicts ⇒ 與 d20core 不能同時啟用",
              _raises(lambda: packs.resolve(["d20core", "rules.alt"], home=tmpR), packs.PackError))
        _cfgR2 = json.loads(json.dumps(cfgR))
        _cfgR2["packs"] = {"enabled": ["rules.alt"]}
        _cfgR2["rules"] = {"actions": ["attack", "check", "defend", "use", "other"]}
        _tR = mk_table(tmpR, _cfgR2)
        check("S9 只啟用替代規則包 ⇒ **整套規則真的換掉**（六屬性／HP 公式／DC）",
              _tR.rules["attributes"] == ["str", "dex", "con", "int", "wis", "cha"]
              and _tR.rules["hp"] == {"base": 6, "per_str": 0}
              and _tR.rules["check"]["dc_default"] == 10
              and _tR.rules["name"] == "alt-d20",
              f"{_tR.rules.get('name')} {_tR.rules.get('attributes')}")
        # 沒宣告 conflicts 的兩個規則包 ⇒ 載入時叫，且訊息指向正解
        mk_pack(tmpR, "rules.alt2", {"id": "rules.alt2",
                                     "provides": {"defaults": {"rules": {"defend_bonus": 9}}}})
        _e = ""
        try:
            packs.resolve(["rules.alt", "rules.alt2"], home=tmpR)
        except packs.PackError as exc:
            _e = str(exc)
        check("S10 兩個規則包撞同一格又沒宣告 conflicts ⇒ 當場叫，訊息指向 conflicts",
              "conflicts" in _e and "整套規則" in _e, _e[:110])
    finally:
        shutil.rmtree(tmpR, ignore_errors=True)

    # ⑥ 沒有 rules 又沒啟用提供 rules 的包 ⇒ 當場叫（不是跑團中間 KeyError）
    tmpS2, cfgS2b = mk_home(packs_spec=None, data_from=None)
    try:
        cfgS2b = json.loads(json.dumps(cfgS2b))
        cfgS2b.pop("rules", None)
        _e2 = ""
        try:
            mk_table(tmpS2, cfgS2b)
        except Exception as exc:                                           # noqa: BLE001
            _e2 = f"{type(exc).__name__}: {exc}"
        check("S7 沒 rules、也沒啟用提供 rules 預設的包 ⇒ 載入時當場叫",
              "RuleError" in _e2 and "rules" in _e2, _e2[:90])
    finally:
        shutil.rmtree(tmpS2, ignore_errors=True)

    print("== T. 作品層包（`<劇本>/packs/<id>/`）——真用例 ==")
    _reg = packs.registry(home=DEF)          # 作品層包住在定義層
    check("T1 現役的作品層包被註冊表讀到（不只是測試 fixture）",
          "hyakki_world" in _reg
          and _reg["hyakki_world"].dir.startswith(os.path.join(DEF, "packs")),
          f"{_reg['hyakki_world'].dir}")
    # ⚠ 作品層包現在**require rules.core**（★2 起它的 `tables/shop.json` 賣的是規則書包的道具 id）
    _vw = packs.resolve(["d20core", "board", "semic", "mode.turn", "wound", "rules.core",
                         "hyakki_world"], home=DEF)
    check("T2 作品層包的宣告進得了視圖（defaults 的 tower／memory ＋表的路徑）",
          _vw.ids[-1] == "hyakki_world"
          and {"memory", "tower"} <= set(_vw.defaults)
          and any(p.endswith("hyakki_world/tables/artifacts.json") for p in _vw.tables),
          f"defaults={sorted(_vw.defaults)}")

    tmpT, cfgT = mk_home(packs_spec=["d20core", "board", "semic", "mode.turn", "wound",
                                     "rules.core", "hyakki_world"], data_from=None)
    try:
        shutil.copytree(os.path.join(DEF, "packs"), os.path.join(tmpT, "packs"), dirs_exist_ok=True)
        _bt = json.load(open(os.path.join(DEF, "board.json"), encoding="utf-8"))
        _bt.pop("artifact_table", None)                       # 劇本檔那份已搬走
        with open(os.path.join(tmpT, "board.json"), "w", encoding="utf-8") as fh:
            json.dump(_bt, fh, ensure_ascii=False)
        tT = mk_table(tmpT, cfgT)
        check("T3 作品層包的 defaults 生效（tower／memory 由包補，劇本檔沒寫）",
              tT.tower_cfg.get("memory_per_turn_inside") == 1
              and tT.mem_cfg == {"start": 60, "low_threshold": 30},
              f"tower={len(tT.tower_cfg)}鍵 memory={tT.mem_cfg}")
        check("T4 包的表生效：神器表來自 `tables/artifacts.json`（劇本檔已沒有）",
              len(tT.artifacts_table) == 4
              and tT.artifacts_table_path.endswith("hyakki_world/tables/artifacts.json"),
              f"{tT.artifacts_table_path}")
        _cfgT2 = json.loads(json.dumps(cfgT))
        _cfgT2["tower"] = {"memory_per_turn_inside": 3}
        tT2 = mk_table(tmpT, _cfgT2)
        check("T5 劇本檔寫了就贏（config.tower 覆蓋包預設；權威鏈 config ＞ 包）",
              tT2.tower_cfg["memory_per_turn_inside"] == 3
              and tT2.tower_cfg["rest_heal"] == tT.tower_cfg["rest_heal"],
              f"{tT2.tower_cfg.get('memory_per_turn_inside')}")
        with open(os.path.join(tmpT, "board.json"), "w", encoding="utf-8") as fh:
            json.dump(dict(_bt, artifact_table=[{"tier": 9, "items": [{"id": "a_x"}]}]), fh,
                      ensure_ascii=False)
        check("T6 `board.json` 有寫一份 ⇒ 劇本檔贏（包的表不生效）",
              len(mk_table(tmpT, cfgT).artifacts_table) == 1)
    finally:
        shutil.rmtree(tmpT, ignore_errors=True)

    # 同名覆蓋：作品層的 `board` 蓋掉引擎層的 `board`（權威鏈：config ＞ 作品層 ＞ 引擎層）
    tmpT2, cfgT2b = mk_home(packs_spec=["d20core", "board"], data_from=None)
    try:
        _eng = json.load(open(os.path.join(HERE, "packs/board/pack.json"), encoding="utf-8"))
        _eng["provides"]["defaults"]["board"]["entrance_search_dc"] = 14
        mk_pack(tmpT2, "board", _eng)                          # 作品層同名包（複製引擎層再改一格）
        tA = mk_table(tmpT2, cfgT2b)
        check("T7 同名包覆蓋：作品層的 `board` 蓋掉引擎層（entrance_search_dc 14 生效）",
              _eng["id"] == "board" and tA.board_cfg["entrance_search_dc"] == 14,
              f"{tA.board_cfg.get('entrance_search_dc')}")
        _cfgC = json.loads(json.dumps(cfgT2b))
        _cfgC["board"] = {"entrance_search_dc": 9}
        check("T8 權威鏈最後一段：config 又寫了 ⇒ config 贏（9）",
              mk_table(tmpT2, _cfgC).board_cfg["entrance_search_dc"] == 9)
        # 表 schema 壞 ⇒ 載入時就叫（不拖到跑團中間）
        _bad = {"kind": "artifacts", "rows": [{"tier": 0}]}     # 缺 items
        os.makedirs(os.path.join(tmpT2, "packs", "u_tbl", "tables"), exist_ok=True)
        with open(os.path.join(tmpT2, "packs", "u_tbl", "tables", "artifacts.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(_bad, fh)
        mk_pack(tmpT2, "u_tbl", {"id": "u_tbl", "requires": ["d20core"],
                                 "provides": {"tables": ["tables/artifacts.json"]}})
        _cfgu = json.loads(json.dumps(cfgT2b))
        _cfgu["packs"] = {"enabled": ["d20core", "u_tbl"]}
        _bt2 = json.load(open(os.path.join(tmpT2, "board.json"), encoding="utf-8"))
        _bt2.pop("artifact_table", None)
        with open(os.path.join(tmpT2, "board.json"), "w", encoding="utf-8") as fh:
            json.dump(_bt2, fh, ensure_ascii=False)
        _e3 = ""
        try:
            mk_table(tmpT2, _cfgu)
        except Exception as exc:                                          # noqa: BLE001
            _e3 = f"{type(exc).__name__}: {exc}"
        check("T9 包的表 schema 壞（缺 items）⇒ 載入時當場叫", "items" in _e3, _e3[:90])
    finally:
        shutil.rmtree(tmpT2, ignore_errors=True)

    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
