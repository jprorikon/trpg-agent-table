#!/usr/bin/env python3
"""**L6 持久化適配器**——事件流落盤（第 3 刀：把檔案 IO 從 L0 內核搬出來）。

內核（`engine.Table`）只認一組 port（四個方法，語意固定）：

    load() -> list[dict]              # 載入既有事件（新局回 []）
    append(ev) -> None                # 追加一筆（append-only）
    size() -> int                     # 目前位元組長度＝尾行覆寫用的游標
    replace_tail(offset, ev) -> bool  # append-only 的唯一例外：就地把尾行改成 ev

`engine.MemoryStore`（內核的預設，純記憶體）與這裡的 `FileStore` **語意必須一致**；
`test_layers.py` 的 D 組拿同一組斷言跑兩顆 sink。

L6 紀律：這一層只是**載體**——不裁判、不投影、不產人話，也不 import 專案裡的其它層。
"""
from __future__ import annotations

import json
import os
import sys

RUNS_DIR = "runs"
CURRENT = "current"        # `runs/current`：一行文字＝現役那一局的目錄名（＝session）


def runs_dir(home: str) -> str:
    """`<home>/runs/`——**一局一個子目錄**（WS-B 階段 1c）。"""
    return os.path.join(home, RUNS_DIR)


def list_runs(home: str) -> "list[str]":
    """`runs/` 底下的局（目錄名＝session），**新 → 舊**（比 mtime，同分比名字）。"""
    d = runs_dir(home)
    if not os.path.isdir(d):
        return []
    rows = [(os.path.getmtime(os.path.join(d, n)), n) for n in os.listdir(d)
            if os.path.isdir(os.path.join(d, n))]
    rows.sort(reverse=True)
    return [n for _t, n in rows]


def current_run(home: str) -> "str | None":
    """**現役那一局**：`runs/current` 指到誰就是誰；指標不見／指到不存在的 ⇒ 取最新的那一局。

    （指標是快取：壞掉時退回「最新」照樣活得下去——比「整張桌打不開」好。）
    """
    d = runs_dir(home)
    if not os.path.isdir(d):
        return None
    try:
        with open(os.path.join(d, CURRENT), encoding="utf-8") as fh:
            name = fh.read().strip()
        if name and os.path.isdir(os.path.join(d, name)):
            return name
    except OSError:
        pass
    rows = list_runs(home)
    return rows[0] if rows else None


def set_current_run(home: str, session: str) -> None:
    """把「現役那一局」指到 `session`（`begin_run` 用；也可人手修）。"""
    d = runs_dir(home)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, CURRENT), "w", encoding="utf-8") as fh:
        fh.write(str(session) + "\n")


def events_path(home: str, session: "str | None" = None, *, name: str = "events.jsonl") -> str:
    """這一局的檔：`<home>/runs/<session>/events.jsonl`。（**一局一檔**）

    `session` 省略 ⇒ 現役那一局；沒有 `runs/` 而 `<home>/data/events.jsonl` 在 ⇒ 回舊路徑
    （**舊制相容**：階段 1c 之前建的桌照跑，不必先搬）。
    """
    if session:
        return os.path.join(runs_dir(home), str(session), name)
    cur = current_run(home)
    if cur:
        return os.path.join(runs_dir(home), cur, name)
    return os.path.join(home, "data", name)


def copy_current_run(src_home: str, dst_home: str) -> str:
    """把 `src_home` 的**現役那一局**抄成 `dst_home` 的一局（同一套 `runs/<session>/` 結構）。

    為什麼要有它：臨時桌要用現役桌的流試跑（`deploy/` 的臨時桌、`/tmp` 驗證、測試夾具）——
    **抄整包流**這件事只有一個實作，就不會有人抄錯層。回新家的現役 session（沒有流 ⇒ 空字串）。
    """
    sid = current_run(src_home) or ""
    src = events_path(src_home, sid) if sid else ""
    if not (src and os.path.isfile(src)):
        # 現役那一局還沒落檔（剛 begin_run、還沒寫事件）⇒ 退回「最新**且有檔**」的那一局；
        # 再沒有才看舊制 `data/`。理由：這支是給「抄一份現役的流去試跑」用的，
        # 交回一份空的沒有意義（R6 就是這樣抓到的）。
        sid, src = "", ""
        for r in list_runs(src_home):
            p = events_path(src_home, r)
            if os.path.isfile(p):
                sid, src = r, p
                break
        if not src:
            legacy = os.path.join(src_home, "data", "events.jsonl")
            src = legacy if os.path.isfile(legacy) else ""
    if not src:
        return ""
    dst_dir = os.path.join(runs_dir(dst_home), sid) if sid else os.path.join(dst_home, "data")
    os.makedirs(dst_dir, exist_ok=True)
    with open(src, "rb") as a, open(os.path.join(dst_dir, "events.jsonl"), "wb") as b:
        b.write(a.read())
    if sid:
        set_current_run(dst_home, sid)
    return sid


