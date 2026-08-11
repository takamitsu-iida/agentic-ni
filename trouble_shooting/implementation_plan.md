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
| Scale-1 | TTL 完全実装 | ✅ 完了 | 4 / 4 |
| Scale-2 | イベントデデュープ | ✅ 完了 | 4 / 4 |
| Refactor-1 | データモデル定義（incident.py） | ✅ 完了 | 3 / 3 |
| Refactor-2 | EventCorrelator（correlator.py） | ✅ 完了 | 3 / 3 |
| Refactor-3 | IncidentCoordinator（coordinator.py） | ✅ 完了 | 4 / 4 |
| Refactor-4 | DeviceAgent Worker 化 | ✅ 完了 | 4 / 4 |
| Refactor-5 | Orchestrator 配線更新 | ✅ 完了 | 3 / 3 |
| Refactor-6 | システムプロンプト更新 | ✅ 完了 | 2 / 2 |
| Refactor-7 | テスト更新 | ✅ 完了 | 5 / 5 |

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

---

## スケーラビリティ強化フェーズ

> **背景**: ノード数増加時の O(N²) メッセージ爆発とアラームストームを防ぐため、TTL 完全実装とイベントデデュープを追加する。

### 現状のギャップ

| 機能 | 状態 | 場所 |
|---|---|---|
| `hop_count` フィールド | ✅ 存在 | `message.py` |
| バスでのホップ数チェック | ⚠️ `>` のバグあり（`>=` が正しい） | `bus.py` |
| 転送時の `hop_count` インクリメント | ❌ 未実装 | `device_agent.py` |
| バス側の `message_id` 重複チェック | ❌ 未実装 | `bus.py` |
| syslog フィンガープリント | ❌ 未実装 | なし |
| エージェント側のデデュープ窓 | ❌ 未実装 | `device_agent.py` |

---

### Phase Scale-1: TTL 完全実装　✅ 完了

**目標**: ホップ数制限を実際に機能させ、転送チェーン全体でホップ数が正しく伝播するようにする。

**実装ファイル**: `src/agentic_ni/distributed/bus.py`, `message.py`, `device_agent.py`

#### タスク

- [x] 1. **`bus.py` のチェック条件修正（バグ修正）**

  `MAX_HOP_COUNT` 丁度のメッセージが通過するバグを修正する。

  ```python
  # 修正前
  if message.hop_count > MAX_HOP_COUNT:
  # 修正後
  if message.hop_count >= MAX_HOP_COUNT:
  ```

- [x] 2. **`message.py` に `origin_message_id` フィールドを追加**

  転送チェーンの根源となる `message_id` を追跡し、ループ検知の精度を上げる。

  ```python
  origin_message_id: str | None = None
  # 転送チェーンの根源 message_id。最初の送信者は None のまま送り、
  # 転送側がここに元の message_id をセットする。
  ```

- [x] 3. **`device_agent.py` で `hop_count` および `origin_message_id` を引き継ぐ**

  `_process_event()` の冒頭で処理中イベントを `self._current_event` として保持し、
  `_route_output()` での送信時に引き継ぐ。

  ```python
  # _process_event() の冒頭
  self._current_event: AgentEvent = event

  # _route_output() 内でのメッセージ生成
  if isinstance(self._current_event, BusMessageEvent):
      hop = self._current_event.message.hop_count + 1
      origin_id = (self._current_event.message.origin_message_id
                   or self._current_event.message.message_id)
  else:  # SyslogEvent / PollEvent は新規発火
      hop = 0
      origin_id = None

  msg = AgentMessage(..., hop_count=hop, origin_message_id=origin_id)
  ```

- [x] 4. **テスト追加** (`tests/test_distributed_bus.py`)

  - `hop_count = MAX_HOP_COUNT - 1` → バスを通過すること
  - `hop_count = MAX_HOP_COUNT` → バスで破棄されること
  - 3エージェント間の転送チェーンで `hop_count` が 0 → 1 → 2 と積み上がること
  - `origin_message_id` が転送チェーン全体で同一の値を持つこと

**完了条件**: N ホップ以上の連鎖をバスが確実に遮断し、その境界値テストが通ること。

---

### Phase Scale-2: イベントデデュープ　✅ 完了

**目標**: 同一障害由来の重複イベントをエージェントが二重処理しないようにする。

