# 劇本（Scenario）怎麼交進來——入口與格式約束

> 對象：寫劇本的人（不用碰程式）。目標＝**只放檔案，不改一行程式**就能開一張新桌。
> 現成的完整樣本：`modules/hyakki_tower/scenarios/sannenme_no_onibarai/`（照它抄最保險）。
> ⚠ 2026-10-07 起是**三層**（模組 → 劇本 → 桌）：桌住 `modules/<模組>/scenarios/<劇本>/`；
> **模組自己命名**＝`modules/<模組>/module.json`（`{"name": "顯示名"}`——大廳／app 的桌清單標題用它；
> 沒寫就退回目錄名。`WORLD.md` 是散文，不當機器可讀來源）；
> **角色的類由模組定**＝`modules/<模組>/characters.json`（每個角色一個 ID ＋ `class`／`name`／`note`）——
> 帳號進場＝在**模組**底下建立／沿用該角色的**實例**（帳號 × 模組 × 角色ID）；`seats.json` 只決定
> 這一局**哪些角色參與、坐哪一席**；
> 劇本規則資料的檔名是 **`scenario.json`**（舊名 `module.json` 仍讀得到，但會留一行 warn）。
>
> **本檔是正本**。給外部作者（拿不到引擎倉的人）的那一份＝骨架倉 **`trpg/scenario-template`** 的
> `SCENARIO_FORMAT.md`（**生成物**，由 `tools/sync_scenario_format.sh` 從這裡同步；改格式＝改這裡再同步）。

---

## 1. 入口：一個目錄

```
modules/<模組 id>/scenarios/<劇本 id>/          ← 一張桌（桌 id 讀 config.table_id，與目錄名解耦）
    config.json        ← 必需：桌子怎麼跑（端口、種子、規則、節奏、額度…）
    seats.json         ← 必需：有哪些席位、誰演誰
    goals.json         ← 必需：私密願望
    clues.json         ← 必需：碎片與它支持的結論
    board.json         ← 選用：棋盤／塔（不給＝抽象桌）
    module.json        ← 選用：**規則資料**（願望判據／表層判據／結局／觸發／難度）
    WORLD.md           ← 建議：世界觀（給 GM 與 agent 讀，引擎不看）
    GM.md / TABLE.md   ← 建議：GM 守則／桌面說明（引擎不看）
    packs/<id>/        ← 選用：**劇本層包**（這一齣戲專屬的內容／係數；同名蓋掉模組層與引擎層，見 2.1.1）
    runs/<session>/    ← **不要手動建立**：桌子自己寫事件流（`events.jsonl`；一局一檔＝一個副本，
                          `runs/current` 指到現役那一局）
```

**怎麼交給我**：把這個目錄（或壓縮包）放在 部署機上、或用任何方式發給我即可。我會做三件事：
① 跑自檢 `python3 tools/check_campaign.py modules/<模組>/scenarios/<劇本>`；② 在 `/tmp` 起一張臨時桌試跑（不碰現役桌）；
③ 把結果（能不能起、哪裡卡）回報給你，再決定什麼時候起真桌。

**起真桌**：`cd workspace/trpg && ./deploy/start.sh modules/<模組>/scenarios/<劇本>`（改 `config.json`／`scenario.json` 後要重啟才生效；
網頁與前端不用重啟）。`<劇本 id>` 建議＝目錄名，也要寫在 `config.table_id`。

**邊界（什麼情況才需要改程式）**：
- ✅ 你改：文字、數字、節點圖、願望／碎片、判據（AST）、難度、結局、觸發。
- ❌ 要工程：**新的機制**（例如「天氣系統」）、新的 effect 種類、新的事件型別、新的額度維度。
  這種情況告訴我，我把它做成「資料可配」再加進 `module.json` 的詞彙表。

---

## 2. 每個檔要什麼

### 2.1 `config.json`（必需）
照 `modules/<模組>/scenarios/<劇本>/config.example.json` 複製來改。**必填**：

| 區塊 | 欄位 | 說明 |
|---|---|---|
| 根 | `table_id` / `scenario` / `seed` | `seed` 決定骰子與隨機投放（**換 seed＝換一整局的手氣**） |
| 根 | `port` / `bind` | 一張桌一個端口（同期只能一個行程寫同一局） |
| `rules` | **只寫 `actions` 就夠**（其他欄位由 `packs/d20core` 補，見 2.1.1） | `actions`＝**這張桌顯示哪些動作、什麼順序**，同時驅動「規則閘」與「工具列舉」；每條可以是 `"move"` 或 `{id,label,desc,modes}` |
| `turn` | `seconds` / `policy_on_timeout` / `max_waits` / `wait_extra_seconds` | 回合死線與超時策略（`wait`／`pass`／`default_act`） |
| `pacing` | `mode`（`scene`／`turn`）/ `actions_per_scene` / `says_per_scene` | 額度**人與 agent 同一套**（只限 agent 會失衡） |
| `pacing` | `free_actions` / `auto_close_scene` | **玩法循環旋鈕**（2026-10-20）：`free_actions: ["move","ride"]` ＝這些動作**不佔行動格、也不結束回合**；`auto_close_scene: true` ＝場景制下「全員額度用完」就自動收景。兩者預設都關，見 2.1.2 |
| `packs` | `enabled`（清單）/ `params` | **機制包**：這一桌開了哪些機制（見 2.7）。不寫＝legacy（各層照舊讀 config） |
| `auth` | `gm_token` | GM 的敘事／投放／開收景用（**不進 repo**：實檔 `config.json` 已被 `.gitignore`） |
| 選用 | `board` / `tower` / `memory` / `semi` / `kick` / `wake` / `view` | 有棋盤／半合作／喚醒才需要；`board`／`semi` 的**預設值也由包補**（`packs/board`／`packs/semic`），config 只寫要改的 |

`rules.actions` 的 `modes: ["turn"]`＝只在回合制出現（例：`end_turn`）；`pass` 是場景制的動作。

### 2.1.1 `packs`（機制包）——這一桌開了哪些機制

```json
"packs": {"enabled": ["d20core", "board", "semic", "mode.turn"], "params": {}}
```

現役的包（`packs/`）：`d20core`（底層 d20 規則，必開）／`board`（棋盤與塔）／
`semic`（半合作）／`mode.turn`・`mode.scene`（節拍，**二選一**）／
`wound`（**部位受傷／異常狀態**：4 部位＋4 狀態，見 4.1；另帶**救起**規則，見 4.5）／
`death`（**死亡與送還**：HP ≤ 0 ⇒ 送回最近的城市 ＋ 缺席 N 回合，見 4.10）／
`example`（樣板）。
**模組層的包**（住模組 repo 的 `<模組>/packs/<id>/`，同模組的每一齣劇本共用；引擎 repo 不放作品內容）：
例 `rules.core`（**規則書包**：技能表＋等級表 ⇒ 技能／法術與經驗／等級，見 4.2；**屬性表住 config**）——
`hyakki_tower` 與 `hyakki_tower` 各有一份。包的查找順序：引擎層 → 模組層 → 劇本層（同名時後者覆蓋前者）。
完整規格與紀律：**`packs/README.md`**；落地記與下一步：`docs/PLAN_module_packs.md`。

包也可以帶**表**（`tables/*.json`，`kind` 決定誰來載）：`conditions`／`rescue`（`wound`）／
`skills`・`levels`・`items`・`stars`（規則書包）／`monsters`・`shop`（作品層包；★4 敵人與按節點賣貨）／
`artifacts`（作品層包；祈願箱的階，**tier 就是列索引**）——分別見 4.1～4.7／4.10 與 `packs/README.md`。

**你要知道的五件事**（其他交給自檢器）：

1. **不寫 `packs` ＝ legacy**：各層照舊讀 `config.json`，行為完全不變（舊劇本不用動）。
2. **包不能發明機制**：`provides` 只能宣告內核**已實作**的動作／效果／事件／state 欄位／指標／句庫鍵。
   宣告了沒實作的東西 ⇒ **自檢與起桌都會當場叫**（不會變成「送了動作卻什麼都沒發生」）。
3. **`config` 是旋鈕的權威**：節拍看 `pacing.mode`（`mode.*` 包只在 config 沒寫時當預設）；
   `pacing`／`turn` 的值 config 有寫就以 config 為準，包只補缺口。兩邊寫不一樣 ⇒ 自檢器提醒。
4. **啟用清單會凍進事件流**（`table_created` 的 `packs`／`packs_hash`）：以後換清單，
   老局的回放仍分得出是「哪一組機制」跑出來的。預設值也算進 hash。
5. **機制常數寫在包、config 只寫覆寫**（`provides.defaults`，見 `packs/README.md`）：
   深度合併、config 有寫的一律贏。⇒ 例如只想把防禦加成從 3 改成 4，就寫
   `"rules": {"defend_bonus": 4}`，其餘欄位由 `d20core` 補。
   ⚠ 沒啟用包**又**沒把 config 寫滿 ⇒ 起桌當場 `RuleError`（機制沒有來源）。

包也可以帶**係數**（`params`），劇本用 AST 取它（係數外部化，不必改程式）：

```json
{"when": {"<": [{"var": "me.memory"}, {"var": "pack.params.low_memory"}]},
 "then": [{"emit": {"type": "say", "text": "記憶見底了"}}]}
```

