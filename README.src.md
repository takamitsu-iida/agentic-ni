# ソースコード解説

`src/agentic_ni/` パッケージの構成・各モジュールの役割・データフローを説明します。

<br>

---

## ディレクトリ構成

```
src/agentic_ni/
├── __init__.py            # パッケージ初期化（空）
├── state.py               # LangGraph 共有ステート定義
├── llm.py                 # LLM ファクトリー（プロバイダー切替）
├── graph.py               # グラフ組み立て・CLI エントリポイント
│
├── agents/                # エージェント（LLM を呼び出すコアロジック）
│   ├── __init__.py
│   ├── architect.py       # 設計エージェント
│   ├── validator.py       # 検証エージェント
│   ├── fault_simulator.py # 障害シミュレーションエージェント
│   ├── troubleshooter.py  # トラブルシューティングエージェント
│   └── analyzer.py        # 設計分析・改善エージェント
│
├── tools/                 # 外部システム操作ツール（CML / pyATS / RAG）
│   ├── __init__.py
│   ├── cml_tools.py       # Cisco CML 操作（virl2_client ラッパー）
│   ├── pyats_tools.py     # ネットワーク検証（pyATS/Genie ラッパー）
│   └── rag_tools.py       # ベクトル知識ベース（ChromaDB ラッパー）
│
└── prompts/               # エージェント用プロンプトファイル
    ├── architect_system.md
    ├── validator_system.md
    ├── fault_simulator_system.md
    ├── troubleshooter_system.md
    ├── analyzer_system.md
    ├── demo/              # デモ用プロンプトセット
    ├── demo2/             # 障害シミュレーション用プロンプトセット
    └── static/            # 静的サンプルセット
```

<br>

---

## 依存関係

モジュール間の呼び出し関係を示します。

```
graph.py
  ├── state.py           (AgentState 型定義)
  ├── agents/architect.py
  │     ├── llm.py
  │     └── tools/rag_tools.py (知識ベース検索)
  ├── agents/validator.py
  │     ├── llm.py
  │     ├── tools/cml_tools.py (CML デプロイ)
  │     └── tools/pyats_tools.py (テスト実行)
  ├── agents/fault_simulator.py
  │     ├── llm.py
  │     ├── tools/cml_tools.py (リンク断/復旧)
  │     └── tools/pyats_tools.py (テスト再実行)
  ├── agents/troubleshooter.py
  │     ├── llm.py
  │     ├── tools/cml_tools.py (ノード一覧取得)
  │     ├── tools/pyats_tools.py (状態収集・修正投入)
  │     └── tools/rag_tools.py (知識ベース検索)
  └── agents/analyzer.py
        ├── llm.py
        └── tools/rag_tools.py (知識ベース検索)
```

<br>

---

## 各モジュールの解説

### `state.py` — 共有ステート定義

**役割**: LangGraph グラフ内でエージェント間のデータを受け渡す `AgentState` TypedDict を定義します。

グラフの各ノードは `state: AgentState` を受け取り、更新差分を `dict` で返します。LangGraph がそれをマージして次のノードへ渡します。

```python
class AgentState(TypedDict):
    # 入力
    requirement: str           # 自然言語の要件
    prompt_set: str            # prompts/<set>/ のサブディレクトリ名

    # 設計エージェント出力
    topology_yaml: str         # CML 向けトポロジー YAML
    device_configs: dict       # {"R1": "hostname R1\n...", "R2": ...}

    # 検証エージェント出力
    lab_id: str                # デプロイ済み CML ラボ ID
    test_results: list         # [{"test": "ping", "result": "PASS", "detail": "..."}]
    error_log: str             # 失敗原因の推論（設計エージェントへのフィードバック）

    # ループ管理
    retry_count: int           # 現在の試行回数

    # Phase B: 障害シミュレーション
    fault_simulation_enabled: bool
    fault_scenario_results: list[FaultScenarioResult]

    # Phase H: トラブルシューティング
    troubleshoot_lab_id: str
    collected_state: dict      # {デバイス名: {running_config, show_outputs}}
    diagnosis: str
    fix_records: list[TroubleshootFixRecord]

    # Phase E: 分析・改善
    analyze_request: str       # 改善要求テキスト
    analysis_result: str       # 分析・改善レポート

    final_report: str          # 最終 Markdown レポート
```

