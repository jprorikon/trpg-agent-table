"""render（自 __init__.py 拆出）"""

from __future__ import annotations
import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List
from trpg.core import layout as layout                                  # 桌的位置／模組命名（與桌子共用同一支）
from trpg.service import table_directory as tdir                 # L6：掃桌／探活／收局判定（與桌子共用同一支）
from trpg.paths import ROOT as HERE  # repo 根（資料／vendor／docs 都在這裡）

from .common import LOBBY

def _esc(s: Any) -> str:
    return (str(s if s is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))

def _short(scenario: str, table_id: str) -> str:
    """顯示名：`scenario` 的「──」前那段；沒有就用 id。"""
    s = (scenario or "").split("──")[0].strip()
    return s or table_id

def _tables() -> List[Dict[str, Any]]:
    to = float(LOBBY["timeout"])
    return [tdir.probe(t, to) for t in tdir.discover(str(LOBBY["root"]))]

def _char_index(tables: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """**角色 → 哪幾張桌**（＝各桌 `seats.json` 的選角；公開資料，不需登入）。

    ⇒ 這就是設計者說的「選完角色，可以去這個角色出現的劇本的桌」那一步的看板。
    """
    idx: Dict[str, Dict[str, Any]] = {}
    for t in tables:
        for c in (t.get("cast") or []):
            e = idx.setdefault(c["char"], {"char": c["char"], "player": False, "tables": []})
            e["player"] = e["player"] or bool(c.get("player"))
            e["tables"].append({"id": t["id"], "name": _short(t.get("scenario") or "", t["id"]),
                                "seat": c.get("seat"), "kind": c.get("kind"),
                                "ended": bool(t.get("ended")), "online": bool(t.get("online"))})
    return [idx[k] for k in sorted(idx)]

_WIZARD_HTML = """
<h2 class="h2">建立新桌／開新局 <span class="dim">（選模組 → 選劇本 → 開新局 → 進場）</span></h2>
<div class="card" id="wiz">
  <p class="meta">憑證只存在你的瀏覽器裡、只往這台前台送（要進場／開局才需要；旁觀不用）。</p>
  <label class="meta">憑證 <input id="w-token" type="password" autocomplete="off" placeholder="貼上 account token"></label>
  <div id="w-out" class="wiz-out"><p class="hint">載入中…</p></div>
</div>
<script>
(function () {
  var OUT = document.getElementById("w-out");
  var TOK = document.getElementById("w-token");
  var esc = function (s) {
    return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c];
    });
  };
  TOK.value = localStorage.getItem("trpg_token") || "";
  TOK.addEventListener("change", function () { localStorage.setItem("trpg_token", TOK.value.trim()); });

  function gq(query, variables) {
    var h = {"Content-Type": "application/json"};
    var t = (localStorage.getItem("trpg_token") || "").trim();
    if (t) { h["Authorization"] = "Bearer " + t; }
    return fetch("/graphql", {method: "POST", headers: h,
      body: JSON.stringify({query: query, variables: variables || null})})
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (j.errors && j.errors.length) { throw new Error(j.errors[0].message); }
        return j.data;
      });
  }

  function fail(e) { OUT.innerHTML = '<p class="badline">⚠ ' + esc(e.message || e) + '</p>'; }

  function createTable(mid, sid, btn) {
    if (!(localStorage.getItem("trpg_token") || "").trim()) {
      note("建立新桌要貼上你的憑證（Authorization: Bearer …）——誰建的看得出來。");
      return;
    }
    var ok = window.confirm("要在「" + mid + "」的劇本「" + sid + "」開一張新桌嗎？"
      + "（新桌與這個劇本**共用同一份定義**，從景 1 開始）");
    if (!ok) { return; }
    if (btn) { btn.disabled = true; }
    gq("mutation($m: ID!, $s: ID!){ createTable(module: $m, scenario: $s) }", {m: mid, s: sid})
      .then(function (d) {
        var t = d.createTable.table;
        OUT.innerHTML = '<p class="okline">已開好新桌 <b>' + esc(t.name) + '</b>'
          + '（<code>' + esc(t.id) + '</code>・埠 ' + esc(t.port) + '・session <code>'
          + esc(t.session) + '</code>）</p><p class="meta">'
          + '<button class="mini" id="w-back2">← 回劇本列表</button></p>';
        document.getElementById("w-back2").addEventListener("click", function () { showScenarios(mid); });
      }).catch(function (e) { fail(e); if (btn) { btn.disabled = false; } });
  }
  function note(s) { OUT.innerHTML = '<p class="hint">' + esc(s) + '</p>'; }

  function showModules() {
    note("載入模組…");
    gq("{ modules { id name tableCount characters scenarios { id tableCount } } }")
      .then(function (d) {
        var html = "";
        (d.modules || []).forEach(function (m) {
          html += '<div class="wiz-mod"><b>' + esc(m.name) + '</b> <code>' + esc(m.id) + '</code>'
            + ' <span class="dim">' + m.tableCount + ' 張桌・' + (m.scenarios || []).length
            + ' 個劇本・角色 ' + m.characters + '</span>'
            + ' <button class="mini" data-mod="' + esc(m.id) + '">選這個模組</button></div>';
        });
        OUT.innerHTML = html || '<p class="hint">（沒有模組）</p>';
        Array.prototype.forEach.call(OUT.querySelectorAll("button[data-mod]"), function (b) {
          b.addEventListener("click", function () { showScenarios(b.getAttribute("data-mod")); });
        });
      }).catch(fail);
  }

  function showScenarios(mid) {
    note("載入劇本…");
    gq("{ modules { id scenarios { id tableCount tables { id name online ended seats events session } } } }")
      .then(function (d) {
        var m = (d.modules || []).filter(function (x) { return x.id === mid; })[0];
        if (!m) { throw new Error("找不到模組 " + mid); }
        var html = '<p class="meta">模組 <b>' + esc(mid) + '</b>：一個劇本可以開好幾張桌'
          + '（同一個帳號的同一個角色，在某一張桌收局前不能再進別張桌）。</p>';
        (m.scenarios || []).forEach(function (s) {
          html += '<div class="wiz-scn"><b>劇本 ' + esc(s.name || s.id) + '</b>' 
            + ' <span class="dim">' + s.tableCount + ' 張桌</span>'
            + ' <button class="mini" data-newtable="' + esc(mid) + "|" + esc(s.id) + '">＋ 開一張新桌</button></div>';
          (s.tables || []).forEach(function (t) {
            var st = t.online ? (t.ended ? "已收局" : "開著") : "離線";
            html += '<div class="wiz-mod"><b>' + esc(t.name || t.id) + '</b> <code>' + esc(t.id) + '</code>'
              + ' <span class="dim">' + esc(st) + "・席次 " + esc(t.seats) + "・事件 " + esc(t.events) + '</span>'
              + '<div class="acts">'
              + '<button class="mini" data-entry="' + esc(t.id) + '">進場</button>'
              + '<a class="ghost" href="/' + esc(t.id) + '/">進入</a>'
              + '<button class="mini danger" data-newrun="' + esc(t.id) + '">開新局</button>'
              + "</div></div>";
          });
        });
        OUT.innerHTML = html;
        Array.prototype.forEach.call(OUT.querySelectorAll("button[data-newtable]"), function (b) {
          b.addEventListener("click", function () {
            var p = b.getAttribute("data-newtable").split("|");
            createTable(p[0], p[1], b);
          });
        });
        Array.prototype.forEach.call(OUT.querySelectorAll("button[data-entry]"), function (b) {
          b.addEventListener("click", function () { showEntry(b.getAttribute("data-entry")); });
        });
        Array.prototype.forEach.call(OUT.querySelectorAll("button[data-newrun]"), function (b) {
          b.addEventListener("click", function () { doNewRun(b.getAttribute("data-newrun")); });
        });
      }).catch(fail);
  }

  function showEntry(tid) {
    note("判進場…");
    gq('query($t: ID!){ table(id:$t){ entry { session account hasInstance importable { seat charId kind } '
       + 'creatable { seat charId kind } takenSeats { seat charId } spectateOk watchPath reason } } }',
       {t: tid})
      .then(function (d) {
        var e = d.table.entry, html = '<p class="meta">桌 <code>' + esc(tid) + '</code>・'
          + 'session <code>' + esc(e.session) + '</code></p>';
        if (e.reason) { html += '<p class="hint">' + esc(e.reason) + '</p>'; }
        if (e.importable.length) {
          html += '<p class="meta"><b>匯入已有的角色</b>（這個模組你已經有的實例）：</p>';
          e.importable.forEach(function (o) {
            html += '<div class="wiz-mod">' + esc(o.charId) + ' <span class="dim">（席 ' + esc(o.seat) + '）</span>'
              + ' <button class="mini" data-pick="' + esc(o.charId) + '" data-seat="' + esc(o.seat) + '">用它進場</button></div>';
          });
        }
        if (e.creatable.length) {
          html += '<p class="meta"><b>建立新角色</b>（挑一個還空著的席位）：</p>';
          e.creatable.forEach(function (o) {
            html += '<div class="wiz-mod">' + esc(o.charId) + ' <span class="dim">（席 ' + esc(o.seat)
              + '・' + esc(o.kind) + '）</span>'
              + ' <button class="mini" data-pick="' + esc(o.charId) + '" data-seat="' + esc(o.seat) + '">選它</button></div>';
          });
        }
        if (e.takenSeats.length) {
          html += '<p class="dim">已有人坐：'
            + e.takenSeats.map(function (o) { return esc(o.seat) + "/" + esc(o.charId); }).join("、") + '</p>';
        }
        if (e.spectateOk) {
          html += '<div class="acts">' + (e.watchPath
            ? '<a class="ghost" href="' + esc(e.watchPath) + '">旁觀（唯讀、不佔席）</a>'
            : '<a class="ghost" href="/' + esc(tid) + '/">只看公開投影</a>'
          ) + "</div>";
        }
        html += '<div class="acts"><button class="mini ghost" data-back="' + esc(tid) + '">← 回去</button></div>';
        OUT.innerHTML = html;
        Array.prototype.forEach.call(OUT.querySelectorAll("button[data-pick]"), function (b) {
          b.addEventListener("click", function () {
            doPick(tid, e.session, b.getAttribute("data-pick"));
          });
        });
        Array.prototype.forEach.call(OUT.querySelectorAll("button[data-back]"), function (b) {
          b.addEventListener("click", function () { showScenarios(tid); });
        });
      }).catch(fail);
  }

  function doPick(tid, sid, charId) {
    note("選角色…");
    gq("mutation($t: ID!, $s: ID!, $c: ID!){ pickChar(tableId:$t, session:$s, charId:$c) }",
       {t: tid, s: sid, c: charId})
      .then(function () {
        OUT.innerHTML = '<p class="okline">✓ 已經選好 <code>' + esc(charId) + '</code>——'
          + '席位已綁在你的帳號上。 <a class="primary" href="/' + esc(tid) + '/">進入這張桌</a></p>';
      }).catch(fail);
  }

  function doNewRun(tid) {
    if (!window.confirm("要為「" + tid + "」開一局新的嗎？（舊局會留在文件裡、可以回放；"
        + "現在這局的進行中狀態不會帶過去）")) {
      return;
    }
    note("開新局…");
    gq("mutation($t: ID!){ newRun(tableId:$t) }", {t: tid})
      .then(function (d) {
        var r = d.newRun;
        OUT.innerHTML = '<p class="okline">✓ 換局完成：<code>' + esc(r.old_session || "（無）") + '</code> → <code>'
          + esc(r.session) + '</code>（舊局檔留著）</p>'
          + '<div class="acts"><button class="mini" data-entry="' + esc(tid) + '">進場判定</button>'
          + '<a class="ghost" href="/' + esc(tid) + '/">進入</a></div>';
        Array.prototype.forEach.call(OUT.querySelectorAll("button[data-entry]"), function (b) {
          b.addEventListener("click", function () { showEntry(b.getAttribute("data-entry")); });
        });
      }).catch(fail);
  }

  showModules();
})();
</script>
"""

_WIZARD_CSS = """
label.meta{display:block;margin:.4rem 0} label.meta input{width:100%;margin-top:.25rem;padding:.4rem .6rem;
border-radius:.4rem;border:1px solid var(--line);background:#0e1116;color:var(--fg)}
.wiz-out{margin-top:.6rem} .wiz-scn{margin:.7rem 0 .2rem;padding-top:.5rem;border-top:1px solid var(--line)} .okline{color:#8ee6a0;margin:.4rem 0} .wiz-mod{display:flex;flex-wrap:wrap;gap:.4rem;align-items:center;
padding:.45rem 0;border-bottom:1px solid var(--line)}
button.mini{background:#243044;color:var(--fg);border:1px solid var(--line);border-radius:.4rem;
padding:.25rem .6rem;font-size:.85rem;cursor:pointer}
button.mini.danger{background:#3a2230;border-color:#6b3247} button.mini.ghost{background:transparent}
.okline{color:var(--on)} .badline{color:#ff8b8b}
"""

def render(host: str, tables: List[Dict[str, Any]], *, prefix_mode: bool = False) -> bytes:
    """大廳頁。`prefix_mode=True` ＝ 被**單一入口前台**服務（連結走子路徑 `/<桌>/`），
    否則印絕對網址（獨立跑在大廳埠時，或桌還沒收進前台時）。"""
    # **按模組分類**（設計者 2026-10-07）：「桌子一覽」先分模組（＝一個作品／世界），
    # 模組底下才是他的劇本（每張桌＝一個劇本的一局）。模組 id 走 `layout.scan` 那一份
    # （新制＝`modules/<模組>/` 目錄名；舊制＝桌名）；掃不到的歸「（未分類）」——
    # 寧可醜，也不要靜默吞掉一張桌。
    groups: Dict[str, List[str]] = {}
    for t in tables:
        _mid = str(t.get("module") or "（未分類）")
        _g = groups.setdefault(_mid, [])
        if t.get("config_error"):
            _g.append(f'<div class="card bad"><h3>{_esc(t["id"])}</h3>'
                      f'<p>config.json 讀不動</p></div>')
            continue
        if not t.get("online"):
            dot, st = "off", "離線"
        elif t.get("ended"):
            dot, st = "end", "已收局"
        else:
            dot, st = "on", ("進行中（第 %s 景）" % t["turn"] if t.get("turn") else "開著（等人入座）")
        link = (f'/{_esc(t["id"])}/' if prefix_mode else f'http://{_esc(host)}:{t.get("port")}/')
        wl = (f'<a class="ghost" href="{"/" + _esc(t["id"]) + "/watch/" if prefix_mode else "http://" + _esc(host) + ":" + str(t["watch_port"]) + "/"}">觀戰</a>'
              if t.get("watch_port") else "")
        acc = ("帳號＋密碼登入" if t.get("accounts") else "（這張桌沒有帳號層：只能看公開投影）")
        cast = "、".join(f'{_esc(c["char"])}（{_esc(c["seat"])}）' for c in (t.get("cast") or [])) or "—"
        _g.append(f'''<div class="card {dot}">
  <div class="hd"><h3>{_esc(t.get("scenario_name") or t.get("scenario") or t["id"])}</h3><span class="dot {dot}">{st}</span></div>
  <p class="meta">模組 <b>{_esc(t.get("module_name") or t.get("module") or "")}</b> ＞ 劇本 <b>{_esc(t.get("scenario_name") or t.get("scenario") or "")}</b>　（id <code>{_esc(t.get("scenario") or "")}</code>）</p>
  <p class="meta">桌 <code>{_esc(t["id"])}</code>　位址 <code>/{_esc(t["id"])}/</code>　埠 <code>{_esc("（內部）" if prefix_mode else t.get("port"))}</code>
     席次 <b>{_esc(t.get("seats") if t.get("seats") is not None else "—")}</b>
     事件 <b>{_esc(t.get("events") if t.get("events") is not None else "—")}</b>
     （尾序 {_esc(t.get("last_seq") if t.get("last_seq") is not None else "—")}）</p>
  <p class="meta">選角：{cast}</p>
  <p class="meta">進場：{acc}</p>
  <div class="acts"><a class="primary" href="{link}">進入</a>{wl}</div>
</div>''')
    if groups:
        _blocks = []
        for _mid, _cards in groups.items():
            _n = len(_cards)
            try:                                   # 顯示名由模組自己給（`modules/<模組>/module.json`）
                _mname = layout.module_name(str(LOBBY["root"]), _mid) if _mid != "（未分類）" else _mid
            except Exception:                                      # noqa: BLE001
                _mname = _mid
            _idbit = (f' <code>{_esc(_mid)}</code>' if _mname != _mid else "")
            _blocks.append(f'<h2 class="h2">{_esc(_mname)}{_idbit}'
                           f'<span class="dim">　{_n} 張桌</span></h2>'
                           + "\n".join(_cards))
        body = "\n".join(_blocks)
    else:
        body = ('<p class="hint">（這台機器上還沒掃到桌子：'
                'modules/&lt;模組&gt;/scenarios/&lt;劇本&gt;/config.json 或 campaigns/&lt;桌&gt;/config.json）</p>')
    idx = _char_index(tables)
    if idx:
        items = []
        for e in idx:
            where = "；".join(f'{_esc(x["name"])}（席 {_esc(x["seat"])}'
                              + ("・已收局" if x["ended"] else ("・開著" if x["online"] else "・離線"))
                              + "）" for x in e["tables"])
            tag = "" if e["player"] else ' <span class="dim">（NPC／GM 席，不開放玩家坐）</span>'
            items.append(f'<li><code>{_esc(e["char"])}</code> → {where}{tag}</li>')
        charsec = ('<h2 class="h2">角色 → 去哪張桌（選角＝劇本資料，公開）</h2><ul class="chars">'
                   + "".join(items) + '</ul>')
    else:
        charsec = ""
    # 建桌嚮導只在前台頁（prefix_mode）畫：獨立大廳埠沒有 `/graphql` 這條線。
    wiz = (_WIZARD_HTML if prefix_mode else
           '<h2 class="h2">建立新桌</h2><p class="hint">要在**前台的頁面**（單一入口）上做——'
           '那裡才有 `/graphql`。</p>')
    return f'''<!DOCTYPE html>
<html lang="zh-Hans"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>TRPG 大廳</title>
<style>
:root{{--bg:#12141a;--fg:#e8eaf0;--dim:#9aa3b2;--card:#1b1f28;--line:#2b3140;--on:#3ecf8e;--off:#8b93a3;--end:#e0b341}}
*{{box-sizing:border-box}} body{{margin:0;padding:1rem;background:var(--bg);color:var(--fg);
font:15px/1.6 system-ui,-apple-system,"Noto Sans CJK JP",sans-serif}}
h1{{font-size:1.15rem;margin:.2rem 0 1rem}} .h2{{font-size:1rem;margin:1.4rem 0 .5rem}}
.hint,.meta,.dim{{color:var(--dim);font-size:.85rem;margin:.15rem 0}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:.7rem;padding:.8rem .9rem;margin:0 0 .8rem}}
.card.bad{{border-color:#7a2b2b}} .hd{{display:flex;justify-content:space-between;align-items:baseline;gap:.6rem}}
.hd h3{{font-size:1rem;margin:.1rem 0}} code{{background:#0e1116;padding:.05rem .3rem;border-radius:.25rem}}
.dot{{font-size:.8rem;color:var(--dim)}} .dot.on{{color:var(--on)}} .dot.end{{color:var(--end)}}
.acts{{display:flex;gap:.5rem;margin-top:.6rem}} a{{text-decoration:none;border-radius:.45rem;padding:.35rem .8rem;font-size:.9rem}}
a.primary{{background:#2f6df6;color:#fff}} a.ghost{{border:1px solid var(--line);color:var(--fg)}}
ul.chars{{list-style:none;padding:0;margin:0}} ul.chars li{{padding:.3rem 0;border-bottom:1px solid var(--line);font-size:.9rem}}
{_WIZARD_CSS}
</style></head><body>
<h1>TRPG 大廳　<span class="hint">這台機器上的桌子（{len(tables)} 張）</span></h1>
{body}
{charsec}
<p class="hint">進桌之後請用**這一桌自己的**帳號＋密碼登入（一把憑證跨桌通用）。
帳號登入 → 選角色 → 這個角色有戲的桌；**同一個帳號的同一個角色不能同時在兩張桌**
（另一張桌還開著且未收局時會被擋）。沒有帳號層的桌只能看公開投影。</p>
{wiz}
</body></html>'''.encode("utf-8")
