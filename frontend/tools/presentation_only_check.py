#!/usr/bin/env python3
"""「只改呈現」diff 守門：設計改造的每一顆 commit／PR 都要過這一關。

    python3 tools/presentation_only_check.py                  # 在 repo 根目錄跑；比 origin/main...HEAD 的 web/
    python3 tools/presentation_only_check.py --base main      # 換比較基準
    python3 tools/presentation_only_check.py --worktree       # 連還沒 commit 的改動一起看（開發中用）
    python3 tools/presentation_only_check.py --boundary       # 另外檢查「別人的地盤」有沒有被碰（見下）

規則（任何一條中了 ⇒ exit 1）：對 `git diff -U0 <base>...HEAD -- web/`，**新增或刪除**的行裡
  ① 出現 `gql(`／`fetch(`／`api(`／`_ROUTE`／`setInterval`／`TX(`（子字串比對，**但先剝註解**：
     `//` 行註解與 `/* */` 區塊註解不算，字串與模板原樣保留——跟 tools/check_tx_private.py 同一套；
     剝註解是對「整份檔」做，所以跨行的區塊註解中間那幾行也不會誤殺）
  ② 出現 `V =` 賦值（`V=`、`let V = …`、`if (…) V = …`、`{ V = …` 都算；
     `==`／`===`／`!==`／`>=`／`<=`／`=>`、`.V =`、物件字面裡的 `V =` 不算）
  ③ 出現 `location.`／`history.`（含 `window.`／`document.` 前綴、`location[…]`、`location = …`）——
     讀 `?seat=` 之類是邏輯不是呈現；網址與歷史的增刪一律拆出去給家側審

⚠ 本檔自己的說明文字也含這些字，所以它不在 web/ 之下，不會被掃到。

--boundary（可選，預設不跑，免得家側自己的 PR 被誤傷）：另外檢查整個 diff
  ③ 沒有動 `mock/`、`tools/grab_fixtures.py`、`fixtures/`、`docs/API.md`
  ④ `web/index.html` 的第 2 行（部署標記）、`<meta name="trpg-ui">`、CSP 那顆 `<meta http-equiv="Content-Security-Policy"…>`
     在 base 與 HEAD 一字不差

純標準庫。exit 0＝綠、1＝紅、2＝跑不起來（例如找不到 base）。
"""
import argparse
import re
import subprocess
import sys

TOKENS = ["gql(", "fetch(", "api(", "_ROUTE", "setInterval", "TX("]
# 賦值只認「語句開頭」：行首、或接在 ; { } ) else let var const 之後。
# 不能只認行首（`^\s*V\s*=`）——這份程式裡 `if(r.view) V = r.view;`、`{ V = r.view; …}` 才是常態，行首錨會整個漏掉。
# 不認 `==`／`===`／`!==`／`>=`／`<=`／`=>`（`=` 後面不能再接 `=` 或 `>`），也不認 `.V =`、`, V =`、`: V =`。
V_ASSIGN = re.compile(r"(?:^|[;{})]|\belse\b|\b(?:let|var|const)\b)\s*V\s*=(?![=>])")
# location.／history.（含 window./document./globalThis. 前綴；location[…]、location = …）。前面是 \w $ . 的不算（chatHistory.、a.history.）。
LOC_HIST = re.compile(
    r"(?:(?<![\w$.])|(?<=window\.)|(?<=document\.)|(?<=globalThis\.))"
    r"(?:location\s*(?:\.|\[|=(?![=>]))|history\s*(?:\.|\[))")
FORBIDDEN_PATHS = ["mock/", "tools/grab_fixtures.py", "fixtures/", "docs/API.md"]
HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def git(*args, check=True):
    r = subprocess.run(["git", "-c", "core.quotepath=off", *args], capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        if not check:
            return None
        sys.stderr.write("git %s 失敗：%s\n" % (" ".join(args), r.stderr.strip()))
        sys.exit(2)
    return r.stdout


def strip_comments(s):
    """去掉 // 行註解與 /* */ 區塊註解（字串／模板字面量原樣保留）。

    抄自 tools/check_tx_private.py 開頭那兩行 re.sub（同一套，兩支閘對「什麼算註解」不會吵架）；
    差一處：區塊註解換成「同樣多的空白，但換行留著」，這樣行號對得上 diff 的行號。
    已知限制（兩支閘共有）：字串裡的 `//`（例如 "https://…"）會被當成行註解起點，同一行後半不被掃到。"""
    s = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), s, flags=re.S)
    return re.sub(r"//[^\n]*", "", s)


class Side:
    """diff 的一側（舊／新）：檔案內容剝註解後的行，按需讀、有快取。"""

    def __init__(self, reader):
        self.reader, self.cache = reader, {}

    def line(self, path, n):
        if path not in self.cache:
            text = self.reader(path)
            self.cache[path] = strip_comments(text).split("\n") if text is not None else None
        ls = self.cache[path]
        return ls[n - 1] if ls is not None and 0 < n <= len(ls) else None


def rules_hit(code):
    why = [t for t in TOKENS if t in code]
    if V_ASSIGN.search(code):
        why.append("V =")
    if LOC_HIST.search(code):
        why.append("location./history.")
    return why


