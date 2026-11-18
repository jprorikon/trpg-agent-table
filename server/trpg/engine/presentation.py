"""L4 Presentation — 句庫（事件＋地點＋觀看者已知的事 → 句子）。**唯一產人話的地方**。

這一層的紀律（2026-10-06 設計者拍的分層）：
  * **不讀 state**：句子要的事實由呼叫端（L0/L1 或 L6）當參數帶進來；
  * **不碰時鐘／網路／檔案**：純字串進、純字串出；
  * **不 import engine／board／semic**（否則 L4 就長回 L0 裡了——`test_layers.py` 會機械擋）。

為什麼要有這一層：以前「人話」有兩份——`engine.render_log_tail()` 在內核產「★…擊破了 BOSS」，
前端 `web/index.html` 又有 125 條 `lg_*`。多一份＝多一處會漂的措辭。這裡收成**伺服器這一份**；
前端的 `lg_*` 是 協作端 那側的資產，**只透過 API 契約往來，不追代碼同步**（設計者 2026-10-06 定）。

兩種句子：
  1. `text(key, bank, **facts)`——**寫進事件流**的文案（塔的旁白、記憶流失…）。
     內核不再自己拼字串，只交出 key ＋ 事實。
  2. `render_log_tail(events, …)`——把公開事件流渲染成給人／agent 看的補課行（kick prompt 的 digest、
     `view.recent`）。**碎片正文永不出現**（只寫「一枚碎片落到 X 手上」）。

**句庫是一張桌一本**（2026-10-19）：`TEXT` 只放**內核的中性預設**（不帶任何作品的造語）；
作品的措辭由模組自己帶（`<模組>/text.json`，劇本層 `<劇本>/text.json` 再蓋一次），包的 `text.json`
也在這一疊裡——`make_bank()` 疊成一本**這一桌的**字典，交給 `text()`／`render_log_tail()` 的 `bank`。
為什麼不併進 `TEXT`：`hub_server.py` 一個行程裝 N 張桌，併進全域字典＝A 桌的措辭漏到 B 桌。
事件流存的是**已經展開的句子**⇒ 換措辭只影響之後的新事件（鍵與事件型別不變）。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional

#: **內核的中性預設**（作品的措辭住模組的 `text.json`，見檔頭）。鍵＝契約：改鍵＝改所有模組的句庫。
TEXT: Dict[str, str] = {
    # ---- 記憶（memory_change.reason）----
    "memory.tower_drain": "塔内滞在で記憶が薄れる",
    "memory.picked": "「意味のある」記憶を拾った",
    "memory.daily": "{place} で日常を過ごした（意味のある記憶）",
    "memory.arcade": "ゲームセンターで遊んだ（意味のある記憶）",
    # ---- 塔的旁白 ----
    "white_room.text": "意識が途切れる——気づけば始まりの場所に戻されていた。",
    "white_room.reason": "記憶が尽きた（始まりの場所へ戻る）",
    "entrance_found.text": "隠された塔の入口を見つけた（入口は毎回変わる）",
    "entrance_found.note": "入口を見つけた——次に入れる（他の人はまた探す）",
    "boss_defeated.text": "最強の敵を討った——最上階の宝が開く",
    "boss.slain_by": "「{place}」の主を討った",
    "assimilated.text": "塔内で飲食した——塔に取り込まれ、記憶が削られる",
    "tower.enter_note": "扉を開けると塔の中へ転送される。塔内のものは持ち出せない（神器を除く）",
    # ---- 棋盤／塔的規則錯誤（回給出手的人看）----
    "board.err.tower_ride_only": "塔内只能用 ride 换层（楼梯无效）；离开用 exit_tower",
    "board.err.ride_outside": "ride は塔の中でしか使えない",
    "board.err.no_ride_route": "{node} 没有通往 {target} 的通路",
    "board.err.foes_block_ride": "この階の敵が残っている（{left} 体）——全滅させないと上へは行けない",
    "board.err.fight_outside": "塔の外の魔物は attack で扱う（fight は塔内の敵専用）",
    "board.err.no_foes": "この階に敵はいない（もう掃討済み）",
    "board.err.no_wish_box": "{node} に宝箱はない",
    "board.err.wish_box_used": "この宝箱はもう開いている（{node}）——一度きりだ",
    "board.err.wish_box_guarded": "敵が守っている——先に全滅させる",
    # ---- 商店的錯誤 ----
    "shop.err.coins_short": "貨幣が足りない（{have}/{cost}）",
    "shop.err.coins_short_table": "貨幣不夠（{have}/{cost}）——去打敵人或賣東西換",
    # ---- 半合作 ----
    "contest.rows": "{kind} @ {node} の競合（{n} 人）",
    "contest.left": "勝ったが、その場にいない（宣言後に移動した）",
    # ---- 神器／道具的代價（memory_change.reason）----
    "memory.artifact_cost": "神器「{name}」を得た代償（tier {tier}）",
    "memory.item_effect": "{item} の効果",
    "memory.tower_food": "塔内饮食（一体化）",
    # ---- 引擎自己代打的那一手（超時）----
    "timeout.defend_note": "超时代打",
    # ---- 死亡與送還（death.py）----
    "death.text": "致命の一撃——意識が途切れ、気づけば最寄りの街の床に運ばれている",
    "death.memory_lost": "死の際に記憶を落とした",
    "death.turn_skipped": "傷は癒えていない（あと {remaining} 回合は動けない）",
    # ---- 補課行（`render_log_tail`；不進事件流，每次現算）----
    "tail.elevator": "{who} 换层 → {to}",
    "tail.mob_killed": "{who} 击破一体敌人（{node}，+{coins} 貨幣）",
    "tail.artifact_gained": "★ {who} 获得神器「{name}」(tier {tier})，记忆代价 {memory_cost}（d20={die}）",
    "tail.shop_buy": "{who} 在店里买了 {item}（{cost} 貨幣）",
    "tail.white_room": "⚠ {who} 被塔吞掉，意识中断（记忆回 {memory}）",
    # ---- 喚醒訊息（`wake.kick_prompt` 的棋盤段）----
    "wake.coins": " 貨幣 {n}",
    "wake.foes_left": "⚠ 这个节点还剩敌人 {n} 体（fight 清掉才能上楼）",
    "wake.wish_box": "这里有宝箱（tier {tier}）：pray 可以换神器，但会扣记忆",
    "wake.shop": "这里有商店：buy 用貨幣买东西（塔内吃东西会掉记忆）",
    # ---- MCP 工具描述（`mcp_api.tools_for` 的棋盤段）----
    "mcp.board_act": (" 本桌是棋盘制：move 必须给 target=相邻节点 id（先 table_view 看 board.can_go）；"
                      "塔内只能 ride（楼层间唯一通路）；清敌人用 fight；宝箱换神器用 pray；"
                      "出塔用 exit_tower（塔内所得会被收夺）。"),
    "mcp.board_view": " 含棋盘：你在哪个节点、可去哪些节点、该节点的敌人/宝箱/商店。",
}


def make_bank(*layers: Optional[Mapping[str, Any]]) -> Dict[str, str]:
    """疊一本**這一桌的**句庫：內核預設 ← 各層（包 → 模組 → 劇本；**後者蓋前者**）。

    回一份新字典（不改 `TEXT`）。同一層裡的衝突由各層自己管（例：兩個包同鍵不同文案在 `packs` 當場叫）；
    這裡只做「上層蓋下層」——模組改內核的措辭正是這一本存在的理由。
    """
    out = dict(TEXT)
    for layer in layers:
        for k, v in (layer or {}).items():
            out[str(k)] = str(v)
    return out


def text(key: str, bank: Optional[Mapping[str, str]] = None, /, **facts: Any) -> str:
    """取一條文案並填事實（`bank`＝這一桌的句庫，見 `make_bank`；沒給＝內核預設）。

    缺 key 直接爆（不給靜默的空白句——寧可當場看到）。
    """
    tpl = bank.get(key) if bank is not None else None
    if tpl is None:
        tpl = TEXT.get(key)
    if tpl is None:
        known = sorted(set(TEXT) | set(bank or {}))
        raise KeyError(f"句庫沒有這條：{key}（可用：{known}）")
    return tpl.format(**facts) if facts else tpl


def render_log_tail(events: List[Dict[str, Any]], *,
                    name_of: Callable[[Optional[str]], str],
                    place_of: Callable[[Optional[str]], str],
                    since_seq: Optional[int] = None, limit: int = 16,
                    bank: Optional[Mapping[str, str]] = None,
             slot_events: Optional[Mapping[str, Any]] = None,
             term: str = "碎片") -> List[str]:
    """公開事件流 → 「人話」補課行（kick prompt 與 `view.recent` 共用同一支）。

    這是「agent 看不到別人幹了什麼」的正解：光把事件寫進 log 不夠，必須**推**給席位
    （kick prompt 帶「你上次出手之後發生了什麼」）。碎片正文永不出現——只寫「一枚碎片落到 X 手上」。

    `name_of(seat)`／`place_of(node_id)` 由呼叫端給（引擎手上有席位表與節點表）；這一層不讀 state。
    `bank`＝這一桌的句庫（帶作品語彙的那幾行走 `tail.*` 鍵）；沒給＝內核預設。
    """
    def _ev(name: str) -> tuple:
        """這個槽位**認得的**事件型別——由呼叫端算好傳進來（L4 不 import board、不讀 state）。"""
        return tuple((slot_events or {}).get(name) or ())

    evs = [e for e in events if e.get("public", True)]
    if since_seq is not None:
        evs = [e for e in evs if e["seq"] > since_seq]
    out: List[str] = []
    for e in evs[-limit:]:
        t = e["type"]
        if t == "say":
            out.append(f"{name_of(e.get('seat'))}：「{(e.get('text') or '').strip()[:120]}」")
        elif t == "gm_say":
            out.append(f"GM：「{(e.get('text') or '').strip()[:120]}」")
        elif t == "action_declared":
            continue                                  # 噪声：结果由下面的事件体现
        elif t == "move":
            out.append(f"{name_of(e.get('seat'))} 移动 {place_of(e.get('frm'))}→{place_of(e.get('to'))}")
        elif t == "elevator" or t in _ev("lift"):
            out.append(text("tail.elevator", bank, who=name_of(e.get("seat")), to=place_of(e.get("to"))))
        elif t == "entered_tower":
            out.append(f"{name_of(e.get('seat'))} 进入塔内（{place_of(e.get('floor'))}）")
        elif t == "entrance_found":
            out.append(f"{name_of(e.get('seat'))} 找到了塔的入口（{place_of(e.get('node'))}）")
        elif t == "mob_killed" or t in _ev("mob"):
            out.append(text("tail.mob_killed", bank, who=name_of(e.get("seat")),
                            node=place_of(e.get("node")), coins=e.get("coins")))
        elif t == "boss_defeated":
            out.append(f"★ {name_of(e.get('seat'))} 击破了 BOSS（{place_of(e.get('node'))}）")
        elif t == "artifact_gained":
            out.append(text("tail.artifact_gained", bank, who=name_of(e.get("seat")), name=e.get("name"),
                            tier=e.get("tier"), memory_cost=e.get("memory_cost"), die=e.get("die")))
        elif t == "memory_change":
            out.append(f"{name_of(e.get('seat'))} 记忆 {e.get('before')}→{e.get('after')}（{e.get('reason')}）")
        elif t == "damage":
            out.append(f"{name_of(e.get('target'))} 受伤 {e.get('amount')}（剩 HP {e.get('hp_after')}）")
        elif t == "heal":
            out.append(f"{name_of(e.get('seat'))} 回血 {e.get('amount')}（剩 HP {e.get('hp_after')}）")
        elif t == "shop_buy":
            out.append(text("tail.shop_buy", bank, who=name_of(e.get("seat")), item=e.get("item"),
                            cost=e.get("cost")))
        elif t == "tower_exit":
            out.append(f"{name_of(e.get('seat'))} 出塔（{place_of(e.get('to'))}）")
        elif t == "white_room" or t in _ev("sanctum"):
            out.append(text("tail.white_room", bank, who=name_of(e.get("seat")), memory=e.get("memory")))
        elif t == "assimilated":
            out.append(f"⚠ {name_of(e.get('seat'))} 被一体化（塔在吞他的记忆）")
        elif t == "round_started":
            out.append(f"—— 第 {e.get('no')} 轮开始")
        elif t == "round_ended":
            out.append(f"—— 第 {e.get('no')} 轮结束")
        elif t == "declare":
            out.append(f"{name_of(e.get('seat'))} 宣言争夺 {e.get('kind')}@{place_of(e.get('node'))}")
        elif t == "declare_won":
            out.append(f"★ 争夺揭晓：{name_of(e.get('seat'))} 拿下 {place_of(e.get('node'))}")
        elif t == "declare_failed":
            out.append(f"争夺揭晓：{name_of(e.get('seat'))} 在 {place_of(e.get('node'))} 落空"
                       + (f"（{e.get('reason')}）" if e.get("reason") else ""))
        elif t == "declare_resolved":
            out.append(f"争夺揭晓：{place_of(e.get('node'))} 归 {name_of(e.get('winner') or e.get('seat'))}")
        elif t == "fragment_granted":
            out.append(f"一枚{term}落到 {name_of(e.get('holder'))} 手上（内容只有他看得到）")
        elif t == "fragment_seen":
            out.append(f"{name_of(e.get('seat'))} 向 {name_of(e.get('to'))} 出示了一枚{term}")
        elif t == "fragment_given":
            out.append(f"{name_of(e.get('seat'))} 把一枚{term}交给了 {name_of(e.get('to'))}")
        elif t == "wish_done":
            out.append(f"★ {name_of(e.get('seat'))} 兑现了自己的私密愿望")
        elif t == "settle":
            out.append("—— 结算")
        elif t in ("condition_set", "condition_tick"):
            # 句子的正本在包的 text.json（引擎在 emit 時就把 key 展開成 text 帶進事件）
            line = (e.get("text") or "").strip()
            if t == "condition_tick" and not e.get("damage"):
                if not line:
                    continue
            out.append(line or (f"{name_of(e.get('seat'))} 狀態變化（{e.get('id')}）"
                                if t == "condition_set" else
                                f"{name_of(e.get('seat'))} 受到 {e.get('id')} 的侵蝕"))
        elif t == "death":
            out.append(f"☠ {name_of(e.get('seat'))} 被击倒——送还到「{place_of(e.get('to'))}」"
                       f"（缺席 {e.get('skip')} 回合，HP 回到 {e.get('hp_after')}）")
        elif t == "turn_skipped":
            out.append(f"{name_of(e.get('seat'))} 还在送还中（还剩 {e.get('remaining')} 回合不能动）")
        elif t == "audio":
            # 音訊（2026-10-20）：只寫「哪個聲道、哪一首」——不放 URL（日誌是給人看的）
            ch = e.get("ch") or "bgm"
            if str(e.get("op")) == "play":
                name = e.get("title") or e.get("key") or "?"
                if isinstance(name, dict):                    # 多語系標題：取一個能看的
                    name = name.get("zh_Hans") or name.get("ja") or next(iter(name.values()), "?")
                tail = "（模組提示）" if e.get("auto") else ""
                out.append(f"♪ {ch}：{name}{tail}")
            else:
                out.append(f"♪ {ch} 停止")
        elif t in ("kick", "acted", "roll", "search_loot", "status_set"):
            continue
    return out
