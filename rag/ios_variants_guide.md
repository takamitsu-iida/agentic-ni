# CML OSイメージ別 コマンドリファレンス

CML (Cisco Modeling Labs) で利用できる主要な OS イメージの違いと、
各イメージで使用する典型的な確認コマンドをまとめたガイドです。

---

## 1. OS イメージ一覧と特徴

| node_definition | OS | インターフェース名 | 用途 |
|---|---|---|---|
| `iosv` | IOS (IOSv) | `GigabitEthernet0/0` 〜 | L3 ルーター |
| `iosvl2` | IOS (IOSv) | `GigabitEthernet0/0` 〜 | L2/L3 スイッチ |
| `iol` | IOS on Linux | `Ethernet0/0` 〜 `Ethernet3/3` | L3 ルーター（軽量） |
| `ioll2` | IOS on Linux | `Ethernet0/0` 〜 | L2/L3 スイッチ（軽量） |
| `csr1000v` | IOS-XE | `GigabitEthernet1` 〜 | 高機能ルーター |
| `cat8000v` | IOS-XE | `GigabitEthernet1` 〜 | 高機能ルーター |
| `nxosv` | NX-OS | `Ethernet1/1` 〜 | データセンタースイッチ |

---

## 2. IOSv / iosvl2（最も一般的）

CML の基本ノード。`iosv` がルーター、`iosvl2` がスイッチ。

### インターフェース名
```
GigabitEthernet0/0   ← 1番目（slot 0）
GigabitEthernet0/1   ← 2番目（slot 1）
GigabitEthernet0/2   ← 3番目（slot 2）
```

### 基本確認コマンド
```
show version
show ip interface brief
show interfaces GigabitEthernet0/0
show ip route
show ip route 192.168.1.0
show ip ospf neighbor
show ip bgp summary
show ip protocols
ping 10.0.0.1 source GigabitEthernet0/0
traceroute 10.0.0.1 source Loopback0
```

## 動作しないコマンドの例

経路情報の確認時にプレフィクス長は指定できない。

```
show ip route 192.168.1.0/24
```

### 設定の特徴
```
interface GigabitEthernet0/0
 ip address 10.0.12.1 255.255.255.252
 ip ospf network point-to-point
 no shutdown              ← 必須（デフォルトで shutdown）
```

---

## 3. IOL / ioll2（IOS on Linux）

IOSv より軽量でメモリ消費が少ない。インターフェース名が異なる。

### インターフェース名
```
Ethernet0/0   ← slot 0, port 0（CML の slot: 0 に対応）
Ethernet0/1   ← slot 0, port 1（CML の slot: 1 に対応）
Ethernet0/2   ← slot 0, port 2
Ethernet0/3   ← slot 0, port 3
Ethernet1/0   ← slot 1, port 0（CML の slot: 4 に対応）
Ethernet1/1   ← slot 1, port 1（CML の slot: 5 に対応）
```

### 基本確認コマンド（IOSv と同じ）
```
show ip interface brief
show interfaces Ethernet0/0
show ip route
show ip ospf neighbor
show ip bgp summary
```

### 設定の特徴
```
interface Ethernet0/0
 ip address 10.0.12.1 255.255.255.252
 ip ospf network point-to-point
 no shutdown              ← IOSv と同様に必須
```

### IOL 固有の注意点
- `GigabitEthernet` ではなく `Ethernet` を使う
- スループットは低いが CML リソース消費が少ない
- OSPF/BGP の動作は IOSv と同じ

---

## 4. CSR1000v / Cat8000v（IOS-XE）

IOS-XE ベースの高機能ルーター。起動に時間がかかる（2〜5分）。

### インターフェース名
```
GigabitEthernet1    ← 1番目（スロット番号なし）
GigabitEthernet2    ← 2番目
GigabitEthernet3    ← 3番目
GigabitEthernet4    ← 4番目
```

### 基本確認コマンド（IOS と同じ構文）
```
show version
show ip interface brief
show interfaces GigabitEthernet1
show ip route
show ip ospf neighbor
show ip bgp summary
show platform
show environment all
```

### 設定の特徴
```
interface GigabitEthernet1
 ip address 10.0.12.1 255.255.255.252
 ip ospf network point-to-point
 no shutdown              ← 必須
```

### IOS-XE 固有コマンド
```
! 設定の確認（running-config の簡略表示）
show running-config | section interface

! プロセス状態
show platform software status control-processor brief

! モジュール情報
show module
```

---

## 5. NX-OS（nxosv）

データセンター向け OS。コマンド体系が IOS と大きく異なる。

### インターフェース名
```
Ethernet1/1    ← 1番目
Ethernet1/2    ← 2番目
Ethernet1/3    ← 3番目
mgmt0          ← 管理インターフェース
```

### 基本確認コマンド
```
show version
show interface brief
show interface Ethernet1/1
show ip route
show ip ospf neighbors         ← IOS と異なり "neighbors"（複数形）
show bgp summary               ← "ip" が不要
show vlan brief
show vpc
```

### 設定の特徴（IOS と大きく異なる）

```
! 機能を有効化してから設定（IOS にはない手順）
feature ospf
feature bgp
feature interface-vlan

! インターフェース設定
interface Ethernet1/1
 ip address 10.0.12.1/30      ← プレフィックス長形式（/30）
 ip router ospf 1 area 0      ← interface レベルで OSPF を有効化
 no shutdown

! OSPF 設定（network 文は不要）
router ospf 1
 router-id 1.1.1.1

! BGP 設定
router bgp 65000
 neighbor 2.2.2.2 remote-as 65000
 address-family ipv4 unicast
  neighbor 2.2.2.2 activate
```

### NX-OS 固有の注意点
- `feature ospf` 等の機能有効化が必要
- インターフェース IP は `/30` 形式
- OSPF の設定はインターフェースレベルで `ip router ospf 1 area 0`
- `show ip ospf neighbor` でなく `show ip ospf neighbors`（s が付く）
- `iosvl2` のように `ip routing` コマンドは不要（デフォルト有効）

---

## 6. OS 間の主要コマンド対照表

| 操作 | IOSv / IOL | CSR1000v / Cat8000v | NX-OS |
|---|---|---|---|
| インターフェース一覧 | `show ip interface brief` | `show ip interface brief` | `show interface brief` |
| ルーティングテーブル | `show ip route` | `show ip route` | `show ip route` |
| OSPF ネイバー | `show ip ospf neighbor` | `show ip ospf neighbor` | `show ip ospf neighbors` |
| BGP セッション | `show ip bgp summary` | `show ip bgp summary` | `show bgp summary` |
| インターフェース詳細 | `show interfaces Gi0/0` | `show interfaces Gi1` | `show interface Eth1/1` |
| 設定確認 | `show running-config` | `show running-config` | `show running-config` |
| OSPF 機能有効化 | 不要（自動） | 不要（自動） | `feature ospf` |
| IP アドレス表記 | `10.0.0.1 255.255.255.0` | `10.0.0.1 255.255.255.0` | `10.0.0.1/24` |
