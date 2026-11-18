#!/usr/bin/env python3
"""檢查 web/index.html 的三語字典（I18N）完整性。

用法：
    python3 tools/check_i18n.py          # 在 repo 根目錄跑
    python3 tools/check_i18n.py -v       # 也列出「字典有但程式沒引用」的鍵

檢查三件事：
  ① 三語（zh-Hans / zh-Hant / ja）互缺的鍵 —— 有缺就 exit 1
  ② 程式用到（含 HTML 的 data-i18n="…"、data-i18n-ph／-title／-aria="…"）但字典沒有的鍵 —— 有缺就 exit 1
  ③ 字典有但沒被引用的鍵 —— 只提示，不算失敗（可能是動態鍵）

純標準庫；不需要起伺服器。
"""
import re
import sys

PATH = "web/index.html"


def block(text, start):
    """從 text[start] 附近的 { 起做括號配對，回傳括號內內容。"""
    i = text.index("{", start)
    depth, j = 0, i
    while j < len(text):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[i + 1:j]
        j += 1
    raise ValueError("括號沒有配對起來")


def main():
    verbose = "-v" in sys.argv
    src = open(PATH, encoding="utf-8").read()

    m = re.search(r"const\s+I18N\s*=\s*\{", src)
    if not m:
        print("找不到 const I18N = { … }")
        return 2
    body = block(src, m.start())
    langs = {}
    for lm in re.finditer(r'"([a-zA-Z\-]+)"\s*:\s*\{', body):
        langs[lm.group(1)] = set(re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*[\"'`]", block(body, lm.end() - 1)))
    if not langs:
        print("I18N 裡找不到任何語言區塊")
        return 2

    print("語言：", {k: len(v) for k, v in langs.items()})
    union = set().union(*langs.values())
    names = list(langs)
    failed = False

    gaps = [(k, [n for n in names if k not in langs[n]]) for k in sorted(union)]
    gaps = [(k, miss) for k, miss in gaps if miss]
    print("\n① 三語互缺：")
    if gaps:
        failed = True
        for k, miss in gaps:
            print(f"  {k:22s} 缺：{', '.join(miss)}")
    else:
        print("  （三語齊全）")

    # 程式引用：t("key") 與 HTML 的 data-i18n="key"；t("前綴_" + 變數) 視為動態前綴（字典須至少有一個該前綴的鍵）
    call = r"""(?<![A-Za-z0-9_$.])[tL]\(\s*["']([A-Za-z_][A-Za-z0-9_]*)["']"""
    dyn = set(re.findall(call + r"\s*\+", src))
    used = set(m for m in re.findall(call + r"(?!\s*\+)", src))
    used |= set(re.findall(r'data-i18n(?:-ph|-title|-aria)?="([A-Za-z_][A-Za-z0-9_]*)"', src))   # 屬性版（placeholder／title／aria-label）也算引用
    missing = sorted(used - union) + sorted(p + "…（動態前綴）" for p in dyn if not any(k.startswith(p) for k in union))
    print(f"\n② 程式用到 {len(used)} 鍵 + {len(dyn)} 個動態前綴、字典 {len(union)} 鍵")
    if missing:
        failed = True
        print("  用到但字典沒有：", ", ".join(missing))
    else:
        print("  用到但字典沒有：（無）")

    # 「引用」＝t("k") / 動態前綴 / 程式裡出現過同名的字串字面量（例如 return "why_move" 之後才 t(w)）
    lits = set(re.findall(r"""["'`]([A-Za-z_][A-Za-z0-9_]*)["'`]""", src[:src.index("const I18N")] + src[src.index("const LANGS"):]))
    covered = used | lits | {k for k in union if any(k.startswith(p) for p in dyn)}
    unused = sorted(union - covered)
    print(f"\n③ 字典有但沒被引用：{len(unused)} 個" + (f" -> {', '.join(unused)}" if verbose else "（-v 可列出）"))

    print("\n結果：", "FAIL" if failed else "PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
