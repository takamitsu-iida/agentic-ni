# 自律分散型トラブルシューティングエージェント 実装計画

概念設計書（`concept_design.md`）を既存の `agentic-ni` コードベース上に段階的に実装するための計画書。

---

## 進捗サマリー

| フェーズ | タイトル | 状態 | 完了タスク |
|---------|---------|------|-----------|
| TS-1 | Message Bus 抽象化レイヤー | ✅ 完了 | 4 / 4 |
| TS-2 | DeviceAgent コア実装 | ✅ 完了 | 4 / 4 |
| TS-3 | Device Tools 実装 | ✅ 完了 | 3 / 3 |
| TS-4 | Orchestrator 実装 | ✅ 完了 | 4 / 4 |
| TS-5 | プロンプト設計と E2E 検証 | ✅ 完了 | 3 / 3 |

> 凡例: 🔲 未着手 / 🔄 進行中 / ✅ 完了

---

## 現状コードとのギャップ分析

| 観点 | 現状 | 概念設計 | 差分 |
|------|------|----------|------|
| アーキテクチャ | 中央集権型 LangGraph（逐次ノード実行） | 分散型（装置ごとの独立エージェント） | **大** |
| エージェント数 | 役割別 5 エージェント（architect/validator 等） | 装置別 N エージェント | **大** |
| 通信手段 | LangGraph State（インメモリ共有） | Message Bus (MQTT/NATS/Redis) | **大** |
| 起動トリガー | CLI 実行（人間が開始） | syslog/アラート等の自律トリガー | **中** |
| ツール権限 | 全コマンド可（設計・デプロイ含む） | 初期は Read-Only、変更は Human 承認 | **小**（既存 HITL 活用可） |
| ローカルメモリ | AgentState（グラフ全体で共有） | 装置ごとのローカルメモリ | **中** |

---

## 実装方針

既存コードを壊さず、`trouble_shooting/` 配下に**新モジュールとして追加**する。
段階的に動作確認できるよう Phase を分割する。

```
src/agentic_ni/
└── distributed/          ← 新規パッケージ（今回の実装対象）
    ├── __init__.py
    ├── bus.py            # Message Bus 抽象化レイヤー
    ├── device_agent.py   # 装置エージェント本体
    ├── device_tools.py   # SSH/RESTCONF/SNMP ツール群
    ├── memory.py         # エージェントローカルメモリ
    ├── message.py        # メッセージスキーマ定義
    ├── orchestrator.py   # 複数エージェントの起動・管理
    └── prompts.py        # 装置別プロンプトビルダー

prompts/
└── device_agent_system.md  ← 装置エージェント共通システムプロンプト

tests/
├── test_distributed_bus.py
├── test_distributed_device_agent.py
└── test_distributed_e2e.py
```

---

## フェーズ計画

### Phase TS-1: Message Bus 抽象化レイヤー　✅ 完了

**目標**: MQTT / NATS / Redis のいずれにも切り替え可能な Pub/Sub インターフェースを実装する。

**実装ファイル**: `src/agentic_ni/distributed/bus.py`, `message.py`

#### タスク

- [x] 1. **メッセージスキーマ定義** (`message.py`)
   ```python
   class AgentMessage(BaseModel):
       from_agent: str       # 送信元 Agent ID（例: "Agent-R1"）
       to_agent: str         # 宛先 Agent ID、"ALL" でブロードキャスト
       msg_type: Literal["query", "response", "alert", "report"]
       content: str          # 自然言語または JSON テキスト
       payload: dict         # 構造化データ（診断結果等）
       timestamp: datetime
       message_id: str       # UUID
   ```

- [x] 2. **Bus 抽象基底クラス** (`bus.py`)
   ```python
   class MessageBus(ABC):
       @abstractmethod
       async def publish(self, topic: str, message: AgentMessage) -> None: ...
       @abstractmethod
       async def subscribe(self, topic: str, handler: Callable) -> None: ...
       @abstractmethod
       async def close(self) -> None: ...
   ```

- [x] 3. **バックエンド実装**
   - `InMemoryBus`: テスト・開発用（依存なし）
   - `MQTTBus`: `paho-mqtt` を使用（本番向け）
   - `NATSBus`: `nats-py` を使用（高スループット向け）

- [x] 4. **トピック命名規則**
   - `network/agents/chat` — 全員参加のブロードキャスト
   - `network/agents/{agent_id}/direct` — 1対1 ダイレクトメッセージ

**完了条件**: `InMemoryBus` を使い、2エージェント間でメッセージ送受信できること。
**依存追加**: `paho-mqtt>=2.0`, `nats-py>=2.0`（`pyproject.toml` に `[distributed]` extras として追加）

---

### Phase TS-2: DeviceAgent コア実装　✅ 完了

**目標**: 1装置 = 1エージェントの独立した AI エージェントを実装する。

**実装ファイル**: `src/agentic_ni/distributed/device_agent.py`, `prompts.py`, `memory.py`

