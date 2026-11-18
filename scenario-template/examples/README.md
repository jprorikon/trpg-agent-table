# `examples/` —— 填好的實例（照抄比讀規格快）

| 範例 | 是什麼 | 看它學什麼 |
|---|---|---|
| `hyakki_tower/` | **戰鬥為主**的完整實例（三座城市＋荒野＋六層的百鬼塔；含角色表與模組層規則包） | 模組層包 `rules.core`、敵人住表、按節點賣貨、經驗只從戰鬥來、死了回最近的城市並缺席 2 回合 |
| `hyakki_tower/` | **世界觀與機制完整對齊**的官方級模組實例（示範劇本篇） | 現代模組全套結構：獨立 `npcs.json`／`enemies.json` 表、畫面用語表 `locale.json`、服務端語調 `text.json`、素材瘦清單 `assets/index.json`（Schema 2）、角色服裝版本併入本體（`outfits`）、2026-10-19 戰鬥擴充（`hp`／`count`／`active`） |

兩者都是**模組目錄**（三層形狀）：

```
<範例>/
    module.json                 世界層命名
    characters.json             角色表（角色的類由模組定；含 outfits）
    npcs.json                   NPC 表（GM／agent／出圖／查證用）
    enemies.json                敵表（魔物與場景物件）
    locale.json                 畫面固有名詞與世界觀用語（Schema 1，前端讀取）
    text.json                   後端措辭與旁白語調（一桌一本）
    packs/<包id>/                模組層機制包（如 rules.core）
    assets/                     素材清單（Schema 2 瘦清單與分類索引）
    scenarios/<劇本>/            劇本層（定義；同一劇本可開多張桌）
        scenario.json board.json seats.json goals.json clues.json scene1.json
        config.example.json     桌的旋鈕底稿
        packs/<包id>/            劇本層機制包（如 hyakki_world 帶 monsters 表）
```

用法：複製到引擎倉的 `modules/<你的模組>/`，改文字與數字，再用自檢工具驗證：
```sh
tools/check_module.sh examples/hyakki_tower
tools/check_module.sh examples/hyakki_tower
```
起一張臨時桌試玩：
```sh
tools/check_module.sh --serve examples/hyakki_tower 18980
```
