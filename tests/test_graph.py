"""グラフ骨格と条件分岐のユニットテスト。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentic_ni.graph import MAX_RETRIES, build_graph, compile_graph, should_continue
from agentic_ni.state import AgentState
from agentic_ni.agents.architect import DeviceConfig


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------


def _base_state(**overrides) -> AgentState:
    base: AgentState = {
        "requirement": "テスト要件",
        "topology_yaml": "",
        "device_configs": {},
        "lab_id": "",
        "test_results": [],
        "error_log": "",
        "retry_count": 0,
        "final_report": "",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# should_continue のテスト
# ---------------------------------------------------------------------------


def test_should_continue_returns_complete_when_all_pass():
    state = _base_state(
        test_results=[
            {"test": "ping", "result": "PASS", "detail": ""},
            {"test": "ospf_neighbor", "result": "PASS", "detail": ""},
        ]
    )
    assert should_continue(state) == "complete"


def test_should_continue_returns_redesign_when_fail():
    state = _base_state(
        test_results=[
            {"test": "ping", "result": "PASS", "detail": ""},
            {"test": "ospf_neighbor", "result": "FAIL", "detail": "neighbor down"},
        ],
        retry_count=1,
    )
    assert should_continue(state) == "redesign"


def test_should_continue_returns_escalate_when_max_retries():
    state = _base_state(
        test_results=[
            {"test": "ping", "result": "FAIL", "detail": "timeout"},
        ],
        retry_count=MAX_RETRIES,
    )
    assert should_continue(state) == "escalate"


def test_should_continue_escalates_immediately_on_tool_errors():
    """テスト実行エラー（インフラエラー）の場合はリトライせず即時エスカレーション。"""
    state = _base_state(
        test_results=[
            {"test": "ospf", "result": "FAIL", "detail": "テスト実行エラー: SchemaError"},
            {"test": "ping",  "result": "FAIL", "detail": "テスト実行エラー: ConnectionFailed"},
        ],
        retry_count=1,
    )
    assert should_continue(state) == "escalate"


def test_should_continue_returns_redesign_when_no_results():
    """テスト結果が空の場合は redesign（初回ループ）。"""
    state = _base_state(test_results=[], retry_count=0)
    assert should_continue(state) == "redesign"


# ---------------------------------------------------------------------------
# グラフ構築のテスト
# ---------------------------------------------------------------------------


def test_build_graph_returns_state_graph():
    from langgraph.graph import StateGraph

    graph = build_graph()
    assert isinstance(graph, StateGraph)


def test_compile_graph_succeeds():
    """グラフがエラーなくコンパイルできることを確認。"""
    app = compile_graph()
    assert app is not None


def test_graph_mermaid_output():
    """グラフをMermaid形式で可視化できることを確認。"""
    app = compile_graph()
    mermaid = app.get_graph().draw_mermaid()
    assert "architect" in mermaid
    assert "validator" in mermaid
    assert "report" in mermaid
    assert "escalate" in mermaid


# ---------------------------------------------------------------------------
# スタブノードの結合テスト（実エージェントなし）
# ---------------------------------------------------------------------------


def test_stub_graph_escalates_after_max_retries():
    """architect/validatorをモックし、MAX_RETRIES後にエスカレーションされることを確認。"""
    from agentic_ni.agents.architect import DesignOutput
    from agentic_ni.agents.validator import TestPlan

    mock_design = DesignOutput(
        topology_yaml="lab:\n  title: test\n",
        device_configs=[DeviceConfig(device_name="R1", config_text="hostname R1\n")],
        design_rationale="テスト",
    )

    mock_plan = TestPlan(tests=[], rationale="テスト計画")

    def make_structured_llm(model, **kwargs):
        m = MagicMock()
        if model is DesignOutput:
            m.invoke.return_value = mock_design
        else:
            m.invoke.return_value = mock_plan
        return m

    with patch("agentic_ni.agents.architect.get_llm") as mock_arch_llm, \
         patch("agentic_ni.agents.validator.get_llm") as mock_val_llm, \
         patch("agentic_ni.agents.validator._deploy", return_value="lab-001"), \
         patch("agentic_ni.tools.pyats_tools.build_testbed", return_value="testbed: {}\n"):

        mock_arch_llm.return_value.with_structured_output.side_effect = make_structured_llm
        mock_val_llm.return_value.with_structured_output.side_effect = make_structured_llm

        app = compile_graph()
        state = _base_state(requirement="OSPFテスト")
        result = app.invoke(state)

    assert "エスカレーション" in result["final_report"]
    assert result["retry_count"] >= MAX_RETRIES


def test_stub_graph_completes_when_tests_pass():
    """validatorがPASS結果を返したときにレポートノードに到達することを確認。"""
    from agentic_ni.agents.architect import DesignOutput

    mock_design = DesignOutput(
        topology_yaml="lab:\n  title: test\n",
        device_configs=[DeviceConfig(device_name="R1", config_text="hostname R1\n")],
        design_rationale="テスト",
    )

    # validator.run() を直接モックして PASS 結果を返す
    validator_pass_result = {
        "lab_id": "lab-001",
        "test_results": [
            {"test": "ospf_neighbor", "result": "PASS", "detail": "2 neighbors up"},
        ],
        "error_log": "",
        "retry_count": 1,
    }

    with patch("agentic_ni.agents.architect.get_llm") as mock_arch_llm, \
         patch("agentic_ni.agents.validator.run", return_value=validator_pass_result):

        mock_structured = MagicMock()
        mock_structured.invoke.return_value = mock_design
        mock_arch_llm.return_value.with_structured_output.return_value = mock_structured

        app = compile_graph()
        state = _base_state(requirement="OSPFテスト")
        result = app.invoke(state)

    assert "検証成功" in result["final_report"]