#### タスク

- [x] 1. **装置別プロンプトビルダー** (`prompts.py`)
   - 既存の `agents/prompts.py` の `load_agent_prompt()` を参照しつつ、装置名・隣接情報を埋め込む
   - テンプレート変数: `{device_name}`, `{device_type}`, `{neighbors}`, `{management_ip}`
   - ベースプロンプトファイル: `prompts/device_agent_system.md` として新規作成

- [x] 2. **ローカルメモリ** (`memory.py`)
   ```python
   class DeviceMemory:
       device_name: str
       status_history: deque[StatusSnapshot]  # 直近1時間のステータス
       neighbor_map: dict[str, NeighborInfo]  # トポロジーマップ（隣接装置）
       active_incidents: list[Incident]        # 進行中インシデント
   ```

- [x] 3. **DeviceAgent クラス** (`device_agent.py`)
   - `asyncio` ベースの非同期ループで動作
   - 外部トリガーの受付: syslog メッセージ, バス経由メッセージ, ポーリング
   - LLM 呼び出し: 既存 `llm.py` の `get_llm()` を再利用
   - ツール呼び出し: `device_tools.py` の関数群をバインド
   - 出力パーサー: `"TO: [target] | MSG: [content]"` フォーマットを解析しバスに送信

- [x] 4. **エージェントの動作ループ**
   ```
   ┌─────────────────────────────────────┐
   │  EventQueue（syslog / バスメッセージ）  │
   └─────────────┬───────────────────────┘
                 │ event
                 ▼
   ┌─────────────────────────────────────┐
   │  LLM 推論（ツール呼び出し含む）       │
   │  ReAct / Tool Calling ループ         │
   └─────────────┬───────────────────────┘
                 │ 出力
                 ▼
   ┌────────────────────────────────────────┐
   │  OutputRouter                          │
   │  ├─ TO: [AgentID] → バスに Publish    │
   │  ├─ TO: HUMAN     → 承認キューに積む  │
   │  └─ TO: LOG       → ローカルメモリ更新│
   └────────────────────────────────────────┘
   ```

**完了条件**: `InMemoryBus` 上で DeviceAgent 1台が syslog テキストを受け取り、LLM が自装置調査 → 他エージェントへ調査依頼メッセージを生成できること。

---

### Phase TS-3: Device Tools 実装　✅ 完了

**目標**: 装置へのアクセスツール（Read-Only 限定）を実装する。

**実装ファイル**: `src/agentic_ni/distributed/device_tools.py`

#### タスク

- [x] 1. **SSH ツール（Read-Only）**
   既存 `tools/pyats_tools.py` の `run_show_commands()` を再利用・ラップ
   - `get_running_config(device_name)` — running-config 取得
   - `run_show(device_name, command)` — 任意の show コマンド
   - `get_interface_status(device_name)` — インターフェース状態一覧
   - `get_routing_table(device_name)` — ルーティングテーブル

- [x] 2. **Write ツール（Human 承認ゲート付き）**
   既存 `graph.py` の `interrupt()` 機構を参考に実装
   - `apply_config(device_name, commands)` — 設定投入（承認待ちキューに積む）
   - 承認なし呼び出しは `PermissionError` を返す（ガードレール）

- [x] 3. **ツール設定フラグ**
   環境変数 `DEVICE_AGENT_READONLY=true` でWrite ツールを完全無効化（デフォルト `true`）

**完了条件**: Read-Only ツール群が pyATS 経由で実際の show コマンドを実行できること。

---

### Phase TS-4: Orchestrator 実装　✅ 完了

**目標**: `topology.yaml` を読み込み、全装置エージェントを自動起動・管理するオーケストレーターを実装する。

**実装ファイル**: `src/agentic_ni/distributed/orchestrator.py`

#### タスク

- [x] 1. **トポロジー読み込み**
   既存の `configs/<name>/topology.yaml` を解析し、各ノードの装置情報・隣接情報を抽出

- [x] 2. **エージェント起動・停止**
   ```python
   class AgentOrchestrator:
       async def start(self, topology_path: str) -> None:
           # topology_yaml を読み込み DeviceAgent を生成・起動
       async def stop_all(self) -> None:
           # 全エージェントにシャットダウン指示
       async def get_agent(self, agent_id: str) -> DeviceAgent: ...
   ```

- [x] 3. **Human 承認ワークフロー**
   既存 `graph.py` の `interrupt()` / `human_review_node` を流用
   - 承認キューを `asyncio.Queue` で実装
   - CLI (`--approve`) または将来的に Slack/Teams からのコールバックで承認

- [x] 4. **エントリポイント追加**
   `pyproject.toml` に `agentic-ni-distributed` スクリプトを追加
   ```toml
   agentic-ni-ts = "agentic_ni.distributed.orchestrator:main"
   ```

