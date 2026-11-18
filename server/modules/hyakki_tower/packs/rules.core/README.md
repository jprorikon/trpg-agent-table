# `rules.core`——示範模組 規則書（技能／經驗／道具／星等；原創）

⚠ **啟用這一包時要一起啟用 `wound`**：技能表（`tables/skills.json`）的幾招用到 `condition_set`
（點燃／混亂那種），沒啟用帶條件表的包 ⇒ **載入時當場叫**（訊息會直接告訴你要加哪一個）。

> 一本「規則書」型機制包（引擎層）：**機制在內核**（`skills.py` 的 cast 檢定鏈、`advancement.py`
> 的經驗／等級），**這一包只帶內容**——技能表（8 條）與等級表（5 級）。
> 原創（不是抄數值、也不是抄文本）。
> 設計與落地記：`trpg/module-hyakki-tower` 的 `packs/rules.core/README.md`（引擎機制：`trpg/server` 的 `skills.py`／`advancement.py`／`items.py`）。
> 📦 **這一份是百鬼塔自己的副本**（2026-10-19 引擎 repo 不再帶作品內容時複製過來；內容與 id 與原版相同 ⇒ 既有桌的 `packs_hash` 不變）。
> 要改成百鬼塔自己的味道（技能名、道具名、星等稱號）就直接改這一份——**會改到 `packs_hash`，只影響新開的局**。

## 1. 怎麼啟用

```jsonc
// campaigns/<桌>/config.json
"packs": { "enabled": ["d20core", "board", "semic", "mode.turn", "wound", "rules.core"] }
```

- `wound` 不是硬依賴，但**技能表用到 `condition_set`**（`hyousou_no_sho`／`kagaribi`／`meisou_no_me`）
  ⇒ 沒啟用帶條件表的包時**載入就報錯**（不會跑一半才發現）。
- 啟用後多一個動作 `cast`（`provides.actions` 宣告；config 的動作目錄不必改，config 只管顯示順序與文案）。

## 2. 屬性表（**這一桌的規則**，寫在 `config.json` 的 `rules`）

四個屬性（`str`／`agi`／`wil` 保留原名是**故意的**：現役事件流與 roster 已經用這三個名字，
改名＝改歷史語意；`luck` 是新增的第四項）：

| key | 名 | 管什麼 |
|---|---|---|
| `str` | **剛** | 體能・近戰傷害・HP 上限（`hp.per_str`） |
| `agi` | **技** | 機動・瞄準・閃避（`move`／`fight`／`attack`） |
| `wil` | **心** | 意志・魔法・記憶・祈願（`search`／爭搶／法術） |
| `luck` | **運** | 貨幣掉落（`+max(0, luck − attr_default)`）、祈願箱的運氣 |

元素（Ignis／Ventus／Aqua／Umbra／Terra／Lux）與風格（Collector／Adventurous／Creative／Steady／
Inquisitive）**不是數值屬性**，是**角色標籤＋技能分類**（給 GM 敘事口徑用；相剋表屬難度設計，未做）。

```jsonc
"rules": { "attributes": ["str", "agi", "wil", "luck"] }   // 其餘留給 d20core 的預設補
```
⚠ 屬性表**住 config**（config 是權威）：`rules.core` 不提供 `defaults.rules`——它若也宣告
`rules.attributes` 就會和 `d20core` 撞同一格（同段同鍵不同值＝載入時叫）。**換整套規則**要走
`conflicts` 的獨立規則包（見 `packs/README.md`「換整套規則」）。

## 3. 兩張表（改數值＝改這裡，不改程式）

- `tables/skills.json`（`kind=skills`）——每條：`attr`（用哪個屬性檢定）／`prof`（熟練加值）／
  `dc`（難度，GM 可覆寫）／`lvl`（等級需求）／`target`（foe／self／none）／`cost`（先扣，不足＝拒）／
  `effects`（同一套 effect 語意）。
- `tables/levels.json`（`kind=levels`）——`need`＝累計經驗門檻；`gains`＝`max_hp`（加上限並補血）／
  `attrs`（**基準屬性**）／`resources`（通用資源，例如 `mana`）。第 1 級＝開局那一列。

## 4. 誰在吃（驗收看到的東西）

| 東西 | 在哪裡看得到 |
|---|---|
| 技能清單（含 `known`） | `view.you.skills`（只有這一席真的有等級紀錄時才出現） |
| 等級／經驗／下一級門檻 | `view.you.level`／`.exp`／`.next_need` |
| 放招 | `POST /api/act {"action":"cast","skill":"kagaribi","target":"p2"}`（MCP `table_act` 同欄位） |
| 給經驗 | `POST /api/gm {"op":"exp","target":"p1","amount":30}`（MCP `table_gm op=exp`） |
| 擊破小怪給經驗 | `params.exp_per_star_kill`（這包給 6）／BOSS `params.exp_per_boss_defeat`（20） |

## 5. 閘

`test_skills.py`（K1–K9）＋`test_advancement.py`（L1–L7）＋`test_packs.py`（P13／P23／P24／P27b／P27c
跟著機制更新）；沒啟用本包＝**legacy 零變更**（動作目錄沒有 `cast`、`exp`／`level` 永遠空的、
投影不多任何欄位、join 不多發事件）。
