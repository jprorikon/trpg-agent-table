# 模組腳手架（開一個新作品／新世界）

一個模組目錄代表**一個獨立的世界觀（作品）**，對應伺服器端（`trpg/server`）的 `modules/<模組id>/`。

## 模組完整結構一覽

| 路徑 | 角色與規格 |
|---|---|
| `module.json` ★ | **世界層命名**：`{"id": "<模組id>", "name": "大廳與APP顯示名稱", "note": "世界觀注記"}` |
| `characters.json` ★ | **模組角色表**：**角色的類（`class`）由模組決定**。服裝版本併入本體（`outfits`），選人時同一角色可挑不同立繪 |
| `npcs.json` | **NPC 表**（設計者 2026-10-19：「NPC 和敵人都各自單獨建表」）：本世界常駐/劇情 NPC 清單，立繪索引對應 `npc:<id>` |
| `enemies.json` | **敵表**（設計者 2026-10-19：「NPC 和敵人都各自單獨建表」）：魔物/野獸/物件提示表，立繪索引對應 `enemy:<id>` |
| `locale.json` | **畫面用語表（Schema 1）**：固有名詞與世界觀用語（`glossary`、`places`、`text`）。前端不內建任何作品語彙，一律從此讀取 |
| `text.json` | **服務端措辭表（一桌一本）**：覆寫引擎預設字樣（`white_room`、`boss_defeated`、`action.*.label/desc` 等），劇本層可再覆蓋 |
| `packs/<id>/` | **模組層機制包**：同模組劇本共用的內容包（如 `rules.<模組id>`：技能/道具/等級/星等表）。引擎倉不帶任何作品內容 |
| `assets/` | **模組級素材清單（Schema 2）**：`index.json` 瘦清單 ＋ `index/<類>.json`。**圖檔二進位本體不進版控**，只版管清單與 README |
| `scenarios/<劇本>/` ★ | **劇本定義層**：起手劇本（**改有意義的 id**，如 `chapter_1`，別留 `example`；同一劇本可開多張桌） |

---

## 關鍵規範與約束

### 1. 角色表與服裝（`characters.json`）
- 角色的職業/職能（`class`）由模組全權定義（例如 `アタッカー／バランサー／サポーター` 或 `warrior／mage／ranger`）。
- 2026-10-19 起**服裝版本併入本體**（`outfits: [{"id": "default", "name": "通常"}, {"id": "awakened", "name": "覺醒"}]`），選人時玩家可在同一角色下切換不同立繪版本。

### 2. NPC 與敵人都單獨建表（`npcs.json`／`enemies.json`）
- 世界觀裡的 NPC 與敵人不再混入雜項表，各自單獨成表。
- 引擎內核不直接讀這兩張表（給 GM、agent、立繪出圖、查證使用）；引擎在劇本層讀取 `tables/monsters.json` 決定實際遭遇。

### 3. 畫面用語（`locale.json`）與伺服器措辭（`text.json`）
- **`locale.json`（前端讀取）**：對外暴露於 `/assets/<模組>/locale.json`。
  - `glossary`：原文（日文/中文）→ 各語言譯名（`kind: faction|role|artifact|board`）。
  - `places`：節點 id → 地名譯名。
  - `text`：覆寫前端字典中性字樣（例：將中性「敵人 ×{n}」覆寫為「魔物 ×{n}」）。
- **`text.json`（後端讀取）**：自定義廣播、旁白、錯誤提示與動作名稱。

### 4. 包的三層架構（Packs）
```
① 引擎層包（跨模組通用基礎機制：d20core, board, semic, wound, death... 不含作品內容）
  ↓
② 模組層包（<模組>/packs/<id>/，如 rules.example，同模組劇本共用）
  ↓
③ 劇本層包（<劇本>/packs/<id>/，劇本專有數值；同名時劇本層優先覆蓋）
```

### 5. 戰鬥遭遇擴充（`tables/monsters.json`）
2026-10-19 戰鬥系統支援：
- `hp`：每隻怪的血量（預設 1＝一擊必殺）。
- `count`：同格怪的數量。
- `active`：主動怪收景咬人機制（`{"chance": 0.4, "max_targets": 1}`；`n=0` 視為被動怪）。
- 帶血量戰鬥時，包需在 `pack.json` 的 `provides.events` 宣告 `"monster_damaged"`。

### 6. 素材目錄（`assets/`）Schema 2 瘦清單
- `assets/index.json` 為瘦清單（`schema: 2`），透過 `parts` 拆分至 `index/{portrait, avatar, npc, enemy, bg, crowd, icon}.json`。
- **圖檔本體嚴禁進 Git 版控**。

### 7. 桌實體（執行態）與模組目錄解耦
- 卓的實際資料在伺服器端的 `state/tables/<模組>/<劇本>/<桌ID>/`。
- `config.json`、`runs/`、`*.bak-*` 均為卓執行狀態，**不進版本控制**。
- 帳號層（角色實例／錢包／座次）已搬到**引擎 PostgreSQL**（`auth.db_env`，由部署端給）：
  模組 repo 不再有任何帳號資料；舊的 `accounts.json` 只是 legacy（一桌一份，已被取代）。

---

## 建立新模組工作流

1. 複製 `module-template/` 至 `modules/<你的模組id>/`。
2. 修改 `module.json`（id、name、note）與 `characters.json`（角色表）。
3. 按需填寫 `npcs.json`、`enemies.json`、`locale.json`、`text.json` 與 `assets/`。
4. 將起手劇本 `scenarios/example/` 改名為具體劇本 id（如 `scenarios/prologue/`），編寫劇本內容。
5. **作者自檢**（確保 error 0 / warn 0）：
   ```sh
   tools/check_module.sh modules/<你的模組id>
   ```
6. 試跑臨時桌：
   ```sh
   tools/check_module.sh --serve modules/<你的模組id> 18980
   ```

## 設計（不只是格式）

`docs/SCENARIO_DESIGN.md`——七條設計原則、交桌檢查清單、現役劇本盤點；
機械可查的部分用 `python3 tools/design_lint.py <這一包>`（WARN／INFO，不擋起桌）。
`scenarios/example/` 已照那份手冊調過：每條結論三枚碎片（跨池）、起點兩出口＋環路、
`e_retreat`（neutral 撤退結局）、`t_night_falls`（世界時鐘）——**改劇本時別把這些示範刪掉**。
