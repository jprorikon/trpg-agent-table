# 桌 HTTP API 契约（前端唯一依赖）

> **API: 2026-10-19.3** —— 前端请在 `web/index.html` 文件头注明对应的 API 版本；
> fixture 与前端对不上时，一眼看出是谁落后。（变更记录见文末「版本变更」）

桌引擎 = 权威层（骰子 / HP / 记忆 / 规则 / 权重），前端只读视图 + 提交意图。
同一个源（页面与 API 同源部署）时，前端用**相对路径**即可。

基址示例：`http://127.0.0.1:8971`

---

## 1. 读

### `GET /api/state?seat=<seat>&gm_token=<token>&detail=compact`
返回**该席位的视图**（投影：只含它该知道的东西）。`seat` 省略 = 旁观视图（无 `you`）。
带正确 `gm_token` = GM 视图（多一个 `gm` 段）。席位不存在时不报错：返回公开视图 + `seat_unknown`。

**`auth`（2026-10-07.1；纯附加）**：顶层多一格 `auth: {has_accounts, required, seat_link}`——
`has_accounts=false` ⇒ **这张桌没有账号层**（没有 registry）⇒ 前端**把登录卡收起来**，
别再挂在那里（以前贴什么 token 都回「token 不认得」，看起来像自己打错密码）。
`required=true` ⇒ 玩家端点都要带这张桌认得的 token（`?seat=` 不再等于坐在那一席）。
`seat_link`（**2026-10-07.2**）⇒ 这张桌要不要留**兼容期的 `?seat=` 连结**（贴连结＝自动入座那一席）：
**默认 false＝收掉**，要留的开放桌自己写 `config.auth.seat_link: true`。
账号层本身见 `docs/SCENARIO_FORMAT.md §4.8`（账号＝人、跨桌唯一；座次是桌级的）；
**模型第二版（2026-10-07.4）**：账号層可住**關聯式資料庫**（`account×模組` 錢包／
`account×模組×角色` 實例／`+副本` 參與列；設計＝引擎倉 `docs/PLAN_account_model_v2.md`）——
**HTTP 面形狀不變**（純附加：`POST /api/enter` 回應多了 `module`／`run_id`）。
进桌的正式入口＝**大厅**（`docs/LOBBY.md`）。

**`session`（2026-10-07.4；只影響 agent 的 MCP 面）**：一個 session 對應一個副本實例。
kick 訊息一定帶 `session`（副本 id）與 `session_name`；**MCP 寫入類工具**
（`table_act／say／tell／secret／gm`）在 `config.session.require=true` 的桌上**必須帶對的 `session`**
（帶舊副本的 id 會被擋），`table_view` 不要求但回應一定帶 `session`。
**前端與 HTTP 面不受影響**（`/api/*` 收到 `session` 會用，沒帶照常）。

**`detail=compact`（2026-10-06.4；纯附加）**：省 token 版——`log` 收到 `log_window_compact` 条、
**不给 `recent`**（＝同一个尾巴的第二次投影），并回 `detail:"compact"`；**判断依据（桌况／自己／私讯／
棋盘／额度／动作目录）一律不动**，省的是重复的历史。省略＝`full`，旧客户端一字不改。
（线上实测同一张桌：full 34,759 字元 → compact 24,453 字元。MCP `table_view` 早已有这个参数，现在两个入口同一支。）

```jsonc
{
  "table": {"table_id":"hyakki_tower","scenario":"…","rules":"minimal-d20","scene":"…","now":1791…},
  "turn": null,                       // 严格回合制时为 {"seat":"p1", …}；场景制下为 null
  "paused": false,                    // 整桌暂停中（2026-10-06.3）：牌面冻结、不再叫醒任何人、计时停止
  "paused_seconds": 0,                //   已经暂停了多久（未暂停＝0）。前端可只显示一枚「暫停中」徽章
  "seats": [{"seat":"p1","name":"カガリ","kind":"player","hp":10,"max_hp":12,
             "status":[],"artifacts":[{"name":"願い星のかけら","tier":4,"desc":"…","revealed":false}]}],
  // ⚠ `kind` 从 **2026-10-07.5** 起：**非 GM 视角一律 `player`**（玩家平等原则——
  //   「agent 不知道谁是人，人也不知道谁是 agent」）。`npc`／`gm` 照旧；**只有 GM 视角看得到真值**
  //   `human`／`agent`（GM 要决定叫醒谁、演 NPC）。`you.kind` 仍给自己的真值（自己知道自己是什麼）。
  "log":     [ /* 公开事件（白名单类型，最近 60 条） */ ],
  "recent":  ["★ カガリ 祈願成功：神器「願い星のかけら」(tier 4)…", "…"],   // 公开事件的「人话版」
  "actions": [{"id":"move","label":"移動","desc":"走去相鄰節點"},          // 动作目录（2026-10-05.3）
              {"id":"pass","label":"","desc":""}, …],                     // label/desc 数据驱动：剧本给、原样渲染；
                                                                         //   空（或缺席）＝没内容 ⇒ 前端回退内建字串
  "allowed_actions": ["move","search","enter",…],   // 旧字段＝actions[].id（保留，旧前端仍读它）
  "stage":   {"no":24,"text":"…","open":true},        // 无景时为 null → 页面进「开局前」形态
  "scene_waiting": ["p2"],            // 本景还没出手的人（公开）；
                                      //   桌开 rules.wait_for_all_ends 时改口径：＝「没标 ended 且额度还没用完」的人
  "you_acted": true,                  // 本景我出手了吗
  "you_ended": false,                 // 本景我「結束這一景」了吗（2026-10-05.5；pass 也算）
  "throttle": {"used":{"act":2},"limits":{"act":2,"say":3}},   // 我的额度
  "settled": {"note":"…","rows":[…]} | null,                    // 结算表（若有）
  "board": { /* 见下 */ },
  "you": { /* 仅带 seat 且席位存在时 */ },
  "gm":  { /* 仅带正确 gm_token 时；含 full_state / secret_rolls / artifact_catalog */ }
}
```