**完了条件**: `agentic-ni-ts --topology configs/demo/topology.yaml` で全装置エージェントが起動し、バスを経由してメッセージを交換できること。

---

### Phase TS-5: プロンプト設計と E2E 検証　✅ 完了

**目標**: 障害シナリオでの End-to-End 動作確認。

#### タスク

- [x] 1. **システムプロンプト作成** (`prompts/device_agent_system.md`)
   概念設計書セクション 4 のコアイメージを正式なプロンプトに拡張
   - 役割・行動指針の定義
   - 出力フォーマット制約（`TO: ... | MSG: ...`）
   - ツール利用ポリシー（Read-Only 制約の明文化）
   - エスカレーション条件（人間への報告タイミング）

- [x] 2. **E2E テストシナリオ**
   既存の CML ラボ（`demo` 構成）を使用:
   - シナリオA: R1-R2 間リンク断 → Agent-R1 が検知 → Agent-R2 に問い合わせ → 根本原因特定
   - シナリオB: OSPF ネイバー消失 → 複数エージェントの協調調査
   - テストファイル: `tests/test_distributed_e2e.py`

- [x] 3. **レポート生成**
   既存 `reports/` ディレクトリに分散トラブルシューティングレポートを出力
   （既存フォーマットと統一）

**完了条件**: CML ラボ上でリンク断を発生させ、エージェント間対話ログと最終診断レポートが生成されること。

---

## ファイル変更・追加一覧

### 新規作成

| ファイル | 説明 |
|----------|------|
| `src/agentic_ni/distributed/__init__.py` | パッケージ初期化 |
| `src/agentic_ni/distributed/message.py` | `AgentMessage` スキーマ |
| `src/agentic_ni/distributed/bus.py` | Message Bus 抽象化・実装 |
| `src/agentic_ni/distributed/memory.py` | `DeviceMemory` クラス |
| `src/agentic_ni/distributed/device_tools.py` | Read-Only + Gated Write ツール |
| `src/agentic_ni/distributed/device_agent.py` | `DeviceAgent` コアクラス |
| `src/agentic_ni/distributed/prompts.py` | 装置別プロンプトビルダー |
| `src/agentic_ni/distributed/orchestrator.py` | `AgentOrchestrator` + `main()` |
| `prompts/device_agent_system.md` | 装置エージェント共通システムプロンプト |
| `tests/test_distributed_bus.py` | Bus レイヤーのユニットテスト |
| `tests/test_distributed_device_agent.py` | DeviceAgent のユニットテスト |
| `tests/test_distributed_e2e.py` | E2E 統合テスト |

### 変更

| ファイル | 変更内容 |
|----------|----------|
| `pyproject.toml` | `[distributed]` extras 追加（`paho-mqtt`, `nats-py`）、`agentic-ni-ts` スクリプト追加 |
| `requirements.txt` | 上記に対応 |

### 変更なし（既存資産の再利用）

| ファイル | 再利用箇所 |
|----------|-----------|
| `src/agentic_ni/llm.py` | `get_llm()` をそのまま使用 |
| `src/agentic_ni/tools/pyats_tools.py` | Read-Only ツールの内部実装として再利用 |
| `src/agentic_ni/logger.py` | `get_logger()` をそのまま使用 |
| `configs/*/topology.yaml` | オーケストレーターがそのまま読み込む |

---

## 依存関係の追加

```toml
# pyproject.toml に追加
[project.optional-dependencies]
distributed = [
    "paho-mqtt>=2.0",   # MQTT バックエンド
    "nats-py>=2.3",     # NATS バックエンド
]
```

開発時は InMemoryBus を使うため、`distributed` extras なしで全テストが通ること。

---

## 実装順序とマイルストーン

```
Week 1:  Phase TS-1 (bus.py, message.py) + ユニットテスト
Week 2:  Phase TS-2 (device_agent.py, memory.py, prompts.py)
Week 3:  Phase TS-3 (device_tools.py) + Read-Only 動作確認
Week 4:  Phase TS-4 (orchestrator.py) + トポロジー読み込み
Week 5:  Phase TS-5 (プロンプト・E2E テスト) + レポート生成
```

---

## リスクと対策

| リスク | 対策 |
|--------|------|
| LLM のレスポンスが `TO: X \| MSG: Y` フォーマットに従わない | 出力パーサーにフォールバック（全文を HUMAN 宛とみなす）を実装 |
| 複数エージェントが同時に同一装置を操作する競合 | 装置ごとにセマフォを確保し排他制御 |
| メッセージループ（A→B→A の無限連鎖） | `message_id` チェーンで循環検知し最大ホップ数（デフォルト 5）で打ち切る |
| MQTT/NATS サーバー未整備時の開発効率 | Phase TS-1 で `InMemoryBus` を優先実装し、外部 MQ なしでテスト可能にする |
| pyATS が接続できない環境でのテスト | `device_tools.py` に `MockDeviceTools` を実装しオフライン単体テストを実現 |