要**自己寫一個包**（放 `<劇本>/packs/<id>/pack.json`；整個模組共用的放 `<模組>/packs/<id>/pack.json`）：先複製 `packs/example/`。
⚠ 想做「內核還沒有」的新機制（部位受傷、法術、先攻制…）＝**先找工程**：那不是寫 json，
是內核要先把機制做成「資料可配」（先例：`difficulty_set`／`fact_set` 都是這樣進來的）。

### 2.1.2 玩法循環旋鈕（2026-10-20）

一局的牆鐘 ≈ **手數 × 每手牆鐘**；手數 = 景數 × 參戰席位 × 每景行動格。實測兩張現役桌之後，
把「一局為什麼會拖」拆成三個**只改 config** 就能動的旋鈕（細節與量測見 `docs/GAMEPLAY_LOOP.md`）：

| 旋鈕 | 寫法 | 意思 |
|---|---|---|
| 免費動作 | `pacing.free_actions: ["move","ride","enter","exit_tower"]` | 這些動作**不佔行動格、也不結束回合**（回合制下可以連走幾格再做事）。實測移動＋升降機吃掉 47.8% 的手 |
| 自動收景 | `pacing.auto_close_scene: true` | 場景制：**全員行動額度用完且沒有待揭曉宣言** ⇒ 自動 `scene_close`。回合制：**一輪走完**（末席結束回合）⇒ 自動收景，並**叫 GM 來歸結本輪＋開下一景**。⚠ 引擎**只自動收、不自動開**：下一景永遠由 GM 開（開景＝敘事權） |
| 逐席節拍 | `turn.seconds_by_kind: {"agent": 90}`、`max_waits_by_kind`、`wait_extra_seconds_by_kind`、`policy_on_timeout_by_kind` | 依席位種類（`human`／`agent`／`npc`）覆寫回合秒數與等待次數。實測超時率 57%，人與 agent 同一套秒數會互相拖累 |

三個旋鈕**預設都不啟用**（不寫＝行為與以前完全一樣）。`turn_cfg_for(seat)` 是逐席解析的入口。

### 2.2 `board.json`（選用；沒有＝抽象桌）
```jsonc
{
  "board_name": "示範模組",
  "start_node": "teckan",                 // 開局大家站哪
  "nodes": [
    {"id": "teckan", "jp": "鉄環市", "cn": "铁环市", "type": "city",
     "danger": 0, "tags": ["shop", "rest"], "flavor": "……",
     "monolith": "hyakki",            // 這個節點的塔（入口節點要自指，否則「站在塔口進不去」）
     "edges": [{"to": "w_teckan", "kind": "road"},
               {"to": "hyakki", "kind": "entrance"}]}
  ],
  "roster":  [{"seat": "char_a", "name": "カガリ", "attrs": {"str": 1, "agi": 5, "wil": 3}}],
  "artifact_table": [{"name": "願い星のかけら", "tier": 4}]   // 也可以搬進包的表，見下
}
```
⚠ **機制旋鈕不要寫在 `board.json`**（例如 `entrance_search_dc`）：`board.py` 只讀
`config.json` 的 `board` 段（那一格的預設由 `packs/board` 提供）；寫在這裡的那一份**沒人讀**。
`artifact_table` 則可以搬進**作品層包**的表
（`<劇本>/packs/<id>/tables/artifacts.json`，`{"kind": "artifacts", "rows": [...]}`；
`board.json` 有寫就輪不到它）——現役 `hyakki_tower` 就是這樣放的。
- `type`：`city`／`wild`／`monolith`／`floor`（例）；`depth` 給樓層（**最深的一層**＝「登頂」判據用）。
- `edges[].kind`：`road`／`wild`／`entrance`／`elevator`／`exit`——**有向**，相鄰判定看它（別靠對稱鄰接表）。
- 邊指向不存在的節點＝起桌就報錯（自檢器也會先叫）。
- `roster[].seat` 是**角色名**；席位要拿模板，`seats.json` 的 `character` 要跟它對上。

### 2.3 `goals.json`（必需）——私密願望
```jsonc
{"goals": [
  {"id": "g_cat", "owner_character": "char_a", "timing": "any",
   "title": "迷い猫を連れて帰る", "text": "（本人看得到的全文，會出現在他的卡上）"}
]}
```
- `id` 之後要跟 `module.json` 的 `requirements` 對上（判據）——**只寫在這裡不寫判據＝永遠不會達成**（自檢器會提醒）。
- 舊寫法：也可以在這裡寫 `required: {claims, min_fragment_sources, at_nodes, first_pray_at, show_to, min_shown_fragments}`（引擎的 legacy 分支），但**新劇本請用 `module.json` 的 AST**。

### 2.4 `clues.json`（必需）——碎片與結論
```jsonc
{"claims": [{"id": "c_maou_here", "text": "「マオウ」はここにいる"}],
 "fragments": [{"id": "f1", "pool": "tower", "weight": 2, "supports": ["c_maou_here"],
                "text": "軍旗の残骸。布に書かれた文字は「マオウ」——"}],
 "drop_policy": "…"}
```
- `text` 是**私密正文**（只有持有人看得到；GM 只要洩漏會被閘門擋）。
- `supports` 指向 `claims` 的 id：碎片＝證據，`claims`＝結論（願望判據比對的是結論）。
- **`drop_policy` 現在真的會被讀**（2026-10-20）：
  ```jsonc
  {"on_pray_to": "anyone_but_requester",   // 祈願掉的碎片不落在提出者手上
   "prefer_unheld": true,                  // 優先給「手上還沒有碎片」的人（資訊才會流動）
   "count_per_pray": 1}                    // 一次祈願投幾枚（1–3）
  ```
  另外引擎會**按欠帳挑碎片**：哪一條結論出土得少，就先給支持它的那一枚（同分時 `weight` 大的先出）。
  以前是純隨機 ⇒ 實測一局 353 手只出土 8 枚、某幾條「願望必要結論」整局沒出土過。

### 2.5 `seats.json`（必需）——誰坐在哪
```jsonc
{"composition": {"gm": 1, "npc": 1, "players": 4},
 "seats": [{"seat": "gm", "kind": "gm", "name": "GM"},
           {"seat": "npc1", "kind": "npc", "character": "tsurugi", "start_node": "teckan"},
           {"seat": "p1", "kind": "human", "character": "char_a", "start_node": "teckan", "goal": "g_cat"},
           {"seat": "p3", "kind": "agent", "character": "homura", "start_node": "teckan", "goal": "g_company"}]}
```
- `kind`：`gm`／`npc`／`human`／`agent`。GM 不入行動順序；NPC 由 GM 代演（不被踢）。
- `goal` 把席位綁到 `goals.json` 的 id（**綁了才會判達成**）。

### 2.6 `module.json`（選用，但**強烈建議**）——規則資料
```jsonc
{
  "meta": {"id": "my_scenario", "rules_version": "1.0"},
  "entities": {                                   // 實體（NPC…）的專屬知識
    "tsurugi": {"kind": "npc", "display": "ツルギ",
               "knowledge": ["塔の店の品揃えと値段", "…"],   // 牠「可以知道」的範圍
               "note": "只說以上範圍；被問到範圍外就含糊過去。"}
  },
  "requirements": {                               // 願望判據（AST）
    "g_cat": {"doc": "給人看的說明",
              "expr": {"and": [{"in": ["c_maou_here", {"var": "me.claims"}]},
                               {"in": [{"var": "me.node"}, ["hyakki_f1", "hyakki_f2"]]}]}}
  },
  "surface": {                                    // 表層共同目標的判據
    "tower_cleared": {"expr": {"exists": "world.pray_first.hyakki_f4"}},
    "reached_top":   {"expr": {"in": ["hyakki_f5",
                                {"pluck": [{"values": {"var": "seats"}}, "node"]}]}}
  },
  "difficulty_presets": {                          // 難度＝L3 的獎懲手段（DC 與行動格）★運行時糾偏
    "relaxed": {"dc_mod": -1, "act_mod": 1},       //   賞：檢定−1、每回合多一格
    "normal":  {"dc_mod": 0,  "act_mod": 0},
    "tight":   {"dc_mod": 1,  "act_mod": -1}},     //   罰：檢定+1、每回合少一格
  "starting_difficulty": {                         // ★開局難度（建桌時選，一局不變）
    "default": "normal",                           //   不指定／劇本沒這段 ⇒ 用 default（都沒有＝legacy）
    "levels": {
      "easy":   {"label": "轻松", "label_jp": "ゆるめ",
                 "doc": "一句話說明這一檔改了什麼（大廳選單會顯示）",
                 "rules": {"death": {"skip_turns": 1, "hp_restore": 2, "clear_conditions": true},
                           "levels": {"exp_base": 30, "exp_step": 25}},
                 "mods": {"monster_dc": -1, "exp_mult": 1.2, "coins_mult": 1.2, "price_mult": 0.8}},
      "normal": {"label": "标准", "label_jp": "標準", "doc": "…",
                 "rules": {"death": {"skip_turns": 2, "hp_restore": 1},
                           "levels": {"exp_base": 40, "exp_step": 30}},
                 "mods": {"monster_dc": 0, "exp_mult": 1.0, "coins_mult": 1.0, "price_mult": 1.0}},
      "hard":   {"label": "严酷", "label_jp": "きびしめ", "doc": "…",
                 "rules": {"death": {"skip_turns": 3, "drop_coins": 0.5, "lost_memory": 8},
                           "levels": {"exp_base": 50, "exp_step": 40}},
                 "mods": {"monster_dc": 1, "exp_mult": 0.85, "coins_mult": 0.85, "price_mult": 1.3}},
    },
  },
  "director": {                                    // L3 導演：超出劇本預期時的糾正力量
    "track": true,                                 //   false＝關掉
    "window": 5,                                   //   「最近幾手」的窗口
    "progress_on": ["mob_killed", "entered_tower", "boss_defeated", "wish_done"],
    "policy": [                                    //   第一條命中的就是提案（與現行相同＝不提）
      {"when": {">=": [{"var": "signals.stall_hands"}, 10]}, "preset": "tight",
       "why": "卡了 {stall_hands} 手沒有推進——收緊"},
      {"when": true, "preset": "normal", "why": "回到標準"}]},
  "clocks": {                                    // ★ 世界自己的時鐘（2026-10-20）
    "night": {"max": 6, "label": "夜"},            //   沒宣告的時鐘名 ⇒ 觸發／effect 會用不到
    "ritual": {"max": 4, "label": "儀式"}
  },
  "endings": [{"id": "e_win", "kind": "win", "when": {"==": [{"var": "world.event.type"},
                                                             "boss_defeated"]}}],
  "triggers": [{"id": "t_greet", "on": "say", "once": true,
                "when": {"==": [{"var": "world.event.text"}, "hi"]},
                "then": [{"fact_set": {"key": "greeted", "value": true}}]}]
}
```

