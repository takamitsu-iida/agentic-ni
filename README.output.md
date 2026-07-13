# `agentic-ni` 実行例

<br>

## static

**ポイント**
- 初回実行時はエンド・エンドの疎通に失敗しているが、原因を分析して、ホストにデフォルトルートがないことを突き止めている
- ２回目の施行ではホストにデフォルトルートが設定されているため、エンド・エンドの疎通に成功し、要件を満たしたネットワークを完成させている
- もちろん、プロンプトで「ホストにはデフォルトルートを設定しておくこと」と書いておけば初回実行時で完了できる。

```
(agentic-ni) iida@s400win:~/git/agentic-ni$ agentic-ni static
プロンプトセット: static

【要件】
  ## ネットワーク要件
  - R1（ルータ1）とR2（ルータ2）を直接接続する
  - 疎通確認用の端末としてR1の背後にホストを1台配置する
  - 疎通確認用の端末としてR2の背後にホストを1台配置する

  ## 機器要件
  - R1とR2のノード定義はiosvを使用する
  - 疎通確認用のホストのノード定義はiol-xeを使用する
  - 疎通確認用のホストのインタフェースはEthernet0/0を使用する

  ## ネットワークアドレス
  - 共通の接続セグメントは 192.168.12.0/24 とする
  - R1の背後に 192.168.10.0/24 を配置する
  - R2の背後に 192.168.20.0/24 を配置する

  ## ルーティング要件
  - 両拠点間の通信は、スタティックルーティング（静的ルート）を用いて開通させること

処理を開始します...


============================================================
[第1回 / 上限5回]  設計エージェント  (初回設計)
============================================================
  >>> LLM にトポロジーとコンフィグを生成させています...
  [知識ベース] rag/ の参考情報を設計プロンプトに追加しました。
  <<< 設計完了

[第1回 / 上限5回]  検証エージェント  開始
  [1/4] CML にデプロイ中...
    ラボをインポート中...
    コンフィグを投入中 (4 ノード)...
    ラボを起動中...
    ノードの起動を待機中...
    起動完了 (lab_id=21860d61-0556-48e3-a7be-3cf751e83905)
  [1/4] デプロイ完了 (lab_id=21860d61-0556-48e3-a7be-3cf751e83905)
  [2/4] テスト計画を立案中...
  [2/4] テスト計画完了 (7 件)
  [3/4] テストを実行中...
        (1/7) R1のGigabitEthernet0/0インターフェースの状態を確認
               → ✅ PASS  GigabitEthernet0/0: line=up, protocol=up
        (2/7) R1のGigabitEthernet0/1インターフェースの状態を確認
               → ✅ PASS  GigabitEthernet0/1: line=up, protocol=up
        (3/7) R2のGigabitEthernet0/1インターフェースの状態を確認
               → ✅ PASS  GigabitEthernet0/1: line=up, protocol=up
        (4/7) Host1からHost2（192.168.20.2）へのpingを確認
               → ❌ FAIL  ping 192.168.20.2 FAILED
        (5/7) R1から共通セグメント接続のR2（192.168.12.2）へのpingを確認
               → ✅ PASS  ping 192.168.12.2 OK
        (6/7) Host1からR1の最寄りゲートウェイ（192.168.10.1）へのpingを確認
               → ✅ PASS  ping 192.168.10.1 OK
        (7/7) Host2からR2の最寄りゲートウェイ（192.168.20.1）へのpingを確認
               → ✅ PASS  ping 192.168.20.1 OK
  [4/4] 失敗原因を AI が分析中... (1 件失敗)
  [4/4] 分析完了

  『根本原因』 Host1からHost2へのpingが通らない原因は、Host1およびHost2でデフォルトゲートウェイが設定されていないことです。デフォルトゲートウェイが設定されておらず、両ホストのルーティングが完了していないためです。
  『修正依頼』 Host1でデフォルトゲートウェイを192.168.10.1に、Host2でデフォルトゲートウェイを192.168.20.1に設定してください。


============================================================
[第2回 / 上限5回]  設計エージェント  (修正設計)
============================================================
  >>> LLM にトポロジーとコンフィグを生成させています...
  [知識ベース] rag/ の参考情報を設計プロンプトに追加しました。
  【設計方針】 Host1とHost2にデフォルトゲートウェイを設定し、双方向の通信を可能にしました。
  <<< 設計完了

[第2回 / 上限5回]  検証エージェント  開始
  [1/4] CML にデプロイ中...
    既存ラボを停止・wipe中...
    コンフィグを更新中 (4 ノード)...
    ラボを再起動中...
    ノードの起動を待機中...
    起動完了 (lab_id=21860d61-0556-48e3-a7be-3cf751e83905)
  [1/4] デプロイ完了 (lab_id=21860d61-0556-48e3-a7be-3cf751e83905)
  [2/4] テスト計画を立案中...
  [2/4] テスト計画完了 (4 件)
  [3/4] テストを実行中...
        (1/4) Verify that R1's GigabitEthernet0/0 interface is up/up.
               → ✅ PASS  GigabitEthernet0/0: line=up, protocol=up
        (2/4) Verify that R1's GigabitEthernet0/1 interface is up/up.
               → ✅ PASS  GigabitEthernet0/1: line=up, protocol=up
        (3/4) Verify that R2's GigabitEthernet0/0 interface is up/up.
               → ✅ PASS  GigabitEthernet0/0: line=up, protocol=up
        (4/4) Check connectivity between Host1 (behind R1) and Host2 (behind R2) to ensure static routing is implemented correctly.
               → ✅ PASS  ping 192.168.20.2 OK
  [4/4] 全テスト PASS

  >>> 全テスト PASS! 最終レポートを生成しています...
# 検証成功レポート

**生成日時**: 2026-07-06 21:20:30

## 要件
## ネットワーク要件
- R1（ルータ1）とR2（ルータ2）を直接接続する
- 疎通確認用の端末としてR1の背後にホストを1台配置する
- 疎通確認用の端末としてR2の背後にホストを1台配置する

## 機器要件
- R1とR2のノード定義はiosvを使用する
- 疎通確認用のホストのノード定義はiol-xeを使用する
- 疎通確認用のホストのインタフェースはEthernet0/0を使用する

## ネットワークアドレス
- 共通の接続セグメントは 192.168.12.0/24 とする
- R1の背後に 192.168.10.0/24 を配置する
- R2の背後に 192.168.20.0/24 を配置する

## ルーティング要件
- 両拠点間の通信は、スタティックルーティング（静的ルート）を用いて開通させること

## 概要
- 試行回数: 2 回
- PASSテスト: 4 件
- FAILテスト: 0 件
- ラボID: 21860d61-0556-48e3-a7be-3cf751e83905

## ネットワーク設計

### トポロジー定義（CML YAML）
```yaml
lab:
  title: agentic-ni-static
  description: "Direct connection between R1 and R2 with hosts behind each router for connectivity check."
  notes: ""
  timestamp: 0
  version: "0.1.0"

