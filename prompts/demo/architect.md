## このセットのトポロジー仕様

- R1とR2の2台（node_definition: `iosv`）を直接接続する
- リンク構成: R1-R2 間の1本

## IPアドレス割り当て（厳守）

| 機器 | インターフェース | IPアドレス |
|---|---|---|
| R1 | GigabitEthernet0/0 | 10.0.12.1/30 |
| R2 | GigabitEthernet0/0 | 10.0.12.2/30 |
| R1 | Loopback0 | 1.1.1.1/32 |
| R2 | Loopback0 | 2.2.2.2/32 |

## iBGP設定

- AS番号: 65000
- R1は `neighbor 2.2.2.2 remote-as 65000` を設定する
- R2は `neighbor 1.1.1.1 remote-as 65000` を設定する
