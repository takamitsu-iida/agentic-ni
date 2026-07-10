# AI駆動型 ネットワーク設計検証 自律エージェントシステム

agentic NIとは、AI駆動型の **ネットワーク設計検証自律エージェントシステム** です。

人間が要件を入力するだけで、設計・デプロイ・検証・実機投入までを AI が自律的に実行します。

Cisco CML環境において、設計・デプロイ・検証・デバッグのサイクルをAIエージェントが自律的に実行します。

<br>

---

<br>

## アーキテクチャ概要

```
[ 人間 (要件入力) ]                    [ 既存ラボ + 問題説明 ]
       │                                        │
       ▼                                        ▼ --troubleshoot
 ┌──────────────────────────────┐   ┌─────────────────────────┐
 │ LangGraph (設計・検証ループ)   │   │ LangGraph (診断・修正)    │
 │                              │   │                         │
 │ 知識ベース RAG ←── rag/ 索引  │   │ 知識ベース RAG ←── rag/  │
 │                              │   │                         │
 │  設計エージェント ◀─ エラー ─┐ │   │  収集 → 診断 → 修正      │
 │       │                    │ │   │      ↕（リトライ）        │
 │       ▼                    │ │   └──────────┬──────────────┘
 │  検証エージェント ─ FAIL ───┘ │              │
 │       │                      │         修正レポート
 │       │ 全PASS                │
 │       ▼                      │
 │  障害シミュレーション           │
 │  ← --fault-sim              │
 └──────────┬───────────────────┘
            ▼
      最終レポート
```

<br>

---

<br>

## 技術スタック

| 層                   | 採用技術                                      |
| -------------------- | --------------------------------------------- |
| オーケストレーション | LangGraph                                     |
| LLM                  | OpenAI / Anthropic / Ollama（環境変数で切替） |
| CML操作              | `virl2_client` 2.10（CML 2.10 対応）          |
| ネットワーク検証     | `pyATS` + `Genie`                             |
| 知識ベース RAG       | `ChromaDB` + `pysqlite3-binary`               |
| 設定管理・型安全     | Pydantic v2                                   |
| 依存関係管理         | `uv`                                          |

<br>

---

<br>

## プロジェクト構造

```
agentic-ni/
├── README.md
├── instruction.md
├── pyproject.toml
├── .env.example             # 接続情報テンプレート（実体は .env で管理）
│
├── rag/                     # 知識ベース RAG 用テキストファイル置き場
│   ├── ospf_guide.md            # OSPF 設計・設定・トラブルシューティングガイド
│   ├── bgp_guide.md             # BGP（iBGP/eBGP）設定ガイド
│   ├── vlan_l2_guide.md         # VLAN・レイヤ 2 設定ガイド（iosvl2 向け）
│   ├── cml_design_guide.md      # CML トポロジー YAML 設計パターンガイド
│   └── ios_variants_guide.md    # IOSv / IOL / CSR1000v / NX-OS 別コマンド対照表
│
├── src/
│   └── agentic_ni/
│       ├── __init__.py
│       ├── state.py         # LangGraph 共有 State 定義
│       ├── graph.py         # グラフ組み立て・エントリポイント
│       ├── llm.py           # LLM ファクトリー（プロバイダー切替）
│       │
│       ├── agents/
│       │   ├── __init__.py
│       │   ├── architect.py        # 設計エージェント（トポロジー・コンフィグ生成）
│       │   ├── validator.py        # 検証エージェント（デプロイ・テスト・推論）
│       │   ├── fault_simulator.py  # 障害シミュレーションエージェント（リンク断・復旧・再テスト）
│       │   └── troubleshooter.py   # トラブルシューティングエージェント（診断・インクリメンタル修正）
│       │
│       ├── tools/
│       │   ├── __init__.py
│       │   ├── cml_tools.py    # virl2_client ラッパー
│       │   ├── pyats_tools.py  # pyATS/Genie ラッパー
│       │   └── rag_tools.py    # 知識ベース RAG（ChromaDB）ラッパー
│       │
│       └── prompts/
│           ├── architect_system.md        # 設計エージェント 共通プロンプト
│           ├── validator_system.md        # 検証エージェント 共通プロンプト
│           ├── fault_simulator_system.md  # 障害シミュレーション 共通プロンプト
│           ├── troubleshooter_system.md   # トラブルシューティング 共通プロンプト
│           ├── demo/                       # R1-R2 OSPF+iBGP
│           ├── demo2/                      # R1-R2-R3 フルメッシュ（障害シミュレーション用）
│           ├── demo3/                      # R1-R2 eBGP（手動トポロジーYAML使用・コンフィグのみ生成）
│           └── <セット名>/                # 任意で追加可能（例: ospf_l3vpn/）
│
└── tests/
    ├── __init__.py
    ├── test_cml_tools.py
    ├── test_pyats_tools.py
    ├── test_architect.py
    ├── test_validator.py
    ├── test_fault_simulator.py
    ├── test_troubleshooter.py
    ├── test_rag_knowledge.py
    ├── test_graph.py
    └── test_e2e.py
```

<br>

---

<br>

## 実装

<br>

### Phase 1 — プロジェクト基盤セットアップ

**目標**: 依存関係・環境変数・プロジェクト骨格を整備します。

**タスク**:
- [x] `pyproject.toml` を作成し依存パッケージを定義
- [x] `.env.example` を作成（CML URL/認証情報、LLM APIキーのテンプレート）
- [x] `src/agentic_ni/` ディレクトリ構造と空の `__init__.py` を作成
- [x] 仮想環境を構築
- [x] `src/agentic_ni/llm.py` の LLMファクトリーを実装
- [x] `tests/test_llm.py` のユニットテストがすべてPASS

**主要な依存パッケージ**:

```toml
[dependencies]
langgraph = "*"
langchain-openai = "*"
langchain-anthropic = "*"
langchain-ollama = "*"
virl2-client = ">=2.10,<2.11"  # CML 2.10 対応
pyats = "*"
genie = "*"
pydantic = ">=2.0"
python-dotenv = "*"

[dev-dependencies]
pytest = "*"
pytest-asyncio = "*"
```

**`.env.example` の内容**:

```
# --- LLMプロバイダー選択 ---
# "openai" / "anthropic" / "ollama" のいずれかを指定
LLM_PROVIDER=openai

# OpenAI
OPENAI_API_KEY=your_key_here
OPENAI_MODEL=gpt-4o

# Anthropic
ANTHROPIC_API_KEY=your_key_here
ANTHROPIC_MODEL=claude-3-5-sonnet-20241022

# Ollama（ローカルLLM）
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1:70b

# Cisco CML
CML_URL=https://your-cml-server
CML_USERNAME=admin
CML_PASSWORD=your_password
CML_VERIFY_SSL=false

# エージェント設定
MAX_RETRIES=5
```

**LLMファクトリー関数** (`src/agentic_ni/llm.py` として実装):

```python
import os
from langchain_core.language_models import BaseChatModel

def get_llm() -> BaseChatModel:
    provider = os.getenv("LLM_PROVIDER", "openai").lower()
    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-4o"))
    elif provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"))
    elif provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            model=os.getenv("OLLAMA_MODEL", "llama3.1:70b"),
        )
    else:
        raise ValueError(f"未対応のLLMプロバイダー: {provider}")
```

各エージェントは `get_llm()` を呼び出すだけでよく、プロバイダー切替時にエージェント側のコードは変更不要です。

> **Ollamaを使う場合の注意**: 構造化出力（`with_structured_output()`）の精度はモデルに依存します。`llama3.1:70b` 以上を推奨。ローカルリソースが不足する場合は `mistral-nemo` や `qwen2.5:32b` も選択肢です。

<br>

> **WSL環境での注意**: `uv sync` がWSLのファイルシステム制限（rename禁止）でエラーになる場合は、pipで代替インストールします。
> ```bash
> python3 -m venv .venv
> .venv/bin/pip install -r requirements.txt
> .venv/bin/pip install -e . --no-deps
> ```
> pyATS/Genie は大容量のため Phase 4 で別途インストール: `pip install -r requirements-network.txt`

**完了基準**: `.venv/bin/pytest tests/test_llm.py -v` で 4 tests PASSED。また `LLM_PROVIDER` を切り替えて `get_llm()` が各プロバイダーのインスタンスを返すことを確認します。 ✅ 完了

<br>

---

<br>

### Phase 2 — State定義 & グラフ骨格

**目標**: エージェント間の共有Stateとグラフの条件分岐ロジックを定義します（実処理はスタブで構いません）。

**タスク**:
- [x] `src/agentic_ni/state.py` を実装
- [x] `src/agentic_ni/graph.py` にグラフ骨格（スタブノード付き）を実装
- [x] グラフが正常に組み立てられることを確認

**`state.py` の設計**:
```python
from typing import TypedDict, Annotated
from langgraph.graph.message import add_messages

class AgentState(TypedDict):
    requirement: str           # 人間の要件（自然言語）
    topology_yaml: str         # CML用トポロジーYAML
    device_configs: dict       # { "router1": "config text", ... }
    test_results: list         # [{"test": "ospf_neighbor", "result": "PASS"}, ...]
    error_log: str             # 失敗時の詳細ログ（推論結果を含む）
    retry_count: int           # ループ回数
    final_report: str          # 完了時レポート
    lab_id: str                # CML上のラボID（デプロイ後に格納）
```

**`graph.py` の条件分岐ロジック**:
```python
def should_continue(state: AgentState) -> str:
    if all(r["result"] == "PASS" for r in state["test_results"]):
        return "complete"
    elif state["retry_count"] >= MAX_RETRIES:
        return "escalate"  # 人間へエスカレーション
    else:
        return "redesign"  # 設計エージェントへ差し戻し
```

**完了基準**: グラフを `graph.get_graph().draw_mermaid()` で可視化できます。 ✅ 完了

<br>

---

<br>

### Phase 3 — CMLツール実装

**目標**: virl2_client を使った CML 操作ツール群を実装・単体テストします。

**タスク**:
- [x] `src/agentic_ni/tools/cml_tools.py` を実装
- [x] `tests/test_cml_tools.py` で各ツールを検証

**実装する関数**:

| 関数                                                            | 説明                                    |
| --------------------------------------------------------------- | --------------------------------------- |
| `create_lab(topology_yaml: str) -> str`                         | YAMLからラボ作成・起動。`lab_id` を返す |
| `delete_lab(lab_id: str) -> None`                               | ラボ削除（クリーンアップ）              |
| `push_config(lab_id: str, node_name: str, config: str) -> None` | 機器にコンフィグを投入                  |
| `set_link_state(lab_id: str, link_id: str, up: bool) -> None`   | リンクUP/DOWN（障害シミュレーション）   |
| `wait_for_nodes_ready(lab_id: str, timeout: int) -> bool`       | 全ノード起動待ち                        |

**注意事項**:
- 認証情報は `.env` から読み込み、コードにハードコードしません。
- `CML_VERIFY_SSL=false` の場合は urllib3 の警告を抑制します。
- ラボ作成失敗時は例外を発生させ、呼び出し元でハンドリングします。

**完了基準**: 実CML環境（またはモック）でラボの作成・削除が往復できます。 ✅ 完了（14件モックテストPASS）

<br>

---

<br>

### Phase 4 — pyATSツール実装

**目標**: pyATS/Genie を使ったネットワーク検証ツール群を実装・単体テストします。

