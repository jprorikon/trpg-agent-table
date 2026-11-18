# 機制包（`packs/`）—— 邊界宣告 ＋ 啟用清單 ＋ 模式包

這一層是「插件化」的落點：**包只往各層「填資料 ＋ 宣告闸位」，不新增層、不含程式碼。**
（對照 `NewDemonDts`（新·電波大逃殺）的 `include/modules/`：它有 `module.inc.php` 標頭、
`modules.list.php` 啟用清單、`gameflow_*` 模式模組——我們抄它的**邊界**，不抄它的 `eval` 函數鏈。）

## 一個包長什麼樣

```
packs/<id>/
    pack.json     ← 必需：id／requires／optional／conflicts／provides／params
    text.json     ← 選用：這一包的人話（事件流文案；`provides.text_keys` 要把它列全）
    tables/*.json ← 選用：純資料表（係數、對照表、文案池）
```

**作品的措辭不放包**：引擎的句庫（`presentation.TEXT`）只帶中性預設；塔的旁白、補課行、喚醒訊息、
棋盤錯誤、MCP 工具描述、包宣告的動作名（`action.<id>.label`／`action.<id>.desc`）要換成作品的叫法，
寫在 **`<模組>/text.json`**（劇本層 `<劇本>/text.json` 再蓋一次；`_` 開頭的鍵是註記）。
`packs.text_bank_for()` 把「內核 ← 包的 text.json ← 模組 ← 劇本」疊成**這一桌的**句庫——一桌一本，
不進全域字典（hub 一個行程裝 N 張桌），也不進 `packs_hash`。鍵沿用內核那一套（新的鍵＝找工程加進 `TEXT`）。

```json
{
  "id": "wound", "name": "部位受傷", "version": 1,
  "requires": ["d20core"], "optional": ["board"], "conflicts": ["hp_single"],
  "provides": {
    "actions":    [{"id": "aim", "label": "瞄準", "desc": "…", "modes": ["turn"]}],
    "effects":    ["condition_set"],
    "events":     ["condition_set", "condition_tick"],
    "text_keys":  ["condition.poison_tick"],
    "state_keys": ["conditions"],
    "signals":    ["conditions_active"],
    "tables":     ["tables/conditions.json"],
    "defaults":   {"rules": {"hp": {"base": 5, "per_str": 1}}}
  },
  "params": {"poison_tick_dmg": 1}
}
```

`provides` 的九格：`actions／effects／events／text_keys／state_keys／signals／tables／mode／defaults`。

## 鐵律（`packs.py` ＋ `test_packs.py` 機械擋）

1. **包不含程式碼**：`provides` 裡的每一項都必須是**內核已實作**的東西——
   `effects`／`state_keys`／`signals` 對 `KERNEL_*` 池；`actions`／`events` 對**內核原始碼裡的字面**。
   要新機制＝先請工程做成「資料可配」，再回包裡宣告。**宣告了沒人實作的動作，玩家一送就是
   「什麼都沒發生的一手」——寧可在載入時就叫。**
2. **啟用清單要列全**：硬依賴不在清單裡 ⇒ 當場 `PackError`（不自動補，不然看不出這桌開了什麼）。
3. **一次一個節拍包**：`mode.turn`／`mode.scene` 只能選一個。
4. **凍進事件流**：啟動時把 `ids` ＋ `hash` 寫進 `table_created`。同一份 `events.jsonl`
   換一張啟用清單就會飄——不凍起來的話，`op=replay` 才發現，那時已經晚了。
5. 包分三層，同名時**後者覆蓋前者**：引擎層 `<repo>/packs/<id>/` → 模組層 `<模組>/packs/<id>/` → 劇本層 `<劇本>/packs/<id>/`。
   這一桌要改係數就改劇本層。
   **引擎層只放機制包，不放任何作品的內容**（角色、技能名、道具名、世界觀用語）——那些屬於模組 repo
   （例：示範模組 的規則書 `rules.core` 住 `trpg/module-hyakki-tower` 的 `packs/rules.core/`）。
6. **一個旋鈕一個權威**：機制的**預設值**住包（`provides.defaults`），**config 寫的一律贏**；
   兩個包把同一格寫成不同值 ⇒ 載入時就叫（`test_packs` S4）。

## 現役的包

