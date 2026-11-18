"""base（自 __init__.py 拆出）"""

from __future__ import annotations
from typing import Any, Dict, List, Optional

from .common import PLAYER_KINDS, SCRUB_TYPES

def own(value: Any) -> Any:
    """**遞迴重建**（不是淺拷貝）：回傳的 dict／list 全是新物件，leaf 值照抄。

    這正是「禁止回傳 state 參照、禁止 spread 拷貝」要的東西：`dict(x)` 只換了外殼、
    nested 物件仍與 state 共用（改一層就污染桌子）；這裡一路重建到 leaf。
    """
    if isinstance(value, dict):
        return {k: own(v) for k, v in value.items()}
    if isinstance(value, list):
        return [own(v) for v in value]
    return value

def scrub_public(ev: Dict[str, Any], seat: Optional[str]) -> Dict[str, Any]:
    """公開日誌給「非 GM」看的版本：碎片事件裡的內部 id（`f_xxx`）只留給當事人，
    其他人只看到「有人拿到／出示了一枚碎片」。存檔事件不動，改的只是投影。

    `seat=None`（匿名 `/api/log` 投影）＝「不是任何當事人」⇒ 一律 scrub。
    **不能只寫 `seat not in (holder, to)`**：`fragment_granted` 沒有 `to`（`to is None`），
    `None in (holder, None)` 會讓匿名那條路被誤判成「當事人」而漏掉 scrub
    ——2026-10-05 逐欄/獨立對帳抓到的。

    回傳一律是**新物件**（scrub 與否都重建）。
    """
    out = own(ev)
    if ev.get("type") in SCRUB_TYPES and ev.get("fid"):
        parties = [p for p in (ev.get("holder"), ev.get("to")) if p is not None]
        if seat is None or seat not in parties:
            out.pop("fid", None)
    return out

def seat_kind_public(kind: Any, gm: bool = False) -> Any:
    """席位種類的**對外**值：GM 看真值，其餘人一律 `player`。

    呼叫端：`_seat_public()`（席位列表）、`graphql_api` 的進場選項（`entry`——那條**連 GM 例外都不給**，
    選角流程不需要知道誰是 AI）。
    """
    if gm:
        return kind
    return "player" if kind in PLAYER_KINDS else kind

def secret_window(cfg: Optional[Dict[str, Any]]) -> int:
    """私訊（`your_secrets`）在**會進模型上下文**的地方要列幾則。

    `view.secret_window`（預設 **8**）：最近 N 則；**`<=0` ＝全部**（＝舊行為）。

    為什麼要有：現役桌跑到第 5 天，單一席位的私訊堆到 8.7k 字（p2 9.9k／p3 7.0k／p4 2.8k），
    而每一次喚醒、每一次 `table_view` 都把它整包重送一次——**那是無上限長出來的，不是設計**。
    投影本身**不裁**（網頁的 messenger 要完整收件匣、`detail=full` 要查得到全部），
    裁的只有「要送進模型的那兩處」：喚醒訊息（`wake.kick_prompt`）與 `detail=compact` 的視圖。
    """
    try:
        n = int(((cfg or {}).get("view") or {}).get("secret_window", 8))
    except (TypeError, ValueError):
        n = 8
    return n

def trim_secrets(secrets: Any, cfg: Optional[Dict[str, Any]]) -> "tuple[list, int]":
    """回 `(要列出的那幾則, 被藏起來的則數)`。按 `seq` 排（投影不保證呼叫方給的順序）。"""
    sec = sorted((secrets or []), key=lambda s: (s.get("seq") if isinstance(s, dict) else 0) or 0)
    w = secret_window(cfg)
    if w <= 0 or len(sec) <= w:
        return sec, 0
    return sec[-w:], len(sec) - w

