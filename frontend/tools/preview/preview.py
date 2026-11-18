#!/usr/bin/env python3
"""離線預覽載具（開發用；不進 web/、不上線、不需要家側部署）。

起一個本機靜態伺服器，服務 `web/index.html` 時**在 <head> 裡插一段 fetch shim**，
用 `fixtures/*.json` 回答頁面發出的 GraphQL。`web/index.html` 一個字都不改（讀進來、插完才送出）。

    python3 tools/preview/preview.py                 # → http://127.0.0.1:8990/preview/
    python3 tools/preview/preview.py --port 9000
    python3 tools/preview/preview.py --page /path/to/other-index.html   # 比對用：服務另一份 index.html（例如 `git show origin/main:web/index.html > x.html`）

為什麼要這支：頁面已是純 GraphQL，而 `mock/mock_server.py` 只有 REST（沒有 /graphql）。
fixtures 是 10-05 REST 時代的形狀（`state_*.json`＝舊 `/api/state`），而頁面現在讀的是
`table{ view(session:,seat:){ raw } }`——`raw` 就是舊 `/api/state` 的投影，所以這裡只要把 fixture 包進 GraphQL 回應；
fixtures 沒有的欄位（`capabilities.mode`、`entry` 三個籃子、`session.id`…）由本檔合成，頁面本來就容忍缺欄位。

只讀：所有 mutation 一律回「預覽載具唯讀」的原因（頁面照常顯示被拒的樣子），不會改任何狀態。
翻譯服務（`/translate_batch`）在頁面端被 shim 擋成「立刻失敗」（頁面的失敗路徑＝照原文），預設另外把翻譯開關關掉，
免得 pending 標記在截圖時閃。

頁面網址參數（都是 `pv_` 開頭，頁面自己的程式不認得它們，只有 shim 讀）：
  pv_view=p1|agent|spectator   用哪份 fixture 當 view（預設 p1；`/<桌>/watch/` 一律 spectator）
  pv_scene=open|closed         open（預設）＝把 fixture 的「已收景」快照改成「開著的一景、額度還沒用」；closed＝fixture 原樣
  pv_scenetext=long            景文換成 log.json 裡最長的一則 scene_open（看多行景文排版）
  pv_mode=scene|turn           capabilities.mode（預設 scene）
  pv_assets=0                 不合成素材欄位且不代理素材（回退像素比對）
  pv_res=0                     不要合成「魔力／等級」那兩欄（預設會合成——fixtures 是 10-05 的舊快照，沒有它們；
                               桌端其實會給：`projection.py` 的 `v["you"]["resources"]`／`["level"]`／`["exp"]`／`["next_need"]`，
                               前端也是「有才畫」⇒ 不合成就看不到那兩條棒）
  pv_reset=1                   可重現狀態（拍 before／after 必開）：在頁面腳本之前清掉會影響版面的偏好鍵
                               （trpg_lang／theme／translate／mapopen／mappos_v2／howto_seen／momo_seen／name／seat*、
                               sessionStorage 的翻譯快取），再依下面三個 pv_* 顯式寫回；**不碰 trpg_token／trpg_gmtoken**。
                               不給＝照舊沿用 profile 殘留（互動看頁面時要它記得地圖開合，所以不是預設）
    pv_mapopen=open|closed       （配 pv_reset）地圖浮窗，預設 closed
    pv_howto=new|seen            （配 pv_reset）「現在該做」新手引導，預設 new（沒看過＝第一次來的樣子）；seen＝看過了（卡片收成一行）
    pv_momo=<數字>               （配 pv_reset）trpg_momo_seen，預設不設（0）
  pv_lang=zh-Hant|zh-Hans|ja   在頁面腳本之前寫進 localStorage.trpg_lang（沒給＝不動）
  pv_lastseen=<seq>|old        （記在 `trpg_lastseen`）「你不在時」那條摘要用：`old`＝當成全部未讀（才看得到那條）；
                               沒給＝清掉（＝沒有未讀，摘要不顯示）
  pv_auth=0                    不寫假 token（預設會寫一把，頁面才不會一進來就彈登入窗；0＝看「沒登入」的樣子）
  pv_noseat=1                  登入了但這一局還沒有席位（`enter` 回空 ⇒ 頁面會打開「進桌選人」那張卡）
  pv_tr=1                      不關翻譯開關（翻譯請求仍會被 shim 擋成失敗）
  pv_merge=<JSON>              深度合併進 view.raw（造「暫停／結束／輪到別人」之類的狀態）
頁面本身的 `?theme=dark` 照舊可用。
"""
import argparse
import copy
import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
FX = os.path.join(ROOT, "fixtures")
PAGE = os.path.join(ROOT, "web", "index.html")
PAGE_OVERRIDE = None
SESSION_ID = "preview-session"
READONLY = "預覽載具唯讀（fixtures 只讀，不接受寫入）"
# 頁面 `_ROUTE` 那七條：api() 只看回傳物件裡的 error ⇒ 回 {error}；其餘 mutation 走 GraphQL errors
ROUTE_FIELDS = {"join", "act", "say", "tell", "typing", "gmRun", "pickChar"}

