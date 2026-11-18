#!/usr/bin/env python3
"""**這台機器上有哪些桌、活著沒**（L6 共用小模組）。

大廳（`lobby_server.py`）要看它們列出來；**桌子自己**也要看——判斷「這個角色的實例是不是
還在**別張活桌**出場」（同帳號同角色不能同時在兩張桌，見 `docs/LOBBY.md` §5）。

兩件事都只讀、都不碰任何桌的狀態：
  * `discover(root)`：掃 `campaigns/*/config.json`（`hidden: true` 的桌不上榜）
  * `probe(t)`：`/health`（不需憑證）＋`/api/log?meta=1`（不需憑證）＋尾讀事件流判「已收局」
  * `live(t)`：**開著且還沒收局**——「還在出場」的判準（離線或已收局都算自由）
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Dict, List, Optional

from trpg.core import layout as layout


#: 探活逾時（秒）——桌子自己要快，大廳可以慢一點（用 `set_timeout` 調）
_TIMEOUT = 1.0


def set_timeout(sec: float) -> None:
    global _TIMEOUT
    _TIMEOUT = float(sec)


def repo_root_of(home: str) -> str:
    """`<repo>/{campaigns/<桌>|modules/<模組>/scenarios/<劇本>}` ⇒ `<repo>`（正本在 `layout.repo_root`）。"""
    return layout.repo_root(home)


def discover(root: str) -> List[Dict[str, Any]]:
    """掃出這台機器上的桌：**新制 `modules/<模組>/scenarios/<劇本>/` 與舊制 `campaigns/<桌>/` 都吃**
    （定位走 `layout.scan`——那是唯一入口）。一張桌一筆，**只讀 config**，不碰 data／registry。

    * `id` ＝ config 的 `table_id`（目錄名可以好聽；對外 id 不變 ⇒ 搬目錄不驚動客戶端）
    * `module`／`scenario` ＝ 在分層裡的身分；`legacy_layout` ＝ 還在舊制（`campaigns/`）底下
    * `hidden: true` 的桌不上榜（照舊）
    """
    out: List[Dict[str, Any]] = []
    for row in layout.scan(root):
        home = row["home"]
        name = os.path.basename(home)
        cfg = layout.read_config(home)
        if cfg is None:
            out.append({"id": name, "home": home, "config_error": True,
                        "module": row["module"], "scenario": row["scenario"],
                        "module_name": row.get("module_name") or row["module"],
                        "scenario_name": row.get("scenario_name") or row["scenario"],
                        "legacy_layout": bool(row["legacy"])})
            continue
        if cfg.get("hidden"):
            continue
        w = cfg.get("watch") or {}
        auth = cfg.get("auth") or {}
        # ★ 2026-10-20（設計者：「所有的都是局」）：觀戰開關是**局級**（`runs/<run>/watch.json`）；
        #   這一局沒有檔（舊局）才退回 config 的預設。
        try:
            from trpg.core import store as _store                                      # noqa: PLC0415
            from trpg.service import run_watch as _rw                                  # noqa: PLC0415
            _run = _store.current_run(home)
            _got = _rw.read(home, _run) if _run else None
        except Exception:                                                              # noqa: BLE001
            _got = None
        _watch_on = bool(w.get("enabled")) if _got is None else bool(_got)
        out.append({
            "id": str(cfg.get("table_id") or name),
            "home": home,
            "module": str(row["module"]),
            "scenario": str(row["scenario"] or cfg.get("scenario") or ""),
            "module_name": str(row.get("module_name") or row.get("module") or ""),
            "scenario_name": str(row.get("scenario_name") or row.get("scenario") or ""),
            "legacy_layout": bool(row["legacy"]),
            "port": cfg.get("port"),
            "scenario_label": str(cfg.get("scenario") or ""),
            # hub 模式沒有 per-table 觀戰埠（`watch.port` 只在單桌獨立行程用）⇒ 兩個都給：
            # `watch_enabled`＝**這一局**的開關（`runs/<run>/watch.json`，舊局退回 config）；
            # `watch_port`＝舊的獨立觀戰埠（部署層，仍看 config）。
            "watch_enabled": bool(_watch_on),
            "watch_port": (w.get("port") if _watch_on else None),
            "accounts": bool(auth.get("accounts_file") or auth.get("db") or auth.get("db_env")
                             or os.path.exists(os.path.join(home, "accounts.json"))),
            "cast": cast_of(home),
        })
    return out


def cast_of(home: str, limit: int = 12) -> List[Dict[str, str]]:
    """這一桌的**選角**（`seats.json`：角色 → 席位；＝公開資料，不是帳號資料）。

    `player: false` 的是 gm／npc 席（劇情角色，但不由玩家坐）。數量上限避免大廳頁炸開。
    """
    try:
        with open(layout.def_file(home, "seats.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
    except Exception:                                                    # noqa: BLE001
        return []
    out = []
    for row in (doc.get("seats") or [])[:limit]:
        ch, seat = row.get("character"), str(row.get("seat") or "")
        if not ch:
            continue
        out.append({"char": str(ch), "seat": seat, "kind": str(row.get("kind") or ""),
                    "player": str(row.get("kind")) in ("human", "agent")})
    return out


def _get_json(url: str, timeout: Optional[float] = None):
    try:
        with urllib.request.urlopen(url, timeout=timeout or _TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except Exception:                                                    # noqa: BLE001
        return None


def ended(home: str) -> bool:
    """尾讀事件流：**最後一筆「有狀態意義」的事件是 `ending`** ⇒ 這局已收（唯讀）。

    看的是尾段（16KB）——「新開一局」會在 `ending` 之後再寫 `table_created` ⇒ 不會誤判成已收。
    讀不到／視窗裡沒有這兩種事件 ⇒ 回 False（寧可保守）。
    """
    p = layout.events_path(home)          # 一局一檔：runs/<session>/events.jsonl（舊制 data/ 相容）
    try:
        size = os.path.getsize(p)
        with open(p, "rb") as fh:
            fh.seek(max(0, size - 16384))
            tail = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return False
    last = None
    for line in tail:
        line = line.strip()
        if not line:
            continue
        try:
            t = json.loads(line).get("type")
        except Exception:                                                # noqa: BLE001
            continue
        if t in ("table_created", "ending"):
            last = t
    return last == "ending"


def probe(t: Dict[str, Any], timeout: Optional[float] = None) -> Dict[str, Any]:
    """探一張桌：`/health`＋`/api/log?meta=1`（兩個都不需憑證）＋尾讀收局。"""
    info = dict(t)
    info.update({"online": False, "seats": None, "turn": None, "events": None,
                 "last_seq": None, "ended": ended(t.get("home") or "")})
    port = t.get("port")
    if not port:
        return info
    h = _get_json(f"http://127.0.0.1:{port}/health", timeout)
    if isinstance(h, dict):
        info["online"] = True
        info["seats"] = h.get("seats")
        info["turn"] = h.get("turn")
    m = _get_json(f"http://127.0.0.1:{port}/api/log?meta=1", timeout)
    if isinstance(m, dict):
        info["events"] = m.get("count")
        info["last_seq"] = m.get("last_seq")
    return info


def live(t: Dict[str, Any], timeout: Optional[float] = None) -> bool:
    """**開著且還沒收局** ⇒ 這一桌還在出場。"""
    p = probe(t, timeout)
    return bool(p.get("online")) and not bool(p.get("ended"))
