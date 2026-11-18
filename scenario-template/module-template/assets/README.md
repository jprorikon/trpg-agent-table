# 模組素材規範（assets/）

本目錄存放**模組級素材清單**（角色立繪、頭像、NPC、敵人、背景、圖標）。
對外靜態路徑為 `/assets/<模組id>/<相對路徑>`。

## 核心紀律

1. **圖檔本體不進版本控制**（`.gitignore` 中忽略圖檔二進位檔案）。
2. **只版管清單**：`assets/index.json`（瘦清單，Schema 2）與 `assets/index/<類>.json`（各分類清單）及本 README。
3. **投影只查別名（Aliases）**：前端與引擎透過別名獲取素材，不寫死檔案路徑。

## 清單結構（Schema 2）

```
assets/
  index.json                 ← 瘦清單（指向 parts，提供總計與來源資訊）
  index/
    portrait.json            ← 角色立繪清單（別名：portrait:<角色id> 或 portrait:<角色id>:<outfit>）
    avatar.json              ← 角色頭像清單（別名：avatar:<角色id>）
    npc.json                 ← NPC 立繪清單（別名：npc:<id>）
    enemy.json               ← 敵人/魔物立繪清單（別名：enemy:<id>）
    bg.json                  ← 場景背景清單（別名：bg:<節點id>）
    crowd.json               ← 路人/雜兵清單（別名：crowd:<類別>）
    icon.json                ← 圖標清單（別名：icon:item:<id>、icon:skill:<id>、icon:star:<n> 等）
```

## 別名命名約定

| 前綴 | 格式 | 說明 |
|---|---|---|
| `portrait:` | `portrait:<角色id>` 或 `portrait:<角色id>:<outfit>` | 角色立繪。有不同服裝時透過 `<outfit>` 區分 |
| `avatar:` | `avatar:<角色id>` | 角色頭像 |
| `npc:` | `npc:<id>` | 對應 `npcs.json` 中的 NPC |
| `enemy:` | `enemy:<id>` | 對應 `enemies.json` 中的敵人/魔物 |
| `bg:` | `bg:<節點id>` | 對應 `board.json` 中的地圖節點 id |
| `icon:` | `icon:item:<id>`, `icon:skill:<id>`, `icon:star:<n>` | 道具、技能、星等、狀態圖標 |
