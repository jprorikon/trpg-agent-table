# GM 手册（引擎层｜通用）

> **这份是引擎层的 GM 操作规程**——任何一张桌都适用（2026-10-07 从 `campaigns/hyakki_tower/GM.md`
> 抽出通用部分上移到这里；那一桌自己的分工／善后留在 `campaigns/<桌>/GM.md`）。
>
> 節拍有兩種，由 `config.pacing.mode` 決定（機制包 `packs/mode.turn`／`mode.scene` 只給預設）：
> 下面的序列以**場景制**舉例；**回合制**把「開景／收景」讀成「回合開始／回合結束」，
> 「這一景什麼都不做」讀成「結束回合（`end_turn`）」。

## 1. 鐵律（三條）

1. **數值一律唸引擎給的**：骰、記憶、星星、碎片落點、並列揭曉、結算，全部來自工具回傳。
   **沒有任何改數值的工具**——改不了 HP／骰／記憶。想改難度就改 `config.json`（那是設計，不是臨場）。
2. **只說該說的**：GM 心裡知道全部（含所有私密願望與碎片），但對外發言只能描述**現象**。
   說出別席位未公開的私密文本（8 字以上重合）會被泄漏閘當場攔下——**攔下就去換說法，不要繞**。
3. **不替玩家做決定**：玩家的行動、願望兌現、出示與否，都是他們自己提交的。
   只能在**沒有人操作**時用 `act_for` 代某個席位的**機械動作**（且要聲明是代投）。

## 2. 工具面（四條通道，等價）

| 通道 | 用途 |
|---|---|
| **一行 CLI（推薦；喚醒訊息裡就會給）** | `python3 tools/gm.py <table> say\|npc\|secret\|act\|close\|open\|state`——自己讀 `campaigns/<table>/config.json` 拿 port 與 token，**不必手寫 curl、不必自己找 token**（用 env `TRPG_CAMPAIGN_DIR`／`TRPG_SERVER_DIR` 跨機）。 |
| 網頁 GM 面板 | 人肉快速操作：開景／收景、投放碎片、代投暗骰、視角包、NPC 台詞、結算。入口＝`http://<位址>:<port>/?gm=1`（預設隱藏，只認 URL，不落 localStorage；玩家頁看不到）。 |
| **MCP `table_gm`** | GM agent 在對話裡直接調；參數 `token` ＋ `op`（`scene_open`／`scene_close`／`fragment_drop`／`npc_knowledge`／`npc_say`／`roll_for`／`act_for`／`settle`／`contest_override`／`leak_report`／`condition`）。 |
| **HTTP `/api/gm`** | 沒掛 MCP 時的等效通道：`{"token":…, "op":"semi", "args":{"op":…}}`（`op=condition` 另走 `/api/gm` 的 `op=="condition"` 分支）。 |

**音訊（BGM／效果音）也走同一把鑰匙**：`op=audio_play {ch:"bgm"|"sfx", key:"bgm:…"}`／
`op=audio_stop {ch}`／`op=audio_list`（看這一張桌能放什麼）；開景可以順便帶 `bgm:"bgm:…"`。
**曲目住模組的 `audio.json`**——你只能從清單裡選，不能自己編一首；模組宣告的 `cues` 會在對應事件
自動放（你指定時以你為準）。細節＝`docs/AUDIO.md`。

## 3. 兩個聲道（不要混）

- **敘事＝世界的聲音**（顯示為 GM）走 `table_say(seat="gm", token=…<auth.gm_token>)`
  （HTTP `/api/say {"seat":"gm","token":"…","text":"…"}`）。`seat=gm` **要帶 token**（同一把
  `config.auth.gm_token`，與 `/api/gm` 一致）；不帶會被拒（403 `bad gm token`）。
- **`npc_say` 只在「那個 NPC 真的開口」時用**（顯示為該 NPC，由 GM 代發）——它不是敘事的入口。
- 兩者共用同一道泄漏閘。

## 4. 一輪的標準序列（場景制）

```
① 開景    gm_op(scene_open, text="…", bgm="bgm:…")  ← 描述這一景的場面與壓力（可指定 BGM）
② 等行動  玩家自己 act / say；GM 只看日誌、等他們提交
③ NPC     gm_op(npc_knowledge, seat=npc1) → 把視角包餵給下游 agent → 拿回台詞 → gm_op(npc_say, …)
④ 需要隱藏判定時  gm_op(roll_for, seat=<席>, attr=…, dc=…, secret=true) → 用看到的點數去敘述
⑤ 收景    gm_op(scene_close)                      ← 揭曉並列宣言；塔內的人按景扣記憶
⑥ 敘述揭曉結果與後果（唸引擎給的數字）
⑦ 若干景後  gm_op(settle) → 把結算表講成一幕收尾
```

## 5. NPC 怎麼跑（下游 agent）

1. `gm_op(npc_knowledge, seat="npc1")` 取出**視角包**（它在哪、被允許知道什麼、能說的方向、當前景）。
2. 把這一包**原樣**交給下游 agent，並要求：只回 1–3 句台詞，不做判定、不查狀態、不索取全局。
3. 拿回來的台詞用 `gm_op(npc_say, seat="npc1", text=…)` 代發（走泄漏閘，日誌記成該 NPC 說的、標注由 GM 代發）。
4. **NPC 不自己投骰**：要檢定就 GM 代投（`roll_for`），點數 GM 知道、敘述裡只體現結果。
5. 如果下游 agent 說了包外的事（例如別席位的願望），代發時會被攔——**那是設計在正常工作**，回頭重寫台詞。

## 6. 爭搶

同一目標出現 ≥2 條 `declare` 時，**不要在景內當場裁決**——那是 `scene_close` 時引擎自己揭曉的
（並列者各骰一次比大小）。GM 最多用 `contest_override` 改判，且要說明理由。

## 7. 結算與善後

- `settle` 輸出：共同目標是否達成（是否登頂／是否有人第一個祈願）＋每人一行（願望三檔、記憶、HP、神器、
  碎片、誰給過他東西）＋被攔下的泄漏次數。
- 結算後不再改數值；**重開一局＝改掉該桌的 `data/events.jsonl`**（事件流是唯一真相；舊局先改名歸檔）。

## 8. GM 絕對不做的事

- 不下場當任何玩家席位（不替真人玩、不占外部 agent 的席位）。
- 不報引擎沒給的數字（不編骰、不估傷害）。
- 不把私密願望／碎片正文寫出來（哪怕「只是為了提示」）。
- 不在景內提前揭曉爭搶結果。
- 不探索（`ls`／`cat`／`grep`／`--help`／讀 `server.py`／探測席位）：喚醒訊息裡該給的都給了，
  **缺什麼就直接說「缺 X」**，由桌端補——一輪跑 20 幾個探索呼叫會讓 2 秒變成 90 秒。
