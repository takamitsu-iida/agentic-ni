# BGP 設計・設定・トラブルシューティングガイド

このガイドは Cisco IOS (IOSv) を対象とした BGP（iBGP / eBGP）の設計・設定・よくある問題の解決策をまとめたものです。

---

## 1. iBGP の基本設定（Loopback ピアリング）

R1-R2 間を OSPF で接続し、Loopback アドレスで iBGP ピアリングする標準パターン。

### R1 の設定

```
router bgp 65000
 neighbor 2.2.2.2 remote-as 65000
 neighbor 2.2.2.2 update-source Loopback0
```

### R2 の設定

```
router bgp 65000
 neighbor 1.1.1.1 remote-as 65000
 neighbor 1.1.1.1 update-source Loopback0
```

**重要ルール**:
- `neighbor X.X.X.X update-source Loopback0` は**必ず** `neighbor X.X.X.X remote-as` と対で設定すること
- iBGP でピアアドレスに Loopback を使う場合、OSPF（または他のルーティングプロトコル）で Loopback アドレスを到達可能にしておくこと

---

## 2. eBGP の基本設定（直接接続）

異なる AS 間の直接接続（インターフェース IP をピアアドレスに使用）。

```
! R1 (AS 65001)
router bgp 65001
 neighbor 10.0.12.2 remote-as 65002

! R2 (AS 65002)
router bgp 65002
 neighbor 10.0.12.1 remote-as 65001
```

eBGP ではデフォルトで直接接続確認が行われるため、`update-source` は不要（Loopback ピアリングの場合は `ebgp-multihop` が必要）。

---

## 3. よくある設定ミスと解決策

### ミス 1: update-source の設定忘れ（iBGP）

```
! 誤り（iBGP で Loopback ピアリング時）
router bgp 65000
 neighbor 2.2.2.2 remote-as 65000
 update-source Loopback0          ← これは無効（neighbor なしの単独コマンド）
```

**正しい書き方**:
```
router bgp 65000
 neighbor 2.2.2.2 remote-as 65000
 neighbor 2.2.2.2 update-source Loopback0   ← neighbor X.X.X.X を必ず前置
```

症状: `show bgp summary` で `Idle` または `Active` のまま

---

### ミス 2: ピアアドレスの誤り

iBGP で Loopback をピアアドレスに使う場合、OSPF でそのアドレスがアドバタイズされていないと到達できない。

確認手順:
```
show ip route 2.2.2.2       ← OSPF ルートが存在するか確認
show ip ospf database       ← OSPF データベースで Loopback が存在するか確認
```

解決: OSPF の `network` 文に Loopback を含める。`network 0.0.0.0 255.255.255.255 area 0` で全インターフェース包含が最もシンプル。

---

### ミス 3: AS 番号の不一致

```
! R1 で設定
neighbor 2.2.2.2 remote-as 65001   ← R2 の AS 番号を指定

! R2 の実際の AS
router bgp 65000                    ← AS 65000 なのに R1 は 65001 と設定
```

症状: `show bgp summary` で `Connect` → `Idle` を繰り返す、または `Notification` が送受信される

---

## 4. BGP ルートのアドバタイズ

### network コマンドで広報

```
router bgp 65000
 network 1.1.1.1 mask 255.255.255.255   ← Loopback を広報
 network 10.0.0.0 mask 255.255.0.0      ← サマリルートを広報
```

`network` コマンドはルーティングテーブルに**完全一致**するプレフィックスが存在する場合のみ広報される。

---

## 5. 確認コマンド一覧

```
! BGP セッション状態（Established が正常）
show bgp summary
show ip bgp summary

! BGP テーブルの確認
show ip bgp

! 特定プレフィックスの詳細
show ip bgp 1.1.1.1/32

! ネイバーの詳細情報
show bgp neighbor 2.2.2.2

! ルート広報・受信の確認
show ip bgp neighbor 2.2.2.2 advertised-routes
show ip bgp neighbor 2.2.2.2 routes
```

---

## 6. BGP セッションの状態遷移

```
Idle → Connect → Active → OpenSent → OpenConfirm → Established
```

`Established` になれば正常。

| 状態 | 原因 |
|---|---|
| `Idle` / `Active` を繰り返す | ピアへの到達性なし、update-source 設定ミス |
| `OpenSent` で止まる | AS 番号ミスマッチ |
| `OpenConfirm` で止まる | 認証ミスマッチ（パスワードなど） |
| `Connect` | TCP 接続試行中（まだ繋がっていない） |
