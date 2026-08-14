# ネットワークトラブルシューティングガイド（分散エージェント向け）

このガイドは、分散型トラブルシューティングエージェントが障害を自律的に診断するための実践的な知見をまとめたものです。
SYSLOG パターンの解釈・調査コマンドの選択・根本原因の推定に活用してください。

---

## 1. SYSLOG パターンと初動調査コマンド

### 1-1. リンクダウン

```
%LINK-3-UPDOWN: Interface GigabitEthernet0/1, changed state to down
%LINEPROTO-5-UPDOWN: Line protocol on Interface GigabitEthernet0/1, changed state to down
```

**意味**: 物理リンクまたは L2 接続が切断された。

**初動調査コマンド（優先順）**:
```
show interfaces GigabitEthernet0/1
show ip interface brief
show ip route
show ip ospf neighbor
```

**根本原因の切り分け**:
- `show interfaces GigabitEthernet0/1` で `line protocol is down` かつ `Hardware is down` → ケーブル断・対向装置の電源断
- `line protocol is down` かつ `Hardware is up` → L2 ネゴシエーション失敗（速度・duplex ミスマッチ、またはキープアライブ不一致）
- `administratively down` → `shutdown` コマンドが設定されている（意図的か確認）

**影響の伝播**: リンクダウンは同一リンクの両端で同時に SYSLOG が発生することが多い。
片方だけ落ちている場合は一方向断（光ファイバーの送受信逆接など）を疑う。

---

### 1-2. OSPF ネイバーダウン

```
%OSPF-5-ADJCHG: Process 1, Nbr 2.2.2.2 on GigabitEthernet0/1 from FULL to DOWN, Neighbor Down: Dead timer expired
%OSPF-5-ADJCHG: Process 1, Nbr 2.2.2.2 on GigabitEthernet0/1 from FULL to DOWN, Neighbor Down: Interface down or detached
```

**意味**: OSPF 隣接関係が切断された。

**Dead timer expired** の場合:
- Hello パケットが届いていない（リンク品質劣化・帯域不足・CPU 高負荷）
- Hello/Dead タイマーの不一致（デフォルト: Hello=10s, Dead=40s）
```
show ip ospf interface GigabitEthernet0/1   ← タイマー値を確認
show ip ospf neighbor detail                ← Dead time の残り時間を確認
```

**Interface down or detached** の場合:
- 物理リンクダウンが原因。`%LINK-3-UPDOWN` と同時に出る場合はリンク断が根本原因。

**調査コマンド**:
```
show ip ospf neighbor
show ip ospf neighbor detail
show ip ospf interface
show ip route ospf
show ip ospf database
```

**確認ポイント**:
- `show ip ospf neighbor` でネイバーが消えている場合、そのインターフェースの状態を確認
- `show ip route ospf` で経路が消失しているか確認（迂回経路に切り替わっている可能性あり）
- `show ip ospf database` でルーター LSA が残っている場合、まだ SPF 再計算中の可能性

---

### 1-3. BGP セッション断

```
%BGP-5-ADJCHANGE: neighbor 2.2.2.2 Down BGP Notification received
%BGP-5-ADJCHANGE: neighbor 2.2.2.2 Down Interface flap
%BGP-5-ADJCHANGE: neighbor 2.2.2.2 Down Holding time expired
%BGP-3-NOTIFICATION: sent to neighbor 2.2.2.2 4/0 (hold time expired) 0 bytes
```

**意味**: BGP ピアリングが切断された。

**原因別の切り分け**:

| SYSLOG の末尾 | 疑われる原因 |
|---|---|
| `Interface flap` | 下位レイヤー（物理・OSPF）の問題が根本原因 |
| `Hold time expired` | ルートの到達性断（iBGP の場合は OSPF ネイバー断が先行） |
| `BGP Notification received` | 対向ルーターが切断を通知（エラーコードを確認） |
| `Peer closed the session` | 対向ルーターの BGP プロセス再起動 |

**調査コマンド**:
```
show ip bgp summary
show ip bgp neighbors 2.2.2.2
show ip route 2.2.2.2              ← iBGP の場合、ピア Loopback への到達性確認
show ip bgp neighbors 2.2.2.2 received-routes
```

**iBGP セッション断の場合の追加確認**:
iBGP は Loopback アドレスでピアリングすることが多い。
Loopback への経路（= OSPF 経路）が失われると BGP も落ちる。
```
show ip route 2.2.2.2              ← O (OSPF) ルートがあるか確認
show ip ospf neighbor              ← OSPF ネイバーが残っているか確認
```

---

### 1-4. CPU 高負荷・メモリ不足

```
%SYS-3-CPUHOG: Task is running for 2000 msec
%SYS-2-MALLOCFAIL: Memory allocation of 65536 bytes failed
```

**調査コマンド**:
```
show processes cpu sorted
show processes memory sorted
show version                       ← 搭載メモリ確認
```

**確認ポイント**:
- `show processes cpu sorted` で上位プロセスを確認
- `IP Input` や `OSPF Router` プロセスが高い場合、ルーティングプロトコルのフラッピングや経路数増加を疑う

---

## 2. 障害パターンと根本原因の推定

### パターン A: 片側リンクダウン → プロトコル断

```
[発生順序]
1. %LINK-3-UPDOWN: Interface GigabitEthernet0/1 → down (R1 から)
2. %OSPF-5-ADJCHG: Nbr 2.2.2.2 → DOWN (R1 から)
3. %BGP-5-ADJCHANGE: neighbor 2.2.2.2 Down Interface flap (R1 から)
```