---

## 3. AST：判據怎麼寫

**沒有 AST 的東西**：函式、迴圈、賦值、時間、隨機。只有「查值 → 比較 → 邏輯 → 算術」。

### 3.1 語法

| 寫法 | 意思 |
|---|---|
| `7`／`"字"`／`true`／`null`／`[1,2]` | 字面值 |
| `{"var": "me.node"}` | 查值（點號路徑；查不到回 `null`） |
| `{"exists": "world.pray_first.hyakki_f4"}` | 這條路徑有值嗎 |
| `{"get": [<物件>, <鍵>, <預設>]}` | 取欄位（缺了用預設） |
| `{"and": [...]}`／`{"or": [...]}`／`{"not": x}` | 邏輯（**短路**） |
| `{"==": [a,b]}`／`!=`／`<`／`<=`／`>`／`>=` | 比較（**任一邊是 `null` ⇒ 一律 false**，不會炸） |
| `{"in": [x, 集合]}`／`{"nin": [x, 集合]}`／`{"contains": [字串或陣列, 子字串]}` | 成員 |
| `{"+": [...]}`／`-`／`*`／`/`／`%`／`{"min":[..]}`／`{"max":[..]}`／`{"abs":x}`／`{"floor":x}`／`{"round":[x,位數]}` | 算術 |
| `{"len": x}`／`{"count": x}`／`{"sum": x}`／`{"any": x}`／`{"all": x}`／`{"coalesce":[...]}`／`{"concat":[...]}`／`{"select":[條件,真,假]}` | 聚合／挑選 |
| `{"values": x}`／`{"keys": x}`／`{"pluck": [<陣列>, "<每個元素的欄位>"]}` | 容器走訪 |
| 節點上的 `"label"`／`"doc"` | 只是註解（不影響求值） |
| 節點上的 `"why"` | **失敗時**這條要告訴玩家什麼（值可以是表達式，會求值）→ 顯示在 `your_goal.progress` |

### 3.2 可以用哪些欄位（查值詞彙表）

| 路徑 | 內容 |
|---|---|
| `me.seat` / `me.node` / `me.memory` / `me.hp` | 這個席位（誰在判＝誰就是 `me`） |
| `me.claims` | 我「有效持有」的結論（自己手上的碎片 ＋ 別人向我出示過的，它們的 `supports`） |
| `me.fragment_sources` | 向我出示過碎片的人（去重、不含自己） |
| `me.fragments` | 我手上的碎片 id |
| `me.shown_to.<席位>` | 我向那個席位出示過的碎片數 |
| `me.goal` / `me.goal_achieved` / `me.actions_used` | 我的願望 id／是否已達成／這景用了幾次額度 |
| `world.pray_first.<節點>` | 那個節點第一個祈願的人是誰（＝先手） |
| `world.frag_holder` / `world.fragments_out` | 碎片在誰手上（`{碎片id: 席位}`）／出土幾枚 |
| `world.scene_no` / `world.settled` / `world.event_type` / `world.event` | 第幾景／結算過了嗎／**觸發到我的那筆事件** |
| `world.up` / `world.down` | 還站著／已倒下的人 |
| `seats.<席位>.<欄位>` / `seats` | 全桌（`seat`／`name`／`kind`／`node`／`memory`／`hp`／`down`／`star_coins`／`artifacts`） |
| `facts.<鍵>` | 劇本自己寫的事實（見 `fact_set`） |
| `clocks.<名>` | **時鐘**的現在值（0…`max`；由 `clock_tick`／`clock_set` 事件折出，見 2.6） |
| `turn.seat` / `turn.index` / `round_no` / `scene.no` / `scene.closed` | 節奏 |
| `difficulty` / `ended` | 目前難度預設名／已判定的結局 id |
| `pack.ids` / `pack.params.<鍵>` / `pack.mode` | 這張桌啟用的機制包與它們帶來的係數（見 2.1.1） |
| `me.conditions` / `seats.<席位>.conditions` | 身上的條件 id 陣列（部位受傷／異常狀態；見 4.1） |
| `me.attr_mods` / `seats.<席位>.attr_mods` | 屬性加值（`{屬性: 加值}`；見 4.0） |
| `me.resources` / `seats.<席位>.resources` | 通用資源（`{名: 值}`；見 4.0） |

**L3 導演看得到的是另一組指標**（`signals.*`，不是上面那些——導演不讀 state）：

| 指標 | 意思 |
|---|---|
| `signals.hands` | 打到第幾手（`turn_ended` 累計） |
| `signals.progress` | 主線推進累計次數（`progress_on` 那些事件） |
| `signals.stall_hands` | **距上次推進幾手**（「卡住」的距離） |
| `signals.progress_recent` | 最近 `window` 手裡推進了幾次 |
| `signals.recent_idle` | 最近 `window` 手裡有幾手空手／超時 |
| `signals.idle_hands` / `idle_streak` | 空手／超時的累計與連續 |
| `signals.move_lag` | 距上次真正換地點幾手 |
| `signals.rounds` / `scene_no` / `window` | 輪數／景號／窗口大小 |
| `signals.players` / `agents` / `down` | 玩家數／agent 數／倒下人數 |

⚠ **政策請用「距離／窗口」指標**（`stall_hands`／`recent_idle`／`progress_recent`）：累計數
（`idle_hands`／`progress`）在長局一定飽和，拿它當門檻會一開局就誤判。
⚠ 另一個坑：`recent_idle` 吃的是**超時／pass**——而超時常常是**技術性**的（agent 慢、掉線），
拿它罰玩家不公平。怕這個就別把超時寫進政策（現役 `hyakki_tower` 就是**只留「主線停滯」**那條）。

### 3.3 例子

```jsonc
// 「在塔裡任一層」                      {"in": [{"var": "me.node"}, ["hyakki_f1","hyakki_f2"]]}
// 「至少兩個不同的人向我出示過碎片」    {">=": [{"len": {"var": "me.fragment_sources"}}, 2]}
// 「我是第一個在 f4 祈願的人」          {"==": [{"var": "world.pray_first.hyakki_f4"}, {"var": "me.seat"}]}
// 「我記憶剩不到 20」                   {"<": [{"coalesce": [{"var": "me.memory"}, 0]}, 20]}
// 「有人站上最頂」                      {"in": ["hyakki_f5", {"pluck": [{"values": {"var": "seats"}}, "node"]}]}
// 「我達成願望了而且擲過骰」            {"and": [{"var": "me.goal_achieved"}, {">": [{"var": "round_no"}, 3]}]}
```
失敗說明：
```jsonc
{"in": ["c_maou_here", {"var": "me.claims"}],
 "why": {"need_claims": ["c_maou_here"], "you_have_claims": {"var": "me.claims"}}}
```

---

## 4. 觸發與結局

```jsonc
{"triggers": [
  {"id": "t_名字要唯一",
   "on": "turn_ended",              // 事件型別（可寫陣列）；不寫＝每一筆事件都看（貴，慎用）
   "when": {…AST…},                 // 可省（＝on 命中就跑）
   "then": [ …effects… ],           // 至少一個
   "once": true}                    // 可省：這一局只跑一次
]}
```
**語意**：
- **跑到定點**：觸發產生的新事件（`emit`／`fact_set`…）會再排一輪，直到沒有新事件（上限 32 輪，防互觸迴圈）。
- **事件溯源**：effect 只能**發事件**，狀態一律由事件折出來 ⇒ `op=replay` 永遠一致（沒有任何 effect 能直接改 state）。
- 觸發在 **L0 內核**發事件的那一瞬間跑（最外層才跑，遞迴被擋住）。
- 結局（`endings`）也一樣：每筆事件後判一次，**先搶先贏**（列表順序），判到就記 `state.ended`。

