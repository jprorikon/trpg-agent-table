# TRPG 劇本倉（腳手架）

**兩類腳手架，看你要做哪一件事：**

| 你要做的事 | 抄哪一包 | 產出 |
|---|---|---|
| **開一個新作品／新世界**（第一次建這個模組） | `module-template/` | `modules/<模組>/{module.json, characters.json, npcs.json, enemies.json, locale.json, text.json, packs/, assets/, scenarios/<劇本>/…}` |
| **給既有的模組加一齣戲**（模組已存在） | `scenario-template/` | `modules/<既有模組>/scenarios/<新劇本>/…` |

兩包都是**可以直接起桌**的完整品（自檢 error 0／warn 0）。

## 三層：模組 → 劇本 → 桌

```
modules/<模組>/                     一個作品／世界觀
    module.json                     世界層命名（大廳與APP顯示名稱）
    characters.json                 模組角色表——角色的類由模組定；服裝版本併入本體（outfits）
    npcs.json                       NPC 表——世界觀常駐/劇情 NPC 清單（對應 npc:<id>）
    enemies.json                    敵表——魔物/野獸/場景物件提示表（對應 enemy:<id>）
    locale.json                     畫面用語表（Schema 1，前端讀取：glossary / places / text）
    text.json                       後端措辭與語調（一桌一本，覆寫 presentation.TEXT）
    packs/<包id>/                    模組層機制包（同模組劇本共用，如 rules.example）
    assets/                         模組素材清單（Schema 2 瘦清單與分類索引，圖檔本體不進版控）
    scenarios/<劇本>/               劇本的定義（同一劇本可以開好幾張桌）
        scenario.json board.json seats.json goals.json clues.json scene1.json
        config.example.json         桌的旋鈕底稿（複製成 config.json 給某桌使用）
        GM.md TABLE.md WORLD.md     設定說明與 GM 手冊
        packs/<包id>/                劇本層機制包（如帶 monsters 表、artifacts 表）

  ┌─ 桌的執行實體（由引擎維護，與定義分離，不進版控）
  └─ 伺服器端 state/tables/<模組>/<劇本>/<桌ID>/{config.json, runs/<session>/events.jsonl}
```

## 用法

```sh
# 1. 模組自檢（每個劇本各造一張臨時桌，跑同一套起桌判據）
tools/check_module.sh module-template
tools/check_module.sh examples/hyakki_tower

# 2. 劇本自檢（驗「這一齣戲」放進指定模組能否直接起桌）
tools/check_module.sh --module module-template scenario-template

# 3. 試玩模式（起一張臨時桌測試，只動 /tmp 副本）
tools/check_module.sh --serve module-template 18980
```

規格（每個 JSON 的欄位、句庫、效果、投影、戰鬥擴充）＝ **`SCENARIO_FORMAT.md`**（正本在引擎倉，用 `tools/sync_scenario_format.sh` 同步）。
真例參考：
- `examples/hyakki_tower/`：世界觀與機制完全對齊的官方範例（含 `locale.json`、`text.json`、`npcs.json`、`enemies.json`、`assets/` 與戰鬥擴充）。
- `examples/hyakki_tower/`：以戰鬥與死亡送還為核心的百鬼塔範例。

## 寫得「對」之後，還要寫得「好玩」

格式只保證「起得來」。**設計**是另一件事，寫在 `docs/SCENARIO_DESIGN.md`：

- **七條原則**：準備局面不準備劇情（P1）／每個結論至少三條證據（P2）／地圖要多條路（P3）／
  世界要有自己的時鐘（P4）／願望不要共用同一個瓶頸（P5）／失敗要有出口（P6）／碎片正文要可攜且私密（P7）。
  每一條都對應到我們格式裡的一個欄位，也對應到工具的一條規則。
- **交桌前的檢查清單**（14 條）＋ **四種結構怎麼選**（塔式／節點調查／壓力時鐘／抽象桌）。
- **現役三個劇本的實測盤點**（2026-10-20）：哪幾條結論只有一枚碎片、哪 3 條願望共用一個結論、
  為什麼「全員倒地」的 lose 幾乎不成立、最新的那一齣為什麼反而寫成了五幕劇本。

機械可查的部分：

```sh
python3 tools/design_lint.py <模組目錄>            # 設計味道掃描（WARN／INFO，不擋起桌）
python3 tools/design_lint.py <模組目錄> --strict   # 有 WARN 就 exit 1（交件前用）
```

分工很清楚：**能不能起桌**問 `tools/check_module.sh`；**會不會卡死／好不好玩**問 `tools/design_lint.py`。

一局「跑起來多長、手花在哪」是另一個問題，寫在 **`docs/GAMEPLAY_LOOP.md`**（四個旋鈕：單次事件長度／
事件密度／地圖規模／戰鬥日常配比，附兩個可直接 A/B 的 config 變體）。量測工具吃現役事件流：

```sh
python3 tools/loop_metrics.py <桌目錄> --board <模組>/scenarios/<劇本>/board.json
```


兩個腳手架（`module-template/`、`scenario-template/`）已照這份手冊調整過：每條結論三枚碎片（跨池）、
起點兩條出路＋一個環路、內建 `neutral`（撤退）結局、內建一條「世界自己的時鐘」觸發、願望示範 `or` 替代路線。
`tools/design_lint.py` 對它們的輸出是 **WARN 0**（只有幾條刻意的 INFO）。
