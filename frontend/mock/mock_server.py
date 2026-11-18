#!/usr/bin/env python3
"""本地假桌：拿 fixtures/*.json 起来给前端开发用（标准库，无依赖）。

    python3 mock/mock_server.py            # → http://127.0.0.1:8978/
    python3 mock/mock_server.py 9000       # 换端口

它模仿真桌的**形状**（同样路径、同样字段），**只演契约保证的那几步**，不判规则：

  * `POST /api/mock/scene {"text":"…"}`：**开一景**（假桌新增的接口，真桌没有）。
    会清掉本景额度、把 `stage` 改成开着的这一景、往日志 append 一条 `scene_open`。
    没有开着的景时，`act`/`say` 一律照引擎的样子拒掉（fixtures 是「已收景」的快照，额度本来就用完）。
  * `POST /api/act`：额度够 → append `action_declared`、额度 +1、该席位从 `scene_waiting` 拿掉、`you_acted=true`；
    **额度不够／没开景／动作不在允许列表 → HTTP 409 ＋ `{"error":"…","kind":"rule"}`，不 append**。
    骰子/HP/记忆/碎片不由假桌决定——那些永远算在引擎里。
  * `POST /api/say` → append `say`、发言额度 +1；额度不够同样 409 ＋ 顶层 error。
  * `POST /api/join`：只回显一份视图，不改状态。

额度上限照 fixtures 的 `throttle.limits`（act 2 / say 3）；假桌自己算的额度只存在**内存**，
不写回 fixtures/（跑测试不会弄脏 git 工作区）。重启进程即全部清空。

契约以 `docs/API.md` §2 为准：**规则拒绝＝409 ＋ 顶层 `error`＋`kind:"rule"`，消息不带内部类名**；
前端原样展示。假桌自己的文案会注明「假桌」，真文案以引擎为准。
"""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FX = os.path.join(ROOT, "fixtures")
WEB = os.path.join(ROOT, "web", "index.html")

# ---- 内存会话状态（不落盘）----
SESSION = {"events": [], "used": {}, "acted": set(), "scene": None, "scene_text": ""}


def load(name):
    with open(os.path.join(FX, name), "r", encoding="utf-8") as fh:
        return json.load(fh)


def views():
    """按席位挑一份 fixture；未知席位 → 旁观视图 + 造一个 you（方便用任意 seat 名开发）。"""
    return load("state_p1.json"), load("state_spectator.json"), load("state_agent.json")


def _max_seq(v):
    base = [e.get("seq", 0) for e in (v.get("log") or [])] + [e.get("seq", 0) for e in SESSION["events"]]
    return max(base) if base else 0


def _limits(v):
    lim = ((v.get("throttle") or {}).get("limits") or {})
    return {"act": lim.get("act", 2), "say": lim.get("say", 3)}


def open_scene(text=""):
    """開一景：額度歸零、stage 換成新的一景、append scene_open。回傳新景號。"""
    v = load("state_agent.json")
    last = (v.get("stage") or {}).get("no") or 0
    for e in (v.get("log") or []):
        if e.get("type") == "scene_open" and isinstance(e.get("no"), int):
            last = max(last, e["no"])
    for e in SESSION["events"]:
        if e.get("type") == "scene_open" and isinstance(e.get("no"), int):
            last = max(last, e["no"])
    SESSION["scene"] = last + 1
    SESSION["scene_text"] = text or "（假桌）新的一景：這裡是假桌自己開的景。"
    SESSION["used"], SESSION["acted"] = {}, set()
    SESSION["events"].append({
        "seq": _max_seq(v) + 1, "ts": time.time(), "type": "scene_open",
        "public": True, "no": SESSION["scene"], "text": SESSION["scene_text"]})
    return SESSION["scene"]


