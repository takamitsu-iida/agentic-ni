# 自律分散型トラブルシューティングエージェントシステム

ネットワーク装置ごとにAIエージェント（DeviceAgent）を配置し、**コーディネーター・ワーカーパターン**で連携してトラブルシューティングを自動化するシステムの設計書です。


## 1. システム概要

本システムは、topology.yaml で定義されたネットワーク装置に1対1で対応する DeviceAgent（ワーカー）を配置し、IncidentCoordinator（コーディネーター）が調査ライフサイクルを一元管理することで、障害の検知・原因特定・一次対処を自動化するマルチエージェントシステムである。

当初設計では エージェント間 P2P 通信（Agent-A → Agent-B）を採用していたが、**現在の実装では廃止**し、すべての調査はコーディネーターを経由する集中制御方式に変更された。


## 2. 全体アーキテクチャ

```text
[ 物理/仮想ネットワーク層 ]

+--------+   syslog(UDP)  +--------+   syslog(UDP)  +--------+
|   R1   |--------------->|        |<---------------|   R2   |
+--------+                |SyslogServer             +--------+
                          | (Ubuntu)|
+--------+   SSH/pyATS    |        |    SSH/pyATS   +--------+
|   R1   |<---------------|        |--------------->|   R2   |
+--------+                +--------+                +--------+

[ エージェント実行層（Python asyncio プロセス） ]

+-------------------+
| AgentOrchestrator |  topology.yaml からエージェントを生成・管理
+-------------------+
          |
          |  NetworkIncident
          v
+-------------------+     DeviceQueryRequest      +---------------+
| IncidentCoordinator|-------------------------->  | DeviceAgent-R1|
|                   |<--------------------------  | (LLM + Tools) |
|   (RCA / LLM)    |     DeviceQueryResponse      +---------------+
+-------------------+
          |                                        +---------------+
          |  DeviceQueryRequest      ---------->   | DeviceAgent-R2|
          |<--------------------------             | (LLM + Tools) |
          +----------------------------------------+---------------+
          ^
          | NetworkIncident
+-------------------+
| EventCorrelator   |  syslog を相関させてインシデント化
+-------------------+
          ^
          |  SyslogEvent
+-------------------+
| SyslogServer      |  UDP 514 で RFC 3164 syslog を受信
| DeviceLogPoller   |  show logging を定期ポーリング
| CMLStateWatcher   |  CML lab.sync_states() で状態変化を検知
+-------------------+

[ メッセージバス (InMemory / MQTT / NATS) ]
  - network/agents/#       : ConversationRecorder が全メッセージを記録
  - network/agents/alert   : PacketLossDetector がアラートを発行
```


## 3. 構成要素と役割

### ① AgentOrchestrator（`orchestrator.py`）

topology.yaml を解析して全 DeviceAgent・IncidentCoordinator・EventCorrelator を生成・起動する。

- `parse_topology()` でノード情報・隣接マップを構築する。
- `_NO_AGENT_NODE_DEFINITIONS`（ubuntu, external_connector 等）に該当するノードはエージェント起動対象外。
- CLI エントリポイント: `agentic-ni-ts`（syslog 注入テスト）、`agentic-ni-watch`（CML 監視）

### ② SyslogServer / DeviceLogPoller / CMLStateWatcher（障害検知層）

| コンポーネント | 検知手段 | 用途 |
|---|---|---|
| `SyslogServer` | UDP 514 で RFC 3164 syslog を受信 | 実機からのリアルタイム通知 |
| `DeviceLogPoller` | `show logging` を定期ポーリング | 自装置のログバッファ自律監視 |
| `CMLStateWatcher` | `lab.sync_states()` で CML 状態を監視 | CML ラボでのリンク/ノード変化を検知 |
| `ConnectivityMonitor` | SSH（TCP 22）の到達性を定期確認 | 通信断・復旧の自律検知 |
| `PacketLossDetector` | 両端の IF 統計を比較してロス率を計算 | パケットロスアラートを MessageBus へ発行 |

### ③ EventCorrelator（`correlator.py`）

複数の SyslogEvent を時間窓（デフォルト数秒）で束ねて `NetworkIncident` を生成する。

