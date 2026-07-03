# AI駆動型 ネットワーク設計検証 自律エージェントシステム

Cisco CML環境における設計・デプロイ・検証・デバッグのサイクルをAIエージェントが自律的に実行するシステム。

詳細な要件定義は [instruction.md](./instruction.md) を参照。

---

## アーキテクチャ概要

```
[ 人間 (要件入力) ]
       │
       ▼
 ┌─────────────────────────────────────┐
 │ LangGraph (オーケストレーション)      │
 │                                     │
 │  設計エージェント ◀── エラーログ ──┐  │
 │       │                           │  │
 │       │ topology_yaml             │  │
 │       │ device_configs            │  │
 │       ▼                           │  │
 │  検証エージェント ─── FAIL ────────┘  │
 │       │                              │
 │       │ 全PASS                       │
 └───────┼──────────────────────────────┘
         ▼
   最終レポート → 人間が承認
```

---

## 技術スタック

| 層 | 採用技術 |
|---|---|
| オーケストレーション | LangGraph |
| LLM | OpenAI / Anthropic / Ollama（環境変数で切替） |
| CML操作 | `virl2_client` |
| ネットワーク検証 | `pyATS` + `Genie` |
| 設定管理・型安全 | Pydantic v2 |
| 依存関係管理 | `uv` |

---

## プロジェクト構造

```
agentic-ni/
├── README.md
├── instruction.md
├── pyproject.toml
├── .env.example             # 接続情報テンプレート（実体は.envで管理）
│
├── src/
│   └── agentic_ni/
│       ├── __init__.py
│       ├── state.py         # LangGraph共有State定義
│       ├── graph.py         # グラフ組み立て・エントリポイント
│       ├── llm.py           # LLMファクトリー（プロバイダー切替）
│       │
│       ├── agents/
│       │   ├── __init__.py
│       │   ├── architect.py # 設計エージェント（トポロジー・コンフィグ生成）
│       │   └── validator.py # 検証エージェント（デプロイ・テスト・推論）
│       │
│       ├── tools/
│       │   ├── __init__.py
│       │   ├── cml_tools.py     # virl2_client ラッパー
│       │   └── pyats_tools.py   # pyATS/Genie ラッパー
│       │
│       └── prompts/
│           ├── architect_system.md
│           └── validator_system.md
│
└── tests/
    ├── __init__.py
    ├── test_cml_tools.py
    ├── test_pyats_tools.py
    └── test_graph.py
```

---

## 実装フェーズ

### Phase 1 — プロジェクト基盤セットアップ

**目標**: 依存関係・環境変数・プロジェクト骨格を整備する。

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
virl2-client = "*"
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

各エージェントは `get_llm()` を呼び出すだけでよく、プロバイダー切替時にエージェント側のコードは変更不要。

> **Ollamaを使う場合の注意**: 構造化出力（`with_structured_output()`）の精度はモデルに依存します。`llama3.1:70b` 以上を推奨。ローカルリソースが不足する場合は `mistral-nemo` や `qwen2.5:32b` も選択肢です。

> **WSL環境での注意**: `uv sync` がWSLのファイルシステム制限（rename禁止）でエラーになる場合は、pipで代替インストールする。
> ```bash
> python3 -m venv .venv
> .venv/bin/pip install langgraph langchain-openai langchain-anthropic langchain-ollama \
>     virl2-client pydantic python-dotenv pytest pytest-asyncio
> .venv/bin/pip install -e . --no-deps
> ```
> pyATS/Genie は大容量のため Phase 4 で別途インストール: `pip install pyats genie`

**完了基準**: `.venv/bin/pytest tests/test_llm.py -v` で 4 tests PASSED。また `LLM_PROVIDER` を切り替えて `get_llm()` が各プロバイダーのインスタンスを返すことを確認する。 ✅ 完了

---

### Phase 2 — State定義 & グラフ骨格

**目標**: エージェント間の共有Stateとグラフの条件分岐ロジックを定義する（実処理はスタブでよい）。

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

**完了基準**: グラフを `graph.get_graph().draw_mermaid()` で可視化できる。 ✅ 完了

