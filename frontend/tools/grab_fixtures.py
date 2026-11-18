#!/usr/bin/env python3
"""从真桌抓一份 fixtures（自带**脱敏**：把私密串换成占位符）。

    python3 tools/grab_fixtures.py http://127.0.0.1:8971 [p1_seat] [agent_seat]

脱敏规则：`you.wish` / `your_goal` / `your_secrets` / `your_fragments` / `seen_by_you`
以及结算表里的每个人 `goal`——这些原文在写文件前会被整体替换（连日志里出现过的也一起替换）。
"""
import json
import os
import sys
import urllib.request

SAN = "（示例：此处是私密内容，已在对外仓库里替换）"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FX = os.path.join(ROOT, "fixtures")


def get(base, path):
    with urllib.request.urlopen(base + path, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def secrets_of(views):
    out = set()
    for v in views:
        you = v.get("you") or {}
        for src in (you.get("wish") or {}, v.get("your_goal") or {}):
            for k in ("title", "text"):
                if src.get(k):
                    out.add(src[k])
        for k in ("your_secrets", "your_fragments", "seen_by_you"):
            for it in (v.get(k) or []):
                if it.get("text"):
                    out.add(it["text"])
        for row in ((v.get("settled") or {}).get("rows") or []):
            if row.get("goal"):
                out.add(row["goal"])
    return out


def main():
    base = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8971").rstrip("/")
    p1 = sys.argv[2] if len(sys.argv) > 2 else "p1"
    ag = sys.argv[3] if len(sys.argv) > 3 else "p3"
    views = {n: get(base, f"/api/state?seat={s}") for n, s in
             (("p1", p1), ("sp", None), ("p3", ag))}
    sec = secrets_of(views.values())
    scrub = lambda o: json.loads(_replace(json.dumps(o, ensure_ascii=False), sec))
    files = {
        "state_p1.json": {k: v for k, v in views["p1"].items() if k != "gm"},
        "state_spectator.json": {k: v for k, v in views["sp"].items() if k != "gm"},
        "state_agent.json": {k: v for k, v in views["p3"].items() if k != "gm"},
        "map.json": get(base, "/api/map"),
        "log.json": {"events": get(base, "/api/log")["events"][-400:]},
    }
    os.makedirs(FX, exist_ok=True)
    for name, obj in files.items():
        with open(os.path.join(FX, name), "w", encoding="utf-8") as fh:
            json.dump(scrub(obj), fh, ensure_ascii=False, indent=1)
        print("写出", name)
    print(f"脱敏：替换了 {len(sec)} 个私密串")


def _replace(txt, sec):
    for s in sec:
        txt = txt.replace(s, SAN)
    return txt


if __name__ == "__main__":
    main()
