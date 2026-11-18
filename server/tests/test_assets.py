#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_assets.py — **模組級素材**（`GET /assets/<模組>/…` ＋ 投影的圖欄位）。

為什麼要有這一支（2026-10-09）：桌子開始吃圖片（立繪／場景／圖示），而圖是**作品內容**——
它必須綁在**模組**上（`modules/<模組>/assets/`），不可以讓 A 模組的圖漏給 B 模組。
這支測試釘住六件事：

  ① 靜態面**只送該模組 `assets/` 底下的白名單圖**：路徑穿越、非白名單副檔名、不存在的檔
     一律 404（這條靜態路由不能被拿去讀 `assets/` 以外的任何東西）；
  ② 圖**可以長快取**（`public, max-age` ＋ `ETag` → 304），而錯誤回應仍然 `no-store`；
  ③ 素材表是**模組級**的：A 模組的桌看不到 B 模組的圖（不互相污染）；
  ④ 投影的圖欄位**純附加**：沒有素材的模組／legacy 桌一格都不多（逐字不變）；
  ⑤ **別名制**：投影只查別名（`portrait:<角色 id>`／`bg:<節點 id>`／`icon:item:<道具 id>`…），
     不猜檔名 ⇒ 素材換檔／改名不必動投影程式；
  ⑥ 素材面**不看身分**（觀戰頁是匿名的，它也要看得到圖）。
  ⑦ **畫面用語表**（`<模組>/locale.json`，2026-10-19）：作品的固有名詞／世界觀用語由模組給，
     同一條路由送出、manifest 帶 `locale`（`?v=<mtime>` 破快取）；沒放用語表的模組一格都不多。
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo 根
sys.path.insert(0, HERE)
os.chdir(HERE)

from trpg.service import assets as A                                                      # noqa: E402
from trpg.service import service as SVC                                                   # noqa: E402
import test_support as TS                                               # noqa: E402
from trpg.api import web as web_api                                                          # noqa: E402

FAIL = []
PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)     # 夠真：路由只 stat／讀 bytes，不看內容


