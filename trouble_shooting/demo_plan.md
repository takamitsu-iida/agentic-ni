# デモンストレーション計画書
## AIエージェントコンテスト出品：自律分散型ネットワーク AI エージェント

---

## 1. デモのコンセプト

### 一言で言うと
> 「ネットワーク障害が起きた瞬間、AI エージェントたちが自律的に会話して、人間より先に根本原因を突き止める」

### 審査員に訴えるポイント

| # | おぉポイント | 演出 |
|---|------------|------|
| 1 | **並行稼働** — 5台の AI が同時起動 | Spine/Leaf エージェントが一斉起動する様子 |
| 2 | **自律調査** — `show` コマンドを AI が自ら実行 | `⚙ run_show(command='show ip bgp summary')` のリアルタイム表示 |
| 3 | **自然言語対話** — エージェントが互いに報告・依頼 | `Agent-Leaf3 → Agent-Spine2:「BGP セッションが Down しています」` |
| 4 | **クロスチェック** — 複数エージェントが独立して同じ根本原因を特定 | Spine1/Spine2 が 2 方向から Leaf2 障害を確認 |
| 5 | **構造化レポート** — 復旧コマンドまで自動生成 | 🚨 HUMAN エスカレーション ボックス |

---

## 1-B. 新コンセプト：Ubuntu ノード + SYSLOG 受信方式

### 背景・課題

現行の `agentic-ni-watch`（CMLStateWatcher）はリンクダウン・ノードダウンの 2 種類しか検知できず、デモとして説得力に欠ける。

### 新アーキテクチャ

```text
[ CML 内仮想ネットワーク ]

  [R1] ─── [R2] ─── [R3]      ← IOS ルーター
   |          |        |
   └──────────┴────────┘
         SYSLOG UDP 514 ↓
   ┌────────────────────────────┐
   │  Ubuntu ノード             │
   │                           │
   │  rsyslog（SYSLOG 受信）   │
   │       ↓ ブロードキャスト  │
   │  ┌────────────────────┐   │
   │  │ Agent-R1           │   │
   │  │ Agent-R2           │   │
   │  │ Agent-R3           │   │
   │  └────────┬───────────┘   │
   │           │ InMemory Bus  │
   └───────────┼───────────────┘
               │ SSH / pyATS
  [R1] ←───────┘
  [R2] ←───────┘
  [R3] ←───────┘
```

### 旧方式との比較

| 項目 | 旧（CMLStateWatcher） | 新（Ubuntu + SYSLOG） |
|------|----------------------|---------------------|
| イベント検知 | CML API ポーリング（3秒間隔） | リアルタイム SYSLOG 受信 |
| 対応イベント種別 | リンクダウン・ノードダウンのみ | SYSLOG 全種別（BGP/OSPF/IF/認証/etc.） |
| 実機らしさ | 合成 syslog を注入 | 装置が実際に送信した SYSLOG をそのまま受信 |
| CML 依存 | CML API 必須 | CML API 不要（装置への IP 疎通のみ） |
| デモ説得力 | 限定的 | 実際の運用に近く高い |

### 実現するデモシナリオ（新方式）

| シナリオ | 障害操作 | 検知 SYSLOG | 説得力ポイント |
|----------|---------|------------|-------------|
| BGP ネイバーダウン | `clear ip bgp * soft` | `%BGP-5-ADJCHANGE` | BGP は CML API では検知不可能 |
| OSPF ネイバーロスト | `shutdown`（仮想リンク） | `%OSPF-5-ADJCHG` | OSPF 再収束を AI が診断 |
| インターフェースエラー | CML で shutdown 設定 | `%LINK-3-UPDOWN` | 従来と同じだが検知経路がリアル |
| 認証失敗（ログイン攻撃） | 失敗ログイン試行 | `%SEC_LOGIN-4-LOGIN_FAILED` | セキュリティイベントも対応可能 |

### 事前設定（ネットワーク装置側 — 全装置に適用）

```
logging host <ubuntu_ip>
logging trap informational
service timestamps log datetime msec
```

### CML トポロジーへの Ubuntu ノード追加

topology.yaml に Ubuntu ノード（alpine / server ノード定義）を追加し、
全ルーターと管理セグメントで疎通できるよう接続する。

```yaml
nodes:
  - id: "n99"
    label: "Ubuntu"
    node_definition: "ubuntu"
    # 全ルーターと同一セグメントに接続
```

---

