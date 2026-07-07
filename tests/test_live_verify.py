"""live_verify_node および build_testbed_from_inventory のユニットテスト。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml


# ---------------------------------------------------------------------------
# ヘルパー関数
# ---------------------------------------------------------------------------


def _make_state(**overrides) -> dict:
    base = {
        "requirement": "テスト要件",
        "prompt_set": "demo",
        "fault_simulation_enabled": False,
        "skip_deploy": False,
        "error_history": [],
        "topology_yaml": "",
        "device_configs": {"R1": "hostname R1\n", "R2": "hostname R2\n"},
        "lab_id": "lab-001",
        "test_results": [
            {"test": "ospf_neighbor", "result": "PASS", "detail": "2 neighbors"},
        ],
        "test_plan_items": [
            {
                "test_type": "ospf_neighbors",
                "device": "R1",
                "target": None,
                "description": "OSPFネイバー確認 R1",
            },
            {
                "test_type": "ping",
                "device": "R1",
                "target": "2.2.2.2",
                "description": "ping 2.2.2.2",
            },
        ],
        "error_log": "",
        "retry_count": 0,
        "fault_scenario_results": [],
        "fault_report": "",
        "troubleshoot_lab_id": "",
        "troubleshoot_issue": "",
        "collected_state": {},
        "diagnosis": "",
        "fix_records": [],
        "troubleshoot_retry_count": 0,
        "troubleshoot_report": "",
        "analyze_request": "",
        "analysis_result": "",
        "live_inventory_path": "",
        "live_apply_records": [
            {
                "device": "R1",
                "host": "192.168.100.1",
                "apply_mode": "config_merge",
                "connectivity_ok": True,
                "backup_config": "hostname R1\n",
                "backup_lines": 1,
                "applied_config": "hostname R1\nrouter ospf 1\n",
                "apply_success": True,
                "apply_output": "end\n",
                "apply_error": "",
                "rollback_done": False,
                "rollback_error": "",
            },
        ],
        "live_verify_enabled": True,
        "live_human_decision": "yes",
        "live_test_results": [],
        "live_report": "",
        "final_report": "# 検証成功レポート\n\n全テスト PASS。",
    }
    base.update(overrides)
    return base


def _write_inventory(tmp_path: Path) -> str:
    content = """\
metadata:
  description: "テスト用"
devices:
  R1:
    host: "192.168.100.1"
    device_type: "cisco_ios"
    username: "admin"
    password: "password"
    port: 22
    apply_mode: "config_merge"
  R2:
    host: "192.168.100.2"
    device_type: "cisco_ios"
    username: "admin"
    password: "password"
    port: 22
    apply_mode: "config_merge"
