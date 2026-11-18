#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 `assets/index.json` **按类拆开**（設計者 2026-10-19：「index 把角色物品啥的全都包进去了，是不是应该按类拆分」）。

拆完的形状（對外契約見 `trpg/frontend` 的 `docs/API.md` §2.9）：

```
assets/
  index.json          ← 瘦清單（schema 2）：只放 module／generated_at／source／parts／counts
  index/portrait.json ← 立繪（100 檔／5 條別名）
  index/avatar.json   ← 頭像
  index/bg.json       ← 場景
  index/crowd.json    ← 路人
  index/icon.json     ← 圖示（道具／神器／技能／星等／狀態／小怪）
```

每一份 part 自己就是**舊的 schema 1 形狀**（`files`＋`aliases`，只含這一類）⇒ 想直接讀某一類的人
（GM 腳本、素材盤點）不必先載整個 index；前端只多一步「照 manifest 的 `parts` 逐份抓再合併」。

## 用法

```bash
python3 tools/assets_index.py split              # 依現有 index.json 重新分組（可重複跑，idempotent）
python3 tools/assets_index.py check              # 校驗：manifest ↔ parts ↔ 別名指向（CI/交付前跑）
python3 tools/assets_index.py split --dry-run    # 只印會怎麼分，不寫檔
```

改素材的流程不變（`_assets_build.py --index-only` 會重寫一份 schema 1 的 `index.json`），
寫完**再跑一次 `split`** 就是新形狀；本工具也會吃舊的 schema 1，所以兩個方向都通。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(os.path.dirname(HERE), "assets")     # `<模組>/assets`
MANIFEST = os.path.join(ASSETS, "index.json")
PART_DIR = "index"                                          # 相對 assets/
SCHEMA_PART = 1                                             # part 用舊形狀（files／aliases）
SCHEMA_MANIFEST = 2                                         # manifest 只指路

#: 固定分類與順序（新類別要一起加在這裡；`check` 會咬「有 part 沒登記」）
KINDS = ("portrait", "avatar", "npc", "enemy", "bg", "crowd", "icon")

DOC = ("素材清單（schema 2）：本檔只放索引，**內容按類拆在 `index/<類>.json`**。"
       "前端照 `parts` 逐份抓再合併；`source`／`missing` 留在這裡。")


# ---------------------------------------------------------------------------
# 讀寫
# ---------------------------------------------------------------------------


def _read_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _write_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1, sort_keys=False)
        fh.write("\n")
    os.replace(tmp, path)


def load_flat(assets_dir: str = ASSETS) -> dict:
    """把「不論現在是 schema 1 還是 2」的素材清單讀成一份 flat 的 `{files, aliases, ...}`。

    schema 1 ⇒ 原樣；schema 2 ⇒ 讀 manifest ＋它指到的每一份 part 再合併（缺 part ⇒ 當壞檔）。
    """
    man = _read_json(os.path.join(assets_dir, "index.json"))
    schema = man.get("schema")
    if schema == SCHEMA_PART:
        return man
    if schema != SCHEMA_MANIFEST:
        raise SystemExit(f"index.json 的 schema={schema!r} 不認得（只吃 1 或 2）")
    files: dict = {}
    aliases: dict = {}
    for kind, rel in (man.get("parts") or {}).items():
        path = os.path.join(assets_dir, *str(rel).split("/"))
        if not os.path.isfile(path):
            raise SystemExit(f"manifest 指到的 part 不見了：{rel}（{kind}）")
        part = _read_json(path)
        files.update(part.get("files") or {})
        aliases.update(part.get("aliases") or {})
    out = {"schema": SCHEMA_PART, "module": man.get("module"), "files": files, "aliases": aliases,
           "source": man.get("source") or {}, "missing": man.get("missing") or []}
    if man.get("generated_at"):
        out["generated_at"] = man["generated_at"]
    return out


def _kind_of_file(key_or_path: str, files: dict) -> str:
    """一筆 `files` 的 key 或路徑 ⇒ 類別（＝路徑第一段；認不得 ⇒ `icon`）。"""
    entry = files.get(key_or_path) or {}
    path = str(entry.get("path") or key_or_path)
    head = path.split("/", 1)[0]
    return head if head in KINDS else "icon"


# ---------------------------------------------------------------------------
# split
# ---------------------------------------------------------------------------