def apply_session(v, seat=None):
    """把内存状态叠到 fixture 视图上（只叠，不改 fixture）。"""
    if "log" in v:
        v["log"] = list(v["log"]) + list(SESSION["events"])
    if SESSION["scene"] is not None:
        # 假桌開著一景 ⇒ stage/scene_waiting/you_acted 都以這一景為準
        v["stage"] = {"no": SESSION["scene"], "text": SESSION["scene_text"], "open": True}
        if seat:
            v["you_acted"] = seat in SESSION["acted"]
        if v.get("scene_waiting"):
            v["scene_waiting"] = [s for s in v["scene_waiting"] if s not in SESSION["acted"]]
    if not seat:
        return v
    used = SESSION["used"].get(seat) or {}
    if used:
        th = v.setdefault("throttle", {})
        th.setdefault("used", {})
        for k, n in used.items():
            if SESSION["scene"] is not None:
                th["used"][k] = n          # 本景以假桌自己的計數為準
            else:
                th["used"][k] = th["used"].get(k, 0) + n
    elif SESSION["scene"] is not None:
        th = v.setdefault("throttle", {})
        th["used"] = {}                    # 新景：額度從零起算
    if seat in SESSION["acted"]:
        v["you_acted"] = True
    return v


def quota(v, seat, kind):
    """(已用, 上限)。假桌開著景時只算自己的計數；否則照 fixture（=已收景、額度用完）。"""
    lim = _limits(v)[kind]
    if SESSION["scene"] is None:
        return lim, lim
    return (SESSION["used"].get(seat) or {}).get(kind, 0), lim


