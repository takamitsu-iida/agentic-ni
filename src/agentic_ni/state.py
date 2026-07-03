"""エージェント間で共有される LangGraph State の定義。"""

from __future__ import annotations

from typing import TypedDict


class TestResult(TypedDict):
    """テスト1件分の結果。"""

    test: str    # テスト名（例: "ospf_neighbor", "ping"）
    result: str  # "PASS" または "FAIL"
    detail: str  # 詳細メッセージ


class AgentState(TypedDict):
    """グラフ全体で共有されるステート。"""

    # --- 入力 ---
    requirement: str
    """人間が入力した要件（自然言語）。"""

    # --- 設計エージェント出力 ---
    topology_yaml: str
    """CMLに読み込ませるトポロジー定義（YAML文字列）。"""

    device_configs: dict[str, str]
    """機器ごとのコンフィグテキスト。キーはデバイス名。例: {"R1": "hostname R1\\n..."}"""

    # --- 検証エージェント出力 ---
    lab_id: str
    """デプロイ後に格納されるCMLラボID。"""

    test_results: list[TestResult]
    """テスト結果の一覧。"""

    error_log: str
    """失敗時の詳細ログ（原因推論を含む）。設計エージェントへのフィードバックに使用。"""

    # --- ループ管理 ---
    retry_count: int
    """設計・検証ループの試行回数。MAX_RETRIES を超えたらエスカレーション。"""

    # --- 最終出力 ---
    final_report: str
    """全PASS時またはエスカレーション時に生成される最終レポート。"""