**タスク**:
- [x] `src/agentic_ni/tools/pyats_tools.py` を実装
- [x] testbed YAMLを動的生成するヘルパー関数を実装
- [x] `tests/test_pyats_tools.py` で各ツールを検証

**実装する関数**:

| 関数                                                                     | 説明                              |
| ------------------------------------------------------------------------ | --------------------------------- |
| `build_testbed(lab_id: str, device_configs: dict) -> str`                | CML情報からtestbed YAMLを動的生成 |
| `run_show_command(testbed_yaml: str, device: str, command: str) -> dict` | showコマンドをGenieでJSONパース   |
| `check_ospf_neighbors(testbed_yaml: str, device: str) -> dict`           | OSPFネイバー状態を確認            |
| `check_bgp_summary(testbed_yaml: str, device: str) -> dict`              | BGPピア状態を確認                 |
| `check_ping(testbed_yaml: str, device: str, target: str) -> bool`        | 疎通確認                          |
| `check_vlan_interfaces(testbed_yaml: str, device: str) -> dict`          | VLAN/インターフェース状態確認     |

**完了基準**: 実機（またはCML上の仮想機器）に対してOSPFネイバー状態を取得できます。 ✅ 完了（19件モックテストPASS）

> **pyATS未インストールの場合の注意**: 全関数は遅延importによりモジュール自体はインポート可能です。実機接続時には `pip install pyats genie` が必要です。

<br>

---

<br>

### Phase 5 — 設計エージェント実装

**目標**: LLMを使って要件からCMLトポロジーYAMLと機器コンフィグを生成するエージェントを実装します。

**タスク**:
- [x] `src/agentic_ni/prompts/architect_system.md` にシステムプロンプトを作成
- [x] 出力スキーマを Pydantic で定義（構造化出力）
- [x] `src/agentic_ni/agents/architect.py` を実装

**出力スキーマ（Pydantic）**:

```python
class TopologyNode(BaseModel):
    name: str
    node_type: str  # "iosv", "nxosv" など
    interfaces: list[str]

class TopologyLink(BaseModel):
    src_node: str
    src_interface: str
    dst_node: str
    dst_interface: str

class DesignOutput(BaseModel):
    topology_yaml: str         # CMLに読み込ませるYAML文字列
    device_configs: dict[str, str]  # { デバイス名: コンフィグテキスト }
    design_rationale: str      # 設計意図の説明（ログ用）
```

**エージェントの動作分岐**:
- `error_log` が空 → 要件からゼロ設計
- `error_log` に内容あり → 原因を分析し**差分修正のみ**出力（コスト最適化）

**完了基準**: サンプル要件（「R1とR2をOSPFで接続」）に対して有効なコンフィグが生成されます。 ✅ 完了（12件モックテストPASS）

<br>

---

<br>

### Phase 6 — 検証エージェント実装

**目標**: デプロイ・テスト・失敗推論を行う検証エージェントを実装します。

**タスク**:
- [x] `src/agentic_ni/prompts/validator_system.md` にシステムプロンプトを作成
- [x] `src/agentic_ni/agents/validator.py` を実装

**処理フロー**:
1. Phase 3のツールを使いCMLへトポロジーをデプロイ
2. コンフィグを各機器へ投入
3. 要件文を解析し、**必要なテスト項目をLLMが自律判断**
4. Phase 4のツールを使いテストを実行
5. 結果をパース
   - 全PASS → Stateの `test_results` を更新
   - FAIL → ログを分析し「なぜ失敗したか」を推論して `error_log` に格納
6. `retry_count` をインクリメント

**完了基準**: OSPFエリア番号のミスマッチを検知し、原因推論を `error_log` に格納できます。 ✅ 完了（25件モックテストPASS）

<br>

---

<br>

### Phase 7 — グラフ統合 & E2Eテスト

**目標**: 全コンポーネントをLangGraphで統合し、エンドツーエンドで動作させます。

**タスク**:
- [x] `graph.py` のスタブを実コンポーネントで置き換え
- [x] `tests/test_e2e.py` でE2Eシナリオを検証
- [x] Human-in-the-Loop（最終承認ステップ）を実装
- [x] 最大リトライ超過時のエスカレーションレポートを実装
- [x] 最終成功レポートのフォーマットを整備

**E2Eテストシナリオ**:

| シナリオ                               | 期待結果                              |
| -------------------------------------- | ------------------------------------- |
| 正常系：OSPFシンプル接続               | 1〜2回のループで全PASS                |
| 異常系：意図的にエリア番号をミスマッチ | 失敗検知 → 修正 → 再テストでPASS    |
| 上限超過：修正不能な要件               | MAX_RETRIES後に人間へエスカレーション |

**完了基準**: 自然言語の要件入力から最終レポート出力まで、人手介入なく動作します。 ✅ 完了（16件E2Eテスト・累記99件PASS）

<br>

---

<br>

### Phase B — 障害シミュレーション

**目標**: Phase A（全テスト PASS）成功後に、CML リンク切断/復旧を自動実施し、冗長性・フェイルオーバー・復旧を検証します。

**タスク**:
- [x] `src/agentic_ni/agents/fault_simulator.py` を実装
- [x] `src/agentic_ni/prompts/fault_simulator_system.md` を作成
- [x] `src/agentic_ni/prompts/demo2/` プロンプトセットを作成（R1-R2-R3 フルメッシュ OSPF）
- [x] `cml_tools.set_link_state` を CML インターフェースレベル切断に変更（`interface.shutdown()` / `interface.bring_up()`）
- [x] `state.py` に `FaultScenarioResult` TypedDict と障害シミュレーション関連フィールドを追加
- [x] `graph.py` に `fault_simulate_node` / `fault_report_node` を追加し `--fault-sim` CLI オプションを実装
- [x] `tests/test_fault_simulator.py` でユニットテスト（29 件 PASS）

**実装したコンポーネント**:

| コンポーネント             | 内容                                                                                     |
| -------------------------- | ---------------------------------------------------------------------------------------- |
| `fault_simulator.py`       | LLM が障害シナリオを自律計画 → リンク切断 → テスト → 復旧 → テストのシーケンスを実行 |
| `FaultScenario` (Pydantic) | シナリオ名・リンクID・期待OSPF ネイバー数（`expected_ospf_neighbors`）を定義             |
| `set_link_state()`         | `interface.shutdown()` + `interface.bring_up()` で両端インターフェースを同時切断/復旧    |
| `fault_report_node`        | 各シナリオの障害中・復旧後テスト結果を Markdown レポートに追記                           |
| `demo2` プロンプトセット   | R1-R2-R3 フルメッシュ・OSPF Hello=3s/Dead=10s の短縮タイマー構成                         |

**障害中 OSPF テストの精度向上**:

`expected_ospf_neighbors` フィールドにより、障害中の期待ネイバー数を完全一致で検証します。
（例: R1-R2 リンク断時、R1 は `expected: 1`、R3 は対象外で `neighbors_up > 0`）

**実行例**:

```bash
agentic-ni demo2 --fault-sim
```

```
[障害シミュレーション]  開始
  シナリオ 1/3: R1–R2 リンク断による冗長経路検証
    CML リンク DOWN: R1 <-> R2 (15s 待機中...)
    テスト実行（障害中）:
      OSPF ネイバー数確認: R1 （期待値: 1） → ✅ PASS  1 neighbor(s) FULL (expected: 1)
      OSPF ネイバー数確認: R2 （期待値: 1） → ✅ PASS  1 neighbor(s) FULL (expected: 1)
      ping 2.2.2.2                          → ✅ PASS  ping 2.2.2.2 OK
    CML リンク UP（復旧）: R1 <-> R2 (15s 待機中...)
    テスト実行（復旧後）: 全 PASS
    シナリオ結果: ✅ PASS
  ...
  [障害シミュレーション 完了] 3/3 シナリオ PASS
```

**完了基準**: `demo2 --fault-sim` で全 3 シナリオが復旧確認（PASS）できること。 ✅ 完了（29 件ユニットテスト PASS）

<br>

---

<br>

### Phase H — トラブルシューティングモード

**目標**: 稼働中の既存 CML ラボに接続し、「診断 → インクリメンタル修正 → 検証」のサイクルで問題を自律解決します。

**タスク**:
- [x] `src/agentic_ni/agents/troubleshooter.py` を実装
  - `run_collect`: 全機器の `running-config` と `show` コマンド出力を収集
  - `run_diagnose`: LLM が根本原因を `DiagnosisResult`（Pydantic）として診断
  - `run_fix`: LLM が `FixPlan`（Pydantic）を生成し `configure terminal` で差分投入
- [x] `src/agentic_ni/prompts/troubleshooter_system.md` を作成
- [x] `state.py` に `TroubleshootFixRecord` TypedDict と関連フィールドを追加
- [x] `graph.py` に `compile_graph_troubleshoot()` / `initial_state_troubleshoot()` を実装
- [x] `--troubleshoot` / `--issue` CLI オプションを実装
- [x] `tests/test_troubleshooter.py` でユニットテスト PASS

**実装したコンポーネント**:

| コンポーネント                      | 内容                                                       |
| ----------------------------------- | ---------------------------------------------------------- |
| `troubleshooter.py`                 | `run_collect` / `run_diagnose` / `run_fix` の 3 関数       |
| `DiagnosisResult` (Pydantic)        | `root_cause` / `affected_devices` / `severity` / `summary` |
| `FixPlan` / `FixCommand` (Pydantic) | デバイス別修正コマンドと rollback コマンド                 |
| `troubleshooter_system.md`          | トラブルシューター専用システムプロンプト                   |
| `compile_graph_troubleshoot()`      | `collect → diagnose → fix → verify` ループグラフ        |
| `initial_state_troubleshoot()`      | トラブルシューティングモード用初期ステートファクトリー     |
| `TroubleshootFixRecord` (TypedDict) | 修正履歴レコード（`state.py`）                             |

**Phase A/B との主な違い**:

| 項目               | Phase A（設計・検証）               | Phase H（トラブルシューティング）   |
| ------------------ | ----------------------------------- | ----------------------------------- |
| 起点               | 自然言語の要件                      | 既存ラボ ID + 問題説明              |
| 修正方式           | wipe + 再デプロイ                   | `configure terminal` による差分適用 |
| 主担当エージェント | 設計エージェント + 検証エージェント | トラブルシューターエージェント      |
| ラボ作成           | あり（新規）                        | なし（既存ラボに接続）              |

**実行例**:

```bash
# 既存ラボの問題を診断・自動修正
agentic-ni demo --troubleshoot <lab_id>

# 問題の説明を添えて実行
agentic-ni demo --troubleshoot <lab_id> --issue 'OSPF ネイバーが確立しない'
```

```
[トラブルシューティング] 機器状態を収集中...
[トラブルシューティング 診断 1/3] 根本原因を分析中...
  根本原因: R1 の router ospf 1 に network 文が設定されていない。
  影響デバイス: ['R1']
  重大度: config_error
[トラブルシューティング] 修正コマンドを生成・投入中...
  [R1] router ospf 1 / network 0.0.0.0 255.255.255.255 area 0 → ✅ 成功
[トラブルシューティング] 検証テストを実行中...
  (1/2) OSPF ネイバー確認 → ✅ PASS
  (2/2) ping 2.2.2.2      → ✅ PASS
>>> トラブルシューティング完了レポートを生成しています...
```

**完了基準**: `--troubleshoot` で既存ラボに接続し、`collect → diagnose → fix → verify` のサイクルが最大 `TROUBLESHOOT_MAX_RETRIES`（デフォルト 3 回）まで繰り返せること。 ✅ 完了（ユニットテスト PASS）