def _ending_info(deps: Any, state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """結局的名稱／類型：從劇本 `module.json` 的 `endings[]` 查（沒有＝只有 id）。"""
    eid = state.get("ended")
    if not eid:
        return None
    mod = getattr(deps, "module", None)
    rows = []
    if mod is not None and hasattr(mod, "endings"):
        try:
            rows = [r for r in (mod.endings() or [])]
        except Exception:                                                    # noqa: BLE001
            rows = []
    row = next((r for r in rows if str(r.get("id")) == str(eid)), {}) or {}
    return {"id": str(eid), "kind": row.get("kind") or "neutral",
            "name_jp": row.get("name_jp") or str(eid),
            "name_cn": row.get("name_cn") or row.get("name_jp") or str(eid)}

def _run_summary(deps: Any, state: Dict[str, Any]) -> Dict[str, Any]:
    """★3 結局的**戰果結算**（純讀 state／事件；不寫任何東西）。

    給前端做結局卡：這一局打了多久、幾手、各席最後的樣子（HP／等級／經驗／貨幣／道具／狀態）、
    願望達成數、還沒交出去的碎片數。
    """
    # ⚠ L2 紀律：投影不准 `dict(／list(` 淺拷貝（`test_layers` B4 咬住）——一律用推導式重建。
    evs = [e for e in (getattr(deps, "events", None) or [])]
    seats = [s for s in (state.get("seats") or {}).values()]
    semi = state.get("semi") or {}
    goals = semi.get("goals") or {}
    t_end = next((e.get("ts") for e in evs if e.get("type") == "ending"), None)
    t_beg = evs[0].get("ts") if evs else None
    _coin = getattr(deps, "slot", lambda _n: "star_coins")("coin")
    return {
        "turns": int(state.get("turn_index", -1)) + 1,
        "rounds": int(state.get("round_no") or 0),
        "events": len(evs),
        "started_at": t_beg,
        "ended_at": t_end,
        "seconds": (int(t_end - t_beg) if (t_beg and t_end and t_end >= t_beg) else None),
        "goals_achieved": sum(1 for g in goals.values() if (g or {}).get("achieved")),
        "goals_total": len(goals),
        "fragments_out": len([1 for h in (semi.get("frag_holder") or {}).values() if h]),
        "seats": [{
            "seat": s.get("seat"), "name": s.get("name"), "kind": s.get("kind"),
            "hp": s.get("hp"), "max_hp": s.get("max_hp"),
            "level": (state.get("level") or {}).get(s.get("seat")),
            "exp": (state.get("exp") or {}).get(s.get("seat")),
            "star": (state.get("star") or {}).get(s.get("seat")),
            "coin": s.get(_coin), "star_coins": s.get(_coin),
            "status": [x for x in (s.get("status") or [])],
            "conditions": [c for c in ((state.get("conditions") or {}).get(s.get("seat")) or {})],
            "items": {k: v for k, v in ((state.get("inventory") or {}).get(s.get("seat")) or {}).items()},
        } for s in seats],
    }

def _assets(deps: Any) -> Any:
    """這一桌的**模組級**素材表（`assets.Assets`；沒有＝None）。

    素材是「內容」不是「規則」⇒ 投影只拿它決定「露哪幾格圖」；查不到別名就**不加那一格**
    （沒有素材的模組／legacy 桌：投影逐字不變）。模組之間互不可見是 `assets` 那層保證的。
    """
    a = getattr(deps, "assets", None)
    try:
        return a if (a is not None and a.ok) else None
    except Exception:                                             # noqa: BLE001
        return None

def _aurl(deps: Any, alias: str) -> Optional[str]:
    """別名 ⇒ 對外 URL（`/assets/<模組>/…`）；沒有素材／查不到 ⇒ None。"""
    a = _assets(deps)
    if a is None or not alias:
        return None
    try:
        return a.url(alias)
    except Exception:                                             # noqa: BLE001
        return None

def _seat_art(deps: Any, out: Dict[str, Any], seat: Any, spec: Optional[Dict[str, Any]] = None) -> None:
    """把這一席的立繪／頭像填進 `out`（**查不到就不加欄位**）。

    同一個角色有**服裝版本**時（素材清單的別名 `portrait:<id>:<outfit>`，例 `portrait:char_a:festival`）
    另外給 `portraits`＝可選立繪清單（`{id, url}`，第一筆是預設）⇒ 前端在進場頁讓人挑；
    只有一種立繪就**不加**這個欄位（舊桌／舊模組逐字不變）。角色表的 `outfits` 有寫顯示名的
    那幾筆多一鍵 `name`（見 `_outfit_names`；沒寫就不加）。
    """
    sp = spec if spec is not None else ((getattr(deps, "seats_spec", {}) or {}).get(seat) or {})
    cid = str(sp.get("character") or "")
    if not cid:
        return
    for key, alias in (("portrait", f"portrait:{cid}"), ("avatar", f"avatar:{cid}")):
        u = _aurl(deps, alias)
        if u:
            out[key] = u
    a = _assets(deps)
    if a is not None:
        alts: List[Dict[str, Any]] = []
        try:
            for alias in sorted(a.aliases()):
                if alias.startswith(f"portrait:{cid}:"):
                    u = a.url(alias)
                    if u:
                        alts.append({"id": alias.rsplit(":", 1)[1], "url": u})
        except Exception:                                         # noqa: BLE001
            alts = []
        if alts and out.get("portrait"):
            out["portraits"] = [{"id": "default", "url": out["portrait"]}] + alts
            names = _outfit_names(a, cid)
            for p in out["portraits"]:
                if p["id"] in names:
                    p["name"] = names[p["id"]]

def _outfit_names(a: Any, cid: str) -> Dict[str, Any]:
    """角色表（`characters.json`）裡這個角色的服裝顯示名：`{服裝 id: name}`（沒寫＝空的）。

    `outfits: [{id, name, alias_id?}]`——`name` 可以是字串或多語系物件，原樣交給前端。
    `alias_id`（舊的角色 id，例 `festival_char_a`）也認：素材別名若還用舊名當服裝鍵也對得上。
    查不到名字就**不加** `name`（沒寫服裝表的模組 `portraits` 逐字不變）。
    """
    try:
        from trpg.core import layout as layout                                                 # 只讀模組的角色表（不帶規則）
        if not (a.root and a.module):
            return {}
        row = layout.characters(a.root, a.module).get(cid) or {}
        names: Dict[str, Any] = {}
        for o in (row.get("outfits") or []):
            if isinstance(o, dict) and o.get("id") and o.get("name"):
                names.setdefault(str(o["id"]), o["name"])
                if o.get("alias_id"):
                    names.setdefault(str(o["alias_id"]), o["name"])
        return names
    except Exception:                                                 # noqa: BLE001
        return {}

def _with_icon(deps: Any, rows: List[Dict[str, Any]], alias_of: Any) -> List[Dict[str, Any]]:
    """每一列多一鍵 `icon`（查不到就**不加那個鍵**——沒有素材的桌一格都不多）。"""
    out: List[Dict[str, Any]] = []
    for r in rows:
        d = {k: r[k] for k in r}
        try:
            u = _aurl(deps, alias_of(r))
        except Exception:                                         # noqa: BLE001
            u = None
        if u:
            d["icon"] = u
        out.append(d)
    return out