**補助 TypedDict**:

| クラス | 用途 |
|---|---|
| `TestResult` | `test_results` リストの各要素 (`test`, `result`, `detail`) |
| `FaultScenarioResult` | 障害シミュレーション 1 シナリオ分の結果 |
| `TroubleshootFixRecord` | トラブルシューティングで適用した修正コマンドの記録 |

---

### `llm.py` — LLM ファクトリー

**役割**: `LLM_PROVIDER` 環境変数に応じて LLM インスタンスを生成して返します。

各エージェントは `get_llm()` を呼ぶだけでよく、プロバイダー変更時にエージェント側のコードは変更不要です。

```python
def get_llm() -> BaseChatModel:
    provider = os.getenv("LLM_PROVIDER", "openai").lower()
    # "openai"    → ChatOpenAI(model=OPENAI_MODEL)
    # "anthropic" → ChatAnthropic(model=ANTHROPIC_MODEL)
    # "ollama"    → ChatOllama(base_url=OLLAMA_BASE_URL, model=OLLAMA_MODEL)
```

**構造化出力との組み合わせ**:

```python
# 各エージェントでの典型的な使い方
llm = get_llm()
structured_llm = llm.with_structured_output(DesignOutput, method="function_calling")
result: DesignOutput = structured_llm.invoke(messages)
```

`with_structured_output()` により LLM の出力を Pydantic モデルとして受け取ります。`method="function_calling"` は Function Calling API を使うため、JSON Schema に対する出力精度が高くなります。

---

### `graph.py` — グラフ組み立て・CLI

**役割**: LangGraph の `StateGraph` にノードと条件分岐を登録し、実行可能なグラフを構築します。また `main()` 関数が CLI エントリポイントです。

#### グラフのモード

モードごとに専用のグラフが存在します。

| 関数 | モード | フロー |
|---|---|---|
| `compile_graph()` | 通常（設計・検証ループ） | `architect → validator →` 条件分岐 |
| `compile_graph_dry_run()` | ドライラン | `architect → dry_run → END` |
| `compile_graph_troubleshoot()` | トラブルシューティング | `collect → diagnose → fix → verify →` 条件分岐 |
| `compile_graph_analyze()` | 設計分析 | `collect → analyze → report → END` |
| `compile_graph_improve()` | 設計改善 | `collect → improve → save → END` |

#### 通常モードの条件分岐

```
validator_node
    ↓
should_continue()
    ├── "complete"  → report_node → (_should_run_fault_sim)
    │                    ├── "fault_simulate" → fault_simulate_node → fault_report_node
    │                    └── "done" → (human_review_node →) END
    ├── "redesign"  → architect_node  ← ループ
    └── "escalate"  → escalate_node → END
```

`should_continue()` の判定ロジック:

```python
def should_continue(state):
    if all(r["result"] == "PASS" for r in test_results):
        return "complete"
    if all("テスト実行エラー" in r["detail"] for r in test_results):
        return "escalate"   # ツールエラー（設計の問題ではない）
    if error_log.startswith("デプロイ失敗:"):
        return "escalate"   # CML デプロイ失敗
    if retry_count >= MAX_RETRIES:
        return "escalate"   # 上限超過
    return "redesign"       # 設計エージェントへ差し戻し
```

#### Phase D: 設計ドキュメント生成

`report_node` と `dry_run_node` から呼ばれる `_generate_design_docs(state, out_dir)` が以下の 5 ファイルを `configs/<prompt_set>/` に保存します:

```python
def _parse_ip_ledger(device_configs)  # regex で interface/ip address を抽出
def _parse_routing_config(device_configs)  # regex で router ospf/bgp を抽出
def _generate_design_docs(state, out_dir)  # 上記を呼び出してファイルを保存
```

#### ファクトリー関数

各モードの初期ステートを生成するファクトリー:

| 関数 | 用途 |
|---|---|
| `initial_state(requirement, prompt_set, ...)` | 通常・ドライラン・障害シミュレーション |
| `initial_state_troubleshoot(lab_id, issue, prompt_set)` | トラブルシューティング |
| `initial_state_analyze(lab_id, prompt_set)` | 設計分析 |
| `initial_state_improve(lab_id, analyze_request, prompt_set)` | 設計改善 |

