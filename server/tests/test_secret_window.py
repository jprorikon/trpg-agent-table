#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_secret_window.py — 私訊收件匣的「送進模型」窗口（`view.secret_window`）。

為什麼要有：私訊會**無上限長出來**。現役桌跑到第五天，單一席位的收件匣已經
p1 8.9k 字／p2 9.9k／p3 7.0k，而每一次喚醒都把整包重送一次（p2 的喚醒訊息因此 11.7k 字）。
這支測試釘住四件事：

  ① 喚醒訊息只列最近 N 則（預設 8），而且**明確寫出還有幾則沒列**（不能靜默丟）；
  ② **投影本身不裁**——網頁 messenger 與 `table_view detail="full"` 一定看得到完整收件匣
     （資訊防火牆的另一半：裁的是「送進模型的那一份」，不是資料本身）；
  ③ `secret_window: 0` ＝全部＝**舊行為逐字節相同**（逃生口）；
  ④ compact 視圖才收窗口，且帶 `your_secrets_hidden` 讓人知道被藏了幾則。
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo 根
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.chdir(HERE)

from trpg.engine import projection as projection                                                       # noqa: E402
from trpg.service import service as SVC                                                   # noqa: E402
import test_support as TS                                               # noqa: E402

FAIL = []


def ok(name: str, cond: bool, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  " + str(detail)) if detail else ""))
    if not cond:
        FAIL.append(name)


def mk(cfg_over: "dict | None" = None, n_secrets: int = 12):
    """臨時桌 + n 則私訊給一個席位（回 root/home/cfg/svc/seat）。"""
    root, home, cfg = TS.mk_table(module="hyakki_tower", scenario="main", table_id="sw")
    cfg = json.loads(json.dumps(cfg))
    if cfg_over:
        cfg.update(cfg_over)
    svc = SVC.TableService(home, cfg)
    seat = svc.table.join(name="甲", kind="human")          # 不寫死席位 id：座位是 join 出來的
    for i in range(n_secrets):
        svc.table.secret(to=seat, text=f"私訊第{i}則", frm="gm")
    return root, home, cfg, svc, seat


def t_prompt() -> None:
    print("== A. 喚醒訊息：只列最近 N 則 + 說出還有幾則 ==")
    root, home, _cfg, svc, seat = mk(n_secrets=12)
    try:
        p = svc.kick_prompt(seat, reason="turn")
        ok("A1 預設窗口 8：只列最近 8 則", p.count("私訊第") == 8, p.count("私訊第"))
        ok("A2 列的是**最近**的（第4～11 則）",
           "私訊第11則" in p and "私訊第3則" not in p)
        ok("A3 明白寫出「更早的 4 則沒列在這裡」", "更早的 4 則" in p)
        ok("A4 給出去哪裡看的指示（table_view detail=full）", 'table_view' in p and 'full' in p)
        ok("A5 私訊窗口不影響投影本身",
           len(svc.table.view(seat=seat)["your_secrets"]) == 12)
    finally:
        TS.rm_root(root)

    # 逃生口：<= 0 ＝ 全部＝舊行為（自己照舊公式組一次比對，逐字節相同）
    root, home, _cfg, svc, seat = mk(cfg_over={"view": {"secret_window": 0}}, n_secrets=12)
    try:
        p = svc.kick_prompt(seat, reason="turn")
        old = "只有你知道：" + " / ".join(
            f"私訊第{i}則（来自 gm）" for i in range(12))
        ok("A6 secret_window:0 ⇒ 全部列出且與舊公式逐字節相同",
           old in p and "更早的" not in p and p.count("私訊第") == 12)
    finally:
        TS.rm_root(root)


def t_view() -> None:
    print("== B. 投影不裁；只有 compact 這一版收窗口 ==")
    root, home, _cfg, svc, seat = mk(n_secrets=12)
    try:
        full = svc.table.view(seat=seat)
        comp = svc.table.view(seat=seat, detail="compact")
        ok("B1 full 視圖：完整 12 則（網頁 messenger 靠這條）", len(full["your_secrets"]) == 12)
        ok("B2 full 視圖不帶 hidden 欄位", "your_secrets_hidden" not in full)
        ok("B3 compact 視圖：只 8 則", len(comp["your_secrets"]) == 8, len(comp["your_secrets"]))
        ok("B4 compact 視圖帶 your_secrets_hidden=4", comp.get("your_secrets_hidden") == 4)
        ok("B5 compact 視圖列的是最近那 8 則（seq 較大者）",
           comp["your_secrets"][-1]["text"] == "私訊第11則"
           and comp["your_secrets"][0]["text"] == "私訊第4則",
           [s["text"] for s in comp["your_secrets"]][:2])
        ok("B6 compact 視圖的 detail 旗標沒被弄壞", comp.get("detail") == "compact")
    finally:
        TS.rm_root(root)


def t_edge() -> None:
    print("== C. 邊界 ==")
    root, home, _cfg, svc, seat = mk(n_secrets=3)
    try:
        p = svc.kick_prompt(seat, reason="turn")
        ok("C1 私訊少於窗口 ⇒ 全部列出、不說「更早的」",
           p.count("私訊第") == 3 and "更早的" not in p)
    finally:
        TS.rm_root(root)

    root, home, _cfg, svc, seat = mk(cfg_over={"view": {"secret_window": "壞值"}}, n_secrets=12)
    try:
        ok("C2 設定寫壞（字串）⇒ 退回預設 8，不炸",
           svc.kick_prompt(seat).count("私訊第") == 8)
    finally:
        TS.rm_root(root)

    root, home, _cfg, svc, seat = mk(n_secrets=0)
    try:
        p = svc.kick_prompt(seat, reason="turn")
        ok("C3 沒有私訊 ⇒ 一個字都不加（不長出空行）", "只有你知道" not in p)
    finally:
        TS.rm_root(root)

    ok("C4 純函式：trim_secrets 不看輸入順序（亂序進來也取 seq 最大的 N 則）",
       [s["text"] for s in projection.trim_secrets(
           [{"seq": 9, "text": "c"}, {"seq": 1, "text": "a"}, {"seq": 5, "text": "b"}],
           {"view": {"secret_window": 2}})[0]] == ["b", "c"])


def main() -> int:
    print("test_secret_window.py — 私訊窗口（view.secret_window）")
    t_prompt()
    t_view()
    t_edge()
    print()
    print("結果: %s  （%d 失敗）" % ("全部通過" if not FAIL else "、".join(FAIL), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
