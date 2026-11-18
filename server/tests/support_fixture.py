#!/usr/bin/env python3
"""測試用「一局的事件流」來源。

2026-10-21 設計者把現役桌整批退役之後，`store.copy_current_run(CAMP, tmp)` 就抄不到東西
（CAMP 現在指向**劇本定義目錄**，那裡沒有 `runs/`）。這支提供一個穩定的來源：

1. 首選：`state/_retired/tables-retired-*.tar.gz` 裡現役那一局（真流、有席位、有歷史）。
2. 退路：現場用劇本定義開一張最小桌、坐滿席位、打幾手（不需要任何既有狀態）。

用法（測試裡）：`from support_fixture import seed_run; seed_run(CAMP, tmp)`
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import sys
import tarfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from trpg.core import store  # noqa: E402


def _from_archive(dst_home: str, scenario: str = "sannenme_no_onibarai") -> str:
    """從退役歸檔裡撈一局真的流出來（有的話）。"""
    for arc in sorted(glob.glob(os.path.join(HERE, "state", "_retired", "tables-retired-*.tar.gz")), reverse=True):
        try:
            with tarfile.open(arc) as tf:
                names = [n for n in tf.getnames()
                         if f"/{scenario}/" in n and n.endswith("events.jsonl")]
                if not names:
                    continue
                name = sorted(names, key=lambda n: tf.getmember(n).size, reverse=True)[0]
                sid = name.split("/runs/")[-1].split("/")[0] or "fixture"
                dst_dir = os.path.join(store.runs_dir(dst_home), sid)
                os.makedirs(dst_dir, exist_ok=True)
                src = tf.extractfile(name)
                if src is None:
                    continue
                with open(os.path.join(dst_dir, "events.jsonl"), "wb") as fh:
                    fh.write(src.read())
                store.set_current_run(dst_home, sid)
                return sid
        except Exception:                                              # noqa: BLE001
            continue
    return ""


def _seat_rows(def_dir: str) -> "list[dict]":
    """`seats.json` 裡「設計給玩家坐的席位」（human／agent）。

    ⚠ 2026-10-24（review R3）：這裡從前讀 `t.view(gm=True)["seat_plan"]`——但 `seat_plan`
    是 **L6（`TableService`）** 讀 `seats.json` 之後才掛上去的，裸引擎（`Table`）永遠是空的
    ⇒ 合成局一個人都不會入座（乾淨 clone 上 `seed_run` 靜默產出「0 席 1 筆事件」的假局，
    `test_auth`／`test_layers` 因此 KeyError 'p1'）。合成局要自己讀 seats.json。
    """
    try:
        with open(os.path.join(def_dir, "seats.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return []
    return [r for r in (doc.get("seats") or [])
            if isinstance(r, dict) and r.get("seat")
            and str(r.get("kind")) in ("human", "agent")]


def _synthetic(def_dir: str, dst_home: str, seats: int = 4, *, with_module: bool = True) -> str:
    """沒有歸檔就現場打一局最小桌（坐滿 → 開輪 → 各走一手）。

    `with_module=False` ⇒ **不把 `scenario.json` 抄進去**（`test_module` 的「舊劇本」路徑
    要一張沒有模組檔的桌；從前這裡無條件抄，B1 假紅）。
    """
    from trpg.core import layout  # noqa: PLC0415
    from trpg.engine import Table  # noqa: PLC0415
    from trpg.rules import packs as _packs  # noqa: PLC0415

    # 桌目錄沒有 config.json ⇒ 讀劇本層的 config.example.json（同一支 `read_table_config`）
    cfg = layout.read_table_config(def_dir)
    cfg.pop("hub_base", None)
    cfg.pop("public_prefix", None)
    cfg["port"] = 0
    cfg["table_id"] = "fixture"
    cfg["run_id"] = "fixture"
    cfg.setdefault("auth", {})
    cfg.setdefault("session", {})["require"] = False
    cfg.setdefault("turn", {})["auto_start_first_turn"] = True
    files = ["board.json", "goals.json", "clues.json", "seats.json"]
    if with_module:
        files.append("scenario.json")
    for f in files:
        p = os.path.join(def_dir, f)
        if os.path.exists(p):
            shutil.copy(p, os.path.join(dst_home, f))
    _psrc = os.path.join(def_dir, "packs")
    if os.path.isdir(_psrc) and not os.path.isdir(os.path.join(dst_home, "packs")):
        shutil.copytree(_psrc, os.path.join(dst_home, "packs"))
    # ★ 模組層的包（`rules.core` 住模組 repo，不在劇本層）——沒有它 `Table()` 直接 PackError
    _packs.seed_module_packs(def_dir, dst_home)

    t = Table(home=dst_home, cfg=cfg, store=store.FileStore(dst_home))
    plan = _seat_rows(def_dir)[:seats]
    for row in plan:
        try:
            t.join(seat=row["seat"], name=row.get("name") or row["seat"],
                   kind=str(row.get("kind") or "human"))
        except Exception:                                              # noqa: BLE001
            pass
    try:
        if not (t.state.get("turn") or {}):
            t.start_turn()
    except Exception:                                                  # noqa: BLE001
        pass
    for row in plan[:2]:
        try:
            t.act(seat=row["seat"], action="search")
        except Exception:                                              # noqa: BLE001
            pass
    return str(t.state.get("run_id") or store.current_run(dst_home) or "")


def seed_run(def_dir: str, dst_home: str, *, with_module: bool = True) -> str:
    """一局的事件流：現役那一局 → 退役歸檔 → 現場合成。`with_module=False` 只影響合成局。"""
    sid = store.copy_current_run(def_dir, dst_home)
    if sid:
        return sid
    sid = _from_archive(dst_home)
    if sid:
        return sid
    return _synthetic(def_dir, dst_home, with_module=with_module)