**effect 白名單**（目前**十四種**；後九種要這張桌啟用了**宣告它們的包**——`attr_mod`／`resource_add`／
`resource_set` 由 `d20core` 宣告，`damage`／`heal`／`item_grant`／`item_take`／`star_set`／`growth_gain`
由 `rules.core` 之類的「規則書包」宣告）：
| effect | 作用 |
|---|---|
| `{"emit": {"type": "…", "public": true, "text_key": "句庫鍵", "facts": {…}, …}}` | 發一筆事件；`text_key` 由 **L4 句庫**產字（`facts` 是給它填的值） |
| `{"fact_set": {"key": "…", "value": <值或 AST>}}` | 寫一條事實（`facts.<key>`，之後 AST 查得到） |
| `{"ending": {"id": "…", "kind": "win|lose|neutral"}}` | 判結局（先搶先贏）。**判到之後這一局就結束**：時間軸停住（不再倒數／不再換手／不再叫醒）、`act` 被擋，但 **say／tell 照常**；投影的 `ended` 會帶結局 id（前端據此顯示「本局已結束」） |
| `{"difficulty_set": {"name": "…"}}` | 切難度預設（名字要在 `difficulty_presets` 裡） |

### 泛用槽位（`slots`）：引擎不寫死作品的詞

引擎的棋盤機制只認**四個抽象槽位**，實際字串由模組決定（`module.json`，劇本層 `scenario.json` 可再蓋一層）：

| 槽位 | 語意 | 沒宣告時的預設 |
|---|---|---|
| `mob` | 節點上的小怪（數量欄位＋擊破事件） | `mobs`／`mob_killed` |
| `coin` | 這一桌的貨幣（席位錢包） | `star_coins` |
| `lift` | 層與層之間的載具（`board.json` 的節點 `type` 與換層事件） | `elevator` |
| `sanctum` | 記憶見底時送回的起點房間 | `white_room` |

```jsonc
// modules/<模組>/module.json（或 scenarios/<劇本>/scenario.json，近的贏）
"slots": {
  "mob":     {"key": "shades",  "event": "shade_slain", "name_jp": "影",   "name_cn": "影"},
  "coin":    {"key": "gold",    "name_jp": "金貨", "name_cn": "金币"},
  "lift":    {"key": "lift",    "event": "lift",   "name_jp": "昇降筒", "name_cn": "升降筒"},
  "sanctum": {"key": "chapel",  "event": "chapel", "name_jp": "礼拝堂", "name_cn": "礼拜堂"}
}
```

規則：
* **沒寫 `slots` ⇒ 全部用預設**（舊模組一字不變）。
* 宣告了就要合法（`key` 非空、`event` 非空、不認識的槽位／欄位當場叫）；`coin` 不需要 `event`（走 `coin_changed`／`shop_buy`）。
* **事件流是 append-only**：換了名字之後，引擎仍認得**預設名**（別名表）⇒ 舊 `runs/*/events.jsonl` 照樣回放。
* **投影同時給兩種鍵**：抽象名（`you.coin`、`board.mob`、`board.you_are.mob_left`）＋ 舊別名
  （`you.star_coins`、`board.mobs`、`board.you_are.mobs_left`，僅在槽位等於預設值時出現）。
  前端可以慢慢遷到抽象名；舊欄位不會突然消失。
* **`board.json` 的資料要跟著槽位走**：節點的怪數欄位用 `mob` 的 key（`shades: 3`）、載具節點的
  `type` 用 `lift` 的 key、`shop.json` 的 `currency` 用 `coin` 的 key。
* **config 的數值旋鈕不槽位化**（`mob_dc`／`star_coin_per_kill`／`mob_damage_die`／
  `white_room_restore`）：它們是劇本作者寫的鍵，換作品時自己寫自己的；引擎只讀不認名字。
* **賬號層的錢包欄位固定叫 `star_coins`**（`accounts`／DB `wallet` 表）：那是**跨局**的通用錢包，
  與「這一桌的幣叫什麼」解耦——桌內記在 `slot("coin")`，入賬時按同一個數字搬過去。

### 星等（`stars.json`）的兩種寫法：舊制 vs **渠道制**

`packs/<包>/tables/stars.json` 是同一個檔案、兩種語意（**有 `channels` 就是渠道制**）：

```jsonc
// ① 舊制（沒有 channels）：逐級 when 判據——「下一顆星的條件成立了嗎」，每筆事件後判、可連升
{"kind": "stars",
 "rows": [{"star": 2, "when": {"or": [{"var": "me.goal_achieved"}]}, "gains": {"max_hp": 4}}]}

// ② 渠道制（有 channels）：三條路各自封頂，星等 = 1 ＋ Σ各渠道已漲，再封頂 max_star
{"kind": "stars", "max_star": 5,
 "channels": {
   "shop": {"max": 2, "doc": "在店裡用貨幣買（沒有 when ⇒ 只能靠買／effect 給）"},
   "goal": {"max": 2, "when": {"var": "me.goal_achieved"}, "doc": "完成自己的私密願望"},
   "side": {"max": 2, "when": {"exists": "facts.side_quest_done"}, "doc": "全體副任務"}},
 "rows": [{"star": 2, "gains": {"max_hp": 4}, "unlocks": ["hyousou_no_sho"]}]}
```

| | 舊制 | 渠道制 |
|---|---|---|
| 升星條件 | `rows[].when`（逐級） | `channels.<名>.when`（每渠道各判一次） |
| 一次給幾星 | 一級一級（可連升） | **條件成立 ⇒ 該渠道一次補滿**（「完成任務就拿到那 2 星」） |
| 上限 | 表裡最大的 star | 每渠道 `max` ＋ 總 `max_star` |
| 買星 | 不支援 | 商店商品寫 `star`（`star_channel` 選渠道、`price_step` 逐次漲價；`price_step_mode` 由模組自己定） |
| rows 的 when | **必填** | **不用寫**（渠道制下不被判定；只用作成長曲線） |
| 引擎含數字？ | 否 | 否（渠道、上限、價格、任務條件全在劇本） |

三條渠道的觸發方式（**都用現成機制，不新增動作**）：
1. **買**：`shop.json` 一列寫 `{"id":"star_upgrade","price":15,"price_step":10,"star":1,"nodes":["<節點>"]}`
   ——`price_step_mode`（**各模組自己定**）：`star`（預設，按「這條渠道已買幾顆星」遞增）／
   `purchase`（按「這個席位買過這個商品幾次」遞增，`star:2` 的商品第二次買只漲一格）。
   —— 買到即給 `star_channel`（預設 `shop`）＋1；渠道滿了**在扣錢之前**就擋（並在 `shop_items` 標 `sold_out`）。
2. **條件**（私密任務／副任務）：`channels.<名>.when` 寫 AST 判據（`me.*`／`facts.*` 都行），
   每筆事件後判；成立就把那條渠道補滿。要「全桌一起算」就判 `facts.<鍵>`（劇本用 trigger 的 `fact_set` 寫）。
3. **劇本直接給**：effect `{"star_grant": {"channel": "side", "value": 2, "scope": "all"}}`
   （`scope:"all"`＝全桌每人各加；省略 `seat` ＝ me）。

投影：`view.you.star` 多了 `channels: {名: {done, max, doc}}`（只看得到自己的），前端可以畫「這條路我還差幾星」。

### 兩個「難度」不是同一件事

| | `difficulty_presets` | `starting_difficulty` |
|---|---|---|
| 什麼時候定 | **運行時**（L3 導演判「卡了 10 手」就發 `difficulty_set`，GM 也可手動） | **建桌時**（大廳選，寫進桌 config 的 `difficulty`） |
| 影響什麼 | 只有 `dc_mod`／`act_mod`（檢定偏移、每回合行動格） | `rules.*`（`death`／`levels`…）＋四顆倍率（`monster_dc`／`exp_mult`／`coins_mult`／`price_mult`） |
| 會不會變 | 一局內會變 | 一局不變（換局也沿用） |
| 誰實作 | `trpg/engine/director.py` ＋ `scenario.difficulty_presets` | `trpg/service/table_factory.apply_difficulty()` |

