## このセットで必ず実行するテスト（省略禁止）

以下4項目を**毎回すべて実行すること**（前回の試行結果に関わらず省略しない）:

1. R1 の `ospf_neighbors`
2. R2 の `ospf_neighbors`
3. R1 の `bgp_summary`（iBGPピアが Established になっているか）
4. R2 の `bgp_summary`（iBGPピアが Established になっているか）
5. R1 から `2.2.2.2` への `ping`
6. R2 から `1.1.1.1` への `ping`

前回BGPが失敗していても、次回も必ず `bgp_summary` を含めること。

## このセット固有の失敗パターン

- **BGPセッションが確立しない（最頻出ミス）**:
  - `update-source Loopback0` の設定漏れ（R1またはR2）
  - iBGPでLoopback IPをピアアドレスに使う場合、TCPセッションの送信元もLoopbackにしなければならない
  - OSPFがLoopbackアドレス（1.1.1.1/32、2.2.2.2/32）をアドバタイズできていない

- **pingが通らない**:
  - Loopbackアドレスが OSPF の `network` ステートメントに含まれていない
