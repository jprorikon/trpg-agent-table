#!/usr/bin/env python3
"""**L0 Audio——播放控制**（`audio` 事件；聲音本身在前端）。

設計者 2026-10-20 定的分工，這支只做中間那一層：

* **資源綁模組**：有哪些曲子、哪個事件該放哪一首、音量／循環 ＝ `<模組>/audio.json`
  （`trpg/service/audio.py` 讀它）。引擎**不認識任何一首曲子**。
* **引擎只負責播放控制**：什麼時候放、放哪個聲道、淡入淡出幾秒、現在在放什麼，
  以及**這一切都要進事件流**（回放一致、晚進場的人補得到現況）。
* **前端負責真的出聲**：解碼、混音、音量滑桿、瀏覽器 autoplay 解鎖（那不在引擎的管轄）。

三個刻意的取捨：

1. **一個事件型別 `audio`**（`op=play|stop`），不是 `audio_play`／`audio_stop` 兩個型別
   ——`_apply` 一處折狀態、前端一處分派、白名單只加一個字。
2. **事件裡帶已解析的 URL**（`url`＝`/assets/<模組>/audio/…`）**＋`key`**：前端拿到事件就能直接播，
   不必先抓表；`key` 留著是給「同一首曲子換檔」與人看的。查不到別名／音檔不在磁碟上
   ⇒ **不發事件**（缺＝沒有），但 GM 明確要求時**當場叫**（列出可用的鍵）。
3. **自動提示（`cues`）只由模組宣告**：GM 不必記「打王要放哪首」，模組作者寫一次。
   GM 仍可臨場覆蓋（`audio_play`／開景帶 `bgm`）——引擎只認「這一手是模組給的還是 GM 給的」
   （事件上的 `auto`），不認曲名。

`state["audio"]` ＝ 由 `audio` 事件折出來的**現況**（晚進場、重新整理、觀戰頁都靠它）：

    {"bgm": {"key","url","loop","gain","fade","seq","auto","title"} | None,
     "sfx": {...} | None,
     "seq": 最後一筆音訊事件的 seq}

前端輪詢：`bgm.seq` 變了 ⇒ 換曲（或 `null` ⇒ 停）；`sfx.seq` 比上次大 ⇒ 播一次。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from trpg.core.errors import RuleError

EVENT = "audio"
DEFAULT_FADE = 0.8              # 秒（表沒寫時）


class AudioMixin:
    """桌的播放控制面（`Table` 繼承；`self.audio` 由 L6 注入＝模組音訊表）。"""

    audio: Optional[Any] = None

    # -- 讀 ------------------------------------------------------------------
    def audio_lib(self) -> Optional[Any]:
        """這一桌的模組音訊表（沒有表 ⇒ None）。**只有 L2／L6 讀得到，引擎自己只用它查鍵**。"""
        a = getattr(self, "audio", None)
        try:
            return a if (a is not None and a.ok) else None
        except Exception:                                             # noqa: BLE001
            return None

    def audio_catalog(self) -> Dict[str, Any]:
        """這一桌可以放什麼（給 GM 與 `table_gm op=audio_list`）：`{tracks:[…], cues:{…}}`。"""
        a = self.audio_lib()
        if a is None:
            return {"tracks": [], "cues": {}, "note": "這一張桌的模組沒有 audio.json（沒有 BGM／效果音）"}
        return {"tracks": a.catalog(), "cues": a.cues()}

    def audio_check(self, key: str) -> Dict[str, Any]:
        """別名 ⇒ 音軌；不能用就**當場叫**（GM 打錯字要立刻知道，不要靜靜沒聲音）。"""
        a = self.audio_lib()
        if a is None:
            raise RuleError("這一張桌沒有音訊表（模組沒放 audio.json）——先讓模組作者加音軌")
        row = a.track(key)
        if not row:
            have = a.keys()
            raise RuleError(f"沒有這個音軌：{key!r}（可用：{have or '（空）'}）")
        if not a.url(key):
            raise RuleError(f"音軌 {key!r} 的檔案不在磁碟上（{row.get('path')}）——"
                            f"把檔案放到 modules/<模組>/assets/{row.get('path')}")
        return row

    def audio_state(self) -> Dict[str, Any]:
        """現況（由事件折出；沒放過任何東西 ⇒ 兩個聲道都 None）。"""
        st = (self.state or {}).get("audio") or {}
        return {"bgm": st.get("bgm"), "sfx": st.get("sfx"), "seq": int(st.get("seq") or 0)}

    # -- 控制 ----------------------------------------------------------------
    def audio_play(self, *, ch: str = "bgm", key: str, gain: Optional[float] = None,
                   loop: Optional[bool] = None, fade: Optional[float] = None,
                   by: Optional[str] = None, auto: bool = False) -> Dict[str, Any]:
        """放一個音軌（`bgm` 換掉該聲道現有的；`sfx` 是一次性的提示）。

        聲道必須與別名前綴一致（`bgm:` 只能進 bgm）——避免「把音效當 BGM 循環」這種手滑。
        `loop`／`gain` 沒給 ⇒ 用表上的；`fade` 沒給 ⇒ 0.8 秒。
        """
        row = self.audio_check(key)
        ch = str(ch or row["kind"])
        if ch not in ("bgm", "sfx"):
            raise RuleError(f"聲道只能是 bgm／sfx（收到 {ch!r}）")
        if ch != row["kind"]:
            raise RuleError(f"{key!r} 是 {row['kind']}，不能放進 {ch} 聲道")
        url = self.audio_lib().url(key)
        return self._emit(EVENT, public=True, op="play", ch=ch, key=str(key), url=url,
                          loop=(row["loop"] if loop is None else bool(loop)),
                          gain=(row["gain"] if gain is None else float(gain)),
                          fade=(DEFAULT_FADE if fade is None else float(fade)),
                          title=row.get("title"), by=by, auto=bool(auto))

    def audio_stop(self, *, ch: str = "bgm", fade: Optional[float] = None,
                   by: Optional[str] = None) -> Dict[str, Any]:
        """停一個聲道（`fade` 秒淡出；沒在放也照樣記一筆——前端據此收乾淨）。"""
        ch = str(ch or "bgm")
        if ch not in ("bgm", "sfx"):
            raise RuleError(f"聲道只能是 bgm／sfx（收到 {ch!r}）")
        return self._emit(EVENT, public=True, op="stop", ch=ch,
                          fade=(DEFAULT_FADE if fade is None else float(fade)), by=by)

    # -- 事件 → 狀態 ---------------------------------------------------------
    def audio_apply(self, ev: Dict[str, Any]) -> None:
        """`audio` 事件折進 `state["audio"]`（**唯一寫入路徑**；回放因此一致）。"""
        if ev.get("type") != EVENT:
            return
        st = self.state
        cur = st.setdefault("audio", {"bgm": None, "sfx": None, "seq": 0})
        ch = str(ev.get("ch") or "bgm")
        if ch not in ("bgm", "sfx"):
            return
        if str(ev.get("op")) == "play":
            cur[ch] = {"key": ev.get("key"), "url": ev.get("url"),
                       "loop": bool(ev.get("loop")), "gain": ev.get("gain"),
                       "fade": ev.get("fade"), "seq": ev.get("seq"),
                       "auto": bool(ev.get("auto")), "title": ev.get("title")}
        else:
            cur[ch] = None
        cur["seq"] = int(ev.get("seq") or 0)

    # -- 模組宣告的自動提示 ---------------------------------------------------
    def audio_cue(self, ev: Dict[str, Any]) -> List[Dict[str, Any]]:
        """一筆事件 ⇒ 模組宣告的自動提示（`cues`）；沒有＝不回任何東西。

        * `scene_open` 帶了 `bgm`（GM 開景時指定）⇒ **以 GM 為準**，不吃表上的預設；
        * 模組的 cue 壞掉（鍵不存在／音檔不在）⇒ 記一條警告、**不炸桌**——一顆打錯字的
          音軌不該讓整張景開不起來，但下一次 `audio_catalog()`／`/health` 看得到。
        """
        a = self.audio_lib()
        if a is None:
            return []
        etype = str(ev.get("type") or "")
        # 事件型別 → cue 鍵（模組表上寫的是 cue 的名字；拆景後「輪開始」叫 round_start）
        _cue_key = {"round_started": "round_start"}.get(etype, etype)
        key = ""
        if not key:
            key = a.cue(_cue_key) or ""
        if not key:
            return []
        try:
            return [self.audio_play(ch=a.track(key)["kind"] if a.track(key) else "bgm",
                                    key=key, auto=True, by="module")]
        except (RuleError, KeyError) as exc:
            self._audio_warn(f"cue {etype}→{key}: {exc}")
            return []

    def _audio_warn(self, msg: str) -> None:
        """自動提示的警告收在記憶體（不進事件流：這不是桌上發生的事，是資料問題）。"""
        box = getattr(self, "_audio_warnings", None)
        if box is None:
            box = self._audio_warnings = []
        if msg not in box:
            box.append(msg)

    def audio_warnings(self) -> List[str]:
        a = self.audio_lib()
        out = list(getattr(self, "_audio_warnings", []) or [])
        if a is not None:
            out += a.warnings()
        return out