合併規則（**只有一條**）：`starting_difficulty` 只補 **`config.rules` 沒寫的**鍵——**config 寫了一律贏**（既有規矩）；
四顆倍率則一律照該檔寫進 `config.difficulty_mods`（它就是那一檔的定義）。
建桌 API：`createTable(module:, scenario:, name:, watchable:, difficulty:)`——`difficulty` 傳檔名（例 `hard`），
不傳＝劇本 `default`；傳了不存在的檔名＝退回 `default` 並在 hub log 明講（不靜默）。
劇本沒寫 `starting_difficulty` ⇒ 大廳不顯示選單、`createTable` 也不用傳（legacy 一字不變）。
| `{"condition_set": {"id": "poison", "seat": "me", "turns": 3, "note": "…"}}` | **投／治一個部位受傷／異常狀態**（`seat` 省略＝`me`；`"value": false`＝治好；`turns` 覆寫「幾回合後自己好」） |
| `{"attr_mod": {"seat": "me", "attr": "agi", "value": 2, "note": "…"}}` | **屬性加值**（`value` 是**絕對值**、`0`＝拿掉；`attr` 要是這張桌的屬性之一）。效期＝直到再發一次把它改掉 ⇒ 檢定／攻擊／傷害／移動**全部**吃到它 |
| `{"resource_add": {"seat": "me", "name": "spell_slot", "value": -1}}` | **通用資源加減**（下限 0；`name` 自己取，例 `spell_slot`／`ammo`／`stamina`） |
| `{"resource_set": {"seat": "me", "name": "spell_slot", "value": 3}}` | **通用資源設值**（回滿／歸零用這條） |
| `{"damage": {"seat": "target", "die": 6, "attr": "str", "bonus": 1}}` | **傷害**：走引擎的算式（`die` 骰＋`attr`×`damage.attr_multiplier`＋`bonus`），骰值與結果都寫進事件（M3） |
| `{"heal": {"seat": "me", "die": 6, "bonus": 2}}` | **治療**（同上，封頂在 `max_hp`；M3） |
| `{"clock_tick": {"name": "night", "value": 1}}` | **時鐘填／退一格**（2026-10-20；`value` 可為負；夾在 `0…clocks.<name>.max`）。事件 `clock_changed` 帶結果值 `after` ⇒ 回放一致；AST 查 `clocks.<name>` |
| `{"clock_set": {"name": "night", "value": 0}}` | **時鐘設值**（歸零／跳段用；同樣帶 `after`） |

### 4.0 動作 DSL 的兩個原語（`attr_mod`／`resource_*`）

> **時鐘**（`clocks`）也是同一種「資料可配」的機制：宣告在 `scenario.json` 的 `clocks`，
> 動它用 `clock_tick`／`clock_set`，看它用 `{"var": "clocks.<name>"}`。
> 它由 `d20core` 宣告（多數桌都開著）；沒有 `d20core` 的桌要另外宣告這兩個 effect。
> 典型用法：`{"id":"t_night","on":["turn_ended"],"then":[{"clock_tick":{"name":"night"}}]}`
> ＋ `{"id":"e_dawn","kind":"neutral","when":{">=":[{"var":"clocks.night"},6]}}`。

機制在內核 `dsl.py`，**沒有內容表**（數值全部寫在你自己的 effect 裡）；開 `d20core` 就能用。

| effect | 語意 |
|---|---|
| `attr_mod` | 給某席某屬性一個**加值絕對值**（`value: 0` ＝拿掉）。生效範圍＝`attrs_of()`，也就是**檢定／攻擊／傷害／移動**都算它 |
| `resource_add` | 改某席的通用資源：`{"name": "spell_slot", "value": -1}`（**下限 0**，不會變負） |
| `resource_set` | 把某席的通用資源**設成某值**：`{"name": "spell_slot", "value": 3}` |

- 兩個都吃 `"seat"`（省略＝`me`）與 `"value"`（可以是 AST，例如 `{"var": "pack.params.slot_max"}`）。
- **AST 看得到**：`me.attr_mods.<屬性>`／`me.resources.<名>`（`seats.<席>.…` 同理）⇒ 可以寫
  「還有法術位才允許施法」「agi 加值 ≥2 時…」這種判據。
- **投影**：席位列表與自己的卡上都有（**只有真的有值才出現那一格**）。
- **GM 當場裁定**：`POST /api/gm {"op":"attr_mod","target":"p1","attr":"agi","amount":2}`／
  `{"op":"resource","target":"p1","name":"spell_slot","amount":-1,"mode":"add"}`（MCP 走 `table_gm`，
  參數同名 `amount`；`mode` 可 `add`／`set`）。
- 用途舉例：裝備給 `+2 agi`、祝福給 `+1 wil`、法術位用完就不能再放、彈藥耗盡、體力值。

### 4.1 部位受傷／異常狀態（啟用 `wound` 之類的包時）

條件的**內容**在包的 `tables/conditions.json`（id／名稱／`check_mod`／`attack_mod`／`tick`（每回合
扣多少、可加骰）／`turns`（幾回合自己好）／文案鍵）——**改數值＝改那張表，不改程式**。

機制（內核，`conditions.py`）：
- **每回合結算**：輪到某席位開始時，他身上的每個條件跑一次 → 先報（`condition_tick`）→ 扣血
  （`damage` 事件，數值是引擎算的、寫進事件）→ 倒數；數到 0 就自動解除。
- **判定自動吃修正**：`attack`／`check` 的 d20 mod 會加上身上的 `attack_mod`／`check_mod`。
- **公開**：傷在身上看得到（席位列表與自己的卡都有 `conditions`）；GM 的 `full_state` 也看得到。
- **AST 看得到**：`me.conditions`（條件 id 陣列）／`seats.<席>.conditions` ⇒ 可以寫
  `{"in": ["poison", {"var": "me.conditions"}]}` 這種判據。
- **GM 當場裁定**：`POST /api/gm {"op": "condition", "target": "p1", "id": "poison",
  "value": true}`（MCP 走 `table_gm` 的 `op=condition`；`value=false` 治好、`turns` 可覆寫）。
- 自檢器會用**這張桌啟用的包**決定 effect 池：沒啟用帶條件表的包 ⇒ 劇本用 `condition_set`
  會**在載入時就叫**（不會變成「跑了但什麼都沒發生」）。

`text_key` 一定要是**句庫裡現有的鍵**（見 `presentation.py` 的 `TEXT`，或啟用包的 `text.json`）——自檢器會擋錯字。

### 4.2 技能／法術與經驗／等級（啟用「規則書包」時，例：`rules.core`）

**內容在包的表，機制在內核**（`skills.py`／`advancement.py`）——改技能數值＝改表，不改程式。

| 表（`kind`） | 放哪 | 一條長什麼樣 |
|---|---|---|
| `lands` | `packs/<id>/tables/lands.json` | `{"id":"heal_spot","stop":[{"heal":{"seat":"me","bonus":2}}]}`——`board.json` 節點加 `"land":"heal_spot"`；落地時在**剛落地那個人**身上跑（`seat:"me"`）。沒有表／節點沒 `land` ⇒ 不多發事件 |
| `skills` | `packs/<id>/tables/skills.json` | `{"id":"kagaribi","name_jp":"篝火","attr":"wil","prof":2,"dc":12,"lvl":1,"target":"foe","cost":{"resource":"mana","value":1},"effects":[{"damage":{"seat":"target","die":6,"bonus":2}}]}` |
| `levels` | `packs/<id>/tables/levels.json` | `{"level":2,"need":30,"gains":{"max_hp":4,"attrs":{"agi":1},"resources":{"mana":1}}}`——`need` 是**累計**經驗門檻；第 1 級＝開局那一列 |

**動作 `use`（放技能）**（包宣告了才會出現在動作目錄；**2026-10-21 起技能與道具併進 `use`**，
`cast` 仍收同義呼叫、但不再對外宣告）：`{"action":"use","skill":"kagaribi","target":"p2"}`
（`skill` 必填；`target: "foe"` 的技能要 `target`——可以是別的席位，也可以是**同格的怪物 uid**）。流程：

1. 檢查等級（`lvl`；這一席還沒有等級紀錄＝當 1 級）
2. **先扣消耗**（`cost.resource` 不足⇒當場拒，**不擲骰、不留事件**）
3. 檢定 `d20 ＋ 生效屬性×attr_multiplier ＋ 熟練 prof ＋ 條件修正` vs DC（與 `check`**同一條算式**）
4. **成功才**套 `effects`（可用 `damage`／`heal`／`attr_mod`／`resource_*`／`condition_set`／`emit`／`fact_set`）
   ——與 `module.json` 的觸發是**同一份實作**（`scenario.effects_apply`），不會兩邊算出不同結果

**經驗／等級**

- 來源：GM `POST /api/gm {"op":"exp","target":"p1","amount":30}`（MCP `table_gm` 的 `op=exp`）；或棋盤上
  擊破小怪（`params.exp_per_star_kill`；沒設＝0，不發事件）。
- 過門檻就自動升（**逐級**發 `level_up`，事件帶**結果值**：`max_hp`／`attrs`／`resources` ⇒ 回放不必重算）。
  `gains.attrs` 是**基準屬性**（與 4.0 的加值層分開；兩者相加才是 `attrs_of`）。
- 開局：`seat_joined` 時自動給第 1 級（＝起始資源，例如 `mana: 3`）。**這一局中途才裝規則書** ⇒ 對舊席位
  發一次 `op=exp` 就會先補第 1 級（**裝包本身不會自己寫事件**——`test_packs` P28 擋著）。

**玩家看到的**：`view.you.skills`（每條帶 `known`＝等級夠不夠；不帶＝沒開這張表）／`level`／`exp`／`next_need`；
席位列表露 `level`／`exp`（有值才出現）。

### 4.3 星等（升星）——**靠劇情事件，不靠數字累積**

第二條成長軸，與 4.2 的等級**互相獨立**：

| 軸 | 燃料 | 誰決定 | 表 |
|---|---|---|---|
| 等級（`level`） | 一個累計值（擊破小怪／GM `op=exp`／劇本 `growth_gain`） | 表上的門檻 | `kind=levels` |
| **星等（`star`）** | **劇情條件** | 表上的 `when`（AST 判據） | `kind=stars` |

```json
{"kind": "stars",
 "rows": [{"star": 2, "title": "星の担い手",
           "when": {"or": [{"var": "me.goal_achieved"},
                            {"==": [{"var": "world.event_type"}, "boss_defeated"]}]},
           "gains": {"max_hp": 4, "attrs": {"wil": 1}, "resources": {"mana": 1}},
           "unlocks": ["hyousou_no_sho"]}]}
```