`board`：
```jsonc
{
  "you_are": {"id":"w_teckan","jp":"北の荒道","cn":"…","type":"city",
              "depth":null,"stalactites_left":0,"wish_box":null,"shop":false,
              "game_room":false,"entrance_fresh":null,"mobs_left":0,"danger":0},
  "can_go":  [{"to":"hyakki","kind":"wild","mobs_left":0}],
  "roster":  [{"seat":"p1","name":"カガリ","faction":"…","role":"…"}],
  "party":   [{"seat":"p2","name":"シズク","kind":"human","is_party":true,
               "where":"鉄環市","node":"teckan","hidden_floor":false}],
  "you_goal": null,            // 无棋盘模式的桌为 null
  "your_fragments": [ {"id":"f1","text":"…"} ],   // 只有我持有的碎片（私密）
  "seen_by_you":    [ {"id":"f3","text":"…","from":["p3"]} ],  // 别人给我看过的
  "your_secrets":   [ {"text":"…","from":"gm","to":"p1","peer":"gm","seq":846,"ts":1791…} ],  // 我收到/发出的私讯（ts＝该则时间，画 messenger 时间戳用）
  "typing":  {"p2":{"to":"p1","ts":1791…}},   // 谁正在打字（纯内存，不落事件；只回最近 15s 内的）
  "your_declarations": [ … ]
}
```
**红线**：`party[].hidden_floor == true` 时**不要显示具体层数**（塔内不暴露位置，只写「塔内」）。
**红线（私讯）**：`your_secrets` 是**只有我看得到**的内容——不要进 `TX()`／外送翻译，也不要用它反推别人看不看得到。
`peer`＝这条是谁跟谁的对话（对 GM 发的那条回显＝`peer:"gm"`；玩家互传＝对方的 seat），前端据此分对话串；
自己发的那条回显 `from==to==你`、带 `peer`，`to=="gm"` 那条只在 GM 视图出现。

`you`（席位自身视图片段）：
```jsonc
{"name":"カガリ","seat":"p1","hp":10,"max_hp":12,"memory":18,"star_coins":2,
 "attrs":{"str":2,"agi":2,"wil":2},"status":[],"artifacts":[{"name":"…","tier":4,"desc":"…","revealed":false}],
 "wish":{"id":"g_cat","title":"…","text":"…","achieved":false},
 "roster":{"faction":"…","role":"…"}}
```
> 注：`allowed_actions` / `actions` 在 **顶层**（不在 `you` 里）——本文件早前误放在 `you`，2026-10-05.3 更正。
> **自 2026-10-06.1 起两者都是「当前节奏下看得见的动作」**：条目可带 `modes`（`["scene"]`／`["turn"]`），
> 回合制的桌里 `pass` 不出现、`end_turn`（結束回合）出现。动作**清单本身**仍以剧本 `rules.actions` 为准。

### 动作目录 `actions`（**2026-10-05.3**；協作端 2026-10-05 §2105 定案）
顶层 `actions: [{id,label,desc}]` 是动作的**唯一权威清单**，由**剧本**（`rules.actions`）提供。六条边界：
1. **`actions[]` 同时决定「显示哪几颗」**：前端不再用固定清单，只渲染本数组里出现的 `id` ⇒ 剧本没这动作就没这按钮（空砖自然消失）。
2. **fallback**：字段缺席（旧引擎／旧剧本）⇒ 退回 `allowed_actions` ＋现有字串，不炸。
3. **不送翻译服务**：`label`／`desc` 原样渲染——翻译白名单不再开这一格；剧本要日文就自己写日文。
4. **`id` 是契约、`label`／`desc` 是内容**：前端只认 `id` 对应行为，文案一律数据驱动。
5. **空＝没内容**：`label`／`desc` 为空字符串、或缺席、**或 `label === id`**（id 的回声不是内容），
   前端一律回退到内建字串（`act_<id>`／`actd_<id>` 的翻译）。引擎侧字符串写法因此**默认给空**（见下）。
6. **版本戳**：破坏性契约变更由引擎侧（引擎維護者）升。
引擎侧：`rules.actions` 两种写法都吃——字符串 `"move"`（⇒ `label`／`desc` 默认都 `""`＝没内容，前端回退内建字串；**不把 id 回声成 label**）或对象 `{"id":"move","label":"移動","desc":"…"}`（缺的字段同样默认 `""`）；
`GET /api/state` 顶层同时给 `actions`（新）与 `allowed_actions`（旧，＝`actions[].id`）。

### `GET /api/log` → `{"events":[ … ]}`（**两段式闸**，自 **2026-10-05.2** 起；`kick`／`seat_timeout` 自 **2026-10-07.5** 起不再公开）
- **没带 `gm_token`** ⇒ 只回**公开投影** = **`view.log` 白名单 ∩ `public:true`**（＝ `view` 看得到的那些；
  ⚠ **`kick` 与 `seat_timeout` 这两型自 2026-10-07.5 起不带 `public:true`**（白名单没动、动的是发射点）：
  只有 agent 席位会被叫醒、也只有人／agent 的差别让「被跳过」有意义 ⇒ 公开就等于标出谁是 AI（玩家平等原则）。
  两型**仍写进事件流**，带对 `gm_token` 的 raw 那条照旧看得到（`/api/log?gm_token=…`））。
  内部记账事件如 `acted` 被排除）⇒ **匿名端点永不看到比 `view` 更多的东西**（白名单与 `view` 共用同一支常数）。
  线上实测：原始 838 ⇒ 拔 `public:false`（178）⇒ 660 ⇒ 再排除白名单外（`acted` 61）⇒ **599 ＝ `view` 口径**。
