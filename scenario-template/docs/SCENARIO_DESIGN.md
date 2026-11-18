# 劇本設計手冊（作者向）

> **格式**怎麼寫 → `SCENARIO_FORMAT.md`（每個 JSON 的欄位、AST 詞彙、effect 白名單）。
> **這一份**講的是另一件事：**怎麼寫得好**——什麼樣的結構會讓一桌人卡死、什麼樣的結構會讓玩家自己長出故事。
>
> 讀者：寫劇本的人、演 GM 的 agent、審稿的人。第一版 2026-10-20，附**三個現役劇本的實測盤點**（§4）。
> 機械可查的部分做成 `tools/design_lint.py`（§5）；**它不擋起桌**，起桌的前提永遠是引擎的 `tools/check_campaign.sh`／`check_campaign.py`。

---

## 0. 一句話

一個好劇本不是「一段故事」，是**一組局面 ＋ 可查證的資訊 ＋ 會自己動的對手 ＋ 每個人各自的出口**。

引擎保證的是「規則不漏、回放一致、私密不外洩」；它**不保證這齣戲好玩**。下面七條，就是把「好玩」拆成**可以檢查**的東西——每一條都對應到我們格式裡的一個欄位，也對應到 `design_lint.py` 的一條規則。

---

## 1. 七條原則

### P1 準備「局面」，不要準備「劇情」

**主張**：不要寫「A 發生→B 發生→C 發生」。寫「現在是什麼局面、誰想要什麼、不動的話會怎樣」，然後讓玩家的行動決定事件順序。

**來源**：Justin Alexander, *Don't Prep Plots*（2009）。原文的說法很利：「你的團不是一個故事，是一件**正在發生的事**（a happening）」。劇情式準備的每個節點都是一個**失敗點**（PC 沒上船／沒發現難民／沒問出線索，後面全斷），局面式準備只需要準備**地點、人、他們的工具與目標**。

**在我們的格式裡**：
- `GM.md §2 開場` 要的是「**一個具體的麻煩 ＋ 兩個可能的方向**」，不是「第一景→第二景→第三景」。
- `scenario.json` 是**宣告式**的（願望判據／表層判據／結局／觸發），天生就是局面式——這是我們的架構優勢，別在 `GM.md` 裡把它寫回劇本。
- `director` 的政策是「卡住時收緊、順的時候放鬆」，那是**調節**，不是推進。

**反例（真實，兩種方向都有人踩）**：
- `hyakki_tower/scenarios/sannenme_no_onibarai` 的 `GM.md` 現在只剩分工／節拍／善後三節，**開場與收景判準不見了**——GM 手上沒有「這一景要給玩家什麼麻煩」的框，只能照 `WORLD.md` 的藍本即興，那就等於沒有局面設計。
- `hyakki_tower/scenarios/scenario_b`（**最新的一齣，2026-10-19**）反過來：`GM.md §2` 直接寫成「**進行フロー（標準5景）**」，第 1 景到第 5 景各自的狀況、GM 該讀哪段、該讓哪個碎片出土、第 5 景誰完成哪條願望——全都排好了。這是一份**五幕劇本**：玩家只要不照順序走（先去 3 樓、不進茶房、繞過 BOSS），那份「GM 行動」清單就有一半用不上，而 GM 會很自然地**把玩家推回流程**（＝鐵路）。素材（哪一景有哪個 NPC、哪枚碎片適合在哪裡出土）**很有用**，但它是「手上的牌」，不是「必須發生的順序」。

**寫法上的分界**：GM.md 可以（也應該）寫「這裡有什麼、誰想要什麼、沒人管會怎樣」，但不要寫「第 N 景 GM 要促 player 做 X」。把五景的素材留下，把「標準」兩個字拿掉，每一景改成「如果玩家走到這裡，這裡有…」。

**檢查**：`design_lint` 的 `gm.missing_section`（GM.md 必須有 調性／開場／收景／不能做 四段）。

---

### P2 每一個結論，至少三條證據

**主張**：任何「你要玩家得出的結論」，都要有至少三條彼此獨立的線索。玩家會漏看第一條、誤讀第二條，第三條是保險。

**來源**：Justin Alexander, *Three Clue Rule*（2008）。同一位作者在 *Node-Based Scenario Design* 裡把它反過來用：**只要玩家手上有任意三條線索，他們至少會走到一個結論**——所以每個節點往外指三條線，玩家就不可能真的走進死路（*Inverting the Three Clue Rule*）。