nodes:
  - id: "n0"
    label: "R1"
    node_definition: "iosv"
    x: -200
    y: 0
    configuration: ""
    interfaces:
      - id: "i0"
        label: "GigabitEthernet0/0"
        slot: 0
        type: physical
      - id: "i1"
        label: "GigabitEthernet0/1"
        slot: 1
        type: physical
  - id: "n1"
    label: "R2"
    node_definition: "iosv"
    x: 200
    y: 0
    configuration: ""
    interfaces:
      - id: "i0"
        label: "GigabitEthernet0/0"
        slot: 0
        type: physical
      - id: "i1"
        label: "GigabitEthernet0/1"
        slot: 1
        type: physical
  - id: "n2"
    label: "Host1"
    node_definition: "iol-xe"
    x: -300
    y: 100
    configuration: ""
    interfaces:
      - id: "i0"
        label: "Ethernet0/0"
        slot: 0
        type: physical
  - id: "n3"
    label: "Host2"
    node_definition: "iol-xe"
    x: 300
    y: 100
    configuration: ""
    interfaces:
      - id: "i0"
        label: "Ethernet0/0"
        slot: 0
        type: physical

links:
  - id: "l0"
    n1: "n0"
    i1: "i0"
    n2: "n1"
    i2: "i0"
    label: "l0"
  - id: "l1"
    n1: "n0"
    i1: "i1"
    n2: "n2"
    i2: "i0"
    label: "l1"
  - id: "l2"
    n1: "n1"
    i1: "i1"
    n2: "n3"
    i2: "i0"
    label: "l2"
```

### 機器コンフィグ

### R1
```
hostname R1
!
interface GigabitEthernet0/0
 ip address 192.168.12.1 255.255.255.0
 no shutdown
!
interface GigabitEthernet0/1
 ip address 192.168.10.1 255.255.255.0
 no shutdown
!
ip route 192.168.20.0 255.255.255.0 192.168.12.2
!
end
```

### R2
```
hostname R2
!
interface GigabitEthernet0/0
 ip address 192.168.12.2 255.255.255.0
 no shutdown
!
interface GigabitEthernet0/1
 ip address 192.168.20.1 255.255.255.0
 no shutdown
!
ip route 192.168.10.0 255.255.255.0 192.168.12.1
!
end
```

### Host1
```
hostname Host1
!
interface Ethernet0/0
 ip address 192.168.10.2 255.255.255.0
 no shutdown
!
ip route 0.0.0.0 0.0.0.0 192.168.10.1
!
end
```

### Host2
```
hostname Host2
!
interface Ethernet0/0
 ip address 192.168.20.2 255.255.255.0
 no shutdown
!
ip route 0.0.0.0 0.0.0.0 192.168.20.1
!
end
```

## 検証テスト結果

| テスト名 | 結果 | 詳細 |
|---|---|---|
| Verify that R1's GigabitEthernet0/0 interface is up/up. | ✅ PASS | GigabitEthernet0/0: line=up, protocol=up |
| Verify that R1's GigabitEthernet0/1 interface is up/up. | ✅ PASS | GigabitEthernet0/1: line=up, protocol=up |
| Verify that R2's GigabitEthernet0/0 interface is up/up. | ✅ PASS | GigabitEthernet0/0: line=up, protocol=up |
| Check connectivity between Host1 (behind R1) and Host2 (behind R2) to ensure static routing is implemented correctly. | ✅ PASS | ping 192.168.20.2 OK |

すべてのテストが PASS しました。要件を満たすネットワーク設計が確認されました。
(agentic-ni) iida@s400win:~/git/agentic-ni$
```

<br><br><br>

## staticの例（１回で成功）