---

### Phase 3 — CMLツール実装

**目標**: virl2_client を使った CML 操作ツール群を実装・単体テストする。

**タスク**:
- [x] `src/agentic_ni/tools/cml_tools.py` を実装
- [x] `tests/test_cml_tools.py` で各ツールを検証

**実装する関数**:

| 関数 | 説明 |
|---|---|
| `create_lab(topology_yaml: str) -> str` | YAMLからラボ作成・起動。`lab_id` を返す |
| `delete_lab(lab_id: str) -> None` | ラボ削除（クリーンアップ） |
| `push_config(lab_id: str, node_name: str, config: str) -> None` | 機器にコンフィグを投入 |
| `set_link_state(lab_id: str, link_id: str, up: bool) -> None` | リンクUP/DOWN（障害シミュレーション） |
| `wait_for_nodes_ready(lab_id: str, timeout: int) -> bool` | 全ノード起動待ち |

**注意事項**:
- 認証情報は `.env` から読み込み、コードにハードコードしない
- `CML_VERIFY_SSL=false` の場合は urllib3 の警告を抑制する
- ラボ作成失敗時は例外を発生させ、呼び出し元でハンドリング

**完了基準**: 実CML環境（またはモック）でラボの作成・削除が往復できる。 ✅ 完了（14件モックテストPASS）

---

### Phase 4 — pyATSツール実装

**目標**: pyATS/Genie を使ったネットワーク検証ツール群を実装・単体テストする。

**タスク**:
- [x] `src/agentic_ni/tools/pyats_tools.py` を実装
- [x] testbed YAMLを動的生成するヘルパー関数を実装
- [x] `tests/test_pyats_tools.py` で各ツールを検証

**実装する関数**:

| 関数 | 説明 |
|---|---|
| `build_testbed(lab_id: str, device_configs: dict) -> str` | CML情報からtestbed YAMLを動的生成 |
| `run_show_command(testbed_yaml: str, device: str, command: str) -> dict` | showコマンドをGenieでJSONパース |
| `check_ospf_neighbors(testbed_yaml: str, device: str) -> dict` | OSPFネイバー状態を確認 |
| `check_bgp_summary(testbed_yaml: str, device: str) -> dict` | BGPピア状態を確認 |
| `check_ping(testbed_yaml: str, device: str, target: str) -> bool` | 疎通確認 |
| `check_vlan_interfaces(testbed_yaml: str, device: str) -> dict` | VLAN/インターフェース状態確認 |

**完了基準**: 実機（またはCML上の仮想機器）に対してOSPFネイバー状態を取得できる。 ✅ 完了（19件モックテストPASS）

> **pyATS未インストールの場合の注意**: 全関数は遅延importによりモジュール自体はインポート可能。実機接続時には `pip install pyats genie` が必要。

---

### Phase 5 — 設計エージェント実装

**目標**: LLMを使って要件からCMLトポロジーYAMLと機器コンフィグを生成するエージェントを実装する。

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

**完了基準**: サンプル要件（「R1とR2をOSPFで接続」）に対して有効なコンフィグが生成される。 ✅ 完了（12件モックテストPASS）

---

### Phase 6 — 検証エージェント実装

**目標**: デプロイ・テスト・失敗推論を行う検証エージェントを実装する。

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

**完了基準**: OSPFエリア番号のミスマッチを検知し、原因推論を `error_log` に格納できる。 ✅ 完了（25件モックテストPASS）

---

### Phase 7 — グラフ統合 & E2Eテスト

**目標**: 全コンポーネントをLangGraphで統合し、エンドツーエンドで動作させる。

**タスク**:
- [x] `graph.py` のスタブを実コンポーネントで置き換え
- [x] `tests/test_e2e.py` でE2Eシナリオを検証
- [x] Human-in-the-Loop（最終承認ステップ）を実装
- [x] 最大リトライ超過時のエスカレーションレポートを実装
- [x] 最終成功レポートのフォーマットを整備

**E2Eテストシナリオ**:

