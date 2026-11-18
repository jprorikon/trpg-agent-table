#!/usr/bin/env python3
"""窄屏量測器：用 CDP 把 headless Chromium 設成指定視口，量橫向溢出並截圖。

為什麼要有這支：兩邊各自用不同寫法量「有沒有橫滾」，數字會對不上。
這支把口徑固定成三件事，兩邊跑同一支就不會吵：
  1. 視口寬 `vw`（Emulation.setDeviceMetricsOverride，mobile=False）
  2. `documentElement.scrollWidth`（> vw 就是真的可以橫滾）
  3. 「HTML 元素越界」計數——**只算非 SVG 的元素**；
     SVG 內部的 line/text/circle 常落在 viewBox 外、由 SVG 自己裁掉，不算破版。
     SVG 內部的越界數另外報成 `svgInternalN`，僅供參考。

口徑註（免得兩邊吵）：元素越界計數**跟當下開著哪些面板有關**——地圖面板開著時，
`#mapsvg`（480px）與它的 line/text/circle 會落在可視範圍外，由 SVG 自己裁掉，`docScrollW` 不動。
所以**判準只有一條：`docScrollW <= vw`**（看 `horizontalScroll: false`）；
元素計數只當「去找是誰撐寬」的線索，0 尺寸的元素（display:none）本來就不算。

依賴：python3 + `websocket-client`（`pip install websocket-client`）。
需要先有一顆開著 CDP 的 Chromium（沒 playwright 也能做）：

  mkdir -p /tmp/cdp-profile
  chromium --headless=new --remote-debugging-port=9222 --remote-allow-origins='*' \\
    --no-sandbox --disable-dev-shm-usage --disable-gpu --no-proxy-server \\
    --user-data-dir=/tmp/cdp-profile about:blank &

用法：
  python3 tools/narrow_probe.py <url> <截圖路徑|/dev/null> [寬=390] [高=844]
  另可用環境變數 PROBE_JS_FILE=<js檔> 送一段自訂 JS（量什麼都行，例如找是誰撐寬頁面）

輸出：PROBE/MEASURE 一行 JSON + SHOT 一行 + CONSOLE 一行（有 error/warning 才列）。
"""
import base64
import json
import os
import sys
import time
import urllib.request

import websocket  # websocket-client

PORT = int(os.environ.get("CDP_PORT", "9222"))

MEASURE = r"""
(() => {
  const vw = innerWidth;
  const htmlOver = [], svgOver = [];
  document.querySelectorAll('*').forEach(e => {
    const r = e.getBoundingClientRect();
    if (r.width <= 0 || (r.right <= vw + 1 && r.left >= -1)) return;
    const sel = e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') +
      ((typeof e.className === 'string' && e.className) ? '.' + e.className.trim().split(/\s+/)[0] : '');
    const rec = sel + ' [' + Math.round(r.left) + '..' + Math.round(r.right) + ']';
    (e.closest('svg') ? svgOver : htmlOver).push(rec);
  });
  const bar = document.querySelector('.bar');
  const title = document.getElementById('title');
  return JSON.stringify({
    vw,
    docScrollW: document.documentElement.scrollWidth,
    bodyScrollW: document.body.scrollWidth,
    htmlOverN: htmlOver.length, htmlOver: htmlOver.slice(0, 12),
    svgInternalN: svgOver.length,
    barH: bar ? Math.round(bar.getBoundingClientRect().height) : null,
    titleH: title ? Math.round(title.getBoundingClientRect().height) : null,
    titleW: title ? Math.round(title.getBoundingClientRect().width) : null,
    horizontalScroll: document.documentElement.scrollWidth > vw
  });
})()
"""


def _http(path, method="GET"):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method)
    with urllib.request.urlopen(req, timeout=10) as r:
        body = r.read().decode() or "{}"
    return json.loads(body)


class CDP:
    def __init__(self):
        tab = _http("/json/new?about:blank", "PUT")
        self.tab = tab
        self.i = 0
        self.notes = []
        self.ws = websocket.create_connection(tab["webSocketDebuggerUrl"],
                                             suppress_origin=True, timeout=30)

    def send(self, method, params=None, wait=True):
        self.i += 1
        mid = self.i
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        if not wait:
            return None
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                return msg
            self._note(msg)

    def _note(self, msg):
        if msg.get("method") in ("Log.entryAdded", "Runtime.consoleAPICalled"):
            p = msg.get("params", {})
            lvl = p.get("entry", {}).get("level") or p.get("type")
            if lvl in ("error", "warning"):
                txt = p.get("entry", {}).get("text") or ""
                args = p.get("args") or []
                if not txt and args:
                    txt = " ".join(str(a.get("value", a.get("description", ""))) for a in args)
                self.notes.append(f"[{lvl}] {txt[:160]}")

    def eval(self, expr):
        r = self.send("Runtime.evaluate",
                      {"expression": expr, "returnByValue": True, "awaitPromise": True}) or {}
        return (((r.get("result") or {}).get("result")) or {}).get("value")

    def shot(self, path):
        data = self.send("Page.captureScreenshot", {"format": "png"})["result"]["data"]
        if path and path != "/dev/null":
            with open(path, "wb") as fh:
                fh.write(base64.b64decode(data))
        return path

    def close(self):
        try:
            _http(f"/json/close/{self.tab['id']}", "PUT")
        except Exception:
            pass
        self.ws.close()


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    url, out = sys.argv[1], sys.argv[2]
    width = int(sys.argv[3]) if len(sys.argv) > 3 else 390
    height = int(sys.argv[4]) if len(sys.argv) > 4 else 844

    c = CDP()
    c.send("Emulation.setDeviceMetricsOverride",
           {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False})
    for m in ("Page.enable", "Log.enable", "Runtime.enable"):
        c.send(m)
    c.send("Page.navigate", {"url": url})
    for _ in range(40):
        time.sleep(0.4)
        st = c.eval("JSON.stringify([document.readyState, location.href])")
        if st and st.startswith('["complete"') and "about:blank" not in st:
            break
    time.sleep(1.5)

    js = os.environ.get("PROBE_JS")
    if not js and os.environ.get("PROBE_JS_FILE"):
        with open(os.environ["PROBE_JS_FILE"], encoding="utf-8") as fh:
            js = fh.read()
    # 玩法說明那層窗會蓋住版面；量之前先收掉
    c.eval("try { closeHowto(); } catch (e) {}")
    time.sleep(0.3)

    if js:
        print("PROBE:", c.eval(js))
    else:
        print("MEASURE:", c.eval(MEASURE))
    print("SHOT:", c.shot(out))
    time.sleep(0.4)
    print("CONSOLE:", "；".join(c.notes) if c.notes else "（無 error/warning）")
    c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