```
iida@s400win:~/git/agentic-ni$ agentic-ni static
プロンプトセット: static

【要件】
  ## ネットワーク要件
  - R1（ルータ1）とR2（ルータ2）を直接接続する
  - 疎通確認用の端末としてR1の背後にホストを1台配置する
  - 疎通確認用の端末としてR2の背後にホストを1台配置する

  ## 機器要件
  - R1とR2のノード定義はiosvを使用する
  - 疎通確認用のホストのノード定義はiol-xeを使用する
  - 疎通確認用のホストのインタフェースはEthernet0/0を使用する

  ## ネットワークアドレス
  - 共通の接続セグメントは 192.168.12.0/24 とする
  - R1の背後に 192.168.10.0/24 を配置する
  - R2の背後に 192.168.20.0/24 を配置する

  ## ルーティング要件
  - 両拠点間の通信は、スタティックルーティング（静的ルート）を用いて開通させること

  ## 必須検証項目
  - R1の背後のホストからR2のホストにpingが通ること

処理を開始します...


============================================================
[第1回 / 上限5回]  設計エージェント  (初回設計)
============================================================
  >>> LLM にトポロジーとコンフィグを生成させています...
  [知識ベース] rag/ の参考情報を設計プロンプトに追加しました。
  [Strategy E] 4 ノードのコンフィグをファイルに保存: configs/static/
  <<< 設計完了

[第1回 / 上限5回]  検証エージェント  開始
  [1/4] CML にデプロイ中...
    ラボをインポート中...
    コンフィグを投入中 (4 ノード)...
    ラボを起動中...
    ノードの起動を待機中... (タイムアウト: 300s / 4 ノード)
    起動完了 (lab_id=a5f95c04-86ef-4d51-aeef-094a1af6bba9)
  [1/4] デプロイ完了 (lab_id=a5f95c04-86ef-4d51-aeef-094a1af6bba9)
  [2/4] テスト計画を立案中...
  [2/4] テスト計画完了 (8 件)
  [3/4] テストを実行中... (並列 最大 8 workers)
        [1/8] Confirm that the interface R1 GigabitEthernet0/0 is up/up. → ✅ PASS  GigabitEthernet0/0: line=up, protocol=up
        [3/8] Confirm that the interface R2 GigabitEthernet0/0 is up/up. → ✅ PASS  GigabitEthernet0/0: line=up, protocol=up
        [4/8] Confirm that the interface R2 GigabitEthernet0/1 is up/up. → ✅ PASS  GigabitEthernet0/1: line=up, protocol=up
        [2/8] Confirm that the interface R1 GigabitEthernet0/1 is up/up. → ✅ PASS  GigabitEthernet0/1: line=up, protocol=up
        [5/8] Verify connectivity from R1's host to the R2's host via ping. → ✅ PASS  ping 192.168.20.1 OK
        [6/8] Verify connectivity from R2's host to the R1's host via ping. → ✅ PASS  ping 192.168.10.1 OK
        [8/8] Check for the presence of a static route on R2 to reach 192.168.10.0/24. → ✅ PASS  prefix=192.168.10.0/24 found via , next_hop=
        [7/8] Check for the presence of a static route on R1 to reach 192.168.20.0/24. → ✅ PASS  prefix=192.168.20.0/24 found via , next_hop=
  [4/4] 全テスト PASS

  >>> 全テスト PASS! 最終レポートを生成しています...
  [Phase D] 設計ドキュメント生成完了: configs/static (8 ファイル)
# 検証成功レポート

**生成日時**: 2026-07-13 14:53:48

## 要件
## ネットワーク要件
- R1（ルータ1）とR2（ルータ2）を直接接続する
- 疎通確認用の端末としてR1の背後にホストを1台配置する
- 疎通確認用の端末としてR2の背後にホストを1台配置する

## 機器要件
- R1とR2のノード定義はiosvを使用する
- 疎通確認用のホストのノード定義はiol-xeを使用する
- 疎通確認用のホストのインタフェースはEthernet0/0を使用する

## ネットワークアドレス
- 共通の接続セグメントは 192.168.12.0/24 とする
- R1の背後に 192.168.10.0/24 を配置する
- R2の背後に 192.168.20.0/24 を配置する

## ルーティング要件
- 両拠点間の通信は、スタティックルーティング（静的ルート）を用いて開通させること

## 必須検証項目
- R1の背後のホストからR2のホストにpingが通ること

## 概要
- 試行回数: 1 回
- PASSテスト: 8 件
- FAILテスト: 0 件
- ラボID: a5f95c04-86ef-4d51-aeef-094a1af6bba9

## ネットワーク設計

### トポロジー定義（CML YAML）
```yaml
lab:
  title: agentic-ni-static
  description: "A simple lab connecting R1 and R2 with static routing and host connectivity"
  notes: ""
  timestamp: 0
  version: "0.1.0"

nodes:
  - id: "n0"
    label: "R1"
    node_definition: "iosv"
    x: -200
    y: 0
    configuration: ""
    interfaces:
      - id: "i0"
        label: "GigabitEthernet0/0"
        slot: 0
        type: physical
      - id: "i1"
        label: "GigabitEthernet0/1"
        slot: 1
        type: physical
  - id: "n1"
    label: "R2"
    node_definition: "iosv"
    x: 200
    y: 0
    configuration: ""
    interfaces:
      - id: "i0"
        label: "GigabitEthernet0/0"
        slot: 0
        type: physical
      - id: "i1"
        label: "GigabitEthernet0/1"
        slot: 1
        type: physical
  - id: "n2"
    label: "Host1"
    node_definition: "iol-xe"
    x: -200
    y: 200
    configuration: ""
    interfaces:
      - id: "i0"
        label: "Ethernet0/0"
        slot: 0
        type: physical
  - id: "n3"
    label: "Host2"
    node_definition: "iol-xe"
    x: 200
    y: 200
    configuration: ""
    interfaces:
      - id: "i0"
        label: "Ethernet0/0"
        slot: 0
        type: physical

