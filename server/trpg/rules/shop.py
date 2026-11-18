#!/usr/bin/env python3
"""**L1｜商店**（★2，2026-10-07 設計者拍「繼續按你的順序」）：把商店接上**道具表**。

以前商店的貨品住在 `config.tower.shop`，而且買到的東西只給 `heal`／`memory`——**不進庫存**，等於
跟 `items.py`（★2）是兩套物品概念。這一支把「賣什麼」變成**一張表**，買到＝**真的進背包**：

```json
{"kind": "shop",
 "rows": [{"id": "heal_pack", "price": 3, "currency": "star_coins", "note": "…"}]}
```

| 這張桌怎麼賣 | 走哪條路 | 買到什麼 |
|---|---|---|
| `config.tower.shop` **有寫**（legacy 形狀 `{名: {cost, heal, memory}}`） | **legacy 路**（一字不變） | 照舊：貨幣 −cost、`heal`／`memory` 生效 |
| config 沒寫、**有 `shop` 表** | **表路**（新） | 貨幣 −price ＋ **`item_add` ⇒ 進 `inventory`（★2）** |
| 兩者都沒有 | 當場叫 | — |

**config 一律贏**（一個旋鈕一個權威）：config 有寫就全走 config，表只在「config 沒寫」時補缺口——
所以 legacy 桌的行為**逐字不變**。

其他紀律：
* 商店表賣的是**道具表的 id** ⇒ 載入時就檢查「這張桌有沒有道具表、賣的東西在不在道具表裡」（沒開
  規則書的桌＝**載入時就叫**，不是等玩家按下去才炸）。
* `currency` 目前**只認 `star_coins`**（貨幣）；寫別的就是當場叫（誠實，不做半套）。
* 庫存（`stock`）**還沒做**——每次都能買，數量不限。
* 買賣只發事件（`shop_buy`＋`item_changed`），state 由事件折出（`op=replay` 一致）。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

#: 商店表在包裡的形狀（`{"kind": "shop", "rows": [...]}`）
TABLE_KIND = "shop"

#: 一列認得的欄位（`stock` 目前**不支援**——寫了會當場叫，免得以為有效）
#: `nodes`（選填）：這一列只在這些節點賣；**不寫＝所有有店的節點都賣**。同一個 id 可以在不同
#: 節點寫不同價格（那是「不同城市不同價」的正當用法）——但**同一節點下同 id 只能有一列**。
#: ★ 2026-10-20 新增（賣星的商品）：`star`（買到給幾星）／`star_channel`（哪條渠道，預設 shop）／
#: `price_step`（每買一次漲多少價——「第一顆 15、第二顆 25」就是這樣寫的）；
#: `name_jp`／`name_cn` 是給**不在道具表裡**的貨品（賣星不是道具）用的顯示名。
ROW_KEYS = ("id", "price", "currency", "note", "nodes", "star", "star_channel", "price_step",
            "price_step_mode", "name_jp", "name_cn")

#: `price_step` 的遞增基準（**各模組自己定**）：
#: * `star`（預設）＝按「這條渠道已買幾顆星」——賣星商品天生就是「第一顆 15、第二顆 25」；
#: * `purchase`＝按「這個席位買過這個商品幾次」——`star:2` 的商品第二次買只漲一格。
PRICE_STEP_MODES = ("star", "purchase")

#: 目前認得的貨幣（只認貨幣）
CURRENCIES = ("star_coins",)


class ShopError(ValueError):
    """商店表／買賣不合法（載入或呼叫時就叫，不靜默）。"""


def validate(rows: Any, allowed_currencies: Any = None) -> List[str]:
    """表內容合不合法（作者工具與 `test_shop` 共用）。

    `allowed_currencies`：這張桌認得的幣（預設只認 `star_coins`）。桌內可以把槽位
    `coin` 的 key（例 `gold`）一起傳進來——**表是資料，幣名由模組的槽位宣告決定**。
    """
    _ok_cur = tuple(allowed_currencies or CURRENCIES)
    e: List[str] = []
    if not isinstance(rows, list):
        return ["rows 要是陣列"]
    # ⚠ 這裡**不檢查 id 重複**：按節點賣貨時「同一個 id 在不同店不同價」是正常寫法——
    #   「同一節點下同 id 只能一條」由 `load()` 的衝突檢查咬（它才知道 `nodes` 有沒有交集）。
    for i, row in enumerate(rows):
        where = f"rows[{i}]"
        if not isinstance(row, dict):
            e.append(f"{where}: 要是物件")
            continue
        iid = str(row.get("id") or "").strip()
        where = f"rows[{i}]({iid or '?'})"
        if not iid:
            e.append(f"{where}: 每個貨品要有 id（＝**道具表**的道具 id；賣星的商品例外，自己寫 name_*）")
        _pr = row.get("price", 0)              # ⚠ 省略＝0（以前這裡第二段用 -1 當預設 ⇒ 沒寫 price 就被誤判）
        if not isinstance(_pr, int) or _pr < 0:
            e.append(f"{where}.price: 要是 >=0 的整數（省略＝0）")
        cur = row.get("currency")
        if cur is not None and str(cur) not in _ok_cur:
            e.append(f"{where}.currency: 只認 {'／'.join(_ok_cur)}（寫的是 {cur!r}）")
        if row.get("note") is not None and not isinstance(row["note"], str):
            e.append(f"{where}.note: 要是字串")
        for k in ("star", "price_step"):
            if row.get(k) is not None and (not isinstance(row[k], int) or row[k] < 0):
                e.append(f"{where}.{k}: 要是 >=0 的整數（省略＝0）")
        for k in ("star_channel", "price_step_mode", "name_jp", "name_cn"):
            if row.get(k) is not None and not isinstance(row[k], str):
                e.append(f"{where}.{k}: 要是字串")
        _mode = row.get("price_step_mode")
        if _mode is not None and str(_mode) not in PRICE_STEP_MODES:
            e.append(f"{where}.price_step_mode: 只認 {'／'.join(PRICE_STEP_MODES)}（寫的是 {_mode!r}）")
        nd = row.get("nodes")
        if nd is not None and (not isinstance(nd, list) or any(not isinstance(x, str) or not x for x in nd)):
            e.append(f"{where}.nodes: 要是節點 id 的字串陣列（省略＝所有有店的節點都賣）")
        for k in row:
            if k not in ROW_KEYS:
                e.append(f"{where}: 不認識的欄位 {k!r}（只有 {'／'.join(ROW_KEYS)}"
                         f"；⚠ `stock` 還沒做，別寫）")
    return e


def load(paths: Any, allowed_currencies: Any = None) -> List[Dict[str, Any]]:
    """讀 `packs.tables` 裡所有 `kind=shop` 的表（**保持順序**；按節點賣貨靠每一列的 `nodes`）。

    ⚠ 同一個 id 可以出現在不同節點（不同價），但**同一節點下同 id 只能有一列**——不然結帳時不知道
    該算哪個價（載入時就叫）。
    """
    out: List[Dict[str, Any]] = []
    for p in (paths or []):
        if not p or not os.path.exists(p):
            continue
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or data.get("kind") != TABLE_KIND:
            continue
        rows = data.get("rows") or []
        errs = validate(rows, allowed_currencies)
        if errs:
            raise ShopError(f"{os.path.basename(p)}: " + "；".join(errs[:8]))
        for row in rows:
            out.append(dict(row))
    # 同一節點下同 id 唯一（`nodes` 有交集就算衝突）
    for i, a in enumerate(out):
        for b in out[i + 1:]:
            if str(a["id"]) != str(b["id"]) or a == b:
                continue
            na, nb = a.get("nodes"), b.get("nodes")
            if na is None or nb is None:
                raise ShopError(f"貨品 {a['id']!r} 有兩列會同時在同一个節點生效"
                                f"（一列寫了 `nodes`、一列沒寫＝通用）——同一節點下同 id 只能一條")
            if set(na) & set(nb):
                raise ShopError(f"貨品 {a['id']!r} 在節點 {sorted(set(na) & set(nb))} 有兩列（同店同貨只能一條）")
    return out


class ShopMixin:
    """商店機制的實作。**表空＋config 沒寫 ⇒ 沒有商店**（legacy 零變更）。"""

    # ------------------------------------------------------------------ 載入
    def shop_init(self, tables: Any = None) -> None:
        # 幣名跟槽位走：這張桌宣告 `coin=gold` 時，`shop.json` 的 `currency` 可以寫 gold。
        _coin = (self.slot("coin") if hasattr(self, "slot") else "star_coins") or "star_coins"
        self.shop_rows: List[Dict[str, Any]] = load(tables, (_coin,) + CURRENCIES)
        if not self.shop_rows:
            return
        # 商店賣的是道具 ⇒ 這張桌必須有道具表（載入時就叫：別等玩家按下去才炸）
        item_tbl = getattr(self, "item_table", None) or {}
        if not item_tbl:
            raise ShopError("有商店表（shop）但這張桌沒有道具表——商店表賣的是道具，"
                            "請把帶道具表的包（例：rules.core）一起列進 `config.packs.enabled`")
        # ★ 2026-10-20：**賣星的商品**（`star`）不是道具（買到不進 inventory）⇒ 不要求在道具表裡
        missing = sorted({str(r["id"]) for r in self.shop_rows if not r.get("star")} - set(item_tbl))
        if missing:
            raise ShopError(f"商店表賣的道具不存在：{missing}"
                            f"（道具表有的是 {sorted(item_tbl)}）")

    # ------------------------------------------------------------------ 讀出口
    def _star_room(self, row: Dict[str, Any], seat: Optional[str] = None) -> int:
        """賣星商品**還能買幾顆**（渠道餘量 ∧ 總星等餘量）；不是賣星商品 ⇒ -1（不適用）。

        用途有兩個：`shop_view` 標 `sold_out`、`shop_buy` 在**扣錢之前**擋——不然玩家會花了錢
        卻只拿到 `capped`（S5f 那個坑：錢照扣、星不給）。

        ★ 2026-10-20：以前只看**渠道** `max`，沒看總上限 `stars_max`。實測：★5 滿級、shop 渠道
        還有 2 格 ⇒ 商品照賣、錢照扣、`star_grant` 回 `star_cap` 而 0 星。總上限也要算進來。
        """
        if not row.get("star"):
            return -1
        ch = str(row.get("star_channel") or "shop")
        spec = (getattr(self, "star_channels", None) or {}).get(ch) or {}
        mx = int(spec.get("max") or 0)
        done = int(((self.state.get("star_ch") or {}).get(str(seat or "")) or {}).get(ch) or 0)
        room_ch = max(0, mx - done)
        try:
            room_all = max(0, int(self.stars_max()) - int(self.star_of(seat or "")))
        except Exception:                                                     # noqa: BLE001
            room_all = 0                    # 沒有星等表／讀不到 ⇒ 不賣（不要在扣錢後才 capped）
        return min(room_ch, room_all)

    def _price_of(self, row: Dict[str, Any], seat: Optional[str] = None) -> int:
        """表路一列的**實價**：`price` 經難度倍率；有 `price_step` ⇒ 再按「這個席位已經買過幾次」遞增。

        沒有 `price_step` ⇒ 就是原本的 `price`（legacy 一字不變）。遞增基準由模組定
        （`price_step_mode`）：`star`（預設）＝該商品的 `star_channel` 渠道已買星數；
        `purchase`＝這個席位買過這個商品的次數（從事件流數，回放一致）。
        """
        base = int(row.get("price") or 0)
        step = int(row.get("price_step") or 0)
        n = 0
        if step and seat:
            if str(row.get("price_step_mode") or "star") == "purchase":
                iid = str(row.get("id") or "")
                n = sum(1 for e in (getattr(self, "events", None) or [])
                        if str(e.get("type")) == "shop_buy"
                        and str(e.get("seat")) == str(seat)
                        and str(e.get("item")) == iid)
            else:
                ch = str(row.get("star_channel") or "shop")
                try:
                    n = int(((self.state.get("star_ch") or {}).get(str(seat)) or {}).get(ch) or 0)
                except Exception:                                         # noqa: BLE001
                    n = 0
        return self._price_mod(base + step * max(0, n))

    def _price_mod(self, p: Any) -> int:
        """難度價倍率（`config.difficulty_mods.price_mult`；沒設＝1.0 ⇒ legacy 一字不變）。"""
        mul = float(((getattr(self, "cfg", {}) or {}).get("difficulty_mods") or {}).get("price_mult") or 1.0)
        return max(0, int(round(float(p or 0) * mul)))

    def shop_offers(self, node_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """這一節點買得到哪幾列：`nodes` 省略＝通用（每間店都有）；寫了＝只在那幾個節點。"""
        rows = getattr(self, "shop_rows", None) or []
        if node_id is None:
            return [r for r in rows]
        return [r for r in rows
                if r.get("nodes") is None or str(node_id) in [str(x) for x in r["nodes"]]]

    def shop_ids(self, node_id: Optional[str] = None) -> List[str]:
        return sorted({str(r["id"]) for r in self.shop_offers(node_id)})

    def shop_source(self) -> str:
        """這張桌的商店從哪來（診斷用）：`both`／`table`（道具，進庫存）／`config`（legacy）／`none`。

        ⚠ 不是「二選一」：**道具表優先**（id 在道具表裡＝走表路），其餘貨品走 config 的 legacy 路。
        """
        t = bool(getattr(self, "shop_rows", None) or [])
        c = bool(self.tower_cfg.get("shop") or {})
        return "both" if (t and c) else ("table" if t else ("config" if c else "none"))

    def shop_view(self, *, seat: Optional[str] = None,
                  node_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """給投影用：這一節點買得到的東西＝**表路的道具** ∪ **legacy 路（config／包 defaults 的貨品）**。

        每一列：`item`／`price`／`currency`／`affordable`／`legacy`（legacy＝舊式「買了直接生效」，不進庫存）；
        道具那邊多帶 `name_jp`／`name_cn`／`item_note`（名字從道具表來）。
        """
        offers = self.shop_offers(node_id)
        cfg_shop = self.tower_cfg.get("shop") or {}
        if not offers and not cfg_shop:
            return []
        coins = int(((self.state.get("seats") or {}).get(seat or "") or {}).get(self.slot("coin")) or 0)
        out: List[Dict[str, Any]] = []
        for row in offers:
            iid = str(row["id"])
            item = (getattr(self, "item_table", None) or {}).get(iid) or {}
            price = self._price_of(row, seat)
            _room = self._star_room(row, seat)
            _need = int(row.get("star") or 0)
            out.append({
                "item": iid, "price": price,
                "currency": str(row.get("currency") or self.slot("coin")),
                "affordable": coins >= price, "legacy": False,
                # 買一顆至少要 1 格；賣 2 星的商品餘量 < 2 也不賣（不能只成交一半）
                "sold_out": bool(_room >= 0 and _room < max(1, _need)),
                "name_jp": row.get("name_jp") or item.get("name_jp") or iid,
                "name_cn": row.get("name_cn") or item.get("name_cn") or item.get("name_jp") or iid,
                "item_note": item.get("note"),
                "note": row.get("note"),
            })
        for iid, spec in sorted(cfg_shop.items()):
            spec = spec or {}
            price = self._price_mod(spec.get("cost", 0))
            _room = (self._star_room({"star": int(spec.get("star") or 0),
                                      "star_channel": spec.get("star_channel")}, seat)
                     if spec.get("star") else -1)
            out.append({
                "item": iid, "price": price, "currency": self.slot("coin"),
                "affordable": coins >= price, "legacy": True,
                "sold_out": bool(_room >= 0 and _room < int(spec.get("star") or 1)),
                "name_jp": iid, "name_cn": iid,
                "item_note": None,
                "note": ("star +%s" % spec["star"]) if spec.get("star")
                        else (("heal %s" % spec["heal"]) if spec.get("heal")
                              else (("memory %s" % spec["memory"]) if spec.get("memory") else None)),
            })
        return out

    # ------------------------------------------------------------------ 買
    def shop_buy(self, *, seat: str, item: str, node_id: Optional[str] = None,
                 note: Optional[str] = None) -> Dict[str, Any]:
        """買一件：**config.tower.shop 有寫就全走 config（legacy 一字不變）**；否則走表（進庫存）。"""
        with self.lock:
            from trpg.core.errors import RuleError                                   # noqa: PLC0415
            s = (self._require_seat(seat) if hasattr(self, "_require_seat")
                 else self.state["seats"].get(seat))
            if not s:
                raise RuleError(f"沒有這個席位：{seat}")
            iid = str(item or "").strip()
            offers = self.shop_offers(node_id)
            by_id = {str(r["id"]): r for r in offers}
            cfg_shop = self.tower_cfg.get("shop") or {}
            # **道具表優先**：id 在道具表裡 ⇒ 這是一件「可持有的東西」⇒ 走表路（買到進 inventory）。
            # 不在道具表裡的貨品（例 coffee／bento 這種「當場吃掉」的）⇒ 照舊走 config 的 legacy 路。
            if iid not in by_id:
                if iid not in cfg_shop:
                    avail = sorted(set(by_id) | set(cfg_shop))
                    raise RuleError(f"沒有這個商品：{iid or '（空）'}（這家店的可選：{avail}）")
                spec = cfg_shop[iid] or {}
                cost = self._price_mod(spec.get("cost", 0))
                if int(s.get(self.slot("coin"), 0)) < cost:
                    raise RuleError(self.phrase("shop.err.coins_short", have=s.get(self.slot("coin"), 0), cost=cost))
                # ⚠ 賣星的商品：**扣錢之前**先驗渠道／餘量（含總星等上限）。以前這段在
                #   `shop_buy` 事件之後才由 `star_grant` 炸 ⇒ 錢照扣、只留一筆已成交事件。
                _star_n = int(spec.get("star") or 0)
                _ch = str(spec.get("star_channel") or "shop")
                if _star_n:
                    if _ch not in (getattr(self, "star_channels", None) or {}):
                        raise RuleError(f"這件商品要給星（渠道 {_ch}），但這張桌沒有這個星等渠道"
                                        f"（有：{'／'.join((getattr(self, 'star_channels', None) or {})) or '無'}）")
                    _room = self._star_room({"star": _star_n, "star_channel": _ch}, seat)
                    if _room < _star_n:
                        raise RuleError("這件商品不賣了：這條路的星已經買滿（渠道上限／總星等上限）")
                self._emit("shop_buy", public=True, seat=seat, node=node_id, item=iid,
                           cost=cost, heal=int(spec.get("heal", 0)))
                out: Dict[str, Any] = {"bought": iid, "cost": cost, "via": "config"}
                # ★ 2026-10-20：商品可以賣「星」——`{"cost": 15, "star": 1}` ⇒ 買到即給該渠道 +1 星
                #   （渠道名預設 `shop`，商品可寫 `star_channel` 指到劇本宣告的其他渠道）
                if _star_n:
                    _g: Any = None
                    try:
                        _g = self.star_grant(seat=seat, channel=_ch,
                                             value=_star_n, by="shop", note=iid)
                    except Exception as exc:                                      # noqa: BLE001
                        raise RuleError(f"這件商品要給星（渠道 {_ch}），但星等機制不支援：{exc}")
                    out["star"] = _g
                if spec.get("memory"):
                    self._memory_delta(seat, int(spec["memory"]),
                                       self.phrase("memory.item_effect", item=iid))
                return out
            if not offers and not cfg_shop:
                raise RuleError("這張桌沒有商店貨品（`config.tower.shop`／包 defaults 沒寫、也沒有 shop 表）")
            row = by_id[iid]
            cur = str(row.get("currency") or self.slot("coin"))
            if cur not in (self.slot("coin"),) + CURRENCIES:
                raise RuleError(f"商店只認 {'／'.join(CURRENCIES)}（這一列寫的是 {cur!r}）")
            price = self._price_of(row, seat)
            if int(s.get(self.slot("coin"), 0)) < price:
                raise RuleError(self.phrase("shop.err.coins_short_table", have=s.get(self.slot("coin"), 0),
                                            cost=price))
            # ⚠ 賣星的商品：**扣錢之前**就先擋（渠道／總星等滿了不賣），否則玩家花了錢只拿到 capped
            _need = int(row.get("star") or 0)
            if _need:
                _ch = str(row.get("star_channel") or "shop")
                if _ch not in (getattr(self, "star_channels", None) or {}):
                    raise RuleError(f"這件商品要給星（渠道 {_ch}），但這張桌沒有這個星等渠道")
                if self._star_room(row, seat) < _need:
                    raise RuleError("這件商品不賣了：這條路的星已經買滿（渠道上限／總星等上限）")
            # 先發 `shop_buy`（reducer 扣貨幣）→ 再 `item_add`（進 inventory，★2）
            self._emit("shop_buy", public=True, seat=seat, node=node_id, item=iid,
                       cost=price, heal=0, note=note)
            # ★ 2026-10-20：**賣星的商品**（`star`）不進庫存——買到即給該渠道（渠道名 `star_channel`，
            #   預設 `shop`）。價格可用 `price_step` 逐次遞增（第一顆 15、第二顆 25 就是這樣寫的）。
            if row.get("star"):
                _ch = str(row.get("star_channel") or "shop")
                _g = self.star_grant(seat=seat, channel=_ch, value=int(row["star"]),
                                     by="shop", note=iid)
                return {"bought": iid, "cost": price, "via": "table", "star": _g}
            res = self.item_add(target=seat, id=iid, count=1, note=f"buy:{iid}", by="shop")
            return {"bought": iid, "cost": price, "via": "table", "count": res.get("count")}