<br>

---

<br>

### Phase E — 設計分析・改善モード

**目標**: 既存の CML ラボを読み込み、AI が設計品質を評価して問題点・改善提案をレポートします。
また、改善要求に基づいて新しいコンフィグを自動生成しファイルに保存します。

**タスク**:
- [x] `src/agentic_ni/agents/analyzer.py` を実装
  - `run_analyze`: 機器状態を LLM で分析し `AnalysisResult`（Pydantic）に診断
  - `run_improve`: 改善要求から `ImprovementOutput`（Pydantic）として改善コンフィグを生成
- [x] `src/agentic_ni/prompts/analyzer_system.md` を作成
- [x] `cml_tools.py` に `export_lab_configs` / `export_lab_topology` を追加
- [x] `state.py` に `analyze_request` / `analysis_result` フィールドを追加
- [x] `graph.py` に `compile_graph_analyze()` / `compile_graph_improve()` を実装
- [x] `--analyze` / `--improve` / `--request` CLI オプションを実装
- [x] `tests/test_analyzer.py` でユニットテスト 30 件 PASS

**実装したコンポーネント**:

| コンポーネント                 | 内容                                                                |
| ------------------------------ | ------------------------------------------------------------------- |
| `analyzer.py`                  | `run_analyze` / `run_improve` の 2 関数                             |
| `AnalysisIssue` (Pydantic)     | `severity` / `device` / `description` / `recommendation`            |
| `AnalysisResult` (Pydantic)    | `overall_rating` / `summary` / `issues` / `improvement_suggestions` |
| `ImprovementOutput` (Pydantic) | 改善後 `device_configs` / `changes_summary` / `rationale`           |
| `analyzer_system.md`           | 分析・改善エージェント専用システムプロンプト                        |
| `compile_graph_analyze()`      | `collect → analyze → report` グラフ                               |
| `compile_graph_improve()`      | `collect → improve → save` グラフ                                 |
| `export_lab_configs(lab_id)`   | CML から Day-0 コンフィグを取得（`cml_tools.py`）                   |
| `export_lab_topology(lab_id)`  | CML からトポロジー YAML をエクスポート（`cml_tools.py`）            |
| `tests/test_analyzer.py`       | ユニットテスト 30 件                                                |

**Phase H (troubleshooter) との主な違い**:

| 項目       | Phase E --analyze    | Phase E --improve               | Phase H           |
| ---------- | -------------------- | ------------------------------- | ----------------- |
| 目的       | 設計品質評価         | 設計改善コンフィグ生成          | 障害の自動修正    |
| 変更の適用 | なし（読み取り専用） | ファイル保存のみ（deploy なし） | CML に直接投入    |
| ループ     | なし（1 パス）       | なし（1 パス）                  | 最大 3 回リトライ |

**実行例**:

```bash
# 設計分析（変更なし）
agentic-ni demo --analyze
agentic-ni demo --analyze <lab_id>

# 設計改善（configs/demo/ にファイル保存）
agentic-ni demo --improve --request "OSPFにBFDを追加したい"
agentic-ni demo --improve <lab_id> --request "Loopbackインターフェースを追加したい"
```

```
[設計分析] ラボ lab-abc-001 を分析中...
  分析結果: [needs_improvement] 基本的な OSPF 接続は機能しているが...
>>> 設計分析レポートを生成しています...

## 設計評価: ⚠️ 要改善

### 検出された問題 (2 件)

| 重大度 | デバイス | 問題 | 推奨対応 |
|---|---|---|---|
| WARNING | R1 | router-id が未設定 | router ospf 1 で router-id 1.1.1.1 を設定 |
| INFO | all | no ip domain-lookup が未設定 | グローバル設定で no ip domain-lookup を追加 |
```

**完了基準**: `--analyze` で既存ラボの設計評価レポートが出力され、`--improve --request` で改善コンフィグが `configs/<set>/` に保存されること。 ✅ 完了（30 件ユニットテスト PASS）

```
Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5 → Phase 6 → Phase 7 → Phase B → Phase H → Phase E → Phase I
  基盤      骨格      CML操作    pyATS     設計AI    検証AI     統合       障害シミュ   トラブルシュ  分析・改善   実機適用
  (llm.py含む)
```

各Phaseは独立して動作確認できるよう設計されています。
Phase 3・4はCML環境がなくてもモックで進められます。

**Ollamaでオフライン開発する場合**: Phase 1完了後に `ollama pull llama3.1:70b` を実行し、`.env` に `LLM_PROVIDER=ollama` を設定すれば、外部APIキーなしで全Phaseを進められます。

<br>

---

<br>

## セキュリティ注意事項

- `.env` は `.gitignore` に追加し、リポジトリにコミットしないでください。
- CML接続はSSL証明書検証を本番環境では必ず有効化してください。
- LLMへ送信するデータに本番ネットワークの機密情報を含めないでください。
- pyATSのtestbed YAMLにパスワードを平文で書く場合はファイル権限に注意してください。

<br>

---

<br>

## 動作確認手順

別環境で動かす際の確認ステップです。
外部依存（LLM API → CML → pyATS）の順に段階的に検証します。

```
Phase 1: 環境セットアップ確認
Phase 2: LLMファクトリー単体テスト（APIキー不要・モック）
Phase 3: .env 設定 → LLM 実疎通確認
Phase 4: 設計エージェント・グラフ単体テスト（LLMモック）
Phase 5: CML 接続確認
Phase 6: pyATS/Genie セットアップ確認
Phase 7: E2E テスト（全機能統合）
RAG  : 知識ベース RAG セットアップ確認（任意）
```

<br>

---

<br>

### Phase 1 — 環境セットアップ確認

**目的**: 仮想環境・依存パッケージ・プロジェクト構造が正しいことを確認します。

**前提条件**: Python 3.12 以上、`uv` がインストール済みであること。

uvがインストールされていな場合は公式に記載の方法でインストールします。管理者権限は不要です。

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

このリポジトリをクローンします。

```bash
git clone https://github.com/takamitsu-iida/agentic-ni.git
```

リポジトリをクローン後、プロジェクトルートへ移動します。

```bash
cd agentic-ni
```

依存パッケージをインストールします。このとき `agentic-ni` コマンドもPython仮想環境にインストールされます。

```bash
uv sync
```

仮想環境を有効化します。direnvを入れているなら`direnv allow`して有効にします。

```bash
source .venv/bin/activate
```

Pythonモジュールの import が通るか確認します。

```bash
python -c "from agentic_ni.state import AgentState; print('state: OK')"
python -c "from agentic_ni.llm import get_llm; print('llm: OK')"
python -c "from agentic_ni.graph import build_graph; print('graph: OK')"
```

> **WSL 環境で `uv sync` が失敗する場合**（rename 禁止エラー）:
> ```bash
> python3 -m venv .venv
> .venv/bin/pip install -r requirements.txt
> .venv/bin/pip install -e . --no-deps
> ```

**完了条件**: すべての `python -c` コマンドが `OK` を出力します。

<br>

---

<br>

### Phase 2 — LLMファクトリー単体テスト

**目的**: LLMモジュールがモック込みで正しく動くことを確認します。APIキーは不要です。

```bash
pytest tests/test_llm.py -v
```

期待される出力例:
```
tests/test_llm.py::test_get_llm_openai PASSED
tests/test_llm.py::test_get_llm_anthropic PASSED
tests/test_llm.py::test_get_llm_ollama PASSED
tests/test_llm.py::test_get_llm_unknown_provider_raises PASSED
```

**完了条件**: `test_llm.py` の全テストが PASS します。

<br>

---

<br>

### Phase 3 — `.env` 設定 → LLM 実疎通確認

**目的**: 実際の LLM API に繋がり、応答が返ることを確認します。

ここでは **OpenAI `gpt-4o-mini`** を使う手順を説明します。

#### 3-1. OpenAI アカウントの作成と API キーの取得

1. **アカウント作成**
   <https://platform.openai.com/signup> にアクセスし、メールアドレスまたは Google / Microsoft アカウントでサインアップする。

2. **支払い方法の登録**
   左メニューの **Billing → Payment methods** からクレジットカードを登録する。Billingが見つからないときは検索で探します。
   事前にクレジットをチャージしておく場合は **Add to credit balance** からチャージできる（最低 $5）。

   > **費用の目安**: `gpt-4o-mini` は $0.15 / 1M 入力トークン・$0.60 / 1M 出力トークン。
   > Phase 3 の疎通確認 1 回は 0.01 円以下。Phase 7 まで通しで動かしても数十円程度。

3. **（任意）使用量上限の設定**
   **Billing → Usage Limits** で月次の上限金額（Usage limit）を設定しておくと、意図しない高額請求を防げる。

4. **プロジェクトの作成**

  左上に表示されるのは現在のプロジェクト名で、初期に作られるプロジェクトは `Default project` という名称になっている。
  新しく `agentic-ai` プロジェクトを作成する。

5. **API キーの発行**
   左メニューの **API keys → Create new secret key** をクリックする。Nameは入れなくてもよいが `My Test Key` としておく。
   プロジェクト名が `agentic-ni` になっていることを確認して **Create secret key** を押す。
   表示された `sk-...` の文字列をコピーする。**この画面を閉じると二度と表示されない**ため、必ずコピーしておく。

#### 3-2. `.env` の設定

```bash
cp .env.example .env
```

`.env` を開き、以下のように設定します。

```dotenv
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-ここに取得したキーを貼り付け
OPENAI_MODEL=gpt-4o-mini
```

> **注意**: `.env` はリポジトリにコミットしない。`.gitignore` に含まれていることを確認する。
>
> 以下を実行して ".env" が出力されれば OK
>
> ```bash
> grep '\.env' .gitignore
> ```

#### 3-3. スモークテストの実行

```bash
python - <<'EOF'
from agentic_ni.llm import get_llm
llm = get_llm()
res = llm.invoke("Reply with just 'OK'.")
print("LLM response:", res.content)
EOF
```

期待される出力:
```
LLM response: OK
```

実行時に以下のメッセージを含む例外が出たらAPIキーが正しくない。

```bash
Error code: 401 - {'error': {'message': 'Incorrect API key provided: your_key*here. You can find your API key at https://platform.openai.com/account/api-keys.', 'type': 'invalid_request_error', 'param': None, 'code': 'invalid_api_key'}}
```

**完了条件**: LLM から応答テキストが返ります。

<br>

---

<br>

### Phase 4 — 設計エージェント・グラフ単体テスト

**目的**: LangGraph のフローおよび architect / validator エージェントのロジックを、LLM をモックした状態で確認します。

```bash
pytest tests/test_architect.py tests/test_validator.py tests/test_graph.py -v
```

**完了条件**: 3 ファイルの全テストが PASS します。

<br>

---

<br>

### Phase 5 — CML 接続確認

**目的**: Cisco CML に繋がり、ラボの作成・削除が正常に動作することを確認します。

**前提条件**: Cisco CML バージョン **2.10** が稼働していること。`virl2-client` は `>=2.10,<2.11` を使用します。

**手順**:

1. `.env` に CML の接続情報を設定する。

   ```dotenv
   CML_URL=https://your-cml-server
   CML_USERNAME=admin
   CML_PASSWORD=your_password
   CML_VERIFY_SSL=false
   ```

2. CML 接続テストを実行します。

   ```bash
   pytest tests/test_cml_tools.py -v
   ```

