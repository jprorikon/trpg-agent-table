#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**L6｜桌的位置**（WS-B：模組 → 劇本 三層；舊 `campaigns/<桌>/` 相容）

設計者 2026-10-07 拍的三層（規格見 `docs/SCENARIO_FORMAT.md`）：

```
modules/<模組>/                        ← 世界層：「這個模組所需的所有角色」（characters.json）
  scenarios/<劇本>/                    ← 劇本層：定義（**只讀**；模組 repo 的內容）
      scenario.json                   劇本的規則資料（**舊名 `module.json` 相容**）
      board.json / seats.json / goals.json / clues.json / WORLD.md / GM.md / TABLE.md
      packs/                          作品層包（模組的內容）
state/tables/<模組>/<劇本>/<桌ID>/       ← **桌的實體（instance）**：config.json／runs/（不進版控、不屬於模組 repo）
```

舊制 `campaigns/<桌>/` 是「模組＋劇本＋地圖＋角色表」揉在一層的歷史形狀；
`modules/<劇本>/tables/<桌ID>/`（2026-10-07～10-19 的形狀）也還讀得到；
**相容期全都讀**（這一支是唯一的定位入口，`table_directory`／`status.sh` 都走它）。

**為什麼桌搬出模組目錄（2026-10-19 設計者拍「桌的實體搬出模組目錄」）**：模組＝世界觀的內容，
住在自己的 repo（`trpg/module-*`）；**桌的 config／事件流是這一台機器上的執行狀態**，跟著機器走，
不該跟著內容 repo 走。⇒ 模組 repo 裡不再有任何 `tables/`。

