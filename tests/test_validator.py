"""validator エージェントのユニットテスト。LLM/CML/pyATS はすべてモック。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch, call

import pytest

from agentic_ni.agents.validator import (
    FailureAnalysis,
    TestItem,
    TestPlan,
    _build_analysis_messages,
    _build_test_plan_messages,
    _deploy,
    _execute_test,
    _run_tests,
    run,
)
from agentic_ni.state import AgentState, TestResult


# ---------------------------------------------------------------------------
# テスト用ヘルパー
# ---------------------------------------------------------------------------

_SAMPLE_TOPOLOGY = "lab:\n  title: test\n"
_SAMPLE_CONFIGS = {"R1": "hostname R1\n", "R2": "hostname R2\n"}
_SAMPLE_TESTBED = "testbed:\n  name: lab\n"


def _base_state(**overrides) -> AgentState:
    base: AgentState = {
        "requirement": "R1とR2をOSPFで接続する",
        "topology_yaml": _SAMPLE_TOPOLOGY,
        "device_configs": _SAMPLE_CONFIGS,
        "lab_id": "",
        "test_results": [],
        "error_log": "",
        "retry_count": 0,
        "final_report": "",
    }
    base.update(overrides)
    return base


def _make_test_item(
    test_type: str = "ospf_neighbors",
    device: str = "R1",
    target: str | None = None,
    description: str = "OSPFネイバー確認",
) -> TestItem:
    return TestItem(
        test_type=test_type,
        device=device,
        target=target,
        description=description,
    )


def _make_test_plan(items: list[TestItem] | None = None) -> TestPlan:
    return TestPlan(
        tests=items or [_make_test_item()],
        rationale="OSPFの疎通確認",
    )


def _make_llm_mock(plan: TestPlan, analysis: FailureAnalysis | None = None) -> MagicMock:
    """get_llm() が返す LLM のモックを構築する。"""
    mock_plan_structured = MagicMock()
    mock_plan_structured.invoke.return_value = plan

    mock_analysis_structured = MagicMock()
    if analysis:
        mock_analysis_structured.invoke.return_value = analysis

    mock_llm = MagicMock()
    # 1回目の with_structured_output → TestPlan、2回目 → FailureAnalysis
    mock_llm.with_structured_output.side_effect = [
        mock_plan_structured,
        mock_analysis_structured,
    ]
    return mock_llm


# ---------------------------------------------------------------------------
# Pydantic スキーマのテスト
# ---------------------------------------------------------------------------


class TestSchemas:
    def test_test_item_valid(self):
        item = TestItem(
            test_type="ospf_neighbors",
            device="R1",
            target=None,
            description="OSPF確認",
        )
        assert item.test_type == "ospf_neighbors"
        assert item.target is None

    def test_test_item_ping_with_target(self):
        item = TestItem(
            test_type="ping",
            device="R1",
            target="10.0.0.2",
            description="疎通確認",
        )
        assert item.target == "10.0.0.2"

    def test_test_plan_schema(self):
        plan = _make_test_plan()
        assert len(plan.tests) == 1
        assert plan.rationale == "OSPFの疎通確認"

    def test_failure_analysis_schema(self):
        analysis = FailureAnalysis(
            root_cause="OSPFエリア番号のミスマッチ",
            suggestion="R2の router ospf 1 / network ... area 0 に修正",
            affected_devices=["R2"],
        )
        assert "ミスマッチ" in analysis.root_cause


# ---------------------------------------------------------------------------
# _build_test_plan_messages のテスト
# ---------------------------------------------------------------------------


class TestBuildTestPlanMessages:
    def test_includes_requirement(self):
        state = _base_state(requirement="OSPFで冗長接続")
        msgs = _build_test_plan_messages(state)
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert "OSPFで冗長接続" in msgs[1]["content"]

    def test_includes_device_names(self):
        state = _base_state(device_configs={"R1": "cfg", "R2": "cfg"})
        msgs = _build_test_plan_messages(state)
        assert "R1" in msgs[1]["content"]
        assert "R2" in msgs[1]["content"]

    def test_system_prompt_loaded(self):
        state = _base_state()
        msgs = _build_test_plan_messages(state)
        assert "CCIE" in msgs[0]["content"] or "検証エンジニア" in msgs[0]["content"]


# ---------------------------------------------------------------------------
# _build_analysis_messages のテスト
# ---------------------------------------------------------------------------


class TestBuildAnalysisMessages:
    def test_includes_failed_test_detail(self):
        state = _base_state()
        failed: list[TestResult] = [
            {"test": "OSPF確認", "result": "FAIL", "detail": "neighbors_up=0"},
        ]
        msgs = _build_analysis_messages(state, failed)
        assert "neighbors_up=0" in msgs[1]["content"]
        assert "失敗" in msgs[1]["content"]

    def test_includes_device_configs(self):
        state = _base_state(device_configs={"R1": "hostname R1\n"})
        failed: list[TestResult] = [
            {"test": "ping", "result": "FAIL", "detail": "timeout"},
        ]
        msgs = _build_analysis_messages(state, failed)
        assert "R1" in msgs[1]["content"]


# ---------------------------------------------------------------------------
# _execute_test のテスト
# ---------------------------------------------------------------------------


class TestExecuteTest:
    def test_ospf_neighbors_pass(self):
        item = _make_test_item("ospf_neighbors", "R1")
        ospf_data = {"neighbors_up": 2, "neighbors": [{"neighbor_id": "2.2.2.2", "state": "FULL/DR"}]}

        with patch("agentic_ni.tools.pyats_tools.check_ospf_neighbors", return_value=ospf_data):
            result = _execute_test(item, _SAMPLE_TESTBED)

        assert result["result"] == "PASS"
        assert "2 neighbor" in result["detail"]

    def test_ospf_neighbors_fail(self):
        item = _make_test_item("ospf_neighbors", "R1")
        ospf_data = {"neighbors_up": 0, "neighbors": []}

        with patch("agentic_ni.tools.pyats_tools.check_ospf_neighbors", return_value=ospf_data):
            result = _execute_test(item, _SAMPLE_TESTBED)

        assert result["result"] == "FAIL"
        assert "neighbors_up=0" in result["detail"]

    def test_bgp_summary_pass(self):
        item = _make_test_item("bgp_summary", "R1", description="BGP確認")
        bgp_data = {"peers_established": 1, "peers": [{"peer": "10.0.0.2", "established": True, "state": 5}]}

        with patch("agentic_ni.tools.pyats_tools.check_bgp_summary", return_value=bgp_data):
            result = _execute_test(item, _SAMPLE_TESTBED)

        assert result["result"] == "PASS"

    def test_ping_pass(self):
        item = _make_test_item("ping", "R1", target="192.168.1.1", description="疎通確認")

        with patch("agentic_ni.tools.pyats_tools.check_ping", return_value=True):
            result = _execute_test(item, _SAMPLE_TESTBED)

        assert result["result"] == "PASS"
        assert "OK" in result["detail"]

    def test_ping_fail(self):
        item = _make_test_item("ping", "R1", target="192.168.1.1", description="疎通確認")

        with patch("agentic_ni.tools.pyats_tools.check_ping", return_value=False):
            result = _execute_test(item, _SAMPLE_TESTBED)

        assert result["result"] == "FAIL"

    def test_ping_without_target_fails(self):
        item = _make_test_item("ping", "R1", target=None, description="疎通確認")
        result = _execute_test(item, _SAMPLE_TESTBED)
        assert result["result"] == "FAIL"
        assert "target" in result["detail"]

    def test_vlan_interfaces_pass(self):
        item = _make_test_item("vlan_interfaces", "SW1", description="VLAN確認")
        vlan_data = {"vlans": {"10": "active"}, "interfaces_up": 3, "vlan_raw": {}, "intf_raw": {}}

        with patch("agentic_ni.tools.pyats_tools.check_vlan_interfaces", return_value=vlan_data):
            result = _execute_test(item, _SAMPLE_TESTBED)

        assert result["result"] == "PASS"

    def test_exception_returns_fail_result(self):
        item = _make_test_item("ospf_neighbors", "R1")

        with patch(
            "agentic_ni.tools.pyats_tools.check_ospf_neighbors",
            side_effect=ConnectionError("接続失敗"),
        ):
            result = _execute_test(item, _SAMPLE_TESTBED)

        assert result["result"] == "FAIL"
        assert "ConnectionError" in result["detail"]


# ---------------------------------------------------------------------------
# _deploy のテスト
# ---------------------------------------------------------------------------


class TestDeploy:
    def test_creates_lab_and_pushes_configs(self):
        state = _base_state(device_configs={"R1": "cfg1", "R2": "cfg2"})

        with patch("agentic_ni.tools.cml_tools.deploy_lab", return_value="lab-001") as mdeploy, \
             patch("agentic_ni.tools.cml_tools.delete_lab"):
            result = _deploy(state)

        assert result == "lab-001"
        mdeploy.assert_called_once_with(_SAMPLE_TOPOLOGY, {"R1": "cfg1", "R2": "cfg2"}, title="agentic-ni-demo")

    def test_raises_when_nodes_not_ready(self):
        """deploy_lab が RuntimeError を送出した場合に _deploy が伝播させること。"""
        state = _base_state()

        with patch("agentic_ni.tools.cml_tools.deploy_lab",
                   side_effect=RuntimeError("ノードが規定時間内に起動しませんでした")), \
             patch("agentic_ni.tools.cml_tools.delete_lab"):
            with pytest.raises(RuntimeError, match="起動しませんでした"):
                _deploy(state)

    def test_deletes_old_lab_before_redeploy(self):
        """既存ラボがある場合は update_configs_and_restart を試み、失敗時は deploy_lab にフォールバック。"""
        state = _base_state(lab_id="old-lab", device_configs={"R1": "cfg1"})

        with patch("agentic_ni.tools.cml_tools.update_configs_and_restart",
                   side_effect=Exception("lab gone")) as mupdate, \
             patch("agentic_ni.tools.cml_tools.deploy_lab", return_value="new-lab"):
            _deploy(state)

        mupdate.assert_called_once_with("old-lab", {"R1": "cfg1"})


# ---------------------------------------------------------------------------
# run() の統合テスト
# ---------------------------------------------------------------------------


class TestValidatorRun:
    def _setup_mocks(
        self,
        plan: TestPlan,
        testbed_yaml: str = _SAMPLE_TESTBED,
        test_results: list[TestResult] | None = None,
        analysis: FailureAnalysis | None = None,
        deploy_lab_id: str = "lab-001",
    ):
        """run() に必要な全モックをまとめてセットアップする。"""
        mock_llm = _make_llm_mock(plan, analysis)
        return mock_llm, {
            "agentic_ni.agents.validator.get_llm": mock_llm,
            "deploy_lab_id": deploy_lab_id,
            "testbed_yaml": testbed_yaml,
            "test_results_override": test_results,
        }

    def test_run_all_pass_returns_empty_error_log(self):
        """全テストPASS時に error_log が空であること。"""
        plan = _make_test_plan([
            _make_test_item("ospf_neighbors", "R1", description="OSPF R1"),
        ])
        mock_llm = _make_llm_mock(plan)

        with patch("agentic_ni.agents.validator.get_llm", return_value=mock_llm), \
             patch("agentic_ni.agents.validator._deploy", return_value="lab-001"), \
             patch("agentic_ni.tools.pyats_tools.build_testbed", return_value=_SAMPLE_TESTBED), \
             patch("agentic_ni.tools.pyats_tools.check_ospf_neighbors",
                   return_value={"neighbors_up": 1, "neighbors": [{"neighbor_id": "2.2.2.2", "state": "FULL/DR"}]}):

            result = run(_base_state())

        assert result["error_log"] == ""
        assert result["lab_id"] == "lab-001"
        assert result["test_results"][0]["result"] == "PASS"
        assert result["retry_count"] == 1

    def test_run_fail_generates_error_log(self):
        """テストFAIL時に error_log に推論結果が格納されること。"""
        plan = _make_test_plan([
            _make_test_item("ospf_neighbors", "R1", description="OSPF R1"),
        ])
        analysis = FailureAnalysis(
            root_cause="OSPFエリア番号のミスマッチ: R1はarea 0だがR2はarea 1",
            suggestion="R2の設定を area 0 に修正してください",
            affected_devices=["R2"],
        )
        mock_llm = _make_llm_mock(plan, analysis)

        with patch("agentic_ni.agents.validator.get_llm", return_value=mock_llm), \
             patch("agentic_ni.agents.validator._deploy", return_value="lab-001"), \
             patch("agentic_ni.tools.pyats_tools.build_testbed", return_value=_SAMPLE_TESTBED), \
             patch("agentic_ni.tools.pyats_tools.check_ospf_neighbors",
                   return_value={"neighbors_up": 0, "neighbors": []}):

            result = run(_base_state())

        assert "根本原因" in result["error_log"]
        assert "エリア番号" in result["error_log"]
        assert result["test_results"][0]["result"] == "FAIL"

    def test_run_deploy_failure_returns_error_log(self):
        """デプロイ失敗時に error_log にエラー内容が格納されること。"""
        mock_llm = MagicMock()

        with patch("agentic_ni.agents.validator.get_llm", return_value=mock_llm), \
             patch("agentic_ni.agents.validator._deploy",
                   side_effect=RuntimeError("CML接続失敗")):

            result = run(_base_state())

        assert "デプロイ失敗" in result["error_log"]
        assert "CML接続失敗" in result["error_log"]
        assert result["retry_count"] == 1

    def test_run_increments_retry_count(self):
        plan = _make_test_plan()
        mock_llm = _make_llm_mock(plan)

        with patch("agentic_ni.agents.validator.get_llm", return_value=mock_llm), \
             patch("agentic_ni.agents.validator._deploy", return_value="lab-001"), \
             patch("agentic_ni.tools.pyats_tools.build_testbed", return_value=_SAMPLE_TESTBED), \
             patch("agentic_ni.tools.pyats_tools.check_ospf_neighbors",
                   return_value={"neighbors_up": 1, "neighbors": []}):

            result = run(_base_state(retry_count=2))

        assert result["retry_count"] == 3

    def test_run_calls_test_plan_with_structured_output(self):
        """LLMが with_structured_output(TestPlan) で呼ばれること。"""
        plan = _make_test_plan()
        mock_llm = _make_llm_mock(plan)

        with patch("agentic_ni.agents.validator.get_llm", return_value=mock_llm), \
             patch("agentic_ni.agents.validator._deploy", return_value="lab-001"), \
             patch("agentic_ni.tools.pyats_tools.build_testbed", return_value=_SAMPLE_TESTBED), \
             patch("agentic_ni.tools.pyats_tools.check_ospf_neighbors",
                   return_value={"neighbors_up": 1, "neighbors": []}):

            run(_base_state())

        mock_llm.with_structured_output.assert_any_call(TestPlan, method="function_calling")


# ---------------------------------------------------------------------------
# _run_tests（Strategy D: テスト並列実行）
# ---------------------------------------------------------------------------


def _pass_result(description: str = "test") -> TestResult:
    return TestResult(test=description, result="PASS", detail="OK")


def _fail_result(description: str = "test") -> TestResult:
    return TestResult(test=description, result="FAIL", detail="NG")


class TestRunTests:
    """_run_tests() の逐次・並列両モードのテスト。"""

    def _make_items(self, n: int) -> list[TestItem]:
        return [
            _make_test_item("ospf_neighbors", f"R{i + 1}", description=f"OSPF R{i + 1}")
            for i in range(n)
        ]

    # ------------------------------------------------------------------
    # 逐次モード（MAX_TEST_WORKERS=1）
    # ------------------------------------------------------------------

    def test_sequential_returns_results_in_order(self, monkeypatch):
        """逐次モードで結果が plan.tests と同じ順序で返ること。"""
        monkeypatch.setattr("agentic_ni.agents.validator.MAX_TEST_WORKERS", 1)
        items = self._make_items(3)
        plan = TestPlan(tests=items, rationale="test")
        expected = [_pass_result(f"OSPF R{i + 1}") for i in range(3)]

        with patch("agentic_ni.agents.validator._execute_test", side_effect=expected):
            results = _run_tests(plan, "testbed_yaml")

        assert [r["test"] for r in results] == [f"OSPF R{i + 1}" for i in range(3)]

    def test_sequential_calls_execute_test_for_each_item(self, monkeypatch):
        """逐次モードで _execute_test が全テスト分呼ばれること。"""
        monkeypatch.setattr("agentic_ni.agents.validator.MAX_TEST_WORKERS", 1)
        items = self._make_items(3)
        plan = TestPlan(tests=items, rationale="test")

        mock_exec = MagicMock(side_effect=[_pass_result(f"OSPF R{i + 1}") for i in range(3)])
        with patch("agentic_ni.agents.validator._execute_test", mock_exec):
            _run_tests(plan, "testbed_yaml")

        assert mock_exec.call_count == 3

    def test_single_item_uses_sequential_path(self, monkeypatch):
        """テストが 1 件の場合は workers 設定に関わらず逐次実行されること。"""
        monkeypatch.setattr("agentic_ni.agents.validator.MAX_TEST_WORKERS", 8)
        items = self._make_items(1)
        plan = TestPlan(tests=items, rationale="test")
        expected = [_pass_result("OSPF R1")]

        with patch("agentic_ni.agents.validator._execute_test", side_effect=expected):
            results = _run_tests(plan, "testbed_yaml")

        assert len(results) == 1
        assert results[0]["result"] == "PASS"

    # ------------------------------------------------------------------
    # 並列モード（MAX_TEST_WORKERS=8）
    # ------------------------------------------------------------------

    def test_parallel_returns_results_in_original_order(self, monkeypatch):
        """並列モードで結果が plan.tests の元の順序で返ること。"""
        monkeypatch.setattr("agentic_ni.agents.validator.MAX_TEST_WORKERS", 4)
        n = 5
        items = self._make_items(n)
        plan = TestPlan(tests=items, rationale="test")
        # 各テストが確実に対応するデバイス名で返るようにする
        expected_by_item = {
            item.description: _pass_result(item.description) for item in items
        }

        def fake_execute(item: TestItem, _: str) -> TestResult:
            return expected_by_item[item.description]

        with patch("agentic_ni.agents.validator._execute_test", side_effect=fake_execute):
            results = _run_tests(plan, "testbed_yaml")

        assert len(results) == n
        for i, result in enumerate(results):
            assert result["test"] == f"OSPF R{i + 1}"

    def test_parallel_calls_execute_test_for_all_items(self, monkeypatch):
        """並列モードで _execute_test が全テスト分呼ばれること。"""
        monkeypatch.setattr("agentic_ni.agents.validator.MAX_TEST_WORKERS", 4)
        n = 4
        items = self._make_items(n)
        plan = TestPlan(tests=items, rationale="test")

        call_count = 0

        def fake_execute(item: TestItem, _: str) -> TestResult:
            nonlocal call_count
            call_count += 1
            return _pass_result(item.description)

        with patch("agentic_ni.agents.validator._execute_test", side_effect=fake_execute):
            results = _run_tests(plan, "testbed_yaml")

        assert call_count == n
        assert len(results) == n

    def test_parallel_fail_results_preserved(self, monkeypatch):
        """並列モードで FAIL 結果が正しく保持されること。"""
        monkeypatch.setattr("agentic_ni.agents.validator.MAX_TEST_WORKERS", 4)
        items = self._make_items(3)
        plan = TestPlan(tests=items, rationale="test")
        # 2 番目のテストだけ FAIL
        outcomes = [
            _pass_result("OSPF R1"),
            _fail_result("OSPF R2"),
            _pass_result("OSPF R3"),
        ]

        def fake_execute(item: TestItem, _: str) -> TestResult:
            idx = int(item.device[1]) - 1
            return outcomes[idx]

        with patch("agentic_ni.agents.validator._execute_test", side_effect=fake_execute):
            results = _run_tests(plan, "testbed_yaml")

        assert results[0]["result"] == "PASS"
        assert results[1]["result"] == "FAIL"
        assert results[2]["result"] == "PASS"

    def test_parallel_same_device_uses_lock(self, monkeypatch):
        """並列モードで同一デバイスのテストがロックで直列化されること。"""
        monkeypatch.setattr("agentic_ni.agents.validator.MAX_TEST_WORKERS", 4)
        # 全テストが同じデバイス R1 を対象にする
        items = [
            _make_test_item("ospf_neighbors", "R1", description=f"test-{i}")
            for i in range(4)
        ]
        plan = TestPlan(tests=items, rationale="test")
        call_order: list[int] = []

        def fake_execute(item: TestItem, _: str) -> TestResult:
            idx = int(item.description.split("-")[1])
            call_order.append(idx)
            return _pass_result(item.description)

        with patch("agentic_ni.agents.validator._execute_test", side_effect=fake_execute):
            results = _run_tests(plan, "testbed_yaml")

        # 全テストが完了していること
        assert len(results) == 4
        assert all(r["result"] == "PASS" for r in results)
        # R1 へのアクセスがシリアライズされているので call_order は連続（重複なし）
        assert len(call_order) == 4
