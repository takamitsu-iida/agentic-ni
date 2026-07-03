"""E2Eテスト: 3シナリオのフルパイプライン検証（LLM/CML/pyATSはすべてモック）。

シナリオ1: 正常系 — 1回目のループで全テストPASS → 検証成功レポート
シナリオ2: 異常系 — 1回目FAIL（OSPFエリア番号ミスマッチ） → 修正 → 2回目PASS → 成功
シナリオ3: 上限超過 — 常にFAIL → MAX_RETRIES後にエスカレーションレポート
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentic_ni.agents.architect import DesignOutput
from agentic_ni.graph import MAX_RETRIES, compile_graph, initial_state
from agentic_ni.state import AgentState, TestResult


# ---------------------------------------------------------------------------
# テスト用ヘルパー
# ---------------------------------------------------------------------------

_TOPOLOGY_YAML = "lab:\n  title: OSPF Lab\n"
_CONFIGS_V1 = {
    "R1": "hostname R1\nrouter ospf 1\n network 10.0.0.0 0.0.0.3 area 0\n",
    "R2": "hostname R2\nrouter ospf 1\n network 10.0.0.0 0.0.0.3 area 1\n",  # area 1 がバグ
}
_CONFIGS_V2 = {
    "R1": "hostname R1\nrouter ospf 1\n network 10.0.0.0 0.0.0.3 area 0\n",
    "R2": "hostname R2\nrouter ospf 1\n network 10.0.0.0 0.0.0.3 area 0\n",  # area 0 に修正
}

_DESIGN_V1 = DesignOutput(
    topology_yaml=_TOPOLOGY_YAML,
    device_configs=_CONFIGS_V1,
    design_rationale="初回設計（R2のエリア番号にバグあり）",
)
_DESIGN_V2 = DesignOutput(
    topology_yaml=_TOPOLOGY_YAML,
    device_configs=_CONFIGS_V2,
    design_rationale="修正設計（R2のエリア番号を0に修正）",
)

_PASS_RESULTS: list[TestResult] = [
    {"test": "OSPF R1 ネイバー確認", "result": "PASS", "detail": "1 neighbor(s) FULL"},
    {"test": "OSPF R2 ネイバー確認", "result": "PASS", "detail": "1 neighbor(s) FULL"},
    {"test": "R1→R2 疎通確認", "result": "PASS", "detail": "ping 10.0.0.2 OK"},
]
_FAIL_RESULTS: list[TestResult] = [
    {"test": "OSPF R1 ネイバー確認", "result": "FAIL", "detail": "neighbors_up=0"},
    {"test": "OSPF R2 ネイバー確認", "result": "FAIL", "detail": "neighbors_up=0"},
    {"test": "R1→R2 疎通確認", "result": "FAIL", "detail": "ping 10.0.0.2 FAILED"},
]


def _make_architect_mock(designs: list[DesignOutput]):
    """呼び出し順にdesignを返すarchitectモックを構築する。"""
    call_iter = iter(designs)

    def _run(state: AgentState) -> dict:
        design = next(call_iter)
        return {
            "topology_yaml": design.topology_yaml,
            "device_configs": design.device_configs,
            "error_log": "",
        }

    return _run


def _make_validator_mock(results_sequence: list[list[TestResult]], error_logs: list[str] | None = None):
    """呼び出し順にtest_resultsを返すvalidatorモックを構築する。"""
    _errors = error_logs or [""] * len(results_sequence)
    call_idx = 0

    def _run(state: AgentState) -> dict:
        nonlocal call_idx
        idx = min(call_idx, len(results_sequence) - 1)
        results = results_sequence[idx]
        error = _errors[idx]
        call_idx += 1
        return {
            "lab_id": f"lab-{idx:03d}",
            "test_results": results,
            "error_log": error,
            "retry_count": state.get("retry_count", 0) + 1,
        }

    return _run


# ---------------------------------------------------------------------------
# シナリオ1: 正常系 — 1回のループで全PASS
# ---------------------------------------------------------------------------


class TestScenario1NormalFlow:
    """正常系: 最初のループで全テストPASSし、検証成功レポートが出力される。"""

    def test_produces_success_report(self):
        arch_mock = _make_architect_mock([_DESIGN_V1])
        val_mock = _make_validator_mock([_PASS_RESULTS])

        with patch("agentic_ni.agents.architect.run", side_effect=arch_mock), \
             patch("agentic_ni.agents.validator.run", side_effect=val_mock):

            app = compile_graph()
            result = app.invoke(initial_state("R1とR2をOSPFで接続する"))

        assert "検証成功" in result["final_report"]
        assert result["retry_count"] == 1

    def test_report_contains_test_names(self):
        arch_mock = _make_architect_mock([_DESIGN_V1])
        val_mock = _make_validator_mock([_PASS_RESULTS])

        with patch("agentic_ni.agents.architect.run", side_effect=arch_mock), \
             patch("agentic_ni.agents.validator.run", side_effect=val_mock):

            app = compile_graph()
            result = app.invoke(initial_state("R1とR2をOSPFで接続する"))

        report = result["final_report"]
        assert "OSPF R1 ネイバー確認" in report
        assert "OSPF R2 ネイバー確認" in report
        assert "R1→R2 疎通確認" in report

    def test_report_contains_lab_id(self):
        arch_mock = _make_architect_mock([_DESIGN_V1])
        val_mock = _make_validator_mock([_PASS_RESULTS])

        with patch("agentic_ni.agents.architect.run", side_effect=arch_mock), \
             patch("agentic_ni.agents.validator.run", side_effect=val_mock):

            app = compile_graph()
            result = app.invoke(initial_state("R1とR2をOSPFで接続する"))

        assert "lab-000" in result["final_report"]

    def test_report_contains_requirement(self):
        arch_mock = _make_architect_mock([_DESIGN_V1])
        val_mock = _make_validator_mock([_PASS_RESULTS])
        req = "R1とR2をOSPFで接続し冗長化する"

        with patch("agentic_ni.agents.architect.run", side_effect=arch_mock), \
             patch("agentic_ni.agents.validator.run", side_effect=val_mock):

            app = compile_graph()
            result = app.invoke(initial_state(req))

        assert req in result["final_report"]


# ---------------------------------------------------------------------------
# シナリオ2: 異常系 — FAIL → 修正ループ → PASS
# ---------------------------------------------------------------------------


class TestScenario2RecoveryFlow:
    """異常系: 1回目FAILで設計修正が走り、2回目でPASSして成功レポートが出る。"""

    def test_recovers_after_one_failure(self):
        """1回失敗 → architect修正 → 2回目でPASS。"""
        arch_mock = _make_architect_mock([_DESIGN_V1, _DESIGN_V2])
        val_mock = _make_validator_mock(
            results_sequence=[_FAIL_RESULTS, _PASS_RESULTS],
            error_logs=[
                "## 根本原因\nR2のOSPFエリア番号が1になっており、R1のarea 0と不一致。\n\n## 修正依頼\nR2のnetwork ... area 1 を area 0 に変更してください。",
                "",
            ],
        )

        with patch("agentic_ni.agents.architect.run", side_effect=arch_mock), \
             patch("agentic_ni.agents.validator.run", side_effect=val_mock):

            app = compile_graph()
            result = app.invoke(initial_state("R1とR2をOSPFで接続する"))

        assert "検証成功" in result["final_report"]
        assert result["retry_count"] == 2

    def test_second_design_uses_corrected_configs(self):
        """2回目のarchitect呼び出しに修正版コンフィグが設定されること。"""
        arch_calls: list[AgentState] = []

        def track_arch(state: AgentState) -> dict:
            arch_calls.append(dict(state))
            designs = [_DESIGN_V1, _DESIGN_V2]
            design = designs[len(arch_calls) - 1]
            return {
                "topology_yaml": design.topology_yaml,
                "device_configs": design.device_configs,
                "error_log": "",
            }

        val_mock = _make_validator_mock(
            results_sequence=[_FAIL_RESULTS, _PASS_RESULTS],
            error_logs=["## 根本原因\nエリア番号ミスマッチ\n\n## 修正依頼\nR2を area 0 に変更", ""],
        )

        with patch("agentic_ni.agents.architect.run", side_effect=track_arch), \
             patch("agentic_ni.agents.validator.run", side_effect=val_mock):

            app = compile_graph()
            app.invoke(initial_state("R1とR2をOSPFで接続する"))

        # 2回目のarchitect呼び出しにはerror_logが渡っていること
        assert len(arch_calls) == 2
        assert "エリア番号ミスマッチ" in arch_calls[1].get("error_log", "")

    def test_report_shows_retry_count(self):
        arch_mock = _make_architect_mock([_DESIGN_V1, _DESIGN_V2])
        val_mock = _make_validator_mock(
            results_sequence=[_FAIL_RESULTS, _PASS_RESULTS],
            error_logs=["## 根本原因\nエリア番号ミスマッチ\n## 修正依頼\n修正してください", ""],
        )

        with patch("agentic_ni.agents.architect.run", side_effect=arch_mock), \
             patch("agentic_ni.agents.validator.run", side_effect=val_mock):

            app = compile_graph()
            result = app.invoke(initial_state("R1とR2をOSPFで接続する"))

        assert "2" in result["final_report"]  # 試行回数2が含まれる


# ---------------------------------------------------------------------------
# シナリオ3: 上限超過 — 常にFAIL → エスカレーション
# ---------------------------------------------------------------------------


class TestScenario3EscalationFlow:
    """上限超過: 常にFAILが続きMAX_RETRIES後にエスカレーションレポートが出る。"""

    def test_escalates_after_max_retries(self):
        arch_mock = _make_architect_mock([_DESIGN_V1] * (MAX_RETRIES + 1))
        val_mock = _make_validator_mock(
            results_sequence=[_FAIL_RESULTS] * (MAX_RETRIES + 1),
            error_logs=["## 根本原因\n修正不能なエラー\n## 修正依頼\n不明"] * (MAX_RETRIES + 1),
        )

        with patch("agentic_ni.agents.architect.run", side_effect=arch_mock), \
             patch("agentic_ni.agents.validator.run", side_effect=val_mock):

            app = compile_graph()
            result = app.invoke(initial_state("修正不能な要件"))

        assert "エスカレーション" in result["final_report"]
        assert result["retry_count"] >= MAX_RETRIES

    def test_escalation_report_contains_error_log(self):
        arch_mock = _make_architect_mock([_DESIGN_V1] * (MAX_RETRIES + 1))
        val_mock = _make_validator_mock(
            results_sequence=[_FAIL_RESULTS] * (MAX_RETRIES + 1),
            error_logs=["## 根本原因\n特定の修正不能エラー\n## 修正依頼\n不明"] * (MAX_RETRIES + 1),
        )

        with patch("agentic_ni.agents.architect.run", side_effect=arch_mock), \
             patch("agentic_ni.agents.validator.run", side_effect=val_mock):

            app = compile_graph()
            result = app.invoke(initial_state("修正不能な要件"))

        assert "修正不能エラー" in result["final_report"]

    def test_escalation_report_contains_recommended_actions(self):
        arch_mock = _make_architect_mock([_DESIGN_V1] * (MAX_RETRIES + 1))
        val_mock = _make_validator_mock(
            results_sequence=[_FAIL_RESULTS] * (MAX_RETRIES + 1),
            error_logs=["## 根本原因\nエラー\n## 修正依頼\n不明"] * (MAX_RETRIES + 1),
        )

        with patch("agentic_ni.agents.architect.run", side_effect=arch_mock), \
             patch("agentic_ni.agents.validator.run", side_effect=val_mock):

            app = compile_graph()
            result = app.invoke(initial_state("修正不能な要件"))

        # エスカレーションレポートに推奨アクションが含まれること
        assert "推奨アクション" in result["final_report"]
        assert str(MAX_RETRIES) in result["final_report"]

    def test_escalation_report_contains_failed_tests(self):
        arch_mock = _make_architect_mock([_DESIGN_V1] * (MAX_RETRIES + 1))
        val_mock = _make_validator_mock(
            results_sequence=[_FAIL_RESULTS] * (MAX_RETRIES + 1),
            error_logs=["## 根本原因\nエラー\n## 修正依頼\n不明"] * (MAX_RETRIES + 1),
        )

        with patch("agentic_ni.agents.architect.run", side_effect=arch_mock), \
             patch("agentic_ni.agents.validator.run", side_effect=val_mock):

            app = compile_graph()
            result = app.invoke(initial_state("修正不能な要件"))

        assert "OSPF R1 ネイバー確認" in result["final_report"]


# ---------------------------------------------------------------------------
# Human-in-the-Loop のテスト
# ---------------------------------------------------------------------------


class TestHumanInTheLoop:
    """Human-in-the-Loop: interrupt → resume のフローをテストする。"""

    def test_compile_graph_interactive_succeeds(self):
        """Human-in-the-Loop ありのグラフが正常にコンパイルできること。"""
        from agentic_ni.graph import compile_graph_interactive

        app = compile_graph_interactive()
        assert app is not None

    def test_interactive_graph_contains_human_review_node(self):
        """human_review ノードがグラフに含まれること。"""
        from agentic_ni.graph import build_graph

        graph = build_graph(human_in_the_loop=True)
        app = graph.compile()
        mermaid = app.get_graph().draw_mermaid()
        assert "human_review" in mermaid

    def test_non_interactive_graph_excludes_human_review_node(self):
        """human_in_the_loop=False のグラフには human_review ノードが含まれないこと。"""
        from agentic_ni.graph import build_graph

        graph = build_graph(human_in_the_loop=False)
        app = graph.compile()
        mermaid = app.get_graph().draw_mermaid()
        assert "human_review" not in mermaid

    def test_interactive_graph_pauses_at_human_review(self):
        """interrupt() によりグラフが human_review で停止すること。"""
        from langgraph.checkpoint.memory import MemorySaver
        from langgraph.types import Command

        from agentic_ni.graph import build_graph

        arch_mock = _make_architect_mock([_DESIGN_V1])
        val_mock = _make_validator_mock([_PASS_RESULTS])

        graph = build_graph(human_in_the_loop=True)
        app = graph.compile(checkpointer=MemorySaver())

        config = {"configurable": {"thread_id": "test-thread-01"}}

        with patch("agentic_ni.agents.architect.run", side_effect=arch_mock), \
             patch("agentic_ni.agents.validator.run", side_effect=val_mock):

            events = list(app.stream(initial_state("OSPFテスト"), config))

        # __interrupt__ イベントが発生していること
        interrupt_events = [e for e in events if "__interrupt__" in e]
        assert len(interrupt_events) >= 1
        payload = interrupt_events[0]["__interrupt__"][0].value
        assert payload["type"] == "human_review"
        assert "最終レポートを確認" in payload["message"]

    def test_interactive_graph_resumes_after_approval(self):
        """承認後にグラフが完了しレポートが生成されること。"""
        from langgraph.checkpoint.memory import MemorySaver
        from langgraph.types import Command

        from agentic_ni.graph import build_graph

        arch_mock = _make_architect_mock([_DESIGN_V1])
        val_mock = _make_validator_mock([_PASS_RESULTS])

        graph = build_graph(human_in_the_loop=True)
        app = graph.compile(checkpointer=MemorySaver())

        config = {"configurable": {"thread_id": "test-thread-02"}}

        with patch("agentic_ni.agents.architect.run", side_effect=arch_mock), \
             patch("agentic_ni.agents.validator.run", side_effect=val_mock):

            # 1回目: interrupt まで実行
            list(app.stream(initial_state("OSPFテスト"), config))

            # 承認して再開
            final = app.invoke(Command(resume={"approved": True}), config)

        assert "検証成功" in final["final_report"]
