#!/usr/bin/env python3
"""L6 音訊層：**模組級音訊表**（`modules/<模組>/audio.json`）。

一句話：**一個模組一份音訊表**——`hyakki_tower` 的 BGM 不會漏給任何別的模組
（桌子只認自己那張模組的 `audio.json`；沒那個檔＝這一桌沒有聲音，投影一格都不加）。

為什麼和圖分開一個檔（2026-10-20 設計者定：「資源綁在模組上，引擎只負責播放控制」）：

* **圖庫是生成物**（`assets/index.json` ＋ `index/<類>.json`，數百張、由出圖管線產生）；
  **音訊表是作者手寫的正本**（哪一段音樂叫什麼、哪個事件該放哪一首、音量與循環）——
  和 `<模組>/locale.json`（畫面用語表）同性質：住模組根、進版控、人改。
* 音檔本身住 `<模組>/assets/audio/…`（與圖同一條靜態路由 `/assets/<模組>/…`、
  同一個模組白名單、同一套路徑防穿越）；表裡只寫**相對路徑**。

正本形狀：

    {"schema": 1,
     "tracks": {
       "bgm:town":  {"path": "audio/bgm/town.m4a", "loop": true, "gain": 0.55,
                     "title": {"ja": "ヤマト", "zh_Hans": "苍梧城"}},
       "sfx:thunder": {"path": "audio/sfx/thunder.m4a", "gain": 0.9, "title": "雷鳴"}
     },
     "cues": {"scene_open": "bgm:town", "boss_defeated": "sfx:thunder"}}

* **鍵＝別名**（`bgm:`／`sfx:` 前綴決定聲道）⇒ 換檔、改名只動表，不動引擎與投影；
* **缺＝沒有**：查不到 ⇒ `None`（引擎不發事件、投影不加欄位）；
* **`cues`＝自動提示**（引擎事件型別 → 別名）。引擎只做「什麼時候放、放哪個聲道、
  淡入淡出幾秒」；**選哪一首**是模組（這張表）＋ GM 臨場的事。

壞資料（別名沒前綴／路徑不合規／cue 指到不存在的鍵／聲道對不上）**不炸桌**：
收進 `warnings()`（`/health` 看得到、測試咬得住），壞的那一條當作不存在。
理由：一顆打錯字的音軌不該讓整張桌開不起來——但要**看得見**，不能靜靜吃掉。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from trpg.service import assets as assets

LIB_NAME = "audio.json"                  # 住模組根（與 locale.json 同一層）
CHANNELS = ("bgm", "sfx")
DEFAULT_GAIN = 0.7
DEFAULT_FADE = 0.8                       # 秒；換曲／停曲的淡入淡出（前端照這個值做）


def _norm_kind(alias: str) -> str:
    """別名的前綴就是聲道（`bgm:town` ⇒ `bgm`）；不合規 ⇒ 空字串。"""
    head, _, tail = str(alias or "").partition(":")
    return head if head in CHANNELS and tail else ""


def _norm_bool(value: Any, default: bool) -> bool:
    return default if value is None else bool(value)


def _norm_gain(value: Any) -> float:
    try:
        g = float(value)
    except (TypeError, ValueError):
        return DEFAULT_GAIN
    return min(1.0, max(0.0, g))


def _safe_path(rel: Any) -> str:
    """音檔相對路徑：只收**音訊副檔名**的相對路徑（穿越／絕對路徑／別的副檔名 ⇒ 空字串）。"""
    safe = assets.safe_rel(str(rel or ""))
    if not safe or os.path.splitext(safe)[1].lower() not in assets.AUDIO_EXT:
        return ""
    return safe


class AudioTable:
    """一個模組的音訊表（`audio.json` 的 mtime 快取；缺檔＝空的表＝什麼都不給）。"""

    def __init__(self, root: str = "", module: str = "") -> None:
        self.root = os.path.abspath(root) if root else ""
        self.module = str(module or "")
        self.path = assets.audio_lib_path(self.root, self.module) if root else ""
        self._mtime: Optional[float] = None
        self._tracks: Dict[str, Dict[str, Any]] = {}
        self._cues: Dict[str, str] = {}
        self._warnings: List[str] = []

    # -- 讀 ------------------------------------------------------------------
    def _refresh(self) -> None:
        p = self.path
        try:
            mt = os.path.getmtime(p) if p else 0.0
        except OSError:
            mt = 0.0
        if mt == self._mtime:
            return
        self._mtime = mt
        if not mt:
            self._tracks, self._cues, self._warnings = {}, {}, []
            return
        try:
            with open(p, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except Exception:                                             # noqa: BLE001
            # 壞檔＝空的表（不炸桌），但留一條警告——不然「怎麼都沒聲音」查不到原因
            self._tracks, self._cues = {}, {}
            self._warnings = [f"{LIB_NAME} 讀不動（JSON 壞掉？）"]
            return
        self._tracks, self._cues, self._warnings = self._parse(doc)

    def _parse(self, doc: Any) -> "tuple[Dict[str, Dict[str, Any]], Dict[str, str], List[str]]":
        warn: List[str] = []
        tracks: Dict[str, Dict[str, Any]] = {}
        raw = (doc.get("tracks") if isinstance(doc, dict) else None) or {}
        if not isinstance(raw, dict):
            return {}, {}, [f"{LIB_NAME}: tracks 要是物件（別名 → 音軌）"]
        for key, row in raw.items():
            key = str(key or "")
            kind = _norm_kind(key)
            if not kind:
                warn.append(f"別名要 bgm:／sfx: 前綴：{key!r}")
                continue
            if not isinstance(row, dict):
                warn.append(f"{key}: 音軌要是物件（path／loop／gain／title）")
                continue
            rel = _safe_path(row.get("path"))
            if not rel:
                warn.append(f"{key}: path 不是合規的音檔相對路徑（{row.get('path')!r}）")
                continue
            row_kind = str(row.get("kind") or kind)
            if row_kind != kind:
                warn.append(f"{key}: kind={row_kind!r} 與別名前綴 {kind!r} 不一致（以前綴為準）")
            tracks[key] = {
                "key": key, "kind": kind, "path": rel,
                "loop": _norm_bool(row.get("loop"), kind == "bgm"),
                "gain": _norm_gain(row.get("gain")),
                "title": row.get("title"),
            }
        cues: Dict[str, str] = {}
        raw_cues = (doc.get("cues") if isinstance(doc, dict) else None) or {}
        if not isinstance(raw_cues, dict):
            warn.append(f"{LIB_NAME}: cues 要是物件（事件型別 → 別名）")
            raw_cues = {}
        for etype, key in raw_cues.items():
            key = str(key or "")
            if key not in tracks:
                warn.append(f"cues.{etype}: 指到不存在的音軌 {key!r}")
                continue
            cues[str(etype)] = key
        return tracks, cues, warn

    # -- 查 ------------------------------------------------------------------
    @property
    def ok(self) -> bool:
        """這一桌有沒有音訊表（＝`audio.json` 讀得動且有音軌）。"""
        self._refresh()
        return bool(self._tracks)

    def tracks(self) -> Dict[str, Dict[str, Any]]:
        self._refresh()
        return self._tracks

    def warnings(self) -> List[str]:
        self._refresh()
        return list(self._warnings)

    def track(self, key: str) -> Optional[Dict[str, Any]]:
        """別名 ⇒ 音軌（沒這條／壞資料 ⇒ None）。"""
        self._refresh()
        row = self._tracks.get(str(key or ""))
        return dict(row) if row else None

    def cue(self, etype: str) -> Optional[str]:
        """引擎事件型別 ⇒ 自動提示的別名（沒寫 ⇒ None）。"""
        self._refresh()
        return self._cues.get(str(etype or ""))

    def cues(self) -> Dict[str, str]:
        self._refresh()
        return dict(self._cues)

    def keys(self) -> List[str]:
        self._refresh()
        return sorted(self._tracks)

    def url(self, key: str) -> Optional[str]:
        """別名 ⇒ 對外 URL（`/assets/<模組>/audio/…`）；音檔不在磁碟上 ⇒ None（缺＝沒有）。"""
        row = self.track(key)
        if not row:
            return None
        if not assets.resolve_file(self.root, self.module, row["path"]):
            return None
        return assets.url_of(self.module, row["path"])

    def catalog(self) -> List[Dict[str, Any]]:
        """給 GM 看的可選清單（key／kind／title／loop／gain）；**不含 URL**（GM 不需要）。"""
        out: List[Dict[str, Any]] = []
        for key in self.keys():
            row = self._tracks[key]
            out.append({"key": key, "kind": row["kind"], "title": row.get("title"),
                        "loop": row["loop"], "gain": row["gain"],
                        "ready": bool(self.url(key))})
        return out

    def manifest(self) -> Dict[str, Any]:
        """給前端的一格（只有真的有表才給；`index`＝這張表本身，前端可拿它預載）。"""
        if not self.ok:
            return {}
        p = self.path
        try:
            mt = int(os.path.getmtime(p)) if p else 0
        except OSError:
            mt = 0
        return {"module": self.module,
                "base": f"{assets.URL_PREFIX}/{self.module}/",
                "index": f"{assets.URL_PREFIX}/{self.module}/{LIB_NAME}?v={mt}"}

    def health(self) -> Dict[str, Any]:
        self._refresh()
        return {"module": self.module, "present": bool(self._tracks),
                "tracks": len(self._tracks), "cues": len(self._cues),
                "warnings": list(self._warnings)}


EMPTY = AudioTable()          # 沒有模組／沒有音訊表時的共用替身（呼叫端不必判 None）


def load(root: str, module: str) -> AudioTable:
    """組一顆；`module` 或 `root` 缺 ⇒ 回 `EMPTY`（＝投影不會多任何一格、引擎不發任何音訊事件）。"""
    if not root or not assets.module_ok(module):
        return EMPTY
    return AudioTable(root, module)
