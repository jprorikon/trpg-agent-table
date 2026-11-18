# 百鬼塔 モジュール（`hyakki_tower`）

この倉庫＝**一つの世界観（モジュール）**。サーバー（`trpg/server`）から分離した単独リポジトリで、
倉庫の根＝サーバー側の `modules/hyakki_tower/` の中身。

## 構成

| パス | 役割 |
|---|---|
| `module.json` | モジュールの表示名・注記（`{id, name, note}`） |
| `characters.json` | このモジュールで使える全キャラ（`class` はモジュールが決める）。現役 10 名＝玩家 5／NPC 5 |
| `npcs.json`／`enemies.json` | NPC・敵の名簿（`portrait_dir`＝素材ディレクトリ名）。**数値と出没規則は `packs/*/tables/monsters.json`**（エンジンが読むのはそちら） |
| `assets/` | モジュール級の素材表（`index.json` ＋ `index/<類>.json` ＋ WebP）。別名は `portrait:`／`avatar:`／`npc:`／`enemy:`／`bg:`。図は版管理しない、清單（`index*.json`）だけ版管理 |
| `docs/` | （2026-10-20 已清空：角色／技能／素材的擴充記錄已併進 `assets/README.md`、`characters.json` 與各 `packs/*/README.md`；未決事項在 `trpg/server` 的 `docs/PENDING.md`） |
| `scenarios/<劇本>/` | 劇本＝定義層。`scenario.json`／`board.json`／`seats.json`／`goals.json`／`clues.json`／`config.example.json`／`WORLD.md`／`GM.md`／`TABLE.md`／`packs/` |
| `packs/<id>/` | **モジュール層の機制包**（作品の内容包：技能／道具／星等の表など）。同じモジュールの全劇本が共用（劇本の `packs/` に同名があればそちらが優先）。エンジン repo には作品の内容を置かない |
| `text.json` | **サーバー側の語り口**（塔のナレーション・ログの補講行・喚醒メッセージ・棋盤のエラー・MCP ツール説明・動作名）。敵＝鬼、貨幣＝銭、最上階の箱＝鬼の宝櫃。書いていない鍵はエンジンの中性既定文。鍵は `trpg/server` の `presentation.TEXT`（＋`action.<id>.label／desc`）。変更は新しいイベントにだけ効く |
| `audio.json` | **このモジュールの音訊表**（BGM／効果音：`tracks`＝別名→ファイル・音量・ループ・曲名、`cues`＝どのエンジンイベントで自動的に流すか）。エンジンは再生制御だけを行う（`trpg/server` の `docs/AUDIO.md`）。**版管理するのはこの表だけ**——音声ファイル（`assets/audio/**`）は図と同じく生成物／作者用意の素材で、版管理しない |

## 新しい劇本を足す

1. `scenarios/` の下に**意味の分かる id** でディレクトリを作る（`main`／`example` のような placeholder は不可）。
2. 既存の劇本か `trpg/scenario-template` の `scenario-template/` を雛形にコピーし、
   `scenario.json` の `meta.id`／`meta.name`、`board.json`、`seats.json`、`goals.json`、`clues.json`、`packs/` を書く。
3. 作者セルフチェック（`trpg/scenario-template` の一鍵入口。ERROR 0 にする）:
   `<scenario-template>/tools/check_module.sh <このモジュールのディレクトリ>`
   ＝各劇本を `/tmp` の臨時卓にして起桌と同じ判據で見る（実卓・実 state は触らない）。
   `config.example.json` は新劇本の雛形（`config.json` は卓ごと＝版管理しない）。
4. commit → push。サーバー側は `modules/hyakki_tower` で `git pull`、hub は `/__hub/rescan`。

## サーバーへの配置

サーバー repo（`trpg/server`）は `modules/` を版管理しない。デプロイ先で:

```sh
git clone <このリポジトリ> <server>/modules/hyakki_tower
```

## 版管理しないもの

`config.json`／`accounts.json`／`runs/`／`*.bak-*`。
**卓（テーブル）の実体はこの repo の外**——サーバー側の `state/tables/<モジュール>/<劇本>/<卓ID>/`
にあり、サーバー repo も版管理しない（2026-10-19 にモジュールディレクトリから搬出）。
