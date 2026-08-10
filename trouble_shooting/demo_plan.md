# デモンストレーション計画書
## AIエージェントコンテスト出品：自律分散型ネットワーク AI エージェント

---

## 1. デモのコンセプト

### 一言で言うと
> 「ネットワーク障害が起きた瞬間、AI エージェントたちが自律的に会話して、人間より先に根本原因を突き止める」

### 審査員に訴えるポイント

| # | おぉポイント | 演出 |
|---|------------|------|
| 1 | **並行稼働** — 10台の AI が同時起動 | 10台のエージェントが一斉起動する様子 |
| 2 | **自律調査** — `show` コマンドを AI が自ら実行 | `⚙ run_show(command='show ip ospf neighbor')` のリアルタイム表示 |
| 3 | **自然言語対話** — エージェントが互いに報告・依頼 | `Agent-R1 → Agent-R2:「R4 の状態を確認してください」` |
| 4 | **クロスチェック** — 複数エージェントが独立して同じ根本原因を特定 | 4方向確認で「R4 停止」を確定 |
| 5 | **構造化レポート** — CML 復旧コマンドまで自動生成 | 🚨 HUMAN エスカレーション ボックス |

---

## 2. シナリオ一覧

| シナリオ | ノード数 | 障害種別 | CML 操作 | コマンド |
|----------|---------|---------|---------|----------|
| A: OSPF タイマーミスマッチ | 2台 | 設定ミス | なし | `--scenario ospf-timer` |
| B: 物理リンク断 | 2台 | リンク停止 | なし | `--scenario link-down` |
| C: 二重リンク断 + 攻撃エージェント | 3台 | 二重リンク断 | なし | `--scenario attack-dual` |
| **D: CML コアリンク停止** | **10台** | **CML リンク停止** | `l0 state=stopped` | `--scenario core-link-down` |
| **E: CML ノード停止** | **10台** | **CML ノード停止** | `n3 state=stopped` | `--scenario node-failure` |

---

## 3. シナリオ詳細

### シナリオ A: OSPF Hello タイマーミスマッチ（2台）

**ストーリーライン**
1. 夜間バッチ後に OSPF ネイバーが消失（syslog が飛ぶ）
2. Agent-R1 が自装置を調査 → 物理リンクは UP、タイマー 10 秒
3. Agent-R1 が Agent-R2 に問い合わせ
4. Agent-R2 が調査 → タイマー 30 秒（**ここが犯人**）
5. Agent-R1 がクロスチェック → ミスマッチを自律検出
6. 管理者に `ip ospf hello-interval 10` コマンドを提案

**所要時間**: 約 **1.6 秒**（手動診断の平均 30〜120 分との対比）

---

### シナリオ D: CML コアリンク停止 — 10台構成（**新規推奨**）

**ネットワーク構成**
```
Core:         [R1]────l0────[R2]   ← このリンクを CML で停止
             /  | \        / | \
Distribution:[R3] [R4]  [R5]
             / \   / \     \
Access:    [R6][R7] [R8][R9] [R10]
```

**障害発生方法（CML 操作）**
```
PUT /api/v0/labs/{lab_id}/links/l0/state  {"state": "stopped"}
```

**AI が行うこと**
1. Agent-R1 が Gi0/0 DOWN の syslog を受信 → show コマンドを自律実行
2. Agent-R2 に直接問い合わせ → R2 の Gi0/0 も DOWN を独立確認
3. 診断確定 → 全エージェントへ ALL ブロードキャスト
4. Agent-R3 が迂回経路で継続稼働中と報告
5. HUMAN エスカレーション：CML 復旧コマンドを自動生成

**確認される「おぉ」ポイント**: R1 と R2 が互いに独立して同じリンク断を確認

**所要時間**: 約 **2.1 秒**

---

### シナリオ E: CML ディストリビューションルータ停止 — 10台構成（**新規推奨**）

**ネットワーク構成**
```
Core:         [R1]────────[R2]
             /  | \       / | \
Distribution:[R3] [R4] [R5]   ← R4 を CML で停止
              / \   / \   \
Access:    [R6][R7] [R8][R9] [R10]
                    ↑    ↑
               完全孤立  完全孤立
```

**障害発生方法（CML 操作）**
```
PUT /api/v0/labs/{lab_id}/nodes/n3/state  {"state": "stopped"}
```

**AI が行うこと（4方向クロスチェック）**
- **R1 syslog** → Agent-R1 が OSPF 消失・Gi0/2 DOWN 確認 → Agent-R2 に問い合わせ
- **R8 syslog** → Agent-R8 が孤立を検出 → ALL ブロードキャスト「R8 完全孤立」
- **R9 syslog** → Agent-R9 が独立して同様にブロードキャスト「R9 完全孤立」
- Agent-R2 が独立調査 → R4 消失を確認 → Agent-R1 へ報告
- Agent-R1 が4方向のクロスチェックを統合 → R4 ノード停止と確定
- HUMAN エスカレーション：CML 再起動コマンドを優先度付きで提示