- **每筆事件後對每一席各判一次**（判據裡的 `me`＝那一席）⇒ `me.goal_achieved`＝**完成自己的願望**、
  `world.pray_first.<節點>`＝誰先開了那個祈願箱、`facts.<鍵>`＝劇本自己寫的事實、`round_no`＝第幾輪。
- 成立就升、**可連升**（一直判到不成立或到表頂）；`star_up` 事件**公開**並帶 `gains` 的**結果值**。
- `unlocks`：技能表的 `lvl` 之外多一條路——被解鎖的技能**不受等級限制**。
- 當場給星：effect `{"star_set": {"seat": "me"}}`（省略 `star`＝加一星）／GM `op=star`（跳級會累積中間每一星）。
- **開局記 ★1**（`seat_joined` 時發一筆 `star_up`，跟「開局第 1 級」同一個道理）⇒ 玩家一開場就看得到
  自己的星等與「下一星條件」；沒啟用星等表的桌＝一個事件都不多發。
- 「升級脫離 D&D」：等級只認「一個累計值 ＋ 一張門檻表 ＋ 成長內容」，**不綁任何設定**——
  誰給那個值由這張桌決定（擊破／`op=exp`／劇本的 `growth_gain`），術語怎麼叫也由文案決定。

⚠ **屬性表住 `config.rules.attributes`**（例：`["str","agi","wil","luck"]`）——屬性是 **config 的權威**，
規則書包**不宣告**它（否則與 `d20core` 撞同一格＝載入時叫）。要**換整套規則**＝獨立規則包宣告 `conflicts`。

---

### 4.4 道具／庫存（啟用帶道具表的包時，例：`rules.core`）

```json
{"kind": "items",
 "rows": [{"id": "heal_pack", "name_jp": "回復の包", "name_cn": "回复包", "stackable": true,
           "price": 3, "use": {"target": "self", "effects": [{"heal": {"seat": "me", "die": 6, "bonus": 2}}]}}]}
```

- 庫存住 `state.inventory`（`席位 → {道具id: 數量}`），由 `item_changed` 事件折出（`after` 是結果值）。
- **動作 `use`（用道具，`item=`）**：先扣 1（不足＝當場叫、不套效果）→ 成功才跑 `use.effects`
  （與技能／觸發同一個 applier）。`use.target=foe` 的道具要指定 `target`：席位，
  或**同格的怪物 uid**（`星屑の爆弾` 這類「割王硬度」的道具就走這條——只帶 damage 效果）。
  沒有 `use.effects` ⇒ 這件東西不能「用」（它的用處交給劇本的觸發）。
- **effect**：`{"item_grant": {"seat": "me", "id": "rope", "count": 1}}`／`item_take`。
- **GM 當場給**：`op=item`（`mode=take` 拿走）。
- 投影：`you.items`（數量／能不能用／對誰用）＋席位公開 `items`（**有值才出現**）。
- 沒啟用帶道具表的包 ⇒ 動作目錄不會有 `use`（技能／道具都用不了）、`inventory` 永遠空的、`item_grant` 載入時就叫（收窄）。

### 4.5 救起（啟用帶救起表的包時，例：`wound`）

```json
{"kind": "rescue",
 "rows": [{"id": "first_aid", "name_jp": "介抱", "attr": "wil", "dc": 12,
           "heal": {"die": 6, "bonus": 2}, "require_item": "heal_pack",
           "cost": {"resource": "mana", "value": 1},
           "risk": [{"condition_set": {"id": "wound_chest", "seat": "me"}}]}]}
```

把倒地（`status` 有 `down`）的人拉回來——**一個動作＋一張表**，不新增任何 state 欄位：

| 步驟 | 行為 |
|---|---|
| 動作 | `{"action": "rescue", "target": "<倒地的席位>"}`（不能救自己；目標要真的倒地，否則當場叫） |
| 這一桌用哪一條 | `config.rules.rescue`（旋鈕的權威）；省略＝表的第一列 |
| 消耗 | `cost`（通用資源）與 `require_item`（道具 −1）**全驗完才一起扣**；任何一項不足⇒拒、不留半套 |
| 檢定 | `d20 ＋ 生效屬性×mult ＋ prof ＋ 條件修正 vs dc ＋ 導演檔位`（＝`check`／`cast` 同一條） |
| 成功 | 對目標 `roll_heal(die, bonus)` ⇒ HP 回到 0 以上時**引擎自己解掉 `down`**；補不夠 ⇒ 還是倒地（誠實回報） |
| 失敗 | 表上有 `risk` 就套（走 `effects_apply`，與劇本觸發共用） |
| 事件 | `rescue_attempt`（公開，帶 rule／dc／success）＋既有的 `roll`／`heal`／條件事件 |

⚠ 沒啟用帶救起表的包（例：只列 `d20core`）⇒ 動作目錄不含 `rescue`、`rescue()` 當場叫（legacy 零變更）。

### 4.5b `module.entities` 的兩類 NPC（席位 NPC vs **GM 臨場 NPC**）

| 種類 | 寫法 | 誰會用到 |
|---|---|---|
| **席位 NPC** | `entities.<key>` 的 key ＝ `seats.json` 某席的 `character` | 那一席（會被喚醒、有自己的視角包）；GM 用 `gm_pkg` 把 `knowledge` 餵給它 |
| **GM 臨場 NPC** | 同一格加 `"gm_only": true` | **只有 GM**：GM 讀它的 `knowledge` 直接演（例：各城的店員——不配席位、不進喚醒名單） |

⚠ 沒有 `gm_only` 又對不到任何席位角色 ⇒ 自檢器 WARN（那通常是打錯字）。

### 4.6 商店（賣什麼＝一張表；買到＝真的進庫存）

```json
{"kind": "shop",
 "rows": [{"id": "heal_pack", "price": 3, "currency": "star_coins", "note": "…"}]}
```

| 這張桌怎麼賣 | 走哪條路 | 買到什麼 |
|---|---|---|
| 貨品 id **在道具表裡** | **表路**（新） | 貨幣 −`price` ＋ **`item_add` ⇒ 進 `inventory`**（4.4 的道具） |
| 貨品 id 不在道具表（例 `coffee`／`bento` 這種「當場吃掉」的） | **legacy 路** | 照舊：貨幣 −`cost`、`heal`／`memory` 生效（**不進庫存**） |
| 兩邊都沒有 | 當場叫 | — |

- **道具表是「這是道具」的權威**：同一個 id 兩邊都有時走表路（買到的是一件**可持有**的東西）。
- 賣什麼的權威：包（`provides.tables`）＞ 劇本檔；`tower.shop`（config／包 `defaults.tower.shop`）留給
  **非道具**的貨品（legacy 形狀 `{名: {cost, heal, memory}}`）——它不知道道具表，所以只管自己的那些。
- ⚠ 商店表賣的是道具 ⇒ 這張桌必須有道具表；沒開就是**載入時就叫**（作品層包另外 `requires` 規則書包）。
- ⚠ **`currency` 只認 `star_coins`**（寫別的當場叫）；`stock`（庫存）**還沒做**，寫了會當場叫。
- 投影：有店的節點上露 `you_are.shop_items`（`item`／`price`／`affordable`／`legacy`／道具名）——
  config 的 legacy 貨品也在清單裡（`legacy: true`）。

**按節點賣貨**：每一列可以寫 `nodes`（**省略＝通用＝每間店都有**）：

```json
{"kind": "shop",
 "rows": [{"id": "rope", "price": 2},
          {"id": "heal_pack", "price": 2, "nodes": ["teckan"]},
          {"id": "star_flare", "price": 6, "nodes": ["minato", "hyakki_f2"]}]}
```

- **同一個 id 可以在不同店不同價**（＝港町輸入品貴那種正當用法）；但**同一節點下同 id 只能一條**
  （載入時就叫——不然結帳不知道算哪個價）。
- `buy` 只賣**這家店有列**的東西；買別家才有的貨 ⇒ 拒（訊息列出**這家店**的可選）。

### 4.7 遭遇／怪物表（啟用帶 `monsters` 表的包時）

```json
{"kind": "monsters",
 "rows": [{"id": "mob_king", "name_jp": "塔の主", "nodes": ["hyakki_f4"], "boss": true,
           "dc": 17, "check": "agi", "coins": 6, "exp": 20, "damage_die": 8,
           "hp": 10, "count": 1, "active": {"chance": 0.5}}]}
```

塔裡的小怪數值從程式搬到表（作者能換敵人）：

| 這一格遇到誰 | 選擇規則（**越精確越優先**） |
|---|---|
| ① `nodes` 命中這一節點 | 最優先 |
| ② 沒寫 `nodes`、`depth_min/max` 含這一層深度 | 次之 |
| ③ 都沒寫 | 通用 |
| 表空／都不命中 | **回退**舊預設（`mob_dc`／`mob_damage_die`／`star_coin_per_kill`／`exp_per_star_kill`）⇒ legacy 一字不變 |

- 每個欄位都可省略 ⇒ 那一個欄位回退舊預設（**逐欄回退**）。

**戰鬥擴充（2026-10-19）——HP／同格多隻／主動怪**