class H(BaseHTTPRequestHandler):
    server_version = "MockTable/0.2"

    def _send(self, code, body, ctype="application/json"):
        raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if "text" in ctype else ""))
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _refuse(self, msg):
        """規則拒絕：跟引擎同形狀——409 ＋ 頂層 error ＋ kind=rule（見 docs/API.md §2）。"""
        return self._send(409, {"error": msg, "kind": "rule"})

    def log_message(self, fmt, *a):        # 轮询太吵，跳过 /api/state
        if "/api/state" in (fmt % a):
            return
        sys.stderr.write("%s %s - %s\n" % (time.strftime("%H:%M:%S"), self.address_string(), fmt % a))

    # ---- 读 ----
    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            with open(WEB, "rb") as fh:
                return self._send(200, fh.read(), "text/html")
        if u.path == "/health":
            return self._send(200, {"status": "ok", "table_id": "mock", "seats": 5,
                                    "turn": None, "scene": SESSION["scene"]})
        if u.path == "/api/map":
            return self._send(200, load("map.json"))
        if u.path == "/api/log":
            lg = load("log.json")
            lg["events"] = list(lg.get("events") or []) + list(SESSION["events"])
            if not (q.get("gm_token") or [None])[0]:   # 契約：匿名只看公開投影（白名單 ∩ public＋fid scrub）
                lg["events"] = self._public_events(lg["events"], None)
            return self._send(200, lg)
        if u.path == "/api/state":
            p1, sp, ag = views()
            seat = (q.get("seat") or [None])[0]
            v = json.loads(json.dumps(sp))
            if seat:
                v = json.loads(json.dumps(ag if seat.startswith("p3") else p1))
                v.setdefault("you", {})["name"] = v["you"].get("name") or seat
                v["you"]["seat"] = seat
            if (q.get("gm_token") or [None])[0]:
                v["gm"] = {"artifact_catalog": [], "secret_rolls": [], "full_state": {}}
            return self._send(200, self._extra(apply_session(v, seat), seat))
        return self._send(404, {"error": "not found"})

    # ---- 写（只演契约保证的那几步，不判规则）----
    def do_POST(self):  # noqa: N802
        u = urlparse(self.path)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            body = {}
        seat = body.get("seat", "?")

        if u.path == "/api/mock/scene":                 # 假桌專用：開一景
            no = open_scene(body.get("text") or "")
            return self._send(200, {"mock": True, "scene": no,
                                    "note": "假桌開了一景（真桌由 GM 收景/開景）"})

        if u.path == "/api/join":
            p1, _, _ = views()
            v = json.loads(json.dumps(p1))
            v["you"]["name"] = body.get("name") or seat
            v["you"]["seat"] = seat
            return self._send(200, {"seat": seat, "view": self._extra(apply_session(v, seat), seat)})

        if u.path == "/api/act":
            v = self._view_for(seat)
            if SESSION["scene"] is None:
                return self._refuse("假桌：目前沒有開著的景（先 POST /api/mock/scene 開一景）")
            used, limit = quota(v, seat, "act")
            if used >= limit:
                return self._refuse(f"假桌：本景行动额度已用完（{used}/{limit}）")
            action = body.get("action")
            allowed = v.get("allowed_actions") or []
            if allowed and action not in allowed:
                return self._refuse(f"假桌：动作 {action} 不在允许列表：{allowed}")
            SESSION["events"].append({
                "seq": _max_seq(v) + 1, "ts": time.time(), "type": "action_declared",
                "public": True, "seat": seat, "action": action,
                "target": body.get("target"), "note": body.get("note")})
            u_ = SESSION["used"].setdefault(seat, {})
            u_["act"] = u_.get("act", 0) + 1
            SESSION["acted"].add(seat)
            return self._send(200, {"result": {"mock": True, "action": action,
                                               "note": "假桌不判规则：只 append action_declared 并扣额度"},
                                    "view": self._view_for(seat)})

        if u.path == "/api/say":
            v = self._view_for(seat)
            if SESSION["scene"] is None:
                return self._refuse("假桌：目前沒有開著的景（先 POST /api/mock/scene 開一景）")
            used, limit = quota(v, seat, "say")
            if used >= limit:
                return self._refuse(f"假桌：本景發言額度已用完（{used}/{limit}）")
            SESSION["events"].append({
                "seq": _max_seq(v) + 1, "ts": time.time(), "type": "say",
                "public": True, "seat": seat, "text": body.get("text", "")})
            u_ = SESSION["used"].setdefault(seat, {})
            u_["say"] = u_.get("say", 0) + 1
            return self._send(200, {"view": self._view_for(seat), "mentions_pushed": []})

        if u.path == "/api/tell_gm":
            # 契約（協作端 2026-10-05）：私訊算進 say 額度；只有 GM 與發話者看得到；回顯給自己帶 peer
            v = self._view_for(seat)
            text = (body.get("text") or "").strip()
            if not text:
                return self._send(400, {"error": "seat 與 text 都是必填"})
            if SESSION["scene"] is None:
                return self._refuse("假桌：目前沒有開著的景（先 POST /api/mock/scene 開一景）")
            used, limit = quota(v, seat, "say")
            if used >= limit:
                return self._refuse(f"假桌：本景發言額度已用完（{used}/{limit}）")
            seq = _max_seq(v) + 1
            SESSION["events"].append({"seq": seq, "ts": time.time(), "type": "secret", "public": False,
                                      "seat": seat, "text": text, "frm": seat, "to": "gm"})
            SESSION["events"].append({"seq": seq + 1, "ts": time.time(), "type": "secret", "public": False,
                                      "seat": seat, "text": text, "frm": seat, "to": seat, "peer": "gm"})
            u_ = SESSION["used"].setdefault(seat, {})
            u_["say"] = u_.get("say", 0) + 1
            return self._send(200, {"ok": True, "seq": seq})

        if u.path == "/api/typing":
            # 純記憶體（不落事件）：記「seat 正在對 to 打字」，對方的 view.typing 看得到 15 秒
            to = body.get("to")
            if seat and to:
                SESSION.setdefault("typing", {})[seat] = {"to": to, "ts": time.time()}
            return self._send(200, {"ok": True})

        if u.path == "/api/gm":
            return self._send(200, {"mock": True, "op": body.get("op")})
        return self._send(404, {"error": "not found"})

    def _view_for(self, seat):
        p1, sp, ag = views()
        v = json.loads(json.dumps(ag if str(seat).startswith("p3") else p1))
        v.setdefault("you", {})["seat"] = seat
        v["you"]["name"] = v["you"].get("name") or seat
        return self._extra(apply_session(v, seat), seat)

    # 與 engine.py 的語意一致（`state["log"]` 白名單／`_scrub_public`）：正本在 engine.py 的那個 tuple，
    # 這裡是鏡像——**用白名單，不要用黑名單**（引擎外還有 `artifact_revealed`／`scene_props` 這類
    # `public:true` 卻不在白名單的型別；黑名單版會比引擎多給）。改動請對照正本。
    VIEW_LOG_TYPES = {
        "seat_joined", "scene_set", "say", "action_declared", "roll",
        "damage", "heal", "status_set", "turn_started", "turn_ended",
        "turn_extended", "seat_timeout", "table_created",
        "move", "entrance_found", "entered_tower", "elevator", "mob_killed",
        "boss_defeated", "artifact_gained", "memory_change", "tower_exit",
        "white_room", "assimilated", "shop_buy", "search_loot", "kick",
        "scene_open", "scene_close", "declare", "declare_resolved", "declare_won",
        "declare_failed", "fragment_granted", "fragment_seen", "fragment_given",
        "wish_done", "settle", "gm_say", "gm_leak_blocked", "gm_roll_marker",
    }
    FRAGMENT_TYPES = ("fragment_granted", "fragment_seen", "fragment_given")
    TYPING_TTL = 15.0   # 同 docs/API.md：typing 只回最近 15 秒內的

    def _public_events(self, evs, seat):
        """公開投影：白名單（排除內部記帳）∩ public；碎片 fid 對非當事人 scrub（seat=None＝匿名）。"""
        out = []
        for e in evs:
            if e.get("type") not in self.VIEW_LOG_TYPES or e.get("public") is False:
                continue
            # 匿名（seat is None）＝非當事人 ⇒ 一律 scrub。
            # 別只寫 `seat not in (holder, to)`：碎片事件常有一邊是 None
            # （granted: holder 有／to 無；seen: holder 無／to 有）⇒ seat=None 會被那個 None 匹配到，反而漏 scrub。
            if (e.get("type") in self.FRAGMENT_TYPES and e.get("fid")
                    and (seat is None or seat not in (e.get("holder"), e.get("to")))):
                e = {k: v for k, v in e.items() if k != "fid"}
            out.append(e)
        return out

    def _extra(self, v, seat):
        """公開投影＋私訊（契約 2026-10-05）：
        view.log 只給公開事件；secret 走 your_secrets（回顯那則帶 peer，前端據此分對話串）。"""
        v["log"] = self._public_events(v.get("log") or [], seat)
        v["your_secrets"] = [
            {"id": f"s{e['seq']}", "from": e.get("frm"), "to": e.get("to"),
             "peer": e.get("peer") or "", "text": e["text"], "seq": e["seq"],
             "ts": e.get("ts")}
            for e in SESSION["events"]
            if e.get("type") == "secret" and e.get("to") == seat]
        # typing：誰正在「對我」打字（純記憶體、只回最近 TTL 內的；形狀同 docs/API.md {"seat":{"to","ts"}}）
        _now = time.time()
        v["typing"] = {k: {"to": t.get("to"), "ts": t.get("ts")}
                       for k, t in (SESSION.get("typing") or {}).items()
                       if t.get("to") == seat and _now - (t.get("ts") or 0) <= self.TYPING_TTL}
        return v


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8978
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    print(f"假桌 → http://127.0.0.1:{port}/   （fixtures 在 {FX}；改完 json 刷新即见）")
    srv.serve_forever()