- **带正确 `gm_token`** ⇒ 回**原始全量**（`public:false` 在里面）——这是 GM／除错的原始流，**前端两端都不要用它**。
开景/收景事件都带**景号 `no`**（引擎给的，前端只显示、不要自己推号——红线 2）：
```jsonc
{"type":"scene_open","no":13,"text":"…"}
{"type":"scene_close","no":13,"note":"…","resolved":[],"forced":false,"waiting":[]}
```
（`scene_close.no` 自 **2026-10-05.1** 起才有；**历史事件不会追改**（事件流 append-only），旧景仍缺 → 前端按「最近一个 `scene_open.no`」兜底即可。）

### `GET /api/map` → 静态公开地图
```jsonc
{"board_name":"…","start_node":"teckan",
 "nodes":[{"id":"teckan","jp":"鉄環市","cn":"…","type":"city","x":0,"y":0,"danger":0,
           "mobs_left":0,"wish_box":null,"shop":false,"game_room":false}],
 "edges":[{"from":"teckan","to":"w_teckan","kind":"road"}]}
```
`type` 取值：`city / village / wild / monolith_entry / tower_floor / tower_front`。

### `GET /api/roll_attrs?seat=<seat>` → 初期属性掷骰（**2026-10-05.4**；協作端 2026-10-05 §2325）
```jsonc
{"seat":"p1","attrs":{"str":2,"agi":4,"wil":5},"range":[1,5],"rolled":true}
```
**只有开着 `rules.attr_roll` 的桌才有**——没开的桌回 **409** `{"error":"…"}`（前端据 409 决定不显示「掷骰」）。
**只读、可重复问**：值＝`seed＋席位＋属性`（引擎既有的确定性骰子）⇒ 同一席**重问同一组**，
也就不存在「一直重骰到满意」；重放不必额外存档。入座后 `seat_joined` 事件带 `attrs_rolled:true`（原始流看得出这席是骰的）。

### `GET /health` → `{"status":"ok","table_id":"…","seats":5,"turn":null}`

---

## 2. 写

### `POST /api/join`
```jsonc
{"seat":"p1","name":"カガリ","kind":"human","attrs":{"str":2,"agi":2,"wil":2},"wake_url":null}
```
→ `{"seat":"p1","view":{…}}`。`kind`: `human | agent | npc`。
**桌开着 `rules.attr_roll` 时**（2026-10-05.4）body 里的 `attrs` **被忽略**——一律吃 `GET /api/roll_attrs` 的骰子值（见 §1）。

### `POST /api/act`
```jsonc
{"seat":"p1","action":"move","target":"hyakki","attr":"agi","dc":12,"note":"…"}
```
→ `{"result":{…},"view":{…}}`。`result` 例：`{"moved":true,"from":"a","to":"b"}`
（还有 `roll:{die,value,mod,total,dc,success}`、`artifact:{name,tier}`、`damage:{…}` 等以引擎为准）。

动作集（顶层 `actions[].id`；旧字段 `allowed_actions` 同值）：`move / search / enter / ride / fight / pray / buy / game /
rest / exit_tower / declare / show / give / wish / pass / attack / check / defend / use / other`
**＋ 自 2026-10-06.1 起的 `end_turn`（結束回合）**——只在回合制的桌出现；它**不是**游戏动作：
不吃额度、不发 `action_declared`、只能结束**自己**的回合，回 `{"result":{"ended_turn":true,…}}`。
`pass` 自同一版起只在**景制**的桌出现（回合制里被 `end_turn` 取代；但接口仍收 `pass`＝同义，向后相容）。
- `show` / `give`：`target`=碎片 id，`note`=对方 seat
- `declare`：`target`=争夺的项，`note`=说明
- `end_turn`：只需 `{"seat":"<自己>"}`；可选 `note`（会进 `turn_ended` 事件，给 GM 当旁白）

（上面只是范例——**动作清单以剧本 `rules.actions` 为准**；前端只渲染 `actions[]` 里出现的 id，见 §1 动作目录。）

**失败**＝HTTP **非 2xx**，或 200 但 body 带顶层 `error`／`result.error`；前端**原样展示**该消息；
body 不是 JSON 时显示 `HTTP <status>`。（引擎现行：**规则拒绝＝409 ＋ 顶层 `error`**，消息**不含内部类名**；
`result.error` 这个形状目前只有假桌在用——待假桌也换成同形状。）

### `POST /api/say` → `{"seat":"p1","text":"…"}` → `{"view":{…},"mentions_pushed":["p3"]}`
发言里**点到某个 agent 的名字**会触发一次即时推送（每景限 1 次）。
人类席位的公開發言会**叫醒 GM**：幂等键＝「坐席＋景」（`<table>:公開發言:<seat>:scene<no>`），
**同一席位在同一景里只叫一次**（不是每句一次）。前端不需要处理。

### `POST /api/tell` → `{"seat":"p1","to":"p3|gm","text":"…"}` → `{"ok":true,"seq":846}`（**私讯**，自 2026-10-05.2 起）
玩家 ⇄ 玩家／GM 的**场外私讯**。可见范围＝**发话者＋对方**（走 `secret` 管线：不進 `log`、不進公开投影）；
`your_secrets` 里会多一条**回显给自己**的（带 `peer`＝对话对象）。对 `gm`／`agent` 席位私讯 ⇒ **唤醒对方**（每封都醒；
对人类席位 ⇒ 不醒）。**私讯算进 `say` 额度**（同席、同景、同一个池）——额度用完 ⇒ 409 ＋ 顶层 `error`。

