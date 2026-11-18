# 音訊（BGM／效果音）：資源綁模組，引擎只做播放控制

> 2026-10-20 設計者定案：「和圖片資源類似，這些資源本身綁定到模組上，引擎只負責做播放控制」。
> 這一份是那個分工的實作契約：**模組擁有曲子**、**引擎擁有播出**、**前端負責真的出聲**。

## 1. 三層分工（一句話各一層）

| 層 | 擁有什麼 | 在哪 |
|---|---|---|
| **模組**（作品內容） | 有哪些曲子（別名／檔案／音量／循環／曲名）＋**哪個事件自動放哪一首** | `modules/<模組>/audio.json` ＋ `modules/<模組>/assets/audio/**` |
| **引擎**（播放控制） | 什麼時候放、放哪個聲道、淡入幾秒、**現在在放什麼**；全部進事件流（回放一致） | `trpg/engine/audio.py`（`audio_play`／`audio_stop`／`audio_cue`） |
| **前端**（真的出聲） | 解碼、混音、音量滑桿、瀏覽器 autoplay 解鎖 | 前端容器（`/assets/<模組>/…` 拿檔案、`View.audio` 拿現況） |

引擎**不認識任何一首曲子**：它只認「別名」與「聲道的狀態」。反過來，模組**不能**決定
「什麼時候放」——那是引擎／GM 的事（模組只能宣告「這個事件預設放哪一首」）。

## 2. 音訊表（`modules/<模組>/audio.json`）

作者手寫的正本，**進版控**（與 `locale.json` 同性質；`assets/index.json` 那種生成物不放這裡）。

```jsonc
{
  "schema": 1,
  "doc": "這一部作品的音訊表",
  "tracks": {
    "bgm:town":      {"path": "audio/bgm/town.m4a", "loop": true,  "gain": 0.45,
                      "title": {"ja": "ヤマト", "zh_Hans": "苍梧城", "zh_Hant": "ヤマト"}},
    "sfx:thunder":   {"path": "audio/sfx/thunder.m4a", "gain": 0.8, "title": "雷鳴"}
  },
  "cues": {
    "scene_open":    "bgm:town",      // 開景預設（GM 開景帶 bgm 時以 GM 為準）
    "entered_tower": "bgm:tower",
    "boss_defeated": "sfx:thunder",
    "white_room":    "sfx:white_room"
  }
}
```

* **鍵＝別名**：`bgm:` 前綴＝背景音樂（會替換、預設循環）、`sfx:` 前綴＝一次性效果音。
  **聲道由前綴決定**，`kind` 寫不一致會用警告記下來（以前綴為準）。
* `path` 是**相對 `assets/` 的路徑**，副檔名只收 `.mp3/.m4a/.aac/.ogg/.oga/.opus/.wav/.flac`。
* `loop` 預設：`bgm` → `true`、`sfx` → `false`；`gain` 預設 `0.7`（0–1，前端再乘自己的主音量）。
* `title` 可以是字串或**多語系物件**（原樣送給前端與日誌）。
* `cues`：引擎事件型別 → 別名。**引擎只會在被宣告的那幾種事件上自動放**（其他一律由 GM 指示）。

音檔放 `modules/<模組>/assets/audio/…`，對外＝`/assets/<模組>/audio/…`（**與圖同一條路由**：
同一個模組白名單、同一套防穿越、同一套快取規則）。

**音檔不進版控**（2026-10-21 設計者定案：跟圖一樣）——版控只收 `audio.json` 這張**表**（別名、音量、
loop、cues），`assets/audio/**` 本體是生成物／作者素材，`modules/<模組>/.gitignore` 沿用 `assets/*`
的規則直接忽略。缺檔時引擎只在 `/health` 與 GM 的 `available[].ready` 上標出來（那一軌不響、不報錯），
所以**換機器／新成員要自己把音檔放回去**（見下節）。

## 3. 事件流（唯一真相）

引擎發**一種**事件 `audio`，`op` 分 `play`／`stop`：

```jsonc
{"seq": 42, "type": "audio", "public": true, "op": "play", "ch": "bgm",
 "key": "bgm:town", "url": "/assets/hyakki_tower/audio/bgm/town.m4a",
 "loop": true, "gain": 0.45, "fade": 0.8, "title": {...}, "by": "gm", "auto": false}
```

