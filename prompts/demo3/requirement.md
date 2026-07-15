## ネットワーク要件

- R1（AS 65001）とR2（AS 65002）をeBGPで接続する
- R1-R2間リンク: 10.0.12.0/30（R1: 10.0.12.1、R2: 10.0.12.2）

## Loopbackインターフェース

- R1: Loopback0 = 1.1.1.1/32
- R2: Loopback0 = 2.2.2.2/32

## eBGPの設定

- R1のAS番号: 65001、R2のAS番号: 65002
- eBGPピアアドレスはGigabitEthernet0/0のIPアドレスを使用すること（R1: 10.0.12.1 ↔ R2: 10.0.12.2）
- 各ルータは自身のLoopback0アドレスをBGPでアドバタイズすること

## 必須検証項目

- R1とR2のeBGPセッションが確立していること（Established状態）
- R1からR2のLoopback（2.2.2.2）へpingが通ること
- R2からR1のLoopback（1.1.1.1）へpingが通ること
