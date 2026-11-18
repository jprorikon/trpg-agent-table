# 起／停 與自測

> 從 `README.md` 拆出（2026-10-20）。這一檔只管「怎麼把它跑起來／怎麼驗」。

## 起／停

**0. 先起 account 層的資料庫**（有 `auth.db`／`auth.db_env` 的桌才需要；沒有＝走 JSON registry，legacy）
```bash
python3 tools/trpg_db_init.py            # 生成 .secrets/trpg_db.env（只做一次）
sudo bash deploy/db_up.sh                # 起 trpg-postgres（127.0.0.1:8433；幂等）
python3 tools/trpg_db.py init            # 建表
python3 tools/trpg_db.py list            # 看各帳號的錢包／實例／座次（DB 版）
```

**部署方式（推荐：脱离会话常驻，终端关掉也不死）**
```bash
cd /srv/trpg
./deploy/start.sh                        # 起（省略＝用 layout 解出 hyakki_tower 的家目录；setsid 脱离会话）
./deploy/start.sh state/tables/hyakki_tower/sannenme_no_onibarai/hyakki_tower_sannenme_no_onibarai_1   # 指定某张桌
./deploy/status.sh                        # 看：进程 / 健康 / 席位与景 / 日志尾部
./deploy/stop.sh                           # 停（SIGTERM 先礼后兵）
tail -f logs/table.log                     # 日志
```
- **重启不丢局**：状态全在 `state/tables/<模组>/<剧本>/<桌ID>/runs/<session>/events.jsonl`（append-only 事件流），重启后原样恢复。
  **一局一档**（`runs/<session>/`；`runs/current` 指到现役那一局）⇒ 多局并存、换局＝开新目录。
  （席位/位置/景/愿望/碎片/每景额度都在）；`op=replay` 可验证内存与事件流一致。
- **部署机铁律**：`deploy/start.sh` **不写 systemd/cron、不加任何开机项**。
  `deploy/trpg-table.service` 只是模板——要开机自启你自己装（文件头有命令）。
- **GM 激活口（`kick_receiver.py`，跟桌一起起停）**：桌的 `config.kick.receiver_url`（**GM 唤醒路**——玩家私讯/公开
  发言时 `send_kick_to_gm()` POST 出去）指到它；它验 token → 幂等键 `桌:席位:序` 去重 → 起一轮 GM agent
  `/v1/runs`（session `trpg-<桌>-gm`＝GM 自己一条会话）把 GM 叫醒；**无人应答的 approval 闸自己答**
  （`choice=session`），否则那一轮会卡在 `waiting_for_approval`。
  ```bash
  ./deploy/kick_receiver.sh start|stop|status     # 接收器（默认 :8972）；token 放 .secrets/trpg_kick_receiver.token
  ```
  **它必须跟桌一起在**：不在＝玩家私讯/发言叫不醒 GM，而且不会报错。所以 `deploy/start.sh` 会带着它起、
  `deploy/stop.sh` 会带着它停（桌已在跑时 `start.sh` 也会把缺的它补起来）。
  **注意区分**：`config.kick.receiver_url` ＝ GM 唤醒路；**席位自己的唤醒**在 `config.wake.seats.<seat>.url`
  （p3＝外部 agent 那条，两条别互指）。
- **唤醒讯息「一次给足」（2026-10-06）**：`send_kick_to_gm()` 发送前会自己亮灯（`POST /api/typing`）并把 GM
  本来会去查的每一格塞进同一条讯息（职责／席位名册／本桌允许的动作／景的叙述／在场的人／能动的
  地方／谁还在等出手／额度／最近三则），再给一行工具 `tools/gm.py <table> …`——所以 GM 那一轮
  只跑 1~2 个工具调用（旧版 27 趟、2m49s；新版第 2 笔起 3~8s）。工具指令模板可覆写：
  `config.kick.gm_cli`（含 `{here}`＝本 repo 目录、`{table}`＝桌 id）或 env `TRPG_GM_CLI`；
  **预设＝本 repo 的 `tools/gm.py`**（自我定位，引擎与 GM 同机免设定），跨机再指到 GM 那台的指令。
