## トポロジーは提供済み（生成不要）

このプロンプトセットでは **CMLトポロジーYAMLはすでに提供されています**。
`topology_yaml` は変更せず、**`device_configs` のみを生成してください**。

提供済みトポロジーの構成:
- R1: GigabitEthernet0/0 (slot:0) のみ
- R2: GigabitEthernet0/0 (slot:0) のみ
- リンク: R1のGE0/0 ↔ R2のGE0/0（1本のみ）

## IPアドレス割り当て（厳守）

| 機器 | インターフェース | IPアドレス |
|---|---|---|
| R1 | GigabitEthernet0/0 | 10.0.12.1/30 |
| R2 | GigabitEthernet0/0 | 10.0.12.2/30 |
| R1 | Loopback0 | 1.1.1.1/32 |
| R2 | Loopback0 | 2.2.2.2/32 |

## eBGP設定（厳守）

- R1のAS番号: **65001**、R2のAS番号: **65002**
- eBGPピアは直結インターフェースアドレスを使用すること:
  - R1: `neighbor 10.0.12.2 remote-as 65002`
  - R2: `neighbor 10.0.12.1 remote-as 65001`
- 各ルータはLoopback0アドレスを `network` ステートメントでアドバタイズすること

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
router bgp 65001
 bgp router-id 1.1.1.1
 neighbor 10.0.12.2 remote-as 65002
 !
 address-family ipv4
  network 1.1.1.1 mask 255.255.255.255
  neighbor 10.0.12.2 activate
 exit-address-family
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
router bgp 65002
 bgp router-id 2.2.2.2
 neighbor 10.0.12.1 remote-as 65001
 !
 address-family ipv4
  network 2.2.2.2 mask 255.255.255.255
  neighbor 10.0.12.1 activate
 exit-address-family
!
end
```
