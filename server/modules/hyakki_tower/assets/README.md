# assets/ — 百鬼塔（`hyakki_tower`）素材表

**這是生成物**：`index.json`（瘦清單，schema 2）＋ `index/<類>.json`（每類一份：`files`／`aliases`）
＋ 圖檔（WebP）。版控只收 `index*.json` 與這份 README，圖不進版控（`.gitignore`）。

## 目錄

```
assets/
  index.json            瘦清單：module／generated_at／source／parts／counts／totals
  index/portrait.json   立繪（10 檔／10 別名）
  index/avatar.json     頭像（10）
  index/enemy.json      敵人圖（9）
  index/bg.json         場景圖（14，依 board.json 節點）
  index/icon.json       小圖示（技能／道具／神器／星等／狀態／敵人）
  portrait/<角色id>/a02.webp    預設幀（別名 portrait:<角色>）
  avatar/<角色id>.webp          頭像（別名 avatar:<角色>）
  enemy/<敵人id>.webp           別名 enemy:<敵人>
  bg/<節點id>.webp              別名 bg:<節點>
  icon/<key>.webp               256²　別名見下（icon:*）
```

## 別名（投影只查這張表，不猜檔名）

* `portrait:<角色 id>`／`avatar:<角色 id>`：角色表（`characters.json`）裡的每一個人（玩家＋NPC 都有）。
* `npc:<id>`：`npcs.json` 的 NPC（指到同一個立繪檔）。
* `enemy:<id>`：`enemies.json` 的敵人。
* `bg:<node id>`：`scenarios/sannenme_no_onibarai/board.json` 的每一個節點。
* `icon:skill:<id>`／`icon:item:<id>`／`icon:condition:<id>`／`icon:monster:<id>`：四張表各自的 id。
* `icon:artifact:<tier>:<序>` ＋ `icon:artifact:name:<神器名>`：神器表（`artifacts.json`，tier 內第 n 件）；
  兩個都指同一個檔（投影用 name 查）。
* `icon:star:<1..4>`：星等（`stars.json` 的第 n 列）。

**別名一律從資料表讀 id**（`_hyakki_assets_build.py` 的 `icon_aliases()`），不手抄——加一條技能／道具，
重跑一次就有別名；圖沒生出來就只是查不到（投影不加圖，不會壞）。

## 怎麼重生成（可重跑）

```bash
cd workspace
.venv-yolo/bin/python _hyakki_assets_gen.py            # Krea2 anime v1.5 t2i → hyakki_assets_src/<kind>/<id>.png
.venv-yolo/bin/python _hyakki_assets_gen.py --force --only homura   # 只重畫某張
.venv-rb/bin/python  _hyakki_assets_build.py           # 摳圖（rembg/isnet-anime）＋縮放＋WebP＋index.json
.venv-yolo/bin/python _hyakki_icons_build.py           # 小圖示（技能／道具／神器／星等／狀態／敵人；已存在的跳過）
.venv-yolo/bin/python _hyakki_icons_build.py --only=mon_gaki   # 只補某幾張
.venv-yolo/bin/python _hyakki_assets_build.py --index-only     # **生完圖一定要跑這一步**：重掃目錄＋重建別名
PY=python3
$PY tools/assets_index.py split                        # 拆成 index/<類>.json ＋ schema 2 manifest
$PY tools/assets_index.py check --strict-files          # 交付前一定要 0 錯
```

規格：立繪 `h=1536`、頭像 `256`、敵人 `h=1024`、場景 `w=1536`、小圖示 `256²`（Krea2 出 768×1152 後取中心方塊）。

**小圖示（`icon:*`，2026-10-20 補齊）**：技能 24／道具 5／神器 6／星等 4／狀態 8／敵人 9＝**56 張**。
畫的是鉄環地方／百鬼塔的和風味道（同一條 T1 `krea2-moody-t2i` recipe、與示範模組 那套同一組深藍底尾綴，
兩邊擺在一起不會一個亮一個糊）。要換某一張的畫法＝改 `_hyakki_icons_build.py` 的 `ICONS` 清單那行 prompt，
刪掉對應 `.webp` 再跑（沒刪會被「已存在」跳過）。
