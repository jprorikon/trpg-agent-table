# TRPG Table Engine：人类玩家与 AI agent 同桌的跑团引擎

> **One-line (EN):** A rules engine and game server for tabletop RPGs in which human players (web UI) and
> LLM agents (via MCP tools) sit at the same table. The engine owns all dice and state, enforces per-seat
> information firewalls, and records every change as an append-only, replayable event log.

这是一个"多人在线跑团桌"的服务端与网页端。它要解决的核心问题是：**让 LLM agent 像人类玩家一样坐上同一张桌，
同时又不能让它作弊、偷看别人的手牌，或者编造骰子结果。**

```
human  ── 网页（GraphQL）──┐
                           ├──►  server.py（唯一持有引擎）──► trpg/engine/（权威层：规则／骰子／事件）
agent  ── MCP 工具 ────────┘                │
                                            └─► runs/<session>/events.jsonl（append-only，可回放）
"轮到你了" ◄── kick（出站 webhook，按账号唤醒 agent）
```

## 设计上的三条硬规则

1. **数值只由引擎产出**。骰子、HP、伤害、货币全部在 `server/trpg/engine/` 中计算。agent 只能"声明动作"
   （`table_act`），没有任何写状态的通道。
2. **信息防火墙**。`view()` 按席位投影：私密愿望、线索碎片、私信只有持有人看得到。GM 的叙述和 NPC 台词
   走同一道**泄漏闸**，如果说出了别的席位的私密原文，引擎会直接拒绝。
3. **一切可回放**。状态是事件流的折叠结果（event sourcing）。`op=replay` 会从头重放并与内存状态逐字段比对，
   测试里大量用它当作不变式。

## 这份代码里比较值得看的部分

| 主题 | 位置 | 说明 |
|---|---|---|
| 事件溯源与回放 | `server/trpg/core/replay.py`、`store.py` | 一局一个 `events.jsonl`；重启不丢局；replay 一致性是测试不变式 |
| 投影与信息防火墙 | `server/trpg/engine/projection/` | 同一份状态按"谁在看"投影；观战面只给公开投影 |
| MCP 适配层 | `server/trpg/api/mcp_api/`、`mcp_adapter.py` | 7 个工具（`table_view/act/say/tell/join/gm/secret`）；工具集随桌配置自适应 |
| GraphQL 面 | `server/trpg/api/graphql/`、`docs/schema.graphql` | 唯一客户端端点；匿名只读（观战）按 query 做访问控制 |
| 回合制与 agent 唤醒 | `server/trpg/service/wake/`、`trpg/kick/` | 按**账号**（而不是席位）唤醒 agent；幂等键去重；一次唤醒就给足上下文 |
| 规则包三层叠加 | `server/trpg/rules/packs/`、`server/packs/README.md` | 引擎层 → 模组层 → 剧本层；内容（技能／道具／怪物／商店表）与机制分离 |
| 剧本 DSL | `server/trpg/rules/dsl.py`、`expr.py`、`scenario/` | 愿望判据、触发器、结局都是 JSON AST，不写代码 |
| 多桌单进程 hub | `server/trpg/api/hub/`、`docs/HUB.md` | 一个端口挂所有桌（子路径分桌），单点登录走遍各桌 |
| agent 试玩工具 | `server/agent_harness/` | 让多个 agent 自动开局、统计每一手的分布（用来调剧本节奏） |

## 规模

- 引擎：约 **2.1 万行** Python（`server/trpg/`，按 core／engine／rules／api 分层）
- 测试：**55 个测试文件，约 1.7 万行**，全部通过（包含真起 HTTP 服务、MCP 客户端、GraphQL 的端到端测试）
- 前端：单文件、零构建（`frontend/web/index.html`，原生 JS，三语言，深浅色主题）
- 依赖很少：标准库 + `vendor/` 中的 graphql-core / pg8000；MCP 端点需要官方 `mcp` SDK

## 目录

```
server/              引擎与服务（Python）
  trpg/                core（事件存储／回放）· engine（桌、投影、回合）· rules（规则机制）· api（GraphQL／MCP／hub）
  packs/               引擎层机制包（d20core、board、death、wound…）
  modules/hyakki_tower 示范模组「百鬼塔」（原创世界观：角色、地图、剧本、规则书包）
  tests/               55 个测试文件；tools/run_tests.sh 是唯一入口
  docs/                规格文档（API／AGENT／HUB／SCENARIO_FORMAT…）
frontend/            网页端（单文件 index.html + lobby.html）＋本地假桌 mock_server
scenario-template/   剧本作者用的模板、格式说明与自检工具（check_module.sh）
```

## 跑起来

需要 Python **3.10+**。

```bash
cd server
TRPG_PY=python3.12 bash tools/run_tests.sh              # 全部测试
TRPG_PY=python3.12 bash tools/run_tests.sh test_board   # 只跑一支（棋盘玩法的完整流程）
```

部分测试会起本地 HTTP 服务（127.0.0.1 上的随机端口）。MCP 相关的断言需要官方 SDK：
`pip install "mcp<2"`（没装时，那几条会明确报"mcp SDK 可用：失败"，其余测试照常跑）。

起一张单桌（开发模式）：

```bash
cd server/modules/hyakki_tower/scenarios/sannenme_no_onibarai
cp config.example.json config.json        # 填 auth.gm_token 等（config.json 不进版控）
cd -  && python3 server.py --config modules/hyakki_tower/scenarios/sannenme_no_onibarai/config.json \
                           --home   modules/hyakki_tower/scenarios/sannenme_no_onibarai --port 8971
# agent 的 MCP 端点：http://127.0.0.1:8971/mcp
```

更多起停方式见 `server/docs/RUN.md`，agent 接入见 `server/docs/AGENT.md`。

## 关于示范内容

`server/modules/hyakki_tower/`（百鬼塔）是**原创**世界观，角色、地图、剧本和数值都是本项目自己写的。
素材图由本地生成模型产出，不进版控（仓库里只有素材清单 `assets/index*.json`）。
`frontend/fixtures/` 是用这个模组在本机跑一小段示范对局后，用 `frontend/tools/grab_fixtures.py` 抓下来的快照。

## 已知限制

- `frontend/mock/mock_server.py`（假桌）只模拟旧的 REST 接口。前端已经改走 GraphQL 登录，所以假桌打开时会停在登录框；
  要看完整页面需要接真桌（hub）。
- 代码注释和文档以中文（繁／简）和日文为主。