links:
  - id: "l0"
    n1: "n0"
    i1: "i0"
    n2: "n1"
    i2: "i0"
    label: "l0"
  - id: "l1"
    n1: "n0"
    i1: "i1"
    n2: "n2"
    i2: "i0"
    label: "l1"
  - id: "l2"
    n1: "n1"
    i1: "i1"
    n2: "n3"
    i2: "i0"
    label: "l2"
```

### 機器コンフィグ

### R1
```
hostname R1
!
interface GigabitEthernet0/0
 ip address 192.168.12.1 255.255.255.0
 no shutdown
!
interface GigabitEthernet0/1
 ip address 192.168.10.1 255.255.255.0
 no shutdown
!
ip route 192.168.20.0 255.255.255.0 192.168.12.2
!
end
```

### R2
```
hostname R2
!
interface GigabitEthernet0/0
 ip address 192.168.12.2 255.255.255.0
 no shutdown
!
interface GigabitEthernet0/1
 ip address 192.168.20.1 255.255.255.0
 no shutdown
!
ip route 192.168.10.0 255.255.255.0 192.168.12.1
!
end
```

### Host1
```
hostname Host1
!
interface Ethernet0/0
 ip address 192.168.10.2 255.255.255.0
 no shutdown
!
end
```

### Host2
```
hostname Host2
!
interface Ethernet0/0
 ip address 192.168.20.2 255.255.255.0
 no shutdown
!
end
```

## 検証テスト結果

| テスト名 | 結果 | 詳細 |
|---|---|---|
| Confirm that the interface R1 GigabitEthernet0/0 is up/up. | ✅ PASS | GigabitEthernet0/0: line=up, protocol=up |
| Confirm that the interface R1 GigabitEthernet0/1 is up/up. | ✅ PASS | GigabitEthernet0/1: line=up, protocol=up |
| Confirm that the interface R2 GigabitEthernet0/0 is up/up. | ✅ PASS | GigabitEthernet0/0: line=up, protocol=up |
| Confirm that the interface R2 GigabitEthernet0/1 is up/up. | ✅ PASS | GigabitEthernet0/1: line=up, protocol=up |
| Verify connectivity from R1's host to the R2's host via ping. | ✅ PASS | ping 192.168.20.1 OK |
| Verify connectivity from R2's host to the R1's host via ping. | ✅ PASS | ping 192.168.10.1 OK |
| Check for the presence of a static route on R1 to reach 192.168.20.0/24. | ✅ PASS | prefix=192.168.20.0/24 found via , next_hop= |
| Check for the presence of a static route on R2 to reach 192.168.10.0/24. | ✅ PASS | prefix=192.168.10.0/24 found via , next_hop= |

すべてのテストが PASS しました。要件を満たすネットワーク設計が確認されました。

---

## 設計ドキュメント（Phase D）

### IP アドレス台帳

| デバイス | インターフェース | アドレス（CIDR） |
|---|---|---|
| R1 | GigabitEthernet0/0 | 192.168.12.1/24 |
| R1 | GigabitEthernet0/1 | 192.168.10.1/24 |
| R2 | GigabitEthernet0/0 | 192.168.12.2/24 |
| R2 | GigabitEthernet0/1 | 192.168.20.1/24 |
| Host1 | Ethernet0/0 | 192.168.10.2/24 |
| Host2 | Ethernet0/0 | 192.168.20.2/24 |

### ルーティング設計サマリー

(ルーティングプロトコル設定なし)

### 保存先ファイル

- `configs/static/topology.yaml`
- `configs/static/R1.cfg`
- `configs/static/R2.cfg`
- `configs/static/Host1.cfg`
- `configs/static/Host2.cfg`
- `configs/static/ip_ledger.md`
- `configs/static/ip_ledger.csv`
- `configs/static/routing_design.md`
iida@s400win:~/git/agentic-ni$
```

<br><br><br>

## 障害試験の例

**ポイント**
- 擬似的にリンクをダウンさせて通信が継続することを確認
- 復旧時の動作も確認

