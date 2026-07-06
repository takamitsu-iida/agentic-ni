"""troubleshooter エージェントのユニットテスト。LLM/CML/pyATS はすべてモック。"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from agentic_ni.agents.troubleshooter import (
    DiagnosisResult,
    FixCommand,
    FixPlan,
    _build_diagnosis_messages,
    _build_fix_plan_messages,
    _format_collected_state,
    _format_fix_history,
    run_collect,
    run_diagnose,
    run_fix,
)
from agentic_ni.state import AgentState, TroubleshootFixRecord


# ---------------------------------------------------------------------------
# テスト用ヘルパー
# ---------------------------------------------------------------------------

_SAMPLE_LAB_ID = "lab-ts-001"
_SAMPLE_TESTBED = "testbed:\n  name: ts-lab\n"
_SAMPLE_RUNNING_CONFIG = "hostname R1\ninterface GigabitEthernet0/0\n ip address 10.0.12.1 255.255.255.252\n"
_SAMPLE_COLLECTED_STATE = {
    "R1": {
        "running_config": _SAMPLE_RUNNING_CONFIG,
        "show_outputs": {
            "show ip ospf neighbor": "Neighbor ID: (none)",
            "show ip route": "Gateway of last resort is not set",
            "show ip interface brief": "GigabitEthernet0/0  10.0.12.1   YES   up    up",
        },
    },
    "R2": {
        "running_config": "hostname R2\n",
        "show_outputs": {},
    },
}


def _base_state(**overrides) -> AgentState:
    base: AgentState = {
        "requirement": "R1 と R2 を OSPF エリア 0 で接続する",
        "prompt_set": "demo",
        "use_rag": False,
        "fault_simulation_enabled": False,
        "error_history": [],
        "topology_yaml": "",
        "device_configs": {"R1": _SAMPLE_RUNNING_CONFIG, "R2": "hostname R2\n"},
        "lab_id": _SAMPLE_LAB_ID,
        "test_results": [],
        "test_plan_items": [],
        "error_log": "",
        "retry_count": 0,
        "fault_scenario_results": [],
        "fault_report": "",
        "troubleshoot_lab_id": _SAMPLE_LAB_ID,
        "troubleshoot_issue": "OSPF ネイバーが確立しない",
        "collected_state": _SAMPLE_COLLECTED_STATE,
        "diagnosis": "",
        "fix_records": [],
        "troubleshoot_retry_count": 0,
        "troubleshoot_report": "",
        "final_report": "",
    }
    base.update(overrides)
    return base


def _make_diagnosis() -> DiagnosisResult:
    return DiagnosisResult(
        root_cause="R1 の router ospf 1 に network 文が設定されていない。",
        affected_devices=["R1"],
        severity="config_error",
        summary="network ステートメントを追加することで OSPF ネイバーが確立する。",
    )


def _make_fix_plan() -> FixPlan:
    return FixPlan(
        fixes=[
            FixCommand(
                device="R1",
                commands="router ospf 1\n network 0.0.0.0 255.255.255.255 area 0",
                rollback_commands="router ospf 1\n no network 0.0.0.0 255.255.255.255 area 0",
                description="R1 に OSPF network 文を追加する",
            )
        ],
        rationale="OSPF network 文がないため OSPF が起動していない。",
    )


def _make_llm_mock_diagnosis(diagnosis: DiagnosisResult) -> MagicMock:
    mock_structured = MagicMock()
    mock_structured.invoke.return_value = diagnosis
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured
    return mock_llm


def _make_llm_mock_fix(fix_plan: FixPlan) -> MagicMock:
    mock_structured = MagicMock()
    mock_structured.invoke.return_value = fix_plan
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured
    return mock_llm


# ---------------------------------------------------------------------------
# Pydantic スキーマのテスト
# ---------------------------------------------------------------------------


class TestSchemas:
    def test_diagnosis_result_valid(self):
        d = _make_diagnosis()
        assert d.severity == "config_error"
        assert "R1" in d.affected_devices

    def test_fix_command_default_rollback(self):
        fc = FixCommand(
            device="R1",
            commands="router ospf 1\n network 0.0.0.0 255.255.255.255 area 0",
            description="テスト",
        )
        assert fc.rollback_commands == ""

    def test_fix_plan_valid(self):
        plan = _make_fix_plan()
        assert len(plan.fixes) == 1
        assert plan.fixes[0].device == "R1"


# ---------------------------------------------------------------------------
# _format_collected_state のテスト
# ---------------------------------------------------------------------------


class TestFormatCollectedState:
    def test_includes_device_name(self):
        text = _format_collected_state(_SAMPLE_COLLECTED_STATE)
        assert "R1" in text
        assert "R2" in text

    def test_includes_running_config(self):
        text = _format_collected_state(_SAMPLE_COLLECTED_STATE)
        assert "hostname R1" in text

    def test_includes_show_output(self):
        text = _format_collected_state(_SAMPLE_COLLECTED_STATE)
        assert "show ip ospf neighbor" in text

    def test_handles_empty_state(self):
        text = _format_collected_state({})
        assert "(状態なし)" in text

    def test_handles_error_device(self):
        state = {"R1": {"error": "接続タイムアウト"}}
        text = _format_collected_state(state)
        assert "接続タイムアウト" in text

    def test_truncates_long_config(self):
        long_cfg = "X" * 5000
        state = {"R1": {"running_config": long_cfg, "show_outputs": {}}}
        text = _format_collected_state(state)
        assert "省略" in text


# ---------------------------------------------------------------------------
# _format_fix_history のテスト
# ---------------------------------------------------------------------------


class TestFormatFixHistory:
    def test_empty_returns_none_text(self):
        text = _format_fix_history([])
        assert "(なし)" in text

    def test_includes_device_and_status(self):
        records = [
            TroubleshootFixRecord(
                device="R1",
                commands="router ospf 1\n network 0.0.0.0 255.255.255.255 area 0",
                rollback_commands="",
                success=True,
                error="",
                description="OSPF 追加",
            )
        ]
        text = _format_fix_history(records)
        assert "R1" in text
        assert "✅ 成功" in text

    def test_includes_failure_error(self):
        records = [
            TroubleshootFixRecord(
                device="R2",
                commands="bad command",
                rollback_commands="",
                success=False,
                error="Invalid input",
                description="テスト",
            )
        ]
        text = _format_fix_history(records)
        assert "❌ 失敗" in text
        assert "Invalid input" in text


# ---------------------------------------------------------------------------
# _build_diagnosis_messages のテスト
# ---------------------------------------------------------------------------


class TestBuildDiagnosisMessages:
    def test_includes_issue(self):
        state = _base_state()
        msgs = _build_diagnosis_messages(state, _SAMPLE_COLLECTED_STATE)
        assert "OSPF ネイバーが確立しない" in msgs[1]["content"]

    def test_includes_requirement(self):
        state = _base_state()
        msgs = _build_diagnosis_messages(state, _SAMPLE_COLLECTED_STATE)
        assert "OSPF エリア 0" in msgs[1]["content"]

    def test_includes_failed_tests(self):
        state = _base_state(
            test_results=[{"test": "ospf check", "result": "FAIL", "detail": "no neighbors"}]
        )
        msgs = _build_diagnosis_messages(state, _SAMPLE_COLLECTED_STATE)
        assert "ospf check" in msgs[1]["content"]

    def test_system_message_role(self):
        state = _base_state()
        msgs = _build_diagnosis_messages(state, _SAMPLE_COLLECTED_STATE)
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"


# ---------------------------------------------------------------------------
# _build_fix_plan_messages のテスト
# ---------------------------------------------------------------------------


class TestBuildFixPlanMessages:
    def test_includes_diagnosis(self):
        state = _base_state(diagnosis="network 文が漏れている")
        msgs = _build_fix_plan_messages(state, "network 文が漏れている", _SAMPLE_COLLECTED_STATE)
        assert "network 文が漏れている" in msgs[1]["content"]

    def test_includes_fix_history(self):
        records = [
            TroubleshootFixRecord(
                device="R1", commands="test", rollback_commands="",
                success=False, error="err", description="テスト",
            )
        ]
        state = _base_state(fix_records=records)
        msgs = _build_fix_plan_messages(state, "diagnosis", _SAMPLE_COLLECTED_STATE)
        assert "テスト" in msgs[1]["content"]


# ---------------------------------------------------------------------------
# run_collect のテスト
# ---------------------------------------------------------------------------


class TestRunCollect:
    def test_returns_empty_when_no_lab_id(self):
        state = _base_state(troubleshoot_lab_id="", lab_id="")
        result = run_collect(state)
        assert result == {"collected_state": {}, "device_configs": {}}

    def test_returns_empty_when_no_booted_nodes(self):
        state = _base_state()
        with patch(
            "agentic_ni.tools.cml_tools.get_lab_nodes",
            return_value=[{"label": "R1", "state": "DEFINED_ON_CORE"}],
        ):
            result = run_collect(state)
        assert result["collected_state"] == {}

    def test_collects_running_config_into_device_configs(self):
        state = _base_state()
        nodes = [
            {"id": "n0", "label": "R1", "state": "BOOTED"},
        ]
        with (
            patch("agentic_ni.tools.cml_tools.get_lab_nodes", return_value=nodes),
            patch(
                "agentic_ni.tools.pyats_tools.build_testbed",
                return_value=_SAMPLE_TESTBED,
            ),
            patch(
                "agentic_ni.tools.pyats_tools.collect_device_state",
                return_value={
                    "running_config": "hostname R1\n",
                    "show_outputs": {"show ip ospf neighbor": ""},
                },
            ),
        ):
            result = run_collect(state)

        assert "R1" in result["collected_state"]
        assert result["device_configs"]["R1"] == "hostname R1\n"
        assert result["lab_id"] == _SAMPLE_LAB_ID

    def test_handles_collect_failure_gracefully(self):
        state = _base_state()
        nodes = [{"id": "n0", "label": "R1", "state": "BOOTED"}]
        with (
            patch("agentic_ni.tools.cml_tools.get_lab_nodes", return_value=nodes),
            patch(
                "agentic_ni.tools.pyats_tools.build_testbed",
                return_value=_SAMPLE_TESTBED,
            ),
            patch(
                "agentic_ni.tools.pyats_tools.collect_device_state",
                side_effect=RuntimeError("接続失敗"),
            ),
        ):
            result = run_collect(state)

        assert "error" in result["collected_state"]["R1"]
        assert result["device_configs"]["R1"] == ""


# ---------------------------------------------------------------------------
# run_diagnose のテスト
# ---------------------------------------------------------------------------


class TestRunDiagnose:
    def test_returns_diagnosis_text(self):
        state = _base_state()
        diagnosis = _make_diagnosis()

        with patch(
            "agentic_ni.agents.troubleshooter.get_llm",
            return_value=_make_llm_mock_diagnosis(diagnosis),
        ):
            result = run_diagnose(state)

        assert "root_cause" not in result  # Pydantic フィールドではなく formatted text
        assert "根本原因" in result["diagnosis"]
        assert "network 文が設定されていない" in result["diagnosis"]

    def test_diagnosis_includes_severity(self):
        state = _base_state()
        with patch(
            "agentic_ni.agents.troubleshooter.get_llm",
            return_value=_make_llm_mock_diagnosis(_make_diagnosis()),
        ):
            result = run_diagnose(state)

        assert "config_error" in result["diagnosis"]


# ---------------------------------------------------------------------------
# run_fix のテスト
# ---------------------------------------------------------------------------


class TestRunFix:
    def test_applies_fix_and_records_success(self):
        state = _base_state(diagnosis="network 文漏れ")
        fix_plan = _make_fix_plan()

        with (
            patch(
                "agentic_ni.agents.troubleshooter.get_llm",
                return_value=_make_llm_mock_fix(fix_plan),
            ),
            patch(
                "agentic_ni.tools.pyats_tools.build_testbed",
                return_value=_SAMPLE_TESTBED,
            ),
            patch("agentic_ni.tools.pyats_tools.apply_incremental_config") as mock_apply,
        ):
            result = run_fix(state)

        assert len(result["fix_records"]) == 1
        assert result["fix_records"][0]["success"] is True
        assert result["fix_records"][0]["device"] == "R1"
        mock_apply.assert_called_once_with(
            _SAMPLE_TESTBED,
            "R1",
            "router ospf 1\n network 0.0.0.0 255.255.255.255 area 0",
        )

    def test_records_failure_when_apply_raises(self):
        state = _base_state(diagnosis="network 文漏れ")
        fix_plan = _make_fix_plan()

        with (
            patch(
                "agentic_ni.agents.troubleshooter.get_llm",
                return_value=_make_llm_mock_fix(fix_plan),
            ),
            patch(
                "agentic_ni.tools.pyats_tools.build_testbed",
                return_value=_SAMPLE_TESTBED,
            ),
            patch(
                "agentic_ni.tools.pyats_tools.apply_incremental_config",
                side_effect=RuntimeError("コンフィグ投入失敗"),
            ),
        ):
            result = run_fix(state)

        assert result["fix_records"][0]["success"] is False
        assert "コンフィグ投入失敗" in result["fix_records"][0]["error"]

    def test_increments_retry_count(self):
        state = _base_state(troubleshoot_retry_count=1, diagnosis="diag")
        fix_plan = _make_fix_plan()

        with (
            patch(
                "agentic_ni.agents.troubleshooter.get_llm",
                return_value=_make_llm_mock_fix(fix_plan),
            ),
            patch("agentic_ni.tools.pyats_tools.build_testbed", return_value=_SAMPLE_TESTBED),
            patch("agentic_ni.tools.pyats_tools.apply_incremental_config"),
        ):
            result = run_fix(state)

        assert result["troubleshoot_retry_count"] == 2

    def test_empty_fix_plan_skips_apply(self):
        state = _base_state(diagnosis="diag")
        empty_plan = FixPlan(fixes=[], rationale="修正不要")

        with (
            patch(
                "agentic_ni.agents.troubleshooter.get_llm",
                return_value=_make_llm_mock_fix(empty_plan),
            ),
            patch("agentic_ni.tools.pyats_tools.build_testbed", return_value=_SAMPLE_TESTBED),
            patch("agentic_ni.tools.pyats_tools.apply_incremental_config") as mock_apply,
        ):
            result = run_fix(state)

        mock_apply.assert_not_called()
        assert result["fix_records"] == []


# ---------------------------------------------------------------------------
# graph.py トラブルシューティングノードのテスト
# ---------------------------------------------------------------------------


class TestTroubleshootGraphNodes:
    def test_should_continue_returns_complete_on_all_pass(self):
        from agentic_ni.graph import should_continue_troubleshoot

        state = _base_state(
            test_results=[{"test": "ping", "result": "PASS", "detail": "OK"}]
        )
        assert should_continue_troubleshoot(state) == "complete"

    def test_should_continue_returns_retry_on_fail_within_limit(self):
        from agentic_ni.graph import should_continue_troubleshoot

        state = _base_state(
            test_results=[{"test": "ping", "result": "FAIL", "detail": "NG"}],
            troubleshoot_retry_count=1,
        )
        assert should_continue_troubleshoot(state) == "retry"

    def test_should_continue_returns_escalate_at_limit(self):
        from agentic_ni.graph import should_continue_troubleshoot, TROUBLESHOOT_MAX_RETRIES

        state = _base_state(
            test_results=[{"test": "ping", "result": "FAIL", "detail": "NG"}],
            troubleshoot_retry_count=TROUBLESHOOT_MAX_RETRIES,
        )
        assert should_continue_troubleshoot(state) == "escalate"

    def test_should_continue_returns_retry_when_no_tests(self):
        from agentic_ni.graph import should_continue_troubleshoot

        state = _base_state(test_results=[], troubleshoot_retry_count=0)
        assert should_continue_troubleshoot(state) == "retry"

    def test_troubleshoot_report_node_all_pass(self):
        from agentic_ni.graph import troubleshoot_report_node

        state = _base_state(
            test_results=[{"test": "ping", "result": "PASS", "detail": "OK"}],
            fix_records=[
                TroubleshootFixRecord(
                    device="R1",
                    commands="router ospf 1\n network 0.0.0.0 255.255.255.255 area 0",
                    rollback_commands="",
                    success=True,
                    error="",
                    description="OSPF 追加",
                )
            ],
        )
        result = troubleshoot_report_node(state)

        assert "final_report" in result
        assert "troubleshoot_report" in result
        assert "問題が解決されました" in result["final_report"]
        assert "✅" in result["final_report"]

    def test_troubleshoot_report_node_with_fail(self):
        from agentic_ni.graph import troubleshoot_report_node

        state = _base_state(
            test_results=[{"test": "ping", "result": "FAIL", "detail": "NG"}],
        )
        result = troubleshoot_report_node(state)

        assert "手動確認" in result["final_report"]


# ---------------------------------------------------------------------------
# initial_state_troubleshoot のテスト
# ---------------------------------------------------------------------------


class TestInitialStateTroubleshoot:
    def test_lab_id_set_correctly(self):
        from agentic_ni.graph import initial_state_troubleshoot

        state = initial_state_troubleshoot("lab-999")
        assert state["lab_id"] == "lab-999"
        assert state["troubleshoot_lab_id"] == "lab-999"

    def test_issue_set_correctly(self):
        from agentic_ni.graph import initial_state_troubleshoot

        state = initial_state_troubleshoot("lab-999", issue="OSPFが壊れている")
        assert state["troubleshoot_issue"] == "OSPFが壊れている"

    def test_fix_records_initially_empty(self):
        from agentic_ni.graph import initial_state_troubleshoot

        state = initial_state_troubleshoot("lab-999")
        assert state["fix_records"] == []

    def test_retry_count_initially_zero(self):
        from agentic_ni.graph import initial_state_troubleshoot

        state = initial_state_troubleshoot("lab-999")
        assert state["troubleshoot_retry_count"] == 0

    def test_collected_state_initially_empty(self):
        from agentic_ni.graph import initial_state_troubleshoot

        state = initial_state_troubleshoot("lab-999")
        assert state["collected_state"] == {}

    def test_requirement_fallback_to_issue(self):
        from agentic_ni.graph import initial_state_troubleshoot

        state = initial_state_troubleshoot(
            "lab-999", issue="ping が通らない", prompt_set="nonexistent_set"
        )
        # requirement.md が存在しないセットの場合は issue が requirement に使われる
        assert "ping が通らない" in state["requirement"] or "lab-999" in state["requirement"]