> **実CML環境なしで確認する場合**: `test_cml_tools.py` はモックを使うため、CML への実接続なしでも PASS します。
> 実機接続が必要なテストは別途マーキングされている場合があるため、`-m "not integration"` オプションを付けて実行します。

**完了条件**: `test_cml_tools.py` の全テストが PASS します。

<br>

---

<br>

### Phase 6 — pyATS/Genie セットアップ確認

**目的**: ネットワーク検証ツール（pyATS/Genie）が正しくインストールされ、使用できることを確認します。

> **注意**: pyATS/Genie は大容量パッケージ（数百 MB）です。インストールに時間がかかります。

**手順**:

1. network extras をインストールする。

   ```bash
   uv sync --extra network
   # または pip を使う場合
   pip install -r requirements-network.txt
   ```

   > **rag（chromadb）も一緒に入れる場合**: `uv sync --extra all` で network + rag の両方を一度にインストールできます。
   > `uv sync --extra network` と `uv sync --extra rag` を個別に実行すると後に実行した方のパッケージしか残らない場合があるため、両方使う場合は `--extra all` または両方同時指定を推奨します。

2. インストールの確認。

   ```bash
   python -c "import importlib.metadata; print('pyats:', importlib.metadata.version('pyats'))"
   python -c "import importlib.metadata; print('genie:', importlib.metadata.version('genie'))"
   ```

3. テストを実行します。

   ```bash
   pytest tests/test_pyats_tools.py -v
   ```

**完了条件**: `test_pyats_tools.py` の全テストが PASS します。

<br>

---

<br>

### Phase 7 — E2E テスト（全機能統合）

**目的**: 要件入力 → 設計 → CML デプロイ → 検証 → レポート出力まで通しで動作することを確認します。

**前提条件**: Phase 3〜6 が完了していること。

**手順**:

1. E2E テストを実行します（モック使用）。

   ```bash
   pytest tests/test_e2e.py -v -s
   ```

2. CLI から直接実行します（実 CML・実 LLM API が必要です）。

   注意：このテストは長い時間かかります。

   ```bash
   agentic-ni
   ```

   または Python から実行する。

   ```python
   from agentic_ni.graph import build_graph

   graph = build_graph()
   result = graph.invoke({
       "requirement": "R1 と R2 を OSPF エリア 0 で接続し、互いに ping が通ること",
       "topology_yaml": "",
       "device_configs": {},
       "lab_id": "",
       "test_results": [],
       "error_log": "",
       "retry_count": 0,
       "final_report": "",
   })
   print(result["final_report"])
   ```

**完了条件**: 最終レポート（成功またはエスカレーション）が出力されます。

<br>

---

<br>

### RAG — 知識ベース RAG セットアップ確認（任意）

**目的**: `rag/` ディレクトリのテキストファイルを ChromaDB に登録し、設計・トラブルシューティング時に LLM が自動で参照できることを確認します。

**前提条件**: Phase 3（LLM 実疏通）が完了していること。

**手順**:

1. chromadb と pysqlite3-binary をインストールする。

   ```bash
   uv sync --extra rag
   # または pip を使う場合
   pip install chromadb pysqlite3-binary
   ```

   > **network（pyATS）も一緒に入れる場合**: `uv sync --extra all` を使うと両方を一度にインストールできます。
   > ChromaDB 起動時に `RuntimeError: requires sqlite3 >= 3.35.0` が発生します。
   > `pysqlite3-binary` を一緒にインストールすることで解決されます。

2. 知識ベースのユニットテストを実行する。

   ```bash
   pytest tests/test_rag_knowledge.py -v
   ```

3. `rag/` ディレクトリのファイルを索引化する。

   ```bash
   agentic-ni --rag-index
   ```

   期待される出力例:
   ```
   知識ベースを索引化中: rag
       ospf_guide.md: 6 チャンク
   完了: 合計 6 チャンクを知識ベースに登録しました。
   ```

4. 統計を確認する。

   ```bash
   agentic-ni --rag-stats
   ```

   期待される出力例:
   ```
   RAGストア統計:
     実行ログ RAG (成功事例): 0 件
     知識ベース RAG (テキストファイル): 24 チャンク
     保存場所: /home/ユーザー名/.agentic_ni/rag_store
   ```

5. 知識ベースが設計プロンプトに反映されることを確認する。

   ```bash
   # --dry-run は実障害なしで設計プロンプトの内容を確認することができる
   agentic-ni demo --dry-run
   ```

   知識ベースがインデックス済みの場合、設計プロンプト消費トークンが増加します。
   `configs/demo/topology.yaml` と機器コンフィグが出力されなら正常です。

**完了条件**: `test_rag_knowledge.py` の全テストが PASS し、`--rag-index` が 6 チャンクを登録できること。

| 症状                                       | 確認箇所                                                                    |
| ------------------------------------------ | --------------------------------------------------------------------------- |
| `ModuleNotFoundError: agentic_ni`          | `uv sync` または `pip install -e .` が未実行                                |
| `ValueError: 未対応のLLMプロバイダー`      | `.env` の `LLM_PROVIDER` の値を確認                                         |
| LLM API 認証エラー                         | `.env` の API キーが正しいか確認                                            |
| CML 接続タイムアウト                       | `CML_URL` のホスト名・ポートを確認、VPN 接続を確認                          |
| pyATS `ImportError`                        | `uv sync --extra network` または `pip install pyats genie` を実行           |
| `chromadb` が見つからない                  | `uv sync --extra rag` または `pip install chromadb pysqlite3-binary` を実行 |
| `RuntimeError: requires sqlite3 >= 3.35.0` | `pip install pysqlite3-binary` を実行（Ubuntu 20.04 等の古い環境向け）      |
| RAG検索が0件                               | `agentic-ni --rag-stats` で保存件数を確認。初回実行時は空のため正常         |
| 知識ベースが空（0 チャンク）               | `agentic-ni --rag-index` を実行して `rag/` ディレクトリを索引化する         |
| `pytest` が見つからない                    | `.venv/bin/pytest` を使うか `source .venv/bin/activate` を実行              |
---

## コマンドラインの使い方

### 基本構文

```bash
agentic-ni <プロンプトセット名> [オプション]
```

引数なしで実行するとヘルプを表示します。`-h` / `--help` でも同様です。

```bash
agentic-ni
# または
agentic-ni --help
```

**要件はプロンプトセット内の `requirement.md` に記載します。**
CLI 引数として要件テキストを渡すことはできません。

### オプション一覧

| オプション               | 説明                                                                         |
| ------------------------ | ---------------------------------------------------------------------------- |
| `--list`                 | 利用可能なプロンプトセット一覧を表示して終了する                             |
| `--dry-run`              | CML デプロイをスキップして設計・コンフィグ生成のみ行う（CML 不要）           |
| `--use-topology`         | `configs/<set>/topology.yaml` をトポロジーとして使用し、コンフィグのみ生成する |
| `--fault-sim`            | 全テスト PASS 後に障害シミュレーション（リンク断・復旧・再テスト）を実行する |
| `--troubleshoot [ID]`    | 既存ラボをトラブルシュート（ID 省略時はラボ名で自動検索）                    |
| `--issue '<説明>'`       | `--troubleshoot` と併用する問題の説明（任意）                                |
| `--analyze [ID]`         | 既存ラボの設計を分析してレポートを出力する（変更なし）                       |
| `--improve [ID]`         | 既存ラボのコンフィグを改善して `configs/<set>/` に保存する                   |
| `--request '<改善要求>'` | `--improve` と併用する改善要求テキスト（任意）                               |
| `--rag-index [<dir>]`    | `rag/` のテキストファイルを知識ベースに索引化する（要 `chromadb`）           |
| `--rag-clear-knowledge`  | 知識ベースのインデックスを全消去する                                         |
| `--rag-stats`            | 実行ログ RAG・知識ベースの保存件数と保存場所を表示して終了する               |
| `-h` / `--help`          | ヘルプを表示して終了する                                                     |

---

### モード別の使い方

#### 1. 通常モード — 要件から設計・デプロイ・検証まで自動実行

```bash
agentic-ni demo
agentic-ni ospf_l3vpn
```

`prompts/<プロンプトセット名>/requirement.md` の要件を読み込み、設計 → CML デプロイ → 検証 → 修正ループを実行します。全テスト PASS 後に最終レポートを出力し、設計ドキュメント（IP 台帳・ルーティング設計書）を `configs/<set>/` に保存します。

利用可能なプロンプトセット一覧を確認:
```bash
agentic-ni --list
```

---

#### 2. ドライランモード（Phase C/D）— CML 不要・設計ドキュメント生成のみ

```bash
agentic-ni demo --dry-run
```

CML へのデプロイと検証テストをスキップして、設計・コンフィグ生成のみ実行します。
以下のファイルが `configs/demo/` に保存されます:

```
configs/demo/
  topology.yaml       ← CML トポロジー定義
  R1.cfg              ← R1 コンフィグ
  R2.cfg              ← R2 コンフィグ
  ip_ledger.md        ← IP アドレス台帳（Markdown テーブル）
  ip_ledger.csv       ← IP アドレス台帳（CSV）
  routing_design.md   ← OSPF / BGP ルーティング設計書
```

CML 環境なしでコンフィグ生成や設計ドキュメントを確認したい場合に使います。

---

#### 3. 障害シミュレーションモード（Phase B）— リンク断・復旧・再テスト

```bash
agentic-ni demo2 --fault-sim
```

通常モードで全テスト PASS した後、CML 上でリンクを順番に切断・復旧させて冗長性を検証します。
`demo2` セット（R1-R2-R3 フルメッシュ OSPF）で実行すると、3 つのリンク断シナリオを自動実施します。

---

#### 4. トラブルシューティングモード（Phase H）— 既存ラボの診断・自動修正

```bash
# demo ラボをタイトルで自動検索してトラブルシュート
agentic-ni demo --troubleshoot

# lab_id を明示して実行
agentic-ni demo --troubleshoot abc-1234

# 問題の説明を添える
agentic-ni demo --troubleshoot abc-1234 --issue 'OSPF ネイバーが確立しない'
```

既存の稼働中 CML ラボに接続し、`collect → diagnose → fix → verify` のサイクルを最大 3 回繰り返して問題を自動修正します。

- `lab_id` を省略した場合、`agentic-ni-<プロンプトセット名>` というタイトルのラボを自動検索します
- 修正は `configure terminal` による差分投入のみ（wipe・再デプロイなし）

---

#### 5. 設計分析モード（Phase E: --analyze）— 既存ラボの品質評価

```bash
# demo ラボを自動検索して分析
agentic-ni demo --analyze

# lab_id を明示して分析
agentic-ni demo --analyze abc-1234
```

既存の稼働中 CML ラボから running-config を収集し、設計品質を評価してレポートを出力します。
変更は一切加えません（読み取り専用）。

出力例:
```
## 設計評価: ⚠️ 要改善

### 検出された問題 (2 件)
| 重大度 | デバイス | 問題 | 推奨対応 |
|---|---|---|---|
| WARNING | R1 | router-id が未設定 | router ospf 1 で router-id 1.1.1.1 を設定 |
| INFO    | all | no ip domain-lookup が未設定 | グローバルで no ip domain-lookup を追加 |
```

---

#### 6. 設計改善モード（Phase E: --improve）— 改善コンフィグをファイル生成

```bash
# demo ラボを自動検索して改善
agentic-ni demo --improve --request 'OSPF に BFD を追加したい'

# lab_id を明示して改善
agentic-ni demo --improve abc-1234 --request 'Loopback インターフェースを追加して router-id を安定させたい'
```