---

### `agents/architect.py` — 設計エージェント

**役割**: 要件テキストまたはエラーログから CML トポロジー YAML と各機器コンフィグを LLM で生成します。

#### Pydantic スキーマ

```python
class DeviceConfig(BaseModel):
    device_name: str      # ノードの label と一致する名前（例: "R1"）
    config_text: str      # IOS 形式のコンフィグテキスト

class DesignOutput(BaseModel):
    topology_yaml: str           # CML 用トポロジー YAML
    device_configs: list[DeviceConfig]
    design_rationale: str        # 設計意図の説明（ログ用）
```

#### プロンプト構築

```
prompts/architect_system.md       (共通システムプロンプト: 役割・YAML 形式・出力ルール)
  +
prompts/<prompt_set>/architect.md (セット固有ヒント: IP 仕様・プロトコル固有設定)
  +
prompts/<prompt_set>/requirement.md (要件テキスト)
  +
error_log (前回の失敗ログ: 差し戻し時のみ)
  +
RAG 知識ベース (インデックス済みなら関連チャンクを自動付与)
```

#### 動作分岐

```python
# error_log が空 → ゼロ設計
# error_log に内容あり → 差分修正（コスト最適化のため全体再設計はしない）
```

---

### `agents/validator.py` — 検証エージェント

**役割**: CML へデプロイし、テスト計画を自律立案・実行して結果を返します。

#### Pydantic スキーマ

```python
class TestItem(BaseModel):
    test_type: Literal[
        "ospf_neighbors", "bgp_summary", "ping",
        "vlan_interfaces", "route_table", "interface_status",
        "traceroute", "bgp_path"
    ]
    device: str        # テスト実行デバイス名
    target: str | None # ping の宛先 IP、route_table のプレフィックス等
    description: str   # テスト目的の説明

class TestPlan(BaseModel):
    tests: list[TestItem]
    rationale: str

class FailureAnalysis(BaseModel):
    root_cause: str    # 失敗原因の推論
    affected_devices: list[str]
    suggested_fix: str # 修正方針
```

#### 処理フロー（`validator.run(state)` 内）

```
1. CML にラボをデプロイ（deploy_lab）
   ↓
2. LLM がテスト計画を立案（TestPlan）
   ↓
3. テスト計画に従い pyATS でテスト実行（_execute_test）
   ↓
4a. 全 PASS → test_results 更新、error_log を空に
4b. FAIL あり → LLM が失敗原因を推論（FailureAnalysis）→ error_log に格納
```

#### テスト実行のディスパッチ

```python
def _execute_test(item: TestItem, testbed_yaml: str) -> TestResult:
    match item.test_type:
        case "ospf_neighbors":  return _run_ospf(...)
        case "bgp_summary":     return _run_bgp(...)
        case "ping":            return _run_ping(...)
        case "route_table":     return _run_route_table(...)
        # ...
```

---

### `agents/fault_simulator.py` — 障害シミュレーションエージェント

**役割**: 全テスト PASS 後に CML のリンクを順番に断・復旧させて冗長性を検証します。

#### Pydantic スキーマ

```python
class FaultScenario(BaseModel):
    link_id: str                              # CML リンク ID
    link_label: str                           # 表示名（例: "R1 <-> R2"）
    scenario_name: str                        # シナリオ説明
    wait_seconds: int                         # 収束待機秒数（デフォルト: 15）
    expected_ospf_neighbors: dict[str, int]   # {デバイス名: 期待ネイバー数}

class FaultPlan(BaseModel):
    scenarios: list[FaultScenario]
    rationale: str
```

#### 処理フロー（各シナリオ）

```
1. LLM がシナリオ計画（FaultPlan）を立案
   ↓ 各シナリオで:
2. cml_tools.set_link_state(link_id, up=False)  ← リンク断
3. wait_seconds 待機
4. テスト実行（障害中）: expected_ospf_neighbors で完全一致チェック
5. cml_tools.set_link_state(link_id, up=True)   ← 復旧
6. wait_seconds 待機
7. テスト実行（復旧後）: 元のテスト計画を再実行
```

