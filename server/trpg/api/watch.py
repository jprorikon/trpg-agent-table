#!/usr/bin/env python3
"""TRPG **觀戰桌** —— 一張唯讀的桌（獨立埠；`PLAN_spectate_table.md` C-a）。

上游桌（`server.py`；單一入口時掛在 `/<桌>/`，內部 loopback 埠）是唯一真相：席位、回合、收景都在那邊。
這一台（觀戰桌；單一入口時掛在 `/<桌>/watch/`）**只讀**，供應：

  * `GET /`          唯讀前端（同一份 `web/index.html`，注入唯讀旗標）
  * `GET /api/state` 公開投影（**跟上游桌要**，不自己折事件流 ⇒ 零漂移、零重複實作）
  * `GET /api/log`   公開投影那份（同上）
  * `GET /api/map`   靜態地圖
  * `GET /health`    自己的健康 ＋ 上游桌可達性

其餘一律 **404**：這台**沒有** `act／say／tell／join／enter／login／pick_char／gm／hook/inbox／mcp`。
觀戰者在這張桌上**沒有席位** ⇒ 不可能進 `order`、不可能被算「還沒出手」、不可能擋收景。

它**不讀事件流、不讀 accounts.json、不寫任何檔**（除了自己的 log）。

用法：
    python3 watch_server.py --home campaigns/hyakki_tower --port 8990
    python3 watch_server.py --home <桌> --port 8990 --upstream http://127.0.0.1:8971

旋鈕（住上游桌的 `config.json`，**全部可省**）：
    "watch": {"enabled": true, "port": 8990, "upstream": "http://127.0.0.1:8971",
              "read_token": "…"}      // read_token 只在上游桌 require_account=true 時需要；
                                      // 上游桌對這把**只放行公開投影**（不是 GM 全視圖）
"""

from __future__ import annotations

import argparse
import atexit
import json
import re
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from trpg.paths import ROOT as HERE  # repo 根（資料／vendor／docs 都在這裡）

# 觀戰桌**只放行這些**（前綴比對）：其餘路徑一律 404（唯讀面的白名單）
# ⚠ 2026-10-07：前台的觀戰頁（`/<桌>/watch/…`，in-process）已改走 GraphQL（只放行 query）。
#   這一支是**獨立行程的觀戰前門**（開發／備用）⇒ 保留舊只讀白名單（它的測試驗的是那份契約），
#   同時支援 `POST /graphql`（只放行查詢）給新版頁面用。
ALLOW = ("/api/state", "/api/log", "/api/map", "/health")
# 只有這幾個查詢參數會轉給上游桌（**不轉 seat／token／gm_token**：觀戰不該能指定別人的視角）
PASS_Q = ("detail",)


class WatchState:
    upstream = "http://127.0.0.1:8971"
    read_token = ""
    home = HERE
    public_prefix = ""                     # 單一入口時＝「<桌的前綴>/watch」（送頁面時注入給前端）
    cache_ttl = 0.75                       # 微快取：多人同時輪詢時保護上游桌（不影響正確性：TTL < 前端輪詢間隔）
    _cache: dict = {}
    _lock = threading.Lock()


def _fetch(path: str) -> tuple[int, bytes, str]:
    """跟上游桌要一個唯讀路徑。回 (status, body, content_type)。"""
    url = WatchState.upstream.rstrip("/") + path
    req = urllib.request.Request(url, method="GET")
    if WatchState.read_token:
        req.add_header("Authorization", "Bearer " + WatchState.read_token)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read(), resp.headers.get("Content-Type", "application/json")
    except urllib.error.HTTPError as exc:                      # 上游桌回了錯誤（例：403 憑證不對）→ 原樣轉
        return exc.code, exc.read(), exc.headers.get("Content-Type", "application/json")
    except Exception as exc:                                   # noqa: BLE001  連不上
        body = json.dumps({"error": f"上游桌連不上：{type(exc).__name__}: {exc}",
                           "upstream": WatchState.upstream}, ensure_ascii=False).encode()
        return 503, body, "application/json; charset=utf-8"


