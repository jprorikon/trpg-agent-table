#!/usr/bin/env python3
"""**觀戰桌的自測**（唯讀前門；設計＝`docs/PLAN_spectate_table.md` C-a）。

    python3 test_watch.py

起**兩個臨時進程**（上游桌 ＋ 觀戰桌，都跑 `/tmp` 的副本、埠挑空的），驗七條：

  V1 公開投影拿得到，**私密看不到**（塞 `secret` ＋私訊 ⇒ 觀戰面裡都沒有；公開的看得到）
  V2 觀戰桌**不能寫**：POST `act／say／join／enter／gm` ⇒ 404，且**上游桌事件數不變**
  V3 **不影響上游桌**：觀戰者連讀 N 次 ⇒ 上游桌事件數／席位數完全不變
  V4 **跟得上**：上游桌跑一手 ⇒ 觀戰桌下一次讀就看到
  V5 `require_account=true` 的桌：帶 `read_token` ⇒ 200 公開投影（**不含 `you`**）；不帶 ⇒ 403
  V6 唯讀白名單：`/api/map`、`/health` 通；`/api/me`、`/mcp`、`/hook/inbox` ⇒ 404
  V7 legacy（沒有 `accounts.json`）一樣可用（觀戰桌不碰 registry）

⚠ 全程**不碰現役桌**：副本在 `/tmp`，埠是挑空的。
"""
from __future__ import annotations

import json
import hashlib
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo 根
sys.path.insert(0, HERE)
os.environ.setdefault("TRPG_LEGACY_REST", "1")   # 直接跑這支也要能打 `/api/*`（生產已退役 ⇒ 只有測試放行）
from trpg.core import layout as layout                                                          # noqa: E402
from trpg.rules import packs as packs                                                           # noqa: E402
PY = sys.executable or "python3"

OK, FAIL = [], []
PROCS: list = []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def get(url, token="", timeout=8):
    """回 (status, body_text)。任何 HTTP 錯誤都回它的碼，不是拋。"""
    req = urllib.request.Request(url, headers=({"Authorization": "Bearer " + token} if token else {}))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:                                              # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}"