| シナリオ | 期待結果 |
|---|---|
| 正常系：OSPFシンプル接続 | 1〜2回のループで全PASS |
| 異常系：意図的にエリア番号をミスマッチ | 失敗検知 → 修正 → 再テストでPASS |
| 上限超過：修正不能な要件 | MAX_RETRIES後に人間へエスカレーション |

**完了基準**: 自然言語の要件入力から最終レポート出力まで、人手介入なく動作する。 ✅ 完了（16件E2Eテスト・累記99件PASS）

---

## 実装着手順序（推奨）

```
Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5 → Phase 6 → Phase 7
  基盤      骨格      CML操作    pyATS     設計AI    検証AI     統合
  (llm.py
   含む)
```

各Phaseは独立して動作確認できるよう設計されています。
Phase 3・4はCML環境がなくてもモックで進められます。

**Ollamaでオフライン開発する場合**: Phase 1完了後に `ollama pull llama3.1:70b` を実行し、`.env` に `LLM_PROVIDER=ollama` を設定すれば、外部APIキーなしで全Phaseを進められます。

---

## セキュリティ注意事項

- `.env` は `.gitignore` に追加し、リポジトリにコミットしない
- CML接続はSSL証明書検証を本番環境では必ず有効化する
- LLMへ送信するデータに本番ネットワークの機密情報を含めない
- pyATSのtestbed YAMLにパスワードを平文で書く場合はファイル権限に注意

---

## 動作確認手順

既存の実装を別環境で動かす際の確認ステップです。
外部依存（LLM API → CML → pyATS）の順に段階的に検証します。

```
Phase 1: 環境セットアップ確認（外部接続不要）
Phase 2: LLMファクトリー単体テスト（APIキー不要・モック）
Phase 3: .env 設定 → LLM 実疎通確認
Phase 4: 設計エージェント・グラフ単体テスト（LLMモック）
Phase 5: CML 接続確認
Phase 6: pyATS/Genie セットアップ確認
Phase 7: E2E テスト（全機能統合）
```

---

### Phase 1 — 環境セットアップ確認

**目的**: 仮想環境・依存パッケージ・プロジェクト構造が正しいことを確認する。

**前提条件**: Python 3.12 以上、`uv` がインストール済みであること。

```bash
# リポジトリをクローン後、プロジェクトルートへ移動
cd agentic-ni

# 仮想環境の作成と依存パッケージのインストール
uv sync

# 仮想環境を有効化
source .venv/bin/activate

# モジュールの import が通るか確認（APIキー不要）
python -c "from agentic_ni.state import AgentState; print('state: OK')"
python -c "from agentic_ni.llm import get_llm; print('llm: OK')"
python -c "from agentic_ni.graph import build_graph; print('graph: OK')"
```

> **WSL 環境で `uv sync` が失敗する場合**（rename 禁止エラー）:
> ```bash
> python3 -m venv .venv
> .venv/bin/pip install -e ".[dev]"
> ```

**完了条件**: すべての `python -c` コマンドが `OK` を出力する。

---

### Phase 2 — LLMファクトリー単体テスト

**目的**: LLMモジュールがモック込みで正しく動くことを確認する。APIキーは不要。

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

**完了条件**: `test_llm.py` の全テストが PASS する。

---

### Phase 3 — `.env` 設定 → LLM 実疎通確認

**目的**: 実際の LLM API に繋がり、応答が返ることを確認する。

ここでは **OpenAI `gpt-4o-mini`** を使う手順を説明します。

#### 3-1. OpenAI アカウントの作成と API キーの取得

1. **アカウント作成**
   <https://platform.openai.com/signup> にアクセスし、メールアドレスまたは Google / Microsoft アカウントでサインアップする。

2. **支払い方法の登録**
   左メニューの **Billing → Payment methods** からクレジットカードを登録する。
   事前にクレジットをチャージしておく場合は **Add to credit balance** からチャージできる（最低 $5）。

   > **費用の目安**: `gpt-4o-mini` は $0.15 / 1M 入力トークン・$0.60 / 1M 出力トークン。
   > Phase 3 の疎通確認 1 回は 0.01 円以下。Phase 7 まで通しで動かしても数十円程度。