**在我們的格式裡**：`clues.json` 的 `claims`（結論）＋ `fragments`（證據，`supports` 指回結論）。一條結論有幾枚碎片，就是它有幾條備援。**而且盡量來自不同 `pool`**（`tower` / `outside`）——不然「三條」其實是「同一個地方的三个角度」，一起漏。

**實測（見 §4）**：
- 原型桌：8 條結論裡，**5 條只有 1 枚碎片**。其中一條是某個玩家願望的**必要條件** ⇒ 那一枚碎片沒出土，那條願望就是不可能的任務。
- 原型桌 B：5 條結論各 2 枚，1 條只有 1 枚。
- `sannenme_no_onibarai`（改版前）：2 條結論各 2 枚（`c_blade`／`c_night`），碎片總數只有 4 枚——**5 個玩家、4 枚碎片**（之後補到 4 條結論、13 枚碎片）。

**檢查**：`clue.redundancy`（<3 枚＝WARN）、`clue.single_pool`（全同池＝INFO）、`clue.unsupported_claim`（0 枚＝WARN）。

---

### P3 地圖要有多條路（節點設計／非線性）

**主張**：節點（地點、場景）之間要有**多條**連線、**環路**、**多個入口**。玩家的選擇不能退化成「要不要繼續往前走」。

**來源**：*Node-Based Scenario Design*（每個節點＝一個有意義的抉擇點，節點裡放指向其他節點的線索）；Jennell Jaquays 的地城設計（*Xandering the Dungeon*）：Thracia 的一樓有三個入口、往下的路有兩條正規＋八條非正規——所以「同一份準備」可以餵出完全不同的兩桌。

**在我們的格式裡**：`board.json` 的 `nodes[].edges`（**有向**）。出度＝這個節點有幾個「下一步」。起點出度 1＝開場沒有選擇。

**實測**：
- `sannenme_no_onibarai`：14 節點 / 27 邊，**起點 `teckan` 只有一條出路**；塔層是單鏈（f1→f2→…→f6），有後門 `hyakki_back`（好），但塔內沒有第二條上樓的路。
- 塔是「分層單鏈」：**能用，但玩家在塔裡只有一種決策（打不打、上不上）**。要加選擇，不是加節點，是加**邊**（後門、外牆、升降機故障時的另一條路、屋頂）。

**檢查**：`board.start_single_exit`（WARN）、`board.chokepoint`（INFO）、`board.no_loop`（INFO）、`board.linear_chain`（INFO）、`board.unreachable`（WARN）。

---

### P4 對手／世界要有自己的時鐘

**主張**：世界要在玩家不動的時候**自己往前推**。不是「玩家沒進展 ⇒ 我把難度調緊」（那是在罰玩家），而是「天黑了／巡邏換班了／儀式差一步了」。

**來源**：
- *Dungeon World* 的 **Fronts**：每個 danger 有它的 **impulse**（它想幹什麼）與 **grim portents**（如果沒人管，它會依序發生的事），終點是 **impending doom**。
- *Blades in the Dark* 的 **progress clock**：把「持續的威脅」畫成幾格的鐘，每次出事就填一格。鐘反映局面，不決定局面。
- 兩者共同的要點：**它是世界的進度，不是你對玩家的評分**。

**在我們的格式裡**：`scenario.json` 的 `triggers` ＋ `facts`。AST **沒有累加**，所以「鐘」用**門檻＋`once`** 近似：每一階段寫一條

```jsonc
{"id": "t_night_falls", "on": ["turn_ended"], "once": true,
 "when": {">=": [{"var": "round_no"}, 4]},
 "then": [{"fact_set": {"key": "night_falls", "value": true}}]}
```

後面的判據、`emit` 的文案、GM 的語氣都可以吃 `facts.night_falls`。（`scene-template` 現在就帶這一條當示範。）

**實測**：三個現役劇本的觸發**全部掛在玩家的成功事件**上（`mob_killed`／`boss_defeated`／`death`／`move`）——`clock.missing`。玩家不打，世界完全不動。這也解釋了為什麼 `director` 只能「收緊難度」：它手上沒有世界可以推。

**檢查**：`clock.missing`（INFO）。

---

### P5 願望之間不要共用同一個瓶頸