ASSET_HUB = "http://127.0.0.1:8971"
ASSET_CACHE = {}
ASSET_MISSING = set()
ASSET_LOCK = threading.Lock()

def hub_asset(path):
    with ASSET_LOCK:
        if path in ASSET_MISSING: raise OSError("asset unavailable")
        if path not in ASSET_CACHE:
            try:
                with urlopen(Request(ASSET_HUB + path), timeout=5) as response:
                    ASSET_CACHE[path] = (response.read(), dict(response.headers))
            except OSError:
                ASSET_MISSING.add(path)
                raise
        return ASSET_CACHE[path]

def project_assets(raw):
    try:
        index = json.loads(hub_asset("/assets/hyakki_tower/index.json")[0])
        if index.get("parts"):          # schema 2：內容按類拆在 index/<類>.json ⇒ 併回一份
            index.setdefault("files", {}); index.setdefault("aliases", {})
            for part in index["parts"].values():
                sub = json.loads(hub_asset("/assets/hyakki_tower/" + part)[0])
                index["files"].update(sub.get("files") or {})
                index["aliases"].update(sub.get("aliases") or {})
    except (OSError, ValueError):
        return
    base = "/assets/hyakki_tower/"
    def asset(alias):
        entry = index.get("files", {}).get(index.get("aliases", {}).get(alias), {})
        return base + entry["path"] if entry.get("path") else None
    raw.setdefault("table", {}).update(module=index["module"], assets={"module":index["module"],"base":base,"index":base+"index.json"})
    # These ids identify the characters in the frozen preview fixtures only.
    chars = {"p1":"char_a", "p2":"char_b", "p3":"homura", "p4":"isuka", "npc1":"tsurugi"}
    name_map = index.get("source", {}).get("name_map", {})
    def char_asset(kind, char):
        mapped = name_map.get(char, char).lower()
        key = kind+":"+mapped
        if key not in index["aliases"]: key=kind+":"+char
        return asset(key)
    for person in raw.get("seats", []) + ([raw["you"]] if isinstance(raw.get("you"),dict) else []):
        char = chars.get(person.get("seat"))
        if char:
            person["portrait"] = char_asset("portrait", char)
            person["avatar"] = char_asset("avatar", char)
        person["statusIcons"] = {condition:asset("icon:condition:"+condition) for condition in person.get("status", [])}
    you = raw.get("you") or {}
    # 服裝版本（與 projection._seat_art 同形）：portrait:<角色>:<服裝> ⇒ you.portraits（第一筆＝預設）
    _c = chars.get(you.get("seat"))
    if _c and you.get("portrait"):
        alts = [{"id": a.rsplit(":", 1)[1], "url": asset(a)} for a in sorted(index["aliases"])
                if a.startswith("portrait:" + _c + ":") and asset(a)]
        if alts:
            you["portraits"] = [{"id": "default", "url": you["portrait"]}] + alts
    for kind in ["items", "skills", "artifacts"]:
        for item in you.get(kind, []):
            alias = "icon:"+{"items":"item", "skills":"skill", "artifacts":"artifact:name"}[kind]+":"+str(item.get("id") or item.get("item") or item.get("name", ""))
            item["icon"] = asset(alias)
    here = (raw.get("board") or {}).get("you_are") or {}
    here["art"] = asset("bg:"+str(here.get("id", "")))
    if isinstance(here.get("monster"),dict): here["monster"]["icon"] = asset("icon:monster:"+str(here["monster"].get("id", "")))
    for item in here.get("shop_items", []): item["icon"] = asset("icon:item:"+str(item.get("item") or item.get("id", "")))