`expected_ospf_neighbors` を使うことで、障害中に「ネイバー数が正確に 1 であること」など意図した冗長経路への切り替わりを厳密に検証できます。

---

### `agents/troubleshooter.py` — トラブルシューティングエージェント

**役割**: 既存の稼働中ラボに接続し、診断 → 修正 → 検証のサイクルで問題を自動解決します。

#### Pydantic スキーマ

```python
class DiagnosisResult(BaseModel):
    root_cause: str
    affected_devices: list[str]
    severity: Literal["config_error", "topology_error", "timing_issue", "unknown"]
    summary: str

class FixCommand(BaseModel):
    device: str            # 修正対象デバイス
    commands: str          # configure terminal コマンド列
    rollback_commands: str # ロールバック用 no コマンド
    description: str

class FixPlan(BaseModel):
    fixes: list[FixCommand]
    rationale: str
```

#### 3 つのエージェント関数

```python
def run_collect(state)  # CML からノード一覧取得 → pyATS で running-config と show コマンドを収集
def run_diagnose(state) # collected_state を LLM に投入 → DiagnosisResult を返す
def run_fix(state)      # diagnosis を LLM に投入 → FixPlan を生成 → pyATS で差分投入
```

`run_fix` は `configure terminal` による差分投入のみ。wipe・再起動は行いません（既存サービスへの影響を最小化）。

---

### `agents/analyzer.py` — 設計分析・改善エージェント

**役割**: 既存ラボの設計品質を評価（`--analyze`）または改善コンフィグを生成（`--improve`）します。

#### Pydantic スキーマ

```python
class AnalysisIssue(BaseModel):
    severity: Literal["critical", "warning", "info"]
    device: str         # "all" はトポロジー全体の問題を示す
    description: str
    recommendation: str

class AnalysisResult(BaseModel):
    overall_rating: Literal["good", "acceptable", "needs_improvement", "critical"]
    summary: str
    issues: list[AnalysisIssue]
    improvement_suggestions: list[str]

class ImprovementOutput(BaseModel):
    device_configs: dict[str, str]  # {デバイス名: 改善後の完全コンフィグ}
    changes_summary: list[str]
    rationale: str
```

#### troubleshooter との違い

| 観点 | troubleshooter | analyzer |
|---|---|---|
| 目的 | 障害を自動修正する | 設計品質を評価 / 改善コンフィグを生成 |
| CML への変更 | `configure terminal` で投入 | なし（analyze）/ ファイル保存のみ（improve）|
| ループ | 最大 3 回リトライ | 1 パス |

---

### `tools/cml_tools.py` — CML 操作

**役割**: `virl2_client` を薄くラップし、CML ラボのライフサイクル管理と障害シミュレーションを提供します。

#### 主要関数

| 関数 | 説明 |
|---|---|
| `deploy_lab(topology_yaml, device_configs, ...)` | インポート・コンフィグ投入・起動・収束待ちを一括実行 |
| `update_configs_and_restart(lab_id, device_configs)` | 既存ラボのコンフィグを差し替えて再起動 |
| `delete_lab(lab_id)` | stop → wipe → remove の順でラボを削除 |
| `set_link_state(lab_id, link_id, up)` | 両端インターフェースを shutdown/bring_up（障害シミュレーション） |
| `get_lab_nodes(lab_id)` | ノード一覧と起動状態を返す |
| `get_lab_links(lab_id)` | リンク一覧（接続デバイス・インターフェース名）を返す |
| `find_lab_by_title(title)` | タイトルでラボ ID を検索（`--troubleshoot` の自動検索に使用） |
| `export_lab_configs(lab_id)` | CML から Day-0 コンフィグを取得（pyATS 不要） |
| `export_lab_topology(lab_id)` | トポロジー YAML をエクスポート |

#### `_patch_topology_yaml()` について

LLM が生成したトポロジー YAML には CML 2.10 の制約（`lab.version` 必須、`links[].label` 空文字不可、Loopback インターフェースを topology に含めてはならない）に違反する場合があります。`deploy_lab` 内部でこの関数が自動補正します。

```python
def _patch_topology_yaml(topology_yaml: str) -> str:
    # lab.version → "0.1.0" を補完
    # links[].label が空 → link.id で補完
    # type=loopback または slot<0 のインターフェースを除去
```

---

