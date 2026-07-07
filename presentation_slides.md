---
title: AI駆動型 ネットワーク設計検証 自律エージェントシステム
subtitle: agentic-ni
date: 2026-07-07
---

# AI駆動型 ネットワーク設計検証
# 自律エージェントシステム

**agentic-ni**

Cisco CML × LangGraph × pyATS

> 人間が要件を入力するだけで
> 設計・デプロイ・検証・実機投入までを AI が自律実行

---

# アジェンダ

1. **背景・課題** — なぜこのシステムが必要か
2. **システムコンセプトとアーキテクチャ**
3. **各機能フェーズの詳細**
   - 設計・検証ループ（Phase 1〜7）
   - 障害シミュレーション（Phase B）
   - トラブルシューティング（Phase H）
   - 設計分析・改善（Phase E）
   - 実機適用モード（Phase I）
4. **テスト戦略と品質**
5. **今後の展望**

---

# 背景・課題

| 工程 | 課題 |
|------|------|
| 設計書作成 | 属人化・手戻りが多い |
| CML へのデプロイ | 手動・コマンドミスが発生しやすい |
| テスト実行 | 網羅性が低い。障害試験は特に工数大 |
| 実機投入 | バックアップ・切り戻し手順が複雑 |
| ドキュメント | 設計書と実機の乖離が起きやすい |

**理想**

> 「要件を書くだけで、設計から実機投入まで AI が自律的に実行する」

---

# システムコンセプト

**人間の役割**

- 要件入力（自然言語）
- 最終レポートの承認

**AIエージェントの役割**

- 設計 → CMLデプロイ → テスト → 修正ループ
- 障害シミュレーション（任意）
- 実機バックアップ → Human承認 → 実機投入

> CML を「ステージング環境」として活用
> **「CML で PASS したコンフィグだけが実機に届く」** 安全設計

---

# アーキテクチャ全体図

```
[ 人間 (要件入力) ]          [ 既存ラボ + 問題説明 ]
        |                              |
        v                              v --troubleshoot
 +---------------------------+  +---------------------+
 | LangGraph 設計・検証ループ |  | LangGraph トラブル   |
 |                           |  |                     |
 | 知識ベース RAG              |  | 収集→診断→修正      |
 |                           |  +----------+----------+
 |  設計エージェント <- FAIL   |             |
 |        |                  |        修正レポート
 |  検証エージェント - FAIL --+
 |        | 全PASS
 |        v
 |  障害シミュレーション   <- --fault-sim
 +-----------+---------------+
             |
     [Phase I] --apply-to-live
     実機バックアップ → Human承認 → 実機投入
```

---

# 技術スタック

| 層 | 技術 | 役割 |
|----|------|------|
| オーケストレーション | LangGraph | エージェント間の状態管理・条件分岐 |
| LLM | OpenAI / Anthropic / Ollama | 設計・診断・修正コマンド生成 |
| CML操作 | `virl2_client` 2.10 | ラボ作成・起動・リンク断制御 |
| ネットワーク検証 & 実機操作 | `pyATS` + `Genie` + `Unicon` | showコマンド解析・コンフィグ投入 |
| 知識ベース RAG | `ChromaDB` | 社内標準・設計ガイドの参照 |
| 型安全・構造化出力 | `Pydantic v2` | LLM 出力の型強制 |
| 依存関係管理 | `uv` | 高速パッケージ管理 |

> **Netmiko 不使用**: 実機操作も pyATS/Unicon に統一

---

# 実装フェーズ全体マップ

| Phase | 内容 |
|-------|------|
| 1〜2 | 基盤セットアップ・State定義・グラフ骨格 |
| 3〜4 | CMLツール・pyATSツール |
| 5〜6 | 設計エージェント・検証エージェント |
| 7 | グラフ統合 & E2Eテスト ← **基幹フロー完成** |
| B | 障害シミュレーション（リンク断・復旧検証） |
| H | トラブルシューティングモード（既存ラボ診断・修正） |
| E | 設計分析・改善モード（品質評価・コンフィグ生成） |
| **I** | **実機適用モード（pyATS/Unicon で実機へ投入）← 今回** |