### `POST /api/tell_gm` → `{"seat":"p1","text":"…"}` → `{"ok":true,"seq":846}`（对 GM 说一句）
`/api/tell` 的 `to=gm` 简写；同样算进 `say` 额度、回显带 `peer:"gm"`、唤醒 GM。

### `POST /api/typing` → `{"seat":"p1","to":"gm|p3"}` → `{"ok":true}`（**打字中**）
纯内存、**不落事件**（重啟即清空）；前端在私讯框里边打边 ping，让**对方**看到「正在输入…」（通过 `view.typing`）。

### `POST /api/gm`（GM 用，需 `token`）
```jsonc
{"token":"<gm_token>","op":"semi","args":{"op":"scene_open","text":"…"}}
```
常用 `args.op`：`scene_open / scene_close(force?) / npc_say / heal / kick / reveal_artifact /
artifact_catalog / fragment_drop / replay`。**前端只用到 `gm_token` 读状态**；这组接口是 GM 工具面，UI 改动前先问。

---

## 3. 约束（写代码要记住）

1. **视图即权限**：页面只渲染 `view` 给的字段。`seat_unknown` 时按旁观渲染并提示入座。
2. **事件白名单**：`log` 只有公开类型。渲染时对**未知类型**要安全降级（一行灰字），不要抛错、不要猜结构。
3. **错误必须可见**：任何 `fetch` 失败要显示「连接中断，重试中…」，而不是白屏。
   失败判定口径＝§2（非 2xx／顶层 `error`／`result.error`；body 非 JSON 时给 `HTTP <status>`）。
4. **缓存**：引擎对所有响应发 `Cache-Control: no-store`；前端不要自己缓存状态。
5. **不要引入构建链**：保持 `web/index.html` 单文件可直接打开（这是刻意的设计，方便随时手改）。

### `POST /api/gm` — `op=pause` / `op=resume`（**整桌暂停**，2026-10-06.3；设计者 2026-10-06 拍）

带 `gm_token`（或连线标头 `X-Table-GM-Token`）→ `{"ok":true,"pause":{"paused":true|false,"seq":…,"frozen_seconds":…},"view":{…}}`。

| | 暂停（`op=pause`） | 恢复（`op=resume`） |
|---|---|---|
| 叫醒（回合踢／开景／私讯／点名／收景／**GM 自己的脚**） | **全部不出** | 节拍器把「现在这一手」重新踢一次 |
| 计时 / 超时代打 / 自动开局 | 停（`tick` 直接返回） | 照旧 |
| 牌面（`POST /api/act`，含 `end_turn`） | **409 rule**（「整桌已暂停」） | 照旧 |
| 说话 / 私信（`say` / `tell`） | **照常**（冻的是牌面，不是对话） | 照常 |
| 手动补踢（`op=kick`） | **例外：仍然出得去**（人有意识地要这一脚） | 照旧 |
| 当前回合的死线 | 冻住 | **把冻掉的秒数还给当前回合**（顺延一次；`frozen_seconds` 就是它） |

事件：`pacing_paused` / `pacing_resumed`（`public:true`，进公开视图与 `/api/log`；重放一致 —— 死线顺延由事件驱动）。
MCP 面同一件事：`table_gm` 的 `op` 多了 **`pause` / `resume`**。
⚠️ 暂停**不是**新的接口形状，前端可以完全不理（新增字段是附加的；旧前端忽略 `paused` 照常跑）。

---

## 2.9 素材（图片）——`GET /assets/<模组>/<相对路径>`（2026-10-09.1；清单拆分 2026-10-19.2）

**模组级**：图挂在**模组**上（模组仓 `modules/<模组>/assets/`），不是挂在桌上——
同一模组的多张桌、以及**观战页（匿名）**共用同一批图；**一个模组看不到别的模组的图**。

* 请求：`{origin}/assets/<模组>/<相对路径>`（例 `/assets/hyakki_tower/portrait/isuka/a02.webp`）。
  **不带任何凭证**（观战页匿名也要看得到图）；只 GET。
* 响应：`200` ＋ 图（PNG/JPEG/WebP/GIF/SVG；实际都是 **WebP q85-90**）、
  `Cache-Control: public, max-age=86400` ＋ `ETag`／`Last-Modified`（**图片可以长缓存**；
  页面本身仍然 `no-store`）。找不到 ⇒ `404` JSON（形状与其它面一致）。
* **清单（唯一权威，按类拆开）**：`GET /assets/<模组>/index.json` 是**瘦清单**：
  `{schema:2, module, generated_at, parts:{类→"index/<类>.json"}, counts, totals, source, missing}`
  ——`portrait`（立绘）／`avatar`（头像）／`bg`（场景）／`crowd`（路人）／`icon`（道具·神器·技能·星等·状态·小怪）。
  每一份 part 是**旧的 schema 1 形状**（`{schema:1, kind, module, files, aliases}`，只含这一类）⇒
  想只看图标的人抓 `index/icon.json` 就够，不必载 42KB 的全量（拆分后 manifest ~1KB＋各类 1–19KB）。
  **旧模组仍是单档 schema 1**（`files`＋`aliases` 都在 `index.json`）——两种形状前端都吃，不需要迁移。
  拆分工具＝模组仓 `tools/assets_index.py`（`split` 可重复跑、`check` 校验 manifest↔parts↔别名指向）。
* 投影里怎么拿（**全部纯附加**；没有素材的模组／旧桌 ⇒ 一格都不加）：
  * `view.table.assets = {module, base, index}`（`base`＝`/assets/<模组>/`）＋ `view.table.module`
  * `view.seats[].portrait` / `.avatar` / `.statusIcons`（`{状态id: url}`）
  * `view.you.portrait` / `.avatar` / `.items[].icon` / `.statusIcons`
  * `view.board.you_are.art`（当前节点的场景图）／`you_are.monster.icon`／`you_are.shop_items[].icon`
  * `view.you.skills[].icon`／`view.you.artifacts[].icon`
  * GraphQL 同形（`TableMeta.module`／`TableMeta.assets`／`SeatView.portrait|avatar|statusIcons`／
    `YouView.portrait|avatar|items|statusIcons`）；**`raw` 里也都有**（MCP 工具输出不变形）。
