# CML ネットワーク設計パターンガイド

このガイドは Cisco Modeling Labs (CML 2.x) 環境での IOSv / iosvl2 ノードを使った
ネットワーク設計の定石パターンと注意事項をまとめたものです。

---

## 1. CML トポロジー YAML の基本ルール

### 必須フィールド

```yaml
lab:
  title: "ラボ名"
  version: "0.1.0"      # 必須。省略すると API エラー
  description: ""
  notes: ""
  timestamp: 0

nodes: [...]
links: [...]
```

### link の label は空文字不可

```yaml
links:
  - id: "l0"
    n1: "n0"
    i1: "i0"
    n2: "n1"
    i2: "i0"
    label: "l0"        # 空文字（""）は不可。id と同じ値を使うこと
```

### Loopback インターフェースはトポロジーに含めない

CML の `interfaces` セクションに `type: loopback` や `slot: -1` を含めると API エラーになる。
Loopback は機器コンフィグのみに記述する（トポロジー YAML には不要）。

```yaml
# NG: Loopback をインターフェースリストに含める
interfaces:
  - id: "i0"
    slot: -1          # ← エラーの原因
    type: loopback    # ← エラーの原因
```

---

## 2. ノード定義の選び方

| 用途 | node_definition | 特徴 |
|---|---|---|
| ルーター（L3） | `iosv` | IOS 15.x 相当、OSPF / BGP / MPLS 対応 |
| スイッチ（L2/L3） | `iosvl2` | IOS 15.x L2 スイッチ、デフォルトで IP routing 無効 |
| 高性能ルーター | `csr1000v` | IOS-XE、起動が遅い |
| 外部接続 | `external_connector` | 実ネットワークとの接続 |

---

## 3. 典型的なトポロジーパターン

### パターン A: ルータ 2 台・直接接続（最小構成）

```
R1 ──── R2
```

- リンク: 1 本
- 用途: OSPF / BGP の基本確認

```yaml
# R1: n0 (GE0/0=i0)  R2: n1 (GE0/0=i0)
links:
  - id: "l0"
    n1: "n0"
    i1: "i0"
    n2: "n1"
    i2: "i0"
    label: "l0"
```

---

### パターン B: ルータ 3 台・フルメッシュ（冗長構成）

```
R1 ─── R2
 \    /
  R3
```

- リンク: 3 本（R1-R2, R1-R3, R2-R3）
- 用途: OSPF 収束・障害シミュレーション
- 各ルーターに GE0/0 と GE0/1 が必要

```
R1: GE0/0 → R2, GE0/1 → R3
R2: GE0/0 → R1, GE0/1 → R3
R3: GE0/0 → R1, GE0/1 → R2
```

---

### パターン C: ルータ + スイッチ + ホスト

```
R1 ──── SW1 ──── PC1 (VLAN 10)
              └── PC2 (VLAN 20)
```

- R1 と SW1 はトランクリンクで接続
- SW1 で VLAN 分割、R1 でルーター・オン・ア・スティック

---

## 4. IP アドレス設計の定石

### リンク間は /30 サブネットを使用

```
R1-R2 間: 10.0.12.0/30  (R1: .1, R2: .2)
R1-R3 間: 10.0.13.0/30  (R1: .1, R3: .2)
R2-R3 間: 10.0.23.0/30  (R2: .1, R3: .2)
```

命名規則: `10.0.<低ノード番号><高ノード番号>.0/30`

### Loopback アドレス

```
R1: 1.1.1.1/32
R2: 2.2.2.2/32
R3: 3.3.3.3/32
```

Loopback はルーター識別子として使用し、OSPF の `router-id` に指定する。

---

## 5. 機器コンフィグの基本テンプレート（IOSv）

```
hostname R1
!
interface Loopback0
 ip address 1.1.1.1 255.255.255.255
!
interface GigabitEthernet0/0
 ip address 10.0.12.1 255.255.255.252
 ip ospf network point-to-point
 no shutdown
!
router ospf 1
 router-id 1.1.1.1
 network 0.0.0.0 255.255.255.255 area 0
!
end
```

**必ず含めるべき設定**:
1. `hostname` — デバイスの識別
2. Loopback0 — ルーター ID と安定した管理アドレス
3. 各インターフェースに `no shutdown` — デフォルトで shutdown されている
4. `ip ospf network point-to-point` — ルータ間直結リンクに推奨

---

## 6. よくある設計ミス

### ミス 1: インターフェースの `no shutdown` 忘れ

IOSv の GigabitEthernet は起動時に `shutdown` 状態のため、**必ず** `no shutdown` を記述する。

### ミス 2: Loopback を OSPF にアドバタイズしない

`network 0.0.0.0 255.255.255.255 area 0` を使えば Loopback も自動的に含まれる。
個別に `network 1.1.1.1 0.0.0.0 area 0` を書く場合は漏れに注意。

### ミス 3: リンク数とインターフェース数の不整合

ルーターに 2 リンクを接続する場合、トポロジー YAML のインターフェースも 2 つ（slot: 0 と slot: 1）必要。

### ミス 4: フルメッシュで `ip ospf network point-to-point` を忘れる

フルメッシュ構成では DR/BDR 選出を避けるために全リンクに `ip ospf network point-to-point` を設定する。設定漏れがあると OSPF が `2-WAY` から `FULL` に移行しない可能性がある。

---

## 7. 起動確認コマンド

```
! 全インターフェースの状態（up/up が正常）
show ip interface brief

! ルーティングテーブルの確認
show ip route

! OSPF ネイバーの確認
show ip ospf neighbor

! BGP セッションの確認
show bgp summary

! 疎通確認
ping 2.2.2.2 source Loopback0
traceroute 3.3.3.3 source 1.1.1.1
```