## 2. シナリオ一覧（CLOS 構成）

| シナリオ | 構成 | 障害種別 | 検知経路 |
|----------|------|---------|----------|
| **F: BGP ネイバーダウン** | Spine2 + Leaf3 | `clear ip bgp` | `%BGP-5-ADJCHANGE` SYSLOG |
| **G: Spine リンク断** | Spine1 + Leaf1/2/3 | CML リンク停止 | `%LINK-3-UPDOWN` SYSLOG |
| **H: Leaf ノード停止** | Leaf2 | CML ノード停止 | `%OSPF-5-ADJCHG` SYSLOG |

---

## 3. シナリオ詳細（CLOS 構成）

### シナリオ F: BGP ネイバーダウン

**ネットワーク構成**
```
        [Spine1]        [Spine2]
        /  |  \         /  |  \
   [Leaf1][Leaf2][Leaf3]        ← Spine2-Leaf3 BGP が Down
         |
      [Ubuntu]──[Internet]
```

**障害発生方法**
```
Leaf3# clear ip bgp <Spine2_IP> soft
```

**AI が行うこと**
1. Ubuntu が `%BGP-5-ADJCHANGE` SYSLOG を受信 → Agent-Leaf3 へ配信
2. Agent-Leaf3 が `show ip bgp summary` を実行して Idle 状態を確認
3. Agent-Spine2 に問い合わせ → Spine2 側の BGP テーブルをクロスチェック
4. HUMAN エスカレーション：BGP セッション再確立の手順を提示

---

### シナリオ G: Spine リンク断

**障害発生方法（CML 操作）**
```
PUT /api/v0/labs/{lab_id}/links/l0/state  {"state": "stopped"}
```
（l0 = Spine1-Leaf1 リンク）

**AI が行うこと**
1. Ubuntu が `%LINK-3-UPDOWN` SYSLOG を受信 → Agent-Spine1 と Agent-Leaf1 へ配信
2. 両エージェントが独立して DOWN を確認（クロスチェック）
3. Agent-Leaf1 が Spine2 経由の迂回経路で継続稼働中と報告
4. HUMAN エスカレーション：CML 復旧コマンドを自動生成

---

### シナリオ H: Leaf ノード停止

**障害発生方法（CML 操作）**
```
PUT /api/v0/labs/{lab_id}/nodes/n3/state  {"state": "stopped"}
```
（n3 = Leaf2）

**AI が行うこと**
1. Ubuntu が Spine1/Spine2 からの `%OSPF-5-ADJCHG` / `%BGP-5-ADJCHANGE` を受信
2. Agent-Spine1 と Agent-Spine2 が独立して Leaf2 消失を確認（2方向クロスチェック）
3. HUMAN エスカレーション：ノード再起動コマンドを提示

---

## 4. デモ実行手順

### 前提条件
```bash
uv sync    # pyATS/Genie を含む全依存をインストール
```

> **注意**: `/tmp` の書き込み権限がない実行環境では以下を使用してください:
> ```bash
> mkdir -p ~/tmp_uv && TMPDIR=~/tmp_uv uv sync
> ```

`.env` に以下を設定しておくこと（CML 接続用）:
```
CML_URL=https://<CML_HOST>
CML_USERNAME=<USER>
CML_PASSWORD=<PASS>
CML_VERIFY_SSL=false   # 自己署名証明書の場合
```

### Ubuntu ノード方式（CLOS 構成 — **本番推奨**）

**① CML ラボを作成・起動する（初回のみ）**
```bash
uv run agentic-ni-lab deploy --config clos
# → Lab ID が表示される（例: lab_id=abc123...）
```

**② Ubuntu ノード上でエージェントを起動する**
```bash
# Ubuntu ノードにログインして実行
# testbed.yaml は各装置の管理IP・SSH認証情報を記述するファイル（要手動作成）
sudo agentic-ni-ubuntu \
    --topology trouble_shooting/configs/clos/topology.yaml \
    --testbed testbed.yaml
# → エージェント 5 台（Spine1/2, Leaf1/2/3）が起動し、SYSLOG 受信待機
# → testbed.yaml を省略すると SYSLOG 受信のみ（show コマンド不可）
```

**③ 障害を発生させる（CML GUI または CLI）**
```bash
# シナリオ G: Spine1-Leaf1 リンク断
uv run agentic-ni-lab fault --title agentic-ni-clos --link l0 --down

# シナリオ H: Leaf2 ノード停止
uv run agentic-ni-lab fault --title agentic-ni-clos --node n3 --down
```
→ Ubuntu の SYSLOG サーバーが受信すると、対応エージェントが自律診断を開始する。

