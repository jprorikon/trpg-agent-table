#!/usr/bin/env python3
"""**憑證比對的自測（非 ASCII 憑證）**——2026-10-07 起一張新劇本時抓到的真 bug 的回歸閘。

    python3 test_secret_token.py

病根：`web_api` 用 `hmac.compare_digest(str(a), str(b))` 比 `gm_token`／`watch.read_token`，
而 `compare_digest` **只吃 ASCII** ⇒ 憑證裡有 CJK（骨架 config 的中文占位符就是）時丟
`TypeError: comparing strings with non-ASCII characters`。實測症狀：**`/api/state` 直接斷線**
（連「沒帶 token」的請求都會踩到——它是拿空字串跟設定值比），玩家只看到「進不去」。
修法＝`_secret_eq()`（ASCII 走 compare_digest、其餘退化成 `==`）。

  S1 `_secret_eq` 單元：同／不同／None／CJK／空字串，一律回 bool **不炸**。
  S2 ASCII 憑證（舊行為不變）：`/api/state` 沒帶 token 200；帶對的 token 拿得到原始流。
  S3 **CJK 憑證（回歸）**：沒帶 token 200（修前＝連線中斷）、帶對的 CJK token 拿到原始流、
    帶錯的 token 只拿公開投影（不洩漏）。
  S4 `watch.read_token` 走同一支（CJK 也不炸）——觀戰桌的憑證同樣是手打的字串。
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
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo 根
sys.path.insert(0, HERE)
os.environ.setdefault("TRPG_LEGACY_REST", "1")   # 直接跑這支也要能打 `/api/*`（生產已退役 ⇒ 只有測試放行）
from trpg.core import layout as layout                                                          # noqa: E402
from trpg.rules import packs as packs                                                             # noqa: E402
from trpg.rules import scenario as scenario

CAMP = layout.must_locate_scenario(HERE, "hyakki_tower", "sannenme_no_onibarai")
DEF = layout.def_dir_of(CAMP)          # 定義層（WS-B：桌目錄裡只剩 config／runs）
MODF = scenario.module_filename(DEF) or "scenario.json"   # 劇本規則資料的檔名（WS-B：新名優先，別硬編）
PY = sys.executable

from trpg.api import web as web_api                                                           # noqa: E402

OK, FAIL = [], []
#: 非 ASCII 的憑證（骨架 config 的中文占位符就是這種；`compare_digest` 吃到它會丟 TypeError）
CJK_TOKEN = "REPLACE_ME_自己填一串亂數（GM 敘事／投放／開收景用；不要外流）"


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def mk_home(token: str, *, read_token: str = "") -> str:
    """複製一份劇本當臨時桌（原目錄零寫入），把 `auth.gm_token` 換成要測的那一把。"""
    tmp = tempfile.mkdtemp(prefix="trpg_secret_")
    for f in ("board.json", "goals.json", "clues.json", "seats.json", MODF):
        shutil.copy(os.path.join(DEF, f), os.path.join(tmp, f))
    layout.copy_table_config(CAMP, os.path.join(tmp, "config.json"))   # 旋鈕 ⇒ 桌目錄（定義在 DEF）
    shutil.copytree(os.path.join(DEF, "packs"), os.path.join(tmp, "packs"), dirs_exist_ok=True)
    packs.seed_module_packs(os.path.dirname(os.path.join(DEF, "packs")), tmp)   # ＋模組層的包（rules.core 住模組 repo）
    p = os.path.join(tmp, "config.json")
    cfg = json.load(open(p, encoding="utf-8"))
    cfg["port"] = 0
    cfg.pop("kick", None)
    cfg["auth"] = {"gm_token": token}
    if read_token:
        cfg["watch"] = {"enabled": False, "read_token": read_token}
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False)
    return tmp


def start(home: str, port: int, logpath: str) -> subprocess.Popen:
    fh = open(logpath, "w", encoding="utf-8")
    return subprocess.Popen([PY, "server.py", "--config", os.path.join(home, "config.json"),
                             "--home", home, "--port", str(port)], cwd=HERE, stdout=fh, stderr=fh)


def wait_health(url: str, secs: float = 25) -> bool:
    for _ in range(int(secs * 4)):
        try:
            with urllib.request.urlopen(url + "/health", timeout=3):
                return True
        except Exception:                                                # noqa: BLE001
            time.sleep(0.25)
    return False


def get(url: str):
    """回 (HTTP 碼, 內容)；連線被伺服器切斷時回 (0, b'')——那正是這個 bug 的症狀。"""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}),
                                    timeout=8) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception:                                                    # noqa: BLE001
        return 0, b""


def main() -> int:                                                       # noqa: C901
    print("== S1. _secret_eq 單元 ==")
    eq = web_api._secret_eq
    check("S1a ASCII 相符／不符", eq("abc", "abc") is True and eq("abc", "abd") is False)
    check("S1b None／空字串（設定值缺席時不炸）",
          eq(None, "abc") is False and eq(None, None) is True and eq("", "abc") is False)
    check("S1c CJK 相符／不符（修前這裡丟 TypeError）",
          eq("亂數一號", "亂數一號") is True and eq("亂數一號", "亂數二號") is False)
    check("S1d CJK vs 空字串 ⇒ False（就是那句「沒帶 token 的請求」）",
          eq("", "REPLACE_ME_自己填一串亂數") is False)
    check("S1e watch 的憑證比對走同一支（CJK read_token 不炸）",
          web_api._watch_token_ok(type("S", (), {"cfg": {"watch": {"read_token": "讀取憑證甲"}}})(),
                                  "讀取憑證甲") is True)

    print("== S2. ASCII 憑證（舊行為不變）==")
    home = mk_home("2ac95fc46395a29738578e6e")
    port = free_port()
    p = start(home, port, os.path.join(home, "srv.log"))
    try:
        check("S2a 伺服器起得來", wait_health(f"http://127.0.0.1:{port}"))
        c, body = get(f"http://127.0.0.1:{port}/api/state")
        check("S2b 沒帶 token ⇒ 200 公開投影", c == 200 and b"you" in body, f"{c}")
        c, body = get(f"http://127.0.0.1:{port}/api/log?gm_token=2ac95fc46395a29738578e6e")
        check("S2c 帶對的 token ⇒ 原始流（/api/log 回 events）", c == 200 and b"events" in body, f"{c}")
        c, body = get(f"http://127.0.0.1:{port}/api/state?gm_token=2ac95fc46395a29738578e6e")
        check("S2d 帶對的 token ⇒ GM 全視圖", c == 200 and b"gm" in body, f"{c}")
    finally:
        p.terminate()
        shutil.rmtree(home, ignore_errors=True)

    print("== S3. CJK 憑證（回歸：修前 /api/state 會斷線）==")
    home2 = mk_home(CJK_TOKEN, read_token="觀戰讀取憑證")
    port2 = free_port()
    p2 = start(home2, port2, os.path.join(home2, "srv.log"))
    try:
        up = wait_health(f"http://127.0.0.1:{port2}")
        check("S3a 伺服器起得來（config 的 token 是中文）", up)
        c, body = get(f"http://127.0.0.1:{port2}/api/state")
        check("S3b 沒帶 token ⇒ **200**（修前＝連線斷、HTTP 0）", c == 200 and b"you" in body, f"{c}")
        c, body = get(f"http://127.0.0.1:{port2}/api/state?seat=p1")
        check("S3c 帶 ?seat= ⇒ 仍走公開投影 200", c == 200, f"{c}")
        _q = urllib.parse.quote(CJK_TOKEN, safe="")
        c, body = get(f"http://127.0.0.1:{port2}/api/log?gm_token={_q}")
        check("S3d 帶對的中文 token ⇒ 原始流（200 且含 events）", c == 200 and b"events" in body, f"{c}")
        c, body = get(f"http://127.0.0.1:{port2}/api/state?gm_token={_q}")
        check("S3e 帶對的中文 token ⇒ GM 全視圖", c == 200 and b"gm" in body, f"{c}")
        c, _b = get(f"http://127.0.0.1:{port2}/api/state?gm_token=wrong")
        check("S3f 帶錯的 token ⇒ 只拿公開投影（不洩漏）", c == 200, f"{c}")
    finally:
        p2.terminate()
        shutil.rmtree(home2, ignore_errors=True)

    print(f"\n== 結果：{len(OK)} PASS / {len(FAIL)} FAIL ==")
    for x in FAIL:
        print("  FAIL " + x)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