SHIM = r"""<script>/* ── preview shim（tools/preview/preview.py 插入；不是頁面的正式碼）── */
(function(){
  var Q = new URLSearchParams(location.search), OPTS = {};
  Q.forEach(function(v, k){ if(k.indexOf("pv_") === 0) OPTS[k.slice(3)] = v; });
  try{
    if(OPTS.reset === "1"){   /* 可重現狀態：先清掉所有會影響版面的偏好鍵，再用 pv_* 顯式指定。⚠ 不碰 trpg_token／trpg_gmtoken（憑證載體） */
      ["trpg_lang", "trpg_theme", "trpg_translate", "trpg_mapopen", "trpg_mappos_v2", "trpg_howto_seen", "trpg_momo_seen", "trpg_lastseen", "trpg_name", "trpg_seat"]
        .forEach(function(k){ localStorage.removeItem(k); });
      Object.keys(localStorage).forEach(function(k){ if(k.indexOf("trpg_seat:") === 0) localStorage.removeItem(k); });
      sessionStorage.removeItem("trpg_tr_cache");
      localStorage.setItem("trpg_mapopen", OPTS.mapopen === "open" ? "open" : "closed");   /* 地圖浮窗：預設收 */
      if(OPTS.howto === "seen") localStorage.setItem("trpg_howto_seen", "1");              /* 「現在該做」新手引導：預設沒看過（＝全新 profile 的樣子） */
      if(OPTS.momo) localStorage.setItem("trpg_momo_seen", OPTS.momo);                     /* 私訊已讀到第幾則：預設 0 */
      if(OPTS.lastseen) localStorage.setItem("trpg_lastseen", OPTS.lastseen === "old" ? "0" : OPTS.lastseen);  /* 「你不在時」摘要：old＝全部當未讀（才看得到那條） */
    }
    if(OPTS.lang) localStorage.setItem("trpg_lang", OPTS.lang);
    if(OPTS.auth !== "0" && !localStorage.getItem("trpg_token")) localStorage.setItem("trpg_token", "preview-token");
    if(OPTS.tr !== "1" && localStorage.getItem("trpg_translate") === null) localStorage.setItem("trpg_translate", "off");
  }catch(e){}
  var real = window.fetch.bind(window);
  window.__PV = {opts: OPTS, unhandled: []};
  window.fetch = function(url, init){
    var u = String(url && url.url || url), path = u.split("?")[0];
    if(/\/translate_batch$/.test(path)) return Promise.reject(new TypeError("Failed to fetch (preview: translate disabled)"));
    if(/\/graphql$/.test(path)){
      var h = Object.assign({}, (init && init.headers) || {}, {"X-Preview-Opts": JSON.stringify(OPTS), "X-Preview-Path": location.pathname});
      return real("/__preview/graphql", Object.assign({}, init, {headers: h}));
    }
    return real(url, init);
  };
})();
</script>
"""


def load(name):
    with open(os.path.join(FX, name), "r", encoding="utf-8") as fh:
        return json.load(fh)


def deep_merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            deep_merge(dst[k], v)
        else:
            dst[k] = v
    return dst