⚠ **桌 id 與目錄名解耦**：對外 id 一律讀該目錄 `config.json` 的 `table_id`
（目錄可以叫好聽的名字，既有客戶端看到的 id 不變 ⇒ 搬目錄不會驚動 協作端／橋／app／DB）。
"""
import json
import os

from trpg.core import store as store

from typing import Any, Dict, List, Optional

MODULES_DIR = "modules"
SCENARIOS_DIR = "scenarios"
TABLES_DIR = "tables"          # ① `<repo>/state/tables/<模組>/<劇本>/<桌ID>/`（現行）；② 舊制 `<劇本>/tables/<桌ID>/`
LEGACY_DIR = "campaigns"
STATE_DIR = "state"            # 執行時的狀態（桌的實體）；**不進版控**、不屬於任何模組 repo


def state_root(root: str) -> str:
    """這一台機器的**狀態根** `<repo>/state/`（桌的實體、事件流都在底下）。"""
    return os.path.join(root, STATE_DIR)


def tables_root(root: str) -> str:
    """**桌的實體**住的根：`<repo>/state/tables/`（模組目錄裡不再有桌）。"""
    return os.path.join(state_root(root), TABLES_DIR)


def table_home(root: str, module: str, scenario: str, tid: str) -> str:
    """一張桌的實體目錄：`<repo>/state/tables/<模組>/<劇本>/<桌ID>/`（建新桌時唯一的組法）。"""
    return os.path.join(tables_root(root), str(module), str(scenario), str(tid))


def _split_state_home(home: str) -> Optional[tuple]:
    """`<repo>/state/tables/<模組>/<劇本>/<桌ID>` ⇒ `(repo, 模組, 劇本)`；不是這形狀回 `None`。

    為什麼要它：`def_dir_of`／`where` 只拿得到 home，卻要推出「定義在哪、是哪個模組的劇本」。
    靠路徑形狀推（不靠 `root`）⇒ 臨時桌／測試樹也成立。
    """
    parts = os.path.normpath(os.path.abspath(home)).split(os.sep)
    if len(parts) >= 5 and parts[-4] == TABLES_DIR and parts[-5] == STATE_DIR:
        return (os.sep.join(parts[:-5]) or os.sep, parts[-3], parts[-2])
    return None


def def_file(home: str, name: str) -> str:
    """劇本層定義檔（`seats.json`／`board.json`／`goals.json`…）的路徑。

    **桌目錄有就用它**（2026-10-19 之前建的桌把定義複製進桌目錄了，相容），沒有就回**劇本目錄**
    那一份——拆層之後桌目錄只剩 `config.json`／`runs/`，定義一律住劇本層（一處改、每桌吃到）。

    為什麼要有它（2026-10-20 實測）：`_chars_available`／`_seats_of`／`cast_of` 都直接讀
    `<桌目錄>/seats.json` ⇒ 新建的桌（桌目錄沒有 seats.json）選角時一律「這一局沒有可選的 X」，
    app 選了角色也進不去。
    """
    p = os.path.join(home, name)
    if os.path.exists(p):
        return p
    d = def_dir_of(home)
    return os.path.join(d, name) if d else p


def def_dir_of(home: str) -> str:
    """這一張桌的**定義目錄**（＝劇本層）：`state/tables/<模組>/<劇本>/<桌ID>/` ⇒ 模組裡的劇本目錄。

    定義＝劇本層**共享**的東西（`seats.json`／`board.json`／`scenario.json`／`goals.json`／
    `clues.json`／`packs/`…）——引擎**只讀**它。桌自己的檔（`config.json`／`runs/`）一律在桌目錄底下，
    所以同一劇本的多張桌共用同一份定義（一處改、每一桌都吃到）。

    相容：舊制 `<劇本>/tables/<桌ID>/`（模組目錄裡）⇒ 上一層的劇本目錄；更舊的扁平桌 ⇒ 自己。
    """
    p = os.path.normpath(os.path.abspath(home))
    sp = _split_state_home(p)
    if sp:
        root, mod, scn = sp
        return os.path.join(module_dir(root, mod), SCENARIOS_DIR, scn)
    if os.path.basename(os.path.dirname(p)) == TABLES_DIR:
        return os.path.dirname(os.path.dirname(p))
    return p


def read_table_config(dir_path: str) -> Dict[str, Any]:
    """讀一份「桌或劇本目錄」的 config：`config.json` 優先，沒有就退回 `config.example.json`。

    ⚠ 2026-10-21：現役桌可以整批退役（設計者的指示），但測試與工具要的只是**旋鈕**——
    桌目錄不在了就讀劇本層的範例檔，不要讓整套測試因為「桌上沒人」而全紅。
    """
    for name in ("config.json", "config.example.json"):
        path = os.path.join(dir_path, name)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
    raise FileNotFoundError(f"{dir_path} 沒有 config.json／config.example.json")


def copy_table_config(src_dir: str, dst_path: str) -> None:
    """把「桌或劇本目錄」的 config 複製到 `dst_path`（沒有 config.json 就用 example 的內容）。"""
    cfg = read_table_config(src_dir)
    os.makedirs(os.path.dirname(dst_path) or ".", exist_ok=True)
    with open(dst_path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=1)


def read_config(home: str) -> Optional[Dict[str, Any]]:
    """讀一張桌的 `config.json`（壞檔／不存在回 None——定位不該因為壞檔整批掛掉）。"""
    try:
        with open(os.path.join(home, "config.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:                                                     # noqa: BLE001
        return None


def scan(root: str) -> List[Dict[str, Any]]:
    """掃出所有「桌的家目錄」（**現行 `state/tables/` 優先 → 舊制 modules → 最舊 campaigns**；只定位，不讀 config 內容）。

    回：`{"home", "module", "scenario", "legacy", "module_name", "scenario_name", "def_dir"}` 的列表（排序穩定）。
    """
    out: List[Dict[str, Any]] = []
    # ① 現行（2026-10-19 起）：`state/tables/<模組>/<劇本>/<桌ID>/`＝桌的實體（不屬於模組 repo）
    t_root = tables_root(root)
    if os.path.isdir(t_root):
        for mod in sorted(os.listdir(t_root)):
            mroot = os.path.join(t_root, mod)
            if not os.path.isdir(mroot):
                continue
            for scn in sorted(os.listdir(mroot)):
                sroot = os.path.join(mroot, scn)
                if not os.path.isdir(sroot):
                    continue
                for tid in sorted(os.listdir(sroot)):
                    home = os.path.join(sroot, tid)
                    if os.path.isfile(os.path.join(home, "config.json")):
                        out.append({"home": home, "module": mod, "scenario": scn, "legacy": False,
                                    "module_name": module_name(root, mod),
                                    "scenario_name": scenario_name(root, mod, scn),
                                    "def_dir": os.path.join(module_dir(root, mod), SCENARIOS_DIR, scn)})
    # ② 相容（2026-10-07～10-19）：桌住模組目錄裡 `modules/<模組>/scenarios/<劇本>[/tables/<桌ID>]/`
    mods = os.path.join(root, MODULES_DIR)
    if os.path.isdir(mods):
        for mod in sorted(os.listdir(mods)):
            scn_root = os.path.join(mods, mod, SCENARIOS_DIR)
            if not os.path.isdir(scn_root):
                continue
            for scn in sorted(os.listdir(scn_root)):
                home = os.path.join(scn_root, scn)
                if not os.path.isdir(home):
                    continue
                if os.path.isfile(os.path.join(home, "config.json")):
                    # 舊制／第一桌：劇本目錄自己就是一張桌（定義與桌檔同層）
                    out.append({"home": home, "module": mod, "scenario": scn, "legacy": False,
                                "module_name": module_name(root, mod),
                                "scenario_name": scenario_name(root, mod, scn), "def_dir": home})
                tdir = os.path.join(home, TABLES_DIR)
                if os.path.isdir(tdir):
                    for tid in sorted(os.listdir(tdir)):            # ★ 多桌：一桌一個子目錄
                        thome = os.path.join(tdir, tid)
                        if os.path.isfile(os.path.join(thome, "config.json")):
                            out.append({"home": thome, "module": mod, "scenario": scn, "legacy": False,
                                        "module_name": module_name(root, mod),
                                        "scenario_name": scenario_name(root, mod, scn), "def_dir": home})
    leg = os.path.join(root, LEGACY_DIR)
    if os.path.isdir(leg):
        for name in sorted(os.listdir(leg)):
            home = os.path.join(leg, name)
            if os.path.isfile(os.path.join(home, "config.json")):
                out.append({"home": home, "module": name, "scenario": "", "legacy": True,
                            "def_dir": home})
    return out


def table_id_of(row: Dict[str, Any]) -> str:
    """這一張桌的**對外 id**：`config.json` 的 `table_id`，沒有才退回目錄名。"""
    cfg = read_config(row["home"]) or {}
    return str(cfg.get("table_id") or os.path.basename(row["home"]))


def locate(root: str, table_id: str) -> Optional[Dict[str, Any]]:
    """找某一張桌（新制優先；id 對不上時，目錄名也當 id 認——舊制習慣）。"""
    rows = scan(root)
    for row in rows:
        if table_id_of(row) == table_id:
            return row
    for row in rows:
        if os.path.basename(row["home"]) == table_id:
            return row
    return None


def scenarios(root: str, module: str) -> List[str]:
    """這個模組有哪些**劇本**（`modules/<模組>/scenarios/*/` 的目錄名）。

    劇本是**定義**那一層：同一劇本可以開好幾張桌（`tables/<桌ID>/`）。
    """
    d = os.path.join(module_dir(root, module), SCENARIOS_DIR)
    if not os.path.isdir(d):
        return []
    return sorted(n for n in os.listdir(d) if os.path.isdir(os.path.join(d, n)))


def api_base(cfg: Dict[str, Any], port: Any = None) -> str:
    """**這張桌的 API 在哪**（別的行程打得到的 URL）——一律是 **hub 根**，不帶桌前綴。

    2026-10-19 拆前後台之後，後台只剩 `POST /graphql`／`POST /mcp`（桌是 `table(id:)` 欄位），
    桌級子路徑（`/<桌>/graphql`、`/tables/<run>/…`）全沒了 ⇒ 從前把「hub 埠 ＋ 桌前綴」當
    base 的寫法（`hub_base`）會打到 404。這裡把舊值**正規化掉**：`hub_base` 若以這張桌的
    `public_prefix` 結尾，就把前綴剝掉（舊 config 不必手改就能用）。

    優先序：`cfg.hub_base`（剝前綴）＞ hub 進程裡（`TRPG_HUB_ROOT`）用 `TRPG_HUB_PORT`
    ＞ `cfg.port`（單桌模式）。
    """
    pre = str(cfg.get("public_prefix") or "").rstrip("/")
    raw = str(cfg.get("hub_base") or "").strip().rstrip("/")
    if raw:
        if pre and raw.endswith(pre):
            raw = raw[: -len(pre)].rstrip("/")
        return raw
    if os.environ.get("TRPG_HUB_ROOT"):
        return f"http://127.0.0.1:{int(os.environ.get('TRPG_HUB_PORT') or 8971)}"
    try:
        return f"http://127.0.0.1:{int(port or cfg.get('port') or 8975)}"
    except Exception:                                                   # noqa: BLE001
        return "http://127.0.0.1:8975"


def repo_root(home: str) -> str:
    """`<repo>/{state/tables/…|modules/…|campaigns/<桌>}` ⇒ `<repo>`。

    現行桌住 `state/tables/<模組>/<劇本>/<桌ID>/`（五層深）⇒ 先靠 `state/tables` 這兩段直接切；
    切不到才**往上找「像 repo 根」的那一層**（有 `hub_server.py`，或同時有 `campaigns`／`modules`），
    找不到才退回舊的兩層算法。
    """
    here = os.path.abspath(home)
    sp = _split_state_home(here)
    if sp:
        return sp[0]
    d = here
    for _ in range(8):
        d = os.path.dirname(d)
        if not d or d == os.path.dirname(d):
            break
        if os.path.exists(os.path.join(d, "hub_server.py")):
            return d
        if os.path.isdir(os.path.join(d, "campaigns")) or os.path.isdir(os.path.join(d, "modules")):
            return d
    return os.path.dirname(os.path.dirname(here))


def must_locate(root: str, table_id: str) -> str:
    """同 `locate`，但**找不到就大聲死**（測試／工具要一個保證存在的家目錄時用）。

    ⇒ 呼叫端**不再硬編目錄形狀**（`campaigns/<桌>` vs `modules/<模組>/scenarios/<劇本>`），
    WS-B 之後搬目錄不必改測試。
    """
    row = locate(root, table_id)
    if not row:
        raise SystemExit(f"[layout] 在 {root} 底下找不到桌 {table_id!r}"
                         f"（掃到的：{[r['home'] for r in scan(root)]}）")
    return row["home"]


def must_locate_scenario(root: str, module: str, scenario: Optional[str] = None) -> str:
    """找「模組 × 劇本」底下的**代表桌**（找不到就大聲死）。

    2026-10-20「桌 = run」之後，一個 run 就是一個 home ⇒ **桌 id 會隨局改變**，
    呼叫端不能再硬編桌 id（以前測試寫死 `"hyakki_tower"` 這張桌，遷移後就找不到）。
    這裡改用「模組 × 劇本」定位，並在候選裡挑**事件流最長**的那張（＝主線那一局；
    同一劇本的其他 run 都是它的分支／測試局，config 可能是模板值）。
    """
    rows = [r for r in scan(root)
            if str(r.get("module")) == str(module)
            and (scenario is None or str(r.get("scenario")) == str(scenario))]
    # ★ 2026-10-21：`smoke_*` 是測試／演示用的臨時桌（旋鈕可能被改過、難度也可能是 hard）——
    #   測試解析「現役桌」時要跳過它們，否則跑著一張 demo 桌就會讓整套測試莫名其妙變紅。
    def _is_temp(row: dict) -> bool:
        for key in ("run", "run_id", "table_id"):
            if str(row.get(key) or "").startswith("smoke_"):
                return True
        return "smoke_" in os.path.basename(str(row.get("home") or ""))

    rows = [r for r in rows if not _is_temp(r)]      # 只剩臨時桌 ⇒ 當作「沒有現役桌」，退回劇本層
    if not rows:
        # 2026-10-21：現役桌可以整批退役 ⇒ 沒有桌的時候退回劇本層（測試／工具要的是定義與旋鈕）
        if scenario:
            d = os.path.join(module_dir(root, module), SCENARIOS_DIR, scenario)
            if os.path.isdir(d):
                return d
        raise SystemExit(f"[layout] 在 {root} 底下找不到 {module}×{scenario or '*'} 的桌"
                         f"（掃到的：{[r['home'] for r in scan(root)]}）")

    def _events(home: str) -> int:
        runs = os.path.join(home, "runs")
        total = 0
        if not os.path.isdir(runs):
            return 0
        for name in os.listdir(runs):
            p = os.path.join(runs, name, "events.jsonl")
            if os.path.isfile(p):
                try:
                    with open(p, encoding="utf-8") as fh:
                        total += sum(1 for _ in fh)
                except OSError:
                    continue
        return total

    rows.sort(key=lambda r: (-_events(str(r["home"])), str(r["home"])))
    return str(rows[0]["home"])


def events_dir(home: str, session: Optional[str] = None) -> str:
    """這一局的目錄（**一局一檔**：`runs/<session>/`；舊制＝`data/`）。

    規則的正本在 **`store`**（落盤層同時是檔案的擁有者）——這裡只是轉呼，不要另寫一份。
    """
    return os.path.dirname(store.events_path(home, session))


def events_path(home: str, session: Optional[str] = None) -> str:
    """這一局的**事件流**路徑（＝這一張桌的「現役那一局」；`session` 可指定某一局）。

    WS-B 階段 1c：`runs/<session>/events.jsonl`（一局一檔）；舊制 `data/events.jsonl` 相容。
    """
    return store.events_path(home, session)


def module_dir(root: str, module: str) -> str:
    """模組的世界層目錄 `modules/<模組>/`。"""
    return os.path.join(root, MODULES_DIR, module)


def modules(root: str) -> List[str]:
    """這一台裝了哪些**模組**（`modules/*/` 的目錄名）。

    為什麼要它（2026-10-20 實測）：大廳「建新桌」的模組／劇本選單原本只從**現有的桌**反推
    （`table_directory.discover`）⇒ 某個劇本的最後一張桌一被關掉／hidden，那個劇本就在選單裡
    **消失**、再也開不回來（實際踩到：示範劇本關桌後選不到）。劇本是**定義層**，選單要看
    定義層，不是看現在有幾張桌。
    """
    d = os.path.join(root, MODULES_DIR)
    if not os.path.isdir(d):
        return []
    return sorted(n for n in os.listdir(d)
                  if os.path.isdir(os.path.join(d, n)) and not n.startswith("."))


def module_meta(root: str, module: str) -> Dict[str, Any]:
    """模組的**世界層設定**：`modules/<模組>/module.json`（`{id, name, note}`…）；沒有就 `{}`。

    為什麼要有它：模組需要**自己給自己一個命名**（設計者 2026-10-07：「让模组给一个命名」）——
    大廳／app 的桌清單按模組分類，標題不該只是目錄名。散文（`WORLD.md`）不當機器可讀來源。
    """
    p = os.path.join(module_dir(root, module), "module.json")
    try:
        with open(p, encoding="utf-8") as fh:
            got = json.load(fh)
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def characters(root: str, module: str) -> Dict[str, Dict[str, Any]]:
    """模組的**角色表**：`modules/<模組>/characters.json` 的 `characters` ⇒ `{角色ID: 角色}`。

    **角色的類（`class`）由模組決定**（設計者 2026-10-07）；`scenarios/<劇本>/seats.json` 只決定
    這一局**哪些角色參與、坐哪一席**。所以「建立新角色」＝在模組底下給這個帳號開一個角色**實例**，
    候選一律從這一張表來（不是從席位表發明）。
    """
    p = os.path.join(module_dir(root, module), "characters.json")
    try:
        with open(p, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for row in (doc.get("characters") or []):
        if isinstance(row, dict) and row.get("id"):
            out[str(row["id"])] = row
    return out


def module_name(root: str, module: str) -> str:
    """模組顯示名：`module.json` 的 `name` → 沒有就退回模組 id（目錄名）。"""
    return str((module_meta(root, module) or {}).get("name") or module)


def scenario_difficulties(root: str, module: str, scenario: str) -> List[Dict[str, Any]]:
    """劇本支援的**開局難度**檔位（`scenario.json` 的 `starting_difficulty`）。

    回 `[{id, label, label_jp, doc, default}]`；沒有這一段 ⇒ `[]`（＝這張劇本不吃難度，
    大廳建桌就不用顯示選單）。順序照劇本檔（easy→normal→hard）。
    """
    p = os.path.join(module_dir(root, module), SCENARIOS_DIR, str(scenario), "scenario.json")
    if not os.path.exists(p):
        # 舊名相容（`scenario.resolve_module_path` 的口徑：新名優先、退回 module.json）
        p = os.path.join(module_dir(root, module), SCENARIOS_DIR, str(scenario), "module.json")
    try:
        with open(p, "r", encoding="utf-8") as fh:
            d = json.load(fh)
    except Exception:                                                     # noqa: BLE001
        return []
    spec = d.get("starting_difficulty")
    if not isinstance(spec, dict):
        return []
    default = str(spec.get("default") or "")
    out: List[Dict[str, Any]] = []
    for k, v in (spec.get("levels") or {}).items():
        if not isinstance(v, dict):
            continue
        out.append({"id": str(k),
                    "label": str(v.get("label") or k),
                    "label_jp": str(v.get("label_jp") or v.get("label") or k),
                    "doc": str(v.get("doc") or ""),
                    "default": bool(str(k) == default)})
    return out


def scenario_name(root: str, module: str, scenario: str) -> str:
    """劇本的**顯示名**：`scenarios/<劇本>/scenario.json` 的 `meta.name`（或頂層 `name`）→ 沒寫就退回劇本 id。

    為什麼住 `meta.name`：`meta` 本來就是劇本定義的自由區（引擎只讀 `entities`／`requirements`／…），
    不必多開一個檔。跟模組層的 `module.json` 一樣：**機器可讀、壞掉不炸、退回目錄名**。
    ```
    {"meta": {"name": "北の荒道", ...}, "entities": ...}
    ```
    """
    sc = str(scenario or "")
    if not sc:
        return ""
    p = os.path.join(module_dir(root, module), SCENARIOS_DIR, sc, "scenario.json")
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:                                                      # noqa: BLE001
        return sc
    m = d.get("meta") if isinstance(d.get("meta"), dict) else {}
    return str((m or {}).get("name") or d.get("name") or sc)


def where(home: str, root: str) -> Dict[str, str]:
    """回這一張桌在分層裡的身分：`{"module","scenario"}`（舊制 ⇒ module＝目錄名、scenario＝""）。

    先問 `scan(root)`（權威：root 底下真的掃到的那一筆）；**掃不到就從路徑自己解**——
    測試／臨時桌的家常常不在 `root` 底下，那時候靠相對關係會推出垃圾
    （實測踩過：把「scenarios」當成模組名 ⇒ 角色表永遠查不到）。
    """
    for row in scan(root):
        if os.path.abspath(row["home"]) == os.path.abspath(home):
            return {"module": row["module"], "scenario": row["scenario"]}
    sp = _split_state_home(home)                     # 現行形狀：`state/tables/<模組>/<劇本>/<桌ID>`
    if sp:
        return {"module": sp[1], "scenario": sp[2]}
    parts = os.path.normpath(os.path.abspath(home)).split(os.sep)
    if MODULES_DIR in parts:
        i = len(parts) - 1 - parts[::-1].index(MODULES_DIR)      # 最後一個 `modules` 段
        rest = parts[i + 2:]
        return {"module": parts[i + 1] if i + 1 < len(parts) else "",
                "scenario": (rest[1] if len(rest) >= 2 and rest[0] == SCENARIOS_DIR else "")}
    if LEGACY_DIR in parts:
        i = len(parts) - 1 - parts[::-1].index(LEGACY_DIR)
        return {"module": parts[i + 1] if i + 1 < len(parts) else "", "scenario": ""}
    return {"module": os.path.basename(os.path.dirname(home)), "scenario": ""}