相関キーの抽出戦略（優先順）:
1. インターフェース名 + topology → リンク ID ペア `link:{n_a}-{n_b}`
2. BGP/OSPF ネイバー IP → /30 サブネットキー `subnet:{masked_int}`
3. フォールバック: `device:{hostname}`

### ④ IncidentCoordinator（`coordinator.py`）

`NetworkIncident` を受け取り、調査ライフサイクルを管理する。

1. `affected_devices` の各 DeviceAgent へ `DeviceQueryRequest` を**並列**発行する。
2. 各 DeviceAgent から `DeviceQueryResponse` を集約する。
3. LLM（RCA プロンプト）で根本原因を分析し、最大 2 ラウンド実行する。
4. 最終レポートを `human_queue` へ送る（Human-in-the-Loop）。

重複抑制: 同一 `correlation_key` のインシデントは `dedup_window`（デフォルト 60 秒）内に 1 件のみ処理する。

### ⑤ DeviceAgent（`device_agent.py`）

各ネットワーク装置に対応する独立したワーカーエージェント。

- トリガーイベント:

| イベント型 | 発生源 |
|---|---|
| `SyslogEvent` | SyslogServer / DeviceLogPoller |
| `PollEvent` | 定期ポーリング |
| `ConnectivityLostEvent` | ConnectivityMonitor |
| `ConnectivityRestoredEvent` | ConnectivityMonitor |
| `HumanCommandEvent` | 人間オペレーターからの直接指示 |

- `execute_query(DeviceQueryRequest)` を呼び出されると、LLM + show コマンドで自装置を調査し `DeviceQueryResponse` を返す。
- ローカルメモリ（`DeviceMemory` / `DesiredState`）で直近の状態履歴と期待状態を管理する。

### ⑥ DeviceToolkit（`device_tools.py`）

| クラス | 用途 |
|---|---|
| `DeviceToolkit` | pyATS 経由で実機に接続する本番用ツールキット |
| `MockDeviceToolkit` | 外部依存なしのテスト・オフライン用モック |
| `CMLDeviceToolkit` | CML lab API 経由で show コマンドを実行 |

ツール一覧:
- `run_show`: show コマンドを実行して出力を返す（Read-Only）
- `apply_config`: `DEVICE_AGENT_READONLY=false` の場合のみ提供。実際の設定投入は行わず `human_queue` に積むだけ（Human-in-the-Loop）。

### ⑦ メッセージバス（`bus.py`）

| バックエンド | 用途 |
|---|---|
| `InMemoryBus` | テスト・開発用（外部依存なし） |
| `MQTTBus` | paho-mqtt>=2.0 使用 |
| `NATSBus` | nats-py>=2.3 使用 |

トピック命名規則:
- `network/agents/chat`: ブロードキャスト（全体通知）
- `network/agents/{agent_id}/direct`: 1対1ダイレクトメッセージ
- `network/agents/alert`: PacketLossDetector のアラート

メッセージループ防止のため `hop_count` が `MAX_HOP_COUNT`（=2）を超えたメッセージは破棄する。

### ⑧ 重複抑制（`dedup.py`）

| クラス | 対象 |
|---|---|
| `MessageDeduplicator` | `message_id` 単位でバスメッセージの二重配信を防ぐ |
| `SyslogDeduplicator` | syslog テキストをフィンガープリント化して重複を抑制 |

### ⑨ RAG（知識ベース）

`tools/rag_tools.py` の `search_knowledge()` を使い、`rag/` ディレクトリ内の知識ファイルを ChromaDB で索引化して検索する。

| 組み込み箇所 | クエリ | 効果 |
|---|---|---|
| `DeviceAgent._build_query_messages()` | `DeviceQueryRequest.symptom_summary` | show コマンド調査前にプロトコル固有の知識（OSPF/BGP ガイド等）を LLM へ注入 |
| `IncidentCoordinator._analyze()` | `NetworkIncident.syslog_events`（先頭5件） | RCA 分析前に関連するトラブルシューティング手順を LLM へ注入 |

- chromadb が未インストール、またはインデックスが空の場合は何もせず通常処理を継続（エラーにならない）。
- インデックス構築: `agentic-ni --rag-index` または `rag_tools.index_knowledge_files()` を実行する。

### ⑩ ConversationRecorder / Reporter（`reporter.py`）

`ConversationRecorder` が `network/agents/#` を購読してメッセージを時系列記録し、`generate_report()` で `reports/` ディレクトリに Markdown レポートを出力する。


