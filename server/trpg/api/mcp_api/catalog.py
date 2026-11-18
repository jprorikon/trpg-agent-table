"""catalog（自 __init__.py 拆出）"""

from __future__ import annotations
import json
import os
from typing import Any, Dict, Optional
from trpg.accounts import auth as auth                              # L6 account 認證層（agent 與人類同一條路）
from trpg.core.errors import RuleError, TellError                                       # noqa: E402

def _accounts_of_table(t: Any):
    """這張桌的 account registry（**引擎不認識 account**；懶載入、沒有 registry＝空的）。

    ⚠ 與 `web_api._accounts_of` 同一條規則（人與 agent 走同一支）：registry 路徑看
    `config.auth.accounts_file`（共用）、`table` 看這一桌的 id（座次只認自己這一桌）。
    """
    acc = getattr(t, "_accounts_cache", None)
    if acc is None:
        cfg = getattr(t, "cfg", None) or {}
        home = getattr(t, "home", ".") or "."
        table = str(cfg.get("table_id") or os.path.basename(str(home).rstrip("/")))
        acc = auth.Accounts(home, table, cfg=cfg)
        try:
            t._accounts_cache = acc
        except Exception:                                               # noqa: BLE001
            pass
    return acc

def _board_on(cfg: Dict[str, Any], pv: Any) -> bool:
    """這一桌有沒有棋盤？`config.board` 有寫**或**啟用了 `board` 機制包（拆景後由包補預設）。"""
    if cfg.get("board"):
        return True
    try:
        return bool(getattr(pv, "enabled", False) and "board" in (pv.ids or ()))
    except Exception:                                                   # noqa: BLE001
        return False


def tools_for(cfg: Dict[str, Any], home: str) -> list:
    """這一桌的**工具目錄**（`TOOLS` 的**副本** ＋ 按這一桌的 config 調整描述／列舉）。

    為什麼要有這支（2026-10-07 單一進程化）：`TOOLS` 是**模組級共用**的清單——一個進程裝一張桌時，
    直接改它沒事；一個進程裝 N 張桌（`hub_server.py`）時，A 桌的機制包／棋盤字樣會漏到 B 桌。
    ⇒ 每張桌在組裝時拿**自己那份副本**（`svc.tools`），列舉與描述都只反映自己這一桌。
    單桌模式（`server.py`）與 hub 都走這一支（**同一件事不做兩份**）。
    """
    import copy                                        # 區域 import：只有組裝時用得到
    from trpg.rules import packs as packs

    from trpg.engine import presentation as presentation

    from trpg.engine import visible_actions
    tools = copy.deepcopy(TOOLS)
    pv = packs.resolve(cfg.get("packs"), home=home)          # 與引擎同一套解析（同一套判據）
    bank = packs.text_bank_for(pv, home)                     # 這一桌的句庫（棋盤段的措辭由模組給）
    mode = str(((pv.mode or {}).get("name"))
               or "turn").lower()
    acts_raw = packs.effective_action_entries((cfg.get("rules") or {}).get("actions"), pv)
    for tool in tools:
        if tool["name"] == "table_act":
            # 列舉＝**看得見的**動作（帶 modes 的條目按節拍過濾）：回合制下 pass 不顯示、
            # end_turn（結束回合）顯示；規則閘仍收 pass（橋的離線代打還在送它）。
            acts = visible_actions(acts_raw, mode)[0]
            if acts:
                tool["inputSchema"]["properties"]["action"]["enum"] = acts
            if pv.enabled:
                # 機制包＝這桌開了哪些可選規則（agent 靠這行知道「這一桌能做什麼」）
                tool["description"] += f" 本桌启用的机制包：{'/'.join(pv.ids)}。"
            if mode == "turn":
                tool["description"] += (" 本桌是**回合制**：輪到你時先 table_view；不想出手就送 "
                                        "action=end_turn（結束自己的回合，交給下一席）——"
                                        "不能替別人結束。")
            # ⚠ 2026-10-21：`board` 段現在由**機制包**補（config 只寫要改的）⇒ 光看 `cfg["board"]`
            #   會讓新桌的棋盤說明整段消失（agent 只剩 can_go 的原始資料＋撞牆的錯誤訊息）。
            if _board_on(cfg, pv):
                tool["description"] += presentation.text("mcp.board_act", bank)
                tool["inputSchema"]["properties"]["target"]["description"] = (
                    "move/ride/fight 的目标：move=相邻节点 id；ride=相邻楼层 id；attack=席位 id")
        if tool["name"] == "table_view" and pv.enabled:
            tool["description"] += f" 本桌启用的机制包：{'/'.join(pv.ids)}。"
        if tool["name"] == "table_view" and _board_on(cfg, pv):
            tool["description"] += presentation.text("mcp.board_view", bank)
    # ★ session 規範（設計者 2026-10-07「kick 和 MCP 規範：需要帶 session 號，一個 session 對應一個
    #   副本實例」）：每格都吃 `session`；**寫入類在 `config.session.require` 下必填**——換副本
    #   （新的一局）＝新的 id，帶舊的會被擋（防「舊副本的一腳生效在這一局」）。
    #   預設 false＝相容期（沒設的桌 legacy 一字不變；現役兩張桌都寫 true）。
    _req_sess = bool(((cfg.get("session") or {}).get("require", False)))
    _sess_desc = ("**本副本的 session 號**（＝這一局的實例 id）；kick 訊息與 table_view 都會給。"
                  "換一局（新副本）就是新的 session。")
    for tool in tools:
        _props = tool.setdefault("inputSchema", {}).setdefault("properties", {})
        _props["session"] = {"type": "string", "description": _sess_desc}
        if tool["name"] == "table_view":
            tool["description"] += " 回應會帶 `session`（本副本 id）——寫入類工具要帶它。"
        if _req_sess and tool["name"] in ("table_act", "table_say", "table_tell",
                                          "table_secret", "table_gm"):
            _rq = tool["inputSchema"].setdefault("required", [])
            if "session" not in _rq:
                _rq.append("session")
            tool["description"] += " ★必須帶 `session`（本副本的實例 id；桌上換局就換一個）。"
    return tools