**④ 障害復旧（デモ後）**
```bash
uv run agentic-ni-lab fault --title agentic-ni-clos --link l0       # リンク復旧
uv run agentic-ni-lab fault --title agentic-ni-clos --node n3      # ノード復旧
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
> 今日は、CML 上の IP CLOS 構成で、この診断を AI に任せる世界をお見せします。」

**スライド**: 従来の障害対応フロー（アラート→手動SSH→コマンド実行→原因特定→修復）

### アーキテクチャ説明（1 分）
> 「CML 内の Ubuntu ノードが各装置から SYSLOG を受信します。
> SYSLOG が届いた瞬間、該当装置の AI エージェントが自律的に調査を開始し、
> 隣接エージェントと協調して根本原因を特定します。」

**スライド**: 1-B のアーキテクチャ図（Ubuntu + SYSLOG ブロードキャスト）

### ライブデモ — シナリオ G: Spine リンク断（4 分）

**[事前操作]** Ubuntu ノード上でエージェントを起動しておく

**画面に映るもの（逐次解説）**:

```
[1] 5 台エージェント 起動中...
✓ Agent-Spine1  (router)  隣接: Agent-Leaf1, Agent-Leaf2, Agent-Leaf3
✓ Agent-Spine2  (router)  隣接: Agent-Leaf1, Agent-Leaf2, Agent-Leaf3
✓ Agent-Leaf1   (router)  隣接: Agent-Spine1, Agent-Spine2
...
SyslogServer: UDP 0.0.0.0:514 でリッスン開始
```
> 「topology.yaml を読み込んだだけで、5台分の AI が一斉に自動起動します」

```
[2] CML 障害発生 — Spine1-Leaf1 リンク断
SYSLOG受信 [192.168.100.2] Spine1: %LINK-3-UPDOWN: Interface Gi0/0 changed to down
SYSLOG受信 [192.168.100.3] Leaf1:  %LINK-3-UPDOWN: Interface Gi0/0 changed to down
```
> 「Spine1 と Leaf1 の両方から SYSLOG が飛んできました。CML API に依存せず、装置が送信した生の SYSLOG を直接受け取っています」

```
[3] エージェント自律診断開始
0.1s  Agent-Spine1  ⚙  run_show(command='show ip bgp summary')
0.2s  Agent-Leaf1   ⚙  run_show(command='show ip bgp summary')
0.3s  Agent-Spine1 → Agent-Leaf1
      │ Gi0/0 が DOWN。そちらの BGP 状態を確認してください。
0.5s  Agent-Leaf1 → Agent-Spine1
      │ Spine1 との BGP セッションが Idle。Spine2 経由で継続稼働中。
0.6s  Agent-Spine1 📡 ALL
      │ 【診断確定】Spine1-Leaf1 リンク断。迂回経路（Spine2）で継続稼働中。
0.6s  Agent-Spine1 🚨 HUMAN
      │ 根本原因: l0 リンク断  復旧: PUT .../links/l0/state {"state": "started"}
```
> **「Spine1 と Leaf1 が互いにクロスチェック。リンク断を自律確定し、復旧コマンドまで提示しました」**

### ライブデモ — シナリオ F: BGP ネイバーダウン（3 分）

> 「次は CML API では絶対に検知できない BGP イベントです」

```bash
# 別ターミナルから実行
Leaf3# clear ip bgp <Spine2_IP>
```

```
SYSLOG受信 [x.x.x.x] Leaf3: %BGP-5-ADJCHANGE: neighbor 10.2.13.1 Down
SYSLOG受信 [x.x.x.x] Spine2: %BGP-5-ADJCHANGE: neighbor 10.2.13.2 Down
0.1s  Agent-Leaf3  ⚙  run_show(command='show ip bgp summary')
0.2s  Agent-Leaf3 → Agent-Spine2
      │ BGP セッションが Down。そちらの状態を確認してください。