def gq(base, query, variables=None, timeout=8):
    """觀戰面的讀：**POST /graphql**（舊 `/api/state` 2026-10-07 退役；觀戰前門只放行 query）。

    回 `(status, 回應文字的 dict)`；被前門擋下（mutation／非查詢）會是 404。
    """
    data = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(base.rstrip("/") + "/graphql", data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8", "replace") or "{}")
        except Exception:                                                     # noqa: BLE001
            return exc.code, {}
    except Exception as exc:                                                  # noqa: BLE001
        return 0, {"error": f"{type(exc).__name__}: {exc}"}


def watch_proj(base, tid, timeout=8):
    """觀戰面的**公開投影**（先拿 session，再讀 `view.raw`）。"""
    _c, _d = gq(base, "query($t: ID!){ table(id:$t){ session { id } } }", {"t": tid}, timeout)
    _s = (((_d.get("data") or {}).get("table") or {}).get("session") or {}).get("id") or ""
    return gq(base, "query($t: ID!, $s: ID!){ table(id:$t){ view(session:$s) { raw } } }",
              {"t": tid, "s": _s}, timeout)


def post(url, body, timeout=8, raw=False):
    data = body if raw else json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:                                              # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}"


def jload(s):
    try:
        return json.loads(s)
    except Exception:                                                     # noqa: BLE001
        return {}


def start(args, logpath):
    fh = open(logpath, "w")
    p = subprocess.Popen(args, cwd=HERE, stdout=fh, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True)
    PROCS.append(p)
    return p


def wait_health(url, secs=20):
    for _ in range(int(secs * 5)):
        if get(url + "/health", timeout=2)[0] == 200:
            return True
        time.sleep(0.2)
    return False


def mk_table(tmp: str, src: str, name: str = "") -> str:
    """把一張現役桌複製成臨時副本（不碰原件；**也不碰共用的 account registry**）。

    ⚠ 2026-10-07 第二階段：registry 可能住在**共用檔**（config 的 `auth.accounts_file`）⇒
    直接複製 config 會讓副本讀寫到**真的**那份（測試互相污染）。所以副本一律改成自帶一份
    **假的 registry**（帳號層「有開」但沒有座位）——要驗 legacy（沒有 registry）的測試自己再把
    `accounts_file` 拿掉。
    """
    home = os.path.join(tmp, name or os.path.basename(src))
    # ⚠ 桌住 `scenarios/<劇本>/tables/<桌ID>/`：**定義（board／seats／scenario／packs…）在劇本層**，
    #   桌目錄只有 config／runs ⇒ 夾具要從**定義層**抄，再把這一桌的 config 放進去。
    _def = layout.def_dir_of(src)
    shutil.copytree(_def, home, ignore=shutil.ignore_patterns("data", "runs", "*.bak-*",
                                                             ".secrets", "accounts.json*", "config.json"))
    shutil.rmtree(os.path.join(home, "tables"), ignore_errors=True)
    packs.seed_module_packs(_def, home)          # ＋模組層的包（rules.core 住模組 repo）   # ⚠ 只刪**頂層** tables/（那是「桌」的目錄）；
    layout.copy_table_config(src, os.path.join(home, "config.json"))   # 模板可能是劇本目錄（只有 example）
    os.makedirs(os.path.join(home, "data"), exist_ok=True)
    open(os.path.join(home, "data", "events.jsonl"), "w").close()
    cfgp = os.path.join(home, "config.json")
    c = json.load(open(cfgp, encoding="utf-8"))
    c.setdefault("auth", {})["accounts_file"] = os.path.join(home, "accounts_fake.json")
    # 2026-10-21：模板可能是劇本目錄（沒有現役桌的 config）⇒ 補上真桌會有、這支測試要用的鍵
    c.setdefault("auth", {}).setdefault("gm_token", "watch-gm-token")
    c.setdefault("table_id", os.path.basename(src))
    c.setdefault("run_id", c["table_id"])
    json.dump(c, open(cfgp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump({"schema": 2,
               "accounts": {"acct_fake": {"display": "F", "kind": "human",
                                          "token_sha256": hashlib.sha256(b"fake-token").hexdigest()}},
               "sessions": {str(c.get("table_id") or os.path.basename(src)): {"table": "t", "seats": {}}}},
              open(os.path.join(home, "accounts_fake.json"), "w", encoding="utf-8"), ensure_ascii=False)
    return home


def events_n(home: str) -> int:
    p = layout.events_path(home)
    return sum(1 for _ in open(p, encoding="utf-8")) if os.path.exists(p) else 0


def _pick_src() -> str:
    """挑一張現役桌當模板（**不硬編桌名**：2026-10-20「桌=run」遷移後 table id 會變）。"""
    for row in layout.scan(HERE):
        home = str(row.get("home") or "")
        cfgp = os.path.join(home, "config.json")
        if not os.path.isfile(cfgp):
            continue
        try:
            cfg = json.load(open(cfgp, encoding="utf-8"))
        except Exception:                                                    # noqa: BLE001
            continue
        if str((cfg.get("auth") or {}).get("gm_token") or ""):
            return home
    # 2026-10-21：現役桌可以整批退役 ⇒ 沒有桌就用劇本定義目錄當模板（模板只需要劇本檔＋旋鈕）
    d = layout.must_locate_scenario(HERE, "hyakki_tower", "sannenme_no_onibarai")
    print("[test_watch] 沒有現役桌 ⇒ 用劇本定義目錄當模板：", os.path.relpath(d, HERE))
    return d


def main() -> int:                                                        # noqa: C901
    src = _pick_src()
    if not (os.path.exists(os.path.join(src, "config.json")) or os.path.exists(os.path.join(src, "config.example.json"))):
        print("（跳過：這台沒有可當模板的桌）")
        return 0
    tmp = tempfile.mkdtemp(prefix="trpg_watch_")
    home = mk_table(tmp, src)
    cfgp = os.path.join(home, "config.json")
    cfg = json.load(open(cfgp, encoding="utf-8"))
    gm = cfg.setdefault("auth", {}).get("gm_token", "t")
    watch_token = "watch_read_" + os.urandom(8).hex()
    cfg.setdefault("auth", {})["require_account"] = False             # 先驗「不需要憑證」那條（旋鈕住 auth 段）
    cfg["watch"] = {"enabled": True, "port": 0, "read_token": watch_token}
    json.dump(cfg, open(cfgp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    p_main, p_watch = free_port(), free_port()
    main_url, watch_url = f"http://127.0.0.1:{p_main}", f"http://127.0.0.1:{p_watch}"
    start([PY, "server.py", "--config", cfgp, "--home", home, "--port", str(p_main)],
          os.path.join(tmp, "main.log"))
    if not wait_health(main_url):
        print("  FAIL  上游桌起不來（見 %s/main.log）" % tmp)
        return 1
    start([PY, "watch_server.py", "--home", home, "--port", str(p_watch),
           "--upstream", main_url, "--read-token", watch_token], os.path.join(tmp, "watch.log"))
    if not wait_health(watch_url):
        print("  FAIL  觀戰桌起不來（見 %s/watch.log）" % tmp)
        return 1
    time.sleep(0.5)

    print("== V1. 公開投影拿得到，**私密看不到** ==")
    for seat, name in (("p1", "旅人"), ("p2", "同伴")):
        post(main_url + "/api/join", {"seat": seat, "name": name, "kind": "human"})
    post(main_url + "/api/say", {"seat": "p1", "text": "PUBLIC_MARKER_燈を点ける"})
    post(main_url + "/api/gm", {"token": gm, "op": "secret", "to": "p2", "text": "SECRET_MARKER_真相"})
    post(main_url + "/api/tell", {"seat": "p1", "to": "p2", "text": "DM_MARKER_私訊"})
    ws, wstate = get(watch_url + "/api/state")
    _, wlog = get(watch_url + "/api/log")
    check("V1a 觀戰 `/api/state` 200 且是**沒有席位**的公開投影（沒有 `you`）",
          ws == 200 and not (jload(wstate).get("you")), f"HTTP {ws}")
    check("V1b 公開內容看得到（PUBLIC_MARKER）", "PUBLIC_MARKER" in wlog or "PUBLIC_MARKER" in wstate)
    check("V1c **私密看不到**：`secret`／私訊 都不在觀戰面",
          "SECRET_MARKER" not in wlog and "SECRET_MARKER" not in wstate
          and "DM_MARKER" not in wlog and "DM_MARKER" not in wstate)
    check("V1d 觀戰面的 `/api/log` 不含任何 `public:false` 事件",
          all(e.get("public") is not False for e in (jload(wlog).get("events") or [])))

    print("== V2. 觀戰桌**不能寫**（唯讀）==")
    n0 = events_n(home)
    codes = {}
    for path, body in (("/api/act", {"seat": "p1", "action": "check"}),
                       ("/api/say", {"seat": "p1", "text": "X"}),
                       ("/api/join", {"name": "spy", "kind": "human"}),
                       ("/api/enter", {"token": watch_token}),
                       ("/api/gm", {"token": gm, "op": "pause"}),
                       ("/mcp", {"jsonrpc": "2.0", "method": "tools/list", "id": 1}),
                       ("/hook/inbox", {"seat": "p1", "op": "act", "action": "defend"})):
        codes[path] = post(watch_url + path, body)[0]
    check("V2a 觀戰桌 POST 全部 404（沒有寫入面）",
          all(c == 404 for c in codes.values()), f"{codes}")
    check("V2b 這些嘗試**沒有動到上游桌**（事件數不變）", events_n(home) == n0,
          f"{n0} → {events_n(home)}")

    print("== V3. **不影響上游桌**：觀戰者連讀 ⇒ 上游桌一字不動 ==")
    n1 = events_n(home)
    _, st_before = get(main_url + "/api/state?gm_token=" + gm)
    for _ in range(12):
        get(watch_url + "/api/state")
    _, st_after = get(main_url + "/api/state?gm_token=" + gm)
    b, a = jload(st_before), jload(st_after)
    check("V3a 連讀 12 次 ⇒ 事件數不變", events_n(home) == n1, f"{n1} → {events_n(home)}")
    check("V3b 席位清單不變（觀戰者沒有進 `seats`／`order`）",
          [s.get("seat") for s in (b.get("seats") or [])] == [s.get("seat") for s in (a.get("seats") or [])],
          f"{[s.get('seat') for s in (a.get('seats') or [])]}")
    check("V3c 沒有 `seat1`／`seat2` 這種幽靈席（自動編號席）",
          not [s for s in (a.get("seats") or []) if str(s.get("seat", "")).startswith("seat")])

    print("== V4. **跟得上**：上游桌跑一手 ⇒ 觀戰桌下一次就看到 ==")
    post(main_url + "/api/say", {"seat": "p2", "text": "FOLLOW_MARKER_跟得上"})
    time.sleep(1.0)                                                    # 過微快取（TTL 0.75s）
    _, wlog2 = get(watch_url + "/api/log")
    check("V4 觀戰桌看到上游桌剛跑的那一手", "FOLLOW_MARKER" in wlog2)

    print("== V6. 唯讀白名單 ==")
    check("V6a `/api/map` 通（靜態地圖＝公開知識）", get(watch_url + "/api/map")[0] == 200)
    check("V6b `/health` 通＋看得出上游", jload(get(watch_url + "/health")[1]).get("status") == "ok"
          or get(watch_url + "/health")[0] == 200)
    check("V6c `/api/log` 通", get(watch_url + "/api/log")[0] == 200)

    print("== V5. `require_account=true`：帶 `read_token` 才進得來，且**只給公開投影** ==")
    cfg.setdefault("auth", {})["require_account"] =  True
    # 這一節測「沒開觀戰的局：沒憑證就進不去」⇒ 明確關掉局級觀戰開關
    #   （開著時匿名只讀＝公開投影是**刻意的設計**，見 `graphql/context.py::_anon_watch` ④）。
    #   觀戰桌本身由 `--read-token`／`--port` 起，不看這個開關。
    cfg.setdefault("watch", {})["enabled"] = False
    json.dump(cfg, open(cfgp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    # 上游桌要重啟才吃新 config（require_account 在起服務時讀）；副本的重啟不碰現役桌
    PROCS[0].terminate()
    time.sleep(1.5)
    p_main2, p_watch2 = free_port(), free_port()          # **換埠**：舊進程沒放手時不會誤判成新進程
    main_url2, watch_url2 = f"http://127.0.0.1:{p_main2}", f"http://127.0.0.1:{p_watch2}"
    start([PY, "server.py", "--config", cfgp, "--home", home, "--port", str(p_main2)],
          os.path.join(tmp, "main2.log"))
    wait_health(main_url2)
    # 觀戰桌帶 read_token（現在這台就是）⇒ 應 200；不帶 ⇒ 上游 403
    PROCS[1].terminate()
    time.sleep(1.0)
    start([PY, "watch_server.py", "--home", home, "--port", str(p_watch2),
           "--upstream", main_url2, "--read-token", watch_token], os.path.join(tmp, "watch2.log"))
    wait_health(watch_url2)
    c_ok, d_ok = watch_proj(watch_url2, str(cfg.get("table_id") or ""))   # 這一桌的 id 取自它的 config
    j_ok = ((d_ok.get("data") or {}).get("table") or {}).get("view", {}).get("raw") or {}
    check("V5a `require_account=true` ＋帶 `read_token` ⇒ 觀戰面 200 且**不含 `you`**（只放行公開投影）",
          c_ok == 200 and j_ok and "you" not in j_ok, f"HTTP {c_ok} keys={sorted(j_ok)[:8]}")
    PROCS[1].terminate()
    time.sleep(1.0)
    # 「沒有 read_token 的觀戰桌」＝上游桌那側**沒有發**這把憑證（config 沒有 watch.read_token）。
    # ⚠ 觀戰桌會從 config 讀 read_token ⇒ 要驗「沒憑證就進不去」，得餵它一份**沒有那把**的 config。
    p_watch3 = free_port()                                  # **又換埠**：前一台沒放手時不會誤判成新進程
    watch_url3 = f"http://127.0.0.1:{p_watch3}"
    cfg_noread = dict(cfg)
    cfg_noread["watch"] = {k: v for k, v in (cfg.get("watch") or {}).items() if k != "read_token"}
    cfgp_noread = os.path.join(tmp, "config_noread.json")
    json.dump(cfg_noread, open(cfgp_noread, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    start([PY, "watch_server.py", "--config", cfgp_noread, "--home", home, "--port", str(p_watch3),
           "--upstream", main_url2], os.path.join(tmp, "watch3.log"))
    wait_health(watch_url3)
    c_no, d_no = watch_proj(watch_url3, str(cfg.get("table_id") or ""))
    _denied = (c_no == 403) or bool(d_no.get("errors")) or (not ((d_no.get("data") or {}).get("table")))
    check("V5b 不帶 `read_token` ⇒ 上游桌拒（403 透傳或明講 errors），**不會**意外放行",
          _denied, f"HTTP {c_no} keys={sorted(d_no)[:6]}")
    check("V5c 觀戰桌自己也**沒有** `/api/me`（不認身分）", get(watch_url2 + "/api/me")[0] == 404)
    # V5d：`require_account=true` 時 **GM 的 `?gm_token=` 那條路仍要通**（docs/API.md：gm_token 換 GM 全視圖）。
    #      ⚠ 2026-10-07 線上真的壞過：auth 那段先跑 check_seat ⇒ 連 GM 都被 403（GM 面板整個不能用）。
    c_gm, _bgm = get(main_url2 + "/api/state?gm_token=" + gm)
    j_gm = jload(_bgm)
    check("V5d `require_account=true` ＋ `?gm_token=` ⇒ GM 全視圖（不被 account 認證擋）",
          c_gm == 200 and isinstance(j_gm.get("log"), list) and len(j_gm.get("seats") or []) >= 2,
          f"HTTP {c_gm}")
    c_anon, _b = get(main_url2 + "/api/state")
    check("V5e 匿名／沒有憑證 ⇒ 仍然 403（GM 那條路是例外，不是把閘打開）", c_anon == 403, f"HTTP {c_anon}")

    print("== V7. legacy（沒有 registry）一樣可用 ==")
    legacy = mk_table(tmp, src, "legacy_sora")
    _lcp = os.path.join(legacy, "config.json")
    _lc = json.load(open(_lcp, encoding="utf-8"))
    _lc.setdefault("auth", {}).pop("accounts_file", None)      # 第二階段：legacy＝沒有 accounts_file
    _lc["auth"]["require_account"] = False
    json.dump(_lc, open(_lcp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for _f in ("accounts.json", "accounts_fake.json"):         # 真的沒有 registry
        _p = os.path.join(legacy, _f)
        if os.path.exists(_p):
            os.remove(_p)
    lcfg = json.load(open(_lcp, encoding="utf-8"))
    json.dump(lcfg, open(os.path.join(legacy, "config.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    pl, pw2 = free_port(), free_port()
    start([PY, "server.py", "--config", os.path.join(legacy, "config.json"), "--home", legacy,
           "--port", str(pl)], os.path.join(tmp, "legacy.log"))
    ok_l = wait_health(f"http://127.0.0.1:{pl}")
    start([PY, "watch_server.py", "--home", legacy, "--port", str(pw2),
           "--upstream", f"http://127.0.0.1:{pl}"], os.path.join(tmp, "legacy_watch.log"))
    ok_w = wait_health(f"http://127.0.0.1:{pw2}")
    _tid_l = str((json.load(open(os.path.join(legacy, "config.json"), encoding="utf-8")) or {}).get("table_id") or "")
    c_l, d_l = watch_proj(f"http://127.0.0.1:{pw2}", _tid_l)
    _raw_l = ((d_l.get("data") or {}).get("table") or {}).get("view", {}).get("raw") or {}
    check("V7 legacy 桌：觀戰面照樣 200（不需要任何憑證）",
          ok_l and ok_w and c_l == 200 and _raw_l and not _raw_l.get("you"),
          f"HTTP {c_l} tid={_tid_l} keys={sorted(_raw_l)[:6]}")

    print(f"\n{'✅ 全過' if not FAIL else '❌ 有 FAIL'}：{len(OK)} 過 / {len(FAIL)} 敗")
    if FAIL:
        for f in FAIL:
            print("   FAIL:", f)
    print("（副本留在 %s，要追就用它）" % tmp)
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        for p in PROCS:
            try:
                p.terminate()
            except Exception:                                             # noqa: BLE001
                pass
    sys.exit(rc)
