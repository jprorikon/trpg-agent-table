"""view（自 __init__.py 拆出）"""

from __future__ import annotations
from typing import Any, Dict, List, Optional

from .base import _assets, _aurl, _ending_info, _run_summary, _seat_art, _with_icon, own, scrub_public, trim_secrets
from .sections import _board_section, _seat_plan_public, _seat_public, _semi_section

def project(state: Dict[str, Any], deps: Any, *, viewer: Optional[str] = None, gm: bool = False,
            detail: str = "full", now: float, recent: Optional[List[str]] = None) -> Dict[str, Any]:
    """`view(state, viewer) → ViewModel`（純）。

    `now` 必須由呼叫端注入（這一層不讀時鐘）；`recent` 是 L4 產的「人話尾巴」（沒有就給空）。
    """
    compact = str(detail or "full").strip().lower() == "compact"
    window = int((deps.cfg.get("view") or {}).get("log_window") or 60)
    if compact:
        window = max(1, int((deps.cfg.get("view") or {}).get("log_window_compact") or 12))
    log_events = state["log"][-window:] if not gm else state["log"]
    # 逐 seq 去重：**舊版對 GM 分支算了去重卻沒用**（直接回 `self.state["log"]`）⇒ 同一顆事件
    # 會出現兩三次（歷史資料有 17 個重號 seq，是 2026-10-06 修 `_emit` 蓋號之前留下的）。
    # 這裡兩條路同一支口徑：投影裡的 seq 唯一（前端拿 seq 當增量游標才不會亂）。
    seen = set()
    public: List[Dict[str, Any]] = []
    for ev in log_events:
        if ev["seq"] in seen:
            continue
        seen.add(ev["seq"])
        public.append(own(ev) if gm else scrub_public(ev, viewer))
    mine = state["secrets"].get(viewer, []) if viewer else []
    cur = state["turn"]
    _paused = bool(state.get("paused"))
    _ref = float(state.get("paused_at") or now) if _paused else now   # 暫停中：秒數凍在那一刻
    _mf = (_assets(deps).manifest() if _assets(deps) is not None else {})
    # ★ 音訊（2026-10-20；**純附加**）：只有這一模組真的放了 `audio.json` 才露這一格。
    #   內容＝「現在在放什麼」（由 `audio` 事件折出的現況＋seq）＋入口（前端拿表預載）。
    #   GM 另外看得到**可選清單**（key／title／gain）——LLM 不必去讀檔就知道能放什麼。
    _audio_t = getattr(deps, "audio", None)
    try:
        _audio_ok = bool(_audio_t is not None and _audio_t.ok)
    except Exception:                                             # noqa: BLE001
        _audio_ok = False
    _ast = (state.get("audio") or {}) if _audio_ok else {}
    _audio_block = ({"bgm": own(_ast.get("bgm")), "sfx": own(_ast.get("sfx")),
                     "seq": int(_ast.get("seq") or 0),
                     "library": _audio_t.manifest(),
                     **({"available": _audio_t.catalog(), "cues": _audio_t.cues()} if gm else {})}
                    if _audio_ok else {})
    v: Dict[str, Any] = {
        "table": {"table_id": deps.cfg.get("table_id"), "scenario": deps.cfg.get("scenario"),
                  "rules": deps.rules["name"], "now": now,
                  # ★ 素材（2026-10-09；**純附加**）：**只有這一模組真的有素材表**才露
                  #   `module`（所屬模組 id）與 `assets`（入口：base／index）。
                  #   沒素材的模組／legacy 桌 ⇒ 這兩格都不加 ⇒ 投影逐字不變（P27 那一類的閘咬得住）。
                  **({"module": str(getattr(deps, "module_id", "") or ""), "assets": _mf} if _mf else {})},
        **({"audio": _audio_block} if _audio_block else {}),
        "paused": _paused,
        "paused_seconds": (max(0, int(now - float(state.get("paused_at") or 0)))
                           if _paused else 0),
        # 這一局已經判了結局（`ending` 事件的 id）——**判了就不再倒數／不再換手**（pacing.tick 停住）；
        # 前端用它顯示「本局已結束」（沒判結局＝null，legacy 視圖多一個 null 欄位但兩邊一致）。
        "ended": state.get("ended"),
        # ★3 結局收尾：結局的名字／類型（**從 module 查，不是新 state 欄位**）＋戰果結算（只有判了才給）
        "ended_info": _ending_info(deps, state),
        "summary": (_run_summary(deps, state) if state.get("ended") else None),
        # 暫停中：剩餘秒數**停在暫停那一刻**（設計者 2026-10-06：「暫停期間直接停在當前時間」）。
        # 死線本身沒動（state 不動、恢復時才把凍掉的秒數加回死線）——這裡只是讓**顯示**與事實一致：
        # 以前只算 deadline-now ⇒ 暫停中畫面倒數還在跑、恢復後又跳回去＝看起來像沒凍住。
        "turn": None if not cur else {
            "seat": cur["seat"], "index": cur["index"], "seq": cur["seq"],
            "is_yours": bool(viewer and cur["seat"] == viewer),
            # 判了結局：秒數**凍在 0**（時間軸停了 ⇒ 顯示不該還在跑；設計者 2026-10-07 抓到這一條）
            "seconds_left": (0 if state.get("ended")
                             else max(0, int(cur["deadline_at"] - _ref))),
            "waits": cur["waits"],
        },
        "seats": [_seat_public(state, s, gm, deps) for s in state["seats"].values()],
        # ★ 席次表（2026-10-19 設計者：「席位一览里要能看到哪个席位缺席了」）：**劇本排的席位**
        #   對照「這一局實際入座」⇒ 缺席的也列出來（`present:false`），前端置灰。
        #   只有劇本層真的排了席位才露這一格（legacy／沒 seats.json 的桌 ⇒ 逐字不變）。
        **({"seat_plan": _seat_plan_public(state, deps, gm)} if getattr(deps, "seat_plan", None) else {}),
        "log": public,
        "your_secrets": [{"text": s["text"], "from": s.get("frm"), "to": s.get("to"),
                          "peer": s.get("peer") or "", "seq": s["seq"],
                          "ts": s.get("ts")} for s in mine],
        # 誰正在打字／思考（純記憶體、不進事件流）：只回最近 15 秒還在動的
        "typing": {k: own(tv) for k, tv in getattr(deps, "typing", {}).items()
                   if now - (tv.get("ts") or 0) < 15},
        # 公开事件的「人话」版尾巴：轮询型客户端/人类页面都能直接显示
        # （compact 版不给：`log` 已經是同一個尾巴，重複投影＝白付 token）
        "recent": own(recent or []),
        "allowed_actions": own(deps.visible_action_ids),
        # 动作目录（協作端 2026-10-05 §2105 契约）：{id,label,desc}——前端只认 id 对行为，
        # label/desc 一律数据驱动（剧本给、前端原样渲染，不进翻译服务）；
        # 新前端只渲染本数组里出现的 id（空砖自然消失）。旧前端读 allowed_actions（保留不动）。
        "actions": [own(a) for a in deps.actions_catalog],
    }
    if viewer:
        s = state["seats"].get(viewer)
        # ★資源上限（設計者 2026-10-07：「魔力和記憶你需要設計好上限；魔力上限可以跟著等級或者星級走」）
        #   係數住 `rules.resources`（包給預設、config 一律贏）：記憶＝基礎＋意志×per_wil＋(每級)；
        #   魔力＝基礎＋(每級)＋星等×per_star。**沒啟用帶這顆旋鈕的包 ⇒ 這段不算 ⇒ 舊桌投影一字不變**；
        #   前端沒看到上限就退回「以 100 為滿格」的量感畫法。
        _caps_: Dict[str, int] = {}
        _rc_ = ((getattr(deps, "rules", None) or {}).get("resources") or {})
        if _rc_ and s:
            _lv_ = int((state.get("level") or {}).get(viewer) or 1)
            _st_ = int((state.get("star") or {}).get(viewer) or 0)   # `state["star"][seat]` 是數字（星等）
            _wil_ = int((s.get("attrs") or {}).get("wil") or 0)
            _mr_ = _rc_.get("memory") or {}
            if _mr_:
                _cap_ = (int(_mr_.get("base") or 0) + int(_mr_.get("per_wil") or 0) * _wil_
                         + int(_mr_.get("per_level") or 0) * (_lv_ - 1))
                _caps_["max_memory"] = max(_cap_, int(s.get("memory") or 0))   # 不低於當前值（保護舊資料）
            _ar_ = _rc_.get("mana") or {}
            if _ar_:
                _capm_ = (int(_ar_.get("base") or 0) + int(_ar_.get("per_level") or 0) * (_lv_ - 1)
                          + int(_ar_.get("per_star") or 0) * _st_)
                _curm_ = int(((state.get("resources") or {}).get(viewer) or {}).get("mana") or 0)   # 資源住 state["resources"][seat]，不在 seat 字典裡
                _caps_["max_mana"] = max(_capm_, _curm_)      # 同記憶：上限不低於當前值
        _coin = getattr(deps, "slot", lambda _n: "star_coins")("coin")
        v["you"] = None if not s else {
            # ⚠ `you.kind` 給**真值**（自己知道自己是什麼：人就是人、agent 就是 agent）——
            #   玩家平等原則抹的是「**別人**看得出來」的那一面（`seats[].kind`／公開日誌）。
            "seat": s["seat"], "name": s["name"], "kind": s["kind"],
            "attrs": own(s["attrs"]), "hp": s["hp"], "max_hp": s["max_hp"],
            "status": own(s["status"]),
            "node": s.get("node"), "memory": s.get("memory"),
            # （_coin 在下面 you 段之前定義——見 `_coin = getattr(deps, "slot", …)("coin")`）
            **({"max_memory": _caps_["max_memory"]} if "max_memory" in _caps_ else {}),
            **({"max_mana": _caps_["max_mana"]} if "max_mana" in _caps_ else {}),
            "artifacts": own(_with_icon(deps, s.get("artifacts", []),
                                        lambda a: (f"icon:artifact:name:{a.get('name')}"
                                                   if a.get("name") else ""))),
            # 槽位（2026-10-20）：抽象名 `coin` ＋ 舊別名（前端契約，保留相容）
            "coin": s.get(_coin, 0), "star_coins": s.get(_coin, 0),
            "roster": next((own(r) for r in getattr(deps, "roster", {}).values()
                            if r["seat"] == s["seat"]), None),
        }
        # 自己身上的條件（含還剩幾回合／扣多少）——沒有的話不給這一格（legacy 視圖不變）
        _am = own((state.get("attr_mods") or {}).get(viewer) or {}) if viewer else {}
        _rs = own((state.get("resources") or {}).get(viewer) or {}) if viewer else {}
        if _am:
            v["you"]["attr_mods"] = _am
        if _rs:
            v["you"]["resources"] = _rs
        # M3／M4：技能清單與經驗／等級進度——**只有這一席真的有等級紀錄時才出現**
        # （舊局／沒啟用經驗機制的桌：`level` 是空的 ⇒ 一個欄位都不加，legacy 視圖逐字不變）
        if (state.get("level") or {}).get(viewer):
            _sk = (deps.skills_of(viewer) if hasattr(deps, "skills_of") else []) or []
            if _sk:
                v["you"]["skills"] = own(_with_icon(
                    deps, _sk, lambda r: f"icon:skill:{r.get('id')}" if r.get("id") else ""))
            _pg = (deps.progression_view(viewer) if hasattr(deps, "progression_view") else {}) or {}
            v["you"]["level"] = int(_pg.get("level") or 1)
            v["you"]["exp"] = int(_pg.get("exp") or 0)
            if _pg.get("next_need") is not None:
                v["you"]["next_need"] = int(_pg["next_need"])
        # ★2：道具清單（`you.items`）——**有道具才出現**（沒啟用道具表＝空 ⇒ 欄位不加）
        _iv = (deps.items_view(viewer) if hasattr(deps, "items_view") else []) or []
        if _iv:
            v["you"]["items"] = own(_with_icon(
                deps, _iv, lambda it: f"icon:item:{it.get('id')}" if it.get("id") else ""))
        # 星等（升星＝劇情事件觸發）：**只有這一席真的有星等紀錄時才出現**（跟 level／skills 同款規矩）
        # ——沒啟用星等表的桌、或還沒升過星的舊局：一個欄位都不加（legacy 視圖逐字不變，P27 咬住）。
        if (state.get("star") or {}).get(viewer):
            _sv = (deps.stars_view(viewer) if hasattr(deps, "stars_view") else {}) or {}
            if _sv:
                v["you"]["star"] = own(_sv)
        _cv = (deps.conditions_view(viewer)
               if hasattr(deps, "conditions_view") else {}) or {}
        if _cv:
            v["you"]["conditions"] = own(_cv)
        # ★ 素材（2026-10-09）：自己那一格的立繪／頭像，以及「狀態 id → 圖示 URL」
        #   （部位受傷／異常狀態的圖；沒素材的模組一個鍵都不加）
        if v.get("you") is not None:
            _seat_art(deps, v["you"], viewer)
            _st_ids = [x for x in (s.get("status") or [])] + [c for c in _cv]
            _si = {str(k): _aurl(deps, f"icon:condition:{k}") for k in _st_ids}
            _si = {k: u for k, u in _si.items() if u}
            if _si:
                v["you"]["status_icons"] = _si
        # 死亡送還（death.py）：**我還被跳過幾個回合**——只有 >0 才出現（沒啟用這機制的桌不多一格）
        _skip_n = int(((state.get("skip") or {}).get(viewer) or 0)) if viewer else 0
        if _skip_n > 0 and v.get("you") is not None:
            v["you"]["skip_turns"] = _skip_n
    if gm:
        v["gm"] = {"full_state": own(state),
                   "secrets_all": {k: [x["text"] for x in v2]
                                   for k, v2 in state["secrets"].items()},
                   # GM 能看到的暗骰结果（public=False 的 roll 不进 state["log"]）
                   "secret_rolls": [own(e) for e in deps.events
                                    if e.get("type") == "roll" and not e.get("public", True)][-20:],
                   # 神器图鉴（含隐藏效果）——**只有 GM 视图拿得到**
                   "artifact_catalog": own(deps.artifact_catalog())
                   if hasattr(deps, "artifact_catalog") else []}
    v.update(_board_section(state, deps, viewer))
    v.update(_semi_section(state, deps, viewer))
    # ★ 2026-10-21：頂層視圖原本**沒有輪號**（只有 turn.index）⇒ 被叫醒的 agent 不知道第幾輪、
    #   前端 `v.round_no` 也一直是 undefined（「第 N 輪」那幾格顯示不出來）。additive，不動既有欄位。
    # 語意：`round_no`＝**現在是第幾輪（1 起算）**——還在打第 1 輪就是 1（agent 問「第幾輪」要的是這個）。
    #       `rounds_done`＝已打完的輪數（＝state.round_no；大廳的 roundNo 用這個）。
    _done = int(state.get("round_no") or 0)
    v["round_no"] = _done + 1 if state.get("turn") else _done
    v["rounds_done"] = _done
    # ★ 2026-10-21：模組自訂用詞（`scenario.terms.fragment`；例：hyakki＝御札）。
    #   引擎只認 key，顯示名一律問 `Table.term_name()`——投影帶出去，前端才不會永遠寫「碎片」。
    #   模組沒宣告 ⇒ 一個鍵都不加（legacy 視圖逐字不變）。
    _terms: Dict[str, str] = {}
    if getattr(deps, "module", None) is not None and getattr(deps.module, "terms", None):
        for _k in ("fragment",):
            try:
                _terms[_k] = str(deps.term_name(_k))
            except Exception:                                     # noqa: BLE001
                pass
    if _terms:
        v["terms"] = _terms
    # ★ 2026-10-21：開局難度（建桌時選的 easy／normal／hard）——大廳與桌面都該看得到。
    #   只看 `config.difficulty`（`table_factory.apply_difficulty` 會把選的檔寫回去；沒寫＝legacy 桌）。
    #   ⚠ 不拿 `state.difficulty`：那是 L3 導演的**運行時**檔位（`difficulty_set`），與開局難度是兩顆旋鈕。
    _diff = (getattr(deps, "cfg", {}) or {}).get("difficulty")
    if _diff:
        v["difficulty"] = str(_diff)
    if compact:
        # 重複投影（公開日誌的第二次、給人類頁面用的「人話尾巴」）只留 full 版
        v.pop("recent", None)
        # 私訊也收窗口（2026-10-07 設計者：這堆無上限長出來、每輪重送）；投影本身不裁，
        # 只有 compact 這一版收——要看完整收件匣用 `detail="full"`。
        _keep, _hid = trim_secrets(v.get("your_secrets"), deps.cfg)
        if _hid:
            v["your_secrets"] = _keep
            v["your_secrets_hidden"] = _hid
        v["detail"] = "compact"
    return v