* **帶已解析的 URL**：前端拿到事件就能播，不必先抓表；`key` 留著給「換檔不改鍵」與人看。
* `auto: true`＝**模組宣告的提示**（`cues`）；`false`＝GM 指示。分得出來是為了事後查帳。
* `state.audio`（由事件折出）：`{"bgm": {...}|null, "sfx": {...}|null, "seq": 最後一筆音訊事件}`。
  ⇒ 晚進場／重新整理／觀戰頁都能從投影直接補到「現在在放什麼」。
* **缺＝沒有**：別名查不到、音檔不在磁碟上 ⇒ **不發事件**（前端就什麼都不放）。
  GM 明確要求時則**當場拒並列出可用的鍵**（不靜默）。

## 4. 投影（`View.audio`；純附加）

有 `audio.json` 的模組才有這一格：

```jsonc
"audio": {
  "bgm": {"key": "bgm:town", "url": "...", "loop": true, "gain": 0.45,
          "fade": 0.8, "seq": 42, "auto": false, "title": {...}} ,   // 沒在放＝null
  "sfx": null,
  "seq": 42,
  "library": {"module": "hyakki_tower", "base": "/assets/hyakki_tower/",
              "index": "/assets/hyakki_tower/audio.json?v=1729…"},     // 可拿它預載
  "available": [...],   // 只有 GM 視角有：key／kind／title／loop／gain／ready
  "cues": {...}         // 只有 GM 視角有
}
```

**沒有 `audio.json` 的模組／舊桌：這一格整個不出現**（legacy 投影逐字不變；`test_audio` E6/E7 咬住）。

## 5. GM 怎麼用

| 場合 | 工具 | 例 |
|---|---|---|
| 換 BGM | `table_gm op=audio_play` | `{op:"audio_play", ch:"bgm", key:"bgm:tower", fade:1.5}` |
| 放一次效果音 | `table_gm op=audio_play` | `{op:"audio_play", ch:"sfx", key:"sfx:thunder"}` |
| 停一個聲道 | `table_gm op=audio_stop` | `{op:"audio_stop", ch:"bgm"}` |
| 看有哪些 | `table_gm op=audio_list` | 回 `{tracks:[{key,kind,title,loop,gain,ready}], cues:{…}}` |
| 開景順便指定 BGM | `table_gm op=scene_open` | `{op:"scene_open", text:"…", bgm:"bgm:tower"}` |

一行 CLI（`tools/gm.py`）：

```bash
python3 tools/gm.py <桌> sounds                 # 這一張桌能放什麼
python3 tools/gm.py <桌> bgm bgm:tower          # 換 BGM（淡入 0.8s）
python3 tools/gm.py <桌> sfx sfx:thunder        # 一次性效果音
python3 tools/gm.py <桌> silence bgm            # 停 BGM
python3 tools/gm.py <桌> open "景的正文" bgm:tower   # 開景＋指定 BGM
```

`/health` 多一格 `audio`（`present`／`tracks`／`cues`／`warnings`）——**資料問題看這裡**：
cue 指到不存在的鍵、音檔不在磁碟上、別名沒前綴，都會收成 `warnings`，不會讓桌子開不起來。

## 6. 前端要怎麼做（給做客戶端的人）

1. **解鎖**：瀏覽器不給「沒互動就出聲」。第一次點擊／觸碰時 `AudioContext.resume()` 並建好
   兩個聲道（`bgm`／`sfx`），之後才受控。頁面上要有一個喇叭開關（靜音也是使用者的權利）。
2. **輪詢／訂閱**：每次拿到 `View.audio`：
   * `bgm.seq` 變了 ⇒ 換曲（`bgm == null` ⇒ 淡出停掉）；用 `gain`／`fade` 做交叉淡入淡出；
   * `sfx.seq` 比上次大 ⇒ 播一次（**同一個 seq 不要重播**：輪詢會重送同一格）；
   * `seq` 只用來判斷「這是新的一手」，不要拿它當播放進度。
3. **播放**：`new Audio(url)` 或 `MediaElementSource`；`bgm.loop` 決定 `loop`；
   音量 = `gain` × 使用者主音量。音檔走 `/assets/<模組>/…`，**有支援 `Range`（206）**
   ⇒ 手機 Safari 才會播、也才能 seek。