既存ラボから running-config を収集し、改善要求に基づいて改善後のコンフィグを生成します。
`configs/<set>/<device>.cfg` に保存されますが、CML への適用は行いません（deploy なし）。

---

#### 7. RAG 知識ベース操作

```bash
# rag/ 内のファイルを知識ベースに索引化（初回・ファイル追加後に実行）
agentic-ni --rag-index

# 索引化するディレクトリを指定
agentic-ni --rag-index ./my_docs

# 知識ベースのインデックスを全消去
agentic-ni --rag-clear-knowledge

# 保存件数と保存場所を確認
agentic-ni --rag-stats
```

索引化後は、通常モード・ドライランモードの設計プロンプトに関連チャンクが自動追加されます。フラグ不要です。

---

#### 8. トポロジー提供済みコンフィグ生成モード — 手動作成のトポロジーYAMLを使用

```bash
# 手動作成したトポロジーYAMLを使い、コンフィグのみ生成（ドライラン）
agentic-ni demo3 --use-topology --dry-run

# CML環境でフル実行（コンフィグ生成 → CMLデプロイ → 検証）
agentic-ni demo3 --use-topology
```

`configs/<プロンプトセット名>/topology.yaml` に手動で作成した CML トポロジー定義を配置しておくと、AIはトポロジーを生成せず **機器コンフィグのみ**を生成します。

**通常モードとの違い**:

| 項目             | 通常モード                           | `--use-topology` モード                       |
| ---------------- | ------------------------------------ | --------------------------------------------- |
| トポロジー       | AI が自動生成                        | `configs/<set>/topology.yaml` を使用          |
| コンフィグ       | AI が生成                            | AI が生成                                     |
| リトライ時       | トポロジー・コンフィグの両方を修正   | コンフィグのみ修正（トポロジーは変更しない）  |
| 用途             | ゼロからの自動設計                   | 既存/手動設計したトポロジーへのコンフィグ適用 |

**demo3 セットの内容**（R1-R2 eBGP、AS 65001 ↔ AS 65002）:

```
configs/demo3/
  topology.yaml     ← 手動作成済み（R1-R2 直結、GigabitEthernet0/0 × 1リンク）

prompts/demo3/
  requirement.md    ← eBGP 設定要件
  architect.md      ← トポロジー提供済み・コンフィグのみ生成の指示
  validator.md      ← BGP セッション確立・ping の検証仕様
```

**任意のプロンプトセットで使う場合**:

`configs/<セット名>/topology.yaml` を手動で作成して `--use-topology` を付けるだけで、同じコンフィグのみ生成モードが使えます。

---

### プロンプトセットの追加方法

ネットワーク要件の種類に合わせてプロンプトセットを追加できます。

1. `src/agentic_ni/prompts/<セット名>/` ディレクトリを作成します。
2. `requirement.md`（実行する要件テキスト）を作成します。これのみ必須です。
3. `validator.md`（必須テスト一覧と固有の失敗パターン）を作成します。任意です。
4. `architect.md`（設計エージェントへのセット固有ヒント）を作成します。任意です。

**プロンプトの構築方式**:
```
【設計エージェント】
prompts/architect_system.md      ← 常に読み込む（役割・YAML形式・出力ルール）
  +
prompts/<セット名>/architect.md  ← あれば末尾に追記（IPアドレス仕様・固有ヒント）

【検証エージェント】
prompts/validator_system.md      ← 常に読み込む（役割・テストタイプ・汎用ガイドライン）
  +
prompts/<セット名>/validator.md  ← あれば末尾に追記（必須テスト一覧・固有失敗パターン）
```

```bash
# 例: BGP 設計用セットを追加
mkdir -p src/agentic_ni/prompts/bgp_design
cp src/agentic_ni/prompts/demo/requirement.md src/agentic_ni/prompts/bgp_design/
# requirement.md を編集して BGP 要件を記載する

# 追加後に確認
agentic-ni --list
# 利用可能なプロンプトセット:
#   - bgp_design
#   - demo
```

---

### 新しいテストタイプの追加方法

検証エージェントが実行できるテストタイプ（`ospf_neighbors`、`ping` など）を追加するには、以下の **3 箇所を同時に** 更新する必要があります。

> **重要**: この 3 箇所は密結合しています。1 箇所だけ更新すると動作しません。

#### 更新が必要な箇所

| # | ファイル | 役割 |
|---|---|---|
| 1 | `src/agentic_ni/prompts/validator_system.md` | LLM に「このテストタイプが使える」と教える |
| 2 | `src/agentic_ni/agents/validator.py` の `TestItem.test_type` | LLM の出力値を Pydantic の `Literal` 型で制約する |
| 3 | `src/agentic_ni/agents/validator.py` の `_execute_test()` | テストタイプに対応する `pyats_tools` 関数を呼び出す |

#### 例: `ntp_status`（NTPサーバー同期確認）を追加する場合

**Step 1 — `validator_system.md` のテーブルに追記**

```markdown
| `ntp_status` | NTPサーバーとの同期状態を確認 | 不要（null） |
```

**Step 2 — `TestItem.test_type` の `Literal` に追加**

```python
# src/agentic_ni/agents/validator.py
class TestItem(BaseModel):
    test_type: Literal[
        "ospf_neighbors",
        "bgp_summary",
        "ping",
        # ... 既存のタイプ ...
        "ntp_status",   # ← ここに追加
    ]
```

**Step 3 — `_execute_test()` の `elif` チェーンに追加**

```python
# src/agentic_ni/agents/validator.py
elif item.test_type == "ntp_status":
    data = pyats_tools.check_ntp_status(testbed_yaml, item.device)
    ok = data["synchronized"]
    detail = (
        f"NTP synchronized to {data['ref_clock']}"
        if ok
        else "NTP not synchronized"
    )
```

**Step 4 — `pyats_tools.py` に実装関数を追加**

```python
# src/agentic_ni/tools/pyats_tools.py
def check_ntp_status(testbed_yaml: str, device: str) -> dict:
    """NTPサーバーとの同期状態を確認する。"""
    # pyATS/Genie で "show ntp status" を実行してパース
    ...
    return {"synchronized": True, "ref_clock": "10.0.0.1"}
```

#### 仕組みのまとめ

```
LLM（計画係）                    Python（実行係）
    │                                │
    │  with_structured_output()      │
    │  → TestPlan (Pydantic)         │
    │    tests: [                    │
    │      {test_type: "ping",  ─────┼──→ pyats_tools.check_ping()
    │       device: "R1",            │
    │       target: "2.2.2.2"}  ─────┼──→ pyats_tools.check_ping()
    │    ]                           │
    │                                │
    │  FailureAnalysis (Pydantic)    │
    │  → root_cause: "..."           │
    └────────────────────────────────┘

LLM は「何をすべきか」の計画書（JSON）を出力するだけ。
実際の pyATS コマンド実行は Python の _execute_test() が担う。
```

- **LLM がテストタイプを知る手段** → `validator_system.md` のテーブル（自然言語の説明）
- **LLM の出力を制約する手段** → `TestItem.test_type` の `Literal` 型（不正値を弾く）
- **テストを実行する手段** → `_execute_test()` の `if/elif` チェーン + `pyats_tools` 関数

---

### Python API から実行する場合

#### 通常モード

```python
from agentic_ni.graph import compile_graph, initial_state

app = compile_graph()
result = app.invoke(initial_state(
    requirement="R1 と R2 を OSPF エリア 0 で接続する",
    prompt_set="demo",
))
print(result["final_report"])
```

#### トラブルシューティングモード

```python
from agentic_ni.graph import compile_graph_troubleshoot, initial_state_troubleshoot

app = compile_graph_troubleshoot()
result = app.invoke(initial_state_troubleshoot(
    lab_id="abc-1234",
    issue="OSPF ネイバーが確立しない",
    prompt_set="demo",
))
print(result["final_report"])
```

#### 設計分析モード

```python
from agentic_ni.graph import compile_graph_analyze, initial_state_analyze

app = compile_graph_analyze()
result = app.invoke(initial_state_analyze(lab_id="abc-1234", prompt_set="demo"))
print(result["final_report"])
```

#### 設計改善モード

```python
from agentic_ni.graph import compile_graph_improve, initial_state_improve

app = compile_graph_improve()
result = app.invoke(initial_state_improve(
    lab_id="abc-1234",
    analyze_request="OSPF に BFD を追加したい",
    prompt_set="demo",
))
print(result["final_report"])
```

#### トポロジー提供済みコンフィグ生成モード

```python
from agentic_ni.graph import compile_graph, compile_graph_dry_run, initial_state

# CMLへデプロイして検証まで実行
app = compile_graph()
result = app.invoke(initial_state(
    requirement="...",
    prompt_set="demo3",
    use_provided_topology=True,  # configs/demo3/topology.yaml を使用
))
print(result["final_report"])

# ドライラン（コンフィグ生成のみ）
app = compile_graph_dry_run()
result = app.invoke(initial_state(
    requirement="...",
    prompt_set="demo3",
    use_provided_topology=True,
))
print(result["final_report"])
```

#### ドライランモード

```python
from agentic_ni.graph import compile_graph_dry_run, initial_state

app = compile_graph_dry_run()
result = app.invoke(initial_state(requirement="R1 と R2 を OSPF で接続する", prompt_set="demo"))
print(result["final_report"])
# → configs/demo/ に topology.yaml, R1.cfg, R2.cfg, ip_ledger.md 等が保存される
```

### ベクトルRAG機能（知識ベース）

`rag/` ディレクトリに置いたテキストファイルを ChromaDB に登録し、
設計・トラブルシューティングの際に自動で参照します。
「社内標準」「設計ガイド」「既知の問題リスト」など、任意の知識を LLM の考慮に取り込めます。

| 項目                      | 内容                                     |
| ------------------------- | ---------------------------------------- |
| **データ源**              | `rag/` 内のテキストファイル              |
| **登録タイミング**        | `--rag-index` コマンドで手動登録         |
| **有効化**                | フラグ不要。インデックスがあれば常時有効 |
| **空の場合**              | 検索コストほぼゼロで素通り（副作用なし） |
| **ChromaDB コレクション** | `knowledge_base`                         |

**動作フロー**:
```
1. rag/ にファイルを配置する
   rag/ospf_guide.md       ← 付属の OSPF 設計・トラブルシューティングガイド
   rag/company_std.txt     ← 社内標準など任意追加可能

2. 索引化（ファイルを追加・更新したときに再実行）
   agentic-ni --rag-index
   → ospf_guide.md: 6 チャンク
   → 合計 6 チャンクを知識ベースに登録しました。

3. 通常通り実行（追加フラグ不要）
   agentic-ni demo
   → 設計プロンプトに自動で知識ベースの関連チャンクが追記される
```

**対応ファイル形式**: `.txt` / `.md` / `.json`

**チャンク分割**: 1チャンク = 最大 1000 文字（150 文字オーバーラップ）

**LLM への送信数**: インデックス全体ではなく、**要件テキストとの類似度が高い上位 3 チャンクのみ**を LLM プロンプトに追加します。類似度が低いチャンクは閾値フィルタで除外されます。

| 状況                             | API に追加されるチャンク数                  |
| -------------------------------- | ------------------------------------------- |
| 要件に関連するドキュメントがある | 最大 3 チャンク（≈ 1,500〜1,800 トークン） |
| 類似するものが少ない要件         | 0〜1 チャンク                               |
| `rag/` が未インデックス          | 0 チャンク（副作用なし）                    |