### `tools/pyats_tools.py` — ネットワーク検証

**役割**: pyATS/Genie を使って機器への接続・コマンド実行・結果パースを行います。

#### testbed YAML の動的生成

pyATS は接続先を `testbed.yaml` で定義します。`build_testbed(lab_id, device_configs)` が CML のコンソール接続情報から動的に生成します（ファイルを生成せずに文字列として返します）。

```python
testbed_yaml = pyats_tools.build_testbed(lab_id, device_configs)
# → "testbed:\n  name: ...\n  devices:\n    R1:\n      connections:\n ..."
```

#### 主要検証関数

| 関数 | 対応 test_type | 説明 |
|---|---|---|
| `check_ospf_neighbors` | `ospf_neighbors` | `show ip ospf neighbor` で FULL 状態のネイバー数を確認 |
| `check_bgp_summary` | `bgp_summary` | `show ip bgp summary` でセッション状態を確認 |
| `check_ping` | `ping` | `ping <target>` の成功/失敗 |
| `check_vlan_interfaces` | `vlan_interfaces` | VLAN・インターフェース up/up 状態 |
| `check_route_table` | `route_table` | `show ip route` で指定プレフィックスの有無を確認 |
| `check_interface_status` | `interface_status` | `show interface` で up/up 状態を確認 |
| `check_traceroute` | `traceroute` | 経路が指定ホップを経由しているか確認 |
| `check_bgp_path` | `bgp_path` | BGP best path の確認 |
| `collect_device_state` | — | running-config + 複数 show コマンドをまとめて収集 |
| `apply_incremental_config` | — | `configure terminal` で差分コンフィグを投入 |

#### `show ip route` の CIDR 変換

IOS-XE は `show ip route 1.1.1.1/32` を受け付けないため、`check_route_table` は内部で変換します:

```python
# "1.1.1.1/32" → "show ip route 1.1.1.1 255.255.255.255"
# "10.0.0.0/8"  → "show ip route 10.0.0.0 255.0.0.0"
# "1.1.1.1"     → "show ip route 1.1.1.1"（そのまま）
```

---

### `tools/rag_tools.py` — 知識ベース RAG

**役割**: `rag/` ディレクトリのテキストファイルを ChromaDB に登録し、設計・診断プロンプトへの自動付与を行います。

#### ChromaDB コレクション

| コレクション | 用途 |
|---|---|
| `knowledge_base` | `--rag-index` で登録したドキュメント（現在唯一の有効コレクション） |

#### チャンク分割

```
1 ファイル → 1000 文字ごとにチャンク分割（150 文字オーバーラップ）
```

#### 検索と付与

```python
knowledge = rag_tools.search_knowledge(query, k=3)
# → 要件テキストとコサイン距離 < 0.8 の上位 3 チャンクを返す
# → エージェントがプロンプトに追記する
```

#### SQLite バージョン対応

Ubuntu 20.04 など古い環境では `sqlite3 < 3.35.0` のため ChromaDB が起動できません。`_get_client()` 内で `pysqlite3-binary` を自動検出してモンキーパッチします:

```python
try:
    import pysqlite3
    sys.modules["sqlite3"] = pysqlite3  # ChromaDB が使う sqlite3 を差し替え
except ImportError:
    pass  # 未インストール時はシステムの sqlite3 を使用
```

<br>

---

## データフロー

### 通常モード（`compile_graph`）

```
initial_state(requirement, prompt_set)
        │
        ▼
architect_node ─── architect.run(state)
        │           ├── LLM に DesignOutput を生成させる
        │           └── state.topology_yaml, state.device_configs を更新
        │
        ▼
validator_node ─── validator.run(state)
        │           ├── cml_tools.deploy_lab() で CML に展開
        │           ├── LLM に TestPlan を生成させる
        │           ├── pyats_tools.* でテストを実行
        │           └── 全 PASS → state.test_results 更新
        │               FAIL → state.error_log に失敗原因推論を格納
        │
        ▼
 should_continue()
  ├── "complete" ──────────────────────── report_node
  │                                           ├── 最終レポート生成
  │                                           └── _generate_design_docs() でファイル保存
  ├── "redesign" ──── architect_node (ループ)
  └── "escalate" ──── escalate_node
```