TOOLS = [
    {
        "name": "table_view",
        "description": ("看桌子：你可见的场景、回合（是否轮到你、还剩几秒）、所有席位公开状态、"
                        "公开日志窗口、你自己收到的私密信息、你自己的角色卡、允许的动作；"
                        "若是本桌启用了「部位受伤／异常状态」，你自己的卡上会有 conditions"
                        "（每回合结算时扣血／倒数，判定会自动吃加减值）。"
                        "轮到你时先调这个，再决定动作。"
                        "detail=\"compact\"＝省 token 版（日誌窗口收短、不重複給尾巴、"
                        "**私訊只列最近幾則**（其餘的則數寫在 `your_secrets_hidden`）；"
                        "桌況／自己／碎片／棋盤／額度一樣不少）——工具算力貴時用它；"
                        "**要讀完整私訊收件匣就用 detail=\"full\"**（投影不裁，只是 compact 版收窗口）。"
                        "**account 憑證**：這張桌開了 account 認證（`config.auth.require_account`）時，"
                        "每個玩家工具都要帶 `token`＝你那一把 account token（`tools/trpg_accounts.py issue` "
                        "發的）——帶了就以它認出的席位為準，替別的席位行動會被拒。"),
        "inputSchema": {"type": "object", "properties": {
            "seat": {"type": "string", "description": "你的席位 id（join 时返回的）"},
            "detail": {"type": "string", "enum": ["full", "compact"],
                       "description": ("默认 full（完整，含全部私訊）；compact＝省 token"
                                       "（日誌窗口收短、私訊只列最近幾則，其餘數見 "
                                       "your_secrets_hidden）")}},
            "required": ["seat"]},
    },
    {
        "name": "table_join",
        "description": ("入座。返回你的席位 id。agent 第一次加入时调用；已入座就不要再调。"
                        "kind 填 agent。"),
        "inputSchema": {"type": "object", "properties": {
            "seat": {"type": "string", "description": "自选席位 id（可省略，服务端给 seatN）"},
            "name": {"type": "string", "description": "角色名（桌上显示的名字）"},
            "kind": {"type": "string", "enum": ["agent", "npc", "human"],
                     "description": "agent=你是玩家；npc=你演 GM 侧角色"},
            "attrs": {"type": "object", "description": "属性，默认 {str:2,agi:2,wil:2}，范围见 config.rules.attr_range",
                      "properties": {"str": {"type": "integer"}, "agi": {"type": "integer"},
                                     "wil": {"type": "integer"}}},
            "wake_url": {"type": "string", "description": "⚠ 已退役（2026-10-20）：唤端点现在登记在**账号**上（account.wake_url），不再按席位带；这个参数仍收，但 kick 不看它"}},
            "required": ["name"]},
    },
    {
        "name": "table_act",
        "description": ("声明一个动作。action=attack 需 target；check 可选 attr/dc；defend 举盾；"
                        "move/use/other 无需骰。骰子由引擎产出并记进日志——不要自己编数值。"
                        "**技能／道具用法（2026-10-21）**：`action=use, skill=<技能 id>`；"
                        "要打同格的怪物就再帶 `target=<怪物 uid>`（技能走自己的 DC，比 fight 低；"
                        "帶 ally_bonus 的技能同格隊友越多傷害越高）。"
                        "若动作发生在你的回合并已配置 end_turn_on_action，回合会自动交给下一个席位。"
                        "**`req`＝这一手的请求 id（强烈建议带）**：同一个 `seat+req` 只结算一次——"
                        "超时重试时带同一个 `req`，引擎回 `{duplicate:true, seq, note}`，不会再掷骰/扣额度。"
                        "**account 憑證**：開 account 認證的桌要帶 `token`（你的 account token）——"
                        "帶了就動自己那一席，冒充會被拒。"),
        "inputSchema": {"type": "object", "properties": {
            "seat": {"type": "string"},
            "action": {"type": "string", "description":
                "动作 id——**完整清单看 `table_view` 的 actions／allowed_actions 与 `action_args`**"
                "（例：move／search／enter／ride／fight／pray／buy／wish／declare／show／give／"
                "attack／check／defend／use／pass／end_turn／cast（技能）／use_item（道具）／rescue（救起倒地的人））。"
                "⚠ 以前这里写死一小串 enum ⇒ 新动作（cast／use_item／rescue…）在 MCP 侧被挡掉；改成描述，"
                "权威只有桌子自己的动作目录（包宣告＋config 显示）。"},
            "target": {"type": "string", "description":
                "attack／fight／cast／use_item／rescue 的目标：席位 id（rescue＝倒地的那个人），"
                "或**同格怪物的 uid**（fight／cast／use_item 都能打怪；uid 看 board.you_are.monster.monsters[].uid）"},
            "attr": {"type": "string", "description": "check 用哪个属性 str/agi/wil"},
            "dc": {"type": "integer", "description": "难度（省略用 config 默认）"},
            "skill": {"type": "string", "description":
                      "用 `action=use` 施放技能時給技能 id（例：hoshikuzu_giri）；"
                      "打怪物時**同時**把 target 給怪物的 uid（board.you_are.monster.monsters[].uid）"},
            "item": {"type": "string", "description":
                     "用 `action=use` 使用道具時給道具 id；`use.target=foe` 的道具要帶 target——"
                     "可以給**怪物 uid**（破甲道具打王），也可以給別的席位"},
            "req": {"type": "string", "description":
                    "请求 id（幂等键）：超时重试带**同一个**值 ⇒ 引擎不重复结算，回 {duplicate:true, seq}"},
            "note": {"type": "string", "description": "动作描述 / use 用了什么"}},
            "required": ["seat", "action"]},
    },
    {
        "name": "table_say",
        "description": ("对全桌说一句话（角色台词/行动宣言）。**私人的話（只给某人看）走 table_tell**，"
                        "不要在 table_say 里讲。"
                        "**seat=\"gm\" 的公开叙事需带 token＝config.auth.gm_token**（与 table_gm 同一把），"
                        "否则拒绝；玩家席位带自己的 seat，不需 token。"),
        "inputSchema": {"type": "object", "properties": {
            "seat": {"type": "string"},
            "text": {"type": "string"},
            "token": {"type": "string", "description": "仅 seat=gm 时需要：config.auth.gm_token（若连线带了 X-Table-GM-Token 标头则可省——GM 的瘦 profile 就是这样连的）"}},
            "required": ["seat", "text"]},
    },
    {
        "name": "table_secret",
        "description": ("GM 专用（需 token——或连线带 X-Table-GM-Token 标头）：给某个席位发一条"
                        "只有他（和 GM）能看到的私密信息。"),
        "inputSchema": {"type": "object", "properties": {
            "to": {"type": "string", "description": "目标席位 id"},
            "text": {"type": "string"},
            "seat": {"type": "string", "description": "发出者（GM 席位）"},
            "token": {"type": "string", "description": "config.auth.gm_token（连线带标头时可省）"}},
            "required": ["to", "text"]},
    },
    {
        "name": "table_tell",
        "description": ("私訊（玩家 ⇄ 玩家／GM）：**只有你與對方看得到**，也**不是 GM 專用**。"
                        "對方是 agent 席位時會把他叫醒（他下一輪看得到）；對人私訊只是送到他的頁面。"
                        "對 GM 說場外話也是這支（to=\"gm\"）。"
                        "**全桌都聽得見的話走 table_say**；私人的話、不想公開的盤算走這支。"),
        "inputSchema": {"type": "object", "properties": {
            "seat": {"type": "string", "description": "你的席位 id（join 时返回的）"},
            "to": {"type": "string", "description": "收件席位 id（p1/p2/…／gm）"},
            "text": {"type": "string", "description": "私訊內容"}},
            "required": ["seat", "to", "text"]},
    },
    {
        "name": "table_gm",
        "description": ("GM 专用（需 token，op 见枚举）：开景/收景、投放碎片、给 NPC 喂『视角包』、"
                        "代发 NPC 台词、结算、改判并列宣言、查泄漏记录；"
                        "**整桌暂停/解除（op=pause/resume）**——停叫醒（含 GM 自己）／停计时／停超时代打，"
                        "再 op=resume 继续（暂停时间会还给当前回合）。"
                        "**叙述（世界的聲音）走 table_say(seat=\"gm\")＋同一把 token——不是 npc_say**；"
                        "**npc_say 只在『那个 NPC 真的开口』时用**（它只是代发 NPC 台词的入口）。"
                        "**op=condition**＝投／治一个「部位受伤／异常状态」（target＋id；value=false 治好；"
                        "只在这张桌启用了带条件表的机制包时可用）。"
                        "**op=attr_mod**＝给某席某个属性一个「加值」（target＋attr＋amount，0＝拿掉；"
                        "检定／攻击／伤害／移动都会吃到它）。"
                        "**op=resource**＝改某席的「通用资源」（target＋name＋amount；mode=add 加减、"
                        "mode=set 设成某值，例：spell_slot／ammo／stamina）。"
                        "GM 只有叙述与投放权限——**HP／骰／记忆这些核心数值没有直接改的工具**；"
                        "condition／attr_mod／resource 走的是**机制的出口**"
                        "（改動進事件流、由引擎折出，GM 只能调用不能改帳）。"
                        "**每輪開始引擎會叫你一次**（2026-10-21 拆景定案）：用 op=say 說一句公開敘述即可，"
                        "沒有開景／收景這種操作。"
                        "**op=audio_play／audio_stop／audio_list**＝播放控制（BGM／效果音）："
                        "曲目住模組的 `audio.json`，GM 只選『哪個聲道、哪一首、淡入幾秒』；"
                        "**开景时也可直接带 bgm（例 bgm:town）**（不给＝模块宣告的默认曲）。"
                        "GM 叙述与 NPC 台词都走同一道泄漏闸：说出别席位未公开的私密内容会被拦下。"),
        "inputSchema": {"type": "object", "properties": {
            "token": {"type": "string",
                      "description": "config.auth.gm_token（连线带 X-Table-GM-Token 标头时可省——GM 的瘦 profile 走这条）"},
            "op": {"type": "string", "enum": ["fragment_drop",
                                             "npc_knowledge", "npc_say", "settle",
                                             "contest_override", "leak_report",
                                             "roll_for", "act_for", "pause", "resume",
                                             "condition", "attr_mod", "resource", "exp", "item",
                                             "star", "audio_play", "audio_stop", "audio_list"]},
            "text": {"type": "string", "description":
                                   "那个 NPC 真的开口时的台词（npc_say）／GM 的公开叙述（say）"},
            "seat": {"type": "string", "description": "NPC 席位 id（npc_say / npc_knowledge）"},
            "fid": {"type": "string", "description": "fragment_drop 指定碎片 id（可省）"},
            "to": {"type": "string", "description": "fragment_drop 指定持有者（可省）"},
            "pool": {"type": "string", "description": "fragment_drop 投放池 tower/outside"},
            "declare_seq": {"type": "integer", "description": "contest_override：要改判的宣言 seq"},
            "target": {"type": "string", "description":
                                   "condition：投／治的席位 id（部位受傷／異常狀態）"},
            "id": {"type": "string", "description":
                               "condition：條件 id（見 table_view 的條件表；錯了會列出可用的）"},
            "value": {"type": "boolean", "description": "condition：true＝投上、false＝治好"},
            "turns": {"type": "integer", "description":
                                   "condition：覆寫「幾回合後自己好」（省略＝用表上的）"}},
            "skill": {"type": "string", "description":
                                   "cast：技能 id（见 table_view 的 your_skills；错了会列出可用的）"},
            "item": {"type": "string", "description":
                                  "use_item／item：道具 id（见 table_view 的 your_items；错了会列出可用的）"},
            "count": {"type": "integer", "description": "item：数量（默认 1；mode=take＝拿走这么多）"},
            "star": {"type": "integer", "description":
                                  "star：给到 ★几（见 table_view 的 your_star；省略＝加一星）"},
            "attr": {"type": "string", "description": "roll_for／attr_mod：用哪个属性 str/agi/wil"},
            "amount": {"type": "integer", "description":
                                     "attr_mod／resource：整数值（加值或数量）"},
            "name": {"type": "string", "description": "resource：资源名（例 spell_slot／ammo）"},
            "mode": {"type": "string", "enum": ["add", "set", "take"], "description":
                                   "resource：add＝加减（下限 0）、set＝设成某值；item：take＝拿走（默认 add）"},
            "id": {"type": "string", "description": "item：道具 id（见 table_view 的 item_ids）"},
            "dc": {"type": "integer", "description": "roll_for：难度（可省）"},
            "secret": {"type": "boolean", "description": "roll_for：暗骰（默认 true，只有 GM 看得到点数）"},
            "action": {"type": "string", "description": "act_for：代该席位做的动作"},
            "note": {"type": "string", "description": "act_for：动作说明"},
            "force": {"type": "boolean", "description": "（已停用：引擎只跑回合制，沒有收景這件事）"},
            "bgm": {"type": "string", "description":
                                  "（已停用：改用 audio_play，key 用 audio.json 的别名，例 bgm:town）"},
            "ch": {"type": "string", "enum": ["bgm", "sfx"], "description":
                                  "audio_play／audio_stop：声道（bgm＝会循环替换、sfx＝播放一次）"},
            "key": {"type": "string", "description":
                                  "audio_play：音轨别名（bgm:… ／ sfx:…；见 audio_list；错了会列出可用的）"},
            "gain": {"type": "number", "description": "audio_play：音量 0–1（省略＝用表上的）"},
            "loop": {"type": "boolean", "description": "audio_play：是否循环（省略＝用表上的）"},
            "fade": {"type": "number", "description": "audio_play／audio_stop：淡入淡出秒数（默认 0.8）"},
            "required": ["op"]},
    },
]