```
(agentic-ni) iida@s400win:~/git/agentic-ni$ agentic-ni demo2 --fault-sim
プロンプトセット: demo2
障害シミュレーション: 有効

【要件】
  ## ネットワーク構成

  - 3台のルーター（R1・R2・R3）をフルメッシュ（三角形）で接続すること
  - 各リンクで OSPF エリア 0 を構成すること
  - R1–R2 リンクが断した場合でもR1→R3→R2 の迂回経路でトラフィックが継続するように設計すること
  - ルータ・ルータ間のOSPFのリンクタイプはpoint-to-pointとして設計すること
  - OSPFのHello/Deadのタイマー値は3秒/10秒とすること

  ## リンク構成

  | リンク | 接続 | ネットワーク |
  |---|---|---|
  | R1–R2 | GE0/0 (R1) ↔ GE0/0 (R2) | 10.0.12.0/30 |
  | R1–R3 | GE0/1 (R1) ↔ GE0/0 (R3) | 10.0.13.0/30 |
  | R2–R3 | GE0/1 (R2) ↔ GE0/1 (R3) | 10.0.23.0/30 |

  ## IPアドレス割り当て

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

  ## OSPFの設定

  - プロセス番号: 1
  - エリア: 0 のみ
  - 全インターフェース（Loopback 含む）を area 0 に参加させること
  - Router-ID は Loopback0 アドレスを使用すること

  ## 必須検証項目

  - R1・R2・R3 の OSPF ネイバーがそれぞれ 2 つ確立していること
  - R1 から 2.2.2.2（R2 Loopback）へ ping が通ること
  - R1 から 3.3.3.3（R3 Loopback）へ ping が通ること
  - R2 から 3.3.3.3（R3 Loopback）へ ping が通ること

処理を開始します...

既存ラボを検出: agentic-ni-demo2 (ID=8fb6564d-1b70-497a-b0a5-7522c2a6f8ed) → デプロイをスキップして障害検証を実施します

============================================================
[第1回 / 上限5回]  設計エージェント  (初回設計)
============================================================
  >>> LLM にトポロジーとコンフィグを生成させています...
  [知識ベース] 未インデックス（スキップ）。agentic-ni --rag-index で索引化できます。
  <<< 設計完了

[第1回 / 上限5回]  検証エージェント  開始
  [1/4] CML にデプロイ中...
    既存ラボを再利用（デプロイスキップ）: lab_id=8fb6564d-1b70-497a-b0a5-7522c2a6f8ed
  [1/4] デプロイ完了 (lab_id=8fb6564d-1b70-497a-b0a5-7522c2a6f8ed)
  [2/4] テスト計画を立案中...
  [2/4] テスト計画完了 (6 件)
  [3/4] テストを実行中...
        (1/6) R1のOSPFネイバーがR2およびR3と確立していることを確認する。
               → ✅ PASS  2 neighbor(s) FULL
        (2/6) R2のOSPFネイバーがR1およびR3と確立していることを確認する。
               → ✅ PASS  2 neighbor(s) FULL
        (3/6) R3のOSPFネイバーがR1およびR2と確立していることを確認する。
               → ✅ PASS  2 neighbor(s) FULL
        (4/6) R1からR2のLoopback0（2.2.2.2）へのパケットが到達可能であることを確認する。
               → ✅ PASS  ping 2.2.2.2 OK
        (5/6) R1からR3のLoopback0（3.3.3.3）へのパケットが到達可能であることを確認する。
               → ✅ PASS  ping 3.3.3.3 OK
        (6/6) R2からR3のLoopback0（3.3.3.3）へのパケットが到達可能であることを確認する。
               → ✅ PASS  ping 3.3.3.3 OK
  [4/4] 全テスト PASS

  >>> 全テスト PASS! 最終レポートを生成しています...

============================================================
[障害シミュレーション]  開始
============================================================
  [障害シミュレーション 1/3] CML からリンク一覧を取得中...
  [障害シミュレーション 2/3] 障害シナリオを LLM に立案させています (3 リンク)...
  [障害シミュレーション 2/3] 計画完了 (3 シナリオ): R1 <-> R2 のリンクはプライマリ経路として重要であり、断された場合の迂回経路を検証する必要がある。また、R1 <-> R3 と R2 <-> R3 のリンクはコアルーター間接続であり、障害に対する冗長性を確認することが重要である。これにより、全てのリンクが障害を起こした際のネットワークの耐障害性を総合的に評価できる。
  [障害シミュレーション 3/3] 障害シナリオを実行中...

  ▶ シナリオ 1/3: プライマリリンク断時の迂回経路確認
    CML リンク DOWN: R1 <-> R2 (15s 待機中...)
    テスト実行（障害中）:
        (1/6) [障害中] OSPF ネイバー数確認: R1 （期待値: 1）
               → ✅ PASS  1 neighbor(s) FULL (expected: 1)
        (2/6) [障害中] OSPF ネイバー数確認: R2 （期待値: 1）
               → ✅ PASS  1 neighbor(s) FULL (expected: 1)
        (3/6) [障害中] R3のOSPFネイバーがR1およびR2と確立していることを確認する。
               → ✅ PASS  2 neighbor(s) FULL
        (4/6) [障害中] R1からR2のLoopback0（2.2.2.2）へのパケットが到達可能であることを確認する。
               → ✅ PASS  ping 2.2.2.2 OK
        (5/6) [障害中] R1からR3のLoopback0（3.3.3.3）へのパケットが到達可能であることを確認する。
               → ✅ PASS  ping 3.3.3.3 OK
        (6/6) [障害中] R2からR3のLoopback0（3.3.3.3）へのパケットが到達可能であることを確認する。
               → ✅ PASS  ping 3.3.3.3 OK
    CML リンク UP（復旧）: R1 <-> R2 (15s 待機中...)
    テスト実行（復旧後）:
        (1/6) [復旧後] R1のOSPFネイバーがR2およびR3と確立していることを確認する。
               → ✅ PASS  2 neighbor(s) FULL
        (2/6) [復旧後] R2のOSPFネイバーがR1およびR3と確立していることを確認する。
               → ✅ PASS  2 neighbor(s) FULL
        (3/6) [復旧後] R3のOSPFネイバーがR1およびR2と確立していることを確認する。
               → ✅ PASS  2 neighbor(s) FULL
        (4/6) [復旧後] R1からR2のLoopback0（2.2.2.2）へのパケットが到達可能であることを確認する。
               → ✅ PASS  ping 2.2.2.2 OK
        (5/6) [復旧後] R1からR3のLoopback0（3.3.3.3）へのパケットが到達可能であることを確認する。
               → ✅ PASS  ping 3.3.3.3 OK
        (6/6) [復旧後] R2からR3のLoopback0（3.3.3.3）へのパケットが到達可能であることを確認する。
               → ✅ PASS  ping 3.3.3.3 OK
    シナリオ結果: ✅ PASS

  ▶ シナリオ 2/3: コア間リンク断時の冗長性確認
    CML リンク DOWN: R1 <-> R3 (15s 待機中...)
    テスト実行（障害中）:
        (1/6) [障害中] OSPF ネイバー数確認: R1 （期待値: 1）
               → ✅ PASS  1 neighbor(s) FULL (expected: 1)
        (2/6) [障害中] R2のOSPFネイバーがR1およびR3と確立していることを確認する。
               → ✅ PASS  2 neighbor(s) FULL
        (3/6) [障害中] OSPF ネイバー数確認: R3 （期待値: 1）
               → ✅ PASS  1 neighbor(s) FULL (expected: 1)
        (4/6) [障害中] R1からR2のLoopback0（2.2.2.2）へのパケットが到達可能であることを確認する。
               → ✅ PASS  ping 2.2.2.2 OK
        (5/6) [障害中] R1からR3のLoopback0（3.3.3.3）へのパケットが到達可能であることを確認する。
               → ✅ PASS  ping 3.3.3.3 OK
        (6/6) [障害中] R2からR3のLoopback0（3.3.3.3）へのパケットが到達可能であることを確認する。
               → ✅ PASS  ping 3.3.3.3 OK
    CML リンク UP（復旧）: R1 <-> R3 (15s 待機中...)
    テスト実行（復旧後）:
        (1/6) [復旧後] R1のOSPFネイバーがR2およびR3と確立していることを確認する。
               → ✅ PASS  2 neighbor(s) FULL
        (2/6) [復旧後] R2のOSPFネイバーがR1およびR3と確立していることを確認する。
               → ✅ PASS  2 neighbor(s) FULL
        (3/6) [復旧後] R3のOSPFネイバーがR1およびR2と確立していることを確認する。
               → ✅ PASS  2 neighbor(s) FULL
        (4/6) [復旧後] R1からR2のLoopback0（2.2.2.2）へのパケットが到達可能であることを確認する。
               → ✅ PASS  ping 2.2.2.2 OK
        (5/6) [復旧後] R1からR3のLoopback0（3.3.3.3）へのパケットが到達可能であることを確認する。
               → ✅ PASS  ping 3.3.3.3 OK
        (6/6) [復旧後] R2からR3のLoopback0（3.3.3.3）へのパケットが到達可能であることを確認する。
               → ✅ PASS  ping 3.3.3.3 OK
    シナリオ結果: ✅ PASS

  ▶ シナリオ 3/3: コア間リンク断時の冗長性確認
    CML リンク DOWN: R2 <-> R3 (15s 待機中...)
    テスト実行（障害中）:
        (1/6) [障害中] R1のOSPFネイバーがR2およびR3と確立していることを確認する。
               → ✅ PASS  2 neighbor(s) FULL
        (2/6) [障害中] OSPF ネイバー数確認: R2 （期待値: 1）
               → ✅ PASS  1 neighbor(s) FULL (expected: 1)
        (3/6) [障害中] OSPF ネイバー数確認: R3 （期待値: 1）
               → ✅ PASS  1 neighbor(s) FULL (expected: 1)
        (4/6) [障害中] R1からR2のLoopback0（2.2.2.2）へのパケットが到達可能であることを確認する。
               → ✅ PASS  ping 2.2.2.2 OK
        (5/6) [障害中] R1からR3のLoopback0（3.3.3.3）へのパケットが到達可能であることを確認する。
               → ✅ PASS  ping 3.3.3.3 OK
        (6/6) [障害中] R2からR3のLoopback0（3.3.3.3）へのパケットが到達可能であることを確認する。
               → ✅ PASS  ping 3.3.3.3 OK
    CML リンク UP（復旧）: R2 <-> R3 (15s 待機中...)
    テスト実行（復旧後）:
        (1/6) [復旧後] R1のOSPFネイバーがR2およびR3と確立していることを確認する。
               → ✅ PASS  2 neighbor(s) FULL
        (2/6) [復旧後] R2のOSPFネイバーがR1およびR3と確立していることを確認する。
               → ✅ PASS  2 neighbor(s) FULL
        (3/6) [復旧後] R3のOSPFネイバーがR1およびR2と確立していることを確認する。
               → ✅ PASS  2 neighbor(s) FULL
        (4/6) [復旧後] R1からR2のLoopback0（2.2.2.2）へのパケットが到達可能であることを確認する。
               → ✅ PASS  ping 2.2.2.2 OK
        (5/6) [復旧後] R1からR3のLoopback0（3.3.3.3）へのパケットが到達可能であることを確認する。
               → ✅ PASS  ping 3.3.3.3 OK
        (6/6) [復旧後] R2からR3のLoopback0（3.3.3.3）へのパケットが到達可能であることを確認する。
               → ✅ PASS  ping 3.3.3.3 OK
    シナリオ結果: ✅ PASS

  [障害シミュレーション 完了] 3/3 シナリオ PASS

  >>> 障害シミュレーションレポートを生成しています...
# 検証成功レポート

**生成日時**: 2026-07-06 23:11:41

## 要件
## ネットワーク構成

- 3台のルーター（R1・R2・R3）をフルメッシュ（三角形）で接続すること
- 各リンクで OSPF エリア 0 を構成すること
- R1–R2 リンクが断した場合でもR1→R3→R2 の迂回経路でトラフィックが継続するように設計すること
- ルータ・ルータ間のOSPFのリンクタイプはpoint-to-pointとして設計すること
- OSPFのHello/Deadのタイマー値は3秒/10秒とすること

## リンク構成

| リンク | 接続 | ネットワーク |
|---|---|---|
| R1–R2 | GE0/0 (R1) ↔ GE0/0 (R2) | 10.0.12.0/30 |
| R1–R3 | GE0/1 (R1) ↔ GE0/0 (R3) | 10.0.13.0/30 |
| R2–R3 | GE0/1 (R2) ↔ GE0/1 (R3) | 10.0.23.0/30 |

## IPアドレス割り当て

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

## OSPFの設定

- プロセス番号: 1
- エリア: 0 のみ
- 全インターフェース（Loopback 含む）を area 0 に参加させること
- Router-ID は Loopback0 アドレスを使用すること

## 必須検証項目

- R1・R2・R3 の OSPF ネイバーがそれぞれ 2 つ確立していること
- R1 から 2.2.2.2（R2 Loopback）へ ping が通ること
- R1 から 3.3.3.3（R3 Loopback）へ ping が通ること
- R2 から 3.3.3.3（R3 Loopback）へ ping が通ること

## 概要
- 試行回数: 1 回
- PASSテスト: 6 件
- FAILテスト: 0 件
- ラボID: 8fb6564d-1b70-497a-b0a5-7522c2a6f8ed

## ネットワーク設計

### トポロジー定義（CML YAML）
```yaml
lab:
  title: agentic-ni-demo2
  description: "Three routers in a full mesh with OSPF"
  notes: ""
  timestamp: 0
  version: "0.1.0"

