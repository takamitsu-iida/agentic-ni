"""live_report_node のユニットテスト。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# ヘルパー関数
# ---------------------------------------------------------------------------


def _make_record(
    device: str = "R1",
    host: str = "192.168.100.1",
    apply_mode: str = "config_merge",
    backup_config: str = "hostname R1\n",
    backup_lines: int = 1,
    applied_config: str = "",
    apply_success: bool = False,
    apply_output: str = "",
    apply_error: str = "",
    rollback_done: bool = False,
    rollback_error: str = "",
) -> dict:
    return {
        "device": device,
        "host": host,
        "apply_mode": apply_mode,
        "connectivity_ok": True,
        "backup_config": backup_config,
        "backup_lines": backup_lines,
        "applied_config": applied_config,
        "apply_success": apply_success,
        "apply_output": apply_output,
        "apply_error": apply_error,
        "rollback_done": rollback_done,
        "rollback_error": rollback_error,
    }


def _make_state(**overrides) -> dict:
    base = {
        "requirement": "テスト要件",
        "prompt_set": "demo",
        "fault_simulation_enabled": False,
        "skip_deploy": False,
        "error_history": [],
        "topology_yaml": "",
        "device_configs": {},
        "lab_id": "lab-001",
        "test_results": [
            {"test": "ospf_neighbor", "result": "PASS", "detail": "2 neighbors"},
        ],
        "test_plan_items": [],
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
            _make_record("R1", "192.168.100.1"),
            _make_record("R2", "192.168.100.2"),
        ],
        "live_verify_enabled": False,
        "live_human_decision": "yes",
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
    "R1": {"host": "192.168.100.1", "device_type": "cisco_ios",
           "username": "admin", "password": "password", "port": 22, "apply_mode": "config_merge"},
    "R2": {"host": "192.168.100.2", "device_type": "cisco_ios",
           "username": "admin", "password": "password", "port": 22, "apply_mode": "config_merge"},
}

R1_BACKUP = "hostname R1\ninterface Gi0/0\n ip address 10.0.12.1 255.255.255.252\n"
R2_BACKUP = "hostname R2\ninterface Gi0/0\n ip address 10.0.12.2 255.255.255.252\n"
R1_NEW = "hostname R1\nrouter ospf 1\n network 0.0.0.0 255.255.255.255 area 0\n"
R2_NEW = "hostname R2\nrouter ospf 1\n network 0.0.0.0 255.255.255.255 area 0\n"


# ---------------------------------------------------------------------------
# live_report_node — apply モード（全成功）
# ---------------------------------------------------------------------------


class TestLiveReportNodeApplySuccess:
    def _run(self) -> dict:
        from agentic_ni.graph import live_report_node

        state = _make_state(
            live_human_decision="yes",
            live_apply_records=[
                _make_record("R1", "192.168.100.1", applied_config=R1_NEW,
                             apply_success=True, backup_lines=15),
                _make_record("R2", "192.168.100.2", applied_config=R2_NEW,
                             apply_success=True, backup_lines=12),
            ],
        )
        return live_report_node(state)

    def test_returns_live_report(self):
        result = self._run()
        assert "live_report" in result
        assert result["live_report"]

    def test_live_report_appended_to_final_report(self):
        result = self._run()
        assert "final_report" in result
        assert result["final_report"].startswith("# 検証成功レポート")
        assert result["live_report"] in result["final_report"]

    def test_contains_device_names(self):
        result = self._run()
        assert "R1" in result["live_report"]
        assert "R2" in result["live_report"]

    def test_contains_host_addresses(self):
        result = self._run()
        assert "192.168.100.1" in result["live_report"]
        assert "192.168.100.2" in result["live_report"]

    def test_success_verdict(self):
        result = self._run()
        assert "全 2 デバイスへの投入が成功" in result["live_report"]

    def test_contains_apply_header(self):
        result = self._run()
        assert "実機適用レポート（Phase I）" in result["live_report"]
        assert "新規コンフィグ投入" in result["live_report"]

    def test_apply_mode_in_report(self):
        result = self._run()
        assert "config_merge" in result["live_report"]

    def test_no_failure_detail_section(self):
        result = self._run()
        assert "失敗詳細" not in result["live_report"]

    def test_timestamp_in_report(self):
        result = self._run()
        assert "生成日時" in result["live_report"]


# ---------------------------------------------------------------------------
# live_report_node — apply モード（一部失敗 + 自動ロールバック）
# ---------------------------------------------------------------------------


class TestLiveReportNodeApplyPartialFailure:
    def _run(self) -> dict:
        from agentic_ni.graph import live_report_node

        state = _make_state(
            live_human_decision="yes",
            live_apply_records=[
                _make_record("R1", "192.168.100.1", applied_config=R1_NEW,
                             apply_success=True),
                _make_record("R2", "192.168.100.2", applied_config=R2_NEW,
                             apply_success=False, apply_error="SSH Timeout",
                             rollback_done=True, backup_lines=12),
            ],
        )
        return live_report_node(state)

    def test_verdict_mentions_failure(self):
        result = self._run()
        assert "失敗" in result["live_report"]
        assert "1 台成功" in result["live_report"] or "2 台" not in result["live_report"]

    def test_rollback_status_in_table(self):
        result = self._run()
        assert "ロールバック成功" in result["live_report"]

    def test_failure_detail_section_exists(self):
        result = self._run()
        assert "失敗詳細" in result["live_report"]

    def test_failure_detail_contains_error(self):
        result = self._run()
        assert "SSH Timeout" in result["live_report"]

    def test_failure_detail_shows_rollback_success(self):
        result = self._run()
        assert "12 行を復元" in result["live_report"]


# ---------------------------------------------------------------------------
# live_report_node — apply モード（失敗 + ロールバック失敗）
# ---------------------------------------------------------------------------


class TestLiveReportNodeApplyRollbackFailure:
    def _run(self) -> dict:
        from agentic_ni.graph import live_report_node

        state = _make_state(
            live_human_decision="yes",
            live_apply_records=[
                _make_record("R1", "192.168.100.1", applied_config=R1_NEW,
                             apply_success=False, apply_error="Auth failed",
                             rollback_done=False, rollback_error="Rollback error"),
            ],
        )
        return live_report_node(state)

    def test_rollback_failure_in_table(self):
        result = self._run()
        assert "ロールバック失敗" in result["live_report"]

    def test_rollback_error_in_detail(self):
        result = self._run()
        assert "Rollback error" in result["live_report"]

    def test_rollback_not_done_shown(self):
        """ロールバック失敗は「❌ ロールバック失敗」と表示されること。"""
        result = self._run()
        assert "ロールバック失敗" in result["live_report"]


# ---------------------------------------------------------------------------
# live_report_node — apply モード（スキップデバイス含む）
# ---------------------------------------------------------------------------


class TestLiveReportNodeApplySkipped:
    def test_skipped_device_shown(self):
        from agentic_ni.graph import live_report_node

        state = _make_state(
            live_human_decision="yes",
            live_apply_records=[
                # applied_config が空 = スキップ
                _make_record("R1", "192.168.100.1", applied_config="",
                             apply_success=False),
            ],
        )
        result = live_report_node(state)
        assert "スキップ" in result["live_report"]

    def test_no_applied_devices_verdict(self):
        from agentic_ni.graph import live_report_node

        state = _make_state(
            live_human_decision="yes",
            live_apply_records=[
                _make_record("R1", "192.168.100.1", applied_config=""),
            ],
        )
        result = live_report_node(state)
        assert "投入対象デバイスがありませんでした" in result["live_report"]


# ---------------------------------------------------------------------------
# live_report_node — rollback-only モード
# ---------------------------------------------------------------------------


class TestLiveReportNodeRollbackOnly:
    def _run(self, **rec_overrides) -> dict:
        from agentic_ni.graph import live_report_node

        state = _make_state(
            live_human_decision="rollback-only",
            live_apply_records=[
                _make_record("R1", "192.168.100.1", rollback_done=True),
                _make_record("R2", "192.168.100.2", rollback_done=True),
            ],
        )
        return live_report_node(state)

    def test_header_mentions_rollback_only(self):
        result = self._run()
        assert "rollback-only" in result["live_report"]

    def test_operation_is_rollback(self):
        result = self._run()
        assert "バックアップへのロールバック" in result["live_report"]

    def test_success_verdict(self):
        result = self._run()
        assert "全 2 デバイスのロールバックが成功" in result["live_report"]

    def test_contains_device_names(self):
        result = self._run()
        assert "R1" in result["live_report"]
        assert "R2" in result["live_report"]

    def test_no_apply_mode_column(self):
        """rollback-only テーブルには「適用結果」列がないこと。"""
        result = self._run()
        # rollback-only テーブルヘッダーは「デバイス | ホスト | ロールバック結果」のみ
        assert "モード" not in result["live_report"].split("### ロールバック")[1]

    def test_rollback_failure_verdict(self):
        from agentic_ni.graph import live_report_node

        state = _make_state(
            live_human_decision="rollback-only",
            live_apply_records=[
                _make_record("R1", "192.168.100.1", rollback_done=False,
                             rollback_error="SSH Error"),
            ],
        )
        result = live_report_node(state)
        assert "ロールバックに失敗" in result["live_report"]

    def test_rollback_failure_detail(self):
        from agentic_ni.graph import live_report_node

        state = _make_state(
            live_human_decision="rollback-only",
            live_apply_records=[
                _make_record("R1", "192.168.100.1",
                             backup_config=R1_BACKUP,
                             rollback_done=False, rollback_error="Timeout"),
            ],
        )
        result = live_report_node(state)
        assert "Timeout" in result["live_report"]

    def test_empty_backup_skipped(self):
        from agentic_ni.graph import live_report_node

        state = _make_state(
            live_human_decision="rollback-only",
            live_apply_records=[
                _make_record("R1", "192.168.100.1", backup_config="",
                             rollback_done=False),
            ],
        )
        result = live_report_node(state)
        assert "バックアップなし" in result["live_report"]
        assert "スキップ" in result["live_report"]


# ---------------------------------------------------------------------------
# live_report_node — final_report への追記
# ---------------------------------------------------------------------------


class TestLiveReportNodeFinalReport:
    def test_appends_to_existing_final_report(self):
        from agentic_ni.graph import live_report_node

        original = "# 検証成功レポート\n\n全テスト PASS。"
        state = _make_state(
            final_report=original,
            live_human_decision="yes",
            live_apply_records=[
                _make_record("R1", "192.168.100.1", applied_config=R1_NEW,
                             apply_success=True),
            ],
        )
        result = live_report_node(state)

        assert result["final_report"].startswith(original)
        assert len(result["final_report"]) > len(original)

    def test_live_report_stored_separately(self):
        from agentic_ni.graph import live_report_node

        state = _make_state(
            live_human_decision="yes",
            live_apply_records=[
                _make_record("R1", "192.168.100.1", applied_config=R1_NEW, apply_success=True),
            ],
        )
        result = live_report_node(state)

        assert result["live_report"]
        assert result["live_report"] != result["final_report"]


# ---------------------------------------------------------------------------
# compile_graph_apply_to_live — Step 5 統合テスト
# ---------------------------------------------------------------------------


class TestCompileGraphApplyToLiveStep5:
    def test_report_generated_after_apply_success(self, tmp_path):
        """precheck → confirm(yes) → apply(全成功) → live_report の E2E テスト。"""
        from langgraph.types import Command
        from agentic_ni.graph import compile_graph_apply_to_live, initial_state_apply_to_live

        inv_path = _write_inventory(tmp_path)
        state = initial_state_apply_to_live(
            requirement="テスト要件",
            lab_id="lab-001",
            inventory_path=inv_path,
        )
        state["device_configs"] = {"R1": R1_NEW, "R2": R2_NEW}
        state["test_results"] = [{"test": "ospf", "result": "PASS", "detail": "OK"}]

        apply_ok = {"device": "", "success": True, "output": "end\n", "error": ""}
        app = compile_graph_apply_to_live()
        thread = {"configurable": {"thread_id": "step5-ok-01"}}

        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.check_connectivity", return_value={"R1": True, "R2": True}), \
             patch("agentic_ni.tools.pyats_tools.backup_running_config", return_value={"R1": R1_BACKUP, "R2": R2_BACKUP}), \
             patch("agentic_ni.tools.pyats_tools.apply_config", return_value=apply_ok):
            app.invoke(state, thread)
            result = app.invoke(Command(resume={"decision": "yes"}), thread)

        assert result["live_report"]
        assert "実機適用レポート（Phase I）" in result["live_report"]
        assert "全 2 デバイスへの投入が成功" in result["live_report"]
        assert result["live_report"] in result["final_report"]

    def test_report_generated_after_rollback(self, tmp_path):
        """precheck → confirm(rollback-only) → live_rollback → live_report の E2E テスト。"""
        from langgraph.types import Command
        from agentic_ni.graph import compile_graph_apply_to_live, initial_state_apply_to_live

        inv_path = _write_inventory(tmp_path)
        state = initial_state_apply_to_live(
            requirement="テスト要件",
            inventory_path=inv_path,
        )

        rb_ok = {"device": "", "success": True, "output": "end\n", "error": ""}
        app = compile_graph_apply_to_live()
        thread = {"configurable": {"thread_id": "step5-rb-01"}}

        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.check_connectivity", return_value={"R1": True, "R2": True}), \
             patch("agentic_ni.tools.pyats_tools.backup_running_config", return_value={"R1": R1_BACKUP, "R2": R2_BACKUP}), \
             patch("agentic_ni.tools.pyats_tools.rollback_config", return_value=rb_ok):
            app.invoke(state, thread)
            result = app.invoke(Command(resume={"decision": "rollback-only"}), thread)

        assert result["live_report"]
        assert "rollback-only" in result["live_report"]
        assert "全 2 デバイスのロールバックが成功" in result["live_report"]

    def test_apply_with_failure_shows_rollback_in_report(self, tmp_path):
        """apply 失敗 → 自動ロールバック → レポートに詳細が含まれること。"""
        from langgraph.types import Command
        from agentic_ni.graph import compile_graph_apply_to_live, initial_state_apply_to_live

        inv_path = _write_inventory(tmp_path)
        state = initial_state_apply_to_live(
            requirement="テスト要件",
            inventory_path=inv_path,
        )
        state["device_configs"] = {"R1": R1_NEW, "R2": R2_NEW}

        def apply_side(device_name, cfg, config_text):
            if device_name == "R2":
                return {"device": "R2", "success": False, "output": "", "error": "Auth Error"}
            return {"device": device_name, "success": True, "output": "end\n", "error": ""}

        rb_ok = {"device": "", "success": True, "output": "end\n", "error": ""}
        app = compile_graph_apply_to_live()
        thread = {"configurable": {"thread_id": "step5-fail-01"}}

        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.check_connectivity", return_value={"R1": True, "R2": True}), \
             patch("agentic_ni.tools.pyats_tools.backup_running_config", return_value={"R1": R1_BACKUP, "R2": R2_BACKUP}), \
             patch("agentic_ni.tools.pyats_tools.apply_config", side_effect=apply_side), \
             patch("agentic_ni.tools.pyats_tools.rollback_config", return_value=rb_ok):
            app.invoke(state, thread)
            result = app.invoke(Command(resume={"decision": "yes"}), thread)

        assert "Auth Error" in result["live_report"]
        assert "ロールバック成功" in result["live_report"]
        assert "失敗詳細" in result["live_report"]