**主張**：每個人的願望要有**自己的一條可達路徑**。多條願望綁在同一條結論／同一個地點上，那條線一斷就是**多人一起失敗**（相關性失敗），而且會誘發「互相卡位」的爭搶。

**來源**：Alexandrian 的 *Three Clue Rule* 應用在**關卡設計**上——「任何必須被克服才能繼續的問題（chokepoint problem），準備三條路」；`Don't Prep Plots` 也講得很白：局面設計的價值在於「玩家做的每個選擇都有意義」。

**在我們的格式裡**：`goals.json`（每席的私密願望）＋ `scenario.json.requirements`（AST 判據）。判據有 `or` 分支＝有替代路線。

**實測**：
- `sannenme_no_onibarai`：5 條願望、2 條結論 ⇒ **`c_night` 被 3 條願望同時需要**（`g_record`／`g_ember`／`g_route`），而 `c_night` 只有 2 枚碎片。這兩枚碎片只要一直落在同一個人手上，三個玩家**一起**卡住。這是目前最結構性的風險。
- `scenario_b`：4 條願望各綁 1 條結論，分散得好（每條 2 枚碎片）；問題只在**枚數**（P2）。
- `sannenme_no_onibarai`：`g_cat` 綁 `c_cat_trace`（2 枚，都在 `outside` 池）、`g_crane_proof` 綁 `c_tower_knows`（**只 1 枚**）。兩個人的願望各自吊在一根線上。

**檢查**：`goal.shared_claim`（≥2 條願望必要同一結論＝WARN）、`goal.single_route`（沒有 or＝INFO）、`seat.no_goal`（玩家席位沒綁願望＝WARN）、`goal.not_seated`（寫了沒人拿＝INFO）。

---

### P6 失敗要有出口

**主張**：除了「贏」，要有**中性結局**（撤退、時間到、談和、帶著半個答案回家）。否則卡住的桌只有兩條路：一直空轉，或等一個永遠不會成立的「全滅」。

**來源**：*Don't Prep Plots* 的實務結論——失敗不是鐵路（"failure is not railroading"），但**沒有出口的失敗**會讓桌停在原地；PbtA 系（Dungeon World／BitD）把它寫成 "fail forward"：失敗要**改變局面**，不是把局面歸零。

**在我們的格式裡**：`scenario.json.endings`（`kind`: `win`／`lose`／`neutral`）。**先搶先贏**（列表順序），所以 `win` 要排在 `neutral` 前面。

**實測**：三個劇本都只有 `win` ＋ `lose`（`ending.no_neutral`）。而 `lose` 的條件是「全桌都倒」——
- `sannenme_no_onibarai` 開了 `death` 包（HP≤0 ⇒ 送還城市），所以「全員倒地」幾乎不可能成立（劇本自己的註解也承認）。⇒ **實際上只有一個結局**。
- `sannenme_no_onibarai`／`scenario_b` 同理：只要有人站著，lose 就不判；而 win 需要打贏 BOSS。

**建議寫法**（`scenario-template` 已內建）：

```jsonc
{"id": "e_retreat", "kind": "neutral",
 "when": {"and": [{">=": [{"var": "round_no"}, 3]},
                  {"in": ["town", {"pluck": [{"values": {"var": "seats"}}, "node"]}]}]}}
```

**檢查**：`ending.no_neutral`（WARN）、`ending.lose_gated_by_death`（INFO）。

---

### P7 碎片正文要「可攜」而且私密

**主張**：一枚碎片要在**任何可能落地的地方**讀得通，而且只有持有人看得懂。它是**證據**，不是風景描寫。

**來源**：Sly Flourish 的 *Lazy DM* 八步裡有一條 **"Define Secrets and Clues"**：一景準備約 10 條「 revelations」，而且**不綁在特定地點**——因為玩家永遠不會照你安排的地點去。三線索講的是「備援」，這一條講的是**載體**。

**在我們的格式裡**：`clues.json.fragments[].text` 是**私密正文**（GM 轉述會被洩漏閘擋），`pool` 決定它會在哪裡出土。`outside` 池的碎片會在城市裡落地。

**好的寫法（現役範例，`sannenme_no_onibarai`）**：
> 「鞘から抜けない折れ刃。断面だけが新しい——十年前の斬り口ではない。」

一件**可以拿在手上、可以誤讀、可以轉述**的東西。玩家會問「為什麼拔不出來」——那就是線索在起作用。