def _proxy_post(path: str, body: bytes) -> tuple[int, bytes, str]:
    """把**已過濾成唯讀**的 GraphQL 轉給上游桌（不進快取——POST 一律即時）。"""
    url = WatchState.upstream.rstrip("/") + path
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    if WatchState.read_token:
        req.add_header("Authorization", "Bearer " + WatchState.read_token)
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status, resp.read(), resp.headers.get("Content-Type", "application/json")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers.get("Content-Type", "application/json")
    except Exception as exc:                                   # noqa: BLE001
        import json as _j
        return 503, _j.dumps({"error": f"上游桌連不上：{type(exc).__name__}: {exc}"}).encode(), "application/json"


def _cached(path: str) -> tuple[int, bytes, str]:
    now = time.monotonic()
    with WatchState._lock:
        hit = WatchState._cache.get(path)
        if hit and now - hit[0] < WatchState.cache_ttl:
            return hit[1], hit[2], hit[3]
    out = _fetch(path)
    with WatchState._lock:
        WatchState._cache[path] = (now, out[0], out[1], out[2])
    return out


def readonly_page(prefix: Optional[str] = None, tid: str = "", gql: str = "",
                  run: str = "") -> bytes:
    """同一份前端 + 唯讀旗標 + 子路徑前綴（不複製第二份頁面）。

    注入三件事：`<body data-watch="1">`（CSS 藏掉互動區）＋ `window.__WATCH__=1`（JS 早退）
    ＋ `window.TRPG_BASE`（單一入口模式時它在 `/<桌>/watch`；沒有＝直接連觀戰埠）。

    `prefix`：**單一入口（hub）用**——它要把頁面掛在 `/<桌>/watch` 下，而那是**每個請求**才知道的事，
    所以由呼叫端（`web_api._watch_page`）帶進來；省略＝這支行程自己的 `WatchState.public_prefix`。
    """
    p = os.path.join(HERE, "web", "index.html")
    html = open(p, "r", encoding="utf-8").read()
    # ⚠ 不能只檢查 "data-watch"——**頁面自己的 CSS 就有這個字串**（`body[data-watch] …`），
    #   用寬鬆的檢查會讓注入被跳過（踩過：觀戰頁的互動區全都還在）。檢查完整屬性。
    if 'data-watch="1"' not in html:
        html = re.sub(r"<body", '<body data-watch="1"', html, count=1)
    flag = "<script>window.__WATCH__=1;</script>"
    # ⚠ 同理：檢查要帶等號（前端自己就有 `window.TRPG_BASE` 這個詞）
    if "window.TRPG_BASE=" not in html:
        _pre = WatchState.public_prefix if prefix is None else prefix
        flag += f"<script>window.TRPG_BASE={json.dumps(_pre)};</script>"
        # ★ 前端改走 GraphQL ⇒ 觀戰頁也要知道桌 id（唯讀；寫入面在伺服器端本來就全擋）
        _tid = str(tid or "")              # ★ 呼叫端（web_api._watch_page）給的**路由權威值**優先
        # ⚠ 2026-10-19 修：原本寫成 `try: if _tid: raise StopIteration … except: _tid = ""`，
        #   那個 raise 被自己的 `except Exception` 接住 ⇒ **呼叫端給的 tid 被清成 ""**。
        #   舊路徑靠 pathname 猜才沒炸；換成 `/watchtables/<run>` 後第一段是 "watchtables" ⇒ 全錯。
        if not _tid:
            try:
                # ⚠ 桌 id 只認**非 host 的末段**：`http://127.0.0.1:8971` 的末段是「127.0.0.1:8971」✗
                #   （2026-10-07 觀戰頁什麼都看不到就是吃到這個）；上游沒有子路徑時改問 config.json。
                if WatchState.upstream:
                    _tail = WatchState.upstream.rstrip("/").split("/")[-1]
                    _tid = _tail if (":" not in _tail and _tail not in ("http", "https")) else ""
                if not _tid and WatchState.home:
                    import json as _jj                                        # noqa: PLC0415
                    _tid = str((_jj.load(open(os.path.join(WatchState.home, "config.json"),
                                              encoding="utf-8"))).get("table_id") or "")
            except Exception:                                                 # noqa: BLE001
                _tid = ""
        # ⚠ **桌自己的** GraphQL 端點：觀戰子路徑底下**沒有** `/graphql`——但那是**前端整支搬 GraphQL
        #   之前**的口徑（那時觀戰頁走 `/api/state` 等唯讀白名單）。2026-10-08 之後：
        #   觀戰頁的「讀」也走 GraphQL，所以它要打的是**唯讀前門自己的** `/<桌>/watch/graphql`
        #   （伺服器端已放行：watch ⇒ 只准 Query）。單一入口（hub）＝用**呼叫端給的 prefix**；
        #   沒給（這支行程自己起）＝退回 `WatchState.upstream` 那一條。
        _gql = ""
        try:
            if prefix is not None:
                _gql = str(prefix).rstrip("/") + "/graphql"
            elif WatchState.upstream:
                _gql = WatchState.upstream.rstrip("/") + "/graphql"
        except Exception:                                                     # noqa: BLE001
            _gql = ""
        flag += f"<script>window.TRPG_TID={json.dumps(_tid)};</script>"
        # 2026-10-19（URL 正規化）：觀戰頁也要知道「這一局」——前端不准從 pathname 猜（第一段是 watchtables）
        if run and "window.TRPG_RUN=" not in html:
            flag += (f"<script>window.TRPG_RUN={json.dumps(str(run))};"
                     f"window.TRPG_MODE=\"watch\";</script>")
        flag += f"<script>window.TRPG_GQL={json.dumps(_gql)};</script>"
    html = html.replace("</head>", flag + "</head>", 1) if "</head>" in html else html + flag
    return html.encode("utf-8")