4. **晚進場**：直接照 `audio.bgm` 建播放器（不必等下一筆事件）；`sfx` 舊的就不要補播。
5. **參考實作**（最小可用，夠看出契約怎麼用）：

```js
const el = { bgm: new Audio(), sfx: new Audio() };
let seen = { bgm: 0, sfx: 0 };
function applyAudio(a, unlocked) {
  if (!a) return;
  if (a.bgm && a.bgm.seq !== seen.bgm) {         // 換曲（或第一次看到）
    seen.bgm = a.bgm.seq;
    el.bgm.src = a.bgm.url; el.bgm.loop = !!a.bgm.loop;
    el.bgm.volume = a.bgm.gain ?? 0.7;
    if (unlocked) el.bgm.play().catch(() => {});
  } else if (!a.bgm && seen.bgm) {                // 停
    seen.bgm = 0; el.bgm.pause();
  }
  if (a.sfx && a.sfx.seq > seen.sfx) {            // 一次性效果音
    seen.sfx = a.sfx.seq;
    el.sfx.src = a.sfx.url; el.sfx.volume = a.sfx.gain ?? 0.8;
    if (unlocked) { el.sfx.currentTime = 0; el.sfx.play().catch(() => {}); }
  }
}
```

（`fade` 秒的淡入淡出用 `setInterval` 調 `volume`，或 Web Audio 的 `gain.linearRampToValueAtTime`。
這一段刻意留給前端：引擎只給「幾秒」，怎麼淡是客戶端的事。）

## 7. 加一個模組／換一首曲

1. 放檔案：`modules/<模組>/assets/audio/bgm/<名>.mp3`（或 `sfx/…`）。**格式一律 mp3**
   （`.m4a`／AAC 在 Chromium 內核的 Chrome 解不了 ⇒ 那些玩家會靜音；Safari／Edge 才響）。
   **音檔不進版控**，換機器／新成員要自己放回去（`assets/audio/**` 走 `.gitignore` 的 `assets/*` 規則）。
2. 寫進 `modules/<模組>/audio.json` 的 `tracks`（新別名），需要自動提示就加 `cues`。
3. **不用改程式**：投影與引擎都只認別名。換一首＝改 `path`（別名不動 ⇒ 事件流與前端都不用動）。
4. 驗證：`python3 tools/check_audio.py <模組>`（音訊表本身：別名／路徑／cue／音檔在不在）＋
   `curl -s localhost:<port>/health | jq .audio`（`warnings` 要是空的）。

### 7.1 音檔怎麼生（百鬼塔那一套，可複用）

本地 4090 上的 **YuE2-3B（int8）**，工作目錄 `workspace/_hyakki_audio/`：

```
python3 mkwf.py plan          # 產生「出譜」工作流（雙聲部：Vocal＋Ins）→ 丟 4090
python3 fetch_plan.py <名> <prompt_id>   # 取回 ABC 譜（abc/<名>.abc）＋ 對照音檔
python3 mkwf.py render        # 收成**單聲部**（刪 Vocal）＋剪到目標小節數 → 產生「重渲染」工作流
python3 collect.py            # 逐秒 RMS 量時長／尾巴（判有沒有被硬切）→ 進模組 assets/audio/
python3 finalize.py           # 正規化 −18 dB、尾巴淡出、轉 mp3；効果音用 ffmpeg 合成
```

要點：**單聲部**路線最乾淨（模型唱不唱都不影響）；`max_duration` 撞上限＝硬切，所以先把譜剪到
40–60 秒該有的小節數再重渲染，讓它自己收尾；効果音（太鼓／咆哮／鈴）用 ffmpeg 合成比讓模型生更準。

## 8. 已知邊界（刻意不做）

* **引擎不混音、不排隊**：`sfx` 只有「最後一筆」，同時兩個效果音重疊是前端的事
  （前端可以自己開多個播放器）。引擎不記「上一次的 sfx 还在响」。
* **不做時間軸編輯**：沒有「第 3 秒淡出」這種腳本；要分段就請 GM 在那一景換曲。
* **不做跨桌同步**：每張桌各自播放；觀戰頁自己從投影補現況。
* **不做版權檢查**：音檔是模組作者放進 repo 的內容，與圖同一條紀律（見 `ref-library` 那邊的規範）。