| id | 內容 | 誰需要 |
|---|---|---|
| `d20core` | 屬性檢定／傷害／行動順序（內核本來就會的那些格子）＋ **`defaults.rules`＝minimal-d20 規則** ＋ **動作 DSL 兩個原語的 effect**（`attr_mod`／`resource_add`／`resource_set`，機制住 `dsl.py`） | 所有桌 |
| `board` | 地圖與塔（節點圖住 `board.json`）＋ `defaults.board`＝棋盤旋鈕 | 棋盤桌 |
| `semic` | 場景制／私密願望／碎片／爭搶＋ `defaults.semi`＝內容檔名 | 半合作桌 |
| `mode.turn` / `mode.scene` | 節拍（二選一） | 所有桌 |
| `wound` | **部位受傷／異常狀態**（4 部位＋4 狀態；表在 `tables/conditions.json`） | 想要傷勢系統的桌 |
| `example` | **樣板**（複製去改；預設不啟用） | 作者 |

作品層（`campaigns/<桌>/packs/`，預設不存在——要用才建）：

| id | 內容 | 誰需要 |
|---|---|---|
| `hyakki_world` | 示範模組才有的數值（塔／記憶經濟）＋**神器表** | 只有這一桌 |

## 怎麼啟用（`config.json`）

```json
"packs": {"enabled": ["d20core", "board", "semic", "mode.turn"], "params": {}}
```

也可以只寫清單：`"packs": ["d20core", "mode.scene"]`。**不寫＝legacy**（各層照舊走 config，行為一個字都不變）。

## 地塊表（`{"kind": "lands"}`）——落格效果變成資料

機制在內核（`lands.py`：`move`／`enter`／`ride` 落地時發一筆內部 `land_stop`，再跑表上的 `stop`），
**內容在包的表**（例：`<劇本>/packs/<id>/tables/lands.json`）。`board.json` 節點寫 `"land": "<id>"`。

```jsonc
{"kind": "lands",
 "rows": [{"id": "heal_spot", "stop": [{"heal": {"seat": "me", "bonus": 2}}], "params": {}},
          {"id": "poison_forest", "stop": [{"condition_set": {"seat": "me", "id": "poison", "turns": 3}}]}]}
```

* effect 的 `seat: "me"` ＝**剛落地的人**；表裡用到的 effect 必須是啟用包宣告過的（否則載入時就叫）。
* `pass` 欄位保留給「多格移動的經過」（目前引擎只有走一格，還沒消費）。
* 沒有 lands 表／節點沒有 `land` ⇒ 一個事件都不多發（legacy 零變更）。

## 條件表（`{"kind": "conditions"}`）——部位受傷／異常狀態

機制在內核（`conditions.py`：每回合結算、判定加減值、事件溯源），**內容**在包的表：

```json
{"kind": "conditions",
 "rows": [{"id": "poison", "name_jp": "毒", "name_cn": "中毒",
           "check_mod": -1, "attack_mod": 0,      // 進 d20 的 mod（負＝不利）
           "tick": {"damage": 1, "die": 4},       // 每回合扣 1＋1d4（省略 die＝固定值）
           "turns": 3,                             // 幾回合後自己好（省略＝不會自己好）
           "text_onset": "condition.poison_onset", // 句庫鍵（放這一包的 text.json）
           "text_tick": "condition.poison_tick",
           "text_end": "condition.poison_end",
           "note": "給 GM／自檢看的一句話"}]}
```

* 包要宣告：`effects: ["condition_set"]`、`events: ["condition_set","condition_tick"]`、
  `state_keys: ["conditions"]`、`tables: ["tables/conditions.json"]`（＋所有 `text_*` 鍵進 `text_keys`）。
* 用法三條路：**GM 當場**（`/api/gm op=condition`／MCP `table_gm op=condition`）、
  **劇本觸發**（effect `condition_set`，`seat` 省略＝`me`）、**AST 判據**（`me.conditions`）。
* 沒啟用帶條件表的包 ⇒ 整條機制 no-op（表是空的、事件一個都不多發、投影一字不變）。

## 機制預設值（`provides.defaults`）——機制常數的家

「換一張桌還會一樣」的常數（d20 規則、棋盤旋鈕、內容檔名…）屬於**機制**，放包的 `defaults`；
劇本的 `config.json` **只寫這一桌要改的**，其餘由包補。判據是：**config 有寫的一律贏**。

