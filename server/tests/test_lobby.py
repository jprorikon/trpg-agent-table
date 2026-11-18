#!/usr/bin/env python3
"""**大廳（入口頁）自測**——一台機器一張桌 ⇒ 這裡列出每一張桌（只讀、獨立埠）。

    python3 test_lobby.py

閘的內容（全部用**臨時 root**，不碰現役桌）：
  L1 掃桌：`campaigns/*/config.json`（`hidden` 的略過；scenario／port／watch／accounts 解析正確）
  L2 探活：起一張**真的臨時桌** ⇒ 大廳報 online／席次／事件筆數／尾序號；離線的桌照樣列出來（標離線）
  L3 收局判定（`_ended`）：尾段最後一筆 `ending` ⇒ 已收；其後又有 `table_created` ⇒ 不算已收
  L4 頁面：一張桌一張卡（顯示名取 scenario 的「──」前那段）、「進入」連到**那張桌自己的埠**、狀態字樣
  L5 只讀紀律：不代理任何桌的內容（`/api/state` 一律 404）、不寫任何桌的檔、POST 不做事
  L6 `/health`：自己的健康 ＋ 掃到幾張桌
"""
from __future__ import annotations

import json
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
sys.path.insert(0, HERE)          # 跑得起來就好（repo 根）
os.environ.setdefault("TRPG_LEGACY_REST", "1")   # 直接跑這支也要能打 `/api/*`（生產已退役 ⇒ 只有測試放行）
from trpg.core import layout as layout                     # 桌的位置（WS-B：不再硬編 campaigns/<桌>）
from trpg.rules import packs as packs                      # 模組層的包要抄進臨時桌（rules.core 住模組 repo）
import test_support as TS         # 夾具來源（有現役桌用它、沒有退 repo 定義層）
PY = sys.executable
SRC_DEF, SRC_CFG = TS.fixture_source()      # 有現役桌就抄它；沒有就退 repo 定義層（夾具不依賴線上桌）
OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def get(url, timeout=10):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception:                                                    # noqa: BLE001
        return 0, b""


def wait_health(url, tries=60):
    for _ in range(tries):
        if get(url + "/health", timeout=2)[0] == 200:
            return True
        time.sleep(0.25)
    return False


