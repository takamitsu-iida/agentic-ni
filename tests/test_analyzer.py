"""analyzer エージェントのユニットテスト。LLM/CML/pyATS はすべてモック。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentic_ni.agents.analyzer import (
    AnalysisIssue,
    AnalysisResult,
    ImprovementOutput,
    _build_analysis_messages,
    _build_improvement_messages,
    _format_collected_state,
    run_analyze,
    run_improve,
)
from agentic_ni.state import AgentState


# ---------------------------------------------------------------------------
# テスト用ヘルパー
# ---------------------------------------------------------------------------

_SAMPLE_LAB_ID = "lab-analyze-001"
_SAMPLE_RUNNING_CONFIG = (
    "hostname R1\n"
    "interface GigabitEthernet0/0\n"
    " ip address 10.0.12.1 255.255.255.252\n"
    " no shutdown\n"
    "router ospf 1\n"
    " network 0.0.0.0 255.255.255.255 area 0\n"
)
_SAMPLE_COLLECTED_STATE = {
    "R1": {
        "running_config": _SAMPLE_RUNNING_CONFIG,
        "show_outputs": {
            "show ip ospf neighbor": "Neighbor ID: 2.2.2.2  State: FULL",
            "show ip route": "O 2.2.2.2/32 [110/2] via 10.0.12.2",
            "show ip interface brief": "GigabitEthernet0/0  10.0.12.1  YES  up  up",
        },
    },
    "R2": {
        "running_config": "hostname R2\ninterface GigabitEthernet0/0\n ip address 10.0.12.2 255.255.255.252\n",
        "show_outputs": {},
    },
}


def _base_state(**overrides) -> AgentState:
    base: AgentState = {
        "requirement": "R1 と R2 を OSPF エリア 0 で接続する",
        "prompt_set": "demo",
        "fault_simulation_enabled": False,
        "error_history": [],
        "topology_yaml": "",
        "device_configs": {
            "R1": _SAMPLE_RUNNING_CONFIG,
            "R2": "hostname R2\n",
        },
        "lab_id": _SAMPLE_LAB_ID,
        "test_results": [],
        "test_plan_items": [],
        "error_log": "",
        "retry_count": 0,
        "fault_scenario_results": [],
        "fault_report": "",
        "troubleshoot_lab_id": _SAMPLE_LAB_ID,
        "troubleshoot_issue": "",
        "collected_state": _SAMPLE_COLLECTED_STATE,
        "diagnosis": "",
        "fix_records": [],
        "troubleshoot_retry_count": 0,
        "troubleshoot_report": "",
        "analyze_request": "",
        "analysis_result": "",
        "final_report": "",
    }
    base.update(overrides)
    return base


def _make_analysis_result() -> AnalysisResult:
    return AnalysisResult(
        overall_rating="needs_improvement",
        summary="基本的な OSPF 接続は機能しているが、router-id が未設定であり安定性に懸念がある。",
        issues=[
            AnalysisIssue(
                severity="warning",
                device="R1",
                description="router-id が明示的に設定されていない。",
                recommendation="router ospf 1 配下で router-id 1.1.1.1 を設定する。",
            ),
            AnalysisIssue(
                severity="info",
                device="all",
                description="no ip domain-lookup が設定されていない。",
                recommendation="グローバルコンフィグで no ip domain-lookup を追加する。",
            ),
        ],
        improvement_suggestions=[
            "Loopback インターフェースを追加して router-id を安定させる",
            "no ip domain-lookup を設定して誤入力による待ち時間を防ぐ",
        ],
    )


def _make_improvement_output() -> ImprovementOutput:
    improved_r1 = (
        _SAMPLE_RUNNING_CONFIG
        + "interface Loopback0\n ip address 1.1.1.1 255.255.255.255\n"
        + "router ospf 1\n router-id 1.1.1.1\n"
    )
    return ImprovementOutput(
        device_configs={
            "R1": improved_r1,
            "R2": "hostname R2\ninterface GigabitEthernet0/0\n ip address 10.0.12.2 255.255.255.252\ninterface Loopback0\n ip address 2.2.2.2 255.255.255.255\nrouter ospf 1\n router-id 2.2.2.2\n",
        },
        changes_summary=[
            "R1 に Loopback0 (1.1.1.1/32) を追加した",
            "R2 に Loopback0 (2.2.2.2/32) を追加した",
            "R1・R2 の router ospf 1 で router-id を明示設定した",
        ],
        rationale="Loopback インターフェースを router-id に使用することで、物理リンク障害時も OSPF が安定して動作する。",
    )


def _make_llm_mock_analysis(analysis: AnalysisResult) -> MagicMock:
    mock_structured = MagicMock()
    mock_structured.invoke.return_value = analysis
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured
    return mock_llm


def _make_llm_mock_improvement(output: ImprovementOutput) -> MagicMock:
    mock_structured = MagicMock()
    mock_structured.invoke.return_value = output
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured
    return mock_llm


# ---------------------------------------------------------------------------
# スキーマテスト
# ---------------------------------------------------------------------------


def test_analysis_issue_schema_critical():
    issue = AnalysisIssue(
        severity="critical",
        device="R1",
        description="重大な問題",
        recommendation="対応策",
    )
    assert issue.severity == "critical"
    assert issue.device == "R1"


def test_analysis_issue_schema_all_devices():
    issue = AnalysisIssue(
        severity="info",
        device="all",
        description="全体的な問題",
        recommendation="対応策",
    )
    assert issue.device == "all"


def test_analysis_result_schema():
    result = _make_analysis_result()
    assert result.overall_rating == "needs_improvement"
    assert len(result.issues) == 2
    assert result.issues[0].severity == "warning"
    assert result.issues[1].severity == "info"
    assert len(result.improvement_suggestions) == 2


def test_analysis_result_good_rating():
    result = AnalysisResult(
        overall_rating="good",
        summary="設計品質は高い。",
        issues=[],
        improvement_suggestions=[],
    )
    assert result.overall_rating == "good"
    assert result.issues == []
    assert result.improvement_suggestions == []


def test_improvement_output_schema():
    output = _make_improvement_output()
    assert "R1" in output.device_configs
    assert "R2" in output.device_configs
    assert "Loopback0" in output.device_configs["R1"]
    assert len(output.changes_summary) == 3


def test_improvement_output_empty_changes():
    output = ImprovementOutput(
        device_configs={"R1": _SAMPLE_RUNNING_CONFIG},
        changes_summary=[],
        rationale="変更不要。",
    )
    assert output.changes_summary == []
    assert output.device_configs == {"R1": _SAMPLE_RUNNING_CONFIG}


# ---------------------------------------------------------------------------
# _format_collected_state テスト
# ---------------------------------------------------------------------------


def test_format_collected_state_normal():
    text = _format_collected_state(_SAMPLE_COLLECTED_STATE)
    assert "R1" in text
    assert "running-config" in text
    assert "R2" in text


def test_format_collected_state_with_error():
    state = {"R1": {"error": "接続タイムアウト"}}
    text = _format_collected_state(state)
    assert "接続タイムアウト" in text
    assert "R1" in text


def test_format_collected_state_empty():
    text = _format_collected_state({})
    assert text == "(状態なし)"


def test_format_collected_state_truncates_long_config():
    long_cfg = "A" * 3000
    state = {"R1": {"running_config": long_cfg, "show_outputs": {}}}
    text = _format_collected_state(state)
    assert "省略" in text
    assert len(text) < 3000 + 500  # 大幅に短縮されていること


# ---------------------------------------------------------------------------
# _build_analysis_messages テスト
# ---------------------------------------------------------------------------


def test_build_analysis_messages_has_two_messages():
    state = _base_state()
    messages = _build_analysis_messages(state, _SAMPLE_COLLECTED_STATE)
    assert len(messages) == 2


def test_build_analysis_messages_roles():
    state = _base_state()
    messages = _build_analysis_messages(state, _SAMPLE_COLLECTED_STATE)
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"


def test_build_analysis_messages_contains_lab_id():
    state = _base_state()
    messages = _build_analysis_messages(state, _SAMPLE_COLLECTED_STATE)
    assert _SAMPLE_LAB_ID in messages[1]["content"]


def test_build_analysis_messages_contains_device_state():
    state = _base_state()
    messages = _build_analysis_messages(state, _SAMPLE_COLLECTED_STATE)
    assert "R1" in messages[1]["content"]
    assert "hostname R1" in messages[1]["content"]


def test_build_analysis_messages_contains_requirement():
    state = _base_state()
    messages = _build_analysis_messages(state, _SAMPLE_COLLECTED_STATE)
    assert "OSPF エリア 0" in messages[1]["content"]


# ---------------------------------------------------------------------------
# _build_improvement_messages テスト
# ---------------------------------------------------------------------------


def test_build_improvement_messages_with_request():
    state = _base_state(analyze_request="OSPF に BFD を追加したい")
    messages = _build_improvement_messages(state, _SAMPLE_COLLECTED_STATE)
    assert "BFD" in messages[1]["content"]
    assert "改善計画" in messages[1]["content"]


def test_build_improvement_messages_no_request():
    state = _base_state(analyze_request="")
    messages = _build_improvement_messages(state, _SAMPLE_COLLECTED_STATE)
    assert "改善要求なし" in messages[1]["content"]


def test_build_improvement_messages_contains_device_state():
    state = _base_state(analyze_request="Loopback を追加")
    messages = _build_improvement_messages(state, _SAMPLE_COLLECTED_STATE)
    assert "R1" in messages[1]["content"]
    assert "hostname R1" in messages[1]["content"]


# ---------------------------------------------------------------------------
# run_analyze テスト
# ---------------------------------------------------------------------------


@patch("agentic_ni.agents.analyzer.get_llm")
def test_run_analyze_returns_analysis_result_key(mock_get_llm):
    mock_get_llm.return_value = _make_llm_mock_analysis(_make_analysis_result())
    result = run_analyze(_base_state())
    assert "analysis_result" in result


@patch("agentic_ni.agents.analyzer.get_llm")
def test_run_analyze_includes_rating(mock_get_llm):
    mock_get_llm.return_value = _make_llm_mock_analysis(_make_analysis_result())
    result = run_analyze(_base_state())
    text = result["analysis_result"]
    # 評価ラベルが含まれること（英語 or 日本語）
    assert "needs_improvement" in text or "要改善" in text


@patch("agentic_ni.agents.analyzer.get_llm")
def test_run_analyze_includes_issues(mock_get_llm):
    mock_get_llm.return_value = _make_llm_mock_analysis(_make_analysis_result())
    result = run_analyze(_base_state())
    assert "router-id" in result["analysis_result"]


@patch("agentic_ni.agents.analyzer.get_llm")
def test_run_analyze_includes_suggestions(mock_get_llm):
    mock_get_llm.return_value = _make_llm_mock_analysis(_make_analysis_result())
    result = run_analyze(_base_state())
    assert "Loopback" in result["analysis_result"]


@patch("agentic_ni.agents.analyzer.get_llm")
def test_run_analyze_good_rating(mock_get_llm):
    good_result = AnalysisResult(
        overall_rating="good",
        summary="設計品質は高い。",
        issues=[],
        improvement_suggestions=[],
    )
    mock_get_llm.return_value = _make_llm_mock_analysis(good_result)
    result = run_analyze(_base_state())
    assert "good" in result["analysis_result"] or "良好" in result["analysis_result"]


@patch("agentic_ni.agents.analyzer.get_llm")
def test_run_analyze_no_issues_shows_placeholder(mock_get_llm):
    no_issue_result = AnalysisResult(
        overall_rating="good",
        summary="問題なし。",
        issues=[],
        improvement_suggestions=[],
    )
    mock_get_llm.return_value = _make_llm_mock_analysis(no_issue_result)
    result = run_analyze(_base_state())
    assert "問題なし" in result["analysis_result"]


@patch("agentic_ni.agents.analyzer.get_llm")
def test_run_analyze_uses_function_calling(mock_get_llm):
    mock_llm = _make_llm_mock_analysis(_make_analysis_result())
    mock_get_llm.return_value = mock_llm
    run_analyze(_base_state())
    mock_llm.with_structured_output.assert_called_once_with(
        AnalysisResult, method="function_calling"
    )


# ---------------------------------------------------------------------------
# run_improve テスト
# ---------------------------------------------------------------------------


@patch("agentic_ni.agents.analyzer.get_llm")
def test_run_improve_returns_device_configs(mock_get_llm):
    mock_get_llm.return_value = _make_llm_mock_improvement(_make_improvement_output())
    result = run_improve(_base_state(analyze_request="Loopback を追加したい"))
    assert "device_configs" in result
    assert "R1" in result["device_configs"]
    assert "Loopback0" in result["device_configs"]["R1"]


@patch("agentic_ni.agents.analyzer.get_llm")
def test_run_improve_returns_analysis_result(mock_get_llm):
    mock_get_llm.return_value = _make_llm_mock_improvement(_make_improvement_output())
    result = run_improve(_base_state(analyze_request="Loopback を追加"))
    assert "analysis_result" in result
    assert "改善計画" in result["analysis_result"]


@patch("agentic_ni.agents.analyzer.get_llm")
def test_run_improve_analysis_result_contains_changes(mock_get_llm):
    mock_get_llm.return_value = _make_llm_mock_improvement(_make_improvement_output())
    result = run_improve(_base_state(analyze_request="Loopback を追加"))
    assert "Loopback0" in result["analysis_result"] or "3 件" in result["analysis_result"]


@patch("agentic_ni.agents.analyzer.get_llm")
def test_run_improve_empty_changes(mock_get_llm):
    no_change = ImprovementOutput(
        device_configs={"R1": _SAMPLE_RUNNING_CONFIG},
        changes_summary=[],
        rationale="変更不要。",
    )
    mock_get_llm.return_value = _make_llm_mock_improvement(no_change)
    result = run_improve(_base_state())
    assert result["device_configs"] == {"R1": _SAMPLE_RUNNING_CONFIG}
    assert "0 件" in result["analysis_result"] or "変更なし" in result["analysis_result"]


@patch("agentic_ni.agents.analyzer.get_llm")
def test_run_improve_uses_function_calling(mock_get_llm):
    mock_llm = _make_llm_mock_improvement(_make_improvement_output())
    mock_get_llm.return_value = mock_llm
    run_improve(_base_state(analyze_request="改善"))
    mock_llm.with_structured_output.assert_called_once_with(
        ImprovementOutput, method="function_calling"
    )
