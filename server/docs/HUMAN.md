# 人類怎麼進來（大廳／單一入口／觀戰桌／桌面怎麼操作）

> 從 `README.md` 拆出（2026-10-20；**2026-10-21 改寫**：前台已經是 Nuxt SPA＋帳號層，舊的
> 「貼連結／填角色名入座」與 `/api/*` 那一套都退役了）。後台為什麼是單一進程＝`docs/HUB.md`；
> 大廳那一面＝`docs/LOBBY.md`；agent 那一面＝`AGENT.md`。

## 進來的方式

**現在**：瀏覽器打開前台（`http://<主機>:8971/`；頁面由 `trpg/frontend-nuxt` 提供，後台只給 API）→

1. **登入**（右上「登入」）：帳號＋密碼 → 換一把 token 存 `localStorage`（之後自動帶；agent 也用同一條 `resolve`）。
2. **大廳**：選模組 → 劇本 → 桌（一張桌＝一個 run）。建新桌要登入；可以順便選**開局難度**
   （劇本有 `starting_difficulty.levels` 才有選單，例 easy／normal／hard）。
3. **進桌選人**（entry）：用「我的角色」入座，或建一個新角色；有人坐的席位不能擠。
   **入座一律走帳號**——`?seat=p1&name=X` 這種「貼連結＝坐在那一席」的相容連結已收掉
   （`config.auth.seat_link` 預設 false，只有沒有帳號層的開放桌才開回來）。

桌面（同一個頁面，手機瀏覽器一樣能用）：

| 區塊 | 看得到什麼 |
|---|---|
| 頂欄 | 劇本／地名、**第 N 輪**、開局難度、剩餘行動／發言額度、貨幣、**回合倒數**（輪到你時高亮） |
| 現在的格 | 這一格的地名／樓層／特徵（商店、祈願箱、遊戲房、敵人）、**入口狀態**（開著剩幾輪／已封要 search 重開）、同格隊友 |
| 戰鬥 | 這一格的敵人（HP／主動／BOSS）；點立牌或敵人卡選目標，再按一次才真的出手 |
| 行動面板 | 這張桌宣告的動作（`view.actions`，名字與說明由劇本給）：move／search／fight／pray／buy／rest／game／declare／show／give／wish／attack／check／defend／**use**／rescue／other／end_turn。**技能與道具都在 `use` 底下**（先選招式或道具 → 再選目標；怪物與席位都列） |
| 卡／背包 | 自己的數值、狀態、神器、**道具（含說明）**、技能（施放時顯示說明）、只發給你的私密資訊 |
| 說話 | 公開發言（全桌看得到）＋私訊／場外問 GM（只有雙方看得到，會吃發言額度） |

- **回合制**：一次一個人（`end_turn`＝結束自己的回合；超時策略由 `config.turn.policy_on_timeout` 決定）。
  `move` 是免費動作（`turn.free_actions`）時不佔行動格。
- **難度**：建桌時選定、一局不變（`config.difficulty`）；投影的 `difficulty` 與大廳的 `difficulty`
  都看得到。之後的 DC／怪物血量倍率由它套。
- **GM 面板**（底部）：持 `config.json` 的 `auth.gm_token`（或 `?gm=1`）才出現；發私密／結束回合／
  暫停整桌（owner）／封存（owner）都在那裡。

## 資料怎麼流（前台只認 GraphQL）

- **唯一端點**：`POST /graphql`。桌是欄位：
  `query { table(id:"<桌>") { session { id } view(session:"…", seat:"p1") { raw } } }`；
  契約＝`docs/schema.graphql`。`view` 是投影（JSON），`log`／`map` 也是 JSON。
  舊的 `/api/state`、`/api/act`… 只剩相容期用途（hook／腳本），前台不再走。
- 寫入也走 mutation：`act(tableId, session, action, target, attr, dc, note, skill, item, req)`／
  `say`／`tell`／`tellGm`／`join`／`createTable`…
  `req` 是請求 id（重試不會重複結算）。
- **頁面歸前台容器**（`trpg/frontend-nuxt`：Nuxt 4 SPA＋nginx）；後台**不送任何網頁**，只給
  `POST /graphql`、`POST /mcp`、`/assets/<模組>/…`、`/health`。大廳的資料由前台的根端點查
  `modules／tables／me`（`table_directory.discover()` 走 `layout.scan` 掃桌：新制
  `modules/*/scenarios/*` 與舊制 `campaigns/*` 都吃，`hidden: true` 的桌不載入）。

## 一個人怎麼同時開好幾張桌（**單一進程後台：一個埠＋子路徑**）

**一個 `hub_server.py` 進程裝下所有桌**，對外**只有一個埠**（預設 **8971**），用**子路徑**分桌。
設計＝`docs/HUB.md`：

```bash
deploy/start_hub.sh                         # 起：一個進程把所有（非 hidden 的）桌帶起來
deploy/start_hub.sh status                  # 看各桌狀態（問 /health）
deploy/start_hub.sh --stop                  # 停（一停全停）
deploy/start.sh <campaign>                  # 單桌模式（測試／開發；獨立埠）
```

```
http://<主機>:8971/                     大廳（桌清單＋「角色 → 去哪張桌」＋建桌）
http://<主機>:8971/hyakki_tower/          hyakki_tower
http://<主機>:8971/hyakki_tower/watch/    它的觀戰桌（唯讀；同進程的公開投影）
http://<主機>:8971/hyakki_tower/         百鬼塔
http://<主機>:8971/graphql               **客戶端面（唯一端點）**：網頁／app／第三方都走它
http://<主機>:8971/<桌>/mcp              agent 的 MCP（由適配器轉 GraphQL；桌端不再自己養 MCP）
```

- **登入一次走遍所有桌**：`localStorage` 按 origin 隔離 ⇒ 單一 origin ＝ 只登入一次。
- **GM 喚醒也在同一個進程**（桌的 `kick.receiver_url: "inproc://"` ⇒ 不發 HTTP、不用 `:8972`）。
- 改完 config 用 `/__hub/reload`（只重建那一張桌、從 `events.jsonl` 重播、無損）——見 `docs/HUB.md §5`。

## 觀眾怎麼進來（觀戰桌，**唯讀**）

不想下場、只想看 ⇒ 開**觀戰桌**（唯讀前門；單一入口時掛在 `/<桌>/watch/`，設計＝`docs/PLAN_spectate_table.md`）：

```bash
deploy/start_watch.sh <campaign>        # 單獨起觀戰桌（獨立埠）
deploy/stop_watch.sh <campaign>         # 收掉
```

- 觀戰桌**沒有席位概念**：觀眾不進 `order`、不被算「還沒出手」⇒ **不可能擋回合、也不可能擋收景**。
- 它**只讀**：同一份前台，唯讀態（藏掉登入／入座／行動／發言）；API 面只放行讀
  （`table{ view/log/map }`），**所有 mutation 一律拒**（「觀戰桌沒有寫入面」）。
- **私密一律看不到**（只有公開投影那一份；帶任何 token 都一樣）。
- `require_account` 開著的桌：觀戰桌帶 `config.watch.read_token`（上游桌對這把**只放行公開投影**，不是 GM 全視圖）。
- ⚠ **不要把上游桌的「入座」當觀戰**：入座＝下場（`join` 不指定席位時還會**自動新開一席**），
  在回合制語意下會真的卡住那一輪。

**之後（推薦路線）**：手機 app 裡加一個入口，把同一個頁面塞進 WebView——**不要為 app 另開一條線**：
網頁和 app 都是這一個服務的 GraphQL 客戶端。