def split(assets_dir: str = ASSETS, *, dry_run: bool = False) -> dict:
    flat = load_flat(assets_dir)
    files: dict = flat.get("files") or {}
    aliases: dict = flat.get("aliases") or {}
    groups = {k: {"files": {}, "aliases": {}} for k in KINDS}
    for key, entry in files.items():
        groups[_kind_of_file(key, files)]["files"][key] = entry
    for alias, target in aliases.items():
        groups[_kind_of_file(target, files)]["aliases"][alias] = target

    # 空類別不寫檔（模組沒有路人就沒有 crowd.json——manifest 也不列）
    active = [k for k in KINDS if groups[k]["files"] or groups[k]["aliases"]]
    stamp = _dt.datetime.now().astimezone().replace(microsecond=0).isoformat()
    module = flat.get("module") or os.path.basename(os.path.dirname(assets_dir))

    manifest = {
        "schema": SCHEMA_MANIFEST,
        "module": module,
        "generated_at": stamp,
        "doc": DOC,
        "parts": {k: f"{PART_DIR}/{k}.json" for k in active},
        "counts": {k: {"files": len(groups[k]["files"]), "aliases": len(groups[k]["aliases"])}
                   for k in active},
        "totals": {"files": sum(len(groups[k]["files"]) for k in active),
                   "aliases": sum(len(groups[k]["aliases"]) for k in active)},
        "source": flat.get("source") or {},
        "missing": flat.get("missing") or [],
    }
    if dry_run:
        for k in active:
            print(f"  {k:<9} files={len(groups[k]['files']):>4}  aliases={len(groups[k]['aliases']):>4}")
        print(f"  => index.json（schema 2，parts {len(active)} 份）")
        return manifest

    for k in active:
        _write_json(os.path.join(assets_dir, PART_DIR, f"{k}.json"),
                    {"schema": SCHEMA_PART, "kind": k, "module": module,
                     "count": {"files": len(groups[k]["files"]),
                               "aliases": len(groups[k]["aliases"])},
                     "files": groups[k]["files"], "aliases": groups[k]["aliases"]})
    # 舊的 part 檔（這一類已經空了）順手清掉，別留孤兒
    pdir = os.path.join(assets_dir, PART_DIR)
    if os.path.isdir(pdir):
        for name in sorted(os.listdir(pdir)):
            if name.endswith(".json") and name[:-5] not in active:
                os.remove(os.path.join(pdir, name))
    _write_json(os.path.join(assets_dir, "index.json"), manifest)
    return manifest


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


def check(assets_dir: str = ASSETS, *, strict_files: bool = False) -> int:
    """校驗 manifest ↔ parts ↔ 別名指向。回傳失敗數（0＝過）。"""
    bad = 0
    man = _read_json(os.path.join(assets_dir, "index.json"))
    if man.get("schema") != SCHEMA_MANIFEST:
        print(f"✗ index.json 不是 schema {SCHEMA_MANIFEST}（現在 {man.get('schema')!r}）——先跑 split")
        return 1
    parts = man.get("parts") or {}
    files: dict = {}
    aliases: dict = {}
    for kind, rel in parts.items():
        path = os.path.join(assets_dir, *str(rel).split("/"))
        if not os.path.isfile(path):
            print(f"✗ 缺 part：{rel}"); bad += 1; continue
        part = _read_json(path)
        if part.get("schema") != SCHEMA_PART or part.get("kind") != kind:
            print(f"✗ {rel}：schema／kind 不對（{part.get('schema')!r}／{part.get('kind')!r}）"); bad += 1
        for key, entry in (part.get("files") or {}).items():
            head = str(entry.get("path") or "").split("/", 1)[0]
            if head != kind:
                print(f"✗ {rel}：{key} 的路徑屬於 {head!r}（放錯類）"); bad += 1
            if key in files:
                print(f"✗ {key} 同時出現在兩份 part"); bad += 1
            files[key] = entry
        for alias, target in (part.get("aliases") or {}).items():
            if target not in (part.get("files") or {}):
                print(f"✗ {rel}：別名 {alias} 指向自己這份裡沒有的 key {target}"); bad += 1
            if alias in aliases:
                print(f"✗ 別名 {alias} 重複"); bad += 1
            aliases[alias] = target
    for kind, counts in (man.get("counts") or {}).items():
        part = _read_json(os.path.join(assets_dir, PART_DIR, f"{kind}.json")) if kind in parts else {}
        if len(part.get("files") or {}) != counts.get("files") or \
                len(part.get("aliases") or {}) != counts.get("aliases"):
            print(f"✗ counts 與 {kind}.json 對不上（manifest {counts}）"); bad += 1
    if (man.get("totals") or {}).get("files") != len(files):
        print(f"✗ totals.files {man.get('totals', {}).get('files')} ≠ 實際 {len(files)}"); bad += 1
    if strict_files:
        for key, entry in files.items():
            p = os.path.join(assets_dir, *str(entry.get("path") or "").split("/"))
            if not os.path.isfile(p):
                print(f"⚠ 檔不在磁碟（素材不進版控時正常）：{key} → {entry.get('path')}")
    summary = ", ".join(f"{k} {v['files']}" for k, v in (man.get("counts") or {}).items())
    if bad:
        print(f"✗ {bad} 個問題")
        return bad
    print(f"✓ index: {len(parts)} 類／{len(files)} 檔／{len(aliases)} 別名（{summary}）")
    return 0


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(description="assets/index.json 按類拆分／校驗")
    ap.add_argument("--assets", default=ASSETS, help=f"assets 目錄（預設 {ASSETS}）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("split")
    sp.add_argument("--dry-run", action="store_true")
    ck = sub.add_parser("check")
    ck.add_argument("--strict-files", action="store_true", help="連素材檔是否存在一起檢查")
    args = ap.parse_args(argv)
    if args.cmd == "split":
        man = split(args.assets, dry_run=args.dry_run)
        if not args.dry_run:
            print(f"✓ 拆好了：{len(man['parts'])} 類／{man['totals']['files']} 檔／"
                  f"{man['totals']['aliases']} 別名 → {os.path.join(args.assets, 'index.json')}")
        return 0
    return 1 if check(args.assets, strict_files=args.strict_files) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
