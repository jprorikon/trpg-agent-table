#!/usr/bin/env python3
"""**音訊表自檢**——模組作者改完 `audio.json`／放完音檔先跑這一支。

    python3 tools/check_audio.py                # 所有模組（modules/*）
    python3 tools/check_audio.py hyakki_tower    # 只驗一個模組

判據與**跑團時一模一樣**（直接借 `trpg/service/audio.py` 那一支，不是另一套規則）：

* 別名有 `bgm:`／`sfx:` 前綴；`path` 是合規的音檔相對路徑（防穿越、副檔名白名單）；
* `cues` 指到的別名真的存在；
* 音檔**真的在磁碟上**（`assets/audio/…`）——表指著一個不存在的檔，跑起來就是靜音；
* 同一張表裡別名不重複（JSON 物件本身不可能重複；這裡看的是「前綴與 kind 一致」）。

exit code：0＝沒有 warning；1＝有 warning（印出來）；2＝用法錯。
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from trpg.service import audio as audio          # noqa: E402


def check_module(root: str, module: str) -> "tuple[int, list[str]]":
    """回 `(音軌數, 問題清單)`；沒有 `audio.json` ＝（0, []）＝這一模組沒聲音（合法）。"""
    a = audio.load(root, module)
    if not a.path or not os.path.isfile(a.path):
        return 0, []
    problems: list = list(a.warnings())
    for key in a.keys():
        row = a.track(key) or {}
        if not a.url(key):
            problems.append(f"{key}: 音檔不在磁碟上（{row.get('path')}）")
    return len(a.tracks()), problems


def main() -> int:
    root = ROOT
    mods = ([sys.argv[1]] if len(sys.argv) > 1
            else sorted(d for d in os.listdir(os.path.join(root, "modules"))
                        if os.path.isdir(os.path.join(root, "modules", d))))
    bad = 0
    for m in mods:
        n, problems = check_module(root, m)
        if not n and not problems:
            print(f"  --   {m}: 沒有音訊表（沒有 BGM／效果音）")
            continue
        if problems:
            bad += 1
            print(f"  NG   {m}: {n} 首，{len(problems)} 條問題")
            for p in problems:
                print(f"         - {p}")
        else:
            a = audio.load(root, m)
            print(f"  OK   {m}: {n} 首（cues {len(a.cues())} 條）")
    print()
    print("AUDIO TABLE: OK" if not bad else f"AUDIO TABLE: {bad} 個模組有問題")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
