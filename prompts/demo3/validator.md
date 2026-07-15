## このセットで必ず実行するテスト（省略禁止）

以下の 4 項目を**毎回すべて実行すること**:

1. R1 の `bgp_summary`（R2 との eBGP セッションが Established であること）
2. R2 の `bgp_summary`（R1 との eBGP セッションが Established であること）
3. R1 から `2.2.2.2` への `ping`（R2 Loopback 到達確認）
4. R2 から `1.1.1.1` への `ping`（R1 Loopback 到達確認）

## このセット固有の失敗パターンと対策

### BGP セッションが Established にならない場合

- ピアアドレスが間違っている可能性が高い（直結 GE0/0 のアドレスを使用すること）
- remote-as の番号が要件と一致していない
- GigabitEthernet0/0 が `shutdown` 状態

正しい設定例:
```
router bgp 65001
 neighbor 10.0.12.2 remote-as 65002
 address-family ipv4
  neighbor 10.0.12.2 activate
```

### ping が通らない場合

- BGP ルートが学習されていない → `bgp_summary` テストで Established を確認
- `network` ステートメントが抜けている（各ルータはLoopback0アドレスをアドバタイズすること）
- Loopback0 インターフェースが設定されていない

## 注意事項

- このデモではトポロジーYAMLは手動で事前に作成されており、変更不要
- コンフィグのみ検証・修正の対象とする