* **别名制**：投影只回「别名查得到的 URL」。前端拿不到某格 ⇒ 就用 `raw` 的数据＋`index.json` 自己兜底；
  再拿不到 ⇒ 回退现在的 SVG／纯色（**旧桌永远不破**）。
  别名（以 `index.json` 的 `aliases` 为准）：`portrait:<角色id>`（默认帧＝`a02`；
  **没有逐帧别名**，要指定某一帧请用 file key／相对路径）／
  `avatar:<角色id>`／`bg:<节点id>`／`bg:special:<名>`／`crowd:<key>`／
  `icon:item:<道具id>`／`icon:artifact:<tier>:<i>`／`icon:artifact:name:<神器名>`／
  `icon:skill:<技能id>`／`icon:star:<n>`／`icon:condition:<状态id>`／
  `icon:monster:<怪物id>`。
  ⚠ **`icon:rescue:<id>` 已撤**（`2026-10-09.2`）：投影里没有 rescue 挂点 ⇒ 别接（图档仍在但没有别名）。
* 现有内容（`hyakki_tower` 模组）：原创世界观，立绘／头像／场景／图标由本地生成模型产出；
  素材档**不进版控**（只有 `index.json` 进）。

## 2.10 模组用语表——`GET /assets/<模组>/locale.json`（2026-10-19.1）

**作品的固有名词与世界观用语由模组给，前端不内建任何一部作品的语汇。**
（以前写死在 `web/index.html` 的地名繁中表 `PLACE_TW`、`gl_*` 固有名词、「小怪」「貨幣」塔外等用语，
都搬进了模组 repo 的 `locale.json`；前端字典只留中性字样：敌人／货币／原点。）

* 入口：`view.table.assets.locale`（例 `/assets/hyakki_tower/locale.json?v=1792363731`；`?v=`＝档案 mtime，
  改了就换 URL ⇒ 不被长缓存卡住）。模组没放用语表 ⇒ 没有这一格（`assets` 里只剩图库那几格，或整格不出现）。
  只有用语表、没有图的模组 ⇒ `assets = {module, locale}`（`base`／`index` 不给）。
* 形状：
  ```json
  {"schema": 1,
   "glossary": {"<原文（日文，与引擎数据逐字相同）>": {"zh-Hant": "…", "zh-Hans": "…", "ja": "（选填）", "kind": "board|artifact|faction|role"}},
   "places":   {"<节点 id>": {"zh-Hant": "…", "zh-Hans": "（选填）"}},
   "text":     {"zh-Hant": {"<前端字典键>": "…"}, "zh-Hans": {…}, "ja": {…}}}
  ```
  * `glossary`：`gl()` 用（神器名、派系、定位、棋盘名）；`kind:"role"` 是普通名词，不挡即时翻译。
    `ja` 只在原文是别名写法（例 `map.board_name` 带了中文注）时才写。
  * `places`：地图数据没带该语言（`tw`／`zh_Hant`）时 `nodename()` 用。
  * `text`：覆写前端字典（`t(key)` 先查模组、再查前端）。
* 抓不到／格式不对 ⇒ 前端用中性字样，不报错。GraphQL：`AssetManifest.locale`（`base`／`index` 改为可空）。

---

## 3. 版本变更

- **2026-10-19.3**：**大厅页搬进前端**（`web/lobby.html`；hub 的 `/` 送它，`/?legacy=1` 仍是旧的服务端大厅）。
  根端点 `/graphql`（不挂桌）新增可用：`me`（没凭证＝`null`；`Identity.tables` 的 resolver 补上了——以前一律报错）、
  `login`（不必带 `tableId`，依序问各桌的帐号层）。`TableInfo` 加 `scene`／`sceneOpen`／`paused`／`waiting`
  （都是公开投影本来就有的事），`ModuleInfo` 加 `assets: AssetManifest`。模组 `locale.json` 可带 `lobby`：
  `cover`（素材表别名）、`tagline`（三语）、`scenarios.<剧本>.cover`。**纯附加**。
- **2026-10-19.2**：**素材清单按类拆分**（§2.9）——`index.json` 变瘦清单（`schema:2`＋`parts`），
  内容拆到 `index/<类>.json`（portrait／avatar／bg／crowd／icon）。**纯附加＋向后兼容**：旧模组的单档
  `schema:1` 照旧吃，前端两种都认（抓到 schema 2 就逐份抓再合并）。静态路由同时放行 `index/<类>.json`
  （只准小写英数＋`_`，仍挡穿越与非白名单副档名）。

- **2026-10-19.1**：**模组用语表**（§2.10）——新 `GET /assets/<模组>/locale.json`＋`view.table.assets.locale`；
  `AssetManifest` 加 `locale`、`base`／`index` 改可空（只有用语表的模组）。**纯附加**：没放用语表的模组一字不变。
  另：作品的规则书包 `rules.core` 从引擎仓搬进各模组仓的 `packs/`（新「模组层」包；id／内容不变 ⇒ `packs_hash` 不变）。

- **2026-10-09.2**：**别名表修正**（§2.9）——撤掉两条**从没生效过**的别名：
  `portrait:<角色id>:<NN>`（`index.json` 从没生成过逐帧别名；要指定某一帧请用 file key 或相对路径）
  与 `icon:rescue:<id>`（投影里没有 rescue 挂点，图档仍在但没有别名 ⇒ 不必接、也挂不上去）。
  **投影形状一字未动**，纯文档/清单修正（引擎侧清单同步改，`trpg/server aaf59ef`）。
