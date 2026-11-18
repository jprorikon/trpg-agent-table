#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**建桌：每桌一把新的 GM 控制鍵**（review P4，2026-10-20）。

以前 `Hub.create_table` 從同模組的桌繼承整段 `auth`（含 `gm_token`）⇒ 同模組所有桌共用
一把，外流一把全模組失守。現在只繼承帳號層／部署鍵，`auth.gm_token` 每桌重新生成。

驗：
  H1 第一張桌：沒有可繼承的桌也長得出 `auth.gm_token`
  H2 第二張桌：與第一張的 token **不同**
  H3 帳號層／部署鍵仍可繼承（用 `require_account` 當代表）
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from trpg.api import hub as hub_server                                  # noqa: E402
from trpg.core import layout as layout                                  # noqa: E402

OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


def main() -> int:
    src = os.path.join(HERE, "modules", "hyakki_tower")
    if not os.path.isdir(src):
        print("SKIP：沒有 modules/hyakki_tower 可當底稿")
        return 0
    tmp = tempfile.mkdtemp(prefix="trpg_hubauth_")
    old_root = hub_server.Hub.root
    stops = []
    try:
        dst = os.path.join(tmp, "modules", "hyakki_tower")
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns(
            "assets", ".git", "runs", ".studio", "node_modules", "docs"))

        Hub = hub_server.Hub
        for _k in ("services", "rows", "errors", "_stops", "_ticks"):
            getattr(Hub, _k).clear()
        Hub.root = tmp

        # 第一張桌：沒有 donor ⇒ hub 自己長 auth
        out1 = Hub.create_table("hyakki_tower", "sannenme_no_onibarai", log=lambda *_a: None)
        check("H1 第一張桌建得起來", bool(out1.get("ok")), out1.get("error"))
        tid1 = str((out1.get("table") or {}).get("id") or "")
        p1 = os.path.join(tmp, "state", "tables", "hyakki_tower",
                          "sannenme_no_onibarai", tid1, "config.json")
        c1 = json.load(open(p1, encoding="utf-8"))
        t1 = str((c1.get("auth") or {}).get("gm_token") or "")
        check("H1b 沒有模板 auth 也生得出 gm_token（不是 REPLACE_ME）",
              bool(t1) and "REPLACE_ME" not in t1, t1[:6] + "…")
        check("H1c table_id／run_id／session 是同一個值（桌＝run）",
              str(c1.get("run_id") or "") == tid1
              and str((out1.get("table") or {}).get("session") or "") == tid1,
              (c1.get("run_id"), (out1.get("table") or {}).get("session"), tid1))
        # 讓第二張桌有可繼承的帳號層設定
        c1["auth"]["require_account"] = False
        json.dump(c1, open(p1, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

        # 第二張桌：只繼承部署鍵，GM 憑證重新生成
        out2 = Hub.create_table("hyakki_tower", "sannenme_no_onibarai", log=lambda *_a: None)
        check("H2 第二張桌建得起來", bool(out2.get("ok")), out2.get("error"))
        tid2 = str((out2.get("table") or {}).get("id") or "")
        p2 = os.path.join(tmp, "state", "tables", "hyakki_tower",
                          "sannenme_no_onibarai", tid2, "config.json")
        c2 = json.load(open(p2, encoding="utf-8"))
        t2 = str((c2.get("auth") or {}).get("gm_token") or "")
        check("H2b 兩張桌的 gm_token 不同（不再同模組共用一把）", bool(t2) and t2 != t1)
        check("H3 帳號層設定（require_account）仍從 donor 繼承",
              (c2.get("auth") or {}).get("require_account") is False,
              (c2.get("auth") or {}).get("require_account"))

        # ★ 2026-10-20（「桌和 run 等价」）：newRun 是**再建一個 play 實例**，不是換掉舊局。
        _old_sess = str(Hub.services[tid1].table.session_id() or "")
        _nr = Hub.new_run(tid1, log=lambda *_a: None)
        _new_table = str((_nr or {}).get("table") or "")
        check("H4 newRun 成功並回新 table／session", bool(_nr.get("ok")) and bool(_new_table), _nr)
        check("H4b 舊 run 原地不動（registry 還在、session 不變）",
              tid1 in Hub.services
              and str(Hub.services[tid1].table.session_id() or "") == _old_sess,
              (_old_sess, tid1 in Hub.services))
        check("H4c 新 run 的 table_id＝run_id＝session",
              bool(_new_table) and _new_table != tid1
              and str((_nr or {}).get("session") or "") == _new_table,
              (_new_table, (_nr or {}).get("session")))
        _p_new = os.path.join(tmp, "state", "tables", "hyakki_tower",
                              "sannenme_no_onibarai", _new_table, "config.json")
        _cnew = json.load(open(_p_new, encoding="utf-8")) if os.path.exists(_p_new) else {}
        check("H4d 新 run 沿用劇本/設定（packs 有帶）", bool((_cnew.get("packs") or {}).get("enabled")))

        # ★ 2026-10-20：重啟後 config 的 run_id 要釘住同一局（不會又生一個 UUID 局）。
        for st in (Hub._stops or {}).values():
            st.set()
        Hub.services.clear()
        Hub.rows.clear()
        Hub._stops.clear()
        Hub._ticks.clear()
        Hub.load(tmp, log=lambda *_a: None)
        check("H5 重啟後 run_id／session 仍等於 table_id（config 釘住這一局）",
              tid1 in Hub.services
              and str(Hub.services[tid1].table.session_id() or "") == tid1,
              (tid1, (Hub.services.get(tid1).table.session_id() if tid1 in Hub.services else None)))
    finally:
        hub_server.Hub.root = old_root
        for st in (hub_server.Hub._stops or {}).values():
            st.set()
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n===== {len(OK)} PASS / {len(FAIL)} FAIL =====")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