## 4. DeviceAgent の出力フォーマット

LLM が生成する出力は以下のフォーマットを使用する。複数行レポートに対応しており、次の `TO:` が出現するまでの行を同じメッセージとして連結する。

```text
TO: COORDINATOR | MSG: [調査結果サマリー]
TO: LOG         | MSG: [ローカル記録用の詳細ログ]
TO: HUMAN       | MSG: [人間へのエスカレーション・設定変更提案]
```

（当初設計にあった `TO: Agent-X | MSG: ...` による P2P 通信は廃止）


## 5. シーケンス（障害発生から解決まで）

```text
[ネットワーク装置]   [障害検知層]       [EventCorrelator]  [IncidentCoordinator]  [DeviceAgent]    [Human]
       |                 |                    |                    |                    |               |
  (リンクダウン)         |                    |                    |                    |               |
       |-- syslog(UDP)->|SyslogServer         |                    |                    |               |
       |                 |-- SyslogEvent ---->|                    |                    |               |
       |                 |  (相関窓で集約)     |                    |                    |               |
       |                 |                    |-- NetworkIncident ->|                    |               |
       |                 |                    |                    |-- QueryRequest ---> |               |
       |                 |                    |                    |   (並列・複数台)    |               |
       |                 |                    |                    |                    |-- show cmd --->|
       |                 |                    |                    |                    |<-- output -----|
       |                 |                    |                    |<-- QueryResponse --|               |
       |                 |                    |                    |  (LLM で RCA)      |               |
       |                 |                    |                    |-- FINAL_REPORT ----------------------------->|
       |                 |                    |                    |   (human_queue)    |               |
       |                 |                    |                    |                    |               |
       |                 |                    |                    | (追加情報が必要な場合)               |
       |                 |                    |                    |-- QueryRequest ---> |               |
       |                 |                    |                    |<-- QueryResponse --|               |
       |                 |                    |                    |-- FINAL_REPORT -------------------------->|
```


## 6. セキュリティとガードレール

| 項目 | 実装 |
|---|---|
| Read-Only デフォルト | `DEVICE_AGENT_READONLY=true`（デフォルト）のとき `apply_config` ツールは `get_tools()` に含まれない |
| Human-in-the-Loop | `apply_config` は実際の設定投入を行わず `human_queue` に積み、CLI 承認ループが管理者に確認してから反映する |
| ループ防止 | `hop_count` が `MAX_HOP_COUNT`（=2）を超えたメッセージはバスで破棄する |
| 重複抑制 | `MessageDeduplicator`・`SyslogDeduplicator`・インシデント重複抑制（`dedup_window`）の 3 層で過剰反応を防ぐ |
| ノイズフィルタ | pyATS 接続時の自動生成 syslog（`%SYS-5-CONFIG_I` 等）を `SyslogServer` が破棄する |


## 7. ディレクトリ構成（分散型関連）

```text
src/agentic_ni/distributed/
  orchestrator.py       # AgentOrchestrator・トポロジーパーサー
  coordinator.py        # IncidentCoordinator（調査ライフサイクル管理）
  correlator.py         # EventCorrelator（syslog 相関・インシデント化）
  device_agent.py       # DeviceAgent（ワーカー）
  device_tools.py       # DeviceToolkit / MockDeviceToolkit / CMLDeviceToolkit
  bus.py                # MessageBus 抽象化（InMemory / MQTT / NATS）
  message.py            # AgentMessage スキーマ
  incident.py           # NetworkIncident / DeviceQueryRequest / DeviceQueryResponse
  memory.py             # DeviceMemory / DesiredState / StatusSnapshot
  dedup.py              # MessageDeduplicator / SyslogDeduplicator
  syslog_server.py      # UDP syslog 受信サーバー
  log_poller.py         # DeviceLogPoller（show logging 定期ポーリング）
  connectivity_monitor.py # ConnectivityMonitor（TCP 到達性監視）
  cml_watcher.py        # CMLStateWatcher（CML 状態ポーリング）
  packet_loss_detector.py # PacketLossDetector（インターフェース統計比較）
  reporter.py           # ConversationRecorder / generate_report()
  prompts.py            # DeviceAgent 用プロンプトビルダー
  watch_cli.py          # agentic-ni-watch CLI
```
