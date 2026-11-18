#!/usr/bin/env python3
"""L6 資產層：**模組級素材表**（`modules/<模組>/assets/`）。

一句話：**一個模組一份素材表**——`hyakki_tower` 的圖不會漏給任何別的模組
（桌子只認自己那張模組的 `assets/index.json`；沒那個檔＝這一桌沒有圖，投影一格都不加）。

正本＝`<模組>/assets/index.json`（生成物，由模組自己的素材腳本產出）：

    {"schema": 1, "module": "hyakki_tower",
     "files":   {"portrait/amber/a02": {"path": "portrait/amber/a02.webp", "kind": "portrait", ...}},
     "aliases": {"portrait:char_a": "portrait/amber/a02", "bg:teckan": "bg/teckan", ...}}

設計取捨（照本專案既有紀律）：
* **別名制**：投影只查別名（`portrait:<角色 id>`／`icon:item:<道具 id>`…），
  **不猜檔名、不掃目錄**——素材換檔（WebP 重轉、改名）不改任何投影程式。
* **缺＝沒有**：查不到別名 ⇒ 回 `None`，投影那格**不加**（legacy／沒素材的模組逐字不變）。
* **對外路徑與桌 id 解耦**：`/assets/<模組>/<相對路徑>`——同一模組的多張桌、
  以及觀戰頁（`/<桌>/watch/`）共用同一批圖，不必在每個桌前綴下各放一份。
* **模組 id 白名單**（`[A-Za-z0-9][A-Za-z0-9_.-]*`）＋路徑不得穿越 ⇒ 這條靜態路由
  不能被拿去讀 `assets/` 以外的任何檔（唯一例外＝模組根的 `locale.json`，見下）。

**畫面用語表**（2026-10-19）：`<模組>/locale.json`＝這一部作品的**固有名詞與世界觀用語**
（地名譯名、派閥／役割／神器名、「小怪」「貨幣」這類造語）。前端不再內建任何作品的語彙，
一律從這裡讀 ⇒ 對外 `/assets/<模組>/locale.json`（與圖同一條路由、同樣不認身分）。
它住模組根、不住 `assets/`：`assets/` 底下是生成物（不進版控），用語表是作者手寫的正本。
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

URL_PREFIX = "/assets"                       # 對外前綴（模組級）
DIRNAME = "assets"                           # `<模組>/assets/`
INDEX_NAME = "index.json"
LOCALE_NAME = "locale.json"                  # 畫面用語表（住模組根，不在 assets/ 底下）
AUDIO_NAME = "audio.json"                    # 音訊表（住模組根；見 `service/audio.py`）
INDEX_PART_DIR = "index"                     # 清單按類拆分後的 parts（`assets/index/<類>.json`）
#: `index/<類>.json`——只准小寫英數與底線（路徑分隔只有一個 `/`，沒有穿越空間）
INDEX_PART_RE = re.compile(r"^index/[a-z][a-z0-9_]{0,31}\.json$")
_MOD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
# 靜態面只放這幾種圖（擋 `.py`／`.json` 以外的任何東西被當圖片送出去）
ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".gif": "image/gif", ".svg": "image/svg+xml",
        ".json": "application/json"}

#: **音訊**（2026-10-20：BGM／效果音與圖同一條模組級靜態路由）——音檔放 `<模組>/assets/audio/…`，
#: 表是 `<模組>/audio.json`（作者手寫，見 `service/audio.py`）。音訊要過 `Range`（手機 Safari
#: 沒 206 就不播、也不能 seek），所以靜態面另外處理（`web/base.py › asset_response`）。
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wav", ".flac"}
ALLOWED_EXT |= AUDIO_EXT
MIME.update({".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac",
             ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg",
             ".wav": "audio/wav", ".flac": "audio/flac"})


def mime_of(path: str) -> str:
    return MIME.get(os.path.splitext(path)[1].lower(), "application/octet-stream")


def module_ok(module: str) -> bool:
    return bool(_MOD_RE.match(str(module or "")))


def safe_rel(rel: str) -> str:
    """把對外給的相對路徑收成安全值；不合規一律回空字串（呼叫端當 404）。

    擋：絕對路徑、`..` 穿越、`.` 段、空段、反斜線、非白名單副檔名。
    例外＝`index.json`（**素材清單**本身要能給前端讀；它不是圖，所以另外放行）
    與 `locale.json`（**畫面用語表**），以及 `index/<類>.json`（2026-10-19：清單**按類拆分**後的
    每一份 part——前端拿 `index.json` 的 `parts` 再逐份抓；名字只准小寫英數與 `_`）。
    """
    rel = str(rel or "").strip()
    if "\\" in rel:
        return ""
    if not rel or rel.startswith("/") or rel.endswith("/"):
        return ""
    if rel in (INDEX_NAME, LOCALE_NAME, AUDIO_NAME):
        return rel
    if INDEX_PART_RE.match(rel):
        return rel
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return ""
    if os.path.splitext(parts[-1])[1].lower() not in ALLOWED_EXT:
        return ""
    return "/".join(parts)


def assets_dir(root: str, module: str) -> str:
    """`<repo>/modules/<模組>/assets`（模組 id 不合規 ⇒ 空字串）。"""
    if not root or not module_ok(module):
        return ""
    return os.path.join(os.path.abspath(root), "modules", str(module), DIRNAME)


def locale_path(root: str, module: str) -> str:
    """`<repo>/modules/<模組>/locale.json`（模組 id 不合規 ⇒ 空字串；不檢查存在）。"""
    if not root or not module_ok(module):
        return ""
    return os.path.join(os.path.abspath(root), "modules", str(module), LOCALE_NAME)


def audio_lib_path(root: str, module: str) -> str:
    """`<repo>/modules/<模組>/audio.json`（**音訊表**；模組 id 不合規 ⇒ 空字串；不檢查存在）。

    與 `locale.json` 同性質：作者手寫的正本住模組根，不住生成物的 `assets/`。
    """
    if not root or not module_ok(module):
        return ""
    return os.path.join(os.path.abspath(root), "modules", str(module), AUDIO_NAME)


def resolve_file(root: str, module: str, rel: str) -> Optional[str]:
    """對外相對路徑 ⇒ 本機絕對路徑（**必須真的落在該模組的 assets 目錄內**；`locale.json` 例外＝模組根那一份）。"""
    safe = safe_rel(rel)
    base = assets_dir(root, module)
    if not safe or not base:
        return None
    if safe == LOCALE_NAME:
        lp = locale_path(root, module)
        return lp if lp and os.path.isfile(lp) else None
    if safe == AUDIO_NAME:
        ap = audio_lib_path(root, module)
        return ap if ap and os.path.isfile(ap) else None
    p = os.path.realpath(os.path.join(base, safe))
    if not p.startswith(os.path.realpath(base) + os.sep):
        return None
    return p if os.path.isfile(p) else None


class Assets:
    """一個模組的素材表（`index.json` 的 mtime 快取；缺檔＝空的表＝什麼都不給）。"""

    def __init__(self, root: str = "", module: str = ""):
        self.root = os.path.abspath(root) if root else ""
        self.module = str(module or "")
        self.dir = assets_dir(self.root, self.module) if root else ""
        self._mtime: Optional[Any] = None      # 快取戳：float（單檔）或 tuple（index ＋ parts 的 mtime）
        self._files: Dict[str, Any] = {}
        self._aliases: Dict[str, str] = {}

    # -- 讀 ------------------------------------------------------------------
    def _flatten(self, d: dict) -> tuple:
        """`index.json` ⇒（files, aliases）。兩種形狀都吃：

        * **schema 1**：單檔清單，`files`／`aliases` 就在裡面（舊模組）。
        * **schema 2**（2026-10-19.2）：瘦清單＋`parts`（`index/<類>.json`，每份是 schema 1 形狀）
          ⇒ 逐份讀回來合併。**part 壞掉只少那一類**，不讓整桌沒圖。
        """
        if int(d.get("schema") or 1) < 2 or "parts" not in d:
            return (dict(d.get("files") or {}),
                    {str(k): str(v) for k, v in (d.get("aliases") or {}).items()})
        files: Dict[str, Any] = {}
        aliases: Dict[str, str] = {}
        for rel in (d.get("parts") or {}).values():
            safe = safe_rel(str(rel))
            if not safe:
                continue
            try:
                with open(os.path.join(self.dir, *safe.split("/")), "r", encoding="utf-8") as fh:
                    part = json.load(fh)
            except Exception:                                         # noqa: BLE001
                continue
            files.update(part.get("files") or {})
            aliases.update({str(k): str(v) for k, v in (part.get("aliases") or {}).items()})
        return files, aliases

    def _refresh(self) -> None:
        p = os.path.join(self.dir, INDEX_NAME) if self.dir else ""
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
        except OSError:
            if self._mtime is not None:
                self._mtime, self._files, self._aliases = None, {}, {}
            return
        except Exception:                                             # noqa: BLE001
            self._mtime, self._files, self._aliases = None, {}, {}
            return
        # 快取戳＝index.json ＋ 每一份 part 的 mtime（拆開後改一份 part 不會動 index.json）
        stamp = [self._mtime_of(p)]
        for rel in (d.get("parts") or {}).values() if int(d.get("schema") or 1) >= 2 else []:
            safe = safe_rel(str(rel))
            if safe:
                stamp.append(self._mtime_of(os.path.join(self.dir, *safe.split("/"))))
        stamp = tuple(stamp)
        if self._mtime == stamp:
            return
        try:
            self._files, self._aliases = self._flatten(d)
            self._mtime = stamp
        except Exception:                                             # noqa: BLE001
            self._mtime, self._files, self._aliases = stamp, {}, {}     # 壞檔＝空的（不炸桌）

    @staticmethod
    def _mtime_of(path: str) -> float:
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0.0

    @property
    def ok(self) -> bool:
        """這一桌有沒有素材（＝index.json 讀得動且非空）。"""
        self._refresh()
        return bool(self._files)

    def files(self) -> Dict[str, Any]:
        self._refresh()
        return self._files

    def aliases(self) -> Dict[str, str]:
        self._refresh()
        return self._aliases

    # -- 查 ------------------------------------------------------------------
    def key_of(self, alias: str) -> Optional[str]:
        """別名 ⇒ 檔案 key（也接受直接給 key 或相對路徑）。"""
        self._refresh()
        a = str(alias or "")
        if not a:
            return None
        if a in self._aliases:
            return self._aliases[a]
        if a in self._files:
            return a
        for k, v in self._files.items():                 # 允許直接寫相對路徑
            if str(v.get("path") or "") == a:
                return k
        return None

    def url(self, alias: str) -> Optional[str]:
        """別名 ⇒ **對外 URL**（`/assets/<模組>/<相對路徑>`）；查不到 ⇒ None。"""
        k = self.key_of(alias)
        if not k:
            return None
        rel = str((self._files.get(k) or {}).get("path") or "")
        return url_of(self.module, rel) if rel else None

    def urls(self, prefix: str) -> List[str]:
        """某一類（`portrait/`／`crowd/`…）的全部 URL（照 key 排序）。"""
        a = str(prefix or "")
        out = []
        for k in sorted(self.files()):
            if k.startswith(a):
                u = self.url(k)
                if u:
                    out.append(u)
        return out

    def locale_url(self) -> Optional[str]:
        """這一模組的**畫面用語表**對外 URL（帶 `?v=<mtime>`：圖是長快取，用語表改了要立刻換）；沒有 ⇒ None。"""
        lp = locale_path(self.root, self.module) if self.root else ""
        try:
            mt = int(os.path.getmtime(lp)) if lp else 0
        except OSError:
            return None
        return f"{URL_PREFIX}/{self.module}/{LOCALE_NAME}?v={mt}" if mt else None

    def manifest(self) -> Dict[str, Any]:
        """給前端的一格（相對路徑用；沒素材也沒用語表 ⇒ 空 dict ⇒ 那格不加）。

        `base`／`index`＝圖庫（只有真的有素材表才給）；`locale`＝畫面用語表（只有模組放了 `locale.json` 才給）。
        """
        out: Dict[str, Any] = {}
        if self.ok:
            out = {"module": self.module,
                   "base": f"{URL_PREFIX}/{self.module}/",
                   "index": f"{URL_PREFIX}/{self.module}/{INDEX_NAME}"}
        loc = self.locale_url()
        if loc:
            out.setdefault("module", self.module)
            out["locale"] = loc
        return out

    def health(self) -> Dict[str, Any]:
        return {"module": self.module, "present": bool(self.ok),
                "files": len(self.files()), "aliases": len(self.aliases()),
                "locale": bool(self.locale_url())}


def url_of(module: str, rel: str) -> Optional[str]:
    safe = safe_rel(rel)
    if not safe or not module_ok(module):
        return None
    return f"{URL_PREFIX}/{module}/{safe}"


EMPTY = Assets()          # 沒有模組／沒有素材時的共用替身（呼叫端不必判 None）


def load(root: str, module: str) -> Assets:
    """組一顆；`module` 或 `root` 缺 ⇒ 回 `EMPTY`（＝投影不會多任何一格）。"""
    if not root or not module_ok(module):
        return EMPTY
    return Assets(root, module)