**根本原因**: R1-R2 間の物理リンク断。
**推定対応**: リンクの物理確認・CML ではリンク状態を `started` に戻す。

**調査の優先順位**: まず物理層（インターフェース状態）を確認し、L3 プロトコルはその後。

---

### パターン B: 両端同時 OSPF ダウン

```
R1: %OSPF-5-ADJCHG: Nbr 2.2.2.2 from FULL to DOWN
R2: %OSPF-5-ADJCHG: Nbr 1.1.1.1 from FULL to DOWN
```

両端が同時に SYSLOG を出している場合、リンクそのものの断またはケーブル断。
片方だけ出ている場合は、一方向断または片側の設定変更（`shutdown` など）。

---

### パターン C: OSPF 断のみ（物理リンクは UP）

```
%OSPF-5-ADJCHG: ... Dead timer expired
(物理リンクの UPDOWN ログなし)
```

**疑われる原因**:
1. Hello/Dead タイマーの不一致（片方だけ変更した場合）
2. ネットワークタイプ不一致（一方が `broadcast`、他方が `point-to-point`）
3. Area ID の不一致
4. Authentication の不一致
5. MTU 不一致

**確認コマンド**:
```
show ip ospf interface GigabitEthernet0/1
! → Timer intervals configured, Hello 10, Dead 40 の値を両端で比較
! → Network Type POINT_TO_POINT / BROADCAST を両端で比較
! → Message digest authentication Enabled/Disabled を確認
```

---

### パターン D: 一部経路のみ消失

```
R3: %OSPF-5-ADJCHG: Nbr 1.1.1.1 from FULL to DOWN
```

R3 から R1 への経路が消えたが、R3-R2 間は生きている場合、R2 経由の迂回経路に切り替わる。

**調査**:
```
show ip route 1.1.1.1            ← 経由先が変わっているか確認
show ip route ospf               ← メトリックと経路が正しいか確認
traceroute 1.1.1.1 source Lo0   ← 実際の転送パスを確認
```

---

### パターン E: 設定変更後の問題

SYSLOG に `%SYS-5-CONFIG_I` が先行して現れた後に障害ログが続く場合、設定変更が原因の可能性が高い。

```
%SYS-5-CONFIG_I: Configured from console by admin
%OSPF-5-ADJCHG: ... from FULL to DOWN
```

**確認コマンド**:
```
show running-config | section router ospf
show running-config | section interface GigabitEthernet
show ip ospf interface
```

---

## 3. 調査コマンドの選び方（症状別クイックリファレンス）

| 症状 | 最初に実行するコマンド | 次に確認するコマンド |
|---|---|---|
| 特定のホストに到達できない | `show ip route <dst>` | `ping <dst> source <intf>`, `traceroute <dst>` |
| OSPF ネイバーが消えた | `show ip ospf neighbor` | `show ip ospf interface`, `show interfaces` |
| BGP セッションが落ちた | `show ip bgp summary` | `show ip route <peer_loopback>`, `show ip ospf neighbor` |
| インターフェースが down | `show interfaces <intf>` | `show ip interface brief`, `show ip route` |
| ルーティングテーブルが想定と違う | `show ip route` | `show ip ospf database`, `show ip bgp` |
| 間欠的な疎通断 | `show interfaces <intf>` (input/output errors 確認) | `show logging` |

---

## 4. RCA（根本原因分析）の思考フレームワーク

### レイヤー順に確認する

```
L1 物理: インターフェース状態 (show interfaces → line protocol / hardware)
L2 データリンク: エラーカウンター (input errors, CRC, duplex mismatch)
L3 ネットワーク: ルーティング (show ip route, show ip ospf neighbor)
L4 以上: BGP, アプリケーション到達性
```

上位レイヤーの障害は、ほぼ必ず下位レイヤーに原因がある。
BGP が落ちているなら OSPF を、OSPF が落ちているなら物理リンクを先に確認する。

---

### 「最後に変わったもの」を探す

障害の直前に以下のイベントがなかったか確認する:
```
show logging | include CONFIG_I    ← 設定変更履歴
show logging | include UPDOWN      ← インターフェース状態変化
show logging | include ADJCHG      ← ルーティングプロトコルの隣接変化
```

---

### 影響範囲の特定

```
1. 障害の起点となった装置・リンクを特定する
2. そのリンクが切れた場合に影響を受ける装置・経路を列挙する
3. 実際に影響が出ている装置と一致するか確認する
```

複数の装置で同時に OSPF/BGP ダウンが起きている場合、
共通のリンク・装置（コアルーターなど）が根本原因である可能性が高い。

---

## 5. Cisco IOSv 固有の注意事項

### 使用してはいけないコマンド

```
show logs          → 無効。show logging を使うこと
show interfaces status → IOSv では非対応。show ip interface brief を使うこと
show tech-support  → 出力が大きすぎる。具体的なサブコマンドを使うこと
ping <ip>/24       → プレフィクス指定は不可
show ip route <ip>/24 → プレフィクス指定は不可
```

### パイプ（|）の使い方

```
show logging | include OSPF    ← 動作する
show logging | include OSPF|BGP ← 動作しない場合がある（単一パターンのみ）
```

パイプが動作しない場合は `show logging` で全体を取得して確認する。

### インターフェース up にするための設定

IOSv のインターフェースはデフォルトで `shutdown` 状態のため、必ず確認する:

```
show ip interface brief          ← administratively down が出ていないか確認
```

`administratively down` の場合は `no shutdown` が必要（設定変更のため Human 承認が必要）。
