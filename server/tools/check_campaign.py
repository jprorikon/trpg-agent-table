#!/usr/bin/env python3
"""**劇本自檢器**——作者（不是工程師）改完劇本先跑這一支，跟引擎同一套判據。

    python3 tools/check_campaign.py campaigns/<劇本 id>

檢查：目錄結構 → 每個 JSON 的 schema → **交叉指涉**（願望點名的人存在嗎、碎片支持的結論
存在嗎、AST 點到的實體存在嗎）→ `module.json` 用**引擎自己的驗證器**（`scenario.Scenario`）
→ **機制包**（`config.packs` 的啟用清單）用引擎的 `packs.resolve`（同一套判據）。

判據分兩級：
  * **錯誤（error）**：起桌或跑團時一定會壞（檔缺、id 對不上、AST 不通、句庫沒有那個鍵）。
  * **提醒（warn）**：能跑但八成不是你要的（沒有 WORLD.md、NPC 沒有專屬知識、沒寫 module.json）。

同一份檢查也跑在 `test_layers.py` E 組裡（真劇本必須零錯誤）——所以這一支不是「另一套規則」，
是同一套規則給作者用的入口。
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


REQUIRED = ("config.json", "seats.json", "goals.json", "clues.json")
OPTIONAL = ("board.json", "scenario.json", "module.json")   # 規則資料新名 scenario.json（舊名相容）
ADVISED = ("WORLD.md", "GM.md", "TABLE.md")


def _load(path, errs, warns):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:                                     # noqa: BLE001
        errs.append(f"{os.path.basename(path)}: JSON 讀不動（{type(exc).__name__}: {exc}）")
        return None


def check(d: str) -> "tuple[list[str], list[str]]":
    errs: list = []
    warns: list = []
    cfg_path = os.path.join(d, "config.json")
    if not os.path.exists(cfg_path):
        if os.path.exists(os.path.join(d, "config.example.json")):
            errs.append("沒有 config.json（這份是範本：複製成 config.json 再把 token 填進去）")
        else:
            errs.append("沒有 config.json")
    for f in REQUIRED:
        if not os.path.exists(os.path.join(d, f)):
            errs.append(f"缺檔：{f}")
    for f in ADVISED:
        if not os.path.exists(os.path.join(d, f)):
            warns.append(f"建議補上 {f}（世界觀／GM 守則／桌面說明——給人與 agent 看的）")
    if not any(os.path.exists(os.path.join(d, f)) for f in OPTIONAL):
        warns.append("沒有 board.json（沒有棋盤＝抽象桌）也沒有劇本規則資料"
                     "（scenario.json／舊名 module.json——判據全走 legacy 分支）")
    if errs:
        return errs, warns

    cfg = _load(cfg_path, errs, warns) or {}
    seats = _load(os.path.join(d, "seats.json"), errs, warns) or {}
    goals = _load(os.path.join(d, "goals.json"), errs, warns) or {}
    clues = _load(os.path.join(d, "clues.json"), errs, warns) or {}
    # board.json 是**選用**檔（沒有＝抽象桌）⇒ 缺席不是錯誤；只有存在時才讀。
    # 2026-10-06 踩到：這裡無條件 _load ⇒ 抽象桌劇本被誤報「board.json: JSON 讀不動」，
    # 作者會以為一定要做棋盤（骨架交付前的自檢就是這樣被擋下來的）。
    board = {}
    if os.path.exists(os.path.join(d, "board.json")):
        board = _load(os.path.join(d, "board.json"), errs, warns) or {}

    # ---- packs（機制包）：**與引擎同一套判據**（作者入口不養第二份規則）----
    # ⚠ 先解析再驗 config：**引擎是「先解析包、把預設補進 config 的缺口」才算數**
    #   （`Table.__init__` 的 `packs.apply_defaults`）。這裡照做，驗的才是同一份
    #   ——否則「只寫覆寫」的劇本會被誤報「config.rules 缺 hp」（2026-10-07 批次 2）。
    from trpg.rules import packs as _packs                                               # noqa: E402
    view = None
    try:
        if cfg.get("packs") or os.path.isdir(os.path.join(d, "packs")):
            view = _packs.resolve(cfg.get("packs"), home=d)
        else:
            warns.append("沒有 packs：這一張桌走 legacy 路徑（各層照舊讀 config；"
                         "見 packs/README.md）")
        _reg = _packs.registry(home=d)
        _pdir = os.path.abspath(os.path.join(d, "packs"))
        for pid, p in sorted(_reg.items()):
            if not (p.dir and os.path.abspath(p.dir).startswith(_pdir)):
                continue                       # 引擎層的包由引擎層自己負責
            errs += [f"packs/{pid}/pack.json: {e}" for e in p.validate()]
            # **沒啟用也要**核對「宣告的東西內核真的有」——作者寫自己的包時當場就知道
            try:
                _packs.verify_impl(_packs.PackView([p]))
            except _packs.PackError as exc:
                errs.append(f"packs/{pid}: {exc}")
        if view is not None and view.enabled:
            _open = {str(a.get("id") if isinstance(a, dict) else a)
                     for a in ((cfg.get("rules") or {}).get("actions") or [])}
            _absent = [a["id"] for a in view.actions if a["id"] not in _open]
            if _absent:
                warns.append(f"啟用的包宣告了動作 {_absent}，但 config.rules.actions 沒有它們"
                             "（玩家／agent 的工具列不會出現這些動作）")
            if not view.mode:
                warns.append("沒有啟用任何節拍包（mode.turn／mode.scene）：節拍照 config 走")
            else:
                _cfg_mode = str(((cfg.get("pacing") or {}).get("mode") or "")).lower()
                _pk_mode = str((view.mode or {}).get("name") or "").lower()
                if _cfg_mode and _pk_mode and _cfg_mode != _pk_mode:
                    warns.append(f"節拍不一致：config.pacing.mode={_cfg_mode!r} 但啟用了 "
                                 f"{_pk_mode!r} 包——**以 config 為準**（包只在 config 沒寫時"
                                 "當預設）；要換節拍請改 config（順手把包換成對應的那個）")
                _over = [k for k in (view.mode.get("pacing") or {}) if k in (cfg.get("pacing") or {})]
                _over += [k for k in (view.mode.get("turn") or {}) if k in (cfg.get("turn") or {})]
                if _over:
                    warns.append(f"節拍包也想設 {_over}，但 config 已經寫了 ⇒ 以 config 為準"
                                 "（包的值不會生效）")
    except _packs.PackError as exc:
        errs.append(f"機制包: {exc}")
    except Exception as exc:                                             # noqa: BLE001
        errs.append(f"機制包解析失敗：{type(exc).__name__}: {exc}")

    if view is not None:
        cfg = _packs.apply_defaults(cfg, view)      # 機制包補缺口（config 寫的一律贏）

    # ---- config.json（驗的是「補過預設之後」實際生效的那一份）----
    for k in ("table_id", "scenario", "seed", "rules", "turn"):
        if k not in cfg:
            errs.append(f"config.json 缺 {k}（寫在 config，或由啟用的機制包提供預設）")
    rules = cfg.get("rules") or {}
    for k in ("name", "attributes", "attr_range", "actions", "check", "hp"):
        if k not in rules:
            errs.append(f"config.rules 缺 {k}（config 沒寫、啟用的包也沒給預設）")
    # ★ 2026-10-20（設計者：「模組編輯器理論上就不該管這些事情」）：`auth`（帳號層＋GM 憑證）
    #   屬於部署端，模組／編輯器的 config.example **刻意不含 auth** ⇒ 沒有 gm_token 不再是警告；
    #   只有「寫了 auth 但 gm_token 是空字串／佔位符」才提醒（那是有人填錯）。
    _auth = cfg.get("auth") or {}
    if _auth and (not _auth.get("gm_token") or "REPLACE_ME" in str(_auth.get("gm_token"))):
        warns.append("config.auth.gm_token 是空的／佔位符（模組本來可以完全不寫 auth——建桌時由 hub 供給）")
    turn = cfg.get("turn") or {}
    if not turn.get("seconds"):
        errs.append("config.turn.seconds 沒設（回合沒有死線）")
    for a in rules.get("actions") or []:
        if isinstance(a, dict) and not a.get("id"):
            errs.append("config.rules.actions 有一個條目沒有 id")

    # 「config 寫了跟包預設一樣的值」⇒ 那是重複宣告（可以拿掉，讓包當唯一權威）
    if view is not None and view.defaults:
        _raw = _load(cfg_path, [], []) or {}
        _dup = []
        for _sec, _blk in view.defaults.items():
            _got = _raw.get(_sec)
            if not isinstance(_got, dict):
                continue
            for _k in _blk:
                if _k in _got and _got[_k] == _blk[_k]:
                    _dup.append(f"{_sec}.{_k}")
        if _dup:
            warns.append(f"config.json 有 {_dup} 與機制包預設相同 ⇒ 可以拿掉"
                         "（包是機制常數的家；config 只留這一桌要改的）")

    # cfg['rules']['actions'] 列的動作，必須有啟用的包宣告它（否則玩家／agent 叫不出來）
    if view is not None and view.enabled:
        _declared = {a["id"] for a in view.actions}
        _ghost = [str(a.get("id") if isinstance(a, dict) else a)
                  for a in (rules.get("actions") or [])
                  if str(a.get("id") if isinstance(a, dict) else a) not in _declared]
        if _ghost:
            warns.append(f"config.rules.actions 有 {_ghost} 沒有任何啟用的包宣告它"
                         "（動作目錄的完整性權威在包；config 只該列要顯示／排序的）")

    # ---- board.json ----
    nodes = {n["id"]: n for n in (board.get("nodes") or []) if isinstance(n, dict) and n.get("id")}
    if board:
        if len(nodes) != len(board.get("nodes") or []):
            errs.append("board.json 的 nodes 有缺 id 或 id 重複")
        for nid, n in nodes.items():
            if not n.get("jp"):
                warns.append(f"節點 {nid} 沒有 jp 名（前端會顯示 id）")
            for e in (n.get("edges") or []):
                if e.get("to") not in nodes:
                    errs.append(f"節點 {nid} 的邊指向不存在的節點：{e.get('to')}")
        if board.get("start_node") and board["start_node"] not in nodes:
            errs.append(f"board.start_node 不存在：{board['start_node']}")

    # ---- seats.json ----
    _roster = board.get("roster") or {}
    roster_chars = ({r.get("seat") or r.get("id") or r.get("character") or r.get("name")
                     for r in _roster} if isinstance(_roster, list) else set(_roster))
    seat_chars = {}
    for s in (seats.get("seats") or []):
        sid, kind = s.get("seat"), s.get("kind")
        if not sid:
            errs.append("seats.json 有一席沒有 seat id")
            continue
        if kind not in ("gm", "npc", "human", "agent"):
            errs.append(f"席位 {sid} 的 kind 不合（{kind!r}；要是 gm/npc/human/agent）")
        if s.get("character"):
            seat_chars[sid] = s["character"]
            if roster_chars and s["character"] not in roster_chars:
                warns.append(f"席位 {sid} 的角色 {s['character']} 不在 board.roster 裡（屬性模板會用預設）")
    if not [s for s in (seats.get("seats") or []) if s.get("kind") in ("human", "agent")]:
        errs.append("沒有任何玩家席位（kind=human/agent）")

    # ---- goals.json ----
    goals_list = goals.get("goals") or []
    goal_ids = []
    for g in goals_list:
        gid = g.get("id")
        if not gid:
            errs.append("goals.json 有一條願望沒有 id")
            continue
        if gid in goal_ids:
            errs.append(f"願望 id 重複：{gid}")
        goal_ids.append(gid)
        if not g.get("text"):
            warns.append(f"願望 {gid} 沒有 text（玩家看不到內容）")
        who = g.get("owner_character")
        if who and seat_chars and who not in seat_chars.values():
            errs.append(f"願望 {gid} 的 owner_character={who} 不在任何席位上（沒有人能拿到它）")
        if not who:
            warns.append(f"願望 {gid} 沒有 owner_character（不會綁到任何席位）")

    # ---- clues.json ----
    claims = clues.get("claims") or []
    claim_ids = {c.get("id") if isinstance(c, dict) else c for c in claims}
    seen_fids = set()
    for f in (clues.get("fragments") or []):
        fid = f.get("id")
        if not fid:
            errs.append("clues.fragments 有一枚碎片沒有 id")
            continue
        if fid in seen_fids:
            errs.append(f"碎片 id 重複：{fid}")
        seen_fids.add(fid)
        if not f.get("text"):
            errs.append(f"碎片 {fid} 沒有 text（玩家拿到會看到空白）")
        for c in (f.get("supports") or []):
            if claim_ids and c not in claim_ids:
                errs.append(f"碎片 {fid} 支持的結論 {c} 不在 clues.claims 裡")

    # ---- 劇本規則資料（`scenario.json`；**舊名 `module.json` 相容**）——用引擎自己的驗證器 ----
    from trpg.rules import scenario as scenario                                                      # noqa: E402
    mod_path, used_old = scenario.resolve_module_path(d)
    label = scenario.module_filename(d) or "scenario.json"
    if mod_path:
        if used_old:
            warns.append("劇本規則資料用的是舊名 module.json"
                         "（權威名字是 scenario.json——它住在「劇本」層，建議改名）")
        try:
            # 可用的 effect＝啟用包宣告的那些（引擎起桌時也是這樣傳；兩邊判據要一致）
            mod = scenario.Scenario.load(d, allowed_effects=(view.effects
                                                             if view and view.enabled else None))
        except scenario.ModuleError as exc:
            errs.append(str(exc))
            mod = None
        except Exception as exc:                                         # noqa: BLE001
            errs.append(f"module.json 載入失敗：{type(exc).__name__}: {exc}")
            mod = None
        if mod is not None:
            errs += [f"{label}: {e}" for e in mod.validate()]
            from trpg.engine import director as _director

            errs += [f"{label}: {e}" for e in _director.Director(mod).validate()]
            for gid in (mod.data.get("requirements") or {}):
                if gid not in goal_ids:
                    errs.append(f"module.requirements 有一條 {gid} 對不到 goals.json 的任何願望"
                                "（打錯字就會靜默不生效）")
            missing = [g for g in goal_ids if g not in (mod.data.get("requirements") or {})]
            if missing:
                warns.append(f"這些願望沒有 AST 判據（會退回 legacy required）：{missing}")
            known = set(seat_chars.values())
            for key, _ent in (mod.data.get("entities") or {}).items():
                # `gm_only: true` ＝**只給 GM 演的臨場 NPC**（例：各城的店員）——不配席位、不進喚醒名單，
                # 由 GM 讀它的 `knowledge` 直接演。沒有這個旗標又對不到席位角色 ⇒ 才提醒（那是打錯字）。
                if (_ent or {}).get("gm_only"):
                    continue
                if known and key not in known:
                    warns.append(f"module.entities.{key} 對不到任何席位角色（沒人會用到它）")
            if not (mod.data.get("triggers") or []):
                warns.append("module.triggers 是空的（這一劇本沒有任何資料驅動的觸發）")
            _dblk = mod.data.get("director") or {}
            if not _dblk:
                warns.append("沒有 director 區塊：L3 導演不會介入（卡住／超前時沒有糾正力量）")
            elif not _dblk.get("policy"):
                warns.append("director 區塊沒有 policy：導演不會有任何提案")
    else:
        warns.append("沒有劇本規則資料（scenario.json／舊名 module.json）："
                     "願望判據／表層判據走 legacy 分支（見 docs/SCENARIO_FORMAT.md）")

    # ---- 機制表：**真的用引擎的 loader 讀一遍**（2026-10-07 補；起桌＝同一套判據）----
    # 為什麼要有這一關：這支從前只做靜態檢查、不碰各機制的表 ⇒ 表寫壞（schema／**同一節點同 id 兩列**／
    # 賣的道具不在道具表…）**只有起桌那一刻才會叫**，作者的自檢卻印「✅ 可以直接起桌」。
    # 這裡把每個 loader 對「這一桌啟用包帶來的表」跑一遍（載入時就叫的那些守衛也一起跑）。
    if view is not None and view.enabled:
        import importlib                                                 # noqa: PLC0415
        # 句庫：**這一桌的**那一本（內核 ← 包的 `text.json` ← 模組／劇本的 `text.json`），
        # 條件表才驗得到自己的文案（引擎在 `Table.__init__` 也是先疊句庫才載表——兩邊判據要一致）。
        try:
            _bank = _packs.text_bank_for(view, d)
        except Exception as exc:                                         # noqa: BLE001
            errs.append(f"句庫（text.json）讀不了：{type(exc).__name__}: {exc}")
            _bank = None
        # ★ 2026-10-20：這裡以前 import 的是**裸模組名**（"conditions"／"skills"…）⇒ 一定
        #   ModuleNotFoundError，模板自檢每次都吐 8 個假 ERROR（作者被嚇、真問題被淹）。
        #   改成完整路徑 `trpg.rules.<x>`；下面按尾名判斷（`conditions` / `shop`）。
        for label, modname in (("conditions", "trpg.rules.conditions"), ("skills", "trpg.rules.skills"),
                               ("levels", "trpg.rules.advancement"), ("items", "trpg.rules.items"),
                               ("stars", "trpg.rules.stars"), ("rescue", "trpg.rules.rescue"),
                               ("shop", "trpg.rules.shop"), ("monsters", "trpg.rules.monsters"),
                               ("lands", "trpg.rules.lands")):
            try:
                _mod = importlib.import_module(modname)
                if modname.endswith("conditions"):
                    _tbl = _mod.load(view.tables, _bank)
                elif modname.endswith("shop"):
                    # 幣名跟槽位走（與 table.shop_init 同口徑）：coin=gold 的世界，currency 可以寫 gold
                    _coin = "star_coins"
                    try:
                        from trpg.rules.board import slots as _slots          # noqa: PLC0415
                        _coin = _slots.key_of(_slots.load_for(d), "coin")
                    except Exception:                                        # noqa: BLE001
                        pass
                    _tbl = _mod.load(view.tables, (_coin,) + tuple(getattr(_mod, "CURRENCIES", ())))
                else:
                    _tbl = _mod.load(view.tables)
            except Exception as exc:                                     # noqa: BLE001
                errs.append(f"機制表（{label}）：{type(exc).__name__}: {exc}")
                continue
            if modname.endswith("shop") and _tbl:      # 商店賣的是道具 ⇒ 一定要有道具表，且貨品要在表裡
                try:
                    _items = importlib.import_module("trpg.rules.items").load(view.tables) or {}
                except Exception:                                        # noqa: BLE001
                    _items = {}
                if not _items:
                    errs.append("機制表（shop）：有商店表但沒有道具表——把帶道具表的包"
                                "（例：rules.core）一起列進 config.packs.enabled")
                else:
                    _miss = sorted({str(r["id"]) for r in _tbl if not r.get("star")} - set(_items))
                    if _miss:
                        errs.append(f"機制表（shop）：賣的道具不存在 {_miss}"
                                    f"（道具表有 {sorted(_items)}）")
        # 神器表（`kind=artifacts`）：引擎的 `board_load_tables` 只認形狀 ⇒ 這裡照同一條判
        for _p in (view.tables or []):
            try:
                with open(_p, "r", encoding="utf-8") as _fh:
                    _doc = json.load(_fh)
            except Exception:                                            # noqa: BLE001
                continue
            if not isinstance(_doc, dict) or _doc.get("kind") != "artifacts":
                continue
            _rows = _doc.get("rows")
            if not isinstance(_rows, list) or not _rows:
                errs.append(f"機制表（artifacts）{os.path.basename(_p)}: rows 要是非空陣列")
                continue
            if [r.get("tier") for r in _rows] != list(range(len(_rows))):
                warns.append(f"機制表（artifacts）{os.path.basename(_p)}: tier 不等於列索引"
                             "（引擎用 `artifacts_table[tier]` 取列 ⇒ 請從 0 連續編號）")

    return errs, warns


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    d = sys.argv[1]
    if not os.path.isdir(d):
        print(f"不是目錄：{d}")
        return 2
    errs, warns = check(d)
    print(f"== 劇本自檢：{d} ==")
    for w in warns:
        print("  WARN  " + w)
    for e in errs:
        print("  ERROR " + e)
    print(f"\n{'✅ 可以直接起桌' if not errs else '❌ 先修上面的 ERROR'}（error {len(errs)} / warn {len(warns)}）")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