def scan(diff, old, new):
    """回傳 [(檔, '+'|'-', 行號, 命中的規則, 行文字)]。

    命中與否看「剝掉註解後」的那一行（從整份檔取，所以跨行 /* */ 中間的行也算註解）；
    顯示用的還是 diff 裡的原文。"""
    hits, old_path, new_path, old_n, new_n, in_hdr = [], "", "", 0, 0, False
    for raw in diff.splitlines():
        if raw.startswith("diff --git "):
            in_hdr = True
            continue
        if in_hdr:                                    # 檔頭（index／---／+++）只到第一個 @@ 為止
            if raw.startswith("--- "):
                old_path = raw[4:].removeprefix("a/")
                continue
            if raw.startswith("+++ "):
                new_path = raw[4:].removeprefix("b/")
                continue
            if not raw.startswith("@@"):
                continue
            in_hdr = False
        m = HUNK.match(raw)
        if m:
            old_n, new_n = int(m.group(1)), int(m.group(2))
            continue
        if raw.startswith("+") or raw.startswith("-"):
            sign, body = raw[0], raw[1:]
            if sign == "+":
                code, path, n = new.line(new_path, new_n), new_path, new_n
                new_n += 1
            else:
                code, path, n = old.line(old_path, old_n), old_path, old_n
                old_n += 1
            if code is None:                          # 讀不到檔（理論上不會）⇒ 保守退回原行，不放過
                code = body
            why = rules_hit(code)
            if why:
                hits.append((path, sign, n, "、".join(why), body.strip()))
    return hits


def boundary(base, worktree):
    bad = []
    spec = [base] if worktree else ["%s...HEAD" % base]
    names = git("diff", "--name-only", *spec).split()
    for n in names:
        if any(n == p or (p.endswith("/") and n.startswith(p)) for p in FORBIDDEN_PATHS):
            bad.append("動到別人的地盤：%s" % n)

    def protected(text):
        ls = text.splitlines()
        out = {"第 2 行（部署標記）": ls[1] if len(ls) > 1 else ""}
        for l in ls:
            if '<meta name="trpg-ui"' in l:
                out["meta trpg-ui"] = l
            if "Content-Security-Policy" in l and "<meta" in l:
                out["CSP meta"] = l
        return out

    before = protected(git("show", "%s:web/index.html" % base))
    if worktree:
        with open("web/index.html", encoding="utf-8") as fh:
            after = protected(fh.read())
    else:
        after = protected(git("show", "HEAD:web/index.html"))
    for k in sorted(set(before) | set(after)):
        if before.get(k) != after.get(k):
            bad.append("被改動：%s\n      base : %s\n      HEAD : %s" % (k, before.get(k), after.get(k)))
    return bad


def main():
    ap = argparse.ArgumentParser(description="「只改呈現」diff 守門")
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--worktree", action="store_true", help="含未 commit 的改動（比 base 與工作樹）")
    ap.add_argument("--boundary", action="store_true", help="另外檢查別人的地盤與受保護的三處")
    a = ap.parse_args()

    spec = [a.base] if a.worktree else ["%s...HEAD" % a.base]
    diff = git("diff", "-U0", "--no-renames", *spec, "--", "web/")
    # 舊側＝git diff 實際比的那一側：`A...B` 比的是 merge-base(A,B)；`--worktree` 比的是 A 本身
    old_rev = a.base if a.worktree else git("merge-base", a.base, "HEAD").strip()

    def read_new(path):
        if a.worktree:
            try:
                with open(path, encoding="utf-8") as fh:
                    return fh.read()
            except OSError:
                return None
        return git("show", "HEAD:%s" % path, check=False)

    hits = scan(diff, Side(lambda p: git("show", "%s:%s" % (old_rev, p), check=False)), Side(read_new))
    desc = "%s（含工作樹）" % a.base if a.worktree else "%s...HEAD" % a.base
    print("比較：%s  --  web/" % desc)
    n_add = sum(1 for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))
    n_del = sum(1 for l in diff.splitlines() if l.startswith("-") and not l.startswith("---"))
    print("diff：+%d / -%d 行" % (n_add, n_del))

    failed = False
    if hits:
        failed = True
        print("\n✗ 不是「只改呈現」——下列行（剝註解後）含守門字串（%s）、`V =` 賦值或 `location.`／`history.`：" % " ".join(TOKENS))
        for path, sign, n, why, text in hits:
            print("  %s:%d %s [%s] %s" % (path, n, sign, why, text[:140]))
    else:
        print("✓ 沒有 %s 的增刪（註解不算），也沒有 `V =` 賦值、`location.`／`history.`" % " ".join(TOKENS))

    if a.boundary:
        bad = boundary(a.base, a.worktree)
        if bad:
            failed = True
            print("\n✗ 邊界：")
            for b in bad:
                print("  " + b)
        else:
            print("✓ 邊界：沒動 %s；第 2 行／trpg-ui／CSP 三處一字未動" % "、".join(FORBIDDEN_PATHS))

    print("\n結果：%s" % ("FAIL" if failed else "PASS"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
