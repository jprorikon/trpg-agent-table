#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_audio.py — **模組級音訊**（BGM／效果音）：資源綁模組、引擎只做播放控制。

為什麼要有這一支（2026-10-20 設計者定：「和圖片資源類似，資源綁在模組上，引擎只負責播放控制」）：
聲音和圖一樣是**作品內容**⇒ 必須綁在模組（`modules/<模組>/audio.json` ＋ `assets/audio/…`），
不可以讓 A 模組的 BGM 漏給 B 模組；而**播放控制**（什麼時候放、哪個聲道、淡入幾秒、現在在放什麼）
留在引擎，並且**進事件流**（回放一致、晚進場的人補得到現況）。

七組判據：

  A. 音訊表本身：別名制、缺＝沒有、壞資料只收警告（不炸桌）、模組白名單；
  B. 靜態面：同一條 `/assets/<模組>/…`、音訊 MIME、**Range ⇒ 206／416**、`audio.json` 不長快取、
     路徑穿越與非白名單副檔名一律 404；
  C. 播放控制（引擎）：`audio_play`／`audio_stop` 發 `audio` 事件、`state.audio` 由事件折出、
     **`op=replay` 仍一致**、打錯別名當場拒（列出可用的）；
  D. 模組宣告的自動提示（`cues`）：開景自動放 BGM、GM 帶 `bgm` 時以 GM 為準、
     音檔缺失時**不炸桌**但 health 看得到；
  E. 投影：沒有 `audio.json` 的模組一格都不多（legacy 逐字不變）；有表就給現況＋入口；
     GM 額外看得到可選清單；
  F. 模組隔離：A 模組的音訊表不會漏給 B 模組；
  G. 真起一台服務：`Range` 在真的 HTTP 上回 206（手機 Safari 的前提）。
