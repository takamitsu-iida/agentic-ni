# agentic-ni

**AI 駆動型ネットワーク設計検証 自律エージェントシステム**

人間が要件を自然言語で入力するだけで、ネットワーク設計・CML デプロイ・検証・障害シミュレーション・トラブルシューティング・実機投入までを AI エージェントが自律的に実行します。

<br>

---

## 何ができるのか

| モード | コマンド例 | 概要 |
|---|---|---|
| **設計・検証** | `agentic-ni demo` | 要件から CML トポロジーとコンフィグを自動生成し、pyATS でテストが全 PASS するまでループ |
| **ドライラン** | `agentic-ni demo --dry-run` | CML デプロイなし。設計ファイル（topology.yaml / .cfg / IP 台帳 / ルーティング設計書）のみ生成 |
| **トポロジー固定** | `agentic-ni demo3 --use-topology` | 手動作成した topology.yaml を使い、コンフィグのみ AI が生成 |
| **障害シミュレーション** | `agentic-ni demo2 --fault-sim` | 検証成功後にリンク断・復旧・再テストを自動実行し冗長性を検証 |
| **トラブルシューティング** | `agentic-ni demo2 --troubleshoot` | 既存ラボに接続し、診断 → 修正 → 検証のサイクルで問題を自動解決 |
| **設計分析** | `agentic-ni demo --analyze` | 稼働中ラボの設計品質を AI が評価してレポートを出力（変更なし） |
| **設計改善** | `agentic-ni demo --improve` | 改善コンフィグを生成して `configs/<セット>/` に保存 |
| **実機適用** | `agentic-ni demo --apply-to-live` | CML で検証済みのコンフィグを実機へ投入（バックアップ・Human 承認・自動ロールバック付き） |

<br>

---

## アーキテクチャ

LangGraph によるステートマシンで複数の AI エージェントを連携させます。

```
[ 人間: 要件テキスト ]
        │
        ▼
┌─────────────────────────────────┐
│   設計エージェント（architect）   │ ←── RAG 知識ベース（rag/）
│   LLM でトポロジーYAML + コンフィグ生成    │
└──────────┬──────────────────────┘
           │
           ▼
┌─────────────────────────────────┐
│   検証エージェント（validator）  │
│   CML デプロイ → pyATS テスト実行 │
└──────────┬──────────────────────┘
           │
    全 PASS？
    ├── No  → 失敗原因を AI が推論 → 設計エージェントへ差し戻し（最大 5 回）
    │
    └── Yes → レポート生成 + 設計ドキュメント保存
                   │
                   └── --fault-sim 時 → 障害シミュレーション
                   └── --apply-to-live 時 → 実機適用フロー
```

<br>

---

## 技術スタック

| 役割 | 採用技術 |
|---|---|
| エージェントオーケストレーション | LangGraph |
| LLM | OpenAI / Anthropic / Ollama（環境変数で切替） |
| 構造化出力 | Pydantic v2 + Function Calling |
| CML 操作 | `virl2_client` 2.10 |
| ネットワーク検証 | pyATS + Genie |
| 実機接続 | pyATS/Unicon（SSH） |
| 知識ベース RAG | ChromaDB + pysqlite3-binary |
| 依存管理 | `uv` |

<br>

---

## セットアップ

### 前提条件

- Python 3.12 以上
- Cisco CML 2.10（設計・検証モード使用時）
- OpenAI / Anthropic API キー（または Ollama サーバー）

### 1. インストール

```bash
# コア依存（LangGraph / LLM / CML）
uv sync

# ネットワーク検証ライブラリ（pyATS/Genie）も含める場合
uv sync --extra network

# RAG 知識ベースも含める場合
uv sync --extra rag

# すべてインストール
uv sync --extra all
```

> **WSL 環境の場合**: `uv sync` が失敗する場合は以下の代替手順を使用してください:
> ```bash
> python3 -m venv .venv
> .venv/bin/pip install -r requirements.txt
> .venv/bin/pip install -r requirements-network.txt  # pyATS/Genie
> .venv/bin/pip install -e . --no-deps
> ```

### 2. 環境変数の設定

```bash
cp .env.example .env
```

`.env` に以下を設定します（最低限 LLM と CML の設定が必要です）:

```dotenv
# LLM プロバイダー（openai / anthropic / ollama）
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-xxxxxxxxxxxx
OPENAI_MODEL=gpt-4o

# Cisco CML
CML_URL=https://your-cml-server
CML_USERNAME=admin
CML_PASSWORD=your_password
CML_VERIFY_SSL=false

# エージェント設定
MAX_RETRIES=5
MAX_TEST_WORKERS=8   # テスト並列実行ワーカー数
```