**付属サンプル**: `rag/` ディレクトリに以下の 5 ファイルを同梱しています（合計 24 チャンク）。

| ファイル                | 内容                                                        | チャンク数 |
| ----------------------- | ----------------------------------------------------------- | ---------- |
| `ospf_guide.md`         | OSPF 設計・設定・タイマー・トラブルシューティング           | 6          |
| `bgp_guide.md`          | iBGP/eBGP ピアリング・update-source・よくある失敗           | 4          |
| `vlan_l2_guide.md`      | VLAN・トランク・SVI・Router-on-a-Stick（iosvl2 向け）       | 4          |
| `cml_design_guide.md`   | CML トポロジー YAML の書き方・設計パターン・IP アドレス定石 | 4          |
| `ios_variants_guide.md` | IOSv / IOL / CSR1000v / NX-OS の違い・コマンド対照表        | 6          |

```bash
# 知識ベースを索引化
agentic-ni --rag-index

# ディレクトリを指定して索引化
agentic-ni --rag-index ./my_docs

# 知識ベースのインデックスを全消去
agentic-ni --rag-clear-knowledge

# 両コレクションの統計を確認
agentic-ni --rag-stats
# → RAGストア統計:
# →   実行ログ RAG (成功事例): 0 件
# →   知識ベース RAG (テキストファイル): 24 チャンク
# →   保存場所: ~/.agentic_ni/rag_store/
```

**インストール**（初回のみ）:
```bash
uv sync --extra rag        # chromadb のみ
uv sync --extra all        # pyATS/Genie + chromadb 両方
# または
pip install chromadb pysqlite3-binary
```

> **`uv sync --extra` の注意**: `--extra network` と `--extra rag` を**別丅に**実行すると、後から実行した方のパッケージしか残らない場合があります。両方入れる場合は `uv sync --extra all` または `uv sync --extra network --extra rag` と同時指定してください。

> **初回実行時の注意**: chromadb はデフォルトで埋め込みモデル `all-MiniLM-L6-v2` を
> HuggingFace から自動ダウンロードします（約90MB）。オフライン環境では事前にダウンロードが必要です。

**保存場所**: `~/.agentic_ni/rag_store/`（`RAG_STORE_PATH` 環境変数で変更可能）

#### 知識ベース有無の比較デモ（`--dry-run` で CML 不要）

索引ありと索引なしで LLM の設計出力がどう変わるかを確認できます。

**Step 1 — 索引なしで設計**

```bash
agentic-ni --rag-clear-knowledge   # インデックスを消去
agentic-ni demo --dry-run          # 索引なしで設計
```

期待されるコンソール出力（設計エージェントのログ）:
```
  [知識ベース] 未インデックス（スキップ）。agentic-ni --rag-index で索引化できます。
```

`configs/demo/R1.cfg` を別の場所に保存しておく:
```bash
cp configs/demo/R1.cfg /tmp/R1_without_rag.cfg
```

**Step 2 — 索引ありで設計**

```bash
agentic-ni --rag-index             # ospf_guide.md を索引化（6 チャンク）
agentic-ni demo --dry-run          # 索引あり（同じ要件）で設計
```

期待されるコンソール出力:
```
  [知識ベース] rag/ の参考情報を設計プロンプトに追加しました。
```

**Step 3 — 差分比較**

```bash
diff /tmp/R1_without_rag.cfg configs/demo/R1.cfg
```

**差が出やすいポイント**:

`rag/ospf_guide.md` が明示的に推奨している設定で差が表れる可能性があります。

| 設定                             | 索引なし（LLM デフォルト） | 索引あり（ガイド参照）                 |
| -------------------------------- | -------------------------- | -------------------------------------- |
| `ip ospf network point-to-point` | 省略されることが多い       | ガイドが「ルータ間には必ず設定」と明記 |
| `router-id` の明示               | 省略されることがある       | ガイドが「Loopback0 を明示推奨」と記載 |
| Loopback の `network` 文         | 漏れることがある           | ガイドがパターンを具体例付きで説明     |

> **注意**: LLM の出力は非決定的（同じ入力でも毎回異なる可能性あり）なため、
> 差が出るかどうかは実行ごとに変わります。
> `rag/` のドキュメントに「必ず〇〇を含めること」という**強い命令形**の記述を加えるほど効果が安定します。

<br>

---

<br>

## デモンストレーション手順

「設計 → デプロイ → 検証失敗 → 再設計 → 再検証 → 成功」というループを確実に再現するためのデモ用要件と手順を示します。

### デモ用要件（推奨）

以下の要件は、Loopbackアドレスの指定・pingの宛先IPの明示・OSPFへのアドバタイズなど、細かい条件が含まれるため LLM が最初の設計でミスしやすく、リトライループが自然に発生します。

```
R1とR2をOSPFエリア0で接続すること。
それぞれにLoopbackインターフェースを設定し（R1: 1.1.1.1/32、R2: 2.2.2.2/32）、
LoopbackアドレスをOSPFでアドバタイズすること。
R1から 2.2.2.2 へpingが通り、R2から 1.1.1.1 へpingが通ること。
```

### デモの実行

```bash
agentic-ni "R1とR2をOSPFエリア0で接続すること。それぞれにLoopbackインターフェースを設定し（R1: 1.1.1.1/32、R2: 2.2.2.2/32）、LoopbackアドレスをOSPFでアドバタイズすること。R1から 2.2.2.2 へpingが通り、R2から 1.1.1.1 へpingが通ること。"
```

### 期待される動作フロー

```
1. 設計エージェント（1回目）
   ↓ トポロジーYAML・コンフィグを生成
2. 検証エージェント（1回目）
   ↓ CMLにラボを作成してデプロイ
   ↓ OSPFネイバー確認・ping確認を実行
   ↓ FAIL（例: Loopbackへのpingが通らない、OSPFでアドバタイズされていない）
   ↓ 原因推論 → error_log に格納
3. 設計エージェント（2回目）
   ↓ error_log を参照して差分修正
4. 検証エージェント（2回目）
   ↓ 既存ラボにコンフィグを更新・再起動
   ↓ 全テストPASS
5. 最終レポート出力
   ↓ トポロジーYAML・機器コンフィグ・テスト結果をすべて含むレポート
```

### ログの確認

実行中のルーター接続・コマンド実行ログは `logs/` ディレクトリに保存されます。

```bash
# 最新のログを確認
ls -lt logs/
cat logs/agentic-ni-*.log | tail -100

# OSPFネイバーの確認コマンドのやり取りを抽出
grep -A5 "show ip ospf neighbor" logs/agentic-ni-*.log
```

### MAX_RETRIES の調整

デモで失敗ループをより長く見せたい場合や、素早く結果を出したい場合は `.env` で調整します。

```dotenv
# 最大リトライ回数（デフォルト: 5）
MAX_RETRIES=3
```

<br>

---

<br>

## 今後の実装計画

現在の基盤（要件入力 → 設計 → CMLデプロイ → 検証 → 修正ループ）を活かして、
実際のネットワークエンジニアにとって役立つ機能を順次拡充する。

### Phase A — テスト・検証の拡充 ✅ 完了

**目標**: より現実的なネットワーク検証ができるようにします。

| 追加テストタイプ   | 説明                                                                             |
| ------------------ | -------------------------------------------------------------------------------- |
| `route_table`      | `show ip route` でルーティングテーブルを確認。特定プレフィックスの有無を検証する |
| `interface_status` | `show interface` でインターフェースの up/up 状態を確認                           |
| `traceroute`       | 経路が期待通りのホップを通過しているかを確認                                     |
| `bgp_path`         | BGP best path が期待通りのピアから学習されているかを確認                         |

**実装方針**:
- `pyats_tools.py` に新関数を追加
- `TestItem.test_type` の `Literal` を拡張
- `validator_system.md` のテストタイプ表を更新

<br>

---

<br>

### Phase B — 障害シミュレーション ✅ 完了

**目標**: Phase A（全テストPASS）後に、CMLリンク切断/復旧を自動実施し、冗長性・フェイルオーバー・復旧を検証します。

`cml_tools.set_link_state()` を CML インターフェースレベル切断（`interface.shutdown()` / `interface.bring_up()`）に拡張し、実装しました。詳細は「実装セクション Phase B」を参照してください。

```bash
# demo2 セットで障害シミュレーション付き実行（R1-R2-R3 フルメッシュ OSPF）
agentic-ni demo2 --fault-sim
```

<br>

---

<br>

### Phase C — コンフィグ生成のみモード ✅ 完了

**目標**: CML環境がなくても設計・コンフィグ生成だけ使えるようにします。

```bash
# CMLデプロイをスキップしてコンフィグを出力するだけ
agentic-ni demo --dry-run
```

**出力**: `configs/<セット名>/` ディレクトリに以下のファイルを保存する。
- `topology.yaml` — CML向けトポロジー定義
- `R1.cfg`, `R2.cfg` ... — 機器ごとのコンフィグファイル

<br>

---

<br>

### Phase D — 設計ドキュメント自動生成 ✅ 完了

**目標**: 検証成功後またはドライラン実行後に、実務で使えるドキュメントを自動生成してファイルに保存します。

**生成ドキュメント**:

| ドキュメント         | ファイル名          | 内容                                                                 |
| -------------------- | ------------------- | -------------------------------------------------------------------- |
| IP アドレス台帳      | `ip_ledger.md`      | デバイス・インターフェース・CIDR・サブネットの一覧表（Markdown）     |
| IP アドレス台帳      | `ip_ledger.csv`     | 同上（CSV）                                                          |
| ルーティング設計書   | `routing_design.md` | OSPF プロセス ID・Router-ID・エリア・BGP AS 番号・ネイバーのサマリー |
| コンフィグファイル群 | `<device>.cfg`      | 機器ごとの running-config                                            |
| トポロジー定義       | `topology.yaml`     | CML 向けトポロジー YAML                                              |

**タスク**:
- [x] `graph.py` に `_parse_ip_ledger(device_configs)` を実装（regex で IP アドレス抽出）
- [x] `graph.py` に `_parse_routing_config(device_configs)` を実装（OSPF / BGP 設定抽出）
- [x] `graph.py` に `_generate_design_docs(state, out_dir)` を実装
  - topology.yaml + .cfg + ip_ledger.md + ip_ledger.csv + routing_design.md を `configs/<prompt_set>/` に保存
  - final_report に追記する Markdown サマリーを返す
- [x] `report_node()` から `_generate_design_docs()` を呼び出すよう変更
- [x] `dry_run_node()` を `_generate_design_docs()` を利用するよう更新
- [x] `tests/test_design_docs.py` でユニットテスト 38 件 PASS

**出力ファイル例（`agentic-ni demo` 実行後）**:
```
configs/demo/
  topology.yaml        ← CML トポロジー定義
  R1.cfg               ← R1 コンフィグ
  R2.cfg               ← R2 コンフィグ
  ip_ledger.md         ← IP アドレス台帳（Markdown）
  ip_ledger.csv        ← IP アドレス台帳（CSV）
  routing_design.md    ← OSPF / BGP 設計サマリー
```

**ip_ledger.md の出力例**:
```
| デバイス | インターフェース | アドレス（CIDR） | サブネット |
|---|---|---|---|
| R1 | GigabitEthernet0/0 | 10.0.12.1/30 | 10.0.12.0/30 |
| R1 | Loopback0 | 1.1.1.1/32 | 1.1.1.1/32 |
| R2 | GigabitEthernet0/0 | 10.0.12.2/30 | 10.0.12.0/30 |
| R2 | Loopback0 | 2.2.2.2/32 | 2.2.2.2/32 |
```

