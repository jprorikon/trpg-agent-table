**日本語** | [English](README.en.md) | [简体中文](README.zh-CN.md)

# TRPG Agent Table：人間と AI エージェントが同じ卓を囲む TRPG エンジン

オンライン TRPG 卓のサーバーと Web フロントエンドです。目指しているのは、**LLM エージェントを人間のプレイヤーと同じ卓に座らせつつ、
ズル・他人の手札の盗み見・ダイス結果の捏造を構造的にできなくすること**です。

```
human  ── Web（GraphQL）────┐
                            ├──►  server.py（エンジンを持つ唯一のプロセス）──► trpg/engine/（権威層：ルール／ダイス／イベント）
agent  ── MCP ツール ───────┘                │
                                             └─► runs/<session>/events.jsonl（追記のみ・リプレイ可能）
「あなたの番です」 ◄── kick（送信 webhook。アカウント単位でエージェントを起こす）
```

## 設計の三原則

1. **数値はエンジンだけが出す。** ダイス・HP・ダメージ・通貨はすべて `server/trpg/engine/` で計算します。
   エージェントにできるのは「行動の宣言」（`table_act`）だけで、状態を書き換える経路はありません。
2. **情報ファイアウォール。** `view()` は席ごとに状態を射影します。秘密の願望・手がかりの断片・私信は持ち主にしか見えません。
   GM の語りと NPC の台詞は同じ**漏洩ゲート**を通り、他の席の秘密の原文を口にすればエンジンが拒否します。
3. **すべてリプレイ可能。** 状態はイベント列の畳み込み（イベントソーシング）です。`op=replay` で最初から再生し、
   メモリ上の状態とフィールド単位で突き合わせます。テストではこれを不変条件として多用しています。

## 見どころ

| テーマ | 場所 | 内容 |
|---|---|---|
| イベントソーシングとリプレイ | `server/trpg/core/replay.py`、`store.py` | 1 セッション 1 本の `events.jsonl`。再起動しても卓は失われず、リプレイ一致がテストの不変条件 |
| 射影と情報ファイアウォール | `server/trpg/engine/projection/` | 同じ状態を「誰が見ているか」で射影。観戦面には公開射影のみ |
| MCP アダプタ | `server/trpg/api/mcp_api/`、`mcp_adapter.py` | 7 ツール（`table_view/act/say/tell/join/gm/secret`）。卓の設定に応じてツール集合が変わる |
| GraphQL | `server/trpg/api/graphql/`、`docs/schema.graphql` | クライアント向けの唯一のエンドポイント。匿名の読み取り（観戦）はクエリ単位でアクセス制御 |
| ターン制とエージェントの起床 | `server/trpg/service/wake/`、`trpg/kick/` | 席ではなく**アカウント**単位で起こす。冪等キーで重複排除し、1 回の通知で必要な文脈を渡し切る |
| ルールパックの三層構造 | `server/trpg/rules/packs/`、`server/packs/README.md` | エンジン層 → モジュール層 → シナリオ層。内容（スキル／アイテム／モンスター／ショップ表）と仕組みを分離 |
| シナリオ DSL | `server/trpg/rules/dsl.py`、`expr.py`、`scenario/` | 願望の判定・トリガー・エンディングはすべて JSON AST。コードを書かずに作れる |
| 単一プロセスの複数卓ハブ | `server/trpg/api/hub/`、`docs/HUB.md` | 1 ポートで全卓を配信（サブパスで卓を分ける）。一度のログインで全卓を回れる |
| エージェント試遊ツール | `server/agent_harness/` | 複数のエージェントに自動で卓を回させ、手の分布を集計（シナリオのテンポ調整用） |

## 規模

- エンジン：Python 約 **2.1 万行**（`server/trpg/`、core／engine／rules／api の層構造）
- テスト：**55 ファイル・約 1.7 万行**、すべて成功（実 HTTP サーバー・MCP クライアント・GraphQL の E2E を含む）
- フロントエンド：単一ファイル・ビルド不要（`frontend/web/index.html`、素の JS、3 言語、ライト／ダークテーマ）
- 依存は最小限：標準ライブラリ ＋ `vendor/` の graphql-core / pg8000。MCP エンドポイントには公式 `mcp` SDK が必要

## ディレクトリ構成

```
server/              エンジンとサーバー（Python）
  trpg/                core（イベント保存／リプレイ）· engine（卓・射影・ターン）· rules（ルール）· api（GraphQL／MCP／ハブ）
  packs/               エンジン層の機制パック（d20core、board、death、wound…）
  modules/hyakki_tower デモモジュール「百鬼塔」（オリジナル世界観：キャラ・マップ・シナリオ・ルールブック）
  tests/               テスト 55 ファイル。入口は tools/run_tests.sh のみ
  docs/                仕様書（API／AGENT／HUB／SCENARIO_FORMAT…）
frontend/            Web フロント（単一の index.html ＋ lobby.html）＋ローカルのモック卓 mock_server
scenario-template/   シナリオ作者向けのテンプレート・書式説明・セルフチェック（check_module.sh）
```

## 動かし方

Python **3.10 以上**が必要です。

```bash
cd server
TRPG_PY=python3.12 bash tools/run_tests.sh              # 全テスト
TRPG_PY=python3.12 bash tools/run_tests.sh test_board   # 1 本だけ（盤面プレイの一連の流れ）
```

一部のテストはローカルに HTTP サーバー（127.0.0.1 のランダムポート）を立てます。MCP 関連のアサーションには公式 SDK が必要です：
`pip install "mcp<2"`（未インストールの場合、該当項目が「mcp SDK 可用」で失敗と明示され、他のテストは通常どおり走ります）。

卓を 1 つ起動する（開発モード）：

```bash
cd server
S=modules/hyakki_tower/scenarios/sannenme_no_onibarai
cp $S/config.example.json $S/config.json      # auth.gm_token などを記入（config.json はバージョン管理外）
python3 server.py --config $S/config.json --home $S --port 8971
# エージェント用 MCP エンドポイント：http://127.0.0.1:8971/mcp
```

起動・停止の詳細は `server/docs/RUN.md`、エージェントの接続方法は `server/docs/AGENT.md` を参照してください。

## デモコンテンツについて

`server/modules/hyakki_tower/`（百鬼塔）は**オリジナル**の世界観で、キャラクター・マップ・シナリオ・数値はすべて本プロジェクトで作成したものです。
素材画像はローカルの生成モデルで作っており、バージョン管理には含めていません（リポジトリにあるのは素材一覧 `assets/index*.json` のみ）。
`frontend/fixtures/` は、このモジュールでローカルに短いデモセッションを回し、`frontend/tools/grab_fixtures.py` で取得したスナップショットです。

## 既知の制限

- `frontend/mock/mock_server.py`（モック卓）は旧 REST API しか模していません。フロントエンドは GraphQL ログインに移行済みのため、
  モック卓ではログイン画面で止まります。画面全体を見るには実際の卓（ハブ）につなぐ必要があります。
- コードのコメントとドキュメントは主に中国語（繁体／簡体）と日本語です。