def write_table(root, name, *, port=None, scenario="", hidden=False, watch=None, accounts=False,
                cast=()):
    d = os.path.join(root, "campaigns", name)
    os.makedirs(d, exist_ok=True)
    cfg = {"table_id": name, "port": port, "scenario": scenario}
    if hidden:
        cfg["hidden"] = True
    if watch:
        cfg["watch"] = watch
    if accounts:
        cfg["auth"] = {"accounts_file": os.path.join(d, "accounts_registry.json")}
    json.dump(cfg, open(os.path.join(d, "config.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    json.dump({"seats": [{"seat": s, "character": ch, "kind": k} for s, ch, k in cast]},
              open(os.path.join(d, "seats.json"), "w", encoding="utf-8"), ensure_ascii=False)
    return d


def main() -> int:                                                       # noqa: C901
    tmp = tempfile.mkdtemp(prefix="trpg_lobby_")
    root = os.path.join(tmp, "root")
    procs = []
    try:
        print("== L1 掃桌（含 hidden 略過）==")
        p_live = free_port()
        live_home = write_table(root, "live_game", port=p_live,
                                scenario="測試塔 ── 副標題不要顯示（原創）", accounts=True,
                                watch={"enabled": True, "port": 9999})
        write_table(root, "offline_game", port=free_port(), scenario="離線的桌",
                    cast=[("p1", "char_a", "human"), ("p2", "solo_hero", "agent"),
                          ("gm", "gm_only", "gm")])
        write_table(root, "secret_game", port=free_port(), scenario="不該出現", hidden=True)
        # 真的起一張臨時桌（給 L2 探活用）
        os.makedirs(os.path.join(live_home, "data"), exist_ok=True)
        sys.path.insert(0, HERE)
        from trpg.service import table_directory as tdir                                       # noqa: PLC0415（掃桌／探活／收局＝共用模組）
        rows = tdir.discover(root)
        ids = [t["id"] for t in rows]
        check("L1a 掃到 2 張（hidden 的被略過）", ids == ["live_game", "offline_game"], f"{ids}")
        r0 = next(t for t in rows if t["id"] == "live_game")
        check("L1b 解析 scenario／port／watch／accounts",
              r0["port"] == p_live and r0["watch_port"] == 9999 and r0["accounts"] is True
              and r0["scenario"].startswith("測試塔"), f"{r0}")

        print("== L3 收局判定（尾讀事件流）==")
        ev = layout.events_path(live_home)
        open(ev, "w").close()
        check("L3a 空流 ⇒ 不算已收", tdir.ended(live_home) is False)
        with open(ev, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"seq": 1, "type": "table_created"}) + "\n")
            fh.write(json.dumps({"seq": 2, "type": "ending"}) + "\n")
        check("L3b 尾段最後是 `ending` ⇒ 已收", tdir.ended(live_home) is True)
        with open(ev, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"seq": 3, "type": "table_created"}) + "\n")
        check("L3c `ending` 之後又開了新局（`table_created`）⇒ 不算已收", tdir.ended(live_home) is False)

        print("== L2／L4／L5／L6 把大廳起起來打 ==")
        p_lobby = free_port()
        log = open(os.path.join(tmp, "lobby.log"), "w", encoding="utf-8")
        procs.append(subprocess.Popen([PY, os.path.join(HERE, "lobby_server.py"),
                                       "--root", root, "--port", str(p_lobby),
                                       "--timeout", "0.6"], cwd=HERE, stdout=log, stderr=log))
        base = f"http://127.0.0.1:{p_lobby}"
        check("L2a 大廳起得來（/health）", wait_health(base))

        c, body = get(base + "/api/tables")
        d = json.loads(body.decode("utf-8"))
        by = {t["id"]: t for t in d["tables"]}
        check("L2b 離線的桌照樣列出來、標 offline",
              by["offline_game"]["online"] is False and len(d["tables"]) == 2)
        # 起那張「live_game」臨時桌（複製 hyakki 的劇本當內容）
        _def = SRC_DEF                            # 定義層（WS-B：桌目錄裡只有 config／runs）
        for f in os.listdir(_def):
            if f in ("data", "runs") or f.startswith("data_") or f.startswith("accounts") or f == "config.json":
                continue                        # config 用自己的（scenario 要留著測）
            s, dd = os.path.join(_def, f), os.path.join(live_home, f)
            shutil.copytree(s, dd) if os.path.isdir(s) else shutil.copy(s, dd)
        packs.seed_module_packs(_def, live_home)   # ＋模組層的包（rules.core 住模組 repo）
        cfgp = os.path.join(live_home, "config.json")
        cfg = json.loads(json.dumps(SRC_CFG))                                        # 完整底稿
        want = json.load(open(cfgp, encoding="utf-8"))                              # test 自己寫的（scenario 等）
        cfg.update(want)
        cfg.update({"port": p_live, "table_id": "live_game"})
        cfg["auth"] = {"gm_token": "t"}
        cfg["kick"] = {"receiver_url": "http://127.0.0.1:9/trpg/kick", "receiver_token_file": ""}
        json.dump(cfg, open(cfgp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        procs.append(subprocess.Popen([PY, os.path.join(HERE, "server.py"),
                                       "--config", cfgp, "--home", live_home, "--port", str(p_live)],
                                      cwd=HERE, stdout=log, stderr=log))
        check("L2c 臨時桌起得來", wait_health(f"http://127.0.0.1:{p_live}"))
        time.sleep(0.6)
        c, body = get(base + "/api/tables")
        by = {t["id"]: t for t in json.loads(body.decode("utf-8"))["tables"]}
        t = by["live_game"]
        check("L2d 大廳報 online／席次／事件筆數／尾序號",
              t["online"] is True and isinstance(t["seats"], int) and isinstance(t["events"], int)
              and isinstance(t["last_seq"], int), f"{t['seats']}/{t['events']}/{t['last_seq']}")

        c, body = get(base + "/")
        html = body.decode("utf-8")
        check("L4a 頁面 200 ＋ 顯示名取「──」前那段", c == 200 and "測試塔" in html and "副標題不要顯示" in html)
        check("L4b 「進入」連到**那張桌自己的埠**", f":{p_live}/" in html, f"look for :{p_live}")
        check("L4c 狀態字樣（開著等人／離線）都在",
              ("開著" in html or "進行中" in html) and "離線" in html)
        check("L4d 頁面沒有 script（純靜態；不執行任何東西）", "<script" not in html.lower())
        check("L5a 不代理桌的內容：`/api/state` ⇒ 404", get(base + "/api/state")[0] == 404)
        check("L5b `/api/log`／`/api/map` 也 404", get(base + "/api/log")[0] == 404
              and get(base + "/api/map")[0] == 404)
        try:
            req = urllib.request.Request(base + "/api/act", data=b"{}", method="POST")
            with urllib.request.urlopen(req, timeout=5) as r:
                code = r.status
        except urllib.error.HTTPError as e:
            code = e.code
        except Exception:                                                # noqa: BLE001
            code = 0
        check("L5c POST 不做事（非 200）", code != 200, f"{code}")
        c, body = get(base + "/health")
        h = json.loads(body.decode("utf-8"))
        check("L6 `/health`：status/tables/online 三個數都對",
              c == 200 and h.get("service") == "trpg-lobby" and h.get("tables") == 2
              and h.get("online") == 1, f"{h}")

        print("== L7 角色 → 哪幾張桌（選角＝劇本資料，公開）==")
        c, body = get(base + "/api/tables")
        ch = {e["char"]: e for e in json.loads(body.decode("utf-8"))["chars"]}
        check("L7a `/api/tables` 回 chars：同一個角色出現在**兩張桌**都列出來",
              [t["id"] for t in ch["char_a"]["tables"]] == ["live_game", "offline_game"],
              f"{ch.get('char_a', {}).get('tables')}")
        check("L7b 記得出現在哪一席（座位號由劇本定死）",
              all(t.get("seat") for t in ch["solo_hero"]["tables"]),
              f"{ch.get('solo_hero', {}).get('tables')}")
        check("L7c NPC／GM 席標成「不開放玩家坐」（player=false）",
              ch["gm_only"]["player"] is False and ch["char_a"]["player"] is True)
        c, body = get(base + "/")
        html = body.decode("utf-8")
        check("L7d 頁面有「角色 → 去哪張桌」那一區塊", "角色 → 去哪張桌" in html)
        check("L7e 區塊裡連桌名都寫出來（看得到要去哪一張）",
              "離線的桌" in html and "char_a" in html)
    finally:
        for p in procs:
            p.terminate()
            try:
                p.wait(timeout=10)
            except Exception:                                            # noqa: BLE001
                p.kill()
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n== L7 大廳頁「桌子一覽」按模組分類（設計者 2026-10-07）==")
    from trpg.api import lobby as lobby_server                                                # noqa: E402
    _rows = [
        {"id": "a", "module": "m1", "scenario": "s1", "online": True, "ended": False,
         "turn": None, "port": 1, "seats": 3, "events": 5, "last_seq": 5, "cast": [], "accounts": True},
        {"id": "b", "module": "m1", "scenario": "s2", "online": True, "ended": True,
         "turn": None, "port": 2, "seats": 3, "events": 9, "last_seq": 9, "cast": [], "accounts": True},
        {"id": "c", "module": "m2", "scenario": "s1", "online": False, "ended": False,
         "turn": None, "port": 3, "seats": 2, "events": 1, "last_seq": 1, "cast": [], "accounts": False},
    ]
    page = lobby_server.render("127.0.0.1", _rows, prefix_mode=True).decode("utf-8")
    _heads = page.count('class="h2">')
    check("L7a 每個模組一個標題（模組 id ＋ 張數）",
          _heads >= 2 and "m1" in page and "m2" in page, f"標題 {_heads} 個")
    check("L7b 同一模組的桌被歸在同一組（m1 的兩張在同一段、m2 的在其後）",
          page.index(">m1") < page.index(">m2")
          and page.index(">m1") < page.index(">s2<") < page.index(">m2"),
          "序：m1 → s2 → m2")
    _mroot = tempfile.mkdtemp(prefix="lobby_modname_")
    try:                                                   # 模組自己給命名 ⇒ 標題出顯示名（id 仍看得到）
        os.makedirs(os.path.join(_mroot, "modules", "m1"), exist_ok=True)
        with open(os.path.join(_mroot, "modules", "m1", "module.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"name": "測試世界"}, fh, ensure_ascii=False)
        _old_root = lobby_server.LOBBY.get("root")
        lobby_server.LOBBY["root"] = _mroot
        p2 = lobby_server.render("127.0.0.1", _rows, prefix_mode=True).decode("utf-8")
        lobby_server.LOBBY["root"] = _old_root
        check("L7e 標題出模組的顯示名（並保留 id 供對路徑）",
              "測試世界" in p2 and "<code>m1</code>" in p2, "測試世界／m1")
    finally:
        shutil.rmtree(_mroot, ignore_errors=True)
    check("L7c 一張桌都沒掃到時給的是「新制路徑」提示（不是舊的 campaigns/*）",
          "scenarios" in lobby_server.render("h", [], prefix_mode=True).decode("utf-8"))
    check("L7d 沒有 module 欄位的舊列不會被吞掉（歸「未分類」）",
          "（未分類）" in lobby_server.render("h", [{"id": "x", "online": False, "port": 1}],
                                              prefix_mode=True).decode("utf-8"))

    print("\n== L8 建桌嚮導（前臺頁才有；走 /graphql，不寫第二套 REST）==")
    _pg = lobby_server.render("127.0.0.1", _rows, prefix_mode=True).decode("utf-8")
    check("L8a 前臺頁畫得出嚮導（憑證輸入 ＋ 模組清單容器）",
          'id="w-token"' in _pg and 'id="w-out"' in _pg and "建立新桌" in _pg)
    check("L8b 嚮導只打 /graphql（四種操作都在），且**沒有** /api/ 的後門",
          "fetch(\"/graphql\"" in _pg and "modules {" in _pg and "entry {" in _pg
          and "pickChar(" in _pg and "newRun(" in _pg and "/api/" not in _pg)
    check("L8c 憑證走標頭（Authorization: Bearer），不進查詢字串",
          "Authorization" in _pg and "gm_token" not in _pg)
    _so = lobby_server.render("127.0.0.1", _rows, prefix_mode=False).decode("utf-8")
    check("L8d 獨立大廳埠（沒有 /graphql）⇒ 不畫壞掉的嚮導，明說要上前台（單一入口）",
          'id="w-token"' not in _so and "單一入口" in _so and "/graphql" in _so)

    # ⚠ 實測踩過的坑：JS 內嵌在 Python 三引號字串裡時，**JS 字串裡的 `\n` 會被 Python 先吃掉
    #   變成真換行** ⇒ JS 語法錯（Invalid or unexpected token）、整個嚮導腳本不執行，
    #   而頁面看起來只是「載入中…」（極難察覺）。這裡直接在**原始碼**上卡：嚮導那段字串
    #   不許出現反斜線轉義（要就寫 char code，或把整段改成 raw 字串）。
    _src = TS.source_text("lobby_server.py")
    _blk = _src.split("_WIZARD_HTML = ", 1)[1].split("_WIZARD_CSS", 1)[0]
    check("L8e 嚮導字串裡沒有反斜線轉義（Python 會先吃掉 ⇒ JS 語法錯、嚮導整個不執行）",
          "\\" not in _blk, "偵測到反斜線" if "\\" in _blk else "")

    print(f"\n== 結果：{len(OK)} PASS / {len(FAIL)} FAIL ==")
    for x in FAIL:
        print("  FAIL " + x)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
