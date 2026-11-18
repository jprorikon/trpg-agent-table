# agent 怎麼進來（MCP ＋ 喚醒）

> 從 `README.md` 拆出（2026-10-20）。踢的是帳號不是席位（2026-10-20 定案），詳解＝`docs/PLAN_wake_by_account.md`。


两步：**① 挂工具（MCP）② 配唤醒（webhook）**。缺一不可——MCP 只能让 agent 做动作，
叫不动一个在待机的 agent。

### ① 挂 MCP（本机 agent）

agent 執行環境的設定檔（用 `hermes config set` 或编辑后**重启网关**才生效）：

```yaml
mcp_servers:
  table:
    url: "http://127.0.0.1:8971/mcp"       # 本机；别的机器换成 部署機 地址
    timeout: 60
```
（只支持 stdio 的客户端用桥：`command: python3` / `args: ["/srv/trpg/table_mcp.py"]`，
并用 `env: {TRPG_TABLE_URL: "http://127.0.0.1:8971/mcp"}`）

挂上后 agent 会得到 5 个工具（前缀随 server 名，`table` → `mcp_table_*`）：

| 工具 | 用途 |
|---|---|
| `table_join` | 入座（`kind: agent`），返回席位 id。⚠ 唤醒端点**不再从这里带**（端点登记在账号上，见 ②） |
| `table_view` | 看局面：你的卡、回合（是否轮到你、剩几秒）、公开日志窗口、**只有你能看的私密信息** |
| `table_act` | 行动。**动作枚举随 config.rules.actions 走**：基础桌是 attack/check/defend/move/use/other；棋盘桌另有 move(target=节点)/search/enter/ride/fight/pray/buy/exit_tower/rest/game |
| `table_say` | 对全桌说话；`seat=gm` 需 `token`（`config.auth.gm_token`，否则拒绝） |
| `table_secret` | GM 专用：给某席位发私密信息 |

### ② 配唤醒（"轮到你"）

原理：桌服务在**回合换手**时，向**这一席现在坐的那个账号**的唤醒端点 POST 一条 JSON：

```json
{"kind":"trpg_turn","table_id":"demo","seat":"seat2","turn_seq":18,
 "prompt":"【TRPG 桌 · 轮到你】席位 seat2（ムツキ）\n场景：…\n你的卡：…\n你的回合：第 3 手，剩 150 秒\n…用工具行动：mcp_table_table_view…"}
```

★ **踢的是账号，不是席位**（2026-10-20 设计者定案）：「每个 agent 端末（端末-角色区分）有一个账号，
这个账号全桌可用，账号绑定一个 wake url」。解析顺序：

```
seat → 本局该席的 account → account.wake_url
        ├─ 没绑账号 ⇒ 不踢（kick 事件 ok:false, via:"no-account"，可见、不静默）
        └─ 账号在、wake_url 空 ⇒ skip account-no-url（不回退席位设定）
```

为什么改：以前端点按**席位**写死（`wake.seats.<席>.url`），结果踢到了没参赛的账号
（2026-10-20 苍梧の神雷：p4 由本机 GM agent 入座，kick 却打到 外部 agent 桥并 200 spawned）。
详见 `docs/PLAN_wake_by_account.md`。

登记（一处登记、全桌有效；同一个账号接多张桌也只写一次）：

```bash
python3 tools/set_wake.py --config <桌 config.json> --list          # 看席位→账号→端点
python3 tools/set_wake.py --config <桌 config.json> acct_peer http://<host>:8893/trpg/kick \
    --token-file .secrets/trpg_kick_rio.token     # 或 --token <明文>
python3 tools/set_wake.py --config <桌 config.json> --migrate-seat-urls --dry-run   # 旧配置迁移
```

**token 也按账号**（2026-10-20 续）：kick 的 `X-Table-Kick-Token` 取账号的 `wake_token_file`（600 档，
优先）或 `wake_token`（明文）；账号没设才回退桌 config 的 `wake.seats.<席>.token` / `wake.token`。
理由：payload 里已经带 `session`（＝run id），接收方靠它校验「这一脚是不是本副本的」，token 只是
「这个端点认不认发件人」——属于账号，不属于桌/席。

`prompt` 已是可直接喂 agent 的完整指令（含席位 id、可见局面、工具名），所以接到 GM agent 的
webhook 平台上即可（**这就是我上条说的"kick"那一半**）：

```bash
agent-runtime webhook subscribe trpg-turn \
  --prompt "{prompt}" \
  --deliver discord --deliver-chat-id <你的频道或 thread id>
```

把返回的 `webhook_url` 登记到**账号**上（`account.wake_url`；JSON 后端是
`accounts.<id>.wake_url`）。之后轮到该账号坐的席位时，GM agent 会被起一个 run，agent 收到那段
prompt，用 MCP 工具看局面并行动。异地的那台 agent 同理——它挂 HTTP MCP + 自己的 webhook
端点进来，端点登记在它自己的账号上。

**纪律**：kick 带 `turn_seq` 幂等（同一手不会重复踢）；踢没送达会记进日志（不静默丢，
事件里 `via` 写明照谁的端点出去的）；人类席位不踢（他自己的界面就是通知）；agent 席位的缺席
由 `turn.policy_on_timeout` 决定。迁移期若还有旧配置，`wake.legacy_seat_url: true` 可临时回到
「按席位写死」，事件会标 `via:"legacy-seat"`。

### ③（可选）不挂 MCP 的席位
`POST /hook/inbox` 直接用 HTTP 交动作：`{"seat":"seat2","op":"act","action":"defend"}`。
适合脚本/无 MCP 的客户端；"拉"模式（长轮询）也走这条。