def adapter_view(state: Dict[str, Any], deps: Any, *, now: float) -> Dict[str, Any]:
    """**給流程用的**世界讀出口（GM 級、含私密欄位）。

    與 `project()` 的分工：
      * `project()` 是**給人看的 ViewModel**（per-viewer、scrub 過、白名單日誌）。
      * `adapter_view()` 是**給 L6 流程用的**（踢誰、喚醒收斂鍵、路由用的 `kind`、健康檢查）——
        所以它含 `wake_url`／`run_id`／`settled` 這種**不能給玩家**的東西，
        **只准 L6 內部使用**（`view()` 永遠不含 `ops` 這把鑰匙，test_layers F 組會擋）。

    純度與 `project` 相同：只讀輸入、吐新物件、時鐘由呼叫端注入、不算規則
    （需要規則時只呼叫 `deps` 的**查詢**方法，例如 `pending_actors()`）。
    """
    seats: Dict[str, Any] = {}
    for sid, s in (state.get("seats") or {}).items():
        seats[sid] = {"seat": sid, "name": s.get("name"), "kind": s.get("kind"),
                      "node": s.get("node"), "memory": s.get("memory"), "hp": s.get("hp"),
                      "down": "down" in (s.get("status") or []),
                      "wake_url": s.get("wake_url")}
    log = state.get("log") or []
    last_acted: Dict[str, int] = {}
    counts = {"turn_ended": 0, "round_ended": 0}
    for ev in log:
        t = ev.get("type")
        if t == "acted" and ev.get("seat"):
            last_acted[ev["seat"]] = max(last_acted.get(ev["seat"], 0), int(ev.get("seq") or 0))
        elif t in counts:
            counts[t] += 1
    # GM 最後一次出聲（喚醒收斂鍵用）：走**全部事件**（含未公開的私訊），與舊行為一致
    last_gm = 0
    for ev in (deps.events if hasattr(deps, "events") else []):
        if ev.get("type") == "gm_say" or ev.get("via") == "gm" \
                or (ev.get("type") == "secret" and ev.get("frm") == "gm"):
            last_gm = max(last_gm, int(ev.get("seq") or 0))
    semi = state.get("semi") or {}
    turn = state.get("turn") or None
    paused = bool(state.get("paused"))
    paused_at = float(state.get("paused_at") or 0)
    return {
        "run_id": state.get("run_id"),
        "paused": paused,
        "ended": state.get("ended"),
        "paused_seconds": int(max(0.0, now - paused_at)) if (paused and paused_at) else 0,
        "round_no": int(state.get("round_no") or 0),
        "round_start_seq": int(state.get("round_start_seq") or 0),
        "last_seat": state.get("last_seat"),
        "difficulty": state.get("difficulty"),
        "turn_index": int(state.get("turn_index", -1)),
        "turn_key": (f"{turn['seat']}:{turn['seq']}" if turn else None),
        "turn": None if not turn else {
            "seat": turn.get("seat"), "seq": turn.get("seq"), "index": turn.get("index"),
            "waits": turn.get("waits"), "deadline_at": turn.get("deadline_at")},
        "seats": seats,
        "agents": [sid for sid, v in seats.items() if v["kind"] == "agent"],
        "humans": [sid for sid, v in seats.items() if v["kind"] == "human"],
        "npc": [sid for sid, v in seats.items() if v["kind"] == "npc"],
        "gm": [sid for sid, v in seats.items() if v["kind"] == "gm"],
        # ★ 時鐘（2026-10-20）：`{name: 現在值}`——GM／流程用（玩家的公開投影另外接）
        "clocks": {str(k): int(v) for k, v in (state.get("clocks") or {}).items()},
        "settled": bool(semi.get("settled")),
        "last_acted": last_acted,
        "last_gm_seq": last_gm,
        "counts": counts,
        "pending_actors": ([x for x in deps.pending_actors()]
                           if hasattr(deps, "pending_actors") else []),
    }