**不好的寫法**：`outside` 池的碎片寫成「排氣ダクトの煤払い痕」「避雷針の足元に生えた花弁」——城市裡沒有排氣管，也沒有避雷針。要么改寫成可攜物件（一張字條、一本帳簿、一枚收據），要么把它放進 `tower` 池。

**檢查**：`clue.place_bound`（INFO，會排除「書／記録／台帳／レシート」這類可攜物件的關鍵字，其餘請人看一眼）。

---

## 2. 選結構：四種現成模板

不要在「塔」和「故事」之間二選一——先決定**這一齣戲的張力從哪來**，再選結構。

| 結構 | 張力來源 | 適合 | `board.json` | `goals.json`／判據 | 主要風險 |
|---|---|---|---|---|---|
| **塔式攻略** | 資源與風險管理 | 戰鬥、探索、帶東西回家 | 分層＋入口＋退路（**至少一條後門**） | 綁「到哪裡」＋「帶什麼回來」 | 選擇退化成「繼續走」（P3）；碎片太少（P2） |
| **節點網／調查** | 資訊不對稱 | 誰做了什麼、真相、審判 | 少節點、多邊、**要有環路** | 綁「結論」（`me.claims`）＋「誰向我出示過」 | 節點太少→一輪走完；結論只給 1 枚＝單點失敗 |
| **壓力時鐘** | 時間 | 限時救援、守城、儀式倒數 | 小圖＋一個中央節點 | 綁「期限內做到」＋`facts.<階段>` | **沒有時鐘就沒有壓力**（P4） |
| **抽象桌（無 board）** | 對話與立場 | 法庭、密室、茶會、談判 | 不寫 `board.json` | 全靠 `claims`／`shown_to`／`facts` | 沒有地圖 ⇒ **必須**有「誰知道什麼」的硬邊界（`entities.knowledge`） |

混用是可以的（塔式＋一個時鐘＋每人一條私願＝我們現在的樣子），但**每一種都要把它的檢查條目交代清楚**。

---

## 3. 交桌前檢查清單

抄進你的筆記，逐條打勾（機械可查的用 §5 的工具，其餘 eyeball）：

- [ ] **P1** `GM.md` 有四段：調性／開場（一個麻煩＋兩個方向）／收景判準／不能做的事；開場**沒有**寫成事件序列。
- [ ] **P2** 每一條**被願望用到**的結論都有 ≥3 枚碎片，且跨池（`tower`＋`outside`）。
- [ ] **P2** 沒有 0 枚碎片的結論。
- [ ] **P3** 起點有 ≥2 條出路；塔／地城至少有**一條非正規路線**（後門、外牆、故障的升降機）。
- [ ] **P3** 從起點走得到每一個節點（沒有孤島）。
- [ ] **P4** 至少有一條「世界自己的時鐘」（`on=turn_ended` ＋ 景號門檻 ＋ `once`）。
- [ ] **P4** 不要用「收緊難度」代替世界推進（`director` 是調節器，不是劇情引擎）。
- [ ] **P5** 沒有 ≥2 條願望**必要**同一條結論；共用時要給 `or` 的替代路線。
- [ ] **P5** 每個 `human`／`agent` 席位都綁了願望。
- [ ] **P6** 有 `neutral` 結局，且 `win` 排在它前面（先搶先贏）。
- [ ] **P6** `lose` 的條件在這張桌的機制下**真的可能成立**（開了 `death` 就別寫「全員倒地」）。
- [ ] **P7** `outside` 池的碎片正文是可攜物件，不是地景描寫。
- [ ] **P7** 每個 NPC 的 `entities.<id>.knowledge` 只寫它真的該知道的；範圍外要「含糊過去」。
- [ ] **P8（循環）** 每景手數＝玩家席位 × 行動格 ≤ 8；一手的牆鐘（`turn.seconds`＋等待）不是這一桌的主要成本。
- [ ] **最後**：`tools/check_module.sh <模組目錄>`（error 0／warn 0）＋ `python3 tools/design_lint.py <模組目錄>`。

---

## 4. 現役劇本盤點（2026-10-20 實測）

### 4.1 數據

