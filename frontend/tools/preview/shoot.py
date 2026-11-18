#!/usr/bin/env python3
"""用 CDP 對預覽載具（tools/preview/preview.py）拍一組截圖並量基線數字。

  CDP_PORT=9333 python3 tools/preview/shoot.py <輸出資料夾> <標籤> [--base http://127.0.0.1:8990/preview/]
                                 [--lang zh-Hant] [--only 390x844,1280x800]
                                 [--mapopen closed|open] [--howto seen|new] [--momo N]

矩陣：視口 390×844、1280×800 × 淺色／深色 × 景況（open＝開著的一景；closed＝fixture 原樣的已收景）。
每張存成 `<資料夾>/<標籤>-<寬>x<高>-<theme>-<景況>.png`（首屏＝視口那一屏，不是整頁），
並把量到的數字寫進 `<資料夾>/<標籤>-metrics.json`、同時印成表。

量什麼（跟 narrow_probe 同口徑：mobile=False；判準 docScrollW <= vw）：
  sceneY    #scenetx 第一行文字的頁面 y（沒顯示＝null）；inView＝它是否落在首屏（< 視口高）
  actY      HUD「還能行動」磚（.acttile）上緣的頁面 y；actInView 同上
  dockH     手機底部 #dock 的高度（桌機沒有 dock ⇒ 0）
  docScrollW／vw／scrollbarW  橫向溢出（scrollbarW＝視口寬 − 文件可用寬，通常是縱向捲軸）
  barH      頂欄高度

狀態可重現（每張圖都一樣）：URL 一律帶 `pv_reset=1`，預覽載具的 shim 會在頁面腳本之前清掉所有會影響版面的偏好鍵
（trpg_lang／theme／translate／mapopen／mappos_v2／howto_seen／momo_seen／name／seat*、sessionStorage 翻譯快取），
再用 `pv_mapopen`／`pv_howto`／`pv_momo`／`pv_lang` 顯式寫回——所以拍 before 與拍 after 不管共不共用 Chromium profile、
誰先誰後，起始狀態都相同。**不碰 trpg_token／trpg_gmtoken**（憑證載體；預覽載具自己會補一把假的）。
拍完時的 localStorage／sessionStorage 內容（token 只記「有／無」）寫進 metrics.json 的 `storage`，可稽核。
預設狀態：地圖收著（closed）、新手引導沒看過（new＝第一次來的樣子，也是全新 profile 的樣子，跟 10-08 的舊基線同口徑）、
私訊已讀計數 0、翻譯關。

依賴：websocket-client（`/home/inmdd/.venv-cdp/`）、一顆開著 CDP 的 Chromium（埠由環境變數 CDP_PORT 指定，預設 9222；
**請用自己的埠與自己的 --user-data-dir**，別借別條線的 :9222）、預覽載具在跑。
截圖之後逐對比像素用 tools/preview/pixdiff.py（系統 python3，有 Pillow）。
截圖別寫到 /tmp：snap 版 Chromium 的 /tmp 是私有的（這支由 python 寫檔，所以沒這問題；但請照慣例放 $HOME 底下）。
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import narrow_probe as NP  # noqa: E402  重用它的 CDP 類別與 MEASURE（口徑一致）

METRICS_JS = r"""
(() => {
  const vw = innerWidth, vh = innerHeight;
  const el = id => document.getElementById(id);
  const vis = e => e && getComputedStyle(e).display !== "none" && e.getBoundingClientRect().height > 0;
  const topOf = e => Math.round(e.getBoundingClientRect().top + scrollY);
  let sceneY = null;
  const st = el("scenetx");
  if (vis(st)) {
    const rg = document.createRange(); rg.selectNodeContents(st);
    const rc = rg.getClientRects()[0];
    sceneY = Math.round((rc ? rc.top : st.getBoundingClientRect().top) + scrollY);
  }
  const at = document.querySelector("#hud .acttile");
  const actY = vis(at) ? topOf(at) : null;
  const dock = el("dock");
  const dockH = (dock && getComputedStyle(dock).display !== "none") ? Math.round(dock.getBoundingClientRect().height) : 0;
  const bar = document.querySelector(".bar");
  return JSON.stringify({
    vw, vh, docScrollW: document.documentElement.scrollWidth,
    scrollbarW: vw - document.documentElement.clientWidth,
    sceneY, sceneInView: sceneY !== null && sceneY < vh,
    actY, actInView: actY !== null && actY < vh,
    dockH, barH: bar ? Math.round(bar.getBoundingClientRect().height) : null,
    docScrollH: document.documentElement.scrollHeight
  });
})()
"""


STORAGE_JS = r"""
(() => {
  const dump = st => { const o = {}; for (let i = 0; i < st.length; i++) { const k = st.key(i);
    o[k] = /^trpg_(gm)?token$/.test(k) ? "<set>" : String(st.getItem(k)).slice(0, 80); } return o; };
  const sorted = o => Object.fromEntries(Object.keys(o).sort().map(k => [k, o[k]]));
  return JSON.stringify({local: sorted(dump(localStorage)), session: sorted(dump(sessionStorage))});
})()
"""


def run_one(base, lang, w, h, theme, scene, out_png, mapopen="closed", howto="new", momo=None, reset=True):
    url = "%s?theme=%s&pv_lang=%s&pv_scene=%s" % (base, theme, lang, scene)
    if reset:
        url += "&pv_reset=1&pv_mapopen=%s&pv_howto=%s" % (mapopen, howto)
    if momo is not None:
        url += "&pv_momo=%s" % momo
    c = NP.CDP()
    try:
        c.send("Emulation.setDeviceMetricsOverride", {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": False})
        for m in ("Page.enable", "Log.enable", "Runtime.enable"):
            c.send(m)
        c.send("Page.navigate", {"url": url})
        for _ in range(40):                      # 等第一輪輪詢把畫面填起來（席位卡有內容）
            time.sleep(0.4)
            if c.eval("(document.getElementById('seats')||{children:[]}).children.length") :
                break
        time.sleep(0.8)
        c.eval("try { closeHowto(); closeAuth(); } catch (e) {}")
        time.sleep(0.2)
        m = json.loads(c.eval(METRICS_JS))
        m["storage"] = json.loads(c.eval(STORAGE_JS))
        c.shot(out_png)
        m["console"] = c.notes[:6]
        return m
    finally:
        c.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir")
    ap.add_argument("tag")
    ap.add_argument("--base", default="http://127.0.0.1:8990/preview/")
    ap.add_argument("--lang", default="zh-Hant")
    ap.add_argument("--only", default="390x844,1280x800")
    ap.add_argument("--mapopen", default="closed", choices=("closed", "open"))
    ap.add_argument("--howto", default="new", choices=("seen", "new"))
    ap.add_argument("--momo", default=None)
    ap.add_argument("--no-reset", action="store_true", help="診斷用：不帶 pv_reset，沿用 profile 殘留（重現狀態污染；正式基線別用）")
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)
    sizes = [tuple(int(x) for x in s.split("x")) for s in a.only.split(",")]
    res = {}
    for (w, h) in sizes:
        for theme in ("light", "dark"):
            for scene in ("open", "closed"):
                key = "%dx%d-%s-%s" % (w, h, theme, scene)
                png = os.path.join(a.outdir, "%s-%s.png" % (a.tag, key))
                res[key] = run_one(a.base, a.lang, w, h, theme, scene, png, a.mapopen, a.howto, a.momo, not a.no_reset)
                r = res[key]
                print("%-26s sceneY=%-5s inView=%-5s actY=%-5s inView=%-5s dockH=%-4s docScrollW=%s/%s  barH=%s%s" % (
                    key, r["sceneY"], r["sceneInView"], r["actY"], r["actInView"], r["dockH"],
                    r["docScrollW"], r["vw"], r["barH"], ("  console=%s" % r["console"]) if r["console"] else ""))
    with open(os.path.join(a.outdir, "%s-metrics.json" % a.tag), "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