"""
import json
from turn_helper import ensure_turn, drop_scene  # 拆景后的测试小工具
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo 根
sys.path.insert(0, HERE)
os.chdir(HERE)

from trpg.service import audio as AU                                                      # noqa: E402
from trpg.service import service as SVC                                                   # noqa: E402
from trpg.core.errors import RuleError                                                    # noqa: E402
import test_support as TS                                                                 # noqa: E402
from trpg.api import web as web_api                                                       # noqa: E402

FAIL = []
MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + bytes(range(64)) * 4     # 夠真：路由只讀 bytes
MP3_LEN = len(MP3)


def ok(name: str, cond: bool, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  " + str(detail)) if detail else ""))
    if not cond:
        FAIL.append(name)


def mini_audio(root: str, module: str, tracks: dict, cues: dict = None) -> str:
    """在 `<root>/modules/<module>/` 造一份迷你音訊表（tracks: alias → relpath）。"""
    md = os.path.join(root, "modules", module)
    doc = {"schema": 1, "module": module, "tracks": {}, "cues": dict(cues or {})}
    for alias, rel in tracks.items():
        if not rel:
            continue
        p = os.path.join(md, "assets", rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(MP3)
        doc["tracks"][alias] = {"path": rel, "title": alias, "gain": 0.5}
    with open(os.path.join(md, "audio.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    return os.path.join(md, "audio.json")


# ── A. audio.py 纯函数层 ────────────────────────────────────────────────────
def t_units() -> None:
    print("== A. 音訊表：別名制／缺＝沒有／壞資料只警告")
    root = tempfile.mkdtemp(prefix="trpg_audio_")
    try:
        ok("A1 沒有 audio.json ⇒ ok=False（這一桌沒有聲音）", AU.load(root, "m1").ok is False)
        ok("A2 沒有表 ⇒ 查別名回 None（引擎不發事件）", AU.load(root, "m1").track("bgm:x") is None)
        mini_audio(root, "m1", {"bgm:town": "audio/bgm/town.mp3",
                                "sfx:thunder": "audio/sfx/thunder.mp3"},
                   {"scene_open": "bgm:town", "boss_defeated": "sfx:thunder"})
        a = AU.load(root, "m1")
        ok("A3 表讀得動＋兩首", a.ok and len(a.keys()) == 2)
        t = a.track("bgm:town") or {}
        ok("A4 bgm 預設 loop=True、gain 照表", t.get("loop") is True and t.get("gain") == 0.5, t)
        ok("A5 sfx 預設 loop=False", (a.track("sfx:thunder") or {}).get("loop") is False)
        ok("A6 url 形狀（同一條 /assets 路由）",
           a.url("bgm:town") == "/assets/m1/audio/bgm/town.mp3", a.url("bgm:town"))
        ok("A7 cue 查得到", a.cue("scene_open") == "bgm:town" and a.cue("nothing") is None)
        ok("A8 查不到的別名 ⇒ None", a.track("bgm:nobody") is None and a.url("bgm:nobody") is None)
        ok("A9 manifest 形狀（index 帶 ?v= 破快取）",
           a.manifest().get("base") == "/assets/m1/" and "/audio.json?v=" in a.manifest().get("index", ""))
        ok("A10 catalog 給 GM（key／kind／title／ready；不含 URL）",
           all("url" not in row for row in a.catalog())
           and {r["key"] for r in a.catalog()} == set(a.keys()))
        # 壞資料：不炸桌，但要看得見
        with open(os.path.join(root, "modules/m1/audio.json"), "w", encoding="utf-8") as f:
            json.dump({"schema": 1, "tracks": {
                "town": {"path": "audio/bgm/town.mp3"},                    # 沒前綴
                "bgm:evil": {"path": "../../etc/passwd"},                  # 穿越
                "bgm:py": {"path": "audio/bgm/x.py"},                      # 非白名單副檔名
                "bgm:ok": {"path": "audio/bgm/town.mp3", "kind": "sfx"},   # kind 與前綴不符
            }, "cues": {"scene_open": "bgm:missing"}}, f, ensure_ascii=False)
        b = AU.load(root, "m1")
        ok("A11 壞掉的音軌被剔除（只剩合法那一條）", b.keys() == ["bgm:ok"], b.keys())
        ok("A12 壞資料收進 warnings（看得到，不是靜靜吃掉）", len(b.warnings()) >= 4, b.warnings())
        ok("A13 cue 指到不存在的鍵 ⇒ 不收（並警告）", b.cue("scene_open") is None
           and any("cues.scene_open" in w for w in b.warnings()))
        with open(os.path.join(root, "modules/m1/audio.json"), "w", encoding="utf-8") as f:
            f.write("{ 壞 json")
        c = AU.load(root, "m1")
        ok("A14 audio.json 壞掉 ⇒ 空的表＋警告（不炸桌）", c.ok is False and bool(c.warnings()))
        ok("A15 模組 id 白名單", AU.load(root, "../x").ok is False)
        # 音檔不在磁碟上 ⇒ url=None（缺＝沒有）
        with open(os.path.join(root, "modules/m1/audio.json"), "w", encoding="utf-8") as f:
            json.dump({"schema": 1, "tracks": {"bgm:ok": {"path": "audio/bgm/town.mp3"}}},
                      f, ensure_ascii=False)
        os.remove(os.path.join(root, "modules/m1/assets/audio/bgm/town.mp3"))
        d = AU.load(root, "m1")
        ok("A16 音檔不在磁碟 ⇒ url=None（不編一條 404 的 URL）",
           d.track("bgm:ok") is not None and d.url("bgm:ok") is None)
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ── B. HTTP 靜態面（MIME／Range／快取）─────────────────────────────────────
def t_route() -> None:
    print("== B. `/assets/<模組>/…`：音訊 MIME／Range／audio.json")
    root = tempfile.mkdtemp(prefix="trpg_audio_")
    try:
        mini_audio(root, "m1", {"bgm:town": "audio/bgm/town.mp3"},
                   {"scene_open": "bgm:town"})
        c, b, ct, h = web_api.asset_response(root, "/assets/m1/audio/bgm/town.mp3")
        ok("B1 200＋整份 bytes", c == 200 and b == MP3)
        ok("B2 音訊 MIME", ct == "audio/mpeg", ct)
        ok("B3 可長快取＋Accept-Ranges", "public" in h.get("Cache-Control", "")
           and h.get("Accept-Ranges") == "bytes", h)
        c2, b2, _ct, h2 = web_api.asset_response(
            root, "/assets/m1/audio/bgm/town.mp3", {"Range": "bytes=0-31"})
        ok("B4 Range ⇒ 206＋正確切片", c2 == 206 and b2 == MP3[0:32], f"{c2} {len(b2)}")
        ok("B5 206 帶 Content-Range（總長要對）",
           h2.get("Content-Range") == f"bytes 0-31/{MP3_LEN}", h2.get("Content-Range"))
        _c3, b3, _ct3, h3 = web_api.asset_response(
            root, "/assets/m1/audio/bgm/town.mp3", {"Range": f"bytes=-{16}"})
        ok("B6 尾段 Range（bytes=-16）", b3 == MP3[-16:])
        _c4, b4, _ct4, h4 = web_api.asset_response(
            root, "/assets/m1/audio/bgm/town.mp3", {"Range": "bytes=16-"})
        ok("B7 開放式 Range（bytes=16-）", b4 == MP3[16:] and h4.get("Content-Range", "").endswith(f"/{MP3_LEN}"))
        c5, _b5, _ct5, h5 = web_api.asset_response(
            root, "/assets/m1/audio/bgm/town.mp3", {"Range": f"bytes={MP3_LEN + 10}-"})
        ok("B8 越界 Range ⇒ 416＋`bytes */總長`", c5 == 416
           and h5.get("Content-Range") == f"bytes */{MP3_LEN}", f"{c5} {h5.get('Content-Range')}")
        c6, b6, ct6, h6 = web_api.asset_response(root, "/assets/m1/audio.json")
        ok("B9 audio.json 送得出去且 JSON", c6 == 200 and ct6 == "application/json" and b"tracks" in b6)
        ok("B10 清單類 JSON 不長快取（改了就換）", "no-cache" in h6.get("Cache-Control", ""))
        etag = h.get("ETag", "")
        c7, _b7, _ct7, _h7 = web_api.asset_response(
            root, "/assets/m1/audio/bgm/town.mp3", {"If-None-Match": etag})
        ok("B11 ETag ⇒ 304（省流量）", c7 == 304)
        for bad, why in [("/assets/m1/../../etc/passwd", "穿越"),
                         ("/assets/m1/audio/bgm/x.py", "非白名單副檔名"),
                         ("/assets/nope/audio/bgm/town.mp3", "沒有這個模組"),
                         ("/assets/m1/audio/bgm/nope.mp3", "沒有這個檔"),
                         ("/assets/../m1/audio.json", "壞模組 id")]:
            c8, _b8, _ct8, h8 = web_api.asset_response(root, bad)
            ok(f"B12 404：{why}", c8 == 404 and "no-store" in h8.get("Cache-Control", ""), bad)
        # 圖仍然不吃 Range（行為與以前一字不變）
        os.makedirs(os.path.join(root, "modules/m1/assets/bg"), exist_ok=True)
        with open(os.path.join(root, "modules/m1/assets/bg/x.webp"), "wb") as f:
            f.write(MP3)
        c9, b9, _ct9, h9 = web_api.asset_response(
            root, "/assets/m1/bg/x.webp", {"Range": "bytes=0-3"})
        ok("B13 圖帶 Range 照舊 200 整份（沒動到圖的語意）",
           c9 == 200 and b9 == MP3 and "Accept-Ranges" not in h9)
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ── C. 引擎的播放控制 ───────────────────────────────────────────────────────
def _table(module: str = "hyakki_tower", table_id: str = "audio_probe"):
    root, home, cfg = TS.mk_table(module=module, table_id=table_id)
    return root, home, cfg


def t_engine() -> None:
    print("== C. 引擎：audio 事件／state／replay／打錯字當場拒")
    root, home, cfg = _table()
    try:
        mini_audio(root, "hyakki_tower", {"bgm:town": "audio/bgm/town.mp3",
                                         "sfx:thunder": "audio/sfx/thunder.mp3"},
                   {"scene_open": "bgm:town"})
        svc = SVC.TableService(home, cfg)
        t = svc.table
        ok("C1 L6 把模組音訊表接到桌上", t.audio is not None and t.audio.ok)
        before = t.audio_state()
        ok("C2 什麼都還沒放 ⇒ 兩個聲道都 None", before["bgm"] is None and before["sfx"] is None)
        ev = t.audio_play(ch="bgm", key="bgm:town")
        ok("C3 audio_play 發一筆 audio 事件（public）",
           ev.get("type") == "audio" and ev.get("op") == "play" and ev.get("public") is True)
        ok("C4 事件帶已解析的 URL 與音軌參數",
           ev.get("url") == "/assets/hyakki_tower/audio/bgm/town.mp3"
           and ev.get("loop") is True and ev.get("gain") == 0.5 and ev.get("fade") == 0.8, ev)
        st = t.audio_state()
        ok("C5 state.audio.bgm 由事件折出（含 seq）",
           (st["bgm"] or {}).get("key") == "bgm:town" and st["bgm"]["seq"] == ev["seq"], st)
        ok("C6 公開日誌看得到這筆（人要看得懂為什麼在放這首）",
           any(e.get("type") == "audio" for e in t.state["log"]))
        sfx = t.audio_play(ch="sfx", key="sfx:thunder")
        ok("C7 sfx 走同一個事件型別、另一個聲道",
           t.audio_state()["sfx"]["key"] == "sfx:thunder" and t.audio_state()["seq"] == sfx["seq"])
        try:
            t.audio_play(ch="bgm", key="bgm:nobody")
            ok("C8 打錯別名要當場拒", False)
        except RuleError as exc:
            ok("C8 打錯別名要當場拒（並列出可用的）",
               "bgm:town" in str(exc) and "bgm:nobody" in str(exc), str(exc))
        try:
            t.audio_play(ch="bgm", key="sfx:thunder")
            ok("C9 聲道與前綴不符要拒", False)
        except RuleError:
            ok("C9 聲道與前綴不符要拒（sfx 不能當 BGM 循環）", True)
        stop = t.audio_stop(ch="bgm")
        ok("C10 audio_stop 清掉該聲道（事件也留著）",
           t.audio_state()["bgm"] is None and stop.get("op") == "stop"
           and t.audio_state()["seq"] == stop["seq"])
        ok("C11 op=replay 與記憶體一致（音訊狀態也由事件折出）",
           bool(t.replay_check().get("replay_matches")), t.replay_check().get("diff"))
        ok("C12 audio_list 給得出目錄（GM 換曲前先看）",
           {r["key"] for r in t.audio_catalog()["tracks"]} == {"bgm:town", "sfx:thunder"})
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_no_table() -> None:
    print("== C'. 沒有音訊表的桌：一律當場說清楚")
    root, home, cfg = _table(module="hyakki_tower", table_id="audio_none")
    try:
        svc = SVC.TableService(home, cfg)
        t = svc.table
        ok("C13 沒 audio.json ⇒ 桌子照常起（不炸）", t.audio is None or not t.audio.ok)
        try:
            t.audio_play(ch="bgm", key="bgm:x")
            ok("C14 沒表還要放 ⇒ 當場拒", False)
        except RuleError as exc:
            ok("C14 沒表還要放 ⇒ 當場拒（講清楚是模組沒放音訊表）", "audio.json" in str(exc), str(exc))
        ok("C15 沒表 ⇒ audio_list 回空目錄＋說明",
           t.audio_catalog()["tracks"] == [] and "audio.json" in str(t.audio_catalog().get("note")))
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ── D. 模組宣告的自動提示 ───────────────────────────────────────────────────
def t_cues() -> None:
    print("== D. cues：輪開始自動放 BGM／換曲走 audio_play")
    root, home, cfg = _table(table_id="audio_cue")
    try:
        mini_audio(root, "hyakki_tower",
                   {"bgm:night": "audio/bgm/night.mp3", "bgm:tower": "audio/bgm/tower.mp3",
                    "sfx:white": "audio/sfx/white.mp3"},
                   {"round_start": "bgm:night"})
        svc = SVC.TableService(home, cfg)
        t = svc.table
        ensure_turn(t)          # 拆景後：一輪開始＝唯一會放預設曲的時機
        ok("D1 輪開始 ⇒ 模組宣告的 BGM 自動放（不用 GM 記）",
           (t.audio_state()["bgm"] or {}).get("key") == "bgm:night", t.audio_state())
        ev = [e for e in t.events if e.get("type") == "audio"][-1]
        ok("D2 自動提示標了 auto=True（事件上分得出是模組給的還是 GM 給的）",
           ev.get("auto") is True and ev.get("by") == "module")
        # 換曲：拆景後沒有「開景帶 bgm」，改用 audio_play（GM 明示）
        t.audio_play(ch="bgm", key="bgm:tower")
        ok("D3 GM 用 audio_play 換曲 ⇒ 以 GM 為準（不吃表上的預設）",
           (t.audio_state()["bgm"] or {}).get("key") == "bgm:tower")
        ok("D4 換曲後預設曲只放過一次（不與預設疊兩次）",
           len([e for e in t.events if e.get("type") == "audio"
                and e.get("key") == "bgm:night"]) == 1)
        try:
            t.audio_play(ch="bgm", key="bgm:nope")
            ok("D5 audio_play 帶錯的 key ⇒ 當場拒", False)
        except RuleError:
            ok("D5 audio_play 帶錯的 key ⇒ 當場拒（不會靜靜沒聲音）", True)
        ok("D6 op=replay 在自動提示之後仍一致", bool(t.replay_check().get("replay_matches")),
           t.replay_check().get("diff"))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_cue_broken() -> None:
    """cue 指到的音檔不在磁碟上：不炸桌，但 health／warnings 看得到。"""
    print("== D'. 壞 cue：不炸桌、但看得見")
    root, home, cfg = _table(table_id="audio_badcue")
    try:
        md = os.path.join(root, "modules", "hyakki_tower")
        with open(os.path.join(md, "audio.json"), "w", encoding="utf-8") as f:
            json.dump({"schema": 1, "tracks": {"bgm:ghost": {"path": "audio/bgm/ghost.mp3"}},
                       "cues": {"round_start": "bgm:ghost"}}, f, ensure_ascii=False)
        svc = SVC.TableService(home, cfg)
        t = svc.table
        ensure_turn(t)
        ok("D7 音檔不在 ⇒ 輪照常開（不是音檔把桌子炸掉）",
           len([e for e in t.events if e.get("type") == "round_started"]) == 1)
        ok("D8 但沒有發 audio 事件（缺＝沒有）",
           not [e for e in t.events if e.get("type") == "audio"])
        ok("D9 警告看得到（health／audio_warnings）",
           any("bgm:ghost" in w for w in t.audio_warnings()), t.audio_warnings())
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ── E. 投影（純附加）────────────────────────────────────────────────────────
def t_projection() -> None:
    print("== E. 投影：有表才有 audio 這一格")
    root, home, cfg = _table(table_id="audio_view")
    try:
        mini_audio(root, "hyakki_tower", {"bgm:town": "audio/bgm/town.mp3"},
                   {"round_start": "bgm:town"})
        svc = SVC.TableService(home, cfg)
        seat = svc.table.join(name="甲", kind="human")
        v = svc.table.view(seat=seat)
        ok("E1 有音訊表 ⇒ 投影多 audio 一格", "audio" in v)
        ok("E2 入口（library）指向模組的 audio.json",
           str((v["audio"].get("library") or {}).get("index", "")).startswith("/assets/hyakki_tower/audio.json"))
        svc.table.audio_play(ch="bgm", key="bgm:town")
        v2 = svc.table.view(seat=seat)
        ok("E3 現況（bgm.url／seq）給前端直接播", (v2["audio"].get("bgm") or {}).get("url")
           == "/assets/hyakki_tower/audio/bgm/town.mp3" and v2["audio"]["seq"] > 0)
        ok("E4 玩家視角沒有可選清單（那是 GM 的事）", "available" not in v2["audio"])
        vg = svc.table.view(gm=True)
        ok("E5 GM 視角看得到 available／cues（LLM 不必讀檔就知道能放什麼）",
           bool(vg["audio"].get("available")) and vg["audio"]["available"][0]["key"] == "bgm:town"
           and vg["audio"].get("cues", {}).get("round_start") == "bgm:town")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t_legacy() -> None:
    print("== E'. 沒有音訊表的模組：投影逐字不變")
    root, home, cfg = _table(module="hyakki_tower", table_id="audio_legacy")
    try:
        svc = SVC.TableService(home, cfg)
        seat = svc.table.join(name="甲", kind="human")
        v = svc.table.view(seat=seat)
        ok("E6 沒音訊表 ⇒ 投影沒有 audio 這一格", "audio" not in v)
        mini_audio(root, "other_world", {"bgm:town": "audio/bgm/town.mp3"})
        v2 = SVC.TableService(home, cfg).table.view(seat=seat)
        ok("E7 別的模組有音訊表 ⇒ 這一桌一格都不多（不互相污染）", "audio" not in v2)
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ── G. 真起一台服務：Range 在真 HTTP 上 ────────────────────────────────────
def t_live_server() -> None:
    print("== G. 真起一台服務：Range ⇒ 206（手機 Safari 的前提）")
    import socket
    import subprocess
    import time as _t
    import urllib.error
    import urllib.request

    root, home, cfg = _table(table_id="audio_srv")
    try:
        mini_audio(root, "hyakki_tower", {"bgm:town": "audio/bgm/town.mp3"})
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
                with urllib.request.urlopen(base + "/health", timeout=2) as r:
                    _h = json.loads(r.read().decode("utf-8"))
                up = True
                break
            except Exception:                                         # noqa: BLE001
                _t.sleep(0.25)
        ok("G1 服務起得來", up)
        if up:
            ok("G2 /health 帶音訊表狀態（幾首／有沒有警告）",
               (_h.get("audio") or {}).get("present") is True and (_h.get("audio") or {}).get("tracks") == 1,
               _h.get("audio"))
            req = urllib.request.Request(base + "/assets/hyakki_tower/audio/bgm/town.mp3",
                                         headers={"Range": "bytes=0-31"})
            with urllib.request.urlopen(req, timeout=5) as r:
                body = r.read()
                ok("G3 真 HTTP：Range ⇒ 206＋切片＋Content-Range",
                   r.status == 206 and body == MP3[0:32]
                   and r.headers.get("Content-Range") == f"bytes 0-31/{MP3_LEN}",
                   f"{r.status} {r.headers.get('Content-Range')}")
            # 桌端真的把 audio 事件放進投影（GM 視角）
            from trpg.core import layout as layout                               # noqa: PLC0415
            sys.path.insert(0, os.path.join(HERE, "tools"))                      # `gql_client` 住 tools/
            import gql_client                                                    # noqa: PLC0415
            _base = layout.api_base(cfg, port=port)
            tok = cfg.setdefault("auth", {}).get("gm_token", "t")
            _v = gql_client.view(_base, str(cfg.get("table_id")), gql_client.session_of(_base, str(cfg.get("table_id")), gm=tok),
                                 seat="gm", gm=tok)
            ok("G4 GraphQL 的 view 帶得到 audio（前端一個端點就夠）",
               "audio" in json.loads(json.dumps(_v.get("raw") or _v)), str(list(_v))[:120])
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:                                             # noqa: BLE001
            proc.kill()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main() -> None:
    t_units()
    t_route()
    t_engine()
    t_no_table()
    t_cues()
    t_cue_broken()
    t_projection()
    t_legacy()
    t_live_server()
    print()
    print(("AUDIO: PASS  （0 FAIL）" if not FAIL else f"AUDIO: FAIL  {len(FAIL)}：{FAIL}"))
    sys.exit(1 if FAIL else 0)


main()
