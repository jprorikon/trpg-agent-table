#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**WS-B：模組 → 劇本 → 角色ID｜位置與檔名的自測**（設計者 2026-10-07 拍「按照你的层次改」）。

    python3 test_layout.py

  L 組：桌的位置解析（`layout.py`）
    L1 新制 `modules/<模組>/scenarios/<劇本>/` 找得到
    L2 舊制 `campaigns/<桌>/` 照樣找得到（相容）
    L3 兩者並存 ⇒ 都回，且**新制在前**
    L4 **桌 id 與目錄名解耦**：對外 id 讀 `config.json` 的 `table_id`
    L5 `locate` 用 table_id 找得到；目錄名也當 id 認（舊習慣）
    L6 `repo_root` 從三層深（新制）與兩層深（舊制）都回同一個 repo 根
  D 組：名字
    D1 `resolve_module_path`：**新名 `scenario.json` 優先**
    D2 只有舊名 ⇒ 退回 `module.json` 且 `used_old=True`
    D3 兩個都沒有 ⇒ `(None, False)`
    D4 `module_filename` 回對的名字
    D5 `Scenario.load` 讀**新名**載得動
    D6 `Scenario.load` 讀**舊名**也載得動（相容；stderr 會有一行 warn）
  T 組：共用入口
    T1 `table_directory.discover` 新舊都列，且帶 `module`／`scenario`／`legacy_layout`
    T2 `cast` 讀得到（新制路徑下 seats.json 也對）
    T3 `table_directory.repo_root_of`／`table_factory.repo_root_of` 與 `layout.repo_root` 一致
    T4 `layout.where` 回這一張桌在分層裡的身分
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
from contextlib import redirect_stderr

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo 根
sys.path.insert(0, HERE)

from trpg.core import layout as layout                                                            # noqa: E402
from trpg.rules import scenario as scenario                                                          # noqa: E402
from trpg.service import table_directory as tdir                                           # noqa: E402

OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


def _write(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False)


def mk_tree():
    """鋪一棵同時有新制與舊制的臨時 repo（`hub_server.py` 當 repo 根的標記）。"""
    root = tempfile.mkdtemp(prefix="trpg_layout_")
    open(os.path.join(root, "hub_server.py"), "w", encoding="utf-8").close()
    # 新制：modules/m1/scenarios/s1（**目錄名 ≠ table_id**，咬解耦那一條）
    new_home = os.path.join(root, "modules", "m1", "scenarios", "s1")
    _write(os.path.join(new_home, "config.json"),
           {"table_id": "tid_new", "port": 9001, "scenario": "夜の塔"})
    _write(os.path.join(new_home, "scenario.json"), {"entities": {}})
    _write(os.path.join(new_home, "seats.json"),
           {"seats": [{"seat": "p1", "character": "カガリ", "kind": "human"}]})
    # 舊制：campaigns/old1（只有舊名 module.json）
    old_home = os.path.join(root, "campaigns", "old1")
    _write(os.path.join(old_home, "config.json"), {"table_id": "tid_old", "port": 9002})
    _write(os.path.join(old_home, "module.json"), {"entities": {}})
    _write(os.path.join(old_home, "seats.json"),
           {"seats": [{"seat": "p2", "character": "シズク", "kind": "agent"}]})
    # 現行（2026-10-19 起）：桌的實體住 `state/tables/<模組>/<劇本>/<桌ID>/`（模組目錄只有定義）
    st_home = os.path.join(root, "state", "tables", "m1", "s1", "tid_state")
    _write(os.path.join(st_home, "config.json"), {"table_id": "tid_state", "port": 9003})
    return root, new_home, old_home, st_home


