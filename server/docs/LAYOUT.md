# 目錄結構（2026-10-20 模組化）

程式碼按功用收進 `trpg/`，單一檔案（不含測試）一律 < 400 行；測試統一在 `tests/`。
根目錄只留入口與設定範本。

```
server.py / hub_server.py / lobby_server.py / watch_server.py / kick_receiver.py / table_mcp.py
    薄入口：只做 argparse／sys.path，實作在 trpg/ 對應模組

trpg/
  paths.py        ROOT／VENDOR（唯一從 __file__ 推 repo 根的地方）
  core/           errors · db · store · layout · pacing · replay
  rules/          packs/ · scenario/ · board/ · semic/ · conditions · skills · advancement
                  items · stars · rescue · shop · monsters · death · dsl · expr
                  action_catalog · pack_kernel
  engine/         table · lifecycle · actions(act／social) · turns · reading
                  projection/ · presentation · director
  service/        service · table_factory · table_directory · table_mcp · assets
                  wake/ · run_owner · run_archive
  accounts/       auth/ · db/ · account_store
  api/            web/ · graphql/ · mcp_api/ · hub/ · lobby/ · watch · mcp_adapter
  kick/           sender · receiver/

tests/            全部 test_*.py ＋ test_support.py
tools/            運維／遷移腳本；tools/oneoffs/＝一次性取證腳本
deploy/ docs/ packs/ vendor/ (不變)
```

## 資料層級（2026-10-20：桌＝run）

```
modules/<模組>/scenarios/<劇本>/          # 定義層（一處改、每個 run 吃到）
state/tables/<模組>/<劇本>/<run_id>/      # **一個 play 實例＝一個 run＝一張桌**
  config.json                             # 這一 run 的旋鈕（`table_id`／`run_id` 同值）
  runs/<run_id>/events.jsonl              # 事件流（append-only）
  runs/<run_id>/owner                     # 這一 run 的 owner
  runs/<run_id>/watch.json                # 這一 run 的觀戰開關（沒有檔＝舊局，退回 config）
  runs/current                            # 指到 `<run_id>`
```

* `createTable`／`newRun` 都建立**新的 run**；`newRun` **不再換掉舊局**（舊 run 留在原位，仍可看／回放）。
* `newRun` 是「同一個劇本再開一個 play 實例」；新 run 沿用劇本的 packs／難度等設定，但有自己的
  `table_id`、GM token、owner、座位（`run_seat`）、觀戰開關與事件流。
* 舊資料（一個桌目錄裡有多個 `runs/<uuid>/`）的事件流仍在磁盤上、可回放；新流程不會再往同一個 home 塞第二個 run。
  API 的「每一個歷史 run 各自一張桌」入口還沒做（需要拆分／搬家腳本），目前只有最後那一局是現役。

## 舊檔名 → 新位置

| 舊 | 新 |
|---|---|
| `engine.py` | `trpg/engine/`（`Table` 由 `lifecycle/actions/turns/reading` 等 mixin 組成） |
| `projection.py` | `trpg/engine/projection/` |
| `presentation.py` / `director.py` | `trpg/engine/` |
| `board.py` / `semic.py` | `trpg/rules/board/` / `trpg/rules/semic/` |
| `packs.py` / `scenario.py` | `trpg/rules/packs/` / `trpg/rules/scenario/` |
| `conditions.py` 等 L1 | `trpg/rules/` |
| `layout.py` / `store.py` / `db.py` / `pacing.py` / `replay.py` / `errors.py` | `trpg/core/` |
| `service.py` / `table_factory.py` / `wake.py` / `assets.py` … | `trpg/service/` |
| `auth.py` / `accounts_db.py` / `account_store.py` | `trpg/accounts/` |
| `web_api.py` / `graphql_api.py` / `mcp_api.py` / `hub_server.py` / `lobby_server.py` / `watch_server.py` | `trpg/api/` |
| `kick_sender.py` / `kick_receiver.py` | `trpg/kick/` |
| `test_*.py` / `test_support.py` | `tests/` |
| `_trial_*` / `_verify_*` / `_migrate_*` 等一次性腳本 | `tools/oneoffs/` |

## 呼叫端

- 腳本照舊：`python3 server.py`、`python3 hub_server.py`、`python3 tests/test_board.py`
- Python 內：`from trpg.engine import Table`、`from trpg.rules import packs as packs`
- 測試的靜態閘用 `test_support.source_text("engine.py")` 取「舊檔名」對應內容
  （拆套件後同目錄多檔會串起來）。
