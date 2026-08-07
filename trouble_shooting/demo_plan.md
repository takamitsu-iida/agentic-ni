# デモンストレーション計画書
## AIエージェントコンテスト出品：自律分散型ネットワーク AI エージェント

---

## 1. デモのコンセプト

### 一言で言うと
> 「ネットワーク障害が起きた瞬間、AI エージェントたちが自律的に会話して、人間より先に根本原因を突き止める」

### 審査員に訴えるポイント

| # | おぉポイント | 演出 |
|---|------------|------|
| 1 | **並行稼働** — 複数 AI が同時起動 | 2台のエージェント起動アニメーション |
| 2 | **自律調査** — `show` コマンドを AI が自ら実行 | `⚙ run_show(command='show ip ospf neighbor')` のリアルタイム表示 |
| 3 | **自然言語対話** — エージェントが互いに報告・依頼 | `Agent-R1 → Agent-R2:「Hello タイマーを確認してください」` |
| 4 | **根本原因特定** — 設定ミスをクロスチェックで発見 | 数秒以内に正確な診断 |
| 5 | **構造化レポート** — 管理者向け対処手順を自動生成 | 🚨 HUMAN エスカレーション ボックス |

---

## 2. シナリオ選定

### 推奨シナリオ: **OSPF Hello タイマーミスマッチ**

**選定理由**
- 単純なリンク断と異なり「物理は正常なのに疎通しない」という非直感的な障害
- 双方の装置を確認しないと発見できない → エージェント間協調が必然的に発生
- 実際の現場でよく起こる "うっかりミス" で共感を得やすい
- 修正コマンドが明確なので「提案できる AI」を印象づけられる

**ストーリーライン**
1. 夜間バッチ後に OSPF ネイバーが消失（syslog が飛ぶ）
2. Agent-R1 が自装置を調査 → 物理リンクは UP、タイマー 10 秒
3. Agent-R1 が Agent-R2 に問い合わせ
4. Agent-R2 が調査 → タイマー 30 秒（**ここが犯人**）
5. Agent-R1 がクロスチェック → ミスマッチを自律検出
6. 管理者に `ip ospf hello-interval 10` コマンドを提案

**所要時間**: 約 **1.6 秒**（手動診断の平均 30〜120 分との対比）

---

## 3. デモ実行手順

### 前提条件
```bash
# インストール確認
uv sync
# または
pip install -e .
```

### シナリオ A（スクリプトモード — API キー不要）
```bash
agentic-ni-demo --scenario ospf-timer --scripted
```

### シナリオ A（リアル LLM モード — 推奨）
```bash
# .env に LLM_PROVIDER と API キーを設定済みの場合
agentic-ni-demo --scenario ospf-timer
```

### シナリオ B（物理リンク断）
```bash
agentic-ni-demo --scenario link-down --scripted
```

### 終了後のレポート確認
```bash
ls -lt reports/demo-*.md | head -3
cat reports/demo-$(ls -t reports/ | head -1)
```

---

## 4. プレゼンテーション台本（7 分）

### オープニング（1 分）
> 「ネットワーク障害の平均修復時間は 4 時間以上です。
> その大半が『どの装置が原因か』を特定する時間です。
> 今日は、この診断を AI に任せる世界をお見せします。」

**スライド**: 従来の障害対応フロー（アラート→手動SSH→コマンド実行→原因特定→修復）

### アーキテクチャ説明（1 分）
> 「各ネットワーク装置の隣に、専用の AI エージェントを配置します。
> エージェント同士はメッセージバスで会話し、人間の指示なしに協調して動きます。」

**スライド**: concept_design.md のアーキテクチャ図（サイドカー方式）

### ライブデモ（4 分）

**[ステップ 1]** 端末を開いてデモコマンドを実行
```bash
agentic-ni-demo --scenario ospf-timer --scripted
```

**画面に映るもの（逐次解説）**:

```
[1] エージェント起動中...
✓ Agent-R1  (router)  隣接: Agent-R2  ツール: 4 個   ← 「2台の AI が起動しました」
✓ Agent-R2  (router)  隣接: Agent-R1  ツール: 4 個
```
> 「トポロジー YAML を読み込んだだけで、装置の数だけ AI が自動起動します」

