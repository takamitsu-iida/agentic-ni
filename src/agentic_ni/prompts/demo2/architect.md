## このセットのトポロジー仕様

R1・R2・R3 の 3 台（node_definition: `iosv`）をフルメッシュ（三角形）で接続する。
リンクは 3 本: R1–R2・R1–R3・R2–R3。

## IPアドレス割り当て（厳守）

| 機器 | インターフェース | IPアドレス |
|---|---|---|
| R1 | GigabitEthernet0/0 | 10.0.12.1/30 |
| R1 | GigabitEthernet0/1 | 10.0.13.1/30 |
| R1 | Loopback0 | 1.1.1.1/32 |
| R2 | GigabitEthernet0/0 | 10.0.12.2/30 |
| R2 | GigabitEthernet0/1 | 10.0.23.1/30 |
| R2 | Loopback0 | 2.2.2.2/32 |
| R3 | GigabitEthernet0/0 | 10.0.13.2/30 |
| R3 | GigabitEthernet0/1 | 10.0.23.2/30 |
| R3 | Loopback0 | 3.3.3.3/32 |

## CML トポロジー YAML のノード・インターフェース構成

```
R1: i0=GigabitEthernet0/0(slot:0), i1=GigabitEthernet0/1(slot:1)
R2: i0=GigabitEthernet0/0(slot:0), i1=GigabitEthernet0/1(slot:1)
R3: i0=GigabitEthernet0/0(slot:0), i1=GigabitEthernet0/1(slot:1)
```

リンク:
- R1 i0 ↔ R2 i0 （R1–R2 リンク, label: "l0"）
- R1 i1 ↔ R3 i0 （R1–R3 リンク, label: "l1"）
- R2 i1 ↔ R3 i1 （R2–R3 リンク, label: "l2"）

## OSPF 設定（厳守）

- プロセス番号: 1、エリア: 0
- 全機器で以下のコマンドをコンフィグに含めること:

```
router ospf 1
 router-id <Loopback0 のアドレス>
 network 0.0.0.0 255.255.255.255 area 0
```

## コンフィグテンプレート

### R1
```
hostname R1
!
interface Loopback0
 ip address 1.1.1.1 255.255.255.255
!
interface GigabitEthernet0/0
 ip address 10.0.12.1 255.255.255.252
 no shutdown
!
interface GigabitEthernet0/1
 ip address 10.0.13.1 255.255.255.252
 no shutdown
!
router ospf 1
 router-id 1.1.1.1
 network 0.0.0.0 255.255.255.255 area 0
!
end
```

### R2
```
hostname R2
!
interface Loopback0
 ip address 2.2.2.2 255.255.255.255
!
interface GigabitEthernet0/0
 ip address 10.0.12.2 255.255.255.252
 no shutdown
!
interface GigabitEthernet0/1
 ip address 10.0.23.1 255.255.255.252
 no shutdown
!
router ospf 1
 router-id 2.2.2.2
 network 0.0.0.0 255.255.255.255 area 0
!
end
```

### R3
```
hostname R3
!
interface Loopback0
 ip address 3.3.3.3 255.255.255.255
!
interface GigabitEthernet0/0
 ip address 10.0.13.2 255.255.255.252
 no shutdown
!
interface GigabitEthernet0/1
 ip address 10.0.23.2 255.255.255.252
 no shutdown
!
router ospf 1
 router-id 3.3.3.3
 network 0.0.0.0 255.255.255.255 area 0
!
end
```
