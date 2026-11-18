"""trpg_accounts 的共用小工具（registry 讀寫／桌級 session 存取）。"""

from __future__ import annotations
import argparse
import hashlib
import json
import os
import secrets
import sys
import time


def _hash(tok: str) -> str:
    return hashlib.sha256(tok.encode("utf-8")).hexdigest()


def _load(path: str) -> dict:
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _save(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def _registry_path(args) -> str:
    """`--registry` ＞ 該桌 config 的 `auth.accounts_file` ＞ `<home>/accounts.json`。"""
    if getattr(args, "registry", None):
        return os.path.abspath(os.path.expanduser(args.registry))
    home = os.path.abspath(args.home)
    cfg = _load(os.path.join(home, "config.json"))
    p = str(((cfg.get("auth") or {}).get("accounts_file")) or "").strip()
    if p:
        return os.path.abspath(os.path.expanduser(p))
    return os.path.join(home, "accounts.json")


def _table_id(args, home: str) -> str:
    if getattr(args, "table", None):
        return str(args.table)
    cfg = _load(os.path.join(home, "config.json"))
    return str(cfg.get("table_id") or os.path.basename(home.rstrip("/")))


def _sessions(reg: dict) -> dict:
    """`sessions{<桌>: …}`；**舊形狀頂層 `session` 借住進來**（讀舊檔不炸，migrate 才正式搬）。"""
    raw = reg.get("sessions")
    if not isinstance(raw, dict):
        raw = {}
        reg["sessions"] = raw
    legacy = reg.get("session")
    if isinstance(legacy, dict) and legacy and legacy.get("table"):
        raw.setdefault(str(legacy["table"]), legacy)
    return raw


def _sess(reg: dict, table: str) -> dict:
    s = _sessions(reg).setdefault(table, {"table": table, "seats": {}})
    s.setdefault("table", table)
    if not isinstance(s.get("seats"), dict):
        s["seats"] = {}
    return s


def _tables_of(reg: dict, aid: str) -> list:
    return sorted(t for t, s in _sessions(reg).items()
                  if any((b or {}).get("account") == aid for b in (s.get("seats") or {}).values()))


def _state_growth(home: str, seat: str) -> dict:
    """從事件流折出這一席現在的成長（level／exp／star）——沒有就給基線 1／0／None。"""
    out = {"level": 1, "exp": 0}
    from trpg.core import layout as layout                                                       # noqa: PLC0415
    p = layout.events_path(home)          # 一局一檔（舊制 data/ 相容）
    if not os.path.exists(p):
        return out
    try:
        from trpg.engine import Table                                        # noqa: PLC0415
        from trpg.core import store as store                                                    # noqa: PLC0415
        cfg = _load(os.path.join(home, "config.json"))
        cfg["port"] = 0
        t = Table(home=home, cfg=cfg, store=store.FileStore(home), clock=lambda: 0.0)
        out["level"] = int(t.level_of(seat)) if hasattr(t, "level_of") else 1
        out["exp"] = int(t.exp_of(seat)) if hasattr(t, "exp_of") else 0
        if hasattr(t, "star_of") and (t.state.get("star") or {}).get(seat):
            out["star"] = int(t.star_of(seat))
    except Exception as exc:                                            # noqa: BLE001
        print(f"  ⚠ 讀不到 {seat} 的成長（{type(exc).__name__}: {exc}）⇒ 用基線 1／0", file=sys.stderr)
    return out


def _state_star_coins(home: str, seat: str) -> int:
    """這一席現在的貨幣（通用貨幣 ⇒ 播進 account 的錢包）。"""
    try:
        from trpg.engine import Table                                       # noqa: PLC0415
        from trpg.core import store as store                                                   # noqa: PLC0415
        cfg = _load(os.path.join(home, "config.json"))
        cfg["port"] = 0
        t = Table(home=home, cfg=cfg, store=store.FileStore(home), clock=lambda: 0.0)
        return int((t.state.get("seats") or {}).get(seat, {}).get("star_coins") or 0)
    except Exception:                                                   # noqa: BLE001
        return 0
