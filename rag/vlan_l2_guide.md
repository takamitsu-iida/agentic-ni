# VLAN・レイヤ 2 スイッチング 設計・設定ガイド

このガイドは Cisco IOS (IOSv / iosvl2) を対象とした VLAN・レイヤ 2 設定のベストプラクティスをまとめたものです。CML では `iosvl2` ノードを使用します。

---

## 1. CML での L2 スイッチノード

CML のトポロジー YAML では `node_definition: "iosvl2"` を使用する。

```yaml
nodes:
  - id: "n0"
    label: "SW1"
    node_definition: "iosvl2"
    interfaces:
      - id: "i0"
        label: "GigabitEthernet0/0"
        slot: 0
        type: physical
      - id: "i1"
        label: "GigabitEthernet0/1"
        slot: 1
        type: physical
      - id: "i2"
        label: "GigabitEthernet0/2"
        slot: 2
        type: physical
```

---

## 2. VLAN の作成

```
! VLAN データベースに登録
vlan 10
 name Sales
!
vlan 20
 name Engineering
!
end
```

または設定モードで:
```
conf t
vlan 10
 name Sales
exit
vlan 20
 name Engineering
exit
```

確認: `show vlan brief`

---

## 3. アクセスポート（エンドデバイス接続）

```
interface GigabitEthernet0/1
 switchport mode access
 switchport access vlan 10
 no shutdown
```

- アクセスポートは 1 VLAN のみ所属
- ルータやホスト側との接続に使用

---

## 4. トランクポート（スイッチ間・ルータ間接続）

```
interface GigabitEthernet0/0
 switchport trunk encapsulation dot1q
 switchport mode trunk
 switchport trunk allowed vlan 10,20
 no shutdown
```

- `switchport trunk encapsulation dot1q` は IOS スイッチで必須（自動 negotiation しない場合）
- `switchport trunk allowed vlan` で許可 VLAN を明示する

**全 VLAN を許可する場合（省略形）**:
```
interface GigabitEthernet0/0
 switchport trunk encapsulation dot1q
 switchport mode trunk
 no shutdown
```

---

## 5. インターフェース VLAN（SVI: Switch Virtual Interface）

ルーター間通信やスイッチへの管理アクセスに使用。

```
interface Vlan10
 ip address 192.168.10.1 255.255.255.0
 no shutdown
!
interface Vlan20
 ip address 192.168.20.1 255.255.255.0
 no shutdown
```

SVI が up になるには:
1. `vlan 10` が VLAN データベースに存在すること
2. そのスイッチのいずれかのポートが VLAN 10 に属して up であること

---

## 6. Router-on-a-Stick（ルーター VLAN 間ルーティング）

ルーター側でサブインターフェースを使い VLAN 間をルーティングする。

```
! ルーター側（IOSv）
interface GigabitEthernet0/0.10
 encapsulation dot1Q 10
 ip address 192.168.10.254 255.255.255.0
!
interface GigabitEthernet0/0.20
 encapsulation dot1Q 20
 ip address 192.168.20.254 255.255.255.0
```

親インターフェース（GigabitEthernet0/0）は `no shutdown` だが IP アドレスなし。

---

## 7. よくある設定ミスと解決策

### ミス 1: SVI が down のまま

症状: `show interfaces Vlan10` で `Vlan10 is down, line protocol is down`

原因と解決:
- VLAN がデータベースに未登録 → `vlan 10` コマンドで作成
- VLAN 10 に属するポートが存在しない → アクセスポートを設定して `no shutdown`

---

### ミス 2: トランクで VLAN が通らない

症状: ping は通るのに特定 VLAN の通信だけ届かない

確認:
```
show interfaces GigabitEthernet0/0 trunk
```

解決: `switchport trunk allowed vlan add 10` で VLAN を追加。または `allowed vlan all` で全許可。

---

### ミス 3: iosvl2 でルーティングが動かない

`iosvl2` はデフォルトで L2 スイッチとして動作する。OSPF 等を動かすには:
```
ip routing
```

を有効にする必要がある（`iosvl2` ではデフォルト無効）。

---

## 8. 確認コマンド一覧

```
! VLAN 一覧とポート所属
show vlan brief

! 特定 VLAN の詳細
show vlan id 10

! トランクポートの状態
show interfaces trunk

! インターフェース状態
show interfaces GigabitEthernet0/1 switchport

! MAC アドレステーブル
show mac address-table

! STP 状態
show spanning-tree vlan 10
```