```
[2] 障害発生 — syslog 注入
⚠️  %OSPF-5-ADJCHG: Nbr 10.0.12.2 ... from LOADING to DOWN
```
> 「ここで OSPF ネイバーが消えた syslog が飛んできました」

```
1.3s  Agent-R1  ⚙  run_show(command='show ip ospf neighbor')
1.3s  Agent-R1  ⚙  run_show(command='show ip ospf interface GigabitEthernet0/0')
```
> 「R1 の AI が自ら show コマンドを実行して状況を把握しています」

```
1.3s  Agent-R1 → Agent-R2
      │ R1 の OSPF ネイバーが消失しました。Hello タイマーは 10 秒です。
      │ そちらの Hello タイマー設定を確認してください。
```
> 「R1 は原因が自装置にない可能性を感じ、隣のエージェントに問い合わせます」

```
1.3s  Agent-R2  ⚙  run_show(command='show ip ospf interface GigabitEthernet0/0')
1.3s  Agent-R2 → Agent-R1
      │ R2 の Hello タイマーは 30 秒です。R1 と異なっているようです。
```
> 「R2 が自装置を調査して報告。双方の情報が揃いました」

```
🚨  HUMAN エスカレーション  ←  Agent-R1
【障害診断レポート】
- 症状    : R1-R2 間 OSPF ネイバー消失
- 根本原因: Hello タイマーのミスマッチ（R1=10秒、R2=30秒）
- 推奨対応: R2 で `ip ospf hello-interval 10` を実行
```
> 「R1 がクロスチェックして根本原因を自律特定。管理者に具体的な修正コマンドを提案しました」

```
✅  診断完了
診断所要時間: 1.6 秒
```
> **「手動診断なら 30 分かかる作業を 1.6 秒で完了しました」** ← ここが最大の おぉ ポイント

### クロージング（1 分）
> 「このシステムの特長は 3 つです。
> 1) 既存の topology.yaml を読むだけで自動スケール
> 2) 読み取り専用ツールのみで安全。設定変更は必ず人間が承認
> 3) MQTT や NATS に差し替えれば実ネットワークでも動作します」

---

## 5. 想定 Q&A

| 質問 | 回答 |
|------|------|
| 実際のネットワーク装置で動く？ | `DeviceToolkit`（pyATS 経由）を使えば実機に SSH 接続して show コマンドを実行できます |
| 設定変更は AI が自動でやるの？ | `DEVICE_AGENT_READONLY=true`（デフォルト）で read-only のみ。変更は Human-in-the-Loop で管理者が承認してから実行 |
| エージェントが無限ループしない？ | ホップカウンター（上限 5）でループを検知・破棄する仕組みを実装済み |
| LLM のコストは？ | 1 インシデントあたり数回の LLM 呼び出し。ローカル LLM（Ollama）にも対応可能 |
| 大規模ネットワーク（100台以上）は？ | topology.yaml のノード数に比例してエージェントが起動。NATS を使えばブローカー負荷分散も可能 |

---

## 6. デモ実行コマンド早見表

```bash
# シナリオ A: OSPF タイマーミスマッチ（推奨）
agentic-ni-demo --scenario ospf-timer --scripted

# シナリオ B: 物理リンク断
agentic-ni-demo --scenario link-down --scripted

# リアル LLM モード（.env 設定済みの場合）
agentic-ni-demo --scenario ospf-timer

# 最新レポートを確認
cat $(ls -t reports/demo-*.md | head -1)
```

---

## 7. バックアップ計画

| リスク | バックアップ |
|--------|------------|
| API キーが使えない | `--scripted` モードに切り替え（事前に動作確認済み） |
| 端末出力が見づらい | ターミナルのフォントを 14pt 以上に設定、カラーテーマを Dark に |
| 実行エラー | `python -m agentic_ni.distributed.demo_runner --scripted` を直接実行 |
| 時間超過 | シナリオ A のみ実施（1分以内）、シナリオ B は質疑応答で紹介 |