---

# 設計エージェント

**役割**: 要件 → CML トポロジー YAML + 機器コンフィグの生成

**入力**

- 自然言語の要件
- エラーログ（再設計時）

**出力（Pydantic で型強制）**

- `topology_yaml` — CML 読み込み用 YAML
- `device_configs` — 機器ごとのコンフィグテキスト
- `design_rationale` — 設計意図の説明

**動作分岐**

| 状態 | 動作 |
|------|------|
| error_log が空 | 要件からゼロ設計 |
| error_log に内容あり | 原因分析 → **差分修正のみ** 出力（コスト最適化）|

---

# 検証エージェント

**役割**: CML デプロイ → テスト計画立案 → 実行 → 失敗推論

**処理フロー**

1. CML にラボを作成してコンフィグを投入
2. 要件文を解析し、LLM がテスト項目を自律判断
3. pyATS で各テストを実行
4. 全PASS → State 更新 / FAIL → 原因推論して `error_log` に格納
5. `retry_count` をインクリメント

**テスト種別（LLM が自律選択）**

- `ospf_neighbors` / `bgp_summary` / `ping`
- `route_table` / `interface_status` / `traceroute`
- `bgp_path` / `vlan_interfaces`

---

# 設計・検証ループ詳細

```
設計エージェント
      |
      v
検証エージェント
      |
   +--+--+
 PASS  FAIL
   |      |
   v      v
最終     error_log に
レポート 原因推論を格納
              |
         retry_count < MAX_RETRIES (デフォルト 5)
              |
              v 差し戻し
         設計エージェント（修正設計）
```

> MAX_RETRIES 超過 → **エスカレーションレポート** を出力して人間にフィードバック

---

# RAG 知識ベース

**社内標準・設計ガイドを LLM の設計判断に組み込む**

```
1. rag/ にテキストファイルを配置
   ospf_guide.md, bgp_guide.md, 社内標準.txt ...

2. agentic-ni --rag-index で ChromaDB に登録

3. 設計・トラブルシューティング時に自動参照
   → 要件との類似度が高い上位 3 チャンクのみ LLM へ送信
```

**付属サンプル（合計 24 チャンク）**

| ファイル | 内容 |
|---------|------|
| `ospf_guide.md` | OSPF 設計・タイマー・トラブルシューティング |
| `bgp_guide.md` | iBGP/eBGP ピアリング・よくある失敗 |
| `cml_design_guide.md` | CML YAML 設計パターン |
| `ios_variants_guide.md` | IOSv / IOL / CSR1000v / NX-OS 比較 |

---

# Phase B — 障害シミュレーション

**全テスト PASS 後に冗長性を自動検証**

```bash
agentic-ni demo2 --fault-sim
```

**フロー**

1. 全テスト PASS
2. LLM が障害シナリオを自律計画（どのリンクを落とすか）
3. リンク断（CML API: `interface.shutdown()`）
4. テスト実行（OSPFネイバー数・ping）
5. リンク復旧（`interface.bring_up()`）
6. 復旧後テスト実行
7. 全シナリオのレポート出力

> demo2 セット（R1-R2-R3 フルメッシュ OSPF）で
> **3 リンク断シナリオを自動実施**

---

# Phase H — トラブルシューティングモード

**既存の動かないラボを AI が自律修正**

```bash
agentic-ni demo --troubleshoot <lab_id>
agentic-ni demo --troubleshoot <lab_id> --issue 'OSPF ネイバーが上がらない'
```

**フロー: collect → diagnose → fix → verify（最大 3 回）**

| ステップ | 内容 |
|---------|------|
| 収集 | 全機器の running-config + show コマンド出力を取得 |
| 診断 | LLM が根本原因を分析（Pydantic 型強制） |
| 修正 | 差分コマンドを生成し `configure terminal` で投入 |
| 検証 | テストを実行 → PASS なら完了 / FAIL ならサイクルを繰り返す |