nodes:
  - id: "n0"
    label: "R1"
    node_definition: "iosv"
    x: -200
    y: 0
    configuration: ""
    interfaces:
      - id: "i0"
        label: "GigabitEthernet0/0"
        slot: 0
        type: physical
      - id: "i1"
        label: "GigabitEthernet0/1"
        slot: 1
        type: physical
  - id: "n1"
    label: "R2"
    node_definition: "iosv"
    x: 0
    y: 0
    configuration: ""
    interfaces:
      - id: "i0"
        label: "GigabitEthernet0/0"
        slot: 0
        type: physical
      - id: "i1"
        label: "GigabitEthernet0/1"
        slot: 1
        type: physical
  - id: "n2"
    label: "R3"
    node_definition: "iosv"
    x: 200
    y: 0
    configuration: ""
    interfaces:
      - id: "i0"
        label: "GigabitEthernet0/0"
        slot: 0
        type: physical
      - id: "i1"
        label: "GigabitEthernet0/1"
        slot: 1
        type: physical

links:
  - id: "l0"
    n1: "n0"
    i1: "i0"
    n2: "n1"
    i2: "i0"
    label: "l0"
  - id: "l1"
    n1: "n0"
    i1: "i1"
    n2: "n2"
    i2: "i0"
    label: "l1"
  - id: "l2"
    n1: "n1"
    i1: "i1"
    n2: "n2"
    i2: "i1"
    label: "l2"