**実装ファイル**: `src/agentic_ni/distributed/dedup.py`（新規）, `bus.py`, `device_agent.py`

#### タスク

- [x] 1. **`dedup.py` を新規作成**

  2 つのクラスを実装する。

  **`MessageDeduplicator`**: バスメッセージの `message_id` 重複チェック用。
  ```python
  class MessageDeduplicator:
      """同一 message_id の二重配信を防ぐ TTL 付き重複チェッカー。"""
      def __init__(self, window_seconds: int = 300) -> None: ...
      def is_duplicate(self, message_id: str) -> bool: ...
      def mark_seen(self, message_id: str) -> None: ...
      def cleanup(self) -> None:  # 期限切れエントリを削除（メモリリーク防止）
  ```

  **`SyslogDeduplicator`**: syslog テキストのフィンガープリント重複チェック用。
  ```python
  class SyslogDeduplicator:
      """同一障害由来の syslog を dedup_window 秒以内で重複とみなすチェッカー。"""
      def __init__(self, window_seconds: int = 60) -> None: ...
      def fingerprint(self, raw_text: str) -> str:
          # タイムスタンプ・シーケンス番号・変動カウンターを正規表現で除去し
          # %OSPF-5-ADJCHG のような不変部分をキーとして返す
      def is_duplicate(self, agent_id: str, raw_text: str) -> bool: ...
      def mark_seen(self, agent_id: str, raw_text: str) -> None: ...
      def cleanup(self) -> None:
  ```

  フィンガープリント正規化の例:
  ```
  入力: "*Aug  9 12:34:56.789: %OSPF-5-ADJCHG: Process 1, Nbr 10.0.0.2 on Gi0/0 from FULL to DOWN"
  出力: "%OSPF-5-ADJCHG: Process *, Nbr * on * from FULL to DOWN"
  ```

- [x] 2. **`InMemoryBus` に `MessageDeduplicator` を組み込む** (`bus.py`)

  ```python
  class InMemoryBus(MessageBus):
      def __init__(self) -> None:
          ...
          self._msg_dedup = MessageDeduplicator(window_seconds=300)

      async def publish(self, topic, message):
          if self._msg_dedup.is_duplicate(message.message_id):
              logger.debug("重複 message_id を破棄: %s", message.message_id)
              return
          self._msg_dedup.mark_seen(message.message_id)
          ...
  ```

- [x] 3. **`DeviceAgent.inject_event()` に `SyslogDeduplicator` を組み込む** (`device_agent.py`)

  ```python
  class DeviceAgent:
      def __init__(self, ...):
          ...
          self._syslog_dedup = SyslogDeduplicator(window_seconds=60)

      async def inject_event(self, event: AgentEvent) -> None:
          if isinstance(event, SyslogEvent):
              if self._syslog_dedup.is_duplicate(self.agent_id, event.raw_text):
                  logger.info("[%s] 重複 syslog を破棄: %s", self.agent_id, event.raw_text[:60])
                  return
              self._syslog_dedup.mark_seen(self.agent_id, event.raw_text)
          await self._event_queue.put(event)
  ```

- [x] 4. **テスト追加** (`tests/test_distributed_dedup.py`（新規）)

  - 60 秒窓内に同一フィンガープリントの syslog が来た場合 → 2 件目が破棄されること
  - 61 秒後（窓外）に同一 syslog が来た場合 → 処理されること
  - 異なるフィンガープリント（別障害）→ 両方とも処理されること
  - 同一 `message_id` のバスメッセージ → バスで破棄されること
  - `cleanup()` 呼び出し後に期限切れエントリが消えること

**完了条件**: 同一障害を連続注入しても LLM 呼び出しが 1 回のみになること。

---

### Scale フェーズの実装順序