> 修正は **wipe なし・差分投入のみ** → 既存設定を最大限保持

---

# Phase E — 設計分析・改善モード

**稼働中ラボの設計品質を AI が評価・改善**

| モード | コマンド | 動作 |
|--------|---------|------|
| `--analyze` | `agentic-ni demo --analyze` | 設計評価レポートを出力（**変更なし**） |
| `--improve` | `agentic-ni demo --improve --request "..."` | 改善コンフィグを生成し保存（**deploy なし**）|

**--analyze 出力例**

```
## 設計評価: ⚠️ 要改善

| 重大度  | 問題 | 推奨対応 |
|---------|------|---------|
| WARNING | router-id が未設定 | Loopback0 で router-id を明示設定 |
| INFO    | no ip domain-lookup 未設定 | グローバル設定に追加 |
```

---

# Phase I — 実機適用モード（概要）

**CML 検証済みコンフィグを実機に安全投入**

```bash
agentic-ni demo --apply-to-live
agentic-ni demo --apply-to-live --inventory inventory/prod.yaml
agentic-ni demo --apply-to-live --live-verify
```

**フロー**

1. CML 設計・検証（全テスト PASS）
2. 実機へのバックアップ取得
3. Human による最終承認（スキップ不可）
4. pyATS/Unicon で実機に設定投入
5. 実機で pyATS テスト実行（`--live-verify` 指定時）
6. 適用レポート出力

> **「CML で PASS したコンフィグのみが実機に届く」**

---

# Phase I — インベントリファイルとフロー

**`inventory/demo.yaml` — CMLノード名と実機の対応付け**

```yaml
devices:
  R1:
    host: "192.168.100.1"
    device_type: "cisco_ios"     # pyATS の os マッピングに使用
    username: "${LIVE_USERNAME}" # .env から展開
    password: "${LIVE_PASSWORD}"
    port: 22
    apply_mode: "config_merge"
```

**apply_mode の選択肢**

| モード | 動作 |
|--------|------|
| `config_merge` | `configure terminal` で追記（**デフォルト**）|
| `config_replace` | 設定全体を置換 |
| `incremental` | CML との diff のみ投入（変更量を最小化）|

---

# Phase I — 多段安全機構（6層）

| Level | 内容 |
|-------|------|
| **1** | インベントリ存在チェック（ファイルがなければ即エラー）|
| **2** | SSH 疎通確認（全デバイス）—1台でも繋がらなければ中断 |
| **3** | running-config バックアップ取得 — 失敗すれば中断 |
| **4** | **Human-in-the-Loop（承認必須）**— 承認なしでは絶対に進まない |
| **5** | `apply_mode=config_merge` デフォルト — 破壊的変更を抑制 |
| **6** | 失敗デバイスの自動ロールバック — 投入失敗時は即座にバックアップを復元 |

> さらに **CML テスト PASS が前提条件**
> 検証に失敗した設計は実機に一切触れない

---

# Phase I — Human-in-the-Loop 確認画面

**承認なしには絶対に先へ進まない**

```
⚠️  実機へのコンフィグ適用を開始しようとしています

【適用対象】
  ✅ R1 (192.168.100.1) — config_merge — バックアップ取得済み (847 行)
  ✅ R2 (192.168.100.2) — config_merge — バックアップ取得済み (612 行)

【CML テスト結果（検証済み）】
  ✅ OSPF ネイバー確認: PASS
  ✅ ping 2.2.2.2: PASS

続行しますか？ (yes / no / rollback-only)
```

| 入力 | 動作 |
|------|------|
| `yes` | コンフィグ投入を実行 |
| `no` | 中止（実機に一切変更なし）|
| `rollback-only` | 新コンフィグなし・バックアップのみ復元 |

---

# Phase I — 実機 pyATS 検証（--live-verify）

**CML と同じテストを実機でも実行**

**フロー**