**完了基準**: `agentic-ni demo` または `agentic-ni demo --dry-run` 実行後に `configs/demo/` へ上記 5 種のファイルが自動保存されること。 ✅ 完了（38 件ユニットテスト PASS）

<br>

---

<br>

### Phase E — 既存CMLラボの取り込みと分析 ✅ 完了

**目標**: 既存のCMLラボを読み込み、AIが設計品質を評価して問題点・改善提案をレポートします。
また、改善要求に基づいて新しいコンフィグを自動生成しファイルに保存します。

```bash
# 既存ラボの設計を分析してレポートを出力（変更なし）
agentic-ni demo --analyze
agentic-ni demo --analyze <lab_id>

# 既存ラボのコンフィグを改善して configs/<set>/ に保存
agentic-ni demo --improve --request "OSPFにBFDを追加したい"
agentic-ni demo --improve <lab_id> --request "Loopbackインターフェースを追加したい"
```

**--analyze フロー（実装済み）**:
```
1. collect  — 全機器の running-config と show コマンド出力を収集
2. analyze  — LLM が設計品質を評価（問題 / 改善提案）
3. report   — 設計分析レポートを出力（変更なし）
```

**--improve フロー（実装済み）**:
```
1. collect  — 全機器の running-config を収集
2. improve  — LLM が改善要求に基づいて改善後のコンフィグを生成
3. save     — configs/<prompt_set>/<device>.cfg に保存してレポートを出力
```

**Phase H (troubleshooter) との主な違い**:

| 項目       | Phase E --analyze    | Phase E --improve               | Phase H           |
| ---------- | -------------------- | ------------------------------- | ----------------- |
| 目的       | 設計品質評価         | 設計改善コンフィグ生成          | 障害の自動修正    |
| 変更の適用 | なし（読み取り専用） | ファイル保存のみ（deploy なし） | CML に直接投入    |
| ループ     | なし（1 パス）       | なし（1 パス）                  | 最大 3 回リトライ |

**実装したコンポーネント**:

| コンポーネント                 | 内容                                                                |
| ------------------------------ | ------------------------------------------------------------------- |
| `analyzer.py`                  | `run_analyze` / `run_improve` の 2 関数                             |
| `AnalysisIssue` (Pydantic)     | `severity` / `device` / `description` / `recommendation`            |
| `AnalysisResult` (Pydantic)    | `overall_rating` / `summary` / `issues` / `improvement_suggestions` |
| `ImprovementOutput` (Pydantic) | 改善後 `device_configs` / `changes_summary` / `rationale`           |
| `analyzer_system.md`           | 分析・改善エージェント専用システムプロンプト                        |
| `compile_graph_analyze()`      | `collect → analyze → report` グラフ                               |
| `compile_graph_improve()`      | `collect → improve → save` グラフ                                 |
| `export_lab_configs(lab_id)`   | CML から Day-0 コンフィグを取得（`cml_tools.py`）                   |
| `export_lab_topology(lab_id)`  | CML からトポロジー YAML をエクスポート（`cml_tools.py`）            |
| `tests/test_analyzer.py`       | ユニットテスト 30 件 PASS                                           |

**完了基準**: `--analyze` で既存ラボの設計評価レポートが出力され、`--improve --request` で改善コンフィグが `configs/<set>/` に保存されること。 ✅ 完了（30 件ユニットテスト PASS）

<br>

---

<br>

### Phase F — プロンプトセットの拡充（工数中〜大）

**目標**: 実務でよく使われるネットワーク構成のプロンプトセットを追加します。

| プロンプトセット名      | 概要                                          |
| ----------------------- | --------------------------------------------- |
| `mpls_l3vpn`            | MPLS L3VPN（PE-CE BGP、VRF設定）              |
| `datacenter_leaf_spine` | Data Center Leaf-Spine（BGP EVPN/VXLAN）      |
| `wan_dual_isp`          | デュアルISP冗長（BGP、フェイルオーバー）      |
| `security_zone`         | ゾーンベースファイアウォール（IOS ZBF）       |
| `qos_dscp`              | QoS設計（DSCP分類・マーキング・キューイング） |

各セットは `prompts/<set>/` に以下を用意します。
- `requirement.md` — 標準的な要件テンプレート（カスタマイズして使う）
- `architect.md` — そのプロトコルスタック特有の設計ヒント
- `validator.md` — 必須テスト一覧と固有失敗パターン

<br>

---

<br>

### Phase G — マルチベンダー対応（工数大）

**目標**: Cisco以外のベンダーにも対応します。

| ベンダー            | node_definition | 設定言語                 |
| ------------------- | --------------- | ------------------------ |
| Cisco Nexus (NX-OS) | `nxosv`         | NX-OS CLI                |
| Arista EOS          | `eos`           | EOS CLI                  |
| Juniper vMX         | `vmx`           | Junos CLI / set コマンド |

**実装方針**:
- `architect_system.md` にベンダーごとのコンフィグテンプレートを追加
- pyATSの `os` 設定をノードごとに自動設定
- ベンダー固有のShowコマンド対応（Genie parsers）

<br>

---

<br>

### Phase H — トラブルシューティングモード ✅ 完了

**目標**: 「動かない既存構成の原因診断と自動修正」に特化したモードを追加します。

```bash
# 既存ラボの問題を診断・自動修正
agentic-ni demo --troubleshoot <lab_id>
agentic-ni demo --troubleshoot <lab_id> --issue 'OSPF ネイバーが上がらない'
```

**実行フロー（実装済み）**:
```
1. collect  — 全機器の running-config と show コマンド出力を収集
2. diagnose — LLM が根本原因を診断
3. fix      — LLM が差分修正コマンドを生成し configure terminal で投入
4. verify   — テストを実行（deploy なし）
5. 全 PASS → 完了レポート / FAIL & リトライ残 → collect に戻る
```

**Phase A/B との主な違い**:

| 項目               | Phase A（設計・検証）               | Phase H（トラブルシューティング）   |
| ------------------ | ----------------------------------- | ----------------------------------- |
| 起点               | 自然言語の要件                      | 既存ラボ ID + 問題説明              |
| 修正方式           | wipe + 再デプロイ                   | `configure terminal` による差分適用 |
| 主担当エージェント | 設計エージェント + 検証エージェント | トラブルシューターエージェント      |
| ラボ作成           | あり（新規）                        | なし（既存ラボに接続）              |

**実装したコンポーネント**:

| コンポーネント                      | 内容                                                       |
| ----------------------------------- | ---------------------------------------------------------- |
| `troubleshooter.py`                 | `run_collect` / `run_diagnose` / `run_fix` の 3 関数       |
| `DiagnosisResult` (Pydantic)        | `root_cause` / `affected_devices` / `severity` / `summary` |
| `FixPlan` / `FixCommand` (Pydantic) | デバイス別修正コマンドと rollback コマンド                 |
| `troubleshooter_system.md`          | トラブルシューター専用システムプロンプト                   |
| `compile_graph_troubleshoot()`      | `collect → diagnose → fix → verify` ループグラフ        |
| `initial_state_troubleshoot()`      | トラブルシューティングモード用初期ステートファクトリー     |
| `TroubleshootFixRecord` (TypedDict) | 修正履歴レコード（`state.py`）                             |
| `tests/test_troubleshooter.py`      | ユニットテスト（LLM/CML/pyATS はすべてモック）             |

**完了基準**: `--troubleshoot` で既存ラボに接続し、`collect → diagnose → fix → verify` のサイクルが最大 `TROUBLESHOOT_MAX_RETRIES`（デフォルト 3 回）まで繰り返せること。 ✅ 完了（ユニットテスト PASS）

<br>

---

<br>

### Phase I — 実機適用モード（工数大）

**目標**: CML を「ステージング環境」として使い、検証済みコンフィグを pyATS/Unicon で実機へ安全に投入します。

```bash
# CML 検証 → 実機適用
agentic-ni demo --apply-to-live

# インベントリファイルを明示指定
agentic-ni demo --apply-to-live --inventory inventory/production.yaml

# 実機でも pyATS 検証テストを実行
agentic-ni demo --apply-to-live --live-verify
```

**コンセプト**:

```
[ 要件入力 ]
     ↓
 CML 設計・検証（既存フロー）
     ↓ 全テスト PASS
 【Phase I: ここから新規】
     ↓ --apply-to-live
 実機への疎通確認・running-config バックアップ取得
     ↓
 Human による最終承認（必須・スキップ不可）
     ↓ 承認
 pyATS/Unicon で実機に設定投入
     ↓
 実機で pyATS テスト実行（--live-verify 指定時）
     ↓
 適用レポート出力
```

CML で PASS したコンフィグのみが実機に届く。失敗した設計は実機に触れない。

#### インベントリファイル仕様

CML のノード名と実機の接続先を対応付けるファイルを `inventory/<プロンプトセット名>.yaml` に配置します。

```yaml
# inventory/demo.yaml
metadata:
  description: "demo セット向け実機インベントリ"

devices:
  R1:
    host: "192.168.100.1"
    device_type: "cisco_ios"   # pyATS の os マッピングに使用（cisco_ios → ios）
    username: "${LIVE_USERNAME}"
    password: "${LIVE_PASSWORD}"
    port: 22
    apply_mode: "config_merge"  # config_merge / config_replace / incremental

  R2:
    host: "192.168.100.2"
    device_type: "cisco_ios"
    username: "${LIVE_USERNAME}"
    password: "${LIVE_PASSWORD}"
    port: 22
    apply_mode: "config_merge"
```

| apply_mode       | 動作                                                          | 用途                   |
| ---------------- | ------------------------------------------------------------- | ---------------------- |
| `config_merge`   | `configure terminal` で行単位に投入（非破壊・**デフォルト**） | 既存設定への追記       |
| `config_replace` | `configure replace` で設定全体を置換                          | 初回デプロイ・完全一致 |
| `incremental`    | CML と実機の diff を取って差分のみ投入                        | 変更量を最小化         |

#### 新規コンポーネント

| コンポーネント                  | 内容                                                                                                             |
| ------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `tools/pyats_tools.py`（追加分）| `load_inventory` / `check_connectivity` / `backup_running_config` / `apply_config` / `rollback_config` の 5 関数（pyATS/Unicon バックエンド） |
| `LiveApplyRecord` (TypedDict)   | `device` / `host` / `success` / `backup_config` / `rollback_done` 等                                             |
| `live_precheck_node`            | インベントリ読込・SSH 疎通確認・running-config バックアップ取得                                                  |
| `human_confirm_live_node`       | `interrupt()` で停止・Human の承認なしでは先へ進まない                                                           |
| `live_apply_node`               | pyATS/Unicon で各デバイスにコンフィグ投入・失敗時は自動ロールバック                                              |
| `live_verify_node`              | 実機に対して pyATS で同一テスト計画を実行（任意）                                                                |
| `live_report_node`              | 適用結果レポートを final_report に追記                                                                           |
| `compile_graph_apply_to_live()` | 上記ノードをつなぐグラフ                                                                                         |

#### 多段安全機構