def build_raw(opts, watch):
    which = "spectator" if watch else opts.get("view", "p1")
    raw = copy.deepcopy(load("state_%s.json" % which))
    if opts.get("scene", "open") == "open" and raw.get("stage") is not None:
        raw["stage"]["open"] = True
        raw["throttle"] = {"used": {}, "limits": (raw.get("throttle") or {}).get("limits") or {"act": 2, "say": 3}}
        if "you_acted" in raw:
            raw["you_acted"] = False
        if "scene_waiting" in raw:
            mine = (raw.get("you") or {}).get("seat")
            raw["scene_waiting"] = [s["seat"] for s in raw.get("seats", [])
                                    if s.get("kind") in ("human", "agent") and s.get("seat") != "p4"] or raw["scene_waiting"]
            if mine and mine not in raw["scene_waiting"]:
                raw["scene_waiting"].insert(0, mine)
    if opts.get("scenetext") == "long" and raw.get("stage") is not None:
        evs = [e for e in load("log.json").get("events", []) if e.get("type") == "scene_open"]
        if evs:
            raw["stage"]["text"] = max(evs, key=lambda e: len(e.get("text", ""))).get("text", "")
    # fixtures 是 10-05 的舊快照：`you` 沒有 `resources`（魔力）與 `level`／`exp`（等級）——
    # 桌端會給（`projection.py`：`v["you"]["resources"]`／`["level"]`／`["exp"]`／`["next_need"]`），
    # 前端也是「有才畫」⇒ 舊快照上看不到那兩條棒。合成一組合理值讓預覽看得到（`pv_res=0` 可關）。
    if opts.get("res", "1") != "0":
        you = raw.get("you")
        if isinstance(you, dict):
            if not isinstance(you.get("resources"), dict):
                you["resources"] = {}
            you["resources"].setdefault("mana", 7)
            you.setdefault("max_mana", 12)
            you.setdefault("max_memory", 30)
            you.setdefault("level", 3)
            you.setdefault("exp", 40)
            you.setdefault("next_need", 100)
    if opts.get("merge"):
        deep_merge(raw, json.loads(opts["merge"]))
    if opts.get("assets", "1") != "0":
        project_assets(raw)
    return raw


def build_entry(raw):
    """fixtures 沒有 `entry`：照席位表合成三個籃子（我坐的席＝importable.mine、其他在座＝takenSeats）。"""
    you = (raw.get("you") or {}).get("seat")
    seats = [s for s in raw.get("seats", []) if s.get("kind") in ("human", "agent")]
    return {
        "session": SESSION_ID, "account": "preview", "hasInstance": True, "spectateOk": True, "reason": "",
        "importable": [{"seat": s["seat"], "charId": s["seat"], "name": s["name"], "className": "", "inRoster": True,
                        "mine": s["seat"] == you} for s in seats if s["seat"] == you],
        "creatable": [],
        "takenSeats": [{"seat": s["seat"], "charId": s["seat"], "name": s["name"]} for s in seats if s["seat"] != you],
    }


def answer(query, variables, opts, watch):
    q = " ".join(query.split())
    m = re.match(r"mutation\b[^{]*\{\s*(\w+)", q)
    if m:
        field = m.group(1)
        if field == "enter":      # 登入後 authRefresh 問「我是誰」：照 fixture 的 you 合成一份帳號資料（不寫任何東西）
            you = (build_raw(opts, watch).get("you") or {})
            if opts.get("noseat") == "1" or not you:
                return {"data": {field: {"account": "preview", "display": "preview (human)", "kind": "human"}}}
            return {"data": {field: {"account": "preview", "display": "preview (human)", "kind": "human",
                                     "seat": you.get("seat"), "char": you.get("name"), "instance": {"level": 1},
                                     "wallet": {"star_coins": you.get("star_coins", 0)}}}}
        if field == "typing":
            return {"data": {field: {}}}
        if field in ROUTE_FIELDS:
            return {"data": {field: {"error": READONLY}}}
        return {"errors": [{"message": READONLY}]}
    raw = build_raw(opts, watch)
    t = {"id": raw.get("table", {}).get("table_id", "preview")}
    if "session{" in q.replace(" ", "") or "session {" in q:
        t["session"] = {"id": SESSION_ID}
    if "capabilities" in q:
        t["capabilities"] = {"mode": opts.get("mode", "scene")}
    if "view(" in q:
        t["view"] = {"raw": raw}
    if re.search(r"\bmap\(", q):
        t["map"] = load("map.json")
    if "entry{" in q.replace(" ", "") or "entry {" in q:
        t["entry"] = build_entry(raw)
    if len(t) == 1:
        sys.stderr.write("[preview] 沒有對應的回答（回空）：%s\n" % q[:160])
    return {"data": {"table": t}}


