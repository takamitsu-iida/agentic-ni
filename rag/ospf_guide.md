# OSPF 設計・設定・トラブルシューティングガイド

このガイドは Cisco IOS (IOSv) を対象とした OSPF の設計・設定・よくある問題の解決策をまとめたものです。

---

## 1. 基本設定テンプレート

```
router ospf 1
 router-id <Loopback0のIPアドレス>
 network 0.0.0.0 255.255.255.255 area 0
```

`network 0.0.0.0 255.255.255.255 area 0` はすべてのインターフェース（Loopback 含む）を OSPF エリア 0 に参加させる最もシンプルな設定です。
特定のインターフェースだけ参加させたい場合は個別に `network` 文を記述してください。

```
! 個別指定の例
router ospf 1
 router-id 1.1.1.1
 network 10.0.12.0 0.0.0.3 area 0
 network 1.1.1.1 0.0.0.0 area 0
```

---

## 2. Router-ID の設定

Router-ID は OSPF ネイバーを一意に識別するための 32 ビット値です。

推奨: **Loopback0 の IP アドレスを Router-ID として明示的に設定する**

```
interface Loopback0
 ip address 1.1.1.1 255.255.255.255
!
router ospf 1
 router-id 1.1.1.1
```

Router-ID を明示設定しない場合、IOS は起動時に最大のインターフェース IP を自動選択します。
この動作はインターフェースの up/down によって変化するため、明示設定を強く推奨します。

---

## 3. Loopback インターフェースと OSPF

Loopback インターフェースはデフォルトで OSPF の `network type = loopback` になるため、/32 のホストルートとしてアドバタイズされます。
これは意図した動作です。`network 0.0.0.0 255.255.255.255 area 0` でカバーされます。

```
interface Loopback0
 ip address 1.1.1.1 255.255.255.255
! → OSPF でホストルート 1.1.1.1/32 としてアドバタイズされる
```

---

## 4. OSPF ネットワークタイプ

### point-to-point（推奨: ルータ間の直接接続）

```
interface GigabitEthernet0/0
 ip ospf network point-to-point
```

- DR/BDR 選出が不要になり収束が速い
- ルータ間の直接リンクには必ず設定することを推奨
- `show ip ospf neighbor` で `P2P` と表示されることを確認

### broadcast（デフォルト）

GigabitEthernet のデフォルト。DR/BDR の選出が行われます。
フルメッシュ構成の場合は `point-to-point` に変更することを推奨します。

---

## 5. Hello/Dead タイマー

デフォルト値（GigabitEthernet）:
- Hello interval: 10 秒
- Dead interval: 40 秒（Hello の 4 倍）

短縮設定（高速収束が必要な場合）:
```
interface GigabitEthernet0/0
 ip ospf hello-interval 3
 ip ospf dead-interval 10
```

**重要**: 対向インターフェースのタイマー値が一致していないと OSPF ネイバーが確立しません。
`show ip ospf interface GigabitEthernet0/0` でタイマー値を確認してください。

---

## 6. よくある設定ミスと解決策

### ミス 1: エリア番号のミスマッチ

```
! R1 側
router ospf 1
 network 10.0.12.0 0.0.0.3 area 0

! R2 側（間違い）
router ospf 1
 network 10.0.12.0 0.0.0.3 area 1  ← area 0 にすべき
```

症状: `show ip ospf neighbor` で neighbor が表示されない
確認コマンド: `show ip ospf neighbor detail` → "Dead timer" が消えている
解決: 両端のエリア番号を一致させる

---

### ミス 2: network 文の漏れ（Loopback が含まれていない）

```
! 間違い: Loopback が含まれない
router ospf 1
 router-id 1.1.1.1
 network 10.0.12.0 0.0.0.3 area 0  ← Loopback の network 文がない
```

症状: OSPF ネイバーは確立するが、対向から Loopback アドレス宛の ping が通らない
解決: Loopback を含む network 文を追加するか、`network 0.0.0.0 255.255.255.255 area 0` を使用する

---

### ミス 3: インターフェースが shutdown 状態

```
interface GigabitEthernet0/0
 shutdown  ← これがあると OSPF ネイバーが上がらない
```

確認: `show ip interface brief` で `down/down` または `admin down` を確認
解決: `no shutdown` を設定する

---

### ミス 4: Hello/Dead タイマーの不一致

症状: `%OSPF-5-ADJCHG: ... Down (Dead timer expired)` というログが繰り返し出る
確認: `show ip ospf interface` で両端のタイマー値を比較
解決: 両端で同じ hello-interval / dead-interval を設定する

---

## 7. OSPF + iBGP の典型的な構成

R1 と R2 を OSPF で接続し、Loopback アドレスを使った iBGP ピアリングを行う構成:

### R1 の設定

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
router bgp 65000
 neighbor 2.2.2.2 remote-as 65000
 neighbor 2.2.2.2 update-source Loopback0
!
end
```

### R2 の設定

```
hostname R2
!
interface Loopback0
 ip address 2.2.2.2 255.255.255.255
!
interface GigabitEthernet0/0
 ip address 10.0.12.2 255.255.255.252
 ip ospf network point-to-point
 no shutdown
!
router ospf 1
 router-id 2.2.2.2
 network 0.0.0.0 255.255.255.255 area 0
!
router bgp 65000
 neighbor 1.1.1.1 remote-as 65000
 neighbor 1.1.1.1 update-source Loopback0
!
end
```

**重要**: iBGP で Loopback アドレスをピアアドレスに使う場合、必ず `neighbor X.X.X.X update-source Loopback0` を設定すること。
この設定がないと BGP セッションが確立しません。

---

## 8. フルメッシュ（三角形）構成

R1-R2-R3 の 3 台をフルメッシュで OSPF 接続する場合:

```
! R1
interface GigabitEthernet0/0  ← R1-R2 間
 ip address 10.0.12.1 255.255.255.252
 ip ospf network point-to-point
 no shutdown
!
interface GigabitEthernet0/1  ← R1-R3 間
 ip address 10.0.13.1 255.255.255.252
 ip ospf network point-to-point
 no shutdown
!
router ospf 1
 router-id 1.1.1.1
 network 0.0.0.0 255.255.255.255 area 0
```

フルメッシュでは `ip ospf network point-to-point` を全リンクに設定することで DR/BDR 選出を省略できます。
1 リンクが断しても他の 2 リンク経由でトラフィックが継続します。

---

## 9. 確認コマンド一覧

```
! ネイバー状態の確認（Full になっていれば正常）
show ip ospf neighbor

! ネイバーの詳細（エリア番号・タイマーの確認）
show ip ospf neighbor detail

! インターフェースの OSPF 情報（ネットワークタイプ・タイマー）
show ip ospf interface

! ルーティングテーブルに OSPF ルートが入っているか確認
show ip route ospf

! OSPF データベースの確認
show ip ospf database
```

---

## 10. OSPF の状態遷移

正常な OSPF ネイバー確立の流れ:
```
Down → Init → 2-Way → ExStart → Exchange → Loading → Full
```

`Full` になれば正常です。
`2-Way` で止まる場合: DR/BDR 選出の問題（point-to-point 設定で解消）
`ExStart/Exchange` で止まる場合: MTU ミスマッチの可能性（`ip ospf mtu-ignore` で回避可能）