def main() -> int:
    print("\n== T 劇本顯示名（scenario.json 的 meta.name → 沒寫退回目錄名）==")
    _t1 = layout.scenario_name(HERE, "hyakki_tower", "sannenme_no_onibarai")
    check("T1 現役劇本讀得到顯示名（不是 id）", _t1 not in ("", "sannenme_no_onibarai"), _t1)
    check("T1b 劇本 id **不該**是佔位符（'main'）——設計者 2026-10-07",
          all(r.get("scenario") != "main" for r in layout.scan(HERE)),
          str(sorted({str(r.get("scenario")) for r in layout.scan(HERE)})))
    check("T2 沒那個劇本 ⇒ 退回目錄名（不炸）",
          layout.scenario_name(HERE, "hyakki_tower", "no_such_scn") == "no_such_scn",
          layout.scenario_name(HERE, "hyakki_tower", "no_such_scn"))
    check("T3 掃描列帶兩個顯示名",
          all(r.get("module_name") and r.get("scenario_name") for r in layout.scan(HERE)
              if not r.get("legacy")), str(layout.scan(HERE)[:2]))

    root, new_home, old_home, st_home = mk_tree()

    print("== L 組：桌的位置 ==")
    rows = layout.scan(root)
    check("L1 新制找得到", any(r["home"] == new_home for r in rows), str(len(rows)))
    check("L2 舊制找得到", any(r["home"] == old_home for r in rows), str(len(rows)))
    check("L3 三種形狀並存：state（現行）最前 → 模組內舊形狀 → campaigns 最後",
          [r["legacy"] for r in rows] == [False, False, True] and rows[0]["home"] == st_home,
          str([(os.path.relpath(r["home"], root), r["legacy"]) for r in rows]))
    new_row = next(r for r in rows if r["home"] == new_home)
    check("L4 module／scenario 由路徑推得",
          (new_row["module"], new_row["scenario"]) == ("m1", "s1"), str(new_row))
    check("L5 對外 id 讀 config 的 table_id（目錄名≠id）",
          layout.table_id_of(new_row) == "tid_new" and layout.table_id_of(
              next(r for r in rows if r["home"] == old_home)) == "tid_old")
    check("L6 locate 用 id", (layout.locate(root, "tid_new") or {}).get("home") == new_home)
    check("L7 locate 用目錄名（舊習慣）", (layout.locate(root, "old1") or {}).get("home") == old_home)
    check("L8 repo_root：三層深與兩層深都回同一個根",
          layout.repo_root(new_home) == root and layout.repo_root(old_home) == root,
          f"{layout.repo_root(new_home)} / {layout.repo_root(old_home)}")

    print("== S 組：桌的實體住 server 側 `state/tables/`（2026-10-19）==")
    st_row = next(r for r in rows if r["home"] == st_home)
    check("S1 state 桌的 module／scenario 由路徑推得",
          (st_row["module"], st_row["scenario"]) == ("m1", "s1"), str(st_row))
    check("S2 `table_home()` 與掃到的那一筆是同一條路",
          layout.table_home(root, "m1", "s1", "tid_state") == st_home)
    check("S3 `def_dir_of(state 桌)` ⇒ 模組裡的劇本目錄（定義只在模組）",
          layout.def_dir_of(st_home) == os.path.join(root, "modules", "m1", "scenarios", "s1"),
          layout.def_dir_of(st_home))
    check("S4 `repo_root(state 桌)` ⇒ repo 根（五層深也切得到）",
          layout.repo_root(st_home) == root, layout.repo_root(st_home))
    check("S5 `where(state 桌)` ⇒ 分層身分",
          (layout.where(st_home, root) or {}).get("module") == "m1"
          and (layout.where(st_home, root) or {}).get("scenario") == "s1",
          str(layout.where(st_home, root)))
    check("S6 `locate` 用 table_id 找得到 state 桌",
          (layout.locate(root, "tid_state") or {}).get("home") == st_home)
    check("S7 state 桌的 def_dir 欄位也指到模組劇本目錄",
          st_row.get("def_dir") == os.path.join(root, "modules", "m1", "scenarios", "s1"),
          str(st_row.get("def_dir")))

    print("== D 組：劇本規則資料的檔名 ==")
    dbl = tempfile.mkdtemp(prefix="trpg_layout_dbl_")
    _write(os.path.join(dbl, "scenario.json"), {})
    _write(os.path.join(dbl, "module.json"), {})
    p, old = scenario.resolve_module_path(dbl)
    _bn = os.path.basename(str(p or ""))
    check("D1 新名優先", _bn == "scenario.json" and old is False, _bn)
    p2, old2 = scenario.resolve_module_path(old_home)
    check("D2 只有舊名 ⇒ 退回並標記 used_old",
          os.path.basename(str(p2 or "")) == "module.json" and old2 is True)
    empty = tempfile.mkdtemp(prefix="trpg_layout_empty_")
    check("D3 都沒有 ⇒ (None, False)", scenario.resolve_module_path(empty) == (None, False))
    check("D4 module_filename 回對的名字",
          scenario.module_filename(dbl) == "scenario.json"
          and scenario.module_filename(old_home) == "module.json"
          and scenario.module_filename(empty) is None)
    check("D5 Scenario.load 讀新名載得動",
          scenario.Scenario.load(new_home) is not None)
    err = io.StringIO()
    with redirect_stderr(err):
        sc_old = scenario.Scenario.load(old_home)
    check("D6 舊名也載得動（相容）且留一行 warn",
          sc_old is not None and "舊名 module.json" in err.getvalue(),
          err.getvalue().strip()[:60])

    print("== T 組：共用入口 ==")
    got = tdir.discover(root)
    byid = {t["id"]: t for t in got}
    check("T1 discover 三種形狀都列（id 走 table_id）",
          set(byid) == {"tid_state", "tid_new", "tid_old"}, str(sorted(byid)))
    check("T1b discover 帶 module／scenario／legacy_layout",
          (byid["tid_new"]["module"], byid["tid_new"]["scenario"],
           byid["tid_new"]["legacy_layout"]) == ("m1", "s1", False)
          and (byid["tid_state"]["module"], byid["tid_state"]["scenario"],
               byid["tid_state"]["legacy_layout"]) == ("m1", "s1", False)
          and (byid["tid_old"]["module"], byid["tid_old"]["legacy_layout"]) == ("old1", True),
          json.dumps({k: {kk: v[kk] for kk in ("module", "scenario", "legacy_layout")}
                      for k, v in byid.items()}, ensure_ascii=False))
    check("T2 cast 讀得到（新制路徑下 seats.json 也對）",
          (byid["tid_new"]["cast"] or [{}])[0].get("char") == "カガリ",
          str(byid["tid_new"]["cast"]))
    from trpg.service import table_factory as table_factory                                                 # noqa: E402
    check("T3 repo_root_of 三個都與 layout 一致",
          tdir.repo_root_of(new_home) == root and table_factory.repo_root_of(new_home) == root
          and tdir.repo_root_of(old_home) == root and tdir.repo_root_of(st_home) == root
          and table_factory.repo_root_of(st_home) == root)
    wh = layout.where(new_home, root)
    check("T4 where 回分層身分（模組內舊形狀與 state 形狀都對）",
          (wh["module"], wh["scenario"]) == ("m1", "s1")
          and (layout.where(st_home, root)["module"], layout.where(st_home, root)["scenario"])
          == ("m1", "s1"), str(wh))

    # ── R. 一局一檔（WS-B 階段 1c：`data/` → `runs/<session>/`）──────────────────────────
    from trpg.core import store as store                                                         # noqa: E402
    from trpg import engine as engine                                                        # noqa: E402
    rh = tempfile.mkdtemp(prefix="trpg_runs_")
    try:
        check("R1 全新家：沒有現役那一局，事件流先落在舊制 data/（相容）",
              store.current_run(rh) is None
              and layout.events_path(rh) == os.path.join(rh, "data", "events.jsonl"),
              layout.events_path(rh))
        st = store.FileStore(rh)
        p1 = st.begin_run("s1")
        st.append({"seq": 1, "type": "table_created", "run_id": "s1"})
        check("R2 begin_run：切到 runs/<session>/，並把 runs/current 指過去",
              p1 == os.path.join(rh, "runs", "s1", "events.jsonl")
              and store.current_run(rh) == "s1"
              and layout.events_path(rh) == p1
              and os.path.getsize(p1) > 0,
              f"{p1} current={store.current_run(rh)}")
        p2 = st.begin_run("s2")
        check("R3 再開一局：切到新局，**舊局的檔原地留著**（多局並存）",
              store.current_run(rh) == "s2" and os.path.exists(p1) and p2.endswith("s2/events.jsonl")
              and sorted(store.list_runs(rh)) == ["s1", "s2"],
              str(store.list_runs(rh)))
        os.remove(os.path.join(rh, "runs", "current"))
        check("R4 runs/current 掉了 ⇒ 自癒成最新那一局（不至於整張桌打不開）",
              store.current_run(rh) in ("s1", "s2"), str(store.current_run(rh)))
        lh = tempfile.mkdtemp(prefix="trpg_legacy_")
        os.makedirs(os.path.join(lh, "data"), exist_ok=True)
        with open(os.path.join(lh, "data", "events.jsonl"), "w", encoding="utf-8") as fh:
            fh.write('{"seq":1,"type":"table_created","run_id":"old"}\n')
        lst = store.FileStore(lh)
        lst.append({"seq": 2, "type": "x"})
        check("R5 舊制相容：只有 data/ 的桌照舊寫 data/（不必先搬）",
              layout.events_path(lh) == os.path.join(lh, "data", "events.jsonl")
              and os.path.getsize(os.path.join(lh, "data", "events.jsonl")) > 0
              and lst.dir == os.path.join(lh, "data"))
        dh = tempfile.mkdtemp(prefix="trpg_copy_")
        sid = store.copy_current_run(rh, dh)
        check("R6 copy_current_run：現役那一局抄過去（臨時桌用同一份流）",
              sid in ("s1", "s2") and store.current_run(dh) == sid
              and os.path.exists(os.path.join(dh, "runs", sid, "events.jsonl")), sid)
        eh = tempfile.mkdtemp(prefix="trpg_engine_")
        cfg = {"table_id": "t", "rules": {"name": "minimal-d20"}, "port": 0,
               "auth": {"gm_token": "t"}}
        import json as _json                                              # noqa: E402
        with open(os.path.join(eh, "config.json"), "w", encoding="utf-8") as fh:
            _json.dump(cfg, fh)
        t = engine.Table(home=eh, cfg=cfg, store=store.FileStore(eh), clock=lambda: 1000.0)
        sid_e = t.session_id()
        ep = layout.events_path(eh)
        check("R7 新桌由引擎開局：事件落在 runs/<session>/，session＝目錄名（＝對外 session）",
              ep == os.path.join(eh, "runs", sid_e, "events.jsonl")
              and len(sid_e) == 32 and os.path.getsize(ep) > 0,
              f"session={sid_e} path={ep}")
        check("R7b session 是 UUID（設計者：不需要好讀）",
              all(c in "0123456789abcdef" for c in sid_e) and len(sid_e) == 32, sid_e)
        check("R8 指定某一局讀：FileStore(session=…) 讀得到那一局",
              store.FileStore(eh, session=sid_e).load()[0].get("run_id") == sid_e)
        shutil.rmtree(eh, ignore_errors=True)
        shutil.rmtree(lh, ignore_errors=True)
        shutil.rmtree(dh, ignore_errors=True)
    finally:
        shutil.rmtree(rh, ignore_errors=True)

    # ── M. 模組命名（設計者 2026-10-07：「让模组给一个命名」）─────────────────────────────
    mroot = tempfile.mkdtemp(prefix="trpg_modname_")
    try:
        os.makedirs(os.path.join(mroot, "modules", "m1", "scenarios", "s1"), exist_ok=True)
        check("M1 沒有 module.json ⇒ 顯示名退回模組 id", layout.module_name(mroot, "m1") == "m1")
        with open(os.path.join(mroot, "modules", "m1", "module.json"), "w", encoding="utf-8") as fh:
            json.dump({"id": "m1", "name": "測試世界"}, fh, ensure_ascii=False)
        check("M2 有 module.json ⇒ 用它給的 name", layout.module_name(mroot, "m1") == "測試世界",
              layout.module_name(mroot, "m1"))
        with open(os.path.join(mroot, "modules", "m1", "module.json"), "w", encoding="utf-8") as fh:
            fh.write("{壞掉的 json")
        check("M3 module.json 壞掉 ⇒ 退回 id（不炸）", layout.module_name(mroot, "m1") == "m1")
        with open(os.path.join(mroot, "modules", "m1", "characters.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"characters": [{"id": "c1", "class": "player", "name": "甲"},
                                      {"id": "c2", "class": "npc", "name": "乙"}]}, fh,
                      ensure_ascii=False)
        _cs = layout.characters(mroot, "m1")
        check("M4 模組角色表讀得出來（角色ID → 類／名）",
              set(_cs) == {"c1", "c2"} and _cs["c1"]["class"] == "player", str(sorted(_cs)))
        check("M5 沒有 characters.json ⇒ 空表（不炸）", layout.characters(mroot, "nope") == {})
        # ⚠ 實測踩過：家目錄**不在 root 底下**時，`where` 靠相對關係會把「scenarios」當模組名
        _fixture = os.path.join(mroot, "modules", "m1", "scenarios", "s1")
        os.makedirs(_fixture, exist_ok=True)
        check("M6 where 從**路徑**推分層身分（家不在 root 底下也對）",
              layout.where(_fixture, "/nonexistent-root") == {"module": "m1", "scenario": "s1"},
              str(layout.where(_fixture, "/nonexistent-root")))
    finally:
        shutil.rmtree(mroot, ignore_errors=True)

    # ── S. 同一劇本多桌（`scenarios/<劇本>/tables/<桌ID>/`；設計者 2026-10-07 第 3 條）──────
    print("\n== S. 劇本層定義共享／桌各自一個目錄 ==")
    sroot = tempfile.mkdtemp(prefix="trpg_layout_s_")
    try:
        sdir = os.path.join(sroot, "modules", "m9", "scenarios", "s9")
        t1 = os.path.join(sdir, "tables", "t1")
        t2 = os.path.join(sdir, "tables", "t2")
        os.makedirs(t1)
        os.makedirs(t2)
        json.dump({"id": "t1", "table_id": "桌一"}, open(os.path.join(t1, "config.json"), "w"))
        json.dump({"id": "t2", "table_id": "桌二"}, open(os.path.join(t2, "config.json"), "w"))
        json.dump({"seats": []}, open(os.path.join(sdir, "seats.json"), "w"))
        rows9 = layout.scan(sroot)
        check("S1 掃得到 `tables/` 底下的桌（兩張都在）",
              sorted(r["home"] for r in rows9) == sorted([t1, t2]), str(len(rows9)))
        check("S2 桌的 id 讀它**自己的** config（不是目錄名）",
              sorted(r["home"] and layout.read_config(r["home"])["table_id"] for r in rows9)
              == ["桌一", "桌二"], "")
        check("S3 兩張桌的 `def_dir` 都是**劇本層**（定義共享）",
              all(r["def_dir"] == sdir for r in rows9), str([r["def_dir"] for r in rows9]))
        check("S4 `def_dir_of`：桌目錄 ⇒ 劇本層；舊制 ⇒ 自己",
              layout.def_dir_of(t1) == sdir and layout.def_dir_of(new_home) == new_home, "")
        # 遷移期：劇本目錄自己也是桌（舊制）＋ tables/ 另有桌 ⇒ **兩張都要列出來，不吞桌**
        json.dump({"id": "old", "table_id": "舊桌"}, open(os.path.join(sdir, "config.json"), "w"))
        rows9b = layout.scan(sroot)
        check("S5 舊制（劇本目錄自己一張桌）＋新制 tables/ 並存 ⇒ 都列出來，不吞桌",
              len(rows9b) == 3 and any(r["home"] == sdir for r in rows9b), str(len(rows9b)))
    finally:
        shutil.rmtree(sroot, ignore_errors=True)

    # ── N. 版控衛生（兩個實際踩過的洞：新制路徑下備份檔／事件流會溜進版控）─────────────
    gi = [l.strip() for l in open(os.path.join(HERE, ".gitignore"), encoding="utf-8")
          if l.strip() and not l.strip().startswith("#")]
    check("N1 .gitignore 蓋得住新制路徑：`runs/`、`*.bak-*`、`config.json`（都要**不分層**）",
          "runs/" in gi and "*.bak-*" in gi and "config.json" in gi, str(gi[:12]))
    try:
        import subprocess                                                # noqa: E402
        out = subprocess.run(["git", "ls-files"], cwd=HERE, capture_output=True, text=True,
                             timeout=15)
        tracked = [l for l in out.stdout.splitlines() if "/runs/" in l or l.endswith("/runs/current")]
        # ★ 2026-10-24（review R3）：`git ls-files` 在「不是 git 工作區」的樹上會回非 0
        #   （例如 `git archive` 出來的驗證沙箱、或別人 unpack 的 tarball）⇒ 這兩格跳過，
        #   否則乾淨環境一定會假紅。
        _nogit = out.returncode != 0
        check("N2 事件流（runs/）真的不在版控裡" + ("（這棵樹不是 git 工作區 ⇒ 跳過）" if _nogit else ""),
              _nogit or not tracked, str(tracked[:4]))
        cfg_tracked = [l for l in out.stdout.splitlines()
                       if l.endswith("/config.json") or l.endswith("/accounts.json")]
        check("N3 真 config／accounts 不在版控裡" + ("（這棵樹不是 git 工作區 ⇒ 跳過）" if _nogit else ""),
              _nogit or not cfg_tracked, str(cfg_tracked[:4]))
    except Exception as exc:                                             # noqa: BLE001
        check("N2 事件流不在版控裡（這台沒有 git ⇒ 跳過）", True, f"{type(exc).__name__}")

    shutil.rmtree(root, ignore_errors=True)
    shutil.rmtree(dbl, ignore_errors=True)
    shutil.rmtree(empty, ignore_errors=True)
    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    for f in FAIL:
        print("  FAILED: " + f)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
