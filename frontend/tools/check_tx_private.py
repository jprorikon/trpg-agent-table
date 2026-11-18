#!/usr/bin/env python3
"""翻譯外送的私密防線：靜態檢查（跑在 repo 根目錄）。

守四條不變式：
  A. 沒有任何 TX()/sysTX() 呼叫點的引數直接引用私密欄位（you.*、your_*、seen_by_you、your_secrets）——
     私密區只能靠 HTML 跳脫顯示，不能進翻譯。
  B. 送出面（trWant）有掛私密字串遮罩 trHasPriv。
  C. wish 宣言的 note（action_declared, action=wish）不經過 TX。
  F. 私訊直送授權（TX 第三引數 dm）只能是白名單常數 DM_TX_OK，且全檔恰好一處——
     dm 是執行期旗標，靜態閘看不到「誰該傳」，所以把授權本身做成原始碼裡有形狀的東西：
     要新增第二條私訊管線，必須動白名單（= 被本閘擋下、得有人簽名）。
"""
import re, sys, pathlib

p = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "web/index.html")
s = p.read_text(encoding="utf-8")
# 去掉 // 行註解與 /* */ 區塊註解，避免拿註解當證據（字串／模板字面量原樣保留）
code = re.sub(r"/\*.*?\*/", lambda m: " " * len(m.group(0)), s, flags=re.S)
code = re.sub(r"//[^\n]*", "", code)

fail = []


def _arg_list(src, open_paren_end):
    """從 '(' 之後開始，回傳頂層括號內的引數字串。"""
    i = open_paren_end
    depth = 1
    j = i
    while j < len(src) and depth:
        if src[j] == "(":
            depth += 1
        elif src[j] == ")":
            depth -= 1
        j += 1
    return src[i:j - 1]


def _split_top(argstr):
    """依頂層逗號切引數（忽略字串與巢狀括號內的逗號）。"""
    out, buf, depth, q = [], [], 0, None
    i = 0
    while i < len(argstr):
        c = argstr[i]
        if q:
            buf.append(c)
            if c == "\\":
                i += 1
                if i < len(argstr):
                    buf.append(argstr[i])
            elif c == q:
                q = None
        elif c in "\"'`":
            q = c
            buf.append(c)
        elif c in "([{":
            depth += 1
            buf.append(c)
        elif c in ")]}":
            depth -= 1
            buf.append(c)
        elif c == "," and depth == 0:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(c)
        i += 1
    out.append("".join(buf))
    return out


def _line_of(src, pos):
    return src.count("\n", 0, pos) + 1


# A. TX/sysTX 的引數不得引用私密欄位
PRIV = re.compile(r"\b(?:V\.|P_?\.)?(?:you\b|your_[a-z_]+|seen_by_you|your_secrets|your_goal|your_declarations)")
# 只要「呼叫點」：排除 function TX(...) 這種宣告
TX_CALLS = [m for m in re.finditer(r"\b(?:sys)?TX\s*\(", code)
            if not re.search(r"function\s+$", code[max(0, m.start() - 12):m.start()])]
hits = []
for m in TX_CALLS:
    arg = _arg_list(code, m.end())
    if PRIV.search(arg):
        hits.append((_line_of(code, m.start()), arg.strip()[:80]))
if hits:
    fail.append("A. TX() 引數引用私密欄位：%s" % hits)

# B. 遮罩要真的掛在送出面
if "trHasPriv" not in code:
    fail.append("B. 找不到 trHasPriv（私密字串遮罩）")
elif not re.search(r"trWant\s*=\s*[^\n]*trHasPriv", code):
    fail.append("B. trWant 沒有掛 trHasPriv（遮罩沒接上送出面）")

# C. wish 宣言不翻
if not re.search(r'ev\.action\s*===\s*"wish"\s*\)\s*return\s+esc\(', code):
    fail.append('C. actNote 沒有擋 action==="wish"')

# F. 私訊授權（dm）＝白名單常數，全檔只准一處
#    只在「本檔已存在 dm 縱軸」時啟用 ⇒ 閘可以先落 main（空轉），一旦有人引入 dm 縱軸就自動生效。
DM_CONST = "DM_TX_OK"
_args_of = {m.start(): _split_top(_arg_list(code, m.end())) for m in TX_CALLS}
has_dm_axis = bool(re.search(r"\bconst\s+%s\s*=\s*true\s*;" % DM_CONST, code)) or \
    any(len(a) >= 3 for a in _args_of.values())

if not has_dm_axis:
    print("· F 略過：本檔尚無 dm 縱軸（沒有 TX 第三引數、也沒有 %s）" % DM_CONST)
else:
    decl = re.findall(r"\bconst\s+%s\s*=\s*true\s*;" % DM_CONST, code)
    if len(decl) != 1:
        fail.append("F1. 需要恰好一次 `const %s = true;`（實際 %d 次）" % (DM_CONST, len(decl)))

    dm_calls, bad_dm = [], []
    for m in TX_CALLS:
        args = _args_of[m.start()]
        if len(args) < 3:
            continue
        third = args[2].strip()
        if third == DM_CONST:
            dm_calls.append(_line_of(code, m.start()))
        else:
            bad_dm.append((_line_of(code, m.start()), third[:40]))
    if bad_dm:
        fail.append("F2. TX() 第三引數（dm）只允許 %s，出現其他值：%s" % (DM_CONST, bad_dm))
    if len(dm_calls) != 1:
        fail.append("F3. 帶 %s 的呼叫點應恰好 1 處（實際 %d 處：%s）" % (DM_CONST, len(dm_calls), dm_calls))

assert_meta = '<meta name="trpg-ui"' in s
if not assert_meta:
    fail.append('D. 缺穩定標記 <meta name="trpg-ui" content="...">（部署閘認這個，不認 DOM id）')
if not re.search(r"<!--\s*trpg-frontend ver:\s*\d{8}-\d{4}", s):
    fail.append("E. 缺版本戳 <!-- trpg-frontend ver: YYYYMMDD-HHMM -->")

for f in fail:
    print("✗", f)
print("結果：", "PASS" if not fail else "FAIL（%d 條）" % len(fail))
sys.exit(1 if fail else 0)
