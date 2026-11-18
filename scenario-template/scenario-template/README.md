# 劇本腳手架（給**既有模組**加一齣戲）

複製這一包 → 改名成你的劇本 id → 放進 `modules/<模組>/scenarios/<劇本id>/`。

⚠ **id 要有意義**（設計者 2026-10-07：「id 應該反映劇本意思」）：用那個劇本的短名（例：`harbor_night`／
`sannenme_no_onibarai`），**別用** `main`／`example`／`test` 這種佔位符——id 會出現在路徑、GraphQL 與
工具輸出裡，事後改要搬目錄（`tools/rename_scenario.py` 可以改，但一開始就對最省事）。

```
scenario.json       ★ 這一齣戲的規則資料（實體／效果／觸發／結局）
board.json          ★ 地圖（房間／門／東西）
seats.json          ★ 這一局誰上場、坐哪個席（角色 id 必須在模組的 characters.json 裡）
goals.json clues.json   目標／線索（投影與 GM 判據）
config.example.json     桌的旋鈕底稿（複製成 config.json 給某一張桌用；**config.json 不進版控**）
GM.md TABLE.md WORLD.md 寫給人看的（GM 手冊／桌面說明／世界查證）
scene1.json             場景卡（可多張）
packs/<包id>/           作品層包（怪物／道具／商店／規則書）——同一劇本的桌共用
```

**不要動**模組層的 `module.json`／`characters.json`（那是世界層）；要新增角色 ⇒ 改模組層的 `characters.json`。
**同一個劇本可以開好幾張桌**，定義只有這一份。卓的執行實體在伺服器端 `state/tables/<模組>/<劇本>/<桌ID>/`，`config.json`／`runs/` 不進版控；帳號層（角色實例／錢包／座次）住引擎 PostgreSQL（`auth.db_env`），模組 repo 不帶任何帳號資料。

自檢：
```sh
tools/check_module.sh --module <模組目錄> <這一包>   # 指定所屬模組自檢
tools/check_module.sh <這一包>                     # 臨時湊角色表驗語法
```

## 這一包已經照「設計手冊」調過（別把示範刪掉）

設計原則與理由＝ **`docs/SCENARIO_DESIGN.md`**（七條原則＋檢查清單＋現役劇本盤點）。這一包裡的示範：

- `clues.json`：**每條結論三枚碎片**，其中至少一枚來自另一個 `pool`（`tower` ↔ `outside`）——三線索的備援。
- `board.json`：起點 `town` 有**兩條出路**，而且 `town → field → tow → town` 是一個**環路**；塔層才是單鏈。
- `scenario.json` 的 `endings`：多了 `e_retreat`（**neutral**：第 3 景後全員回城鎮＝撤退）。`win` 排在它前面（先搶先贏）。
- `scenario.json` 的 `triggers`：多了 `t_night_falls`（**世界自己的時鐘**：第 4 景後夜就深了）。
- `scenario.json` 的 `requirements.g_b`：示範 **`or` 替代路線**（拿不到 c_2 也能用 c_1＋兩份旁證）。

交桌前兩條指令：

```sh
tools/check_module.sh --module <模組目錄> <這一包>   # 能不能起桌（ERROR 會擋）
python3 tools/design_lint.py <這一包>              # 設計味道（WARN 看一眼，INFO 自己判斷）
```
