# 大廳（入口頁）——這台機器上的每一張桌

> ⚠ 2026-10-07 起「入口」是**單一進程後台**（`hub_server.py`／`deploy/start_hub.sh`，
> **一個進程裝所有桌、對外只一個埠**、用**子路徑**分桌）——見 `docs/HUB.md`。本檔的大廳頁面（`lobby_server.py` 的
> `render()`）**由前台在 `/` 直接供應**（`prefix_mode=True`，連結走 `/<桌>/`）；
> `lobby_server.py` 仍可單獨跑（開發／除錯用），但**平常不用**。

> 2026-10-07 設計者：「我原本想着是同一个IP起服务，有多个桌子」⇒ 補一個入口頁；
> 同一輪把 `?seat=` 相容期連結收掉（進桌一律走帳號）。

## 0. 它在架構裡的位子

一張桌＝**一個 process／一個埠**（`server.py`）。大廳**不是**桌、也不代理桌的內容——它只是
**一張看板**：掃 `campaigns/*/config.json`、對每張桌的 `/health` 與 `/api/log?meta=1` 探活，
把「哪幾張桌開著／離線／已收局」列出來，並附上連到**那張桌自己埠**的連結。

```
lobby_server.py        大廳（只讀、獨立埠，預設 8998；不寫除了自己 log 以外的任何檔）
  GET /                大廳頁（純靜態 HTML，**沒有 script**）
  GET /api/tables      同一份資料的 JSON（測試／腳本用）
  GET /health          {"status":"ok","service":"trpg-lobby","tables":N,"online":M}
  （其餘一律 404；沒有 act／say／tell／join／enter／login／mcp —— 它不碰任何桌的狀態）
deploy/start_lobby.sh  起停（`--stop` 收；一顆 pidfile＝logs/lobby.pid，log＝logs/lobby.log）
```

## 1. 每張桌怎麼被列出來（旋鈕都住各桌 `config.json`，全部可省）

| 欄位 | 作用 |
|---|---|
| `table_id` | 卡片上的桌 id（省略＝目錄名） |
| `port` | 桌子自己的埠（也決定「進入」連結） |
| `scenario` | 顯示名（卡片標題取「──」前那段；省略＝用 `table_id`） |
| `hidden: true` | **這一桌不要出現在大廳**（測試桌／臨時桌用） |
| `watch: {enabled, port}` | 有開觀戰桌就多一顆「觀戰」連結 |
| `auth.accounts_file` / 同目錄 `accounts.json` | 決定卡片上寫「帳號＋密碼登入」還是「沒有帳號層」 |

探活只用兩個**不需憑證**的端點：`/health`（狀態／席次／回合）與 `/api/log?meta=1`
（事件筆數／尾序號——這是刻意開的最小資訊窗，見 `web_api.py` 的註記）。
「已收局」＝**尾讀**各桌 `data/events.jsonl` 的最後一筆 `table_created`／`ending` 誰在後
（同機、同使用者、唯讀；新開一局會在 `ending` 之後再寫 `table_created`，所以不會誤判）。

## 2. 進桌的流程（`?seat=` 已收掉）

1. 開大廳（`http://<主機>:8998/`）→ 點某張桌的「進入」→ 連到**那張桌自己的埠**。
2. 在那一頁**登入**（帳號＋密碼，或貼 token）——席位由 **token** 決定（`account → char → seat`）。
   貼 `?seat=p1&name=X` **不再等於坐在那一席**（`auth.seat_link` 預設 false）。
3. 觀戰（想只看不下場）走那張桌的**觀戰桌**埠（有開才有連結）。

## 3. 帳號邏輯（設計者 2026-10-07 定案——這一段是規則，不是實作細節）

```
帳號 login ──► 選角色（pick_char）──► 這個角色**有戲的桌** ──► 進場（enter）
                    │                        │
                    │                        └─ 座位號**由劇本定死**：char ↔ seat 來自該桌 seats.json，
                    │                          玩家只挑「哪個角色」，不能挑「坐哪一席」
                    └─ **同一個帳號的同一個角色，不能同時在兩張桌**
```

* **一個角色一份實例**：`account × 模組 × char` 就是那份跨局實例（成長 level／exp 跟著它；
  錢包是 `account × 模組`，見 `docs/PLAN_account_model_v2.md`）。
* **「不能同時兩張桌」的判準＝別的桌「開著且還沒收局」**：
  * 別的桌**線上且未收局** ⇒ 那個角色**還在出場** ⇒ `POST /api/pick_char` 與 `POST /api/enter`
    都擋（**409**，訊息指名是哪張桌、事件幾筆）。
  * 別的桌**已收局**（`ending`）或**離線** ⇒ 放行（不因為有人關機就永遠卡住；
    收局之後那個角色就能帶去下一張桌）。
  * 自己那張桌不算；同一個帳號的**不同角色**可以分別在兩張桌（那是兩個實例）。
* `GET /api/me` 會回 `chars_busy`（我的哪個角色還在哪張活桌出場）⇒ 前端可以標灰／說明原因。
* ⚠ 這條規則靠**兩張桌共用同一份 registry**（`auth.accounts_file`）才看得到彼此的座次；
  各自一份 registry 的桌看不到別桌 ⇒ 規則在那些桌上是 no-op（不會誤擋）。
  實作：`web_api._busy_tables()`／`_busy_msg()`；「活著沒」問 `table_directory`（與大廳同一支）。
* 端到端驗收：`_trial_exclusive_tables.py`（兩張活桌、7 條）＋ `test_auth.py` S8（6 條）。

## 4. 起停／驗收

```bash
deploy/start_hub.sh                      # 單一進程後台（唯一一個埠）：/ 大廳、/<桌>/、/<桌>/watch/
deploy/start.sh <campaign>               # 單桌模式（測試／開發；獨立埠）
$PY test_hub.py                          # 22 條（分派／隔離／每桌工具目錄／觀戰唯讀／reload 無損／inproc kick）
$PY test_lobby.py                        # 22 條（掃桌／探活／收局判定／頁面／只讀紀律／health／角色→桌）
```
部署机铁律：**不寫 systemd／cron、不動開機項**——機器重開要自己再起一次（或用 `deploy/` 的腳本）。

## 5. 安全姿態（刻意如此）

* **不代理任何桌的內容**：不做 `/api/state` 轉發、不帶 token、不讀 registry ⇒ 就算大廳整台被外人看到，
  也只看到「有哪幾張桌、開著沒、幾筆事件」這種**運維級**資訊（不含任何局內內容、不含私密）。
* 頁面**沒有 JavaScript** ⇒ 沒有 XSS 面（掃到的字串全部 HTML-escape）。
* 它**唯讀**：不寫任何桌的檔（只寫自己的 log／pidfile）。
