"""fault_simulator エージェントのユニットテスト。LLM/CML/pyATS はすべてモック。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch, call

import pytest

from agentic_ni.agents.fault_simulator import (
    FaultPlan,
    FaultScenario,
    _build_fault_plan_messages,
    run,
)
from agentic_ni.state import AgentState, FaultScenarioResult, TestResult


# ---------------------------------------------------------------------------
# テスト用ヘルパー
# ---------------------------------------------------------------------------

_SAMPLE_TOPOLOGY = "lab:\n  title: test\n"
_SAMPLE_CONFIGS = {"R1": "hostname R1\n", "R2": "hostname R2\n"}
_SAMPLE_LINKS = [
    {"id": "link-01", "node_a": "R1", "node_b": "R2", "interface_a": "GigabitEthernet0/0", "interface_b": "GigabitEthernet0/0"},
    {"id": "link-02", "node_a": "R2", "node_b": "R3", "interface_a": "GigabitEthernet0/1", "interface_b": "GigabitEthernet0/0"},
]
_SAMPLE_TESTBED = "testbed:\n  name: lab\n"
_SAMPLE_TEST_PLAN_ITEMS = [
    {
        "test_type": "ospf_neighbors",
        "device": "R1",
        "target": None,
        "description": "R1 OSPFネイバー確認",
    },
    {
        "test_type": "ping",
        "device": "R1",
        "target": "10.0.0.2",
        "description": "R1 から R2 への ping",
    },
]


def _base_state(**overrides) -> AgentState:
    base: AgentState = {
        "requirement": "R1-R2-R3 を OSPF で冗長接続する",
        "prompt_set": "demo",
        "error_history": [],
        "topology_yaml": _SAMPLE_TOPOLOGY,
        "device_configs": _SAMPLE_CONFIGS,
        "lab_id": "lab-abc",
        "test_results": [],
        "test_plan_items": _SAMPLE_TEST_PLAN_ITEMS,
        "error_log": "",
        "retry_count": 1,
        "fault_simulation_enabled": True,
        "fault_scenario_results": [],
        "fault_report": "",
        "final_report": "# Phase A レポート\n",
    }
    base.update(overrides)
    return base


def _make_fault_plan(scenarios: list[FaultScenario] | None = None) -> FaultPlan:
    return FaultPlan(
        scenarios=scenarios
        or [
            FaultScenario(
                link_id="link-01",
                link_label="R1 <-> R2",
                scenario_name="上位リンク断テスト",
                wait_seconds=1,
            )
        ],
        rationale="冗長性確認のため",
    )


def _make_llm_mock(plan: FaultPlan) -> MagicMock:
    mock_structured = MagicMock()
    mock_structured.invoke.return_value = plan

    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured
    return mock_llm


def _pass_result(desc: str = "OSPFネイバー確認") -> TestResult:
    return TestResult(test=desc, result="PASS", detail="neighbors_up=1")


def _fail_result(desc: str = "OSPFネイバー確認") -> TestResult:
    return TestResult(test=desc, result="FAIL", detail="neighbors_up=0")


# ---------------------------------------------------------------------------
# Pydantic スキーマのテスト
# ---------------------------------------------------------------------------


class TestSchemas:
    def test_fault_scenario_valid(self):
        s = FaultScenario(
            link_id="link-01",
            link_label="R1 <-> R2",
            scenario_name="リンク断テスト",
        )
        assert s.link_id == "link-01"
        assert s.wait_seconds == 15  # デフォルト値

    def test_fault_scenario_custom_wait(self):
        s = FaultScenario(
            link_id="link-01",
            link_label="R1 <-> R2",
            scenario_name="テスト",
            wait_seconds=10,
        )
        assert s.wait_seconds == 10

    def test_fault_plan_valid(self):
        plan = _make_fault_plan()
        assert len(plan.scenarios) == 1
        assert plan.rationale == "冗長性確認のため"


# ---------------------------------------------------------------------------
# _build_fault_plan_messages のテスト
# ---------------------------------------------------------------------------


class TestBuildFaultPlanMessages:
    def test_contains_requirement(self):
        state = _base_state()
        msgs = _build_fault_plan_messages(state, _SAMPLE_LINKS)
        user_content = msgs[1]["content"]
        assert "R1-R2-R3 を OSPF で冗長接続する" in user_content

    def test_contains_link_ids(self):
        state = _base_state()
        msgs = _build_fault_plan_messages(state, _SAMPLE_LINKS)
        user_content = msgs[1]["content"]
        assert "link-01" in user_content
        assert "link-02" in user_content

    def test_contains_node_labels(self):
        state = _base_state()
        msgs = _build_fault_plan_messages(state, _SAMPLE_LINKS)
        user_content = msgs[1]["content"]
        assert "R1 <-> R2" in user_content

    def test_empty_links(self):
        state = _base_state()
        msgs = _build_fault_plan_messages(state, [])
        assert "(リンクなし)" in msgs[1]["content"]

    def test_system_prompt_role(self):
        state = _base_state()
        msgs = _build_fault_plan_messages(state, _SAMPLE_LINKS)
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"


# ---------------------------------------------------------------------------
# run() のテスト
# ---------------------------------------------------------------------------


class TestRun:
    def test_skip_when_no_lab_id(self):
        """lab_id が未設定の場合はスキップして空リストを返す。"""
        state = _base_state(lab_id="")
        result = run(state)
        assert result == {"fault_scenario_results": []}

    def test_skip_when_no_test_plan_items(self):
        """test_plan_items が空の場合はスキップして空リストを返す。"""
        state = _base_state(test_plan_items=[])
        result = run(state)
        assert result == {"fault_scenario_results": []}

    def test_skip_when_no_links(self):
        """CML リンクが空の場合はスキップして空リストを返す。"""
        state = _base_state()
        with (
            patch("agentic_ni.agents.fault_simulator.get_llm"),
            patch(
                "agentic_ni.tools.cml_tools.get_lab_links",
                return_value=[],
            ) as mock_links,
            patch("agentic_ni.tools.pyats_tools.build_testbed"),
        ):
            result = run(state)

        assert result == {"fault_scenario_results": []}

    def test_scenario_passed_when_recovery_all_pass(self):
        """復旧後テストが全 PASS なら scenario passed=True。"""
        state = _base_state()
        plan = _make_fault_plan()

        pass_results = [_pass_result(item["description"]) for item in _SAMPLE_TEST_PLAN_ITEMS]

        with (
            patch(
                "agentic_ni.agents.fault_simulator.get_llm",
                return_value=_make_llm_mock(plan),
            ),
            patch(
                "agentic_ni.tools.cml_tools.get_lab_links",
                return_value=_SAMPLE_LINKS,
            ),
            patch(
                "agentic_ni.tools.pyats_tools.build_testbed",
                return_value=_SAMPLE_TESTBED,
            ),
            patch(
                "agentic_ni.tools.cml_tools.set_link_state",
            ),
            patch(
                "agentic_ni.agents.fault_simulator._run_test_items",
                return_value=pass_results,
            ),
            patch("time.sleep"),
        ):
            result = run(state)

        assert len(result["fault_scenario_results"]) == 1
        scenario = result["fault_scenario_results"][0]
        assert scenario["passed"] is True
        assert scenario["link_id"] == "link-01"
        assert scenario["link_label"] == "R1 <-> R2"
        assert scenario["scenario_name"] == "上位リンク断テスト"

    def test_scenario_failed_when_recovery_has_fail(self):
        """復旧後テストに FAIL があれば scenario passed=False。"""
        state = _base_state()
        plan = _make_fault_plan()

        fail_results = [_fail_result(item["description"]) for item in _SAMPLE_TEST_PLAN_ITEMS]

        with (
            patch(
                "agentic_ni.agents.fault_simulator.get_llm",
                return_value=_make_llm_mock(plan),
            ),
            patch(
                "agentic_ni.tools.cml_tools.get_lab_links",
                return_value=_SAMPLE_LINKS,
            ),
            patch(
                "agentic_ni.tools.pyats_tools.build_testbed",
                return_value=_SAMPLE_TESTBED,
            ),
            patch("agentic_ni.tools.cml_tools.set_link_state"),
            patch(
                "agentic_ni.agents.fault_simulator._run_test_items",
                side_effect=[fail_results, fail_results],
            ),
            patch("time.sleep"),
        ):
            result = run(state)

        assert result["fault_scenario_results"][0]["passed"] is False

    def test_cml_link_state_called_down_then_up(self):
        """CML リンクが DOWN → UP の順に set_link_state が呼ばれること。"""
        state = _base_state()
        plan = _make_fault_plan()
        pass_results = [_pass_result()]

        with (
            patch(
                "agentic_ni.agents.fault_simulator.get_llm",
                return_value=_make_llm_mock(plan),
            ),
            patch(
                "agentic_ni.tools.cml_tools.get_lab_links",
                return_value=_SAMPLE_LINKS,
            ),
            patch(
                "agentic_ni.tools.pyats_tools.build_testbed",
                return_value=_SAMPLE_TESTBED,
            ),
            patch("agentic_ni.tools.cml_tools.set_link_state") as mock_set,
            patch(
                "agentic_ni.agents.fault_simulator._run_test_items",
                return_value=pass_results,
            ),
            patch("time.sleep"),
        ):
            run(state)

        assert mock_set.call_count == 2
        calls = mock_set.call_args_list
        assert calls[0] == call("lab-abc", "link-01", up=False)
        assert calls[1] == call("lab-abc", "link-01", up=True)

    def test_multiple_scenarios(self):
        """複数シナリオを正常に処理できること。"""
        state = _base_state()
        plan = FaultPlan(
            scenarios=[
                FaultScenario(
                    link_id="link-01",
                    link_label="R1 <-> R2",
                    scenario_name="シナリオ1",
                    wait_seconds=1,
                ),
                FaultScenario(
                    link_id="link-02",
                    link_label="R2 <-> R3",
                    scenario_name="シナリオ2",
                    wait_seconds=1,
                ),
            ],
            rationale="全リンクを検証",
        )
        pass_results = [_pass_result()]

        with (
            patch(
                "agentic_ni.agents.fault_simulator.get_llm",
                return_value=_make_llm_mock(plan),
            ),
            patch(
                "agentic_ni.tools.cml_tools.get_lab_links",
                return_value=_SAMPLE_LINKS,
            ),
            patch(
                "agentic_ni.tools.pyats_tools.build_testbed",
                return_value=_SAMPLE_TESTBED,
            ),
            patch(
                "agentic_ni.tools.cml_tools.set_link_state",
            ),
            patch(
                "agentic_ni.agents.fault_simulator._run_test_items",
                return_value=pass_results,
            ),
            patch("time.sleep"),
        ):
            result = run(state)

        assert len(result["fault_scenario_results"]) == 2

    def test_skip_scenario_when_link_not_found(self):
        """link_id が links に存在しない場合はスキップされる。"""
        state = _base_state()
        plan = _make_fault_plan(
            [
                FaultScenario(
                    link_id="nonexistent-link",
                    link_label="XX <-> YY",
                    scenario_name="存在しないリンクのシナリオ",
                    wait_seconds=1,
                )
            ]
        )

        with (
            patch(
                "agentic_ni.agents.fault_simulator.get_llm",
                return_value=_make_llm_mock(plan),
            ),
            patch(
                "agentic_ni.tools.cml_tools.get_lab_links",
                return_value=_SAMPLE_LINKS,  # nonexistent-link は含まれない
            ),
            patch(
                "agentic_ni.tools.pyats_tools.build_testbed",
                return_value=_SAMPLE_TESTBED,
            ),
            patch(
                "agentic_ni.tools.cml_tools.set_link_state",
                side_effect=KeyError("nonexistent-link"),
            ) as mock_set,
            patch("time.sleep"),
        ):
            result = run(state)

        # KeyError でスキップされるため空リスト、set_link_state は 1 回呼ばれた後スキップ
        assert result["fault_scenario_results"] == []
        mock_set.assert_called_once_with("lab-abc", "nonexistent-link", up=False)

    def test_get_links_failure_returns_empty(self):
        """get_lab_links が例外を投げた場合は空リストを返す。"""
        state = _base_state()

        with patch(
            "agentic_ni.tools.cml_tools.get_lab_links",
            side_effect=RuntimeError("接続失敗"),
        ):
            result = run(state)

        assert result == {"fault_scenario_results": []}


# ---------------------------------------------------------------------------
# graph.py の Phase B ノードのテスト
# ---------------------------------------------------------------------------


class TestGraphNodes:
    def test_fault_report_node_no_scenarios(self):
        """fault_scenario_results が空の場合はスキップメッセージを出力。"""
        from agentic_ni.graph import fault_report_node

        state = _base_state(fault_scenario_results=[], final_report="# Phase A\n")
        result = fault_report_node(state)

        assert "fault_report" in result
        assert "final_report" in result
        assert "障害シミュレーション結果" in result["fault_report"]
        assert "# Phase A\n" in result["final_report"]

    def test_fault_report_node_with_passed_scenarios(self):
        """全シナリオ PASS の場合、最終判定が成功メッセージになること。"""
        from agentic_ni.graph import fault_report_node

        scenario = FaultScenarioResult(
            scenario_name="R1-R2 リンク断",
            link_id="link-01",
            link_label="R1 <-> R2",
            tests_during_fault=[_fail_result()],
            tests_after_recovery=[_pass_result()],
            passed=True,
        )
        state = _base_state(
            fault_scenario_results=[scenario],
            final_report="# Phase A\n",
        )
        result = fault_report_node(state)

        assert "全シナリオで復旧を確認" in result["fault_report"]
        assert "PASS（復旧確認）: 1 件" in result["fault_report"]

    def test_fault_report_node_with_failed_scenario(self):
        """FAIL シナリオがある場合、警告メッセージになること。"""
        from agentic_ni.graph import fault_report_node

        scenario = FaultScenarioResult(
            scenario_name="R2-R3 リンク断",
            link_id="link-02",
            link_label="R2 <-> R3",
            tests_during_fault=[_fail_result()],
            tests_after_recovery=[_fail_result()],
            passed=False,
        )
        state = _base_state(
            fault_scenario_results=[scenario],
            final_report="# Phase A\n",
        )
        result = fault_report_node(state)

        assert "FAIL（復旧未確認）: 1 件" in result["fault_report"]
        assert "シナリオで復旧未確認" in result["fault_report"]

    def test_fault_report_no_phase_b_string(self):
        """fault_report に 'Phase B' の文字列が含まれないこと。"""
        from agentic_ni.graph import fault_report_node

        state = _base_state(fault_scenario_results=[], final_report="")
        result = fault_report_node(state)
        assert "Phase B" not in result["fault_report"]
        assert "Phase B" not in result["final_report"]

    def test_ospf_exact_count_check_pass(self):
        """expected_ospf_neighbors が一致する場合は PASS。"""
        from agentic_ni.agents.fault_simulator import _check_ospf_exact_count, _run_test_items
        from agentic_ni.agents.validator import TestItem

        item = TestItem(
            test_type="ospf_neighbors",
            device="R1",
            target=None,
            description="R1 OSPFネイバー確認",
        )
        with patch(
            "agentic_ni.tools.pyats_tools.check_ospf_neighbors",
            return_value={"neighbors_up": 1, "neighbors": []},
        ):
            result = _check_ospf_exact_count(item, _SAMPLE_TESTBED, expected_count=1)

        assert result["result"] == "PASS"
        assert "expected: 1" in result["detail"]

    def test_ospf_exact_count_check_fail_when_mismatch(self):
        """expected_ospf_neighbors が一致しない場合は FAIL。"""
        from agentic_ni.agents.fault_simulator import _check_ospf_exact_count
        from agentic_ni.agents.validator import TestItem

        item = TestItem(
            test_type="ospf_neighbors",
            device="R1",
            target=None,
            description="R1 OSPFネイバー確認",
        )
        with patch(
            "agentic_ni.tools.pyats_tools.check_ospf_neighbors",
            return_value={"neighbors_up": 2, "neighbors": []},
        ):
            # 障害中に 2 ネイバーだが期待値 1 → FAIL
            result = _check_ospf_exact_count(item, _SAMPLE_TESTBED, expected_count=1)

        assert result["result"] == "FAIL"
        assert "expected: 1" in result["detail"]

    def test_fault_report_appends_to_final_report(self):
        """fault_report の内容が final_report に追記されること。"""
        from agentic_ni.graph import fault_report_node

        state = _base_state(fault_scenario_results=[], final_report="# Phase A\n")
        result = fault_report_node(state)

        assert result["final_report"].startswith("# Phase A\n")
        assert "障害シミュレーション" in result["final_report"]

    def test_should_run_fault_sim_enabled(self):
        """fault_simulation_enabled=True のとき 'fault_simulate' を返すこと。"""
        from agentic_ni.graph import _should_run_fault_sim

        state = _base_state(fault_simulation_enabled=True)
        assert _should_run_fault_sim(state) == "fault_simulate"

    def test_should_run_fault_sim_disabled(self):
        """fault_simulation_enabled=False のとき 'done' を返すこと。"""
        from agentic_ni.graph import _should_run_fault_sim

        state = _base_state(fault_simulation_enabled=False)
        assert _should_run_fault_sim(state) == "done"

    def test_should_run_fault_sim_default(self):
        """fault_simulation_enabled が未設定（デフォルト）のとき 'done' を返すこと。"""
        from agentic_ni.graph import _should_run_fault_sim

        state = _base_state()
        del state["fault_simulation_enabled"]
        assert _should_run_fault_sim(state) == "done"


# ---------------------------------------------------------------------------
# initial_state のテスト
# ---------------------------------------------------------------------------


class TestInitialState:
    def test_fault_simulation_enabled_default_false(self):
        from agentic_ni.graph import initial_state

        state = initial_state("テスト要件")
        assert state["fault_simulation_enabled"] is False

    def test_fault_simulation_enabled_true(self):
        from agentic_ni.graph import initial_state

        state = initial_state("テスト要件", fault_simulation_enabled=True)
        assert state["fault_simulation_enabled"] is True

    def test_test_plan_items_initial_empty(self):
        from agentic_ni.graph import initial_state

        state = initial_state("テスト要件")
        assert state["test_plan_items"] == []

    def test_fault_scenario_results_initial_empty(self):
        from agentic_ni.graph import initial_state

        state = initial_state("テスト要件")
        assert state["fault_scenario_results"] == []
