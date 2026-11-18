# 目錄結構與檔案職責

> 從 `README.md` 拆出（2026-10-20）。目錄規範（單檔 < 400 行等）見 `docs/LAYOUT.md`。

## 目錄結構與檔案職責

**2026-10-20 模組化**（完整對照見 `docs/LAYOUT.md`）：程式碼按功用收進 `trpg/`（入口仍在 repo 根），單檔一律 < 400 行；
測試統一在 `tests/`（`python3 tests/test_xxx.py`；舊的 `test_xxx.py` 名稱不變，`tools/run_tests.sh`
會自動到 `tests/` 找）。目錄：

```
server.py / hub_server.py / lobby_server.py / watch_server.py / kick_receiver.py / table_mcp.py
    └─ 薄入口（只做 argparse／sys.path，實作在 trpg/）
trpg/
  core/       errors / db / store / layout / pacing / replay（跨層共用）
  rules/      packs/ · scenario/ · board/ · semic/ · conditions · skills · advancement
              items · stars · rescue · shop · monsters · death · dsl · expr · action_catalog · pack_kernel
  engine/     table · lifecycle · actions(act/social) · turns · reading · projection/ · presentation · director · audio · damage
  service/    service · table_factory · table_directory · table_mcp · assets · audio · wake/ · run_owner · run_archive
  accounts/   auth/ · db/ · account_store
  api/        web/ · graphql/ · mcp_api/ · hub/ · lobby/ · watch · mcp_adapter
  kick/       sender · receiver/
tests/        全部 test_*.py ＋ test_support.py（含靜態閘的 source_text 解析）
tools/        運維／遷移腳本（tools/oneoffs/＝一次性取證腳本）
```

