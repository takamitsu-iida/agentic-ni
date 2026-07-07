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


class TroubleshootFixRecord(TypedDict):
    """トラブルシューティングで適用した修正の一件分の記録。"""

    device: str                # 修正対象デバイス名
    commands: str              # 適用した configure terminal コマンド列
    rollback_commands: str     # ロールバック用の no コマンド（未使用時は空文字）
    success: bool              # 適用成功なら True
    error: str                 # エラーメッセージ（成功時は空文字）
    description: str           # この修正の目的説明


class LiveApplyRecord(TypedDict):
    """Phase I: 実機適用モードにおける 1 デバイス分の適用記録。"""

    device: str            # CML ノード名（インベントリのキー）
    host: str              # 実機の管理 IP アドレス
    apply_mode: str        # "config_merge" / "config_replace" / "incremental"

    # precheck 段階
    connectivity_ok: bool  # SSH 疎通確認結果
    backup_config: str     # バックアップ取得済み running-config（取得失敗時は空文字）
    backup_lines: int      # バックアップ行数（表示用）

    # apply 段階（live_apply_node 実行後に設定）
    applied_config: str    # 実際に投入したコンフィグテキスト
    apply_success: bool    # 投入成功フラグ
    apply_output: str      # 投入コマンドの出力
    apply_error: str       # 投入失敗時のエラーメッセージ（成功時は空文字）

    # rollback 段階（失敗時に自動実行）
    rollback_done: bool    # ロールバック実施済みフラグ
    rollback_error: str    # ロールバック失敗時のエラーメッセージ（成功・未実施は空文字）


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

    skip_deploy: bool
    """True の場合、検証エージェントのデプロイステップをスキップし既存ラボを再利用する。
    --fault-sim 時に同名ラボが存在する場合に自動設定される。"""

    fault_scenario_results: list[FaultScenarioResult]
    """Phase B で実行した各障害シナリオの結果。"""

    fault_report: str
    """Phase B の障害シミュレーション結果レポート（Markdown）。"""

    # --- Phase H: トラブルシューティングモード ---
    troubleshoot_lab_id: str
    """--troubleshoot で指定された既存ラボID（構成検証済みラボ）。"""

    troubleshoot_issue: str
    """ユーザーが報告した問題の説明（自然言語）。"""

    collected_state: dict
    """各機器から収集した現在の状態。
    {device_name: {\"running_config\": str, \"show_outputs\": {cmd: str}}}"""

    diagnosis: str
    """LLM による根本原因の診断結果。"""

    fix_records: list[TroubleshootFixRecord]
    """適用した修正の履歴リスト。"""

    troubleshoot_retry_count: int
    """診断→修正→検証のサイクル数。"""

    troubleshoot_report: str
    """トラブルシューティング完了時の詳細レポート（Markdown）。"""

    # --- Phase E: 分析・改善モード ---
    analyze_request: str
    """--improve で指定された改善要求テキスト（自然言語）。"""

    analysis_result: str
    """--analyze または --improve で生成された分析・改善計画のテキスト（Markdown）。"""

    # --- Phase I: 実機適用モード ---
    live_inventory_path: str
    """--apply-to-live で使用するインベントリ YAML のパス。
    省略時は inventory/<prompt_set>.yaml を自動使用する。"""


    live_apply_records: list[LiveApplyRecord]
    """precheck / apply / rollback の各フェーズで更新されるデバイスごとの記録リスト。"""

    live_verify_enabled: bool
    """True の場合、実機適用後に pyATS で同一テスト計画を実行する（--live-verify）。"""

    live_human_decision: str
    """human_confirm_live_node での Human の決定。
    "yes" = 承認 / "no" = 取り消し / "rollback-only" = ロールバックのみ / "" = 未決定。"""

    live_test_results: list[TestResult]
    """live_verify_node が実機に対して実行したテスト結果（--live-verify 時のみ設定）。"""

    live_report: str
    """実機適用完了時の詳細レポート（Markdown）。final_report に追記される。"""

    # --- 最終出力 ---
    final_report: str
    """全PASS時またはエスカレーション時に生成される最終レポート。"""