<details>
<summary>Anthropic を使う場合</summary>

```dotenv
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxx
ANTHROPIC_MODEL=claude-3-5-sonnet-20241022
```

Anthropic はプロンプトキャッシング（Q4）が自動適用され、2 回目以降の呼び出しコストが約 1/10 になります。

</details>

<details>
<summary>Ollama（ローカル LLM）を使う場合</summary>

```dotenv
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1:70b
```

構造化出力の精度にはモデルサイズが影響します。`llama3.1:70b` 以上を推奨します。

</details>

<br>

---

## クイックスタート

### プロンプトセットの準備

`prompts/<セット名>/` ディレクトリを作成し、要件ファイルを用意します:

```bash
mkdir -p prompts/mynet
```

```markdown
# prompts/mynet/requirement.md

R1 と R2 を OSPF エリア 0 で接続してください。
各ルータに Loopback0 インターフェースを設定し、OSPF に含めてください。
```

必要に応じて設計エージェントへのヒントファイルも追加できます:

```markdown
# prompts/mynet/architect.md

- IPアドレス
- Router-ID
```

### 実行

```bash
# 設計・検証ループを実行（全 PASS で完了）
agentic-ni mynet

# CML なしで設計ファイルだけ生成する場合
agentic-ni mynet --dry-run
```

利用可能なセット一覧の確認:

```bash
agentic-ni --list
```

<br>

---

## 使い方詳細

### 基本オプション

```
agentic-ni <プロンプトセット名> [オプション]

設計・検証:
  （オプションなし）         要件から設計・デプロイ・検証ループを実行
  --dry-run                CML デプロイをスキップして設計ファイルのみ生成
  --use-topology           configs/<セット>/topology.yaml をトポロジーとして使用し
                           コンフィグのみ AI が生成する
  --fault-sim              検証成功後に障害シミュレーションを実行

トラブルシューティング・分析:
  --troubleshoot [名前|ID]  既存ラボを診断・修正（省略時はラボ名で自動検索）
  --issue '<説明>'          問題の説明（--troubleshoot と併用）
  --analyze [名前|ID]       設計品質を分析してレポートを出力（変更なし）
  --improve [名前|ID]       改善コンフィグを生成して configs/<セット>/ に保存
  --request '<改善要求>'    改善要求テキスト（--improve と併用）

実機適用:
  --apply-to-live          CML 検証後に実機へコンフィグ投入
  --inventory <path>       インベントリ YAML のパスを明示指定
                           （省略時は inventory/<セット>.yaml を自動使用）
  --live-verify            適用後に pyATS で実機テストを実行

RAG 知識ベース:
  --rag-index [<ディレクトリ>]  rag/ のファイルを知識ベースに索引化
  --rag-stats              知識ベースの件数・保存場所を表示
  --rag-clear-knowledge    知識ベースのインデックスを消去

その他:
  --list                   利用可能なプロンプトセット一覧を表示
  --verbose / -v           DEBUG ログを表示
  --quiet / -q             WARNING 以上のみ表示
  -h / --help              ヘルプを表示
```

### 実行例

```bash
# demo セットを実行
agentic-ni demo

# demo3: 手動で作成したトポロジー YAML を使用（コンフィグのみ生成）
agentic-ni demo3 --use-topology --dry-run

# demo2: 障害シミュレーションあり（リンク断→復旧→再テスト）
agentic-ni demo2 --fault-sim

# demo2: 既存ラボをトラブルシューティング
agentic-ni demo2 --troubleshoot

# ラボ ID を指定して設計分析
agentic-ni demo --analyze abc-1234-xxxx

# 改善要求を指定してコンフィグ改善
agentic-ni demo --improve --request 'OSPF に BFD を追加したい'

# 実機に適用（インベントリ自動解決: inventory/demo.yaml）
agentic-ni demo --apply-to-live

# 実機適用後に pyATS で実機テストも検証
agentic-ni demo --apply-to-live --live-verify
```

<br>

---

## 設計ドキュメントの自動生成

検証成功またはドライラン実行時に、`configs/<セット名>/` へ以下のファイルが自動保存されます:

| ファイル | 内容 |
|---|---|
| `topology.yaml` | CML トポロジー定義 |
| `<デバイス名>.cfg` | 機器ごとのコンフィグ |
| `ip_ledger.md` | IP アドレス台帳（Markdown） |
| `ip_ledger.csv` | IP アドレス台帳（CSV） |
| `routing_design.md` | ルーティング設計書 |