1. `live_apply`（コンフィグ投入）
2. `live_verify`（`--live-verify` 指定時のみ）
   → インベントリから pyATS testbed YAML を生成（Unicon SSH 直接接続）
   → `test_plan_items` を再利用して同一テストを実行
3. `live_report`（レポート出力）

**レポートへの追記例**

| テスト名 | 結果 | 詳細 |
|---------|------|------|
| OSPFネイバー確認 | ✅ PASS | 2 neighbor(s) FULL |
| ping 2.2.2.2 | ✅ PASS | ping OK |

> **実機検証判定**: ✅ 全 2 テスト PASS

---

# CLI インターフェース一覧

| コマンド | 用途 |
|---------|------|
| `agentic-ni demo` | 通常モード（設計 → CML検証）|
| `agentic-ni demo --dry-run` | ドライラン（CML 不要・設計ドキュメント生成のみ）|
| `agentic-ni demo2 --fault-sim` | 障害シミュレーション |
| `agentic-ni demo --troubleshoot <id>` | トラブルシューティング |
| `agentic-ni demo --analyze` | 設計分析（変更なし）|
| `agentic-ni demo --improve --request '...'` | 設計改善 |
| `agentic-ni demo --apply-to-live` | 実機適用（Phase I）|
| `agentic-ni demo --apply-to-live --live-verify` | 実機適用 + 実機検証 |
| `agentic-ni --rag-index` | 知識ベース索引化 |
| `agentic-ni --rag-stats` | RAG 統計確認 |

---

# テスト戦略・品質指標

**モック中心の高カバレッジテスト体制**（実機・CML・LLM API 不要）

| テスト対象 | テスト数 |
|-----------|---------|
| LLM ファクトリー | 4 |
| CML 操作 | 14 |
| pyATS/Genie | 19 |
| 設計エージェント | 12 |
| 検証エージェント | 25 |
| グラフ条件分岐 | 10 |
| E2E シナリオ | 16 |
| Phase I（live_precheck / confirm / apply / report / verify / e2e）| 203 |
| その他（障害シミュ / トラブルシュ / 分析改善 / 設計ドキュメント）| 127 |
| **合計** | **430+** |

---

# 今後の展望

| フェーズ | 内容 | 工数感 |
|---------|------|-------|
| **Phase F** | プロンプトセットの拡充（MPLS L3VPN、BGP EVPN、QoS 等）| 中 |
| **Phase G** | マルチベンダー対応（Arista EOS、Juniper Junos、NX-OS）| 大 |
| CI/CD 統合 | GitHub Actions でのネットワーク設計の自動テスト | 中 |
| 実績 RAG | 成功した設計パターンを自動蓄積・再利用 | 中 |
| Web UI | ブラウザから要件入力・レポート閲覧 | 大 |

**Phase G のポイント**

- pyATS の `os` フィールドをベンダーごとに自動設定
- Genie parsers が対応済みのベンダーはそのまま利用可能
- `architect_system.md` にベンダー固有のテンプレートを追加

---

# まとめ

**agentic-ni が実現するもの**

| 従来 | agentic-ni |
|------|-----------|
| 設計書作成（数時間〜数日）| 要件を `requirement.md` に記述（数分）|
| CML で手動デプロイ | `agentic-ni demo --apply-to-live` を実行 |
| コマンドラインでテスト | 最終レポートを承認するだけ |
| 実機投入（バックアップ・切り戻し手順）| 6層安全機構で自動保護 |

**主な特長**

- ✅ 自律設計・検証ループ — LLM が失敗原因を推論し自動修正
- ✅ 多段安全機構（6層）— 実機には CML で検証済みのコンフィグのみ
- ✅ pyATS 統一 — CML 検証も実機操作も同一ライブラリで一貫
- ✅ Human-in-the-Loop — 実機投入前は必ず人間の承認を取得
- ✅ RAG 知識ベース — 社内標準・設計ガイドを AI の判断に反映

---

# Q & A

ご質問・ご意見をお願いします
