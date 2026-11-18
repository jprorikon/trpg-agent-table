#!/usr/bin/env python3
"""前端上线前的**静态闸**：不合格就别上线（返回非 0，并打印原因）。

    python3 deploy/check_frontend.py <index.html>

检查项都来自真实踩过的坑（见 trpg-agent-table 技能第 11 条）：
裸 `n(` 会让整页抛 ReferenceError、缺钩子会让测试/功能静默失效、
标签不成对会导致页面半截。
"""
import os
import re
import sys

# 必须在页面里出现的钩子（引擎/测试/功能依赖它们）
NEED = [
    "id=\"boardbox\"", "can_go", "id=\"storycard\"", "id=\"saybar\"", "id=\"themebtn\"",
    "trpg_seat", "trpg_lang", "trpg_theme", "/api/state", "/api/act", "/api/say",
    # 行动参数面板（PR #9 起：按钮式，不再用 <select id="a1">／<input id="a2">）。
    # 保留「give/show 的目标与碎片来自两个独立控件」这条守护的等价钩子。
    'class="argbox"', "data-k=", 'data-v="note"', "data-frag",
    '"zh-Hant"', '"zh-Hans"', '"ja"', "const NM = id =>",
    "trpg_mapopen", "trpg_howto_seen",   # 本机记忆的偏好键：缺了说明拿错文件
]
MIN_BYTES = 20_000
# 大廳（`web/lobby.html`，2026-10-19 改版）：資料全走根 `/graphql`；憑證與桌面共用 `trpg_token`、
# 偏好鍵同一組（語言／主題）——缺了＝拿錯檔或登入不通。
NEED_LOBBY = [
    '"/graphql"', "trpg_token", "trpg_lang", "trpg_theme", "modules {", "me {",
    "createTable", '"zh-Hant"', '"zh-Hans"', '"ja"',
]


def _last_nonspace(out: list, j: int) -> str:
    for k in range(j - 1, -1, -1):
        if out[k] not in " \t\r\n":
            return out[k]
    return ""


def code_only(text: str) -> str:
    """把 `//` 行注释与 `/* */` 块注释挖空（保留换行、长度不变）。

    为什么需要：裸 n( 的检查是**纯字面**的，注释里写「n(失敗次數)」这类说明会被误判成
    真的调用（2026-10-05 PR #9 实测）。只动注释——字符串/模板字面量/正则字面量**原样保留**，
    所以模板字面量 ${…} 里藏的真调用照样查得到（字符串里恰好写 n( 会误报，可接受）。

    字符串/正则都用最朴素的「跳到收尾引号」扫描：够用就好（这段只是为了让 // 不被误当注释）。
    """
    out = []
    i, n = 0, len(text)
    quote = ""
    regex_prev = set("(,=:[!&|?{};+-*%^~<>")     # 这些字符之后出现的 / 当正则字面量开头
    while i < n:
        c = text[i]
        if quote:
            out.append(c)
            if c == "\\" and i + 1 < n:      # 转义：连下一个字符一起原样带走
                out.append(text[i + 1])
                i += 2
                continue
            if c == quote:
                quote = ""
            i += 1
            continue
        nxt = text[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            j = text.find("\n", i)
            if j < 0:
                j = n
            out.append(" " * (j - i))
            i = j
        elif c == "/" and nxt == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("".join(ch if ch == "\n" else " " for ch in text[i:j]))
            i = j
        elif c == "/" and (_last_nonspace(out, len(out)) in regex_prev or not out):
            j, incls = i + 1, False                     # 正则字面量：跳到未转义的收尾 /
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == "[":
                    incls = True
                elif text[j] == "]":
                    incls = False
                elif text[j] == "/" and not incls:
                    j += 1
                    break
                elif text[j] == "\n":
                    break
                j += 1
            out.append(text[i:j])
            i = j
        else:
            if c in "\"'`":
                quote = c
            out.append(c)
            i += 1
    return "".join(out)


def check(path: str) -> list[str]:
    bad: list[str] = []
    try:
        raw = open(path, "rb").read()
    except OSError as exc:
        return [f"读不到文件：{exc}"]
    if len(raw) < (8_000 if os.path.basename(path) == "lobby.html" else MIN_BYTES):
        bad.append(f"文件太小（{len(raw)} < {MIN_BYTES} 字节）——像是被截断")
    if b"\x00" in raw[:4096]:
        bad.append("开头有 NUL 字节——不是文本页面")
    txt = raw.decode("utf-8", "replace")
    if not txt.rstrip().endswith("</html>"):
        bad.append("结尾不是 </html>——文件被截断")
    if txt.count("<script") != txt.count("</script>"):
        bad.append(f"<script> 不成对（{txt.count('<script')} vs {txt.count('</script>')}）")
    if txt.count("<style") != txt.count("</style>"):
        bad.append("style 标签不成对")
    # 裸 n( ：真发生过（render() 里调用了藏在 fmt() 内的助手函数 → 整页 ReferenceError）
    # 先挖掉注释：注释里写「n(失敗次數)」是说明文字，不是调用（2026-10-05 实测）。
    code = code_only(txt)
    bare = re.findall(r"(?<![A-Za-z_$.])n\(", code)
    if bare:
        ctx = [code[max(0, m.start() - 30):m.start() + 12].replace("\n", " ")
               for m in re.finditer(r"(?<![A-Za-z_$.])n\(", code)][:3]
        bad.append(f"出现未定义的裸 n( ×{len(bare)}：{ctx}")
    # ★ 尺寸級距（2026-10-19）：版面斷點只准用 s/m/l/xl/xxl 這條線（CSS 開頭那份），
    #   不准再臨時發明 px。允許值＝四條邊界（max 用 x−1、min 用 x）。
    BPS = {599, 600, 979, 980, 1399, 1400, 1799, 1800}
    # 級距內的特例（不是新級距，是級距**裡面**的排版細節；每一條都要有理由）：
    #   359＝s 級的極窄手機（「還能行動」改橫條）；760＝m 級的平板浮動面板（私訊／地圖改浮窗）
    BPS_EXTRA = {359, 760}
    media_ws = sorted({int(float(w)) for w in re.findall(
        r"@media[^{]*?(?:max|min)-width:\s*([0-9.]+)px", code)})
    stray = [w for w in media_ws if w not in BPS and w not in BPS_EXTRA]
    if stray:
        bad.append(f"版面斷點不合級距（s≤599／m 600–979／l 980–1399／xl 1400–1799／xxl ≥1800）：{stray}")
    need = NEED_LOBBY if os.path.basename(path) == "lobby.html" else NEED
    for key in need:
        if key not in txt:
            bad.append(f"缺关键钩子：{key}")
    return bad


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: check_frontend.py <index.html|lobby.html>")
        return 2
    errs = check(sys.argv[1])
    if errs:
        print("❌ 前端静态闸未过：")
        for e in errs:
            print("   -", e)
        return 1
    print("✅ 前端静态闸通过（钩子齐全、标签成对、无裸 n( ）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