- **2026-10-09.1**：**模组级素材面**（见 §2.9）——新 `GET /assets/<模组>/<相对路径>`（无凭证、
  可长缓存、404 形状一致）＋ 素材清单 `/assets/<模组>/index.json`；投影**纯附加**：
  `view.table.module`／`view.table.assets`、`seats[].portrait|avatar|statusIcons`、
  `you.portrait|avatar|items[].icon|statusIcons`、`board.you_are.art|monster.icon|shop_items[].icon`、
  `skills[].icon`／`artifacts[].icon`；GraphQL 同形（`AssetManifest` 新类型）。
  **没素材的模组／旧桌一字不变**（没别名就不加那一格）。前端 adoption＝有图就画、没有就回退现状。
- **2026-10-07.5**：**玩家平等原则**（设计者 2026-10-07：「至少在盘面上 agent 不知道谁是人，人也不知道谁是 agent」）。
  * `view.seats[].kind`：**非 GM 视角一律 `player`**（`human`／`agent` 不可区分）；`npc`／`gm` 照旧；
    **GM 视角（`?gm_token=`／`X-Table-GM-Token`）仍看真值**。`you.kind` 仍给自己的真值。
  * 进場選項（GraphQL `entry`）的 `kind` 同样一律 `player`（选角流程不需要知道谁是谁）。
  * **`kick` 与 `seat_timeout` 不再公开**（同上理由）：两型**发射时带 `public:false`**
    （事件类型白名单没动，动的是发射点）⇒ `view.log`／匿名 `/api/log` 看不到，带 `gm_token` 的 raw 照旧。
    事件流一字未动 ⇒ 回放一致。
  * 前端：`.a-human`／`.a-agent` 事件配色与 `kind_human`／`kind_agent` 三语标签已移除（`kind_npc` 保留）。

- **2026-10-07.4**：**account 模型第二版 ＋ session 規範**（引擎倉 `docs/PLAN_account_model_v2.md`）。
  * HTTP 面**纯附加**：`POST /api/enter` 回应多了 `module`／`run_id`；`/api/*` 可选带 `session`（没带照常）。
  * **agent 的 MCP 面**：`table_act／say／tell／secret／gm` 在 `config.session.require=true` 的桌上
    必须带对的 `session`（＝副本实例 id；kick 讯息与 `table_view` 都会给）；前端不受影响。
- **2026-10-07.3**：**单入口子路径**（`docs/FRONT.md`）——前端必须读 **`window.TRPG_BASE`**
  （后端起页时注入；例 `/hyakki_tower`），所有 `/api/…` 走 `BASE + path`：
  `const BASE = (typeof window.TRPG_BASE === "string") ? window.TRPG_BASE : "";`
  没有注入（直接连桌埠）⇒ 空字串 ⇒ 与以前逐字相同。**localStorage 的键不变**（单 origin ⇒ 登录一次全桌通用）。
- **2026-10-07.2**：`auth` 多一颗 **`seat_link`**（见 §1）——**默认 false＝收掉 `?seat=` 兼容连结**
  （贴连结不再等于坐在那一席；要留的开放桌写 `config.auth.seat_link: true`）。进桌入口＝大厅（`docs/LOBBY.md`）。
- **2026-10-07.1**：`GET /api/state` 顶层新增 **`auth: {has_accounts, required}`**（见 §1；纯附加）——
  没有账号层的桌，前端要收起登录卡。
- **2026-10-06.4**：`GET /api/state` 新增可选参数 **`detail=compact`**（见 §1；纯附加，省略＝full）。
  另一个**行为修正**：GM 视图的 `log` 现在与席位视图同一支口径——**逐 seq 去重**（旧版对 GM 分支算了去重
  却回传了原始列表，历史上有 17 个重号 seq 的事件会重复出现两三次）；`/api/log` 匿名投影**不变**（仍回原始全量）。
- **2026-10-06.3**：**整桌暂停／继续**（引擎 2026-10-06，设计者拍）。`/api/state` 顶层新增 `paused` / `paused_seconds`；
  新增 GM op **`pause` / `resume`**（`/api/gm` 与 MCP `table_gm` 同）；语义见 §2 那张表（停叫醒含 GM／停计时／
  停超时代打／不自动开局／牌面冻结；`say`／`tell` 照常；`op=kick` 是例外；恢复把冻掉的秒数还给当前回合）。
  事件 `pacing_paused` / `pacing_resumed`（public）。**纯附加**：旧前端忽略 `paused` 即可，不必改版。
- **2026-10-06.2**：**回合制的额度口径 ＋「自动交棒」的回应形状**（纯文件补述——①② 是**线上已生效**的行为、
  ③ 后半是未重启的新 code；引擎本身没有新变更）。
  1. 回合制下 `view.throttle` ＝ **「这一回合」的预算**：`limits.act` ＝ 剧本 `turn.actions_per_turn`
     （没写就沿用 `pacing.actions_per_scene`；`turn.end_turn_on_action=true` 时＝1）。
     `used.act` **逐席在「该席回合开始」时归零**——不是整景共用、也不是全桌一起清；
     所以 `limits.act - used.act` 就是「我这一手还剩几格」。（景制下同一组键照旧＝整景口径。）
  2. **额度花光＝引擎自动交棒**：`POST /api/act` 的回应是 **动作自己的结果 ＋ `turn_ended:true`**——
     **没有 `ended_turn`、也没有 `next`**（这两个字段只在玩家**明说** `end_turn`／回合制的 `pass` 时出现）。
  3. `turn_ended` 事件的 `reason` 四值：`end_turn`（玩家明说，含回合制的 `pass`）／**`action`（额度花光，
     或景制 `end_turn_on_action` 出手即交棒——两者都是「自动」）**／`timeout`（180 秒到期）／`gm`（GM 端点代收）。
     该事件另带 **`turn_seq`** ＝ 那一回合的**起点** `seq`；而 `seq` **自本版起＝事件自己的流水号**——
     先前它会被「回合起点 seq」盖掉 ⇒ 同一序号两笔事件，**拿 `seq` 当增量游标的客户端会漏事件**。
     **旧事件不重写**（线上 `:8971` 未重启，仍在跑旧 code）：要逐笔对帐请以**行序**为准。
