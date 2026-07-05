## ネットワーク構成
- R1、R2、R3の3台のルーターを直列に接続する（R1 - R2 - R3）
- R1-R2間のリンク: 10.0.12.0/30（R1: 10.0.12.1、R2: 10.0.12.2）
- R2-R3間のリンク: 10.0.23.0/30（R2: 10.0.23.1、R3: 10.0.23.2）

## Loopbackインターフェース
- R1: Loopback0 = 1.1.1.1/32
- R2: Loopback0 = 2.2.2.2/32
- R3: Loopback0 = 3.3.3.3/32

## OSPFの設定
- OSPFエリア0で全ルーターを接続する
- 全インターフェース（Loopback含む）をOSPFに参加させる
- Router-IDは各ルーターのLoopback0アドレスを使用する

## iBGPの設定
- AS番号: 65000
- R1とR3の間でiBGPピアを設定する
- BGPピアアドレス: R1のLoopback0 (1.1.1.1) ↔ R3のLoopback0 (3.3.3.3)
- R1とR3の両方に `neighbor X.X.X.X update-source Loopback0` を必ず設定する
- BGPセッションの確立にはOSPFで学習した 1.1.1.1/32 と 3.3.3.3/32 の経路が必要

## 必須検証項目
- R1、R2、R3 全台でOSPFネイバーが確立していること
- R1とR3の間のiBGPセッションが確立していること（R1とR3でbgp_summaryを確認）
- R1から 3.3.3.3（R3のLoopback）へpingが通ること
- R3から 1.1.1.1（R1のLoopback）へpingが通ること