- **全員结束只送「收景」一脚**：`/api/act` 之后若 `pending_actors()` 空（没人还在等出手），
  只发「收景」那一脚（幂等键 `桌:收景:sceneN:gmM`），不再同时送一般唤醒——两脚同秒会让两个
  GM session 排队互卡（实测换景 2m15s vs 单脚 17s）。
- **`rules.wait_for_all_ends`（预设关）**：开启时 `pending_actors()` 改判「没标 `ended` 且行动次数
  还没用完」才算在等——玩家要嘛按「結束这一景」，要嘛次数用完，才进下一景（`pass` 会一并标 `ended`；
  `view().you_ended` 给自己看）。

**开发方式（前台）**
```bash
cd /srv/trpg
python3 server.py                     # 抽象桌（无棋盘）：用根目录 config.json
# 棋盘桌：
python3 server.py --config modules/hyakki_tower/scenarios/sannenme_no_onibarai/config.json \
                   --home   modules/hyakki_tower/scenarios/sannenme_no_onibarai
# 或明确指定： python3 server.py --config config.json --home . --port 8971 --bind 0.0.0.0
```
- 起好后：网页 `http://<这台 部署機 的局域网/Tailscale 地址>:8971/`
- 健康检查：`curl -s http://127.0.0.1:8971/health`
- 停：`./deploy/stop.sh`（前台跑就 Ctrl-C）
- **不要开两个实例**：同一局只能一个进程写（`runs/<session>/events.jsonl` 是唯一真相）。


## 自測

```bash
PY=python3
$PY tests/test_table.py     # 抽象桌        $PY tests/test_board.py   # 棋盘桌（示範模組）
$PY tests/test_semic.py     # 半合作        $PY tests/test_layers.py  # 分层纪律闸
$PY tests/test_director.py  # L3 导演       $PY tests/test_module.py  # L1 求值器＋剧本模组＋自检器
$PY tests/test_packs.py     # 机制包闸    $PY tests/test_conditions.py  # 条件（部位受伤／异常状态）
$PY tests/test_reveal_guard.py              $PY tests/test_hold_tool.py   # /hold-trpg
$PY tools/check_campaign.py modules/<模组>/scenarios/<剧本>   # 作者入口（与引擎同一套判据）
```
覆盖：REST 闭环 / HP 由规则算出 / 骰值与伤害可复算 / 私密信息不泄漏 / 回合流转 /
kick 真的 POST 出去且不含他人秘密 / 超时策略 / 事件回放一致 / MCP(HTTP,SDK 客户端) 全通 /
stdio 桥握手 / 网页可取；棋盘桌另含起点/记忆、相邻与荒野判定、入口失效与重找、昇降機禁行、
小怪与貨幣、商店饮食陷阱、祈願扣记忆、隐藏层解锁、出塔収奪、塔外、回放一致。
**基線不寫在這裡**（會漂）：`bash tools/run_tests.sh` 逐檔印 PASS/FAIL，交付前自己要綠。

### 在別台機器／別人的 clone 上跑（2026-10-24）

```bash
git clone <gitea>/trpg/server.git && cd server
sh deploy/modules_up.sh              # 模組各自一個 repo，不在這棵樹裡（第一次 clone、之後 pull）
bash tools/run_tests.sh              # 全部；或 bash tools/run_tests.sh test_skills …
TRPG_PY=/path/to/python3.11 bash tools/run_tests.sh   # 換解釋器（預設找 部署機那支 → python3.11 → python3）
bash tools/verify_clean_clone.sh     # 「乾淨 clone 就能跑」的驗證器（git archive → modules → 全量測試）
```

要點：測試**不讀本機狀態**（倉庫根的 `config.json` 不進版控、`state/tables/` 的現役流、`state/_retired/` 的歸檔）；
夾具一律從**模組的 `config.example.json` ＋ 劇本定義層**生臨時桌，沒有現役流就現場合成一局
（`tests/support_fixture.py`）。解釋器要 3.10+。