class H(BaseHTTPRequestHandler):
    server_version = "TrpgPreview/0.1"

    def log_message(self, fmt, *a):
        if "/__preview/graphql" not in (fmt % a):   # 輪詢每 1.5 秒一次，太吵
            sys.stderr.write("[preview] " + (fmt % a) + "\n")

    def _send(self, code, body, ctype):
        raw = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        path = urlparse(self.path).path
        if path.startswith("/assets/"):
            if "pv_assets=0" in (self.headers.get("Cookie") or ""):
                return self._send(404, '{"error":"preview assets disabled"}', "application/json")
            if ".." in path.split("/"):
                return self._send(404, "not found", "text/plain")
            try:
                body, headers = hub_asset(path)
            except (OSError, HTTPError):
                return self._send(404, '{"error":"asset unavailable"}', "application/json")
            etag = headers.get("ETag") or headers.get("Etag")
            cached = etag and self.headers.get("If-None-Match") == etag
            self.send_response(304 if cached else 200)
            self.send_header("Content-Type", headers.get("Content-Type", "application/octet-stream"))
            self.send_header("Cache-Control", headers.get("Cache-Control", "public, max-age=86400"))
            if etag: self.send_header("ETag", etag)
            if not cached: self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not cached: self.wfile.write(body)
            return
        if path == "/":
            self.send_response(302)
            self.send_header("Location", "/preview/")
            self.end_headers()
            return
        parts = [p for p in path.split("/") if p]
        if not parts or parts[0].startswith("__"):
            return self._send(404, "not found", "text/plain; charset=utf-8")
        with open(PAGE_OVERRIDE or PAGE, "r", encoding="utf-8") as fh:
            html = fh.read()
        pre = SHIM
        if len(parts) > 1 and parts[1] == "watch":
            pre = "<script>window.__WATCH__=true;window.TRPG_BASE=%s;</script>\n" % json.dumps("/" + "/".join(parts[:2])) + SHIM
        html = html.replace("<head>", "<head>\n" + pre, 1)
        opts = parse_qs(urlparse(self.path).query)
        self.send_response(200)
        body=html.encode("utf-8")
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Set-Cookie", "pv_assets="+("0" if opts.get("pv_assets")==["0"] else "1")+"; Path=/; SameSite=Lax")
        self.end_headers(); self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
            opts = json.loads(self.headers.get("X-Preview-Opts") or "{}")
        except ValueError:
            return self._send(400, '{"errors":[{"message":"bad json"}]}', "application/json")
        watch = "/watch" in (self.headers.get("X-Preview-Path") or "")
        out = answer(body.get("query", ""), body.get("variables") or {}, opts, watch)
        self._send(200, json.dumps(out, ensure_ascii=False), "application/json; charset=utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8990)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--page", help="服務這份 index.html，而不是 web/index.html（before／after 比對用）")
    a = ap.parse_args()
    global PAGE_OVERRIDE
    PAGE_OVERRIDE = a.page
    srv = ThreadingHTTPServer((a.host, a.port), H)
    print("preview: http://%s:%d/preview/   （Ctrl+C 結束；選項見 --help 的 docstring）" % (a.host, a.port), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