<br>

---

## RAG 知識ベース

`rag/` ディレクトリのファイルを知識ベースとして利用できます。

```bash
# rag/ ディレクトリのファイルをすべて索引化
agentic-ni --rag-index

# 特定ディレクトリを指定
agentic-ni --rag-index my_docs/

# 登録状況の確認
agentic-ni --rag-stats
```

標準で以下のガイドが同梱されています:

| ファイル | 内容 |
|---|---|
| `rag/ospf_guide.md` | OSPF 設計・トラブルシューティングガイド |
| `rag/bgp_guide.md` | BGP（iBGP/eBGP）設定ガイド |
| `rag/vlan_l2_guide.md` | VLAN・レイヤ 2 設定ガイド |
| `rag/cml_design_guide.md` | CML トポロジー YAML 設計パターンガイド |
| `rag/ios_variants_guide.md` | IOSv / IOL / CSR1000v / NX-OS 別コマンド対照表 |

<br>

---

## 実機適用モード（--apply-to-live）

CML で検証済みのコンフィグを実機へ安全に投入します。

### インベントリファイルの準備

```yaml
# inventory/<セット>.yaml の例
R1:
  host: 192.168.1.1
  username: admin
  password: cisco
  platform: ios
  apply_mode: config_merge   # config_merge / config_replace / incremental

R2:
  host: 192.168.1.2
  username: admin
  password: cisco
  platform: ios
  apply_mode: config_merge
```

### 安全機構（多段レベル）

| フェーズ | 内容 |
|---|---|
| Level 1 | インベントリファイルの検証 |
| Level 2 | 全デバイスへの SSH 疎通確認 |
| Level 3 | `running-config` バックアップの取得 |
| Human 承認 | 確認画面（`yes` / `no` / `rollback-only`）|
| Level 6 | 投入失敗デバイスの自動ロールバック |

<br>

---

## プロジェクト構造

```
agentic-ni/
├── pyproject.toml
├── .env.example             # 環境変数テンプレート
│
├── rag/                     # 知識ベース RAG 用テキストファイル
├── configs/                 # 生成された設計ファイル（自動保存）
├── inventory/               # 実機インベントリ YAML
├── prompts/                 # プロンプト（共通 + セット別）
│   ├── architect_system.md        # 設計エージェント 共通プロンプト
│   ├── validator_system.md        # 検証エージェント 共通プロンプト
│   ├── fault_simulator_system.md  # 障害シミュレーション 共通プロンプト
│   ├── troubleshooter_system.md   # トラブルシューティング 共通プロンプト
│   ├── demo/                      # サンプルセット（R1-R2 OSPF+iBGP）
│   ├── demo2/                     # サンプルセット（3 ルータ・障害シミュレーション用）
│   └── demo3/                     # サンプルセット（R1-R2 eBGP・手動トポロジーYAML）
│
├── src/agentic_ni/
│   ├── state.py             # LangGraph 共有ステート定義
│   ├── graph.py             # グラフ組み立て・CLI エントリポイント
│   ├── llm.py               # LLM ファクトリー（プロバイダー切替）
│   ├── agents/
│   │   ├── prompts.py       # プロンプト読み込みユーティリティ
│   │   ├── architect.py     # 設計エージェント
│   │   ├── validator.py     # 検証エージェント
│   │   ├── fault_simulator.py  # 障害シミュレーションエージェント
│   │   └── troubleshooter.py   # トラブルシューティングエージェント
│   │
│   └── tools/
│       ├── cml_tools.py     # virl2_client ラッパー
│       ├── pyats_tools.py   # pyATS/Genie ラッパー
│       └── rag_tools.py     # ChromaDB ラッパー
└── tests/
```

詳細なソースコード解説は [README.src.md](README.src.md) を参照してください。

<br>

---

## テスト

すべてのユニットテストは外部依存（LLM API / CML / pyATS / 実機）をモックするため、CML 環境なしで実行できます。

```bash
# ユニットテストのみ実行
pytest tests/ --ignore=tests/test_e2e.py --ignore=tests/test_live_e2e.py -v

# すべてのテストを実行
pytest tests/ -v
```

<br>

---

## 関連ドキュメント

| ファイル | 内容 |
|---|---|
| [README.src.md](README.src.md) | ソースコードに関しての説明 |
| [README.impl.md](README.impl.md) | 実装フェーズごとの設計記録 |
| [README.output.md](README.output.md) | 実行結果サンプル |