| 劇本 | 節點／邊 | 出度 1 的節點 | 結論 | 碎片 | 碎片／結論 | 願望 | 共用的結論 | 結局 | 觸發 | design_lint WARN |
|---|---|---|---|---|---|---|---|---|---|---|
| `hyakki_tower/sannenme_no_onibarai` | 24／46 | **8** | 8 | 10 | 1（5 條）／2（2 條）／3（1 條） | 4 | 各自 1 條 | win, lose | **0** | **9** |
| `hyakki_tower/sannenme_no_onibarai` | 10／20 | 2 | 6 | 10 | 2（5 條）／1（1 條） | 4 | 各自 1 條 | win, lose | 2 | **8** |
| `hyakki_tower/sannenme_no_onibarai` | 14／27 | 3 | 2 | 4 | 2／2 | 5 | **`c_night` ×3** | win, lose | 3 | **5** |

（WARN 明細可用 `python3 tools/design_lint.py <模組目錄>` 重跑；這張表就是那支工具的輸出摘要。）

### 4.2 已經做對的（別改壞）

1. **願望是私密的、綁在角色身上的**——這是這套桌的核心樂趣（資訊不對稱 ＋ 互相出示），也是 `semic` 包的價值。不要為了「讓故事順」把願望公開。
2. **claims／fragments 分層**（結論 vs 證據），加上 `show`／`give` 的來源鏈：玩家「看到別人的證據」這件事本身就有規則意義。這正是 P2／P5 可以機械檢查的原因。
3. **`entities.knowledge` 的知識邊界**：NPC 只知道它那一格，範圍外含糊。這比「叫 GM 別洩漏」可靠得多。
4. **GM.md 的硬規則**（數值只念引擎、不代玩家決定、判到結局就收手）——把「GM 的權力邊界」寫成可執行的句子，而不是風格建議。
5. **`hyakki` 的兩條規則寫成一句話**：「經驗只從戰鬥來」「死亡不是結束，是擔架」——規則就是語氣，語氣就是規則。
6. **碎片正文的物件化**（折れ刃、火箸、レシートの裏書き）：可攜、可誤讀、可轉述。
7. **`director` 用「距離／窗口」指標**（`stall_hands`／`progress_recent`），不用累計數——這是踩過坑才有的設計。

### 4.3 缺口與下一步

**資料就能補（不用改引擎，作者自己做）**
| 缺口 | 影響 | 補法 |
|---|---|---|
| `原型桌` 5 條結論只有 1 枚碎片（`原型桌B` 1 條、`hyakki` 全部 2 枚） | 願望單點失敗 | 每條被願望用到的結論補到 3 枚；至少 1 枚來自另一個 pool |
| `hyakki` 3 條願望共用 `c_night` | 三人一起卡 | 給 `g_ember`／`g_route` 各自一條替代結論，或把 `c_night` 拆成「夜的真實」與「爐火的責任」兩條 |
| 三個劇本都沒有 `neutral` 結局 | 卡住的桌沒有收法 | 加 `e_retreat`（撤退）——scenario-template 已內建 |
| `lose` 條件（全員倒地）在 `death` 包下幾乎不成立 | 實際上只有一個結局 | 改成「全員在同一景內被打倒」或「全員撤退到城鎮＝neutral」 |
| `原型桌` 的 `GM.md` 缺調性／開場／收景／不能做 | GM 沒有局面框 | 照 `scenario-template/GM.md` 補四段 |
| `原型桌B` 的 `GM.md §2` 是「標準 5 景」流程（每景指定 GM 行動與碎片） | 玩家不照順序走就變鐵路 | 保留素材、拿掉順序：改成「如果玩家走到 X，這裡有…」＋開場的兩個方向 |
| 三個劇本都沒有世界時鐘 | 玩家不動＝世界不動 | 每個劇本加 1～2 條 `on=turn_ended` ＋ 景號門檻的 trigger |
| `examples/hyakki_tower` 之前自檢不過（缺 `rules.core` 包） | 範例抄了會踩坑 | **已修**（`f54f118` 補 `packs/rules.core`；2026-10-20 實測 `check_module.sh` error 0／warn 0） |

**要工程（新機制，先找工程師）**
- **真正的時鐘**：現在只能用「景號門檻 ＋ `once`」近似分階段；若要「每 N 手填一格、滿了就 doom」，需要內核多一個 `clock` 維度（`pack` 宣告、`effect` 加 `clock_tick`）。這是 P4 的上限所在。
- **碎片出土可控**：`drop_policy` 目前只有 `anyone_but_requester` ＋ `prefer_unheld`。P2 的補救（「這條結論太久沒出土，優先給它」）需要 policy 多一條「按結論欠帳優先」。
- **`neutral` 與 `win` 的優先序**：結局是「先搶先贏」，多結局劇本需要一個明確的排序慣例（目前靠作者把 `win` 寫在前面——這是慣例，不是機制）。

