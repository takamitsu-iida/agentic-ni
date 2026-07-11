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


# ---------------------------------------------------------------------------
# Q2: 中断時のラボ自動クリーンアップのテスト
# ---------------------------------------------------------------------------


class TestLabCleanup:
    """_register_lab_for_cleanup / _perform_lab_cleanup / validator_node の統合テスト。"""

    def setup_method(self):
        """各テスト前にクリーンアップレジストリを初期化する。"""
        from agentic_ni import graph
        graph._cleanup_lab_ids.clear()

    def test_register_lab_adds_to_registry(self):
        """_register_lab_for_cleanup がラボ ID をリストに追加すること。"""
        from agentic_ni.graph import _register_lab_for_cleanup, _cleanup_lab_ids
        _register_lab_for_cleanup("lab-001")
        assert "lab-001" in _cleanup_lab_ids

    def test_register_lab_no_duplicate(self):
        """同一 lab_id を 2 回登録しても重複しないこと。"""
        from agentic_ni.graph import _register_lab_for_cleanup, _cleanup_lab_ids
        _register_lab_for_cleanup("lab-dup")
        _register_lab_for_cleanup("lab-dup")
        assert _cleanup_lab_ids.count("lab-dup") == 1

    def test_register_empty_string_ignored(self):
        """空文字は登録されないこと。"""
        from agentic_ni.graph import _register_lab_for_cleanup, _cleanup_lab_ids
        _register_lab_for_cleanup("")
        assert _cleanup_lab_ids == []

    def test_perform_cleanup_calls_delete(self):
        """_perform_lab_cleanup が cml_tools.delete_lab を呼び出すこと。"""
        from agentic_ni.graph import _register_lab_for_cleanup, _perform_lab_cleanup
        _register_lab_for_cleanup("lab-to-delete")

        with patch("agentic_ni.tools.cml_tools.delete_lab") as mock_delete:
            _perform_lab_cleanup()

        mock_delete.assert_called_once_with("lab-to-delete")

    def test_perform_cleanup_removes_from_registry(self):
        """クリーンアップ後にレジストリが空になること。"""
        from agentic_ni.graph import _register_lab_for_cleanup, _perform_lab_cleanup, _cleanup_lab_ids
        _register_lab_for_cleanup("lab-cleanup")

        with patch("agentic_ni.tools.cml_tools.delete_lab"):
            _perform_lab_cleanup()

        assert _cleanup_lab_ids == []

    def test_perform_cleanup_noop_when_empty(self):
        """レジストリが空の場合は何もしないこと。"""
        from agentic_ni.graph import _perform_lab_cleanup
        # delete_lab が呼ばれないことを確認
        with patch("agentic_ni.tools.cml_tools.delete_lab") as mock_delete:
            _perform_lab_cleanup()
        mock_delete.assert_not_called()

    def test_perform_cleanup_tolerates_delete_failure(self):
        """delete_lab が例外を投げてもプロセスが継続すること。"""
        from agentic_ni.graph import _register_lab_for_cleanup, _perform_lab_cleanup
        _register_lab_for_cleanup("lab-fail")

        with patch("agentic_ni.tools.cml_tools.delete_lab", side_effect=RuntimeError("CML接続失敗")):
            # 例外が外に出ないこと
            _perform_lab_cleanup()

    def test_validator_node_registers_new_lab(self):
        """validator_node が新規ラボを登録すること（lab_id が変化した場合）。"""
        from agentic_ni.graph import validator_node, _cleanup_lab_ids

        mock_result = {
            "lab_id": "lab-new",
            "test_results": [],
            "error_log": "",
            "retry_count": 1,
        }
        with patch("agentic_ni.agents.validator.run", return_value=mock_result):
            # 初回デプロイ: state の lab_id が空 → 新規ラボが登録される
            state = _base_state(lab_id="")
            validator_node(state)

        assert "lab-new" in _cleanup_lab_ids

    def test_validator_node_skips_existing_lab(self):
        """validator_node が既存ラボ（troubleshoot/analyze 用）を登録しないこと。"""
        from agentic_ni.graph import validator_node, _cleanup_lab_ids

        existing_id = "lab-existing"
        mock_result = {
            "lab_id": existing_id,
            "test_results": [],
            "error_log": "",
            "retry_count": 1,
        }
        with patch("agentic_ni.agents.validator.run", return_value=mock_result):
            # lab_id が変わらない場合（既存ラボ再利用）は登録しない
            state = _base_state(lab_id=existing_id)
            validator_node(state)

        assert _cleanup_lab_ids == []