```

### 機器コンフィグ

### R1
```
hostname R1
!
interface Loopback0
 ip address 1.1.1.1 255.255.255.255
!
interface GigabitEthernet0/0
 ip address 10.0.12.1 255.255.255.252
 ip ospf network point-to-point
 ip ospf hello-interval 3
 ip ospf dead-interval 10
 no shutdown
!
interface GigabitEthernet0/1
 ip address 10.0.13.1 255.255.255.252
 ip ospf network point-to-point
 ip ospf hello-interval 3
 ip ospf dead-interval 10
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
 ip ospf network point-to-point
 ip ospf hello-interval 3
 ip ospf dead-interval 10
 no shutdown
!
interface GigabitEthernet0/1
 ip address 10.0.23.1 255.255.255.252
 ip ospf network point-to-point
 ip ospf hello-interval 3
 ip ospf dead-interval 10
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
 ip ospf network point-to-point
 ip ospf hello-interval 3
 ip ospf dead-interval 10
 no shutdown
!
interface GigabitEthernet0/1
 ip address 10.0.23.2 255.255.255.252
 ip ospf network point-to-point
 ip ospf hello-interval 3
 ip ospf dead-interval 10
 no shutdown
!
router ospf 1
 router-id 3.3.3.3
 network 0.0.0.0 255.255.255.255 area 0
