#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""整桌暫停／恢復——設計者的 `/hold-trpg` 指令用的那支。

用法：
    python3 tools/hold_trpg.py status                     # 列出**每個 session**（哪一局、在不在暫停、誰的回合）
    python3 tools/hold_trpg.py on    --session <id>       # 暫停那一局（＝ /hold-trpg on）
    python3 tools/hold_trpg.py off   --session <id>       # 恢復那一局

**為什麼要 `--session`**（設計者 2026-10-07：「hold-trpg 现在需要选取到session才可用」）：
一個 session ＝ 一個副本（局）。同一台機器可以有好幾局（別張桌、或以後同一張桌的多個副本），
「暫停」是**對某一局**做的事 ⇒ 不能靠猜。規則：

* 給了 `--session`（可用**唯一字首**，例前 8 碼）⇒ 就操作那一局；找不到／撞多個 ⇒ **不動手**、把清單印出來。
* 沒給 ⇒ 只有在**剛好一局還沒收局**時才動手（並印出它選了哪一局）；**兩局以上還沒收局 ⇒ 拒絕**，
  印出清單請你補 `--session`。
* 已收局的局**不會**被自動挑中（停了也沒意義），除非你用 `--session` 明確指名。

設計：
- **走桌子的正規介面**：`POST /api/gm {op: pause|resume}`——與 app 的「暫停整桌／繼續」、GM 工具面
  **同一條路**，不另外開後門。閘門語意（停叫醒／停計時／停超時代打／不自動開局／牌面凍結；
  `say`/`tell` 照常）全在引擎那一側，本檔不重複實作。
- **gm_token 只在本行程的記憶體裡**：從該桌的 `config.json` 讀，**不當參數、不印出來、不進任何 prompt／日誌**。
- **自己找活著的桌**：`layout.scan` 掃桌的位置打 `/health`（**優先 `config.hub_base`**——hub 模式下
  桌自己的埠沒在聽）。`/health` 會回這一局的 `session`（＝副本 id），選取就靠它。

退出碼：0 成功／1 連不上或操作失敗／2 用法錯。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gql_client  # noqa: E402  （舊 /api/* 退役 ⇒ 工具走 GraphQL）

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                      # workspace/trpg


def campaigns_dir() -> str:
    """舊制的 `campaigns/` 那一層（**呼叫時才取**）：測試用 `TRPG_CAMPAIGNS_DIR` 指向臨時目錄。"""
    return os.environ.get("TRPG_CAMPAIGNS_DIR") or os.path.join(ROOT, "campaigns")


def parse_want(arg: str) -> "str | None":
    """人打的字 → 內部動作名（on／resume／status；認不得回 None）。"""
    a = (arg or "").strip().lower()
    if a in ("off", "resume", "繼續", "恢复", "恢復", "go", "release"):
        return "resume"
    if a in ("on", "pause", "暫停", "暂停", "hold", "stop"):
        return "on"
    if a in ("status", "st", "狀態", "状态"):
        return "status"
    return None


