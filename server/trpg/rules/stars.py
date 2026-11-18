#!/usr/bin/env python3
"""**L1｜星等（升星）**（2026-10-07 設計者拍：升星＝**劇情事件觸發**，不是數字門檻）。

第二條成長軸，與「等級／成長值」（`advancement.py`）**互相獨立**：

| 軸 | 燃料 | 誰決定 |
|---|---|---|
| 等級（`level`／`growth`） | 一個累計值（擊破／GM 裁定／劇本 `growth_gain`） | 表上的門檻（`kind=levels`） |
| **星等（`star`）** | **劇情條件**（AST 判據：完成自己的願望、通關某層、劇本寫的事實…） | 表上的 `when`（`kind=stars`） |

表（`packs/<id>/tables/stars.json`）：

```json
{"kind": "stars",
 "rows": [{"star": 2, "when": {"or": [{"var": "me.goal_achieved"},
                                      {"==": [{"var": "world.event_type"}, "boss_defeated"]}]},
           "gains": {"max_hp": 4, "attrs": {"wil": 1}, "resources": {"mana": 1}},
           "unlocks": ["hyousou_no_sho"], "title": "星の担い手"}]}
```

機制（**每一筆事件後判一次**，與結局同一拍；先搶先贏＝`star` 小的先判）：

* 對**每一席**各判一次（判據裡的 `me` ＝那一席）——`me.goal_achieved` 就是「**完成自己的願望**」。
* 只要「下一星」的 `when` 成立就升，**可連升**（一直判到不成立或到表頂）。
* `star_up` 事件**公開**，帶 `star`／`gains`／`unlocks`／`title`，並把成長的**結果值**寫進事件
  （`after`：`max_hp`／`hp`／`attrs`／`resources`）⇒ 回放不必重算。
* `unlocks`：技能表的 `lvl` 之外多一條路——被解鎖的技能**不受等級限制**（`known=true`）。
* `star_set`（effect／GM `op=star`）＝當場給星（劇本自己判的劇情也能直接給）。

紀律：沒啟用帶星等表的包 ⇒ `stars_table` 是空的 ⇒ 一個事件都不多發、投影不多任何欄位（**legacy 零變更**）；
schema 壞＝載入時叫；不准直接改 state（一切由 `star_up`／`star_set` 事件折出）。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from trpg.rules import expr

#: 星等表在包裡的形狀（`{"kind": "stars", "rows": [...]}`）
TABLE_KIND = "stars"

#: 一列認得的欄位
ROW_KEYS = ("star", "when", "gains", "unlocks", "title", "name_jp", "name_cn", "note")

#: `gains` 區塊認得的鍵（與等級表的成長同一套語意）
GAIN_KEYS = ("max_hp", "attrs", "resources")


class StarError(ValueError):
    """星等表／升星操作不合法（載入或呼叫時就叫，不靜默）。"""


def validate(rows: Any, *, require_when: bool = True) -> List[str]:
    """表內容合不合法（作者工具與 `test_stars` 共用）。

    `require_when=False`：**渠道制**（表有 `channels`）時，`rows` 只負責「每一星的成長」，升星條件
    已經搬到 `channels` ⇒ 逐級的 `when` 可以不寫。
    """
    e: List[str] = []
    if not isinstance(rows, list):
        return ["rows 要是陣列"]
    seen = set()
    for i, row in enumerate(rows):
        where = f"rows[{i}]"
        if not isinstance(row, dict):
            e.append(f"{where}: 要是物件")
            continue
        st = row.get("star")
        where = f"rows[{i}](★{st})"
        if not isinstance(st, int) or st < 1:
            e.append(f"{where}.star: 要是 >=1 的整數（★幾）")
        elif st in seen:
            e.append(f"{where}: star 重複")
        seen.add(st)
        if require_when and not row.get("when"):
            e.append(f"{where}.when: 升星的條件（AST 判據）是必填——升星靠劇情，不靠數字")
        gains = row.get("gains")
        if gains is not None:
            if not isinstance(gains, dict):
                e.append(f"{where}.gains: 要是物件（{{max_hp, attrs, resources}}）")
            else:
                for k in gains:
                    if k not in GAIN_KEYS:
                        e.append(f"{where}.gains: 不認識的鍵 {k!r}（只有 {'／'.join(GAIN_KEYS)}）")
                if "max_hp" in gains and not isinstance(gains["max_hp"], int):
                    e.append(f"{where}.gains.max_hp: 要是整數")
                for k in ("attrs", "resources"):
                    if k in gains and not isinstance(gains[k], dict):
                        e.append(f"{where}.gains.{k}: 要是物件（{{名: 值}}）")
        for k in ("title", "name_jp", "name_cn", "note"):
            if row.get(k) is not None and not isinstance(row[k], str):
                e.append(f"{where}.{k}: 要是字串")
        un = row.get("unlocks")
        if un is not None and (not isinstance(un, list) or any(not isinstance(x, str) for x in un)):
            e.append(f"{where}.unlocks: 要是字串陣列（技能 id）")
        for k in row:
            if k not in ROW_KEYS:
                e.append(f"{where}: 不認識的欄位 {k!r}（只有 {'／'.join(ROW_KEYS)}）")
    return e


def load(paths: Any) -> List[Dict[str, Any]]:
    """讀 `packs.tables` 裡所有 `kind=stars` 的表（合成後**依 star 排序**）。"""
    out: Dict[int, Dict[str, Any]] = {}
    for p in (paths or []):
        if not p or not os.path.exists(p):
            continue
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or data.get("kind") != TABLE_KIND:
            continue                                    # 不是星等表：與這一支無關（別人的表）
        rows = data.get("rows") or []
        errs = validate(rows, require_when=not isinstance(data.get("channels"), dict))
        if errs:
            raise StarError(f"{os.path.basename(p)}: " + "；".join(errs[:8]))
        for row in rows:
            st = int(row["star"])
            if st in out and out[st] != row:
                raise StarError(f"★{st} 在兩張表裡寫得不一樣（{p}）")
            out[st] = dict(row)
    return [out[k] for k in sorted(out)]


def load_channel_spec(paths: Any) -> Dict[str, Any]:
    """讀 stars 表**頂層**的 `channels`／`max_star`（多份表合併；同渠道寫法不一致⇒叫）。

    **渠道制**（2026-10-20 設計者：「這是劇本決定的，我只是說有這個機制」）：

    ```json
    {"kind": "stars", "max_star": 5,
     "channels": {"shop": {"max": 2, "doc": "在星見の店用貨幣買"},
                  "goal": {"max": 2, "when": {"var": "me.goal_achieved"}},
                  "side": {"max": 2, "when": {"exists": "facts.side_quest_done"}}},
     "rows": [{"star": 2, "when": …, "gains": {…}}]}
    ```

    * 每渠道 `max`＝這一條路**最多能漲幾星**；`when`（選填）＝自動判成立的條件（AST 判據，`me`＝那一席）。
    * 星等 ＝ `1 ＋ Σ 各渠道已漲（各自 clamp 到 max）`，再封頂 `max_star`（預設＝表裡最大的 star）。
    * **引擎不含任何數字**：哪幾條渠道、各能漲幾星、怎麼觸發，全在劇本的表裡。
    * 沒有 `channels` ⇒ **舊制**（逐級 `when` 判據，行為一字不變）。
    """
    out: Dict[str, Any] = {}
    for p in (paths or []):
        if not p or not os.path.exists(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:                                                 # noqa: BLE001
            continue
        if not isinstance(data, dict) or data.get("kind") != TABLE_KIND:
            continue
        chs = data.get("channels")
        if isinstance(chs, dict):
            for name, spec in chs.items():
                spec = dict(spec or {})
                mx = spec.get("max")
                if not isinstance(mx, int) or mx < 0:
                    raise StarError(f"{os.path.basename(p)}: channels.{name}.max 要是 >=0 的整數")
                if name in out and out[name] != spec:
                    raise StarError(f"渠道 {name} 在兩張表裡寫得不一樣（{p}）")
                out[str(name)] = spec
        if data.get("max_star") is not None:
            v = data["max_star"]
            if not isinstance(v, int) or v < 1:
                raise StarError(f"{os.path.basename(p)}: max_star 要是 >=1 的整數")
            if out.get("_max_star") not in (None, v):
                raise StarError(f"max_star 在兩張表裡寫得不一樣（{p}）")
            out["_max_star"] = v
    return out


class StarsMixin:
    """星等機制的實作。**表是空的 ⇒ 全部方法都是 no-op**（legacy 零變更）。"""

    # ------------------------------------------------------------------ 載入
    def stars_init(self, tables: Any = None) -> None:
        """載入星等表（`tables` 來自 L1 的 `PackView.tables`）＋渠道規格（`channels`／`max_star`）。"""
        self.stars_table: List[Dict[str, Any]] = load(tables)
        _spec = load_channel_spec(tables)
        self.star_cap: int = int(_spec.pop("_max_star", 0) or 0)
        self.star_channels: Dict[str, Dict[str, Any]] = _spec

    # ------------------------------------------------------------------ 折算
    def stars_apply(self, ev: Dict[str, Any]) -> None:
        """把 `star_up`／`star_set` 折進 `state["star"]`（事件溯源；`after` 是結果值）。"""
        st = self.state.get("star")
        if st is None:
            return
        t = ev.get("type")
        if t == "star_up":
            st[str(ev["seat"])] = int(ev.get("star") or 1)
            for key, val in ((ev.get("after") or {})).items():
                s = self.state["seats"].get(ev["seat"])
                if s is None:
                    continue
                if key in ("hp", "max_hp"):
                    s[key] = int(val)
                elif key == "attrs":
                    s["attrs"] = {k: int(v) for k, v in (val or {}).items()}
                elif key == "resources":
                    self.state.setdefault("resources", {})[str(ev["seat"])] = {
                        k: int(v) for k, v in (val or {}).items()}
        elif t == "star_set":
            st[str(ev["seat"])] = int(ev.get("after") or 1)
        elif t == "star_grant":
            # 渠道計數（不公開的事件）：`{seat, channel, value}` ⇒ 累加
            ch = self.state.setdefault("star_ch", {})
            row = ch.setdefault(str(ev["seat"]), {})
            row[str(ev["channel"])] = int(row.get(str(ev["channel"])) or 0) + int(ev.get("value") or 0)

    # ------------------------------------------------------------------ 讀出口
    def star_of(self, seat: str) -> int:
        """這一席現在的星等（沒有紀錄＝1★；沒啟用星等表也回 1）。"""
        return int((self.state.get("star") or {}).get(str(seat)) or 1)

    def star_row(self, star: int) -> Optional[Dict[str, Any]]:
        for row in (getattr(self, "stars_table", None) or []):
            if int(row.get("star") or 0) == int(star):
                return row
        return None

    def stars_max(self) -> int:
        """表裡最大的星（有 `max_star` 就取它；都沒有＝1）。"""
        cap = int(getattr(self, "star_cap", 0) or 0)
        rows = getattr(self, "stars_table", None) or []
        table_max = max([int(r.get("star") or 1) for r in rows], default=1)
        return max(cap, table_max) if cap else table_max

    def unlocks_of(self, seat: str) -> List[str]:
        """這一席**已經拿到**的解鎖（★ ≤ 目前星等的所有 `unlocks`；沒有＝空）。"""
        cur = self.star_of(seat)
        out: List[str] = []
        for row in (getattr(self, "stars_table", None) or []):
            if int(row.get("star") or 0) <= cur:
                for x in (row.get("unlocks") or []):
                    if x not in out:
                        out.append(str(x))
        return out

    def stars_view(self, seat: str) -> Dict[str, Any]:
        """給投影用：現在的星等／稱號／下一星的解鎖（沒有表＝空 dict ⇒ 投影不加欄位）。"""
        if not (getattr(self, "stars_table", None) or []):
            return {}
        cur = self.star_of(seat)
        row = self.star_row(cur) or {}
        nxt = self.star_row(cur + 1) or {}
        view: Dict[str, Any] = {
            "star": cur,
            "max": self.stars_max(),
            "title": row.get("title") or row.get("name_jp"),
            "next_title": nxt.get("title") or nxt.get("name_jp"),
            "next_unlocks": [x for x in (nxt.get("unlocks") or [])],
        }
        # 渠道制：露「這一條路我還差幾星」（只露自己的；數字來自劇本）
        chs = getattr(self, "star_channels", None) or {}
        if chs:
            got = self.star_channel_of(seat)
            view["channels"] = {str(name): {"done": min(int(got.get(str(name)) or 0), int(spec.get("max") or 0)),
                                            "max": int(spec.get("max") or 0),
                                            "doc": spec.get("doc")}
                                for name, spec in chs.items()}
        return view

    # ------------------------------------------------------------------ 開局
    def stars_baseline(self, seat: str) -> Optional[Dict[str, Any]]:
        """開局給 ★1（**只有啟用星等表、而且這一席還沒有紀錄**時才發事件）。

        跟 `advancement_baseline`（開局第 1 級）同一個道理：不這麼做的話，玩家要等到**第一次升星**
        才看得到自己的星等與「下一星條件」（`you.star` 只在有紀錄時才露）。
        ⚠ 沒啟用星等表的桌 ⇒ 一個事件都不多發（legacy 零變更）。
        """
        if not (getattr(self, "stars_table", None) or []):
            return None
        if str(seat) in (self.state.get("star") or {}):
            return None
        return self._star_up(str(seat), 1, {"gains": {}, "unlocks": [], "title": None}, by="join")

    # ------------------------------------------------------------------ 當場給星
    def star_set(self, *, seat: str, star: int = 0, by: str = "gm",
                 note: Optional[str] = None) -> Dict[str, Any]:
        """當場給／設星等（GM `op=star`／劇本 `star_set` effect；`star` 省略＝加一星）。"""
        if not (getattr(self, "stars_table", None) or []):
            raise StarError("這張桌沒有啟用帶星等表的機制包（`config.packs` 裡加帶 stars 表的包）")
        s = str(seat or "").strip()
        if s not in (self.state.get("seats") or {}):
            raise StarError(f"沒有這個席位：{s!r}")
        cur = self.star_of(s)
        want = int(star) if star else cur + 1
        if want < 1 or want > self.stars_max():
            raise StarError(f"星等要在 1～{self.stars_max()} 之間（現在 ★{cur}，要給 ★{want}）")
        if want <= cur:
            raise StarError(f"{s} 已經是 ★{cur}（不能降到 ★{want}）")
        # 一次給多星 ⇒ **成長累積**（cur+1 … want 每一列的 gains 疊起來、unlocks 併集、稱號取最後一個）
        gains: Dict[str, Any] = {"max_hp": 0, "attrs": {}, "resources": {}}
        unlocks: List[str] = []
        title: Optional[str] = None
        for r in (self.stars_table or []):
            if cur < int(r.get("star") or 0) <= want:
                g = r.get("gains") or {}
                gains["max_hp"] += int(g.get("max_hp") or 0)
                for k, v in (g.get("attrs") or {}).items():
                    gains["attrs"][str(k)] = int(gains["attrs"].get(str(k), 0)) + int(v)
                for k, v in (g.get("resources") or {}).items():
                    gains["resources"][str(k)] = int(gains["resources"].get(str(k), 0)) + int(v)
                for x in (r.get("unlocks") or []):
                    if x not in unlocks:
                        unlocks.append(str(x))
                title = r.get("title") or r.get("name_jp") or title
        ev = self._star_up(s, want, {"gains": gains, "unlocks": unlocks, "title": title},
                           by=by, note=note, forced=True)
        return {"seat": s, "star": want, "event": ev}

    # ------------------------------------------------------------------ 渠道（shop／goal／side…）
    def star_channel_of(self, seat: str) -> Dict[str, int]:
        """這一席各渠道已經漲了幾星（`state["star_ch"]`；沒紀錄＝空）。"""
        return dict(((self.state.get("star_ch") or {}).get(str(seat)) or {}))

    def _star_target(self, seat: str) -> int:
        """按渠道計數算出「這一席應該在幾星」：`1 ＋ Σ clamp(count, 渠道 max)`，封頂 `max_star`。"""
        if not (getattr(self, "star_channels", None) or {}):
            return self.star_of(seat)
        ch = self.star_channel_of(seat)
        total = 1
        for name, spec in (self.star_channels or {}).items():
            total += max(0, min(int(ch.get(name) or 0), int(spec.get("max") or 0)))
        return max(1, min(int(total), self.stars_max()))

    def star_grant(self, *, seat: str, channel: str, value: int = 1,
                   by: str = "stars", note: Optional[str] = None) -> Dict[str, Any]:
        """**給某席的某渠道 +N**——買星、完成私密任務、全體副任務都走這一條。

        受兩層封頂：該渠道的 `max`、以及整體 `max_star`（已經滿了就回 `capped`，不報錯也不發事件）。
        數字全部來自劇本（渠道 `max`／商品的 `star`／trigger 的 `value`），**引擎不含任何數值**。
        """
        if not (getattr(self, "stars_table", None) or []):
            raise StarError("這張桌沒有啟用帶星等表的機制包（`config.packs` 裡加帶 stars 表的包）")
        s = str(seat or "").strip()
        if s not in (self.state.get("seats") or {}):
            raise StarError(f"沒有這個席位：{s!r}")
        chs = getattr(self, "star_channels", None) or {}
        spec = chs.get(str(channel))
        if not isinstance(spec, dict):
            raise StarError(f"這張桌沒有這個星等渠道：{channel!r}（有：{'／'.join(chs) or '無'}）")
        room_ch = max(0, int(spec.get("max") or 0) - int(self.star_channel_of(s).get(str(channel)) or 0))
        room_all = max(0, self.stars_max() - self.star_of(s))
        add = max(0, min(int(value), room_ch, room_all))
        if add <= 0:
            return {"seat": s, "channel": str(channel), "added": 0, "star": self.star_of(s),
                    "capped": True, "reason": ("channel_max" if room_ch <= 0 else "star_cap")}
        self._emit("star_grant", public=False, seat=s, channel=str(channel),
                   value=add, by=by, note=note)
        ev = None
        want = self._star_target(s)
        if want > self.star_of(s):
            ev = self.star_set(seat=s, star=want, by=by, note=note).get("event")
        return {"seat": s, "channel": str(channel), "added": add,
                "star": self.star_of(s), "event": ev}

    # ------------------------------------------------------------------ 判定（每一筆事件後）
    def star_seats(self) -> List[str]:
        """**會升星的席位**：只有玩家席（human／agent）。

        ★ 2026-10-20：GM／NPC 不升星（設計者 2026-10-08「GM 不应该升级和升星」）。
        以前這裡直接遍歷 `state["seats"]` ⇒ 全域條件（例 `facts.side_quest_done`）會讓
        GM／NPC 也吃到星等與成長，還會發**公開**的 `star_up` 事件（實測 gm／npc1 各 ★3）。
        """
        out: List[str] = []
        for sid, s in (self.state.get("seats") or {}).items():
            kind = str((s or {}).get("kind") or "")
            if kind in ("human", "agent"):
                out.append(str(sid))
        return out

    def stars_check(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        """**劇情事件觸發升星**：每一席各判一次「下一星的條件成立了嗎」，成立就升（可連升）。

        判據裡的 `me` 就是那一席 ⇒ `me.goal_achieved`＝完成自己的願望、`world.pray_first.<節點>`＝
        誰先開了那個祈願箱、`facts.<鍵>`＝劇本自己寫的事實。

        ⚠ 只有玩家席（`star_seats()`）會被判——GM／NPC 不升星。
        """
        rows = getattr(self, "stars_table", None) or []
        mod = getattr(self, "module", None)
        if not rows or mod is None:
            return []
        out: List[Dict[str, Any]] = []
        # ★ 2026-10-20 渠道制：劇本宣告了 `channels` ⇒ 改判各渠道的 `when`（每渠道各自封頂），
        #   星等 = 1 ＋ Σ已漲。沒有 `channels` ⇒ 走下面的舊制（逐級 when），行為一字不變。
        chs = getattr(self, "star_channels", None) or {}
        if chs:
            for sid in self.star_seats():
                ctx = mod.ctx(self, seat=sid, event=event)
                for name, spec in chs.items():
                    if not spec.get("when"):
                        continue                      # 沒有 when 的渠道＝只能靠外部給（買星／effect）
                    if int(self.star_channel_of(sid).get(name) or 0) >= int(spec.get("max") or 0):
                        continue
                    try:
                        ok = bool(expr.evaluate(spec["when"], ctx))
                    except Exception:                                         # noqa: BLE001
                        ok = False
                    if not ok:
                        continue
                    # 條件一成立 ⇒ **把這條渠道補滿**（不是每筆事件 +1）：劇本寫「完成 X ⇒ 這條路值 2 星」，
                    # 語意就是「達成一次拿滿」，不是「達成後随时间慢慢涨」。
                    _room = int(spec.get("max") or 0) - int(self.star_channel_of(sid).get(name) or 0)
                    r = self.star_grant(seat=sid, channel=str(name), value=_room, by="stars")
                    if r.get("event"):
                        out.append(r["event"])
            return out
        for sid in self.star_seats():
            guard = 0
            while guard < len(rows) + 1:
                guard += 1
                cur = self.star_of(sid)
                nxt = self.star_row(cur + 1)
                if not nxt:
                    break
                ctx = mod.ctx(self, seat=sid, event=event)
                try:
                    ok = bool(expr.evaluate(nxt.get("when"), ctx))
                except Exception:                                             # noqa: BLE001
                    ok = False
                if not ok:
                    break
                out.append(self._star_up(sid, int(nxt["star"]), nxt, by="stars"))
        return out

    # ------------------------------------------------------------------ 內部
    def _star_up(self, seat: str, star: int, row: Dict[str, Any],
                 by: str = "stars", note: Optional[str] = None,
                 forced: bool = False) -> Dict[str, Any]:
        """升到 `star`：成長的**結果值**寫進事件（`after`）⇒ 回放不必重算。"""
        gains = row.get("gains") or {}
        s = self.state["seats"].get(seat) or {}
        attrs = dict(s.get("attrs") or {})
        for k, v in (gains.get("attrs") or {}).items():
            attrs[str(k)] = int(attrs.get(str(k), 0)) + int(v)
        max_hp = int(s.get("max_hp") or 0) + int(gains.get("max_hp") or 0)
        hp = min(max_hp or 10 ** 6, int(s.get("hp") or 0) + int(gains.get("max_hp") or 0))
        res = dict((self.state.get("resources") or {}).get(seat) or {})
        for k, v in (gains.get("resources") or {}).items():
            res[str(k)] = max(0, int(res.get(str(k), 0)) + int(v))
        after = {"star": star, "max_hp": max_hp, "hp": hp, "attrs": attrs, "resources": res}
        return self._emit("star_up", public=True, seat=seat, star=star, gains=gains,
                          unlocks=[x for x in (row.get("unlocks") or [])],
                          title=row.get("title"), after=after, by=by, note=note,
                          forced=bool(forced))
