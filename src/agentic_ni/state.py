"""エージェント間で共有される LangGraph State の定義。"""

from __future__ import annotations

from typing import TypedDict


class TestResult(TypedDict):
    """テスト1件分の結果。"""

    test: str    # テスト名（例: "ospf_neighbor", "ping"）
    result: str  # "PASS" または "FAIL"
    detail: str  # 詳細メッセージ


class FaultScenarioResult(TypedDict):
    """障害シミュレーション 1 シナリオ分の結果。"""

    scenario_name: str            # シナリオの説明（例: "R1-R2 リンク断"）
    link_id: str                  # 障害対象リンクの CML ID
    link_label: str               # 表示用ラベル（例: "R1 <-> R2"）
    tests_during_fault: list[TestResult]    # リンク断中のテスト結果
    tests_after_recovery: list[TestResult]  # リンク復旧後のテスト結果
    passed: bool                  # 復旧後に全テスト PASS なら True


class AgentState(TypedDict):
    """グラフ全体で共有されるステート。"""

    # --- 入力 ---
    requirement: str
    """人間が入力した要件（自然言語）。"""

    prompt_set: str
    """使用するプロンプトセット名。prompts/<prompt_set>/ ディレクトリのサブフォルダ名。デフォルト: 'default'"""

    use_rag: bool
    """過去の成功事例をベクトルRAGで検索して設計エージェントのプロンプトに追加するかどうか。"""

    error_history: list[str]
    """今回の実行中に発生した error_log の履歴。成功時にRAGストアに保存するために使用。"""

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

    test_plan_items: list[dict]
    """Phase A で立案・実行されたテスト計画（シリアライズ済み TestItem リスト）。
    Phase B 障害シミュレーションで同一テストを再利用するために保存する。"""

    error_log: str
    """失敗時の詳細ログ（原因推論を含む）。設計エージェントへのフィードバックに使用。"""

    # --- ループ管理 ---
    retry_count: int
    """設計・検証ループの試行回数。MAX_RETRIES を超えたらエスカレーション。"""

    # --- Phase B: 障害シミュレーション ---
    fault_simulation_enabled: bool
    """True の場合、Phase A 成功後に障害シミュレーションを実行する。"""

    fault_scenario_results: list[FaultScenarioResult]
    """Phase B で実行した各障害シナリオの結果。"""

    fault_report: str
    """Phase B の障害シミュレーション結果レポート（Markdown）。"""

    # --- 最終出力 ---
    final_report: str
    """全PASS時またはエスカレーション時に生成される最終レポート。"""