3. **API キーの発行**
   左メニューの **API keys → Create new secret key** をクリックする。
   キー名（例: `agentic-ni`）を入力して **Create secret key** を押す。
   表示された `sk-...` の文字列をコピーする。**この画面を閉じると二度と表示されない**ため、必ずコピーしておく。

4. **（任意）使用量上限の設定**
   **Billing → Limits** で月次の上限金額（Usage limit）を設定しておくと、意図しない高額請求を防げる。

#### 3-2. `.env` の設定

```bash
cp .env.example .env
```

`.env` を開き、以下のように設定する。

```dotenv
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-ここに取得したキーを貼り付け
OPENAI_MODEL=gpt-4o-mini
```

> **注意**: `.env` はリポジトリにコミットしない。`.gitignore` に含まれていることを確認する。
>
> ```bash
> grep '\.env' .gitignore   # ".env" が出力されれば OK
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

**完了条件**: LLM から応答テキストが返る。

---

### Phase 4 — 設計エージェント・グラフ単体テスト

**目的**: LangGraph のフローおよび architect / validator エージェントのロジックを、LLM をモックした状態で確認する。

```bash
pytest tests/test_architect.py tests/test_validator.py tests/test_graph.py -v
```

**完了条件**: 3 ファイルの全テストが PASS する。

---

### Phase 5 — CML 接続確認

**目的**: Cisco CML に繋がり、ラボの作成・削除が正常に動作することを確認する。

**手順**:

1. `.env` に CML の接続情報を設定する。

   ```dotenv
   CML_URL=https://your-cml-server
   CML_USERNAME=admin
   CML_PASSWORD=your_password
   CML_VERIFY_SSL=false
   ```

2. CML 接続テストを実行する。

   ```bash
   pytest tests/test_cml_tools.py -v
   ```

> **実CML環境なしで確認する場合**: `test_cml_tools.py` はモックを使うため、CML への実接続なしでも PASS する。
> 実機接続が必要なテストは別途マーキングされている場合があるため、`-m "not integration"` オプションを付けて実行する。

**完了条件**: `test_cml_tools.py` の全テストが PASS する。

---

### Phase 6 — pyATS/Genie セットアップ確認

**目的**: ネットワーク検証ツール（pyATS/Genie）が正しくインストールされ、使用できることを確認する。

> **注意**: pyATS/Genie は大容量パッケージ（数百 MB）です。インストールに時間がかかります。

**手順**:

1. network extras をインストールする。

   ```bash
   uv sync --extra network
   # または pip を使う場合
   pip install pyats genie
   ```

2. インストールの確認。

   ```bash
   python -c "import pyats; print('pyats:', pyats.__version__)"
   python -c "import genie; print('genie:', genie.__version__)"
   ```

3. テストを実行する。

   ```bash
   pytest tests/test_pyats_tools.py -v
   ```

**完了条件**: `test_pyats_tools.py` の全テストが PASS する。

---

### Phase 7 — E2E テスト（全機能統合）

**目的**: 要件入力 → 設計 → CML デプロイ → 検証 → レポート出力まで通しで動作することを確認する。

**前提条件**: Phase 3〜6 が完了していること。

**手順**:

1. E2E テストを実行する（モック使用）。

   ```bash
   pytest tests/test_e2e.py -v -s
   ```

2. CLI から直接実行する（実 CML・実 LLM API が必要）。

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

**完了条件**: 最終レポート（成功またはエスカレーション）が出力される。

---

### トラブルシューティング

| 症状 | 確認箇所 |
|---|---|
| `ModuleNotFoundError: agentic_ni` | `uv sync` または `pip install -e .` が未実行 |
| `ValueError: 未対応のLLMプロバイダー` | `.env` の `LLM_PROVIDER` の値を確認 |
| LLM API 認証エラー | `.env` の API キーが正しいか確認 |
| CML 接続タイムアウト | `CML_URL` のホスト名・ポートを確認、VPN 接続を確認 |
| pyATS `ImportError` | `uv sync --extra network` または `pip install pyats genie` を実行 |
| `pytest` が見つからない | `.venv/bin/pytest` を使うか `source .venv/bin/activate` を実行 |