def _get(url: str, timeout: float = 3.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def _post(url: str, obj: dict, timeout: float = 8.0):
    req = urllib.request.Request(url, data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _events_path(home: str) -> str:
    """這一張桌的事件流（階段 1c 之後會變 runs/<session>/events.jsonl ⇒ 一律問 layout）。"""
    try:
        sys.path.insert(0, ROOT)
        from trpg.core import layout as layout                                                    # noqa: PLC0415
        return layout.events_path(home)
    except Exception:                                                    # noqa: BLE001
        return os.path.join(home, "data", "events.jsonl")


def _candidates():
    """(table_id, home, base, cfg)——用 `layout.scan` 掃桌（新制／舊制都吃）。

    ⚠ **base 一律優先 `hub_base`**：單一進程後台（hub）下，桌自己的埠**沒在聽**，
    只有 hub 的 `/<桌>` 子路徑在服務 ⇒ 拿 `port` 探活會「找不到活著的桌」（2026-10-07 修）。
    """
    out = []
    sys.path.insert(0, ROOT)
    from trpg.core import layout as layout                                                        # noqa: PLC0415
    env = os.environ.get("TRPG_CAMPAIGNS_DIR")
    if env:
        rows = ([{"home": os.path.join(env, n)} for n in sorted(os.listdir(env))
                 if os.path.isfile(os.path.join(env, n, "config.json"))]
                if os.path.isdir(env) else [])
    else:
        rows = layout.scan(ROOT)
    for row in rows:
        home = row["home"]
        try:
            with open(os.path.join(home, "config.json"), encoding="utf-8") as fh:
                cfg = json.load(fh)
        except Exception:                                                # noqa: BLE001
            continue
        port = int(cfg.get("port") or 0)
        base = layout.api_base(cfg, port=port)
        if not base:
            continue
        out.append((str(cfg.get("table_id") or os.path.basename(home)), home, base, cfg))
    return out


def live_sessions(campaign: str | None = None):
    """活著的**局**清單（一局一筆）。

    回 `[{"table","base","cfg","home","session","ended","turn","events"}]`——`session` 是這一局的
    副本 id（`/health` 回的），選取與顯示都用它。
    """
    out = []
    for tid, home, base, cfg in _candidates():
        if campaign and campaign not in (tid, os.path.basename(home),
                                         os.path.basename(os.path.dirname(home))):
            continue
        try:
            h = _get(base + "/health", timeout=1.5)
        except Exception:                                                # noqa: BLE001
            continue
        if (h or {}).get("status") != "ok":
            continue
        ev = _events_path(home)
        out.append({"table": tid, "base": base, "cfg": cfg, "home": home,
                    "session": str((h or {}).get("session") or ""),
                    "ended": bool((h or {}).get("ended")),
                    "turn": (h or {}).get("turn"),
                    "events": os.path.getsize(ev) if os.path.exists(ev) else 0})
    out.sort(key=lambda r: (r["ended"], -r["events"]))       # 還沒收局的排前面（同組比事件流）
    return out


def match_session(rows, want: str):
    """依 `--session` 選一局：**完全相等或唯一字首**（人常只複製前幾碼）。回 `(chosen, err)`。"""
    want = (want or "").strip()
    exact = [r for r in rows if r["session"] == want]
    if len(exact) == 1:
        return exact[0], ""
    if len(want) >= 4:
        pref = [r for r in rows if r["session"].startswith(want)]
        if len(pref) == 1:
            return pref[0], ""
        if len(pref) > 1:
            return None, f"`--session {want}` 撞到 {len(pref)} 局（字首太短）：" + _fmt_list(pref)
    return None, (f"找不到 session `{want}`。目前活著的局：\n" + _fmt_list(rows) if rows
                  else f"找不到 session `{want}`，而且沒有任何活著的局。")


def _fmt_list(rows) -> str:
    if not rows:
        return "  （沒有活著的局）"
    lines = []
    for r in rows:
        lines.append(f"  • {r['session'] or '（未提供 session）'}　桌 {r['table']}"
                     f"　{'已收局' if r['ended'] else '開著'}"
                     f"　回合 {r['turn'] if r['turn'] else '—'}")
    return "\n".join(lines)


def choose(rows, want_session: str):
    """沒給 `--session` 時的預設選法。回 `(chosen, err)`。

    **剛好一局還沒收局**才自動選；兩局以上還沒收局 ⇒ 拒絕（請他補 `--session`）。
    """
    if want_session:
        return match_session(rows, want_session)
    live = [r for r in rows if not r["ended"]]
    if len(live) == 1:
        return live[0], ""
    if not live:
        if len(rows) == 1:
            return rows[0], ""
        return None, ("沒有「還沒收局」的局，而且不只一局 ⇒ 請用 `--session <id>` 指名。\n"
                      + _fmt_list(rows))
    return None, (f"同時有 {len(live)} 局還沒收局 ⇒ **請用 `--session <id>` 指名**（不敢替你猜）：\n"
                  + _fmt_list(rows))


def state(base: str, token: str = "", table: str = "") -> dict:
    """讀桌況（舊 `/api/state`；2026-10-07 起走 GraphQL 的 `view.raw`，**形狀不變**）。

    ⚠ `require_account=true` 的桌匿名連讀都 403 ⇒ 帶 `gm_token`（現在走標頭）。
    ⚠ 桌 id：hub 模式從 `base` 的子路徑推（`http://主機:8971/百鬼塔`）⇒ 呼叫點不用改。
    """
    _tbl = table or urllib.parse.urlsplit(base).path.strip("/").split("/")[-1]
    return gql_client.view(base, _tbl, gql_client.session_of(base, _tbl, gm=token), seat="gm", gm=token)


def show(row: dict, st: dict | None = None) -> None:
    """印一局。`st`＝`/api/state`（有帶 token 才有；沒有就只印 `/health` 知道的那些）。"""
    turn = (st or {}).get("turn") or {}
    seat, left = turn.get("seat"), turn.get("seconds_left")
    print(f"  桌 {row['table']}　session {row['session'] or '（未提供）'}"
          f"　{'已收局' if row['ended'] else '開著'}"
          f"　回合 {seat if seat else '—'}"
          + (f"（剩 {left} 秒）" if left is not None else ""))
    if st is not None:
        print(f"    暫停中：{'是' if st.get('paused') else '否'}"
              + (f"（已凍 {st.get('paused_seconds')} 秒）" if st.get("paused") else ""))


def main() -> int:
    args = sys.argv[1:]
    raw = next((a for a in args if not a.startswith("--")), "on")
    want = parse_want(raw)
    if want is None:
        print(__doc__)
        return 2
    campaign = args[args.index("--campaign") + 1] if "--campaign" in args else None
    want_session = args[args.index("--session") + 1] if "--session" in args else ""

    rows = live_sessions(campaign)
    if not rows:
        print("找不到活著的桌（掃 modules/*/scenarios/*、campaigns/* 的 hub_base／port 打 /health 都沒回應）")
        return 1

    if want == "status":
        print(f"活著的局（{len(rows)}）：")
        for r in rows:
            tok = ((r["cfg"] or {}).get("auth") or {}).get("gm_token") or ""
            try:
                st = state(r["base"], tok)
            except Exception:                                            # noqa: BLE001
                st = None
            show(r, st)
        default, err = choose(rows, want_session)
        if default:
            print(f"（不指定 `--session` 時會操作：{default['session']}／{default['table']}）")
        else:
            print("（不指定 `--session` ⇒ 不會動手：" + err.splitlines()[0] + "）")
        return 0

    chosen, err = choose(rows, want_session)
    if not chosen:
        print("✗ " + err)
        return 1
    token = ((chosen["cfg"] or {}).get("auth") or {}).get("gm_token")
    if not token:
        print(f"⚠ {chosen['table']} 的 config.json 沒有 auth.gm_token —— 不能操作（暫停／恢復都要它）")
        return 1
    api_op = "pause" if want == "on" else "resume"        # 桌子的 op 名（與 app／GM 工具面同一組）
    try:
        _tbl = str((chosen.get("cfg") or {}).get("table_id") or chosen.get("table") or "")
        _sess = gql_client.session_of(chosen["base"], _tbl, gm=token)
        res = gql_client.gm_op(chosen["base"], _tbl, _sess, api_op, {"reason": "hold-trpg"}, gm=token)
    except urllib.error.HTTPError as exc:
        body = exc.read()[:200].decode("utf-8", "replace")
        print(f"✗ 操作失敗：HTTP {exc.code} {body}")
        return 1
    except Exception as exc:                                             # noqa: BLE001
        print(f"✗ 連不上 {chosen['base']}：{exc}")
        return 1

    p = (res or {}).get("pause") or {}
    act = "暫停" if want == "on" else "恢復"
    where = f"桌 {chosen['table']}／session {chosen['session'] or '（未提供）'}"
    if p.get("already"):
        print(f"（{act}：{where} 本來就是這個狀態，沒有重複送事件）")
    else:
        extra = f"（凍了 {p.get('frozen_seconds')} 秒，已還給當前回合）" if want == "resume" else ""
        print(f"✓ 已{act}{where}{extra}　事件 seq={p.get('seq')}")
    try:
        show(chosen, state(chosen["base"], token))
    except Exception as exc:                                             # noqa: BLE001
        print(f"  （讀回桌況失敗：{exc}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