**確認される「おぉ」ポイント**: R1/R2（コア視点）と R8/R9（アクセス視点）が同時並行で同一障害を指差す

**所要時間**: 約 **2.0 秒**

---

## 4. デモ実行手順

### 前提条件
```bash
uv sync  # または pip install -e .
```

`.env` に以下を設定しておくこと（CML 接続用）:
```
CML_URL=https://<CML_HOST>
CML_USERNAME=<USER>
CML_PASSWORD=<PASS>
CML_VERIFY_SSL=false   # 自己署名証明書の場合
```

### シナリオ D（10台 — CML コアリンク停止）

**① ラボを作成・起動する（初回のみ）**
```bash
agentic-ni-lab deploy --config demo-large
# → Lab ID が表示される（例: lab_id=abc123...）
```

**② コアリンク l0（R1-R2 間）を停止して障害を注入する**
```bash
agentic-ni-lab fault --lab-id <lab_id> --link l0 --down
```

**③ AI デモを実行する**
```bash
agentic-ni-demo --scenario core-link-down --scripted
```

**④ 障害復旧（デモ後）**
```bash
agentic-ni-lab fault --lab-id <lab_id> --link l0
```

### シナリオ E（10台 — CML ノード停止）
```bash
# CML で n3 (R4) ノードを停止した後に実行
agentic-ni-demo --scenario node-failure --scripted
```

### シナリオ A（2台 — スクリプトモード）
```bash
agentic-ni-demo --scenario ospf-timer --scripted
```

### シナリオ A（リアル LLM モード）
```bash
# .env に LLM_PROVIDER と API キーを設定済みの場合
agentic-ni-demo --scenario ospf-timer
```

### シナリオ B（2台 — 物理リンク断）
```bash
agentic-ni-demo --scenario link-down --scripted
```

### 終了後のレポート確認
```bash
cat $(ls -t reports/demo*.md | head -1)
```

---

## 5. プレゼンテーション台本（10 分）

### オープニング（1 分）
> 「ネットワーク障害の平均修復時間は 4 時間以上です。
> その大半が『どの装置が原因か』を特定する時間です。
> 今日は、CML 上の 10 台構成で、この診断を AI に任せる世界をお見せします。」

**スライド**: 従来の障害対応フロー（アラート→手動SSH→コマンド実行→原因特定→修復）

### アーキテクチャ説明（1 分）
> 「各ネットワーク装置の隣に、専用の AI エージェントを配置します。
> エージェント同士はメッセージバスで会話し、人間の指示なしに協調して動きます。」

**スライド**: concept_design.md のアーキテクチャ図（サイドカー方式）

### ライブデモ — シナリオ D: CML コアリンク停止（4 分）

**[事前操作]** CML コンソールでリンク l0（R1-R2 間）を停止しておく

```bash
agentic-ni-demo --scenario core-link-down --scripted
```

**画面に映るもの（逐次解説）**:

```
[1] 10 台エージェント 起動中...
✓ Agent-R1  (router)  隣接: Agent-R2, Agent-R3, Agent-R4, Agent-R5  ツール: 4 個
✓ Agent-R2  (router)  隣接: Agent-R1, Agent-R3, Agent-R4, Agent-R5  ツール: 4 個
...（全 10 台）
```
> 「topology.yaml を読み込んだだけで、10台分の AI が一斉に自動起動します」

```
[2] CML 障害発生 — 複数 syslog 注入
⚠️  %LINK-3-UPDOWN: Interface GigabitEthernet0/0, changed state to down
```
> 「CML でコアリンクを停止した瞬間、R1 に syslog が飛んできました」

```
[3] 10 台エージェント 自律診断開始
1.4s  Agent-R1  ⚙  run_show(command='show ip ospf neighbor')
1.4s  Agent-R1  ⚙  get_interface_status()
1.4s  Agent-R1 → Agent-R2
      │ R1-R2 間コアリンク（Gi0/0）が DOWN。そちらの状態を確認してください。
1.4s  Agent-R2  ⚙  get_interface_status()
1.4s  Agent-R2 → Agent-R1
      │ R2 の Gi0/0 も DOWN を確認。コアリンク断と判断します。
1.4s  Agent-R1 📡 ALL
      │ 【診断確定】コアリンク（l0）断。R2 が独立確認。迂回経路で継続稼働中。
```
> 「R2 が独立して確認。クロスチェック完了。全エージェントへ診断結果を通知します」

```
🚨  HUMAN エスカレーション  ←  Agent-R1
根本原因: CML リンク l0（R1 Gi0/0 ↔ R2 Gi0/0）の停止
推奨対応: PUT /api/v0/labs/{lab_id}/links/l0/state {"state": "started"}
✅  診断完了  |  診断所要時間: 2.1 秒
```
> **「10台のネットワーク、コアリンク障害を 2.1 秒で診断し、CML 復旧コマンドまで提示しました」**