"""
    inv_file = tmp_path / "demo.yaml"
    inv_file.write_text(content, encoding="utf-8")
    return str(inv_file)


SAMPLE_DEVICES = {
    "R1": {
        "host": "192.168.100.1",
        "device_type": "cisco_ios",
        "username": "admin",
        "password": "password",
        "port": 22,
        "apply_mode": "config_merge",
    },
    "R2": {
        "host": "192.168.100.2",
        "device_type": "cisco_ios",
        "username": "admin",
        "password": "password",
        "port": 22,
        "apply_mode": "config_merge",
    },
}

R1_BACKUP = "hostname R1\ninterface Gi0/0\n ip address 10.0.12.1 255.255.255.252\n"
R2_BACKUP = "hostname R2\ninterface Gi0/0\n ip address 10.0.12.2 255.255.255.252\n"
R1_NEW = "hostname R1\nrouter ospf 1\n network 0.0.0.0 255.255.255.255 area 0\n"
R2_NEW = "hostname R2\nrouter ospf 1\n network 0.0.0.0 255.255.255.255 area 0\n"


# ---------------------------------------------------------------------------
# build_testbed_from_inventory のテスト
# ---------------------------------------------------------------------------


class TestBuildTestbedFromInventory:
    def test_returns_yaml_string(self):
        from agentic_ni.tools.pyats_tools import build_testbed_from_inventory

        result = build_testbed_from_inventory(SAMPLE_DEVICES)
        assert isinstance(result, str)

    def test_yaml_contains_devices(self):
        from agentic_ni.tools.pyats_tools import build_testbed_from_inventory

        result = build_testbed_from_inventory(SAMPLE_DEVICES)
        data = yaml.safe_load(result)
        assert "devices" in data
        assert "R1" in data["devices"]
        assert "R2" in data["devices"]

    def test_contains_ssh_connection(self):
        from agentic_ni.tools.pyats_tools import build_testbed_from_inventory

        result = build_testbed_from_inventory(SAMPLE_DEVICES)
        data = yaml.safe_load(result)
        r1 = data["devices"]["R1"]
        assert r1["connections"]["default"]["protocol"] == "ssh"

    def test_host_ip_is_set(self):
        from agentic_ni.tools.pyats_tools import build_testbed_from_inventory

        result = build_testbed_from_inventory(SAMPLE_DEVICES)
        data = yaml.safe_load(result)
        assert data["devices"]["R1"]["connections"]["default"]["ip"] == "192.168.100.1"
        assert data["devices"]["R2"]["connections"]["default"]["ip"] == "192.168.100.2"

    def test_credentials_are_set(self):
        from agentic_ni.tools.pyats_tools import build_testbed_from_inventory

        result = build_testbed_from_inventory(SAMPLE_DEVICES)
        data = yaml.safe_load(result)
        creds = data["devices"]["R1"]["credentials"]["default"]
        assert creds["username"] == "admin"
        assert creds["password"] == "password"

    def test_port_is_set(self):
        from agentic_ni.tools.pyats_tools import build_testbed_from_inventory

        result = build_testbed_from_inventory(SAMPLE_DEVICES)
        data = yaml.safe_load(result)
        assert data["devices"]["R1"]["connections"]["default"]["port"] == 22

    def test_os_is_ios_for_cisco_ios(self):
        from agentic_ni.tools.pyats_tools import build_testbed_from_inventory

        result = build_testbed_from_inventory(SAMPLE_DEVICES)
        data = yaml.safe_load(result)
        assert data["devices"]["R1"]["os"] == "ios"

    def test_empty_devices(self):
        from agentic_ni.tools.pyats_tools import build_testbed_from_inventory

        result = build_testbed_from_inventory({})
        data = yaml.safe_load(result)
        assert data["devices"] == {}


# ---------------------------------------------------------------------------
# _device_type_to_pyats_os のテスト
# ---------------------------------------------------------------------------


class TestDeviceTypeToOsMapping:
    def test_cisco_ios_maps_to_ios(self):
        from agentic_ni.tools.pyats_tools import _device_type_to_pyats_os
        assert _device_type_to_pyats_os("cisco_ios") == "ios"

    def test_cisco_xe_maps_to_iosxe(self):
        from agentic_ni.tools.pyats_tools import _device_type_to_pyats_os
        assert _device_type_to_pyats_os("cisco_xe") == "iosxe"

    def test_cisco_nxos_maps_to_nxos(self):
        from agentic_ni.tools.pyats_tools import _device_type_to_pyats_os
        assert _device_type_to_pyats_os("cisco_nxos") == "nxos"

    def test_arista_eos_maps_to_eos(self):
        from agentic_ni.tools.pyats_tools import _device_type_to_pyats_os
        assert _device_type_to_pyats_os("arista_eos") == "eos"

    def test_juniper_junos_maps_to_junos(self):
        from agentic_ni.tools.pyats_tools import _device_type_to_pyats_os
        assert _device_type_to_pyats_os("juniper_junos") == "junos"

    def test_unknown_falls_back_to_ios(self):
        from agentic_ni.tools.pyats_tools import _device_type_to_pyats_os
        assert _device_type_to_pyats_os("unknown_vendor") == "ios"

    def test_case_insensitive(self):
        from agentic_ni.tools.pyats_tools import _device_type_to_pyats_os
        assert _device_type_to_pyats_os("CISCO_IOS") == "ios"


# ---------------------------------------------------------------------------
# _should_verify_after_apply のテスト
# ---------------------------------------------------------------------------


class TestShouldVerifyAfterApply:
    def test_returns_verify_when_enabled_and_yes(self):
        from agentic_ni.graph import _should_verify_after_apply

        state = _make_state(live_verify_enabled=True, live_human_decision="yes")
        assert _should_verify_after_apply(state) == "verify"

    def test_returns_report_when_disabled(self):
        from agentic_ni.graph import _should_verify_after_apply

        state = _make_state(live_verify_enabled=False, live_human_decision="yes")
        assert _should_verify_after_apply(state) == "report"

    def test_returns_report_when_rollback_only(self):
        from agentic_ni.graph import _should_verify_after_apply

        state = _make_state(live_verify_enabled=True, live_human_decision="rollback-only")
        assert _should_verify_after_apply(state) == "report"

    def test_returns_report_when_no_decision(self):
        from agentic_ni.graph import _should_verify_after_apply

        state = _make_state(live_verify_enabled=True, live_human_decision="no")
        assert _should_verify_after_apply(state) == "report"

    def test_returns_report_when_verify_not_set(self):
        from agentic_ni.graph import _should_verify_after_apply

        state = _make_state(live_verify_enabled=False)
        assert _should_verify_after_apply(state) == "report"


# ---------------------------------------------------------------------------
# live_verify_node — テスト計画なし
# ---------------------------------------------------------------------------


class TestLiveVerifyNodeNoTestPlan:
    def test_returns_empty_results_when_no_plan(self, tmp_path):
        from agentic_ni.graph import live_verify_node

        inv_path = _write_inventory(tmp_path)
        state = _make_state(live_inventory_path=inv_path, test_plan_items=[])

        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES):
            result = live_verify_node(state)

        assert result["live_test_results"] == []


# ---------------------------------------------------------------------------
# live_verify_node — 正常系
# ---------------------------------------------------------------------------


class TestLiveVerifyNodeSuccess:
    def test_returns_live_test_results(self, tmp_path):
        from agentic_ni.graph import live_verify_node

        inv_path = _write_inventory(tmp_path)
        state = _make_state(live_inventory_path=inv_path)

        pass_result = {"test": "OSPFネイバー確認 R1", "result": "PASS", "detail": "1 neighbor(s) FULL"}
        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.build_testbed_from_inventory", return_value="testbed: yaml"), \
             patch("agentic_ni.agents.validator._execute_test", return_value=pass_result):
            result = live_verify_node(state)

        assert "live_test_results" in result
        assert len(result["live_test_results"]) == 2  # 2 test_plan_items

    def test_all_results_stored(self, tmp_path):
        from agentic_ni.graph import live_verify_node

        inv_path = _write_inventory(tmp_path)
        state = _make_state(live_inventory_path=inv_path)

        pass_r = {"test": "test", "result": "PASS", "detail": "OK"}
        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.build_testbed_from_inventory", return_value="testbed: yaml"), \
             patch("agentic_ni.agents.validator._execute_test", return_value=pass_r):
            result = live_verify_node(state)

        assert all(r["result"] == "PASS" for r in result["live_test_results"])

    def test_mixed_results(self, tmp_path):
        """PASS / FAIL が混在する場合も正しく格納されること。"""
        from agentic_ni.graph import live_verify_node

        inv_path = _write_inventory(tmp_path)
        state = _make_state(live_inventory_path=inv_path)

        call_count = [0]
        def execute_side(item, testbed_yaml):
            call_count[0] += 1
            if call_count[0] == 1:
                return {"test": item.description, "result": "PASS", "detail": "OK"}
            return {"test": item.description, "result": "FAIL", "detail": "ping FAILED"}

        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.build_testbed_from_inventory", return_value="testbed: yaml"), \
             patch("agentic_ni.agents.validator._execute_test", side_effect=execute_side):
            result = live_verify_node(state)

        results = result["live_test_results"]
        assert len(results) == 2
        assert results[0]["result"] == "PASS"
        assert results[1]["result"] == "FAIL"


# ---------------------------------------------------------------------------
# live_verify_node — エラーケース
# ---------------------------------------------------------------------------


class TestLiveVerifyNodeErrors:
    def test_inventory_not_found_returns_fail(self, tmp_path, monkeypatch):
        from agentic_ni.graph import live_verify_node

        monkeypatch.chdir(tmp_path)
        state = _make_state(live_inventory_path="")
        result = live_verify_node(state)

        assert len(result["live_test_results"]) == 1
        assert result["live_test_results"][0]["result"] == "FAIL"

    def test_pyats_import_error_handled(self, tmp_path):
        """pyATS 未インストールの場合はエラーを記録してスキップ。"""
        from agentic_ni.graph import live_verify_node

        inv_path = _write_inventory(tmp_path)
        state = _make_state(live_inventory_path=inv_path)

        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.build_testbed_from_inventory", return_value="testbed: yaml"), \
             patch("agentic_ni.agents.validator._execute_test", side_effect=ImportError("pyATS not installed")):
            result = live_verify_node(state)

        assert all(r["result"] == "FAIL" for r in result["live_test_results"])
        assert "pyATS" in result["live_test_results"][0]["detail"]


# ---------------------------------------------------------------------------
# live_report_node — live_test_results セクション
# ---------------------------------------------------------------------------


class TestLiveReportNodeWithVerify:
    def test_verify_results_in_report(self):
        from agentic_ni.graph import live_report_node

        state = _make_state(
            live_human_decision="yes",
            live_apply_records=[
                {
                    "device": "R1", "host": "192.168.100.1", "apply_mode": "config_merge",
                    "connectivity_ok": True, "backup_config": R1_BACKUP, "backup_lines": 3,
                    "applied_config": R1_NEW, "apply_success": True, "apply_output": "end\n",
                    "apply_error": "", "rollback_done": False, "rollback_error": "",
                },
            ],
            live_test_results=[
                {"test": "OSPFネイバー確認 R1", "result": "PASS", "detail": "1 neighbor FULL"},
                {"test": "ping 2.2.2.2", "result": "PASS", "detail": "ping OK"},
            ],
        )
        result = live_report_node(state)

        assert "実機 pyATS 検証結果" in result["live_report"]
        assert "OSPFネイバー確認 R1" in result["live_report"]
        assert "全 2 テスト PASS" in result["live_report"]

    def test_verify_fail_shown_in_report(self):
        from agentic_ni.graph import live_report_node

        state = _make_state(
            live_human_decision="yes",
            live_apply_records=[
                {
                    "device": "R1", "host": "192.168.100.1", "apply_mode": "config_merge",
                    "connectivity_ok": True, "backup_config": R1_BACKUP, "backup_lines": 3,
                    "applied_config": R1_NEW, "apply_success": True, "apply_output": "",
                    "apply_error": "", "rollback_done": False, "rollback_error": "",
                },
            ],
            live_test_results=[
                {"test": "ping 2.2.2.2", "result": "FAIL", "detail": "ping FAILED"},
            ],
        )
        result = live_report_node(state)

        assert "1 PASS / 1 FAIL" in result["live_report"] or "0 PASS" in result["live_report"]
        assert "❌ FAIL" in result["live_report"]

    def test_no_verify_section_when_empty(self):
        from agentic_ni.graph import live_report_node

        state = _make_state(
            live_human_decision="yes",
            live_apply_records=[
                {
                    "device": "R1", "host": "192.168.100.1", "apply_mode": "config_merge",
                    "connectivity_ok": True, "backup_config": R1_BACKUP, "backup_lines": 3,
                    "applied_config": R1_NEW, "apply_success": True, "apply_output": "",
                    "apply_error": "", "rollback_done": False, "rollback_error": "",
                },
            ],
            live_test_results=[],  # 空
        )
        result = live_report_node(state)

        assert "実機 pyATS 検証結果" not in result["live_report"]


# ---------------------------------------------------------------------------
# compile_graph_apply_to_live — Step 6 E2E テスト
# ---------------------------------------------------------------------------


class TestCompileGraphApplyToLiveStep6:
    def test_graph_with_live_verify_enabled(self, tmp_path):
        """live_verify_enabled=True の場合 live_verify_node が実行されること。"""
        from langgraph.types import Command
        from agentic_ni.graph import compile_graph_apply_to_live, initial_state_apply_to_live

        inv_path = _write_inventory(tmp_path)
        state = initial_state_apply_to_live(
            requirement="テスト要件",
            lab_id="lab-001",
            inventory_path=inv_path,
            live_verify_enabled=True,
        )
        state["device_configs"] = {"R1": R1_NEW, "R2": R2_NEW}
        state["test_plan_items"] = [
            {"test_type": "ospf_neighbors", "device": "R1", "target": None,
             "description": "OSPF確認"},
        ]

        apply_ok = {"device": "", "success": True, "output": "end\n", "error": ""}
        pass_r = {"test": "OSPF確認", "result": "PASS", "detail": "OK"}

        app = compile_graph_apply_to_live()
        thread = {"configurable": {"thread_id": "step6-verify-01"}}

        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.check_connectivity", return_value={"R1": True, "R2": True}), \
             patch("agentic_ni.tools.pyats_tools.backup_running_config", return_value={"R1": R1_BACKUP, "R2": R2_BACKUP}), \
             patch("agentic_ni.tools.pyats_tools.apply_config", return_value=apply_ok), \
             patch("agentic_ni.tools.pyats_tools.build_testbed_from_inventory", return_value="testbed: yaml"), \
             patch("agentic_ni.agents.validator._execute_test", return_value=pass_r):
            app.invoke(state, thread)
            result = app.invoke(Command(resume={"decision": "yes"}), thread)

        assert result["live_test_results"]
        assert result["live_test_results"][0]["result"] == "PASS"
        assert "実機 pyATS 検証結果" in result["live_report"]

    def test_graph_without_live_verify(self, tmp_path):
        """live_verify_enabled=False の場合 live_verify_node はスキップされること。"""
        from langgraph.types import Command
        from agentic_ni.graph import compile_graph_apply_to_live, initial_state_apply_to_live

        inv_path = _write_inventory(tmp_path)
        state = initial_state_apply_to_live(
            requirement="テスト要件",
            inventory_path=inv_path,
            live_verify_enabled=False,  # verify無効
        )
        state["device_configs"] = {"R1": R1_NEW}

        apply_ok = {"device": "", "success": True, "output": "end\n", "error": ""}
        app = compile_graph_apply_to_live()
        thread = {"configurable": {"thread_id": "step6-noverify-01"}}

        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.check_connectivity", return_value={"R1": True, "R2": True}), \
             patch("agentic_ni.tools.pyats_tools.backup_running_config", return_value={"R1": R1_BACKUP, "R2": R2_BACKUP}), \
             patch("agentic_ni.tools.pyats_tools.apply_config", return_value=apply_ok):
            app.invoke(state, thread)
            result = app.invoke(Command(resume={"decision": "yes"}), thread)

        # verify が実行されていないので live_test_results は空
        assert result["live_test_results"] == []
        assert "実機 pyATS 検証結果" not in result.get("live_report", "")