| 欄位 | 意思 | 不寫時 |
|---|---|---|
| `hp` | 這一**隻**怪的血量；打死要打穿它 | **1**（＝一擊必殺，與舊行為相同） |
| `count` | 這一種在這一格放幾隻 | 由節點的 `mobs` **平分**給同格幾種怪（單一種＝原本的「一格 N 隻」） |
| `active` | 主動怪：`true` 或 `{"chance": 0~1, "max_targets": n}`；**收景階段**按機率咬同一格的玩家，**每景最多咬 n 個人**（`1..damage_die`）；**n=0 ⇒ 當被動怪** | **被動**（不主動攻擊） |

- **同格多種怪**：多列都寫 `nodes: ["<同一節點>"]` ⇒ 這一格全部都會出現（`nodes` 命中不再只取第一列）。
  depth 範圍與通用列仍是「先寫先贏」一筆。
- 每一隻怪有自己的 `uid`（`<節點>:<怪 id>:<序號>`）；`fight` 的 `target` 可以給 uid 或怪 id，
  不給＝打第一隻還活著的。
- 打中但沒打穿 ⇒ `monster_damaged` 事件（帶 `uid`／`damage`／`hp_after`）；打穿 ⇒ 照舊 `mob_killed`
  （多帶 `uid`）。擊破一隻 ⇒ 節點 `mobs` −1（上樓的閘門照舊看它）。
- 主動怪：`config.combat.active_attacks`（預設 true）可整桌關掉；預設機率 `config.combat.active_chance`（0.35）、
  預設人數 `config.combat.active_max_targets`（1），列上的 `active.chance`／`active.max_targets` 覆寫它們。
  **「每景」＝每一次收景**：每隻主動怪各自擲一次機率，命中後從同格活著的玩家人裡**種子化抽 n 個**（不挑人；
  n 大於人數＝全咬）。`n=0` 的列**不會出手**（投影也不標主動）。咬人事件是公開的 `damage`（`src`＝怪 id、
  帶 `chance`／`roll`／`node`／`active`），看不出是誰被挑中的原因就看 `chance`／`roll`。
- **沒啟用帶 `monsters` 表的包** ⇒ 這一整套不出現（投影沒有 `monsters`、fight 走原本的路）。
- `boss: true` ＝ 打光這一格 ⇒ `boss_defeated`（原本只有節點的 `boss` 標籤能）。
- 事件：`mob_killed` 多帶 `monster`／`name`；`damage` 的 `src` 換成怪物 id。
- 投影：**塔內樓層或這一格還有小怪**時露 `you_are.monster`（名字／DC／掉落）——城裡不露。

### 4.8 account 認證（token 是 **account token**，不是 seat token）

```
account（**人級、跨桌唯一**）──(角色選擇)──► char 實例（成長：level／exp）
    │                                        └──(劇情包：本劇本 seats.json 的 seat→character)──► seat（這一局的位置）
    └── sessions[<桌>].seats：他在**這張桌**坐哪一席（桌級）
```

| 誰 | 怎麼進來 | 帶什麼 |
|---|---|---|
| **人類** | login UI 打「帳號＋密碼」（或貼 token；之後前端自動帶） | `?token=` 或 `Authorization` 標頭（Bearer 形式） |
| **agent** | MCP／HTTP 同一條路 | 工具參數 `token`（GM 用連線標頭） |

- **兩個層次（2026-10-07 第二階段）**：**帳號＝人**（token／密碼／char 成長／貨幣錢包）**跨桌唯一**
  ⇒ **同一把 token 在每一張桌都認得**；**坐哪一席是桌級**的（`sessions[<桌>].seats`），
  由**那一張桌**的 `seats.json` 決定 ⇒ 沒被配席的桌＝他在那裡只是觀眾（不是犯錯）。
- `config.auth.accounts_file`：registry 檔住哪。**多張桌指同一個檔＝共用**（一台機器一份）；
  沒寫 ⇒ `<home>/accounts.json`（一桌一份的舊寫法，**legacy 一字不變**）。
- `config.auth.require_account`：**預設 false＝相容期**——照舊信 `seat`；**帶了有效 token 就以 token 的席位為準**。
  開了（true）⇒ 沒 token／替別人席位行動一律 **403**。
- GM 的控制權仍看 `config.auth.gm_token`（account 管身份、gm_token 管桌子的控制權）。
  舊形狀（頂層 `session`）的檔案仍讀得動（載入時自動視為「這個檔所屬那張桌」的座次）⇒ 舊桌不必先遷移；
  `tools/trpg_accounts.py migrate` 可把多份舊檔併成一份（**token 的 sha256 原樣搬 ⇒ 已發的憑證不必重發**）。
- **實例數據隨 account×char 走**：換 char 不繼承、換局保留（局內資源——★／道具／記憶——不帶走）。
  貨幣是**通用貨幣** ⇒ 放 account 層的錢包（同一個人的不同角色共用一筆）。
- registry **不進版控**；發憑證用 `tools/trpg_accounts.py`（`init`／`issue`／`list`／`verify`／`passwords`／`migrate`）。
  明文只發一次（寫進 `.secrets/trpg_accounts_*.txt`，600），registry 只存 sha256。多張桌共用一個檔時，
  **讀-改-寫有跨行程鎖**（少一層鎖就會互相蓋掉）。
- 沒有 registry 的桌 ⇒ 這一整套 no-op（legacy 一字不變）；這種桌的**登入卡前端會自動收起來**
  （`/api/state` 回的 `auth.has_accounts=false`），不會再掛在那裡騙人說「token 不認得」。
- **`?seat=` 相容連結收掉了（2026-10-07 第二階段）**：前端不再「貼連結＝坐在那一席」
  （`/api/state` 回的 `auth.seat_link`，預設 false）。要留這條路給**開放桌**（沒有帳號層的桌）自己寫
  `config.auth.seat_link: true`。進桌的正式入口＝**大廳**（`lobby_server.py`／`deploy/start_lobby.sh`，
  見 `docs/LOBBY.md`）。
- **帳號邏輯（設計者 2026-10-07 定案）**：`login → 選角色 → 這個角色有戲的桌 → 進場`；
  **座位號由劇本定死**（`char ↔ seat` 來自本桌 `seats.json`，玩家只挑角色）；
  **同一個帳號的同一個角色不能同時在兩張桌**——別的桌**開著且未收局**時，`/api/pick_char` 與
  `/api/enter` 都會擋（409，指名哪張桌）；別的桌已收局或離線則放行。
  細節與判準見 `docs/LOBBY.md §3`。

**進場的三個端點**（人類 login UI 與 agent 用同一套）：

| 端點 | 做什麼 |
|---|---|
| `GET /api/me` | 「我是誰／這一局坐哪一席／我的 char 實例與貨幣錢包／還有哪些 char 可選」（**不含 token**） |
| `POST /api/enter {token}` | **進場**：token ⇒ 席位 ⇒ 入座 ＋ 把**局外成長**（`level／exp`）帶進這一局（走 `advancement.carry_over`，一筆帶 `reason="carry_over"` 的事件 ⇒ 可回放） |
| `POST /api/pick_char {token, char}` | **角色選擇**：挑一個 char（＝挑那個實例）⇒ 綁到**包排給那個 char 的席位** |

- 前端：login UI（**帳號＋密碼**或貼 token ⇒ 存 localStorage ⇒ 之後**每個請求自動帶**）；`GET /api/state` 也吃 `?token=`。
  ⚠ localStorage 按**來源**（scheme://host:port）隔離 ⇒ **單一入口（一個埠＋子路徑）時登入一次就全桌通用**；
  多埠的舊配置下每個埠要登入一次。單一入口的 URL 形狀＝`http://<主機>:<埠>/<桌>/`（見 `docs/HUB.md`）。

#### 4.8.1 觀戰桌（**唯讀前門**）——想只看、不下場的人走這條

```jsonc
// config.json（**桌自己的**；整段可省 ⇒ 這桌不開觀戰桌）
"public_prefix": "/hyakki_tower",           // 單一入口時這張桌對外的子路徑（沒寫＝空＝舊行為；見 docs/HUB.md）
"watch": {
  "enabled": false,                        // ← 要觀戰桌就改 true（桌主自己開；預設關）
  "port": 18990,                           // 觀戰桌的埠（**hub 模式下不生效**；單桌／獨立觀戰才用）
  "upstream": "http://127.0.0.1:18971",    // 上游桌位址（單一入口時＝該桌的 loopback 埠）
  "read_token": "…"                        // **只有 require_account=true 的桌需要**：專供觀戰讀取的憑證
}
```

- 起法：`deploy/start_watch.sh <劇本 id>`（這一局的 `runs/<run>/watch.json` 開著時，`deploy/start.sh` 也會順手起；舊局沒有檔才看 `config.watch.enabled`）。
- **觀戰者沒有席位**：不進 `order`、不算「還沒出手」⇒ 不會擋回合、不會擋收景。
- **只讀**：`GET /api/state｜/api/log｜/api/map｜/health` ＋同一份前端的唯讀態；
  `act／say／join／enter／gm／mcp` 一律 **404**。私密（私訊／你自己的卡／碎片）**一律看不到**。