---

## 5. 工具：`tools/design_lint.py`

```sh
python3 tools/design_lint.py <模組目錄>            # 掃每一個劇本
python3 tools/design_lint.py <劇本目錄>            # 只掃這一齣
python3 tools/design_lint.py <模組目錄> --strict   # 有 WARN 就 exit 1（交件前用）
python3 tools/design_lint.py <模組目錄> --json     # 給別的程式吃
```

**它只讀 JSON 與 `GM.md`，不載入引擎**，所以：

| | 誰負責 | 會不會擋 |
|---|---|---|
| 能不能起桌、格式對不對、包存不存在 | 引擎 `tools/check_campaign.py`（入口 `tools/check_module.sh`） | **會擋**（ERROR） |
| 好不好玩、會不會卡死、設計味道 | 本工具 | **不擋**（WARN／INFO） |

`WARN` ＝ 結構上會讓「一桌人卡住／某人的願望註定失敗」；`INFO` ＝ 可能是刻意的，自己判斷。

實際輸出（`sannenme_no_onibarai`，2026-10-20）：

```
== sannenme_no_onibarai
   nodes 14 / edges 27 / chokepoints 3 / endings 2 / triggers 3 / goals 5 / claims 2 / fragments 4
   [WARN] clue.redundancy   結論 c_blade 只有 2 枚碎片（建議 ≥3）：f_edge, f_teeth——玩家漏掉／誤讀就沒有備援，這是 chokepoint
   [WARN] goal.shared_claim 3 條願望都要結論 c_night（g_ember, g_record, g_route）——這條線斷了就是多人一起失敗（相關性失敗）
   [WARN] board.start_single_exit 起點 teckan 只有 1 條出路——開場沒有選擇，玩家只能照劇本走（P1／P3）
   [WARN] ending.no_neutral 只有 win／lose，沒有 neutral（撤退、時間到、談和）——卡住或不想打到底的桌沒有體面的收法（P6）
   [INFO] clock.missing     所有觸發都掛在玩家「成功事件」上，沒有世界自己的時鐘……
```

---

## 5.5 玩法循環（一局多長、手花在哪）

設計原則是「這一齣戲好不好」，**玩法循環**是「這一局跑起來多長、手花在哪」——量測與旋鈕寫在
**`docs/GAMEPLAY_LOOP.md`**，工具是 `tools/loop_metrics.py`（吃事件流，只讀）。

2026-10-20 兩張現役桌的實測：原型桌 353 手／超時 57%／移動佔 48%／戰鬥 1.6%／每景碎片 0.24；
hyakki 89 手／整局 1 景未收／碎片 0。四個旋鈕（單次事件長度、事件密度、地圖規模、戰鬥日常配比）與
兩個可直接 A/B 的 config 變體都在那一份。

## 6. 參考（都值得整篇讀）

- Justin Alexander, **Three Clue Rule** — <https://thealexandrian.net/wordpress/1118/roleplaying-games/three-clue-rule>
- Justin Alexander, **Don't Prep Plots** — <https://thealexandrian.net/wordpress/4147/roleplaying-games/dont-prep-plots>
- Justin Alexander, **Node-Based Scenario Design, Part 2: Inverting the Three Clue Rule** — <https://www.thealexandrian.net/creations/misc/node-design/node-design2.html>
- Justin Alexander, **Xandering the Dungeon**（非線性地城的入口／環路／捷徑） — <https://thealexandrian.net/wordpress/13085/roleplaying-games/xandering-the-dungeon>
- Dungeon World SRD, **Fronts**（impulse／grim portents／impending doom） — <https://www.dungeonworldsrd.com/gamemastering/fronts/>
- Blades in the Dark, **Progress Clocks** — <https://bladesinthedark.com/progress-clocks>
- Sly Flourish, **Return of the Lazy Dungeon Master**（八步準備；"Secrets and Clues" 不綁地點）

> 一句話收尾：**引擎管得住「不可以」，管不住「好玩」**。上面七條就是我們把「好玩」盡量變成可檢查的東西的方式；剩下的那一點，永遠是 GM 與玩家在桌上長出來的。
