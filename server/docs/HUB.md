# 後台（單一進程）＋ 單一入口（一個埠、子路徑）

> 設計者 2026-10-07：「**不要用多端口，用子路径**」→「**trpg后台还是做成单一进程吧**」。
> 設計與取捨：當時兩份計畫（第一步一個埠／第二步一個進程）已於 2026-10-20 清除，結論即本文。

## 0. 一句話

**一個 `hub_server.py` 進程裝下所有桌**，對外**只有一個埠**（預設 8971），用**子路徑**分桌。
桌不再各自一個行程——它們是**同一個進程裡的一份 `TableService`**（各自一份狀態、各自的鎖、各自的節拍執行緒）。

```
http://<主機>:8971/                     大廳（桌清單＋「角色 → 去哪張桌」）
http://<主機>:8971/hyakki_tower/          hyakki_tower
http://<主機>:8971/hyakki_tower/watch/    它的觀戰桌（唯讀；同進程的公開投影）
http://<主機>:8971/hyakki_tower/         百鬼塔
http://<主機>:8971/<桌>/mcp              agent 的 MCP（同進程）
http://<主機>:8971/health                各桌狀態（直接問記憶體，不探 HTTP）
```

## 1. 為什麼（三個理由，按重要性）

1. **登入一次走遍所有桌**：`localStorage` 按 origin（`scheme://host:port`）隔離 ⇒ 多埠＝每張桌都要登入一次。
   單一 origin ＝ 貼一次 token 全部認（接上「一把憑證跨桌通用」的帳號層）。
2. **對外只剩一個門**：不再有一堆埠要記、要開、要顧；桌的 `port`／`bind` 在 hub 模式下**不生效**。
3. **沒有內部 HTTP 跳轉**：觀戰＝同進程的公開投影（不再需要第二個行程與 `watch.read_token`）；
   「這個角色是不是還在別張活桌出場」也**直接查記憶體**（不再探別人的埠）；GM 喚醒同理（見 §3）。

## 2. 為什麼一進程是安全的（先量過）

| 檢查 | 結果 |
|---|---|
| 每桌的鎖 | `Table.lock = threading.RLock()`（`engine.py:245`）**本來就有** ⇒ 桌與桌互不阻塞 |
| 背景節拍 | `KickMixin.tick_loop` 每桌一條執行緒（髒活各自獨立；`stop.wait(2.0)` 讓停桌很乾淨） |
| 模組級可變狀態 | **踩到一個**：`mcp_api.TOOLS` 是共用清單、`server.py` 以前直接改它 ⇒ 一進程多桌會**互相污染** 工具描述。已改成 `mcp_api.tools_for(cfg, home)` 每桌一份（`svc.tools`），`test_hub H6` 就驗這個 |
| 挑服務 | `web_api.Handler.service` 是**類別屬性** ⇒ hub 在**每個請求**設**實例屬性**覆蓋（執行緒安全、`web_api` 不用改） |
| 一桌炸掉 | 每個請求包 try/except ⇒ 回 500、**不帶走別桌**（`test_hub H11`） |

## 3. GM 喚醒也收進同一個進程（`inproc://`）

桌的 `config.kick.receiver_url = "inproc://"` ⇒ `kick_sender` **不發 HTTP**，把**同一份 payload**
直接交給同進程的接收器（`kick_receiver.install_local()`；hub 啟動時自動裝）。
* session 名／冪等鍵／「失敗不影響桌上」與 HTTP 那條**一字不差**（`trpg-<桌>-gm`）。
* 不再需要 `:8972`、`receiver_token_file`。
* **跨機**的接收器（例如 協作端 那台）仍寫 `http(s)://…` ⇒ 兩條並存，由 config 決定。
* 獨立接收器（`kick_receiver.py` + `deploy/kick_receiver.sh`）保留給跨機／除錯，production 不用它。

## 4. 旋鈕

| 位置 | 鍵 | 說明 |
|---|---|---|
| 桌 `config.json` | `public_prefix` | 對外子路徑（＝`/<table_id>`）；沒寫＝空＝舊行為 |
| 桌 `config.json` | `kick.receiver_url` | `"inproc://"`＝同進程（hub）；`http(s)://…`＝跨機接收器 |
| 桌 `config.json` | **`hub_base`** | **這張桌現在真的在哪**（別的行程打得到的 URL，例 `http://127.0.0.1:8971/<桌>`）。GM 喚醒的「桌況查詢」與 `tools/gm.py` 都用它——hub 模式下桌自己的埠**沒在聽**，沒設會叫醒一個**看不到桌**的 GM |
| 桌 `config.json` | `hidden: true` | **hub 不載入**（測試／開發桌請用單桌模式 `server.py`） |
| 桌 `config.json` | `port`／`bind`／`watch.*` | **hub 模式下不生效**（單桌模式與獨立觀戰桌才用） |
| 桌 `config.json` | `auth.gm_token` | **每桌一把**：`Hub.create_table` 建新桌時重新生成（同模組只繼承 `db_env`／`db`／`require_account`／`kick`／`wake`），不再讓同模組所有桌共用一把 |
| 局 `runs/<run>/watch.json` | `enabled` | **局級**觀戰開關（2026-10-20 設計者：「所有的都是局」）。`setWatchable(tableId,session)` 只改那一局；舊局沒有檔才退回 `config.json › watch.enabled`（部署預設／相容）。`watch.port`／`read_token` 仍是部署層 |
| hub | `TRPG_HUB_PORT`（預設 8971）、`TRPG_HUB_RELOAD`（>0＝每 N 秒自動 rescan） | `deploy/start_hub.sh` |
| hub 管理面 | `logs/hub.token`（600，首啟自動產生） | `/__hub/reload`、`/__hub/rescan`、`/__hub/status` |