- `read_token` 是憑證 ⇒ 不進版控（`config.json` 本來就不進）；上游桌對它**只放行公開投影**。
- **不要用「入座」當觀戰**：入座＝下場；`/api/join` 不給席位時會**自動新開一席**（`seat1`…），
  在回合制／收景下會把那一輪卡住。

### 4.9 資源上限與升級曲線（啟用「規則書包」時，例：`rules.core`）

兩顆**旋鈕**都住 `config.rules`（**包只給預設，config 一律贏**——包寫在 `provides.defaults.rules`）：

```jsonc
// config.json 的 rules 段（要改這一桌的數值就寫這裡；不寫＝用包的預設）
"resources": {
  "memory": {"base": 60, "per_wil": 10, "per_level": 0},   // 記憶上限 ＝ base ＋ 意志×per_wil ＋ (等級−1)×per_level
  "mana":   {"base": 2,  "per_level": 1, "per_star": 2}    // 魔力上限 ＝ base ＋ (等級−1)×per_level ＋ 星等×per_star
},
"levels": {"exp_base": 30, "exp_step": 20}                 // 從第 L 級升到 L+1 級要 exp_base ＋ exp_step×(L−1)
```

* **記憶／魔力上限**：投影只在**自己**的 `you` 露 `max_memory`／`max_mana`（公開的 `seats[]` 不加）。
  兩者都**夾「不低於當前值」**（保護舊資料，免得畫出 `60/50` 這種超滿條）。
  沒啟用帶這顆旋鈕的包 ⇒ **一個欄位都不露**（舊桌投影一字不變）。
* **升級曲線**：`need(L)` ＝ 前面所有 `Δ` 的累計（第 1 級＝0）⇒ 預設 `0／30／80／150／240`。
  沒這顆旋鈕 ⇒ **照 `tables/levels.json` 每列原本的 `need`**（自訂表安全）；旋鈕形狀壞（負數／非數字）**載入當場叫**。
* 兩者都**只是「怎麼算」**，不改 state、不發事件 ⇒ 事件流回放不受影響。

### 4.10 死亡與送還（啟用 `death` 包時）

**沒啟用 ⇒ 舊行為一字不變**：HP ≤ 0 只是 `down`（躺著等人 `rescue`／治療）。
啟用後，**任何傷害把 HP 打到 ≤ 0** ⇒ 該席位被**送還到圖上最近的城市**、HP 復原，
並**缺席 N 個自己的回合**（輪到他時引擎發 `turn_skipped` 直接交下一席；場景制把他從
「還沒出手」名單排掉，所以 GM 照樣收得了景）。

```jsonc
// config.json 的 rules 段（**包只給預設，config 一律贏**；不寫＝用 packs/death 的預設）
"death": {
  "mode": "return_city",     // off ＝ 關掉（回到舊行為：只是倒地）
  "skip_turns": 2,           // 送還後缺席幾個自己的回合
  "hp_restore": 1,           // 送還後的 HP（1＝只剩一口氣）
  "nearest": true,           // 回**最近的城市**（false＝退回 start_node）
  "clear_conditions": false, // 送還時要不要洗掉部位受傷／異常狀態
  "drop_coins": 0,           // 掉幾成貨幣（0＝不掉、1＝全掉）
  "lost_memory": 0           // 額外扣多少記憶
}
```

* **「最近的城市」是引擎算的**：無向 BFS 走 `board.json` 的邊，挑第一個 `type: "city"`
  （或 `tags` 有 `city`）的節點 ⇒ **同一座塔、不同樓層死，送回的城市可能不同**
  （本倉範例 `examples/hyakki_tower`：1～2 階回鉄環市、3 階以上回港町，因為 4 階有通往港町的裏口）。
  沒有棋盤／圖上沒有城市 ⇒ 退回 `start_node`；**抽象桌**（沒有 board.json）只做 HP 復原與缺席、不動位置。
* **效果全部事件化**：`death`（公開，帶 `to`／`skip`／`hp_after`）＋每跳一次一筆 `turn_skipped`
  （公開，帶 `remaining`）⇒ `op=replay` 一致。`death_apply` 只從事件裡的值折出（`state["skip"]`）。
* **投影**：自己的 `you.skip_turns`（**只有 >0 才出現**）；日誌兩行（「☠ …被击倒——送还到…」／
  「…还在送还中（还剩 N 回合不能动）」）。
* **旋鈕壞了載入當場叫**（`mode` 亂寫／負數／`drop_coins` 不在 0~1）；`mode: "off"` ⇒ 完全 no-op。
* 沒啟用這機制時：`skip` 永遠是空的、投影不多欄位、**一個事件都不多發**。
* ⚠ 這一包只吃 `damage` 事件（四個來源都一樣：`attack`／技能 `damage` effect／`fight` 打輸／
  條件 `tick.damage`）——「倒地」與「死」是兩回事：被 `rescue` 救起來的人不會被送走。

## 5. 自檢與試跑

```bash
tools/check_module.sh campaigns/<劇本 id>            # 骨架倉附的一鍵入口：自己找引擎倉與 python
# ↑ 沒有骨架倉／要直接跑引擎那支時（同一件事）：
PY=python3
$PY tools/check_campaign.py modules/<模組>/scenarios/<劇本>   # ① 結構＋schema＋交叉指涉＋AST（跟引擎同一套判據）
```
`tools/check_module.sh` **劇本目錄放哪都行**（不必塞進引擎樹）；找不到引擎倉就
`TRPG_ENGINE_REPO=<引擎倉路徑>` 指定。`--serve` 可以順手起一張臨時桌（複本跑在 `/tmp`）。
輸出：`ERROR`＝起桌／跑團一定會壞（必須修）；`WARN`＝能跑但八成不是你要的。

試跑（**不要拿真桌當白老鼠**）：把劇本目錄複製到 `/tmp`、改 `table_id`／`port`，
用 `$PY server.py --config /tmp/<id>/config.json --home /tmp/<id> --port 8975` 起一張臨時桌，
用 `curl` 打 `/api/health`、`join`、`act` 玩兩手，確認判據與觸發如預期。

正式起桌：`./deploy/start.sh modules/<模組>/scenarios/<劇本>`（跑起來後 `/health` 應該回 `status: ok`，且帶 `session`）。

---

## 6. 常見 ERROR 對照

| 訊息 | 意思 |
|---|---|
| `節點 X 的邊指向不存在的節點：Y` | `board.edges[].to` 打錯字 |
| `願望 X 的 owner_character=Y 不在任何席位上` | `goals.owner_character` 與 `seats[].character` 對不上 |
| `碎片 X 支持的結論 Y 不在 clues.claims 裡` | `supports` 指到不存在的 claim |
| `module.requirements 有一條 X 對不到 goals.json 的任何願望` | 判據 id 打錯＝靜默不生效（所以直接當 ERROR） |
| `…emit.text_key: 句庫沒有 '…'` | 用了句庫沒有的鍵（要新句子請找工程加，或改 `emit` 直接帶 `text`） |
| `triggers[i](t).then[j]: effect 要是**單鍵**物件` | 一個 effect 只能有一個鍵（`{"emit": …}`） |
| `不認識的算子 '…'` | AST 拼錯或用了未開放的算子 |

---

## 7. 還沒有資料化的東西（誠實清單）

這些目前還在程式裡，寫新劇本**改不動**它們（想改就找我）：
- **塔機制的閘**（入口用過即失效、樓層只能坐昇降機、本層小怪未清不能上行、塔內飲食扣記憶、出塔収奪…）
  ——世界行動仍是「程式形式的規則」（`board.py`），要搬成資料得先做世界行動 DSL。
- ~~怪物數值／遭遇表~~ → **已補**：`kind=monsters`（見 4.7；選擇規則 `nodes` ＞深度＞通用，不命中逐欄回退舊預設）。
  ⚠ 唯一還沒資料化的是**遭遇的「數量」**——它仍由 `board.json` 的 `mobs`（每節點）決定。
- **句庫的鍵**（`presentation.py`）：鍵在程式裡（可加鍵）；**措辭**可以換——`<模組>/text.json`／
  `<劇本>/text.json` 用同一個鍵寫自己的句子（只影響這一桌、之後的新事件），見 `packs/README.md`。
- ~~難度只做到 `dc_mod`~~ → **已補**：L3 導演（`module.json` 的 `director` 區塊）會讀「主線距離／
  偏離」並提難度檔位，檔位決定 `dc_mod`（檢定）與 `act_mod`（行動格）——**罰＝收緊、賞＝放鬆**。
  它只提難度，`difficulty_set` 由內核代發（L3 的介面上沒有改 state 的鑰匙）。
- ~~結局的呈現~~ → **已補**：`ending` 是**公開**事件（日誌看得到「這一局到此為止」）＋帶結局的名字
  （`endings[].name_jp／name_cn`）；投影另給 `ended_info`（id／kind／名字）與 `summary`（戰果結算），
  前端據此畫結局卡。
- ~~機制不知道怎麼分冊~~ → **已補**：機制包（`packs/`；見 2.1.1）＝「哪一桌開了哪些機制」的
  但**包只能啟用已實作的機制**：部位受傷／異常狀態（`conditions.py`＋表）、技能（`skills.py`）、
  經驗／等級（`advancement.py`）、道具（`items.py`）、星等（`stars.py`）都已實作；
  **先攻制刻意不做**（要動的位置列在 `docs/PLAN_module_packs.md` §6）。