!
end
```

## 検証テスト結果

| テスト名 | 結果 | 詳細 |
|---|---|---|
| R1のOSPFネイバーがR2およびR3と確立していることを確認する。 | ✅ PASS | 2 neighbor(s) FULL |
| R2のOSPFネイバーがR1およびR3と確立していることを確認する。 | ✅ PASS | 2 neighbor(s) FULL |
| R3のOSPFネイバーがR1およびR2と確立していることを確認する。 | ✅ PASS | 2 neighbor(s) FULL |
| R1からR2のLoopback0（2.2.2.2）へのパケットが到達可能であることを確認する。 | ✅ PASS | ping 2.2.2.2 OK |
| R1からR3のLoopback0（3.3.3.3）へのパケットが到達可能であることを確認する。 | ✅ PASS | ping 3.3.3.3 OK |
| R2からR3のLoopback0（3.3.3.3）へのパケットが到達可能であることを確認する。 | ✅ PASS | ping 3.3.3.3 OK |

すべてのテストが PASS しました。要件を満たすネットワーク設計が確認されました。

---

## 障害シミュレーション結果

- 実施シナリオ数: 3 件
- PASS（復旧確認）: 3 件
- FAIL（復旧未確認）: 0 件
- **判定: ✅ 全シナリオで復旧を確認**

### プライマリリンク断時の迂回経路確認 (R1 <-> R2) — ✅ PASS

**障害中テスト結果**

| テスト名 | 結果 | 詳細 |
|---|---|---|
| OSPF ネイバー数確認: R1 （障害中の期待値: 1） | ✅ PASS | 1 neighbor(s) FULL (expected: 1) |
| OSPF ネイバー数確認: R2 （障害中の期待値: 1） | ✅ PASS | 1 neighbor(s) FULL (expected: 1) |
| R3のOSPFネイバーがR1およびR2と確立していることを確認する。 | ✅ PASS | 2 neighbor(s) FULL |
| R1からR2のLoopback0（2.2.2.2）へのパケットが到達可能であることを確認する。 | ✅ PASS | ping 2.2.2.2 OK |
| R1からR3のLoopback0（3.3.3.3）へのパケットが到達可能であることを確認する。 | ✅ PASS | ping 3.3.3.3 OK |
| R2からR3のLoopback0（3.3.3.3）へのパケットが到達可能であることを確認する。 | ✅ PASS | ping 3.3.3.3 OK |

**復旧後テスト結果**

| テスト名 | 結果 | 詳細 |
|---|---|---|
| R1のOSPFネイバーがR2およびR3と確立していることを確認する。 | ✅ PASS | 2 neighbor(s) FULL |
| R2のOSPFネイバーがR1およびR3と確立していることを確認する。 | ✅ PASS | 2 neighbor(s) FULL |
| R3のOSPFネイバーがR1およびR2と確立していることを確認する。 | ✅ PASS | 2 neighbor(s) FULL |
| R1からR2のLoopback0（2.2.2.2）へのパケットが到達可能であることを確認する。 | ✅ PASS | ping 2.2.2.2 OK |
| R1からR3のLoopback0（3.3.3.3）へのパケットが到達可能であることを確認する。 | ✅ PASS | ping 3.3.3.3 OK |
| R2からR3のLoopback0（3.3.3.3）へのパケットが到達可能であることを確認する。 | ✅ PASS | ping 3.3.3.3 OK |

### コア間リンク断時の冗長性確認 (R1 <-> R3) — ✅ PASS

**障害中テスト結果**

| テスト名 | 結果 | 詳細 |
|---|---|---|
| OSPF ネイバー数確認: R1 （障害中の期待値: 1） | ✅ PASS | 1 neighbor(s) FULL (expected: 1) |
| R2のOSPFネイバーがR1およびR3と確立していることを確認する。 | ✅ PASS | 2 neighbor(s) FULL |
| OSPF ネイバー数確認: R3 （障害中の期待値: 1） | ✅ PASS | 1 neighbor(s) FULL (expected: 1) |
| R1からR2のLoopback0（2.2.2.2）へのパケットが到達可能であることを確認する。 | ✅ PASS | ping 2.2.2.2 OK |
| R1からR3のLoopback0（3.3.3.3）へのパケットが到達可能であることを確認する。 | ✅ PASS | ping 3.3.3.3 OK |
| R2からR3のLoopback0（3.3.3.3）へのパケットが到達可能であることを確認する。 | ✅ PASS | ping 3.3.3.3 OK |

**復旧後テスト結果**

| テスト名 | 結果 | 詳細 |
|---|---|---|
| R1のOSPFネイバーがR2およびR3と確立していることを確認する。 | ✅ PASS | 2 neighbor(s) FULL |
| R2のOSPFネイバーがR1およびR3と確立していることを確認する。 | ✅ PASS | 2 neighbor(s) FULL |
| R3のOSPFネイバーがR1およびR2と確立していることを確認する。 | ✅ PASS | 2 neighbor(s) FULL |
| R1からR2のLoopback0（2.2.2.2）へのパケットが到達可能であることを確認する。 | ✅ PASS | ping 2.2.2.2 OK |
| R1からR3のLoopback0（3.3.3.3）へのパケットが到達可能であることを確認する。 | ✅ PASS | ping 3.3.3.3 OK |
| R2からR3のLoopback0（3.3.3.3）へのパケットが到達可能であることを確認する。 | ✅ PASS | ping 3.3.3.3 OK |

### コア間リンク断時の冗長性確認 (R2 <-> R3) — ✅ PASS

**障害中テスト結果**

| テスト名 | 結果 | 詳細 |
|---|---|---|
| R1のOSPFネイバーがR2およびR3と確立していることを確認する。 | ✅ PASS | 2 neighbor(s) FULL |
| OSPF ネイバー数確認: R2 （障害中の期待値: 1） | ✅ PASS | 1 neighbor(s) FULL (expected: 1) |
| OSPF ネイバー数確認: R3 （障害中の期待値: 1） | ✅ PASS | 1 neighbor(s) FULL (expected: 1) |
| R1からR2のLoopback0（2.2.2.2）へのパケットが到達可能であることを確認する。 | ✅ PASS | ping 2.2.2.2 OK |
| R1からR3のLoopback0（3.3.3.3）へのパケットが到達可能であることを確認する。 | ✅ PASS | ping 3.3.3.3 OK |
| R2からR3のLoopback0（3.3.3.3）へのパケットが到達可能であることを確認する。 | ✅ PASS | ping 3.3.3.3 OK |

**復旧後テスト結果**

| テスト名 | 結果 | 詳細 |
|---|---|---|
| R1のOSPFネイバーがR2およびR3と確立していることを確認する。 | ✅ PASS | 2 neighbor(s) FULL |
| R2のOSPFネイバーがR1およびR3と確立していることを確認する。 | ✅ PASS | 2 neighbor(s) FULL |
| R3のOSPFネイバーがR1およびR2と確立していることを確認する。 | ✅ PASS | 2 neighbor(s) FULL |
| R1からR2のLoopback0（2.2.2.2）へのパケットが到達可能であることを確認する。 | ✅ PASS | ping 2.2.2.2 OK |
| R1からR3のLoopback0（3.3.3.3）へのパケットが到達可能であることを確認する。 | ✅ PASS | ping 3.3.3.3 OK |
| R2からR3のLoopback0（3.3.3.3）へのパケットが到達可能であることを確認する。 | ✅ PASS | ping 3.3.3.3 OK |
(agentic-ni) iida@s400win:~/git/agentic-ni$
```