- **2026-10-06.1**：**回合制（`pacing.mode="turn"`）＋「結束回合」動作**（设计者 2026-10-06 拍）。
  1. **`allowed_actions`／`actions[]` ＝「當前节奏下看得见的动作」**：剧本 `rules.actions` 的条目可带可选的
     **`modes`**（`["scene"]`／`["turn"]`，不给＝两种节奏都出现）。回合制下 **`pass` 不出现、`end_turn` 出现**；
     景制反之。**规则闸仍收 `pass`**（引擎 `action_ids` 不过滤——桥的离线代打向后相容，回合制下 `pass` ＝ `end_turn` 同义）。
     前端要做的：照目录渲染即可；若 `actWhy()`／`ACT_MORE` 有白名单，**别把 `end_turn` 收进「更多」**（它是「现在能按」的砖）。
  2. **新动作 id `end_turn`（結束回合）**：`POST /api/act {"seat":"<my seat>","action":"end_turn"}` ⇒
     回 `{"result":{"ended_turn":true,"seat":…,"next":…,"note":…},"view":{…}}`；`note` 可选（会进 `turn_ended` 事件）。
     它是**引擎操作不是游戏动作**：不吃动作额度、**不发 `action_declared`**、事件用既有的 `turn_ended`（公开、可回放）；
     **只能结束自己的回合**（现在不是你的回合 ⇒ 拒绝 `{"error":…,"kind":"rule"}`）。
  3. **回合制下 GM 的唤醒点＝「轮」**（不影响前端接口，仅供理解 `log` 里的 `kick` 事件）：
     一轮＝`order` 里每个还在场上的席位各走完一次（起点固定），`kick.reason="輪"`、`turn_seq="<桌>:輪:<n>"`；
     景制那套（开景踢／收景脚）在回合制**不跑**。私訊仍**每封即时**叫 GM。
  4. 只在**回合制的桌**上出现的行为差异：`min_seats_to_start` 以「可行動席位（`order` 里的）」计（gm/npc 不算）；
     `state` 多两个内部键 `round_no`／`round_start_seq`（前者是轮号；前端不需要读）。
  引擎侧 `trpg/server` `77b9d5e`；测试 `test_table 33→35`／`test_board 46`／`test_semic 195→200`。
  **我方线上桌 `:8971` 仍是景制、未重启**（设计者定案：等当前景收掉再切）。
- **2026-10-05.5**：**玩家「結束這一景」**（協作端 2026-10-06 §3；设计者 2026-10-05 22:43「全員結束才進下一回合」）。
  `GET /api/state` 顶层新增 **`you_ended`**（本景我是否已明确结束——按「結束这一景」或 `pass` 都算）。
  **桌端开关** `rules.wait_for_all_ends`：开了之后 `scene_waiting` 的口径从「没出手过」改成
  「**没标 `ended` 且行动次数还没用完**」——玩家要嘛自己结束、要嘛额度用完，**全員都表明结束才进下一景**；
  `pass` 同时是「我出手了」与「我结束了」（旧制两者等价，开了之后才分开）。
  **没写 `wait_for_all_ends` 的桌行为完全不变**（照旧：碰一下就当完），`you_ended` 仍照实回报。
  引擎侧 `trpg/server` `2ecc679`（`semic.py`：`ended` 容器／`semic_mark_ended()`／`pending_actors()`／
  `view().you_ended`）；`test_semic 139→150`（新增 12 条：出手一次不算结束／pass＝结束／次数用完＝结束／
  开新景归零／全員结束时只送「收景」一脚）。**我方线上桌 `:8971` 仍未开 `wait_for_all_ends`、未重啟**（等设计者一句）。
  前端侧：那颗「結束这一景」按钮送 `pass`（協作端 分支 `peer/say-to-gm`），`you_ended` 可用于把按钮置灰。
- **2026-10-05.4**：**初期属性掷骰**（協作端 2026-10-05 §2325；设计者「初期属性點應該要只能用骰的!」）。
  新增只读端点 `GET /api/roll_attrs?seat=<seat>` → `{seat,attrs,range,rolled}`；**桌端开关** `rules.attr_roll`——
  开了之后 `POST /api/join` **不接受**客户端给的 `attrs`，一律用 `seed＋席位＋属性` 的确定性骰子
  （同席重问同一组、也不能重骰到满意），`seat_joined` 事件多带 `attrs_rolled:true`。
  **没开 `attr_roll` 的桌行为完全不变**（没写这个 key 就是没开），端点回 **409**。
  引擎侧 `trpg/server` `76c81c0`（`engine.py` `roll_attrs()`＋`server.py` 路由＋`config.example.json` 补 `"attr_roll": false`）；
  另併 協作端 附的 `campaigns/rio_test`·`peer_trial` 剧本目录。三套自测 `test_table 28→33`／`board 46`／`semic 129` 全绿。
  **⚠ 我方线上桌 `:8971` 仍未开 `attr_roll`、未重启**（要不要开等设计者一句）。
  前端侧（協作端 分支 `peer/say-to-gm`）＝入座面板三输入框改唯读＋「掷骰」按钮，**尚未开 PR**。