### ライブデモ — シナリオ E: CML ノード停止（3 分、オプション）

**[事前操作]** CML コンソールでノード n3（R4）を停止しておく

```bash
agentic-ni-demo --scenario node-failure --scripted
```

**画面の見どころ**:
```
[3] 10 台エージェント 自律診断開始
1.4s  Agent-R1  ⚙  run_show(...)      ← コアから調査開始
1.4s  Agent-R8  ⚙  get_interface_status()   ← 同時並行で R8 も起動
1.4s  Agent-R9  ⚙  get_interface_status()   ← 同時並行で R9 も起動
1.4s  Agent-R8 📡 ALL
      │ 【孤立警告】R8 完全孤立。R4 への接続が切断されました。
1.4s  Agent-R9 📡 ALL
      │ 【孤立警告】R9 も完全孤立。R8 と同じ上位障害と判断。
1.4s  Agent-R2 → Agent-R1
      │ R4 の OSPF ネイバーが消失。R4 ノード停止と判断。
🚨  HUMAN エスカレーション
確認済み（4方向クロスチェック）:
  - Agent-R1/R2: コア側から R4 喪失を確認
  - Agent-R8/R9: アクセス側から孤立を自己報告
推奨対応: PUT .../nodes/n3/state {"state": "started"}
```
> 「R1/R2 がコア側から、R8/R9 がアクセス側から、4方向同時に R4 障害を証明します」

### クロージング（1 分）
> 「このシステムの特長は 3 つです。
> 1) topology.yaml を読むだけでエージェントが自動スケール（今日は 10 台）
> 2) 読み取り専用ツールのみで安全。設定変更は必ず人間が承認
> 3) CML の API と連携すれば、障害注入から診断・復旧コマンド生成まで完全自動化できます」

---

## 6. 想定 Q&A

| 質問 | 回答 |
|------|------|
| 実際のネットワーク装置で動く？ | `DeviceToolkit`（pyATS 経由）を使えば実機に SSH 接続して show コマンドを実行できます |
| 設定変更は AI が自動でやるの？ | `DEVICE_AGENT_READONLY=true`（デフォルト）で read-only のみ。変更は Human-in-the-Loop で管理者が承認してから実行 |
| エージェントが無限ループしない？ | ホップカウンター（上限 5）でループを検知・破棄する仕組みを実装済み |
| LLM のコストは？ | 1 インシデントあたり数回の LLM 呼び出し。ローカル LLM（Ollama）にも対応可能 |
| 大規模ネットワーク（100台以上）は？ | topology.yaml のノード数に比例してエージェントが起動。NATS を使えばブローカー負荷分散も可能 |
| CML との連携は？ | virl2_client 経由で CML API に接続。リンク停止・ノード停止・コンフィグ投入まで自動化可能 |

---

## 7. デモ実行コマンド早見表

```bash
# ── CML ラボ管理 ──────────────────────────────────────────────────────
# ラボ作成・起動（demo-large = 10台構成）
agentic-ni-lab deploy --config demo-large

# CML 上のラボ一覧確認
agentic-ni-lab list

# ノード状態確認
agentic-ni-lab status --lab-id <lab_id>

# ラボ削除
agentic-ni-lab delete --lab-id <lab_id>

# ── 10台構成（CML 環境 — 推奨） ───────────────────────────────────────
# シナリオ D: コアリンク停止
agentic-ni-lab fault --lab-id <lab_id> --link l0 --down
agentic-ni-demo --scenario core-link-down --scripted
agentic-ni-lab fault --lab-id <lab_id> --link l0          # 復旧

# シナリオ E: ディストリビューションルータ停止（CML で n3 を停止後に実行）
agentic-ni-demo --scenario node-failure --scripted

# ── 2〜3台構成（CML 不要） ────────────────────────────────────────────
# シナリオ A: OSPF タイマーミスマッチ
agentic-ni-demo --scenario ospf-timer --scripted

# シナリオ B: 物理リンク断
agentic-ni-demo --scenario link-down --scripted

# シナリオ C: 攻撃エージェント vs 防御エージェント
agentic-ni-demo --scenario attack-dual --scripted

# ── 共通 ──────────────────────────────────────────────────────────────
# リアル LLM モード（.env 設定済みの場合）
agentic-ni-demo --scenario core-link-down

# 最新レポートを確認
cat $(ls -t reports/demo*.md | head -1)
```

---

## 8. バックアップ計画

| リスク | バックアップ |
|--------|------------|
| API キーが使えない | `--scripted` モードに切り替え（事前に動作確認済み） |
| CML が起動しない | シナリオ A/B（CML 不要）に切り替え |
| 端末出力が見づらい | ターミナルのフォントを 14pt 以上に設定、カラーテーマを Dark に |
| 実行エラー | `python -m agentic_ni.distributed.demo_runner --scenario core-link-down --scripted` を直接実行 |
| 時間超過 | シナリオ D のみ実施（2分以内）、シナリオ E は質疑応答で紹介 |
