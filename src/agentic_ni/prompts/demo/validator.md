## このセットで必ず実行するテスト（省略禁止）

以下7項目を**毎回すべて実行すること**（前回の試行結果に関わらず省略しない）:

1. R1 の `ospf_neighbors`
2. R2 の `ospf_neighbors`
3. R3 の `ospf_neighbors`
4. R1 の `bgp_summary`（iBGPピアが Established になっているか）
5. R3 の `bgp_summary`（iBGPピアが Established になっているか）
6. R1 から `3.3.3.3` への `ping`
7. R3 から `1.1.1.1` への `ping`

前回BGPが失敗していても、次回も必ず `bgp_summary` を含めること。
前回pingが失敗していても、次回も必ず `ping` を含めること。

## このセット固有の失敗パターン

- **BGPセッションが確立しない（最頻出ミス）**:
  - `update-source Loopback0` の設定漏れ（R1またはR3）
  - BGPピアアドレスがインターフェースIPになっている（Loopback0を使うべき）
  - OSPFがLoopbackアドレス（1.1.1.1/32、3.3.3.3/32）をアドバタイズできていない

- **pingが通らない**:
  - Loopbackアドレスが OSPF の `network` ステートメントに含まれていない
  - R2 の中継ルーティングが正しく設定されていない