- **2026-10-05.3**：**动作目录 `actions[]`**（協作端 2026-10-05 §2105 定案）。`GET /api/state` 顶层新增
  `actions: [{id,label,desc}]`——**动作的唯一权威清单，由剧本 `rules.actions` 提供**；前端不再用固定清单，
  只渲染本数组里出现的 `id`（剧本没这动作就没按钮，「七颗空砖」自然消失），`label`／`desc` 原样渲染、**不进翻译服务**。
  字段缺席时退回 `allowed_actions` ＋现有字串（不炸）。`allowed_actions` **保留**（＝`actions[].id`，旧前端仍读）。
  引擎侧 `rules.actions` 两种写法都吃：字符串 `"move"`（`label`／`desc` 默认都 `""`＝没内容）或对象 `{"id","label","desc"}`。
  **（2026-10-05 修正）**：字符串写法原先默认 `label`＝`id`（id 回声）⇒ 引擎重开 + 前端上线后砖面会显示原始 id；
  依 協作端 §2121 拍定改为**默认空**（=「没内容」，前端回退内建字串）。形状没动、只动默认值语义，故仍记在 `.3`。
  前端同侧防御：`label` 空 **或 `label === id`** 都回退（见 §动作目录 第 5 条）。
  顺手更正：本文早前把 `allowed_actions` 误写在 `you` 段，实际在**顶层**。
  （**引擎侧改动待重启生效**；前端 adoption＋假桌由 協作端 那条线收，走只審不合。引擎 `trpg/server`。）
- **2026-10-05.2**：**私讯面 + 两段式闸**。新增 `POST /api/tell`（玩家 ⇄ 玩家／GM 私讯）、
  `POST /api/tell_gm`（对 GM 说一句；算进 `say` 额度）、`POST /api/typing`（打字中，纯内存不落事件）；
  `view.your_secrets` 多带 `to` / `peer` / `seq` / `ts`（`ts`＝该则时间，画 messenger 时间戳用），`view.typing` 新增；
  私人内容**不進公开 `log`／公开投影**。`GET /api/log` 改成**两段式闸**：没带 `gm_token` 只回公开投影，
  带 `gm_token` 才回原始全量。**公开投影与 `view.log` 同口径**：同 `VIEW_LOG_TYPES` 白名单 ∩ `public:true`，
  且同样过 `_scrub_public`（碎片内部 id `fid` 对非当事人／匿名一律抹掉）。公開發言叫醒 GM 的幂等键＝「坐席＋景」（同席同景只叫一次）；
  叫醒 GM 的 prompt 先要它 `POST /api/typing {"seat":"gm","to":"<玩家>"}` **亮灯**（玩家看到「GM 思考中…」）再看桌。
  引擎侧改动 `trpg/server`（`server.py`／`semic.py`；`d0bf80a` 私讯面 → `25aa1a6` 闸＋收敛 → `e35a238` 白名单 → 本轮 `fid` scrub＋`ts`）。
  （**口徑已定案（協作端 2026-10-05 拍）**：`/api/log` 公开投影 = **`view.log` 白名单 ∩ `public:true`**
   ——取交集、白名单用与 `view` **同一支常数**（引擎 `engine.py` 的 `VIEW_LOG_TYPES`），
   所以 `acted`（`public:true` 但不在白名单）**不再出现在匿名投影里**。不变式＝「匿名端点不得看到比 `view` 更多」。
   引擎侧 `trpg/server` `e35a238`；线上 838→（拔 `public:false` 178）660→（再排除 `acted` 61）**599**。
   版本**維持 .2**：这是 .2 那格的开着的口径收尾，不是新接口，故不另起 .3。）
- **2026-10-05.1**：`scene_close` 事件补上 `no`（景号）——修「桌面日志显示『景 undefined 已收』」。
  引擎侧改动（`semic.py` 收景时带 `no`），公开事件流 `scene_close` 现在形如
  `{"type":"scene_close","no":13,…}`；`GET /api/gm` 的 `scene_close` 返回 `closed` 语义不变。


---

## 4. 唯讀面（觀戰桌）——**2026-10-07 新增**

第三人想看、不下場 ⇒ 走**觀戰桌**（引擎 `watch_server.py`，獨立埠，預設 8990）。前端**同一份**
`web/index.html`，由觀戰桌注入唯讀旗標（`window.__WATCH__` ＋ `body[data-watch]`）⇒ 前端進**唯讀態**：
藏掉登入卡／入座／行動列／發言框／私訊，只留桌況、地圖、公開日誌，並顯示「觀戰中（唯讀）」。

| 面 | 觀戰桌 | 備註 |
|---|---|---|
| `GET /` | ✅ | 同一份頁面、唯讀態 |
| `GET /api/state` | ✅ | **公開投影**（＝現在匿名那份；**不含 `you`**） |
| `GET /api/log` | ✅ | 公開投影那份 |
| `GET /api/map`、`GET /health` | ✅ | |
| `act／say／tell／join／enter／login／pick_char／gm／hook/inbox／mcp` | ❌ **404** | 唯讀前門沒有寫入面 |

- **不認 token**：觀戰者沒有席位 ⇒ 不進 `order`、不算「還沒出手」、**不會擋回合也不會擋收景**（它存在的理由）。
- 私密一律看不到（帶任何 token 都一樣）；`gm_token` 不轉發（觀戰桌只轉 `detail` 一個查詢參數）。
- `require_account=true` 的桌：觀戰桌帶 `config.watch.read_token` ⇒ 主桌**只放行公開投影**（不是 GM 全視圖）。
- 前端唯讀態**不是新頁面**：同一份 `index.html`；`WATCH` 分支要跟其他改動一起維持（見 `web/index.html` 的 `WATCH`）。
- 引擎側：`watch_server.py`（唯讀前門）＋`web_api.py` 的 `_watch_token_ok`；設計＝`trpg/server` 的 `docs/PLAN_spectate_table.md`。
