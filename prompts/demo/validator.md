## このセットで必ず実行するテスト（省略禁止）

以下6項目を**毎回すべて実行すること**（前回の試行結果に関わらず省略しない）:

1. R1 の `ospf_neighbors`
2. R2 の `ospf_neighbors`
3. R1 の `bgp_summary`（iBGPピアが Established になっているか）
4. R2 の `bgp_summary`（iBGPピアが Established になっているか）
5. R1 から `2.2.2.2` への `ping`
6. R2 から `1.1.1.1` への `ping`

前回BGPが失敗していても、次回も必ず `bgp_summary` を含めること。

## このセット固有の失敗パターンと対策

### BGPセッションが確立しない場合

**機器コンフィグの `router bgp` セクションを必ず確認すること。**

よくある構文ミス（これは IOS で無効）:
```
router bgp 65000
 neighbor 2.2.2.2 remote-as 65000
 update-source Loopback0          ← 誤: neighbor なしの単独コマンドは機能しない
```

正しい構文（neighbor ごとに指定する）:
```
router bgp 65000
 neighbor 2.2.2.2 remote-as 65000
 neighbor 2.2.2.2 update-source Loopback0   ← 正: neighbor X.X.X.X を必ず前置する
```

- `update-source Loopback0` は `neighbor X.X.X.X` なしではグローバルBGP設定として無効
- iBGPでLoopbackアドレスをピアアドレスとして使う場合は、必ず `neighbor X.X.X.X update-source Loopback0` の形式で設定する
- OSPFがLoopbackアドレス（1.1.1.1/32、2.2.2.2/32）をアドバタイズできていないと、BGPの到達性も確保できない

### pingが通らない場合

- Loopbackアドレスが OSPF の `network` ステートメントに含まれていない