class FileStore:
    """事件流落盤（**真值來源**）——**一局一檔**：`<home>/runs/<session>/events.jsonl`。

    * 新制：`runs/` 指到的現役那一局（`runs/current`）。
    * **舊制相容**：沒有 `runs/` 而 `<home>/data/events.jsonl` 在 ⇒ 照舊寫那裡（未搬的桌一字不動）。
    * 全新桌（兩者都沒有）：先掛在 `data/`；**引擎開新局時會叫 `begin_run()`** 切到 `runs/<session>/`
      ——順序由引擎保證（`table_created` 之前，見 `engine.Table._load`）。

    內核只認四個方法（`load`／`append`／`size`／`replace_tail`），**語意與 `MemoryStore` 一致**
    （`test_layers` D 組拿同一組斷言跑兩顆 sink）；`begin_run` 是本層多出來的（記憶體 sink 沒這概念，
    所以引擎用 `getattr` 探。
    """

    def __init__(self, home: str, *, name: str = "events.jsonl",
                 session: "str | None" = None) -> None:
        self.home = home
        self.name = name
        self.dir, self.path = self._resolve(session)

    def _resolve(self, session: "str | None"):
        if session:
            d = os.path.join(runs_dir(self.home), str(session))
            return d, os.path.join(d, self.name)
        cur = current_run(self.home)
        if cur:
            d = os.path.join(runs_dir(self.home), cur)
            return d, os.path.join(d, self.name)
        legacy = os.path.join(self.home, "data")
        return legacy, os.path.join(legacy, self.name)

    def begin_run(self, session: str) -> str:
        """**開一局**：落盤切到 `runs/<session>/`，並把 `runs/current` 指過去。

        舊局的檔**原地留著**（＝多局並存；要回看／回溯就指 `runs/current` 過去，或直接讀那個檔）。
        """
        d = os.path.join(runs_dir(self.home), str(session))
        os.makedirs(d, exist_ok=True)
        self.dir, self.path = d, os.path.join(d, self.name)
        set_current_run(self.home, session)
        return self.path

    def load(self) -> "list[dict]":
        if not os.path.exists(self.path):
            return []
        out: "list[dict]" = []
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def append(self, ev: "dict") -> None:
        os.makedirs(self.dir, exist_ok=True)          # 目錄遲到：`__init__` 不一定建得到（還不知道是哪一局）
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")

    def size(self) -> int:
        try:
            return os.path.getsize(self.path)
        except OSError:
            return 0

    def replace_tail(self, offset: int, ev: "dict") -> bool:
        """就地覆寫尾行（動作失敗時把 `action_declared` 標成 rejected，**不新增行**）。

        ⚠ 這是 append-only 的唯一例外；失敗**不能靜默**（檔案會比記憶體少一行，
        `replay_check` 抓得到）——留一行 stderr 供現場追查（協作端 2026-10-05 §1 建議②）。
        """
        try:
            with open(self.path, "r+b") as fh:
                fh.seek(offset)
                fh.truncate()
                fh.write((json.dumps(ev, ensure_ascii=False) + "\n").encode("utf-8"))
            return True
        except OSError as exc:
            sys.stderr.write(f"[void] 覆寫 {self.path} 尾行失敗 offset={offset} "
                             f"seq={ev.get('seq')} err={type(exc).__name__}: {exc}\n")
            return False