```
Level 1: インベントリ存在チェック（ファイルがなければ即エラー）
Level 2: SSH 疎通確認（繋がらないデバイスがあれば中断）
Level 3: バックアップ取得の成否確認（失敗すれば中断）
Level 4: Human-in-the-Loop（承認なしでは絶対に進まない）
Level 5: apply_mode=config_merge デフォルト（破壊的変更を抑制）
Level 6: 失敗デバイスの自動ロールバック
```

Human 確認画面:
```
⚠️  実機へのコンフィグ適用を開始しようとしています

【適用対象】
  ✅ R1 (192.168.100.1) — config_merge — バックアップ取得済み (847 行)
  ✅ R2 (192.168.100.2) — config_merge — バックアップ取得済み (612 行)

【CML テスト結果（検証済み）】
  ✅ OSPF ネイバー確認: PASS  ✅ ping 2.2.2.2: PASS

続行しますか？ (yes / no / rollback-only)
```

`rollback-only` を選ぶと適用なしで手動ロールバックモードへ移行する。

#### 実装タスク一覧

| #   | ファイル                      | 変更内容                                                               |
| --- | ----------------------------- | ---------------------------------------------------------------------- |
| 1   | `pyproject.toml`              | `[optional-dependencies]` の `live` は network（pyATS）に統合          |
| 2   | `.env.example`                | `LIVE_USERNAME` / `LIVE_PASSWORD` を追加                               |
| 3   | `inventory/demo.yaml`         | サンプルインベントリファイルを新規作成                                 |
| 4   | `tools/pyats_tools.py`        | Live 操作関数を追加（`load_inventory` ほか 5 関数、pyATS/Unicon 使用） |
| 5   | `state.py`                    | `LiveApplyRecord` TypedDict と `live_*` フィールドを追加               |
| 6   | `graph.py`                    | 4 ノード + `compile_graph_apply_to_live()` を追加                      |
| 7   | `graph.py`                    | `main()` に `--apply-to-live` / `--inventory` / `--live-verify` を追加 |
| 8   | `tests/test_pyats_live_backend.py` | 新規作成（pyATS/Unicon モック）                                   |


**完了基準**: `--apply-to-live` で CML 検証済みコンフィグが実機に投入され、適用前バックアップと適用後テスト結果を含むレポートが出力されること。

<br>

---

<br>

### Phase S — スケーラビリティ強化

**目標**: 中規模・大規模ネットワーク（30〜100+ ノード）に対応できるよう、LLM コンテキスト消費・デプロイ時間・テスト実行時間のボトルネックを段階的に解消します。

#### 現状のボトルネック

| ボトルネック | 課題 | 対象ファイル |
|---|---|---|
| LLM コンテキスト上限 | 全デバイスコンフィグを 1 プロンプトに結合 → 128K トークン超え | `architect.py` |
| モノリシックなリトライ | 1 テスト失敗 → 全トポロジー + 全コンフィグを再生成 | `architect.py`, `validator.py` |
| 固定タイムアウト | `deploy_lab()` の 300 秒は 30 台超のラボで不足 | `cml_tools.py` |
| 逐次テスト実行 | テスト件数に比例して検証時間が線形増加 | `validator.py` |
| State の肥大化 | `device_configs` dict がメモリに蓄積 | `state.py` |

#### 戦略一覧と進捗

| 優先度 | 戦略 | 概要 | 実装コスト | 進捗 |
|---|---|---|---|---|
| **高** | [Strategy B: 差分リトライ](#strategy-b--差分リトライ-高優先) | 失敗デバイスのコンフィグのみ再生成 | 低 | ✅ 完了 |
| **高** | [Strategy C: 動的タイムアウト](#strategy-c--動的タイムアウト-高優先) | ノード数に応じたタイムアウト自動計算 | 低 | ✅ 完了 |
| **中** | [Strategy D: テスト並列化](#strategy-d--テストの並列実行-中優先) | `ThreadPoolExecutor` でテストを並列実行 | 中 | ✅ 完了 |
| **中** | [Strategy E: 設定の外部化](#strategy-e--設定の外部化-中優先) | `device_configs` をファイルに分離 | 中 | ✅ 完了 |
| **低** | [Strategy A: 階層的設計](#strategy-a--階層的設計トポロジー分割-低優先) | LangGraph Map-Reduce でポッド並列設計 | 高 | ☐ 未着手 |

---

#### Strategy B — 差分リトライ（高優先）

**課題**: 1 テストが FAIL するだけで全デバイスのコンフィグを LLM に再送信・再生成します。20 台規模だと毎リトライで数万トークンを消費し、コンテキスト上限にも近づきます。

**解決策**: 失敗に関与したデバイスのみを LLM に送り、残りは前回の結果を流用する。

```
【現在】
  FAIL → architect が全 20 台のコンフィグをLLMに送信 → 全 20 台を再生成 → 全 20 台を更新

【Strategy B 適用後】
  FAIL → affected_devices = ["R1", "R3"] → LLMにはR1/R3のコンフィグのみ送信
       → R1/R3 のコンフィグのみ再生成 → 残り 18 台は前回の結果をそのまま流用
```

**変更ファイル**:
- `state.py`: `failed_devices: list[str]` を追加
- `validator.py`: `FailureAnalysis` に `affected_devices: list[str]` を追加し `run()` で `failed_devices` を返す
- `architect.py`: `_build_messages()` / `_build_messages_config_only()` を差分対応に変更し `run()` でコンフィグをマージ

**タスク**:
- [x] `state.py` に `failed_devices: list[str]` を追加
- [x] `validator.py` の `FailureAnalysis` に `affected_devices` を追加
- [x] `validator.py` の `run()` で `failed_devices` を返す
- [x] `architect.py` の `_build_messages()` を差分修正モードに変更
- [x] `architect.py` の `_build_messages_config_only()` を差分修正モードに変更
- [x] `architect.py` の `run()` でコンフィグをマージして返す（失敗デバイス分のみ上書き）
- [ ] `tests/test_architect.py` に差分リトライのテストケースを追加

**完了基準**: `failed_devices` が設定されているリトライでは、LLM への入力が失敗デバイス分のみになり、全デバイス送信時よりトークン数が削減されること。✅ 完了（コード実装済み）

---

#### Strategy C — 動的タイムアウト（高優先）

**課題**: `deploy_lab()` のタイムアウトが固定 300 秒のため、30 台超のラボで `RuntimeError: ノードが規定時間内に起動しませんでした` が発生することがある。

**解決策**: ノード数に応じてタイムアウトを自動計算（目安: `max(300, node_count × 30)` 秒）。

```python
# cml_tools.py の変更イメージ
def deploy_lab(
    topology_yaml: str,
    device_configs: dict[str, str],
    title: str = "agentic-ni-lab",
    timeout: int | None = None,   # None → ノード数で自動計算
) -> str:
    if timeout is None:
        node_count = len(device_configs)
        timeout = max(300, node_count * 30)
```

**タスク**:
- [x] `cml_tools.deploy_lab()` のタイムアウトを `None` デフォルトにして自動計算へ変更
- [x] `cml_tools.update_configs_and_restart()` も同様に変更
- [x] `tests/test_cml_tools.py` にタイムアウト計算のテストを追加（11 件）

**完了基準**: ノード数 30 台の場合に 900 秒のタイムアウトが適用されること。✅ 完了（30 件テスト PASS）

---

#### Strategy D — テストの並列実行（中優先）

**課題**: テストが逐次実行されるため、検証時間がテスト数に比例して増加（10 テスト × 平均 10 秒 = 100 秒）。

**解決策**: `concurrent.futures.ThreadPoolExecutor` を使ってテストを並列実行。

```python
# validator.py の変更イメージ
from concurrent.futures import ThreadPoolExecutor

def _run_tests_parallel(
    plan: TestPlan, testbed_yaml: str, max_workers: int = 8
) -> list[TestResult]:
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_execute_test, item, testbed_yaml): item
                   for item in plan.tests}
        results = []
        for future in futures:
            results.append((futures[future], future.result()))
    # 元の順序に並べ直す
    ordered = {item: res for item, res in results}
    return [ordered[item] for item in plan.tests]
```

**タスク**:
- [x] `validator.py` のテスト実行ループを `_run_tests()` に封装し `ThreadPoolExecutor` で並列化
- [x] テスト結果の元順序保証（`plan.tests` の順序で並べ直し）
- [x] `MAX_TEST_WORKERS` 環境変数でワーカー数を制御できるようにする（1 = 逐次モード、デフォルト: 8）
- [x] `tests/test_validator.py` に `TestRunTests` クラス 8 件追加

**完了基準**: 10 件のテストが逐次実行より高速に完了し、結果の順序が保証されること。✅ 完了（31 件テスト PASS）

**完了基準**: 10 件のテストが逐次実行より高速に完了し、結果の順序が保証されること。

---

#### Strategy E — 設定の外部化（中優先）

**課題**: `device_configs: dict[str, str]` が `AgentState` 内に保持されるため、50 台ノードでは数 MB 規模になり、LangGraph のステートシリアライズ・LLM 呼び出し時の全ステートダンプが重くなる。

**解決策**: コンフィグをファイルシステム（`configs/<prompt_set>/<device>.cfg`）に書き出し、State にはパスだけ持たせる。

```python
# state.py への追加案
device_config_paths: dict[str, str]
"""デバイス名 → コンフィグファイルパスのマッピング。
Strategy E 適用後は device_configs の代わりにこちらを使用する。"""
```

**タスク**:
- [x] `state.py` に `device_config_paths: dict[str, str]` を追加
- [x] `state.py` に `write_device_configs()` / `load_device_configs()` ヘルパーを実装
- [x] `architect.py` でコンフィグをファイルに保存し、`device_configs={}` にクリアして `device_config_paths` にパスを格納
- [x] `validator.py` / `graph.py` 全体で `load_device_configs()` 経由に変更（後方互換フォールバック付き）
- [x] `tests/test_architect.py` に `Strategy E` テスト 3 件追加

**完了基準**: 50 台ノードのラボで `AgentState` のシリアライズサイズが改善されること。✅ 完了（284 件テスト PASS）

---

#### Strategy A — 階層的設計（トポロジー分割）（低優先）

**課題**: LLM の 1 回の呼び出しで 100 台分のトポロジーとコンフィグを生成するのはコンテキスト上限に当たる。また、LLM が 100 台規模のネットワーク全体を一貫した設計で生成するのは困難。

**解決策**: LangGraph の Send API（Map-Reduce）を使い、トポロジーをポッドに分割して並列設計する。

```python
# graph.py への追加イメージ
from langgraph.types import Send

def split_into_pods(state: AgentState) -> list[Send]:
    """トポロジーをポッドに分割して並列設計ノードへ送る。"""
    pods = _partition_topology(state["topology_yaml"])
    return [
        Send("pod_design_node", {**state, "pod_id": i, "pod_spec": spec})
        for i, spec in enumerate(pods)
    ]
```

**タスク**:
- [ ] ポッド分割ロジック（`_partition_topology()`）を実装
  - コア/エッジ/アクセス等のレイヤーでの分割
  - IP アドレス空間の自動割り当て
- [ ] サブグラフで各ポッドを並列設計（`pod_design_node`）
- [ ] ポッド設計結果をマージするノードを実装（`merge_pod_results_node`）
- [ ] ポッド間インターフェースの整合性チェック
- [ ] `tests/` にポッド分割テストを追加

**完了基準**: 50 台規模のトポロジーが複数ポッドに分割され、各ポッドが独立して設計・検証できること。



済　loggingモジュールへの統一
中断時のラボクリーンアップ
チェックポインターによる再開
プロンプトキャッシング
Few-shot例のプロンプト追加