| 文件 | 职责 |
|---|---|
| `config.json` | 唯一配置：规则、回合、超时策略、唤醒、GM token、**account 層（`auth`）／session 規範（`session`）**。**没有硬编码阈值** |
| `trpg/engine/` | 权威层（`table`／`lifecycle`／`actions`／`turns`／`reading`）。纯代码、零依赖。骰子可复算（`seed:roll_seq`） |
| `trpg/rules/board/` | 棋盘/塔玩法（地图、移动、入口、昇降機、小怪、祈願、収奪、记忆）——数据全在 board.json |
| `trpg/rules/semic/` | 半合作模式（场景制、争抢宣言、agent 节流、碎片、私密愿望、泄漏闸、GM 工具面、暗骰代投） |
| `trpg/rules/packs/` ＋ `trpg/rules/pack_kernel.py` ＋ `packs/` | **机制包**：模块边界宣告＋启用清单＋合并视图（`config.packs`）；`pack_kernel.py`＝「包只能宣告内核真的有的东西」的池子与核对。见 `packs/README.md` |
| `trpg/rules/conditions.py` | **条件机制**（部位受伤／异常状态）：每回合结算、判定加减值、事件溯源。内容在包的 `tables/conditions.json`（`packs/wound/`） |
| `server.py` | 桌服务：GraphQL 端點 ＋ HTTP API（`/api/*`，退場中）＋ HTTP MCP ＋ 网页 ＋ kick ＋ 节拍 |
| `trpg/api/graphql/` | **客戶端面（一個端點；桌是欄位）**：契約＝`docs/schema.graphql`；mutation＝「同一支 handler 換入口」 |
| `modules/<模组>/module.json` | **模组自己的命名**（世界层设定：`{id, name, note}`）——大廳／app 的桌清單標題用它；沒寫退回目錄名 |
| `modules/<模组>/characters.json` | **模组的角色表**（每个角色一个 ID ＋ **`class` ＝角色的类，由模组定**）——账号进场＝在模组底下建/沿用角色**实例**；剧本 `seats.json` 只决定这一局谁参与、坐哪 |
| `trpg/accounts/`（`auth/` ＋ `db/` ＋ `account_store.py`） | **account 層**：`auth`＝JSON registry（legacy）；`accounts_db`＝**關聯式後端**（`account×模組` 錢包／`account×模組×角色` 實例／`+副本` 參與列）；`account_store`＝挑後端的那一支。设计＝`docs/PLAN_account_model_v2.md` |
| `trpg/core/db.py` ＋ `vendor/` ＋ `deploy/db_up.sh` | PostgreSQL（容器 `trpg-postgres`）：DDL／連線／起停；driver（pg8000）**vendored**，不动共享 runtime |
| `tools/trpg_db.py` | DB 維運：`init`／`import-json`（從舊 registry 匯入）／`list`／`issue`／`verify`／`set-wallet` |
| `trpg/service/table_mcp.py`（根目錄 `table_mcp.py`＝入口） | stdio↔HTTP 的 MCP 桥（给只支持 stdio 的客户端；无业务逻辑） |
| `trpg/engine/audio.py` ＋ `trpg/service/audio.py` | **音訊**（BGM／效果音）：`engine`＝播放控制（`audio` 事件／兩個聲道／淡入秒數／現況投影，回放一致）；`service`＝讀模組的音訊表（別名制、缺＝沒有、壞資料只警告）。內容（曲目／自動提示）住 `modules/<模組>/audio.json`。見 `docs/AUDIO.md` |
| `trpg/engine/damage.py` | **HP 扣血的唯一出口**（`damage_apply`）：`hp_after` 的下限 0 只在這裡算——5 個傷害來源（attack／roll_damage／fight 失敗／主動怪／條件 tick）都調它。另含 `damage_guard()`＝「送還中（不在場）打不到」。**不在 `_apply` 加 clamp**（否則舊局的負數歷史事件一重放就漂） |
| `web/index.html` | 人类界面（手机可用） |
| `runs/<session>/events.jsonl` | 事件日志（唯一真相；**一局一档**）。`runs/current`＝现役那一局。删掉某一局＝丢那一局；删掉 `runs/current` 会自愈成最新一局 |
| `modules/<模组>/scenarios/<剧本>/` | **剧本（定义层）**：scenario.json（剧本规则资料；旧名 module.json 兼容）／board.json／seats.json／goals.json／clues.json／config.example.json／packs/／WORLD·GM·TABLE.md——引擎**只读**。住模组 repo，**不含任何桌的 config／事件流** |
| `state/tables/<模组>/<剧本>/<桌ID>/` | **一张桌的实体**：config.json ＋ `runs/<session>/`（＋ accounts.json／data_*_archive）＝这一台机器的执行状态，**不進版控**、不属于模组 repo。位置一律走 `layout.table_home()`（对外 `table_id` 与目录名解耦）；迁移工具＝`tools/migrate_tables_to_state.py` |
| `modules/<模组>/`（**部署挂载点**） | **不是本仓的内容**：一个模组＝一个世界观＝**独立 repo**（Gitea `trpg/module-hyakki-tower`／`trpg/module-hyakki-tower`）。本仓 `.gitignore` 掉 `modules/`；部署＝`deploy/modules_up.sh`（清单 `deploy/modules.list`）。**加新剧本＝在那个模组 repo 加 `scenarios/<id>/`，本仓不动** |
| `tests/test_table.py` | 抽象桌自测 |
| `tests/test_board.py` | 棋盘桌自测 |
| `tests/test_semic.py` | 半合作桌自测 |
| `tests/test_packs.py` | **机制包闸**（解析／合并／与内核一致／行为等价／冻结／消费端／作者入口） |
| `tests/test_conditions.py` | **条件闸**（表／legacy 零变更／投治／判定修正／每回合结算／投影／effect 收窄） |

> ⚠ 各檔的**項數會長**，不寫死在這裡——跑 `bash tools/run_tests.sh`（或 `python3 tests/test_xxx.py`）看現值。
