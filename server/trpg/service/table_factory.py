#!/usr/bin/env python3
"""**桌的組裝**（L6 工廠）——「讀 config ⇒ 一個可服務的 TableService」**只做一份**。

2026-10-07 單一進程化：以前這段（工具面按 config 調整 ＋ gm／npc 席位自動入座 ＋ 背景節拍）
寫在 `server.py` 裡；`hub_server.py` 一個進程要裝 N 張桌，所以抽出來共用——**同一件事不做兩份**。

呼叫者：
  * `server.py`    單桌模式（一張桌一個 process；測試／開發）
  * `hub_server.py` 單一進程（一個 process 裝所有桌；production）

⚠ 一進程多桌的兩個必踩點（都在這裡處理掉）：
  1. **工具目錄**：`mcp_api.TOOLS` 是模組級共用清單 ⇒ 每桌拿**自己那份副本**（`svc.tools`）。
  2. **背景節拍**：每張桌都有自己的 `tick_loop` 執行緒（節拍／超時／踢席位）⇒ 一桌一條。
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from typing import Any, Dict, List, Optional, Tuple

from trpg.api import mcp_api as mcp_api

from trpg.core import layout as layout

from trpg.service.service import TableService


def load_cfg(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def apply_difficulty(cfg: Dict[str, Any], def_dir: str, log: Any = print) -> Dict[str, Any]:
    """把劇本 `scenario.json` 的 **`starting_difficulty`** 套進這張桌的 config。

    **開局難度 vs 運行時檔位——兩件事，別混**：
    * `difficulty_presets`（L1／`scenario.json` 原有）＝ L3 導演的**運行時糾偏**，只動 `dc_mod`／`act_mod`，
      由 `difficulty_set` 事件切換（卡關自動收緊）。它不知道死亡罰則、也不該知道。
    * `starting_difficulty`（本函式）＝**這一局的基線**，建桌時選（`config.difficulty`），一局不變：
      ① 補 `config.rules` **沒寫**的旋鈕（`death`／`levels`…）—— **config 寫了一律贏**（既有規矩）；
      ② 設 `config.difficulty_mods`（`monster_dc`／`exp_mult`／`coins_mult`／`price_mult`），由
         `rules/monsters.py`／`rules/shop.py` 讀。

    沒有 `starting_difficulty` 段／`config.difficulty` 留空 ⇒ 用段裡的 `default`；兩者都沒有 ⇒ **原樣回傳
    （legacy 一字不變）**。指定了不存在的檔名 ⇒ 退回 default 並明講（不靜默）。
    """
    out = dict(cfg or {})
    scen_p = os.path.join(str(def_dir or ""), "scenario.json")
    if not os.path.exists(scen_p):
        scen_p = os.path.join(str(def_dir or ""), "module.json")   # 舊名（與 scenario.resolve_module_path 同口徑）
    try:
        with open(scen_p, "r", encoding="utf-8") as fh:
            scen = json.load(fh)
    except Exception:                                                     # noqa: BLE001
        return out
    spec = scen.get("starting_difficulty")
    if not isinstance(spec, dict):
        return out
    levels = {str(k): v for k, v in (spec.get("levels") or {}).items() if isinstance(v, dict)}
    if not levels:
        return out
    want = str(out.get("difficulty") or "").strip()
    default = str(spec.get("default") or (("normal" if "normal" in levels else next(iter(levels)))))
    if not want:
        want = default
    if want not in levels:
        log(f"[difficulty] 這張桌指定了不存在的難度 {want!r}（本劇本有：{'/'.join(levels) or '無'}）"
            f" ⇒ 退回 {default!r}")
        want = default if default in levels else next(iter(levels))
    lv = levels[want]
    # ① rules：**只補 config 沒寫的**（config 一律贏）
    rules = dict(out.get("rules") or {})
    added: List[str] = []
    for k, v in (lv.get("rules") or {}).items():
        if k not in rules or rules.get(k) in (None, {}, []):
            rules[k] = json.loads(json.dumps(v))          # 深拷貝：不讓呼叫端改到劇本檔
            added.append(k)
    if rules:
        out["rules"] = rules
    # ② 倍率：一律寫（它就是這一檔的定義）
    mods = lv.get("mods")
    if isinstance(mods, dict) and mods:
        out["difficulty_mods"] = dict(mods)
    out["difficulty"] = want
    out["difficulty_doc"] = str(lv.get("doc") or "")
    if added:
        log(f"[difficulty] {want}：補進 rules {added}")
    return out


def autojoin_specs(svc: TableService, log=print) -> List[str]:
    """`seats.json` 里声明的 GM / NPC 席位自动入座（它们不是玩家，但要在桌上「在場」）。"""
    joined: List[str] = []
    for sid, spec in (getattr(svc.table, "seats_spec", {}) or {}).items():
        if spec.get("kind") in ("gm", "npc") and sid not in svc.table.state["seats"]:
            try:
                svc.table.join(seat=sid, name=spec.get("name") or spec.get("character") or sid,
                               kind=spec["kind"], node=spec.get("start_node"))
                joined.append(sid)
                log(f"[table] {spec['kind']} 席位入座：{sid}"
                    f"（{spec.get('name') or spec.get('character')}）")
            except Exception as exc:                                       # noqa: BLE001
                log(f"[table] {sid} 入座失败：{exc}")
    return joined


def build(home: str, cfg: Dict[str, Any], log: Any = print, *,
          session: Optional[str] = None, run_id: Optional[str] = None,
          def_dir: Optional[str] = None) -> TableService:
    """組一張可服務的桌：服務本體 ＋ **這一桌自己的**工具目錄 ＋ gm／npc 自動入座。

    `session`／`run_id`：**指定要接哪一局**（換局用，見 `new_run`）；不給＝接現役那一局。

    ⚠ 進來先套 **開局難度**（`apply_difficulty`）：它會補 `rules.death`／`rules.levels` 與
    `difficulty_mods`——**必須在 `TableService` 之前**（`rules` 影響初始化與等級表載入）。
    這裡傳進來的 cfg 是呼叫端的 dict，套難度用副本，不動呼叫端手上那份。

    ⚠ 2026-10-20：模板／編輯器已不再輸出 `auth`，但引擎多處（`/api/state`、`/mcp`、GM 聲道）
    直接讀 `cfg["auth"]["gm_token"]`。沒 auth 的 config（作者手動複製 config.example 直接跑
    `server.py`）會在**每一個讀請求**上 KeyError ⇒ 這裡補一個空 `auth` 讓那些讀取變成
    「沒有 GM 控制鍵」而不是 500；真正的憑證仍由 hub／部署端供給。
    """
    _dd = def_dir or layout.def_dir_of(home)
    cfg = dict(cfg or {})
    cfg.setdefault("auth", {})
    cfg = apply_difficulty(cfg, _dd, log=log)
    svc = TableService(home, cfg, session=session, run_id=run_id, def_dir=_dd)
    try:
        # ⚠ 吃的是**定義目錄**（`packs/` 在劇本層；桌目錄只有 config／runs）——
        #   傳桌目錄的話 `packs.resolve` 會找不到包、這裡的 except 會把它吞掉 ⇒ 工具目錄永遠是空的
        #   （2026-10-07 三層搬家後真的發生過一次，靠 live 冒烟才逮到）。
        svc.tools = mcp_api.tools_for(cfg, svc.def_dir)
    except Exception as exc:                                               # noqa: BLE001
        log(f"[table] 工具目錄組裝失敗（沿用共用底稿）：{exc}")
    autojoin_specs(svc, log=log)
    return svc


def new_run(home: str, cfg: Dict[str, Any], log: Any = print, *,
            def_dir: Optional[str] = None) -> TableService:
    """**低階**：在同一個 home 裡開一個新的 run 目錄（新 session）——測試／舊佈局用。

    ⚠ 2026-10-20「桌和 run 等价」之後，**前台的新 play 實例一律走 `Hub.create_table`**
    （一個 run 一個 home，`table_id`＝`run_id`）；`Hub.new_run` 也是再建一個 home，
    不再呼叫這一支。這裡保留給「同一個 home 內多 run」的舊測試與歷史資料。
    """
    rid = str(uuid.uuid4().hex)
    svc = build(home, cfg, session=rid, run_id=rid, log=log, def_dir=def_dir)
    log(f"[table] 開新一局：session={rid}（舊局留在 runs/ 底下，可回放）")
    return svc


def start_tick(svc: TableService, stop: threading.Event) -> threading.Thread:
    """這一張桌的背景節拍（`KickMixin.tick_loop`；每桌一條）。"""
    t = threading.Thread(target=svc.tick_loop, args=(stop,), daemon=True)
    t.start()
    return t


def split_prefix(path: str, tables: Dict[str, Any]) -> Tuple[Optional[str], str, bool]:
    """把 `self.path` 拆成（桌 id, 剩下的路徑, 是不是觀戰）。

    `/hyakki_tower/api/state` → （`hyakki_tower`, `/api/state`, False）
    `/hyakki_tower/watch/api/log` → （`hyakki_tower`, `/api/log`, True）
    不認得的路徑 ⇒（None, 原樣, False）。
    """
    p = path.split("?", 1)[0]
    segs = [s for s in p.strip("/").split("/") if s]
    if not segs:
        return None, "/", False
    tid = segs[0]
    if tid not in tables:
        return None, p or "/", False
    rest = segs[1:]
    watch = bool(rest and rest[0] == "watch")
    if watch:
        rest = rest[1:]
    # ⚠ 空的 rest（`/<桌>/` 或 `/<桌>/watch/`）＝要那個頁面 ⇒ 補成 "/"
    tail = "/" + "/".join(rest) if rest else "/"
    qs = ("?" + path.split("?", 1)[1]) if "?" in path else ""
    return tid, tail + qs, watch


def repo_root_of(home: str) -> str:
    """`<repo>/{campaigns/<桌>|modules/<模組>/scenarios/<劇本>}` ⇒ `<repo>`（正本在 `layout.repo_root`）。"""
    return layout.repo_root(home)
