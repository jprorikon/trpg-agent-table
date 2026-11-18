# TRPG 桌（人類 ＋ AI agent 同席）

> **2026-10-20 模型**：模組 › 劇本 › **run（＝桌）**。一個 run 是一個劇本的 play 實例；
> `table_id`＝`run_id`＝`session`，一個 run 一個 home（`state/tables/<模組>/<劇本>/<run>/`）。
> `createTable` 與 `newRun` 都是「再開一個 run」；舊 run 不會被換掉。

给「人类玩家 + AI agent 同席」的桌游服务。**一个服务，三种入口**——引擎只有一份状态，
入口都只是薄适配器。

```
human  ──网页 / 消息──┐
                       ├──►  server.py（唯一持有引擎） ──► trpg/engine/（权威层：规则/骰子/事件日志）
agent  ──MCP 工具────┘                    runs/<session>/events.jsonl（append-only，可回放）
「轮到你」──kick（出向 webhook）◄──┘
```

铁律（决定了 AI 能不能上桌）：
1. **数值只由引擎产出**。骰子、HP、伤害全部在 `trpg/engine/` 里算，模型只能"声明动作"，没有写权限。
2. **信息防火墙**。`view()` 只投影"该席位该知道的"：私密信息带持有者，别人看不到。
3. **一切可回放**。状态由 `runs/<session>/events.jsonl` 推导，任意时刻可重放核对（`op=replay`）。

---

## 先看哪一份

| 我想… | 看 |
|---|---|
| 起／停、部署方式、怎麼自測 | `docs/RUN.md` |
| 目錄結構、每個檔案的職責 | `docs/FILES.md` |
| 桌子怎麼玩（棋盤制／半合作桌） | `docs/TABLE_MODES.md` |
| 人怎麼進來（大廳、一個埠多桌、觀戰桌） | `docs/HUMAN.md` |
| agent 怎麼進來（MCP ＋ 喚醒） | `docs/AGENT.md` |
| 規則旋鈕（`config.json`） | `docs/CONFIG.md` |
| HTTP 面（GraphQL／REST／暫停整桌） | `docs/API.md` |
| **音訊**（BGM／效果音：資源綁模組、引擎只做播放控制） | `docs/AUDIO.md` |
| 後台單進程、子路徑、`/__hub/reload` | `docs/HUB.md` |
| 大廳那一面（桌清單／角色去哪張桌） | `docs/LOBBY.md` |
| 劇本格式（scenario.json／board.json／seats…） | `docs/SCENARIO_FORMAT.md` |
| 機制包規格（引擎層／模組層／劇本層） | `packs/README.md` |
| GM 的規矩 | `GM.md` |
| **還沒做／等拍板** | `docs/PENDING.md` |
| 目錄規範（單檔 < 400 行、測試位置） | `docs/LAYOUT.md` |

## 最快起跑

```bash
cd /srv/trpg
./deploy/start_hub.sh            # 一個進程裝下所有桌（預設 :8971，子路徑分桌）
./deploy/start_hub.sh status     # 各桌狀態
./deploy/start_hub.sh --stop     # 一停全停
```

- 大廳 `http://<主機>:8971/`；各桌 `/<桌>/`；觀戰 `/<桌>/watch/`
- 客戶端面（唯一端點）`/graphql`；agent 的 MCP `/<桌>/mcp`
- 單桌開發模式（獨立埠）：`./deploy/start.sh <campaign>` 或 `python3 server.py …`——見 `docs/RUN.md`

三個入口一句話：

- **人**：開大廳 → 選桌 → 用帳號入座（單一 origin ⇒ 登入一次走遍所有桌），見 `docs/HUMAN.md`
- **agent**：掛 MCP（`/<桌>/mcp`）＋ 在**帳號**上登記喚醒端點（踢的是帳號不是席位），見 `docs/AGENT.md`
- **觀眾**：`/<桌>/watch/` 唯讀，沒有席位概念 ⇒ 不可能擋回合或擋收景，見 `docs/HUMAN.md`

## 這份 README 的邊界

README 只做導航。細節一律在各分檔——**改行為的文檔要跟著代碼一起改**，
「還沒做」一律登記在 `docs/PENDING.md`（做完就刪那一條，不留歷史）。