def ok(name: str, cond: bool, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  " + str(detail)) if detail else ""))
    if not cond:
        FAIL.append(name)


def mini_index(root: str, module: str, entries: dict) -> str:
    """在 `<root>/modules/<module>/assets/` 造一份迷你素材表（entries: alias → relpath）。"""
    ad = os.path.join(root, "modules", module, "assets")
    files, aliases = {}, {}
    for alias, rel in entries.items():
        if not rel:
            continue
        p = os.path.join(ad, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(PNG)
        key = rel[:-5] if rel.endswith(".webp") else rel
        files[key] = {"path": rel, "kind": rel.split("/", 1)[0]}
        aliases[alias] = key
    with open(os.path.join(ad, "index.json"), "w") as f:
        json.dump({"schema": 1, "module": module, "files": files, "aliases": aliases}, f)
    return ad


# ── A. assets.py 纯函数层 ───────────────────────────────────────────────────
def t_units() -> None:
    print("== A. 路徑白名單與別名表")
    ok("A1 safe_rel 收正常相對路徑", A.safe_rel("portrait/amber/a02.webp") == "portrait/amber/a02.webp")
    for bad, why in [("../x.webp", "穿越"), ("/etc/passwd", "絕對路徑"), ("a/../../b.webp", "穿越"),
                     ("a/./b.webp", "點段"), ("x.py", "副檔名"), ("a/b\\c.webp", "反斜線"),
                     ("", "空"), ("a//b.webp", "空段")]:
        ok(f"A2 擋 {why}：{bad!r}", A.safe_rel(bad) == "")
    ok("A3 index.json 是唯一的例外（前端要看清單）", A.safe_rel("index.json") == "index.json")
    # 2026-10-19.2：清單按類拆開 ⇒ `index/<類>.json` 放行（只准小寫英數＋底線）
    ok("A3a index/<類>.json 放行", A.safe_rel("index/portrait.json") == "index/portrait.json"
       and A.safe_rel("index/icon.json") == "index/icon.json")
    for bad in ("index/../portrait.json", "index/Portrait.json", "index/portrait.JSON",
                "index/portrait.txt", "index/sub/portrait.json", "index/.hidden.json",
                "index/portrait.json.bak", "Index/portrait.json"):
        ok(f"A3b 擋壞的 part 路徑：{bad}", A.safe_rel(bad) == "")
    ok("A4 模組 id 白名單", A.module_ok("hyakki_tower") and not A.module_ok("../x") and not A.module_ok(""))
    ok("A5 url_of 形狀", A.url_of("hyakki_tower", "bg/teckan.webp") == "/assets/hyakki_tower/bg/teckan.webp")
    ok("A6 url_of 擋壞模組", A.url_of("..", "bg/teckan.webp") is None)
    # 別名表：mtime 快取 / 壞檔 / 缺檔
    root = tempfile.mkdtemp(prefix="trpg_asset_")
    try:
        ok("A7 沒有 index.json ⇒ ok=False（＝這一桌沒有圖）", A.load(root, "m1").ok is False)
        mini_index(root, "m1", {"portrait:char_a": "portrait/amber/a02.webp",
                                "avatar:char_a": "avatar/amber.webp"})
        a = A.load(root, "m1")
        ok("A8 別名 ⇒ URL", a.url("portrait:char_a") == "/assets/m1/portrait/amber/a02.webp")
        ok("A9 查不到的別名 ⇒ None（投影不加那一格）", a.url("portrait:nobody") is None)
        ok("A10 manifest 形狀",
           a.manifest() == {"module": "m1", "base": "/assets/m1/", "index": "/assets/m1/index.json"})
        with open(os.path.join(root, "modules/m1/assets/index.json"), "w") as f:
            f.write("{ 壞 json")
        ok("A11 index.json 壞掉 ⇒ 空的（不炸桌）", A.load(root, "m1").ok is False)
        # A12/A13：schema 2（瘦清單＋parts）——引擎自己也要讀得懂，不然投影一格都不給
        ad = os.path.join(root, "modules/m1/assets")
        os.makedirs(os.path.join(ad, "index"), exist_ok=True)
        os.makedirs(os.path.join(ad, "portrait/amber"), exist_ok=True)
        with open(os.path.join(ad, "portrait/amber/a02.webp"), "wb") as f:
            f.write(PNG)
        with open(os.path.join(ad, "index/portrait.json"), "w") as f:
            json.dump({"schema": 1, "kind": "portrait",
                       "files": {"portrait/amber/a02": {"path": "portrait/amber/a02.webp"}},
                       "aliases": {"portrait:char_a": "portrait/amber/a02"}}, f)
        with open(os.path.join(ad, "index.json"), "w") as f:
            json.dump({"schema": 2, "module": "m1", "parts": {"portrait": "index/portrait.json"},
                       "counts": {"portrait": {"files": 1, "aliases": 1}}}, f)
        a2 = A.load(root, "m1")
        ok("A12 schema 2 讀得懂（parts 合併）", a2.ok and len(a2.files()) == 1)
        ok("A13 schema 2 的別名查得到", a2.url("portrait:char_a") == "/assets/m1/portrait/amber/a02.webp")
        with open(os.path.join(ad, "index/portrait.json"), "w") as f:
            f.write("{ 壞 part")
        a3 = A.load(root, "m1")
        ok("A14 part 壞掉 ⇒ 只少那一類（不炸桌）", a3.ok is False)
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ── B. HTTP 靜態面 ──────────────────────────────────────────────────────────
def t_route() -> None:
    print("== B. `GET /assets/<模組>/…`（白名單／快取／304／不看身分）")
    root = tempfile.mkdtemp(prefix="trpg_asset_")
    try:
        mini_index(root, "m1", {"bg:teckan": "bg/teckan.webp"})
        os.makedirs(os.path.join(root, "modules/m1/assets/icon"), exist_ok=True)
        with open(os.path.join(root, "modules/m1/assets/secret.py"), "wb") as f:
            f.write(b"print(1)\n")                                  # 非白名單副檔名
        c, b, ct, h = web_api.asset_response(root, "/assets/m1/bg/teckan.webp")
        ok("B1 200 + 檔案內容", c == 200 and b == PNG)
        ok("B2 Content-Type 依副檔名", ct == "image/webp", ct)
        ok("B3 可長快取（圖）", "public" in h.get("Cache-Control", "") and "max-age" in h.get("Cache-Control", ""))
        etag = h.get("ETag", "")
        ok("B4 有 ETag", bool(etag))
        c2, b2, _ct2, _h2 = web_api.asset_response(root, "/assets/m1/bg/teckan.webp", {"If-None-Match": etag})
        ok("B5 帶對的 ETag ⇒ 304（省流量）", c2 == 304 and b2 == b"")
        ok("B6 index.json 送得出去（前端要看清單）",
           web_api.asset_response(root, "/assets/m1/index.json")[0] == 200)
        # 2026-10-19.2：拆開的每一份 part（`index/<類>.json`）也走同一條靜態路由
        os.makedirs(os.path.join(root, "modules/m1/assets/index"), exist_ok=True)
        with open(os.path.join(root, "modules/m1/assets/index/portrait.json"), "w") as f:
            f.write('{"schema":1,"kind":"portrait","files":{},"aliases":{}}')
        c6, b6, ct6, h6 = web_api.asset_response(root, "/assets/m1/index/portrait.json")
        ok("B6a part 檔送得出去且是 JSON", c6 == 200 and ct6 == "application/json" and b"portrait" in b6)
        ok("B6a2 清單類 JSON 不長快取（no-cache，改了就換）",
           "no-cache" in h6.get("Cache-Control", ""), h6.get("Cache-Control"))
        ok("B6b part 目錄不能穿越（`index/../index.json`）",
           web_api.asset_response(root, "/assets/m1/index/../index.json")[0] == 404)
        for bad, why in [("/assets/m1/../../etc/passwd", "穿越"), ("/assets/m1/secret.py", "非白名單副檔名"),
                         ("/assets/nope/bg/teckan.webp", "沒有這個模組"),
                         ("/assets/m1/bg/nope.webp", "沒有這個檔"),
                         ("/assets/m1", "缺路徑段"), ("/assets/m1/", "缺路徑段"),
                         ("/assets/../m1/bg/teckan.webp", "壞模組 id")]:
            c3, _b3, _ct3, h3 = web_api.asset_response(root, bad)
            ok(f"B7 404：{why}", c3 == 404 and "no-store" in h3.get("Cache-Control", ""), bad)
        # 觀戰頁（匿名）也要吃得到：這條路由不帶任何 token 判斷
        src = TS.source_text("web_api.py")
        ok("B8 靜態面在 do_GET 最前面、且不做認證判斷",
           "if path.startswith(assets.URL_PREFIX" in src.split("def do_POST", 1)[0]
           and "asset_response(_root, self.path, self.headers)" in src)
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ── C. 投影的圖欄位（含「沒有素材就一格都不多」）────────────────────────────
def t_projection() -> None:
    print("== C. 投影：有素材才露欄位（模組級）")
    root, home, cfg = TS.mk_table(module="hyakki_tower", table_id="asset_probe")
    try:
        mini_index(root, "hyakki_tower", {
            "portrait:isuka": "portrait/isuka/a02.webp", "avatar:isuka": "avatar/isuka.webp",
            "bg:hyakki_f2": "bg/hyakki_f2.webp", "icon:item:heal_pack": "icon/item_heal_pack.webp",
        })
        svc = SVC.TableService(home, cfg)
        svc.table.join(seat="p4", name="イスカ", kind="agent")     # seats.json 的 p4＝isuka
        v = svc.table.view(seat="p4")
        ok("C1 table.module 認得出模組", v["table"].get("module") == "hyakki_tower", v["table"].get("module"))
        ok("C2 table.assets 給了入口（base／index）",
           v["table"].get("assets", {}).get("base") == "/assets/hyakki_tower/")
        ok("C3 you.portrait／avatar（別名查得到）",
           v["you"]["portrait"].endswith("/assets/hyakki_tower/portrait/isuka/a02.webp")
           and v["you"]["avatar"].endswith("/avatar/isuka.webp"))
        _seats = {s["seat"]: s for s in v["seats"]}
        ok("C4 seats[p4].portrait（公開席位也帶立繪）",
           _seats["p4"].get("portrait", "").endswith("/portrait/isuka/a02.webp"))
        ok("C5 沒有別名的席位不加欄位（沒有假資料）", "portrait" not in _seats.get("gm", {}))
        ok("C6 查不到的別名 ⇒ 那一格不加（不編 URL）", "avatar" not in _seats.get("gm", {}))
        ok("C7 GET /assets 這條真的服務得到（實檔存在）",
           web_api.asset_response(root, "/assets/hyakki_tower/portrait/isuka/a02.webp")[0] == 200)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_outfit_names() -> None:
    print("== C'. 投影：服裝立繪帶顯示名（角色表 outfits 有寫才帶）")
    root, home, cfg = TS.mk_table(module="hyakki_tower", table_id="asset_outfits")
    try:
        mini_index(root, "hyakki_tower", {
            "portrait:isuka": "portrait/isuka/a02.webp",
            "portrait:isuka:awakened": "portrait/isuka/awakened02.webp",
            "portrait:isuka:hanfu": "portrait/isuka/hanfu.webp",
            "portrait:isuka:swim": "portrait/isuka/swim.webp",
        })
        chars = os.path.join(root, "modules", "hyakki_tower", "characters.json")

        def write_chars(outfits) -> None:
            row = {"id": "isuka", "name": "イスカ"}
            if outfits is not None:
                row["outfits"] = outfits
            with open(chars, "w", encoding="utf-8") as f:
                json.dump({"characters": [row]}, f, ensure_ascii=False)

        write_chars([{"id": "default", "name": "通常"},
                     {"id": "awakened", "name": {"ja": "覚醒", "zh_Hant": "覺醒"}},
                     {"id": "hanfu_new", "name": "華服", "alias_id": "hanfu"}])
        svc = SVC.TableService(home, cfg)
        svc.table.join(seat="p4", name="イスカ", kind="agent")     # seats.json 的 p4＝isuka
        ps = {p["id"]: p for p in (svc.table.view(seat="p4")["you"].get("portraits") or [])}
        ok("C8 有服裝版本 ⇒ you.portraits（default＋三套）", set(ps) == {"default", "awakened", "hanfu", "swim"},
           sorted(ps))
        ok("C9 default／awakened 帶角色表的 name（多語系物件原樣給）",
           ps["default"].get("name") == "通常"
           and ps["awakened"].get("name") == {"ja": "覚醒", "zh_Hant": "覺醒"}, ps)
        ok("C10 素材鍵對上 alias_id 也認得", ps["hanfu"].get("name") == "華服", ps.get("hanfu"))
        ok("C11 角色表沒寫的服裝 ⇒ 不加 name 鍵（不編名字）", "name" not in ps["swim"], ps["swim"])
        write_chars(None)
        ps2 = svc.table.view(seat="p4")["you"].get("portraits") or []
        ok("C12 角色沒有 outfits ⇒ portraits 照給、每一筆都只有 {id, url}",
           len(ps2) == 4 and all(set(p) == {"id", "url"} for p in ps2), ps2)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_isolation_and_legacy() -> None:
    print("== D. 模組隔離 ＋ legacy 一字不變")
    root, home, cfg = TS.mk_table(module="hyakki_tower", table_id="asset_legacy")
    try:
        svc = SVC.TableService(home, cfg)
        seat = svc.table.join(name="甲", kind="human")
        v = svc.table.view(seat=seat)
        ok("D1 沒素材的模組（此夾具沒放 assets）⇒ 沒有 assets／module 欄位",
           "assets" not in v["table"] and "module" not in v["table"])
        ok("D2 席位也不帶圖欄位",
           all(("portrait" not in s and "avatar" not in s) for s in v["seats"]))
        ok("D3 自己那格也不帶圖", "portrait" not in (v.get("you") or {}))
        # 另一模組放了素材 ⇒ 也不該影響這一桌（模組級，不是 repo 級）
        mini_index(root, "other_world", {"portrait:isuka": "portrait/isuka/a02.webp"})
        v2 = SVC.TableService(home, cfg).table.view(seat=seat)
        ok("D4 別的模組有素材 ⇒ 這一桌一格都不多（不互相污染）",
           "assets" not in v2["table"] and "portrait" not in (v2.get("you") or {}))
        # 反面：把素材放在**這一張桌所屬的模組**才會生效
        ok("D5 模組 id 認得出來（hyakki_tower）", svc.table.module_id == "hyakki_tower", svc.table.module_id)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_live_server() -> None:
    """真起一台單桌服務：素材面**只有一個** `Cache-Control`（public），頁面仍然 `no-store`。

    為什麼要真起身：`no-store` 是 `Handler.end_headers()` 統一補的——素材圖自己送了
    `public, max-age=86400` 之後**不能被再補一次**（實測踩過：同一顆回應出現兩個
    `Cache-Control`，後面的 `no-store` 把快取關掉 ⇒ 每一輪輪詢把整面圖牆重抓）。
    """
    print("== E. 真起一台服務：快取標頭與頁面 no-store 並存")
    import socket
    import subprocess
    import time as _t
    import urllib.request
    import urllib.error

    root, home, cfg = TS.mk_table(module="hyakki_tower", table_id="asset_srv")
    try:
        mini_index(root, "hyakki_tower", {"bg:teckan": "bg/teckan.webp"})
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        cfg = json.loads(json.dumps(cfg))
        cfg["port"] = port
        cfg.pop("kick", None)
        with open(os.path.join(home, "config.json"), "w") as f:
            json.dump(cfg, f, ensure_ascii=False)
        log = open(os.path.join(root, "srv.log"), "w")
        py = sys.executable or "python3"     # 跟著跑這支測試的解釋器（別寫死 部署機那支：別台機器沒有）
        proc = subprocess.Popen([py, "server.py", "--config", os.path.join(home, "config.json"),
                                 "--home", home, "--port", str(port)], cwd=HERE,
                                stdout=log, stderr=log)
        base = f"http://127.0.0.1:{port}"
        up = False
        for _ in range(80):
            try:
                with urllib.request.urlopen(base + "/health", timeout=2):
                    up = True
                    break
            except Exception:                                         # noqa: BLE001
                _t.sleep(0.25)
        ok("E1 服務起得來", up)
        if up:
            with urllib.request.urlopen(base + "/assets/hyakki_tower/bg/teckan.webp", timeout=5) as r:
                cc = r.headers.get_all("Cache-Control") or []
                ok("E2 素材面只送一個 Cache-Control 且是 public",
                   len(cc) == 1 and cc[0].startswith("public"), cc)
                ok("E3 素材面不帶 Pragma:no-cache", not r.headers.get("Pragma"))
            try:
                urllib.request.urlopen(base + "/", timeout=5)
                _st = 200
            except urllib.error.HTTPError as _e:                          # noqa: PERF203
                _st = _e.code
            ok("E4 後台不送網頁（`/` ⇒ 410；頁面在前台容器）", _st == 410, f"HTTP {_st}")
            ok("E5 觀戰/匿名路徑＝同一條（不帶憑證也 200）",
               urllib.request.urlopen(base + "/assets/hyakki_tower/index.json", timeout=5).status == 200)
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:                                             # noqa: BLE001
            proc.kill()
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ── F. 畫面用語表（模組根的 locale.json）──────────────────────────────────────
def t_locale() -> None:
    print("== F. 畫面用語表 `<模組>/locale.json`")
    root = tempfile.mkdtemp(prefix="trpg_asset_")
    try:
        mini_index(root, "m1", {"bg:teckan": "bg/teckan.webp"})
        a = A.load(root, "m1")
        ok("F1 沒放 locale.json ⇒ manifest 一格都不多（legacy 不變）",
           a.manifest() == {"module": "m1", "base": "/assets/m1/", "index": "/assets/m1/index.json"})
        ok("F1b 沒放 ⇒ 路由 404", web_api.asset_response(root, "/assets/m1/locale.json")[0] == 404)
        doc = {"schema": 1, "glossary": {"空白旅団": {"zh-Hant": "空白旅團"}}}
        with open(os.path.join(root, "modules/m1/locale.json"), "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)
        mf = A.load(root, "m1").manifest()
        ok("F2 放了 ⇒ manifest 帶 locale（含 ?v= 破快取）",
           str(mf.get("locale") or "").startswith("/assets/m1/locale.json?v=") and mf.get("index"), mf)
        c, b, ct, _h = web_api.asset_response(root, mf["locale"])
        ok("F3 路由送得出去（帶 ?v= 也行）、JSON 型別、內容是模組根那一份",
           c == 200 and ct == "application/json" and json.loads(b.decode("utf-8")) == doc, f"{c} {ct}")
        with open(os.path.join(root, "modules/m1/assets/locale.json"), "w") as f:
            f.write('{"wrong": 1}')                                  # assets/ 裡的同名檔不是正本
        ok("F4 送的是模組根的 locale.json（不是 assets/ 底下的同名檔）",
           json.loads(web_api.asset_response(root, "/assets/m1/locale.json")[1].decode("utf-8")) == doc)
        for bad in ("/assets/m1/../locale.json", "/assets/m1/x/locale.json", "/assets/../m1/locale.json"):
            ok(f"F5 404：{bad}", web_api.asset_response(root, bad)[0] == 404)
        os.makedirs(os.path.join(root, "modules/m2"), exist_ok=True)
        with open(os.path.join(root, "modules/m2/locale.json"), "w") as f:
            f.write("{}")
        mf2 = A.load(root, "m2").manifest()
        ok("F6 只有用語表、沒有圖的模組 ⇒ manifest 只有 module＋locale（沒有 base／index）",
           set(mf2) == {"module", "locale"} and mf2["module"] == "m2", mf2)
        ok("F7 模組隔離：m3 看不到 m1／m2 的用語表", A.load(root, "m3").manifest() == {})
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main() -> None:
    t_units()
    t_route()
    t_locale()
    t_projection()
    t_outfit_names()
    t_isolation_and_legacy()
    t_live_server()
    print()
    print(("ASSETS: PASS  （0 FAIL）" if not FAIL else f"ASSETS: FAIL  {len(FAIL)}：{FAIL}"))
    sys.exit(1 if FAIL else 0)


main()