```json
"provides": {
  "defaults": {
    "rules": {"name": "minimal-d20", "attributes": ["str","agi","wil"],
              "hp": {"base": 8, "per_str": 2},
              "check": {"die": 20, "attr_multiplier": 2, "dc_default": 12},
              "defend_bonus": 3},
    "board": {"file": "board.json", "entrance_search_dc": 12},
    "semi":  {"goals_file": "goals.json", "clues_file": "clues.json"}
  }
}
```

* 可用的段（`packs.KERNEL_SECTIONS`）：`rules／board／semi／tower／memory／pacing／turn／view`；
  段名寫錯 ⇒ **載入時就叫**（不拖到跑團中間）。
* 合併是**深合併**：config 只寫 `rules.check.dc_default` ⇒ 同層的 `die`／`attr_multiplier` 還在。
* 節拍包的 `provides.mode.pacing`／`mode.turn` 也走同一條（它們就是 `defaults.pacing`／`defaults.turn`）。
* 預設值算進 `packs_hash` ⇒ 換預設＝換一張桌，重放分得出。
* ⚠ **沒啟用包、又沒把 config 寫滿** ⇒ 載入時當場 `RuleError`（機制沒有來源）；
  舊的手寫桌（config 全寫）不受影響。`tools/check_campaign.py` 會提醒「config 寫了跟包預設一樣的值」。

## 換整套規則（`conflicts`）——例如 `d20core` ↔ 類 DND

**拍板（設計者 2026-10-07）：換整套規則＝一個獨立的規則包 ＋ `conflicts` 宣告互斥**；
**不做「疊加式覆蓋」**（第二個規則包只改幾格就疊上去那種）。

* 規則包帶什麼：`provides.defaults.rules`（屬性表／HP 公式／檢定加值／傷害骰／防禦加值…）
  ＋它需要的 `provides.actions`（動作本身仍要內核已實作）。
* 互斥：`"conflicts": ["d20core"]`（**任一方宣告即可**）⇒ 兩個同啟時**載入就報錯**
  （`resolve()` 的衝突檢查，不是跑到一半才發現）。
* 只啟用其中一個 ⇒ 它的 `defaults.rules` **整組生效**（閘：`test_packs` S8–S10）。
* **為什麼不做疊加**：疊上去就看不出「這張桌到底跑哪套規則」，也違反「啟用清單要列全／一個旋鈕一個權威」。
  要局部改某一格 ⇒ 寫在 `config.json`（權威）或走作品層同名包。
* 兩個規則包撞同一格又沒宣告 `conflicts` ⇒ 載入時叫，錯誤訊息會直接指向這條規則。

## 作品層包（`<劇本>/packs/<id>/`）——這一桌專屬的內容

兩層 `packs/`：**引擎層**（`<repo>/packs/`，跨桌共用）＋**作品層**（`<劇本>/packs/`，這一桌專屬）。
**同名** ⇒ 作品層蓋掉引擎層（要改引擎層某個包的預設，就複製一份到作品層再改那一格）。
權威鏈：**`config.json`／劇本檔 ＞ 作品層包 ＞ 引擎層包**。

第一個真實用例：`campaigns/hyakki_tower/packs/hyakki_world/`

```
hyakki_world/
    pack.json              requires: [d20core, board, semic]
                           provides.defaults.tower／.memory（原 config.tower／config.memory）
                           provides.tables: ["tables/artifacts.json"]
    tables/artifacts.json  {"kind": "artifacts", "rows": [...]}  ← 原 board.json 的 artifact_table
```

`kind=artifacts` 的表由內核的 `board_load_tables()` 載入（與條件表同一套：**機制在內核、內容在包的表**）；
`board.json` 有寫一份 ⇒ 劇本檔贏（包不生效）。schema 壞（`rows` 空／缺 `items`）⇒ **載入時就叫**。

## 包怎麼用參數

包的 `params` 會在 AST 裡看得到（`pack.params.<鍵>`），所以「係數外部化」不必改程式：

```json
{"when": {"<": [{"var": "me.memory"}, {"var": "pack.params.low_memory"}]},
 "then": [{"emit": {"type": "say", "text": "記憶見底了"}}]}
```

## 驗收

```bash
PY=python3
$PY test_packs.py                              # 包本身的閘（解析／合併／實作核對／凍結）
$PY tools/check_campaign.py campaigns/<劇本>    # 作者入口（同一套判據）
```