## 5. 起停與維運

```bash
deploy/start_hub.sh              # 起（一個進程把所有非 hidden 的桌帶起來）
deploy/start_hub.sh status       # 問 /health（各桌：開著／已收局／離席）
deploy/start_hub.sh --stop       # 停（一停全停——它本來就是一個進程）

# 只重建某一張桌（改完 config 用這條；從 events.jsonl 重播、無損、不影響別桌）
curl -H "X-Hub-Token: $(cat logs/hub.token)" -X POST localhost:8971/__hub/reload \
     -d '{"table":"hyakki_tower"}'
# 收新桌／移除已刪的目錄（不動還在一起的桌）
curl -H "X-Hub-Token: $(cat logs/hub.token)" -X POST localhost:8971/__hub/rescan
```

**單桌模式**（測試／開發）仍然可用：`deploy/start.sh <campaign>` ＋ `server.py`（一張桌一個行程、獨立埠）。

## 6. 取捨（要知情的）

* **一桌崩＝全崩**：靠「每個請求包 try/except ＋ 一桌一份鎖」把個別錯誤關在那一桌（`test_hub H11`）；
  但整台進程若被 kill／OOM，全部桌一起停（重啟後各自從 `events.jsonl` 重播，**狀態無損**）。
* **重啟粒度**：單桌重啟＝`/__hub/reload`（無損）；整台重啟＝全部重播（也無損，但全桌短暫中斷）。
* **GIL**：所有桌的請求共用一個 GIL——桌的負載是 web＋寫事件（很輕）；重活（agent 推論）在 GM agent 那邊。

## 7. ⚠ 外部 URL 契約（**2026-10-19 上線版**：前台容器站 8971，後台退 18971）

| 誰 | URL |
|---|---|
| 玩家／GM 瀏覽器 | **`http://<主機>:8971/`**（前台容器：大廳／`/tables/<run>`／`/watchtables/<run>`） |
| 前台 → 後台（nginx 反代） | `POST /graphql`、`/assets/<模組>/…`、`/health`、`POST /mcp` |
| 後台直連（內網／維運／其他 agent） | `http://<主機>:18971/graphql`（同一個 API，不經 nginx） |
| agent 的 MCP | `POST /mcp`（多桌共用；桌由 `session`／`arguments.table`／`X-Table-Id` 決定） |
| 本機橋（外部 agent p4） | `TRPG_TABLE_URL` 可維持 `:8971/<桌>`（橋會自己拆成 hub＋桌名，打 `:8971/graphql` 由 nginx 反代） |
| GM 喚醒（引擎 → 接收器） | 同進程 `inproc://`（**不用動網路設定**）；跨機才寫 http(s) URL |

★ **後台不送任何網頁**：`/`、`/index.html`、`/tables/<run>`、`/watchtables/<run>`、`/<桌>/…`
一律 410／404。觀戰頁是前台的 `/watchtables/<run>`，資料走根 `/graphql`（匿名只讀）。

★ **訪問控制按 query 做**：`operation=query` ＋ 沒帶憑證 ＋ `auth.require_account=true` ＋
這一局 `watch.json` 的 `enabled=true`（或舊局退回 `config.watch.enabled=true`）⇒ 公開投影（＝舊觀戰前門那一份）；`mutation` 一律要憑證。

★ **重啟方式**：`TRPG_HUB_PORT=18971 deploy/start_hub.sh`（預設值已是 18971；8971 被前台容器佔著，
起錯了會直接報「埠已被佔用」）。

## 8. 驗收（實測）

* `test_hub.py` **22/0**：分派到正確的桌（與同進程直算的投影逐欄一致）／一進程裡的隔離（寫 A 不動 B）／
  **每桌自己的工具目錄**（B 桌不會出現 A 桌的棋盤字樣）／觀戰唯讀（`__WATCH__`＋前綴、POST 404、`/api/me` 404）／
  `hidden` 不載入／未知桌 404 回桌清單／**壞請求不帶走別桌**／管理面 token＋`reload` **無損**（事件數與席位不變）／
  `rescan` 收新桌＋移除已刪／**`inproc://` 不發 HTTP** 且 payload 帶對的桌／席位／session。
* 真機：`ss` 只剩**一個** listener `:8971`（18971／18986／18990／8972 全消失）；大廳／兩張桌／觀戰／MCP／`/health`
  全 200；示範模組 狀態無損（6 席、`ended=e_boss_down`、p1 的角色）；橋（指 `:8971/hyakki_tower`）**不用改**即通；
  瀏覽器**一次登入走兩桌**（`/hyakki_tower/` 與 `/hyakki_tower/` 各自顯示自己的席位與角色）。
