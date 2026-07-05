## このセットのトポロジー仕様

- R1、R2、R3の3台（node_definition: `iosv`）を直列に接続する
- リンク構成: R1-R2 間と R2-R3 間の2本
- R2は2つのリンクを持つため `GigabitEthernet0/0` と `GigabitEthernet0/1` の両方を使用すること

## IPアドレス割り当て（厳守）

| 機器 | インターフェース | IPアドレス |
|---|---|---|
| R1 | GigabitEthernet0/0 | 10.0.12.1/30 |
| R2 | GigabitEthernet0/0 | 10.0.12.2/30 |
| R2 | GigabitEthernet0/1 | 10.0.23.1/30 |
| R3 | GigabitEthernet0/0 | 10.0.23.2/30 |
| R1 | Loopback0 | 1.1.1.1/32 |
| R2 | Loopback0 | 2.2.2.2/32 |
| R3 | Loopback0 | 3.3.3.3/32 |

## iBGP設定（必須）

- AS番号: 65000
- R1とR3の間でiBGPピアを設定する
- BGPピアアドレス: R1は `neighbor 3.3.3.3`、R3は `neighbor 1.1.1.1`
- **R1とR3の両方に `neighbor X.X.X.X update-source Loopback0` を必ず設定すること**
- iBGPピアのLoopbackアドレスへの到達性はOSPFが確保する

## よくあるミスと対策

- `update-source Loopback0` を忘れると BGP セッションが確立しない → 必ず設定すること
- LoopbackアドレスをOSPF `network` ステートメントに含め忘れると BGP の到達性がない
- R2 の両インターフェース（Gi0/0 と Gi0/1）を両方 OSPF に参加させること