def _readonly_page() -> bytes:                     # 舊名（這支行程自己用；＝帶 WatchState 的前綴）
    return readonly_page()


class Handler(BaseHTTPRequestHandler):
    server_version = "trpg-watch/1.0"
    protocol_version = "HTTP/1.1"

    # -- 工具 ---------------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:  # noqa: BLE001
            pass

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode())

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        if os.environ.get("TRPG_HTTP_LOG", "1") == "1":
            sys.stderr.write("%s %s - %s\n" % (time.strftime("%m-%d %H:%M:%S"),
                                               self.address_string(), fmt % args))

    # -- 路由（**只讀**）-----------------------------------------------------
    def _watch_on(self) -> bool:
        """這一局現在開不開觀戰（局級 `watch.json`；舊局退回 config 預設）。"""
        try:
            from trpg.service import run_watch as _rw                                      # noqa: PLC0415
            return _rw.enabled(WatchState.home, "", default=True)
        except Exception:                                                              # noqa: BLE001
            return True

    def do_GET(self) -> None:  # noqa: N802
        if not self._watch_on():
            self._send(404, b'{"error":"watch disabled for this run"}', "application/json")
            return
        path, _, qs = self.path.partition("?")
        if path in ("/", "/index.html"):
            self._send(200, _readonly_page(), "text/html; charset=utf-8")
            return
        if path not in ALLOW:
            self._json(404, {"error": "觀戰桌唯讀：沒有這條路（要看別的請回上游桌）", "path": path})
            return
        keep = []
        for kv in qs.split("&"):
            if "=" in kv and kv.split("=", 1)[0] in PASS_Q:
                keep.append(kv)
        target = path + (("?" + "&".join(keep)) if keep else "")
        code, body, ctype = _cached(target)
        self._send(code, body, ctype)

    def _refuse(self) -> None:
        self._json(404, {"error": "觀戰桌唯讀：觀戰者沒有席位，不能行動／發言／入座／改桌",
                         "path": self.path.split("?")[0]})

    def do_POST(self) -> None:  # noqa: N802
        """★ 2026-10-07：前端改走 GraphQL ⇒ 觀戰頁要能 `POST /graphql`（**只放行唯讀**）。

        `ALLOW`（GET 白名單）擋不住了 ⇒ 這一格自己當**前門過濾**：body 是 JSON 且那條 query
        **只讀**（去掉註解／字串後以 `query`／`{` 開頭、且不含 `mutation` 這個字）才轉給上游；
        其餘（mutation／訂閱）一律 404——與「觀戰桌沒有寫入面」同一條契約。
        """
        if not self._watch_on():
            self._send(404, b'{"error":"watch disabled for this run"}', "application/json")
            return
        path = self.path.split("?")[0]
        if path != "/graphql":
            self._refuse()
            return
        n = int(self.headers.get("Content-Length") or 0)
        try:
            _b = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            _q = str(_b.get("query") or "")
        except Exception:
            self._json(400, {"error": "body 不是 JSON object"})
            return
        _bare = re.sub(r"#[^\n]*", " ", _q)                      # 去註解
        _bare = re.sub(r'"(?:[^"\\]|\\.)*"', '""', _bare)      # 去字串字面
        _low = _bare.lower()
        if (not _bare.strip().startswith(("query", "{"))) or ("mutation" in _low) or ("subscription" in _low):
            self._json(404, {"error": "觀戰桌沒有寫入面（唯讀前門）：只放行 query"})
            return
        code, body, ctype = _proxy_post(path, json.dumps(_b).encode())
        self._send(code, body, ctype)

    def do_PUT(self) -> None:  # noqa: N802
        self._refuse()

    def do_DELETE(self) -> None:  # noqa: N802
        self._refuse()

    def do_PATCH(self) -> None:  # noqa: N802
        self._refuse()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", default=HERE, help="桌的目錄（跟上游桌同一個 --home）")
    ap.add_argument("--config", default=None, help="預設 <home>/config.json")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--bind", default=None)
    ap.add_argument("--upstream", default=None, help="上游桌位址；預設讀 config 的 port")
    ap.add_argument("--read-token", default=None, help="上游桌 require_account 時的觀戰讀取憑證")
    ap.add_argument("--pidfile", default=None)
    ap.add_argument("--public-prefix", default=None,
                    help="單一入口時這個觀戰桌的對外前綴（預設＝該桌 config 的 public_prefix + /watch）")
    args = ap.parse_args()

    if args.pidfile:
        with open(args.pidfile, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
        atexit.register(lambda p=args.pidfile: os.path.exists(p) and os.remove(p))

    cfg_path = args.config or os.path.join(args.home, "config.json")
    cfg = {}
    if os.path.exists(cfg_path):
        try:
            cfg = json.load(open(cfg_path, encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"[watch] 讀不到 config（{cfg_path}）：{exc}", file=sys.stderr)
    watch = cfg.get("watch") or {}
    # ★ 2026-10-20：觀戰開關是**局級**；獨立觀戰桌啟動時看現役局的 `watch.json`，
    #   舊局沒有檔才退回 config 的 `watch.enabled`（沿用舊行為：沒寫＝開）。
    _watch_on = True
    try:
        from trpg.service import run_watch as _rw                                      # noqa: PLC0415
        _watch_on = _rw.enabled(os.path.abspath(args.home), "",
                                default=bool(watch.get("enabled", True)))
    except Exception:                                                                  # noqa: BLE001
        _watch_on = bool(watch.get("enabled", True))
    if not _watch_on and args.port is None:
        print("[watch] 這一局關了觀戰（watch.json／config.watch.enabled=false）"
              " ⇒ 不開觀戰桌（要開就改開關或帶 --port）", file=sys.stderr)
        return

    WatchState.home = os.path.abspath(args.home)
    WatchState.upstream = (args.upstream or watch.get("upstream")
                           or f"http://127.0.0.1:{int(cfg.get('port') or 8971)}")
    WatchState.read_token = str(args.read_token or watch.get("read_token") or "")
    # 單一入口（子路徑）：觀戰桌掛在 `/<桌>/watch` ⇒ 頁面要注入這個前綴給前端用
    _pp = args.public_prefix if args.public_prefix is not None \
        else (str(cfg.get("public_prefix") or "").rstrip("/") + "/watch" if cfg.get("public_prefix") else "")
    WatchState.public_prefix = str(_pp).strip().rstrip("/")
    port = int(args.port or watch.get("port") or 8990)
    bind = args.bind or watch.get("bind") or "0.0.0.0"

    httpd = ThreadingHTTPServer((bind, port), Handler)
    print(f"[watch] 觀戰桌已起： http://{bind}:{port}/   ← 唯讀；上游＝{WatchState.upstream}"
          f"（read_token：{'set' if WatchState.read_token else 'none'}）", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
