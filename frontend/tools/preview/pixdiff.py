#!/usr/bin/env python3
"""逐對比 before／after 截圖的像素：頂端 N 列是否逐位元相同、整張差多少、差異落在哪些列。

  python3 tools/preview/pixdiff.py <資料夾> [--before before] [--after after] [--top 60]
                                   [--only 390x844] [--tol 2] [--strict]

配對規則：`<資料夾>/<before>-<尺寸>-<主題>-<景況>.png` ↔ `<資料夾>/<after>-…`。
頂端 N 列（預設 60，約等於頂欄）不同 ⇒ 兩張圖「不是同一個狀態」（共用 profile 的 localStorage 殘留、
主題／語言沒對上…），這時整張的差異不能拿來當版面差異讀。--strict：任何一對頂端「逐位元不同」就 exit 1。

頂端那一欄分三種：逐位元相同／只有 ≤ --tol（預設 2）階的色差（＝渲染雜訊）／有 > tol 階的色差（＝真差異，附 bbox 與像素數）。
⚠ 渲染雜訊是真的存在：頂欄（sticky＋`backdrop-filter: blur`）底下的半透明按鈕，圓角邊緣的幾個像素會在 ±1 階之間跳，
「同一頁連拍兩次」也會；要分辨「雜訊」還是「真差異」，先拿 `--before after --after after2`（同頁拍兩次）量噪音底。

依賴：Pillow（系統 python3 有；`/home/inmdd/.venv-cdp/` 那個 venv 沒有，shoot.py 用 venv、這支用系統的）。
"""
import argparse
import glob
import os
import re
import sys

from PIL import Image, ImageChops


def rows_span(diff):
    """差異像素所在列的 [(起, 迄), …]（相鄰列併成一段）。"""
    mask = diff.convert("L").point(lambda v: 255 if v else 0)
    w, h = mask.size
    data = mask.tobytes()
    rows = [y for y in range(h) if any(data[y * w:(y + 1) * w])]
    spans = []
    for y in rows:
        if spans and y == spans[-1][1] + 1:
            spans[-1][1] = y
        else:
            spans.append([y, y])
    return spans


def maxdelta(diff):
    r, g, b = diff.split()
    return ImageChops.lighter(ImageChops.lighter(r, g), b)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--before", default="before")
    ap.add_argument("--after", default="after")
    ap.add_argument("--top", type=int, default=60)
    ap.add_argument("--only", default="")
    ap.add_argument("--tol", type=int, default=2, help="頂端色差 ≤ 此階數視為渲染雜訊")
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()
    pat = re.compile(r"^%s-(.+)\.png$" % re.escape(a.before))
    bad = real = 0
    n = 0
    for pb in sorted(glob.glob(os.path.join(a.dir, "%s-*.png" % a.before))):
        m = pat.match(os.path.basename(pb))
        if not m or (a.only and not m.group(1).startswith(a.only)):
            continue
        pa = os.path.join(a.dir, "%s-%s.png" % (a.after, m.group(1)))
        if not os.path.exists(pa):
            continue
        ib, ia = Image.open(pb).convert("RGB"), Image.open(pa).convert("RGB")
        n += 1
        if ib.size != ia.size:
            print("%-26s 尺寸不同 %s vs %s" % (m.group(1), ib.size, ia.size))
            bad += 1
            continue
        w, h = ib.size
        td = ImageChops.difference(ib.crop((0, 0, w, a.top)), ia.crop((0, 0, w, a.top)))
        top = td.getbbox()
        tm = maxdelta(td)
        big = tm.point(lambda v: 255 if v > a.tol else 0)
        n_big, bbox_big = sum(big.histogram()[255:]), big.getbbox()
        d = ImageChops.difference(ib, ia)
        nz = sum(1 for p in d.convert("L").tobytes() if p)
        spans = rows_span(d)
        shown = " ".join("%d-%d" % (s, e) for s, e in spans[:8]) + (" …+%d 段" % (len(spans) - 8) if len(spans) > 8 else "")
        if top is None:
            tdesc = "逐位元相同"
        elif n_big == 0:
            tdesc = "僅雜訊(最大差 %d 階, %d px)" % (max(i for i, c in enumerate(tm.histogram()) if c), sum(1 for v in tm.tobytes() if v))
        else:
            tdesc = "真差異(>%d 階 %d px, bbox=%s)" % (a.tol, n_big, bbox_big)
        print("%-26s 頂端%d列 %s | 整張差異 %.2f%%（%d/%d）| 差異列段：%s" % (
            m.group(1), a.top, tdesc,
            100.0 * nz / (w * h), nz, w * h, shown or "（無）"))
        if top is not None:
            bad += 1
            real += 1 if n_big else 0
    print("\n共 %d 對；頂端逐位元不同／尺寸不同：%d 對（其中有 > %d 階真差異：%d 對，其餘僅雜訊）" % (n, bad, a.tol, real))
    return 1 if (a.strict and bad) else 0


if __name__ == "__main__":
    sys.exit(main())