🚨  HUMAN エスカレーション ← Agent-Leaf3
根本原因: Leaf3-Spine2 間 BGP セッション断
推奨対応: clear ip bgp <IP> または neighbor <IP> activate 確認
```
> **「BGP のような動的プロトコルイベントも、SYSLOG さえ飛べば即座に AI が診断します」**

### クロージング（1 分）
> 「このシステムの特長は 3 つです。
> 1) Ubuntu が SYSLOG を受け取るだけで、BGP・OSPF・IF エラーなど全種別に対応
> 2) 読み取り専用ツールのみで安全。設定変更は必ず人間が承認
> 3) topology.yaml のノード数に比例してエージェントが自動スケール」

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
# ── CML ラボ管理（CLOS 構成） ─────────────────────────────────────────
# ラボ作成・起動（CLOS = Spine×2 + Leaf×3 + Ubuntu）
uv run agentic-ni-lab deploy --config clos

# CML 上のラボ一覧確認
uv run agentic-ni-lab list

# ノード状態確認
uv run agentic-ni-lab status --title agentic-ni-clos

# ラボ削除
uv run agentic-ni-lab delete --title agentic-ni-clos

# ── 本番デモ（Ubuntu ノード方式 — 推奨） ─────────────────────────────
# Ubuntu ノード上でエージェント起動（UDP 514 で SYSLOG 受信）
# testbed.yaml = 各装置の管理IP + SSH認証情報を記述したユーザー作成ファイル
sudo agentic-ni-ubuntu \
    --topology trouble_shooting/configs/clos/topology.yaml \
    --testbed testbed.yaml  # 省略時は SYSLOG 受信のみ・ show コマンド不可

# シナリオ G: Spine1-Leaf1 リンク断
uv run agentic-ni-lab fault --title agentic-ni-clos --link l0 --down
uv run agentic-ni-lab fault --title agentic-ni-clos --link l0              # 復旧

# シナリオ H: Leaf2 ノード停止
uv run agentic-ni-lab fault --title agentic-ni-clos --node n3 --down
uv run agentic-ni-lab fault --title agentic-ni-clos --node n3              # 復旧

# ── スクリプトモード（CML 不要 / オフライン確認用） ───────────────────
# BGP ネイバーダウンを模擬（SYSLOG 手動送信）
echo '<189>Aug 10 12:34:56 Leaf3 %BGP-5-ADJCHANGE: neighbor 10.2.13.1 Down Interface flap' | \
    nc -u -w1 <ubuntu_ip> 5140

# OSPF ネイバーロストを模擬
echo '<190>Aug 10 12:35:10 Spine1 %OSPF-5-ADJCHG: Process 1, Nbr 10.1.11.2 on Gi0/0 from FULL to DOWN' | \
    nc -u -w1 <ubuntu_ip> 5140

# ── Ubuntu ノード方式（新コンセプト） ────────────────────────────────
# Ubuntu ノード上で起動（UDP 514 で SYSLOG を受信、pyATS で直接 SSH）
# ※ UDP 514 は root 権限が必要
sudo agentic-ni-ubuntu \
    --topology configs/demo2/topology.yaml \
    --testbed testbed.yaml

# テスト用（非特権ポート + モックツール、root 不要）
uv run agentic-ni-ubuntu \
    --topology configs/demo2/topology.yaml \
    --syslog-port 5140 \
    --mock-tools

# SYSLOG 手動送信（動作確認用、別ターミナルから）
# BGP ネイバーダウンを模擬
echo '<189>Aug 10 12:34:56 R1 %BGP-5-ADJCHANGE: neighbor 10.0.0.2 Down Interface flap' | \
    nc -u -w1 <ubuntu_ip> 5140

# OSPF ネイバーロストを模擬
echo '<190>Aug 10 12:35:10 R2 %OSPF-5-ADJCHG: Process 1, Nbr 10.0.0.1 on Gi0/0 from FULL to DOWN' | \
    nc -u -w1 <ubuntu_ip> 5140

# ── 共通 ──────────────────────────────────────────────────────────────
# 最新レポートを確認
cat $(ls -t reports/demo*.md | head -1)
```

---

## 8. バックアップ計画

| リスク | バックアップ |
|--------|------------|
| API キーが使えない | Ollama（ローカル LLM）に切り替え |
| CML が起動しない | `--syslog-port 5140 --mock-tools` で nc コマンドによる SYSLOG 手動注入で実演 |
| 端末出力が見づらい | ターミナルのフォントを 14pt 以上に設定、カラーテーマを Dark に |
| 実行エラー | `uv run agentic-ni-ubuntu --topology ... --syslog-port 5140 --mock-tools` で再試行 |
| 時間超過 | シナリオ G のみ実施（リンク断 → 2方向クロスチェック → HUMAN レポート） |