### トラブルシューティングモード（`compile_graph_troubleshoot`）

```
initial_state_troubleshoot(lab_id, issue)
        │
        ▼
troubleshoot_collect_node ─── troubleshooter.run_collect()
        │   全機器の running-config と show コマンド出力を収集
        │   → state.collected_state, state.device_configs を更新
        ▼
troubleshoot_diagnose_node ─── troubleshooter.run_diagnose()
        │   collected_state を LLM で分析 → DiagnosisResult
        │   → state.diagnosis を更新
        ▼
troubleshoot_fix_node ─── troubleshooter.run_fix()
        │   diagnosis を元に FixPlan を生成 → pyATS で差分投入
        │   → state.fix_records に追記、state.troubleshoot_retry_count++
        ▼
troubleshoot_verify_node
        │   LLM に TestPlan を立案させ pyATS でテスト実行
        │
        ▼
 should_continue_troubleshoot()
  ├── "complete" ── troubleshoot_report_node → END
  ├── "retry"    ── troubleshoot_collect_node (最大 3 回)
  └── "escalate" ── escalate_node → END
```

<br>

---

## Pydantic スキーマ一覧

LLM の構造化出力（`with_structured_output()`）に使われるスキーマをまとめます。

| エージェント | 入力スキーマ | 説明 |
|---|---|---|
| `architect` | `DesignOutput` | `topology_yaml` + `device_configs` + `design_rationale` |
| `validator` | `TestPlan` | テスト計画（`TestItem` のリスト） |
| `validator` | `FailureAnalysis` | 失敗原因推論（`root_cause`, `suggested_fix`）|
| `fault_simulator` | `FaultPlan` | 障害シナリオ計画（`FaultScenario` のリスト） |
| `troubleshooter` | `DiagnosisResult` | 根本原因・影響デバイス・重大度 |
| `troubleshooter` | `FixPlan` | 修正コマンド計画（`FixCommand` のリスト） |
| `analyzer` | `AnalysisResult` | 評価・問題リスト・改善提案 |
| `analyzer` | `ImprovementOutput` | 改善後コンフィグ・変更サマリー |

<br>

---

## プロンプトの構築方式

各エージェントのプロンプトは「共通システムプロンプト ＋ セット固有プロンプト」の 2 段構成です。

```
architect_system.md   (共通: 役割・YAML 仕様・禁止事項)
  +
prompts/<set>/architect.md  (セット固有: IP 仕様・プロトコルヒント)
  +
prompts/<set>/requirement.md (要件テキスト)
  +
error_log (前回失敗ログ: 差し戻し時のみ)
  +
RAG 知識ベース (インデックス済みの場合のみ関連チャンクを自動付与)
```

`validator`, `fault_simulator`, `troubleshooter`, `analyzer` も同様に `<agent>_system.md` と `prompts/<set>/<agent>.md` を組み合わせます（セット固有ファイルが存在しない場合は共通のみ使用）。

<br>

---

## テスト構成

```
tests/
├── test_llm.py            # get_llm() のプロバイダー切替テスト
├── test_graph.py          # グラフノード・条件分岐・初期ステート生成のテスト
├── test_architect.py      # 設計エージェントのユニットテスト（LLM モック）
├── test_validator.py      # 検証エージェントのユニットテスト（LLM/CML/pyATS モック）
├── test_fault_simulator.py# 障害シミュレーションエージェントのテスト
├── test_troubleshooter.py # トラブルシューターエージェントのテスト
├── test_analyzer.py       # 設計分析・改善エージェントのテスト
├── test_design_docs.py    # Phase D ドキュメント生成のテスト（ファイルI/O含む）
├── test_cml_tools.py      # CML ツールのテスト（virl2_client モック）
├── test_pyats_tools.py    # pyATS ツールのテスト（pyATS モック）
├── test_rag_knowledge.py  # 知識ベース RAG のテスト
└── test_e2e.py            # エンドツーエンドのシナリオテスト（全モジュールモック）
```

すべてのテストは外部依存（LLM API / CML / pyATS）をモックするため、CI 環境でも実行できます。

```bash
# 全ユニットテストを実行（E2E 除く）
pytest tests/ --ignore=tests/test_e2e.py -v

# E2E テストを含めて全実行
pytest tests/ -v
```