```
Scale-1-1（バグ修正: bus.py）
    ↓
Scale-1-2（message.py フィールド追加）
    ↓
Scale-1-3（device_agent.py ホップ引き継ぎ）  ←── Scale-2-1（dedup.py 新規作成）と並行可
Scale-2-1
    ↓
Scale-1-4 + Scale-2-2 + Scale-2-3（組み込み）
    ↓
Scale-2-4（テスト）

---

## アーキテクチャ刷新フェーズ（Coordinator + Worker パターン）

> **背景**: DeviceAgent が自律的に並行調査を起動し P2P メッセージで相互に刺激し合う
> エコーループが発生した（1リンクダウン → 10並行調査）。
> Coordinator が調査ライフサイクルを一元管理し、DeviceAgent をツール実行者に格下げする。

### 変更対象ファイル一覧

| ファイル | 種別 | 変更内容 |
|---|---|---|
| `src/agentic_ni/distributed/incident.py` | 新規 | NetworkIncident / QueryRequest / QueryResponse |
| `src/agentic_ni/distributed/correlator.py` | 新規 | 複数 SYSLOG → 1 Incident に束ねる |
| `src/agentic_ni/distributed/coordinator.py` | 新規 | 調査ライフサイクル所有・RCA 判定 |
| `src/agentic_ni/distributed/device_agent.py` | 大幅削除 | P2P 廃止・execute_query() 追加 |
| `src/agentic_ni/distributed/orchestrator.py` | 修正 | Correlator / Coordinator を内包 |
| `prompts/device_agent_system.md` | 修正 | 役割をクエリ応答者に変更 |
| `tests/test_correlator.py` | 新規 | Correlator ユニットテスト |
| `tests/test_coordinator.py` | 新規 | Coordinator ユニットテスト |
| `tests/test_distributed_device_agent.py` | 修正 | execute_query テスト追加・BusMessage テスト削除 |
| `tests/test_distributed_e2e.py` | 書き直し | 新フロー（correlator→coordinator→agent） |
| `tests/test_distributed_orchestrator.py` | 修正 | receive_syslog API に更新 |

---

### Phase Refactor-1: データモデル定義　🔲 未着手

**目標**: Coordinator と DeviceAgent 間で交わす構造化データ型を定義する。

**実装ファイル**: `src/agentic_ni/distributed/incident.py`（新規）

#### タスク

- [x] 1. **`NetworkIncident` データクラス**
   ```python
   @dataclass
   class NetworkIncident:
       incident_id: str           # UUID
       correlation_key: str       # 例: "link:n0-n4"（topology の node id ベース）
       affected_devices: list[str]  # hostname リスト（例: ["Spine1", "Leaf3"]）
       syslog_events: list[str]   # 束ねた生 SYSLOG テキスト
       created_at: float          # time.monotonic()
   ```

- [x] 2. **`DeviceQueryRequest` / `DeviceQueryResponse` データクラス**
   ```python
   @dataclass
   class DeviceQueryRequest:
       incident_id: str
       target_device: str
       symptom_summary: str   # 「Spine1-Leaf3 間リンクダウン疑い」等

   @dataclass
   class DeviceQueryResponse:
       incident_id: str
       from_device: str
       findings: str              # LLM が整理した調査結果サマリー
       show_outputs: dict[str, str]  # command → raw output
   ```

- [x] 3. **`IncidentStatus` Literal 型**
   ```python
   IncidentStatus = Literal["open", "investigating", "resolved", "duplicate"]
   ```

**完了条件**: 型チェックエラーなしで import できること。

---

### Phase Refactor-2: EventCorrelator　🔲 未着手

**目標**: 同一リンクダウン由来の複数 SYSLOG を 1 つの `NetworkIncident` に束ねる。

**実装ファイル**: `src/agentic_ni/distributed/correlator.py`（新規）

#### タスク

- [x] 1. **相関キー抽出（ルールベース・LLM 不要）**

   topology の node id ペアをキーとして使用する。
   SYSLOG テキストから装置名・インターフェース名・ネイバー IP を抽出し、
   topology のリンク情報と照合して `"link:{n1_id}-{n2_id}"` 形式のキーを生成する。

   ```python
   # 例
   # "Spine1: %LINEPROTO ... GigabitEthernet0/2" → Spine1 の i2 → Leaf3 の n4 → "link:n0-n4"
   # "Leaf3: %BGP ... Neighbor 10.1.13.1"       → 同一リンク → "link:n0-n4"
   # キー抽出できない場合は "device:{hostname}" にフォールバック
   ```

- [x] 2. **時間窓バッファ（5 秒）**

   ```python
   class EventCorrelator:
       def __init__(self, topology_data: dict, window_seconds: float = 5.0,
                    on_incident: Callable[[NetworkIncident], Awaitable[None]] | None = None)
       async def receive_syslog(self, hostname: str, raw_text: str) -> None
       async def _flush_expired(self) -> None  # 窓が閉じた Incident を on_incident に渡す
   ```

- [x] 3. **ユニットテスト** `tests/test_correlator.py`（新規）
   - 5 秒以内の Spine1 + Leaf3 の SYSLOG 群 → 1 Incident にまとまること
   - 6 秒後の SYSLOG → 別 Incident として発火すること
   - 相関キー抽出できない SYSLOG → `"device:{hostname}"` キーで単独 Incident になること

**完了条件**: テストが全件パスし、上記ログのケースで Incident 数が 10 → 1 になること。

---

### Phase Refactor-3: IncidentCoordinator　🔲 未着手

**目標**: インシデントの調査ライフサイクルを一元管理し、RCA レポートを生成する。

**実装ファイル**: `src/agentic_ni/distributed/coordinator.py`（新規）

#### タスク

- [x] 1. **インシデント重複抑制**

   同一 `correlation_key` が 60 秒以内にオープン中なら後続を破棄する。

   ```python
   class IncidentCoordinator:
       def __init__(self, agent_registry: dict[str, DeviceAgent], llm, human_queue)
       async def handle_incident(self, incident: NetworkIncident) -> None
       def _is_duplicate(self, correlation_key: str) -> bool
   ```

- [x] 2. **2ラウンド調査ループ**

   ```
   Round 1: affected_devices 全台に QueryRequest を asyncio.gather で並列発行
   Round 2: LLM が「追加情報必要」と判定した場合のみ追加デバイスに発行（最大1ラウンド）
   完了宣言: RCA レポートを human_queue へ送信し Incident をクローズ
   ```

- [x] 3. **RCA 判定プロンプト**

   全 `DeviceQueryResponse` を集約して LLM に渡し、
   「症状 / 根本原因 / 影響範囲 / 推奨対応」を含む構造化レポートを生成する。

- [x] 4. **ユニットテスト** `tests/test_coordinator.py`（新規）
   - 正常系: 2台のレスポンスを集約して HUMAN レポートが生成されること
   - 重複抑制: 同一キーの Incident が 60 秒以内に来た場合スキップされること
   - タイムアウト: DeviceAgent が無応答の場合も完了宣言されること

**完了条件**: Mock DeviceAgent を使い、Incident 1件から RCA レポートが 1件生成されること。

---

### Phase Refactor-4: DeviceAgent Worker 化　🔲 未着手

**目標**: DeviceAgent から自律調査・P2P 通信を削除し、クエリ応答者に変える。

**実装ファイル**: `src/agentic_ni/distributed/device_agent.py`

#### タスク

- [x] 1. **削除する機能**

   | 削除対象 | 理由 |
   |---|---|
   | `BusMessageEvent` クラス | P2P 廃止 |
   | `_on_bus_message()` / bus.subscribe("chat") | P2P 廃止 |
   | `_investigating` フラグ | Coordinator が制御するため不要 |
   | `_chain_dedup` | エコーループが構造的に消えるため不要 |
   | `_route_output()` の `TO: Agent-XX` 分岐 | 対エージェント送信廃止 |
   | `inject_event()` の SyslogEvent 受付 | Correlator が代替 |

- [x] 2. **追加する機能**

   ```python
   async def execute_query(self, request: DeviceQueryRequest) -> DeviceQueryResponse:
       """Coordinator からの QueryRequest を処理して結果を返す。"""
       # 既存の _process_event をベースに出力先を戻り値に変更
       # TO: LOG のみ許可（bus への送信は禁止）
   ```

- [x] 3. **残す機能**
   - `_execute_tool()` — show コマンド実行（変更なし）
   - `_get_llm()` — LLM 初期化（変更なし）
   - `start()` / `stop()` — ライフサイクル（バス subscribe 範囲を縮小）

- [x] 4. **テスト更新** `tests/test_distributed_device_agent.py`
   - `execute_query()` テストを追加
   - `BusMessageEvent` / `inject_event` 関連テストを削除

**完了条件**: DeviceAgent 単体テストが全件パスし、bus への send が `execute_query` 内で発生しないこと。

---

### Phase Refactor-5: Orchestrator 配線更新　🔲 未着手

**目標**: `AgentOrchestrator` に `EventCorrelator` と `IncidentCoordinator` を組み込み、
SYSLOG 受信から RCA レポートまでのパイプを完成させる。

**実装ファイル**: `src/agentic_ni/distributed/orchestrator.py`

#### タスク

- [x] 1. **`EventCorrelator` と `IncidentCoordinator` を内包**

   ```python
   class AgentOrchestrator:
       def __init__(self, ...):
           ...
           self._correlator: EventCorrelator | None = None
           self._coordinator: IncidentCoordinator | None = None

       async def start_from_topology(self, topology_path) -> None:
           # 既存の DeviceAgent 起動処理は継続
           # Correlator / Coordinator を初期化して接続
   ```

- [x] 2. **`broadcast_syslog_to_all()` を `receive_syslog()` に置き換え**

   ```python
   async def receive_syslog(self, source_hostname: str, raw_msg: str,
                            severity: str = "unknown") -> None:
       """SYSLOG を Correlator に渡す（旧 broadcast_syslog_to_all の代替）。"""
       await self._correlator.receive_syslog(source_hostname, raw_msg)
   ```

   後方互換のため `broadcast_syslog_to_all()` は deprecation warning 付きで残す。

- [x] 3. **テスト更新** `tests/test_distributed_orchestrator.py`
   - `receive_syslog()` を使うテストを追加
   - E2E テスト `tests/test_distributed_e2e.py` を新フロー向けに書き直し

**完了条件**: `agentic-ni-ubuntu --config clos` がエラーなく起動し、SYSLOG 注入から
RCA レポートまで動作すること。

---

### Phase Refactor-6: システムプロンプト更新　🔲 未着手

**目標**: DeviceAgent の役割変更をプロンプトに反映する。

**実装ファイル**: `prompts/device_agent_system.md`

#### タスク

- [x] 1. **削除・変更するセクション**

   | 現行 | 変更 |
   |---|---|
   | ステップ 2「隣接エージェントへの問い合わせ（最大1回のみ）」 | 削除 |
   | `TO: Agent-XX` 出力フォーマット | 削除 |
   | `TO: ALL` ブロードキャスト | 削除 |
   | 役割定義「自律的に調査・報告を行い…隣接エージェントと連携」 | 変更 |

- [x] 2. **追加するセクション**

   - 役割定義を「Coordinator から QueryRequest を受け取り、show コマンドで調査し、
     `DeviceQueryResponse` を返す専門家」に変更
   - 出力フォーマットを `TO: COORDINATOR | MSG:` / `TO: LOG | MSG:` のみに変更
   - 「調査完了条件」セクションを追加（ツール上限に達したら中間結果を返す）

**完了条件**: プロンプトに `TO: Agent-XX` が出現しないこと。

---

### Phase Refactor-7: テスト最終確認　✅ 完了

**目標**: 全テストスイートがパスし、エコーループが消滅していることを確認する。

#### タスク

- [x] 1. **新規テストファイル**
   - `tests/test_correlator.py`（Refactor-2 で作成済み）
   - `tests/test_coordinator.py`（Refactor-3 で作成済み）

- [x] 2. **更新テストファイル**
   - `tests/test_distributed_device_agent.py`（Refactor-4 で更新済み）
   - `tests/test_distributed_orchestrator.py`（Refactor-5 で更新済み）
   - `tests/test_distributed_e2e.py`（Refactor-5 で書き直し済み）

- [x] 3. **全テスト実行**
   ```bash
   uv run pytest tests/test_distributed*.py tests/test_correlator.py tests/test_coordinator.py -v
   ```

- [x] 4. **ログ検証**（実機または Mock）
   - SYSLOG 5件（同一リンク）注入 → Incident 1件のみ発火すること
   - `調査開始` のログが Coordinator で 1 回のみ出ること
   - DeviceAgent のログに `TO: Agent-XX` が出ないこと

- [x] 5. **既存テストの非破壊確認**
   ```bash
   uv run pytest tests/ -v --ignore=tests/test_live_*.py
   ```

**完了条件**: 全テストが GREEN になり、ログに調査ループが見られないこと。

---

### Refactor フェーズの実装順序

```
Refactor-1（データモデル）
    ↓
Refactor-2（Correlator）  ←── Refactor-3（Coordinator）と並行可
Refactor-3
    ↓
Refactor-4（DeviceAgent Worker 化）
    ↓
Refactor-5（Orchestrator 配線）
    ↓
Refactor-6（プロンプト）
    ↓
Refactor-7（テスト最終確認）
```
```
