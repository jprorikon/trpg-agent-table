#!/usr/bin/env python3
"""GM 的一行工具——把「話」送進桌端，不必手寫 curl、不必自己讀 config 拿 token。

用法（第一個參數永遠是桌 id；token 由本檔自己從那張桌的 config 讀）：

  gm.py <table> say "<敘事>"              # 世界的聲音（顯示為 GM）
  gm.py <table> npc <seat> "<台詞>"        # 某個 NPC 真的開口（例 seat=npc1）
  gm.py <table> secret <seat> "<文字>"     # 只給某個席位看的私密訊息
  gm.py <table> act <seat> <action> "<note>" ["<target>"]
                                          # 玩家宣告的實質動作→ act_for（會過額度閘、扣點）
  gm.py <table> props '<json>'             # 改當前景「在場的人／能動的地方」
  gm.py <table> close [force]              # 收掉這一景（force=不看 pending 硬收）
  gm.py <table> open "<開景文字>" [bgm:曲]  # 開下一景（可順便指定這一景的 BGM）
  gm.py <table> bgm <bgm:曲>                # 換 BGM（模組音訊表的別名；淡入 0.8s）
  gm.py <table> sfx <sfx:音>                # 放一次效果音
  gm.py <table> silence [bgm|sfx]           # 停那個聲道（預設 bgm）
  gm.py <table> sounds                      # 這一張桌能放什麼（曲目清單）
  gm.py <table> state                      # 印目前桌況（喚醒訊息裡通常已經有了）

成功＝印一行 JSON（桌端回傳）；失敗＝非零 exit、錯誤在 stderr。

桌端程式目錄（拿 campaigns/<table>/config.json 的地方）解析順序：
  1. env TRPG_CAMPAIGN_DIR  ← 直接指到「桌那一層」或它上一層（跨機常用）
  2. env TRPG_SERVER_DIR    ← 指到桌端程式根目錄（`layout.scan` 掃得到桌即可）
  3. 都沒有 ⇒ 用 `layout.must_locate` 掃 `modules/<模組>/scenarios/<劇本>` 與 `campaigns/<桌>`
  3. 本檔所在 repo 的根目錄  ← 本檔住在 <root>/tools/gm.py ⇒ 自我定位，同機免設定
"""
import json
import os
import sys
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_BASE = os.environ.get("TRPG_SERVER_DIR") or _REPO
CAMPAIGNS = os.environ.get("TRPG_CAMPAIGN_DIR") or os.path.join(_BASE, "campaigns")


def campaign_home(table: str) -> str:
    """這一張桌的家目錄（**不再硬編目錄形狀**：WS-B 後是 `modules/<模組>/scenarios/<劇本>`，
    舊制 `campaigns/<桌>` 也吃）。`TRPG_CAMPAIGN_DIR` 指到那一層時優先照它。"""
    cand = os.path.join(CAMPAIGNS, table, "config.json")
    if os.path.isfile(cand):
        return os.path.dirname(cand)
    direct = os.path.join(CAMPAIGNS, "config.json")
    if os.path.isfile(direct):                       # TRPG_CAMPAIGN_DIR 直接指到桌那一層
        return CAMPAIGNS
    try:
        sys.path.insert(0, _BASE)
        from trpg.core import layout as layout                                                    # noqa: PLC0415
        return layout.must_locate(_BASE, table)
    except SystemExit:
        raise


def main() -> int:
    if len(sys.argv) < 3 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return 2
    table, cmd, rest = sys.argv[1], sys.argv[2], sys.argv[3:]
    with open(os.path.join(campaign_home(table), "config.json")) as fh:
        cfg = json.load(fh)
    port, tok = cfg["port"], cfg.setdefault("auth", {}).get("gm_token", "t")
    # **這張桌現在真的在哪**：hub（單一進程）模式＝`hub_base`（hub 的埠＋這一桌的前綴）；
    # 單桌模式＝自己的埠。⚠ 桌自己的埠在 hub 模式下**沒在聽** ⇒ 一律先看 hub_base（2026-10-07）。
    from trpg.core import layout as layout                                                       # noqa: PLC0415
    base = layout.api_base(cfg, port=port)

    import gql_client                                                  # noqa: PLC0415
    TABLE = str(cfg.get("table_id") or table)
    _SESS = gql_client.session_of(base, TABLE, gm=tok)                  # 每一格桌內操作都要帶

    def post(path, obj):
        """相容層：舊 REST 路徑 → **GraphQL**（`/api/*` 2026-10-07 退役）。呼叫點一字不動。"""
        obj = dict(obj or {})
        if path == "/api/gm":
            _op = str(obj.get("op") or "")
            _args = dict(obj.get("args") or {}) or {k: v for k, v in obj.items() if k not in ("op", "args", "token")}
            return gql_client.gm_op(base, TABLE, _SESS, _op, _args, gm=tok)
        if path == "/api/say":
            return gql_client.mutate(base, "say", {"seat": "gm", "text": obj.get("text")},
                                     table=TABLE, session=_SESS, gm=tok)
        raise RuntimeError("unknown path " + path)

    def gm(op, **args):
        return post("/api/gm", {"token": tok, "op": "semi", "args": {"op": op, **args}})

    if cmd == "say":
        out = post("/api/say", {"seat": "gm", "token": tok, "text": rest[0]})
    elif cmd == "npc":
        out = gm("npc_say", seat=rest[0], text=rest[1])
    elif cmd == "secret":
        out = post("/api/gm", {"token": tok, "op": "secret", "to": rest[0], "text": rest[1]})
    elif cmd == "act":
        out = gm("act_for", seat=rest[0], action=rest[1],
                 note=(rest[2] if len(rest) > 2 else ""),
                 target=(rest[3] if len(rest) > 3 else None))
    elif cmd == "props":
        out = gm("scene_props", props=json.loads(rest[0]))
    elif cmd == "close":
        out = gm("scene_close", force=True) if (rest and rest[0] == "force") else gm("scene_close")
    elif cmd == "open":
        out = (gm("scene_open", text=rest[0], bgm=rest[1]) if len(rest) > 1
               else gm("scene_open", text=rest[0]))
    elif cmd == "bgm":
        out = gm("audio_play", ch="bgm", key=rest[0])
    elif cmd == "sfx":
        out = gm("audio_play", ch="sfx", key=rest[0])
    elif cmd == "silence":
        out = gm("audio_stop", ch=(rest[0] if rest else "bgm"))
    elif cmd == "sounds":
        out = gm("audio_list")
    elif cmd == "state":
        out = gql_client.view(base, TABLE, _SESS, seat="gm", gm=tok)   # 舊 /api/state ⇒ view.raw
    else:
        print(f"ERROR: unknown command '{cmd}'", file=sys.stderr)
        return 2
    print(json.dumps(out, ensure_ascii=False)[:1500])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:                                   # noqa: BLE001
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
