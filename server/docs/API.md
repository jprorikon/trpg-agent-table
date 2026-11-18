# 端點（GraphQL 為主，REST 退場中）

> 從 `README.md` 拆出（2026-10-20）。客戶端面契約＝`docs/schema.graphql`；單進程／子路徑＝`docs/HUB.md`。

## 端點

| 方法 路径 | 说明 |
|---|---|
| `GET /api/state?seat=<id>` | 该席位的投影（加 `?gm_token=` 得 GM 全视图；**config 没设 `auth.gm_token` ⇒ 一律当公开投影**，2026-10-24） |
| `POST /api/join` | `{name, kind, attrs?, wake_url?, seat?}` |
| `POST /api/act` | `{seat, action, target?, attr?, dc?, note?, skill?, item?, req?}`——**`req`＝請求 id（冪等鍵）**；技能／道具走 `action:"use"`（`cast`／`use_item` 仍收同義呼叫） |
| `POST /api/say` | `{seat, text}`；`seat=gm`（公開敘事）需 `token`＝`auth.gm_token`，否則 **403 `bad gm token`**（同 `/api/gm` 的碼） |
| `POST /api/gm` | `{token, op: scene\|secret\|start_turn\|end_turn\|heal\|kick\|replay\|pause\|resume, ...}` |
| `GET /api/log` | 全部事件（含 GM 用的原始流） |
| `POST /mcp` | MCP over HTTP（Streamable HTTP） |
| `POST /hook/inbox` | 无 MCP 席位的动作入口（动作字段与 `/api/act` 同一套：`skill`／`item` 也转发） |
| `GET /assets/<模組>/…` | 模組級素材（圖／**音訊**；音訊支援 `Range` ⇒ 206、`audio.json` 是音訊表） |
| `GET /health` | 探活 |

### 音訊（BGM／效果音）

資源綁模組（`modules/<模組>/audio.json` ＋ `assets/audio/…`），**引擎只做播放控制**：
`table_gm op=audio_play|audio_stop|audio_list`（開景可帶 `bgm`），投影多了 `View.audio`
（`{bgm, sfx, seq, library}`；GM 另有 `available`／`cues`），事件流多了 `audio` 事件。
客戶端怎麼播（autoplay 解鎖、`seq` 判新、淡入淡出）見 **`docs/AUDIO.md`**。

### 動作請求冪等（`req`）

`POST /api/act`／MCP `table_act`／GraphQL `act`／`/hook/inbox` 都吃一個可選的 **`req`**（請求 id）。
同一個 `seat + req` 在同一局內**只結算一次**：

- 第一次：正常結算（宣告寫進事件流，`action_declared.req` 記下這個鍵）；
- 重送（同一個 `req`）：回 `{duplicate: true, req, action, seq}`——**不再擲骰、不再扣額度**，
  `seq` 是第一次那筆宣告，結果照 `table_view` 看；
- **失敗被標 `rejected` 的那一手不算**（上一次根本沒成立）⇒ 同一個 `req` 可以重試；
- 索引就是**事件流本身** ⇒ 重啟／跨進程／回放都認得，不必另存一份。不帶 `req` ⇒ 舊行為。

agent 的重試禮儀：**一手一個 `req`（UUID 就行），重試時原樣帶回去**。

### 整桌暫停（**桌主的总闸开关**）

```bash
python3 tools/hold_trpg.py status   # 只看状态（不动桌子）
python3 tools/hold_trpg.py          # 暂停整桌（= /hold-trpg）
python3 tools/hold_trpg.py off      # 恢复（= /hold-trpg off）
```

- 语意：**停叫醒（含 GM 自己）／停计时／停超时代打／不自动开局／牌面冻结（`act` 一律 409）**；
  `say`／`tell` 照常（冻的是牌面不是对话）。恢复＝把冻掉的秒数**还给当前回合**＋重踢当前席位。
- 脚本**自己找活着的桌**（扫桌的位置打 `config.hub_base` 的 `/health`——hub 模式下桌自己的 port 没在听；
  **要用 `--session <副本 id>` 指定是哪一局**，多局还没收局时不猜，
  或 `--campaign <id>` 指定），**自己从该剧本 `config.json` 读 `gm_token`**（token 不当参数、不打印）。
- 与网页 app 的「暂停整桌／继续」、`POST /api/gm {op:pause|resume}` **同一条路**（同一套引擎语义）。
- GM agent 那边对应 **`/hold-trpg`** 指令（bundle → skill `trpg-hold`）。
- 闸：`test_hold_tool.py`（H1–H7；含「送出的 op 名字要是桌子的 `pause`／`resume`」的回归）。
