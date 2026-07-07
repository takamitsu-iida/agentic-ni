"""live_apply_node / live_rollback_node のユニットテスト。実機は不要（すべてモック）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# ヘルパー関数
# ---------------------------------------------------------------------------


def _make_precheck_record(
    device: str,
    host: str,
    backup_config: str = "hostname R1\n",
    apply_mode: str = "config_merge",
    connectivity_ok: bool = True,
) -> dict:
    """precheck 完了済みの LiveApplyRecord を返す。"""
    return {
        "device": device,
        "host": host,
        "apply_mode": apply_mode,
        "connectivity_ok": connectivity_ok,
        "backup_config": backup_config,
        "backup_lines": len([l for l in backup_config.splitlines() if l.strip()]),
        "applied_config": "",
        "apply_success": False,
        "apply_output": "",
        "apply_error": "",
        "rollback_done": False,
        "rollback_error": "",
    }


def _make_state(**overrides) -> dict:
    """最小限の AgentState 風辞書を返す。"""
    base = {
        "requirement": "R1 と R2 を OSPF で接続する",
        "prompt_set": "demo",
        "fault_simulation_enabled": False,
        "skip_deploy": False,
        "error_history": [],
        "topology_yaml": "",
        "device_configs": {
            "R1": "hostname R1\nrouter ospf 1\n network 0.0.0.0 255.255.255.255 area 0\n",
            "R2": "hostname R2\nrouter ospf 1\n network 0.0.0.0 255.255.255.255 area 0\n",
        },
        "lab_id": "lab-001",
        "test_results": [
            {"test": "ospf_neighbor", "result": "PASS", "detail": "OK"},
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
            _make_precheck_record("R1", "192.168.100.1", backup_config="hostname R1\n"),
            _make_precheck_record("R2", "192.168.100.2", backup_config="hostname R2\n"),
        ],
        "live_verify_enabled": False,
        "live_human_decision": "yes",
        "live_report": "",
        "final_report": "# 検証成功レポート\n\n全テスト PASS。",
    }
    base.update(overrides)
    return base


def _write_inventory(tmp_path: Path, content: str = None) -> str:
    content = content or """\
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
R1_NEW_CONFIG = "hostname R1\nrouter ospf 1\n network 0.0.0.0 255.255.255.255 area 0\n"
R2_NEW_CONFIG = "hostname R2\nrouter ospf 1\n network 0.0.0.0 255.255.255.255 area 0\n"


# ---------------------------------------------------------------------------
# live_apply_node — 正常系（全デバイス成功）
# ---------------------------------------------------------------------------


class TestLiveApplyNodeSuccess:
    def _run(self, tmp_path):
        from agentic_ni.graph import live_apply_node

        inv_path = _write_inventory(tmp_path)
        state = _make_state(
            live_inventory_path=inv_path,
            live_apply_records=[
                _make_precheck_record("R1", "192.168.100.1", R1_BACKUP),
                _make_precheck_record("R2", "192.168.100.2", R2_BACKUP),
            ],
        )

        apply_ok = {"device": "R1", "success": True, "output": "end\n", "error": ""}
        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.apply_config", return_value=apply_ok):
            return live_apply_node(state)

    def test_returns_live_apply_records(self, tmp_path):
        result = self._run(tmp_path)
        assert "live_apply_records" in result
        assert len(result["live_apply_records"]) == 2

    def test_apply_success_is_true(self, tmp_path):
        result = self._run(tmp_path)
        for rec in result["live_apply_records"]:
            assert rec["apply_success"] is True

    def test_applied_config_is_stored(self, tmp_path):
        from agentic_ni.graph import live_apply_node

        inv_path = _write_inventory(tmp_path)
        state = _make_state(
            live_inventory_path=inv_path,
            device_configs={"R1": R1_NEW_CONFIG, "R2": R2_NEW_CONFIG},
            live_apply_records=[
                _make_precheck_record("R1", "192.168.100.1", R1_BACKUP),
                _make_precheck_record("R2", "192.168.100.2", R2_BACKUP),
            ],
        )
        apply_ok = {"device": "R1", "success": True, "output": "end\n", "error": ""}
        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.apply_config", return_value=apply_ok):
            result = live_apply_node(state)

        r1_rec = next(r for r in result["live_apply_records"] if r["device"] == "R1")
        assert r1_rec["applied_config"] == R1_NEW_CONFIG

    def test_error_log_is_empty_on_success(self, tmp_path):
        result = self._run(tmp_path)
        assert result.get("error_log", "") == ""

    def test_rollback_not_called_on_success(self, tmp_path):
        from agentic_ni.graph import live_apply_node

        inv_path = _write_inventory(tmp_path)
        state = _make_state(live_inventory_path=inv_path)

        apply_ok = {"device": "R1", "success": True, "output": "end\n", "error": ""}
        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.apply_config", return_value=apply_ok), \
             patch("agentic_ni.tools.pyats_tools.rollback_config") as mock_rb:
            live_apply_node(state)

        mock_rb.assert_not_called()


# ---------------------------------------------------------------------------
# live_apply_node — 失敗デバイスのロールバック（Level 6）
# ---------------------------------------------------------------------------


class TestLiveApplyNodeAutoRollback:
    def test_rollback_called_on_failure(self, tmp_path):
        from agentic_ni.graph import live_apply_node

        inv_path = _write_inventory(tmp_path)
        state = _make_state(
            live_inventory_path=inv_path,
            live_apply_records=[
                _make_precheck_record("R1", "192.168.100.1", R1_BACKUP),
            ],
        )

        apply_fail = {"device": "R1", "success": False, "output": "", "error": "SSH Error"}
        rollback_ok = {"device": "R1", "success": True, "output": "end\n", "error": ""}
        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.apply_config", return_value=apply_fail), \
             patch("agentic_ni.tools.pyats_tools.rollback_config", return_value=rollback_ok) as mock_rb:
            result = live_apply_node(state)

        mock_rb.assert_called_once()
        r1_rec = result["live_apply_records"][0]
        assert r1_rec["apply_success"] is False
        assert r1_rec["rollback_done"] is True

    def test_error_log_set_on_failure(self, tmp_path):
        from agentic_ni.graph import live_apply_node

        inv_path = _write_inventory(tmp_path)
        state = _make_state(
            live_inventory_path=inv_path,
            live_apply_records=[_make_precheck_record("R1", "192.168.100.1", R1_BACKUP)],
        )

        apply_fail = {"device": "R1", "success": False, "output": "", "error": "Authentication failed"}
        rollback_ok = {"device": "R1", "success": True, "output": "", "error": ""}
        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.apply_config", return_value=apply_fail), \
             patch("agentic_ni.tools.pyats_tools.rollback_config", return_value=rollback_ok):
            result = live_apply_node(state)

        assert result.get("error_log")
        assert "R1" in result["error_log"]

    def test_rollback_failure_is_recorded(self, tmp_path):
        from agentic_ni.graph import live_apply_node

        inv_path = _write_inventory(tmp_path)
        state = _make_state(
            live_inventory_path=inv_path,
            live_apply_records=[_make_precheck_record("R1", "192.168.100.1", R1_BACKUP)],
        )

        apply_fail = {"device": "R1", "success": False, "output": "", "error": "Timeout"}
        rollback_fail = {"device": "R1", "success": False, "output": "", "error": "Rollback failed"}
        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.apply_config", return_value=apply_fail), \
             patch("agentic_ni.tools.pyats_tools.rollback_config", return_value=rollback_fail):
            result = live_apply_node(state)

        r1_rec = result["live_apply_records"][0]
        assert r1_rec["rollback_done"] is False
        assert "Rollback failed" in r1_rec["rollback_error"]

    def test_rollback_skipped_when_no_backup(self, tmp_path):
        """バックアップがない場合はロールバックをスキップする。"""
        from agentic_ni.graph import live_apply_node

        inv_path = _write_inventory(tmp_path)
        # backup_config = "" のレコード
        state = _make_state(
            live_inventory_path=inv_path,
            live_apply_records=[
                _make_precheck_record("R1", "192.168.100.1", backup_config=""),
            ],
        )

        apply_fail = {"device": "R1", "success": False, "output": "", "error": "Timeout"}
        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.apply_config", return_value=apply_fail), \
             patch("agentic_ni.tools.pyats_tools.rollback_config") as mock_rb:
            live_apply_node(state)

        mock_rb.assert_not_called()

    def test_partial_failure_only_failed_devices_rollback(self, tmp_path):
        """R1 成功・R2 失敗の場合、R2 だけロールバックされること。"""
        from agentic_ni.graph import live_apply_node

        inv_path = _write_inventory(tmp_path)
        state = _make_state(
            live_inventory_path=inv_path,
            live_apply_records=[
                _make_precheck_record("R1", "192.168.100.1", R1_BACKUP),
                _make_precheck_record("R2", "192.168.100.2", R2_BACKUP),
            ],
        )

        def apply_side_effect(device_name, cfg, config_text):
            if device_name == "R1":
                return {"device": "R1", "success": True, "output": "end\n", "error": ""}
            return {"device": "R2", "success": False, "output": "", "error": "Auth failed"}

        rollback_ok = {"device": "R2", "success": True, "output": "end\n", "error": ""}
        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.apply_config", side_effect=apply_side_effect), \
             patch("agentic_ni.tools.pyats_tools.rollback_config", return_value=rollback_ok) as mock_rb:
            result = live_apply_node(state)

        # ロールバックは R2 のみ
        mock_rb.assert_called_once()
        rb_call_args = mock_rb.call_args[0]
        assert rb_call_args[0] == "R2"

        r1_rec = next(r for r in result["live_apply_records"] if r["device"] == "R1")
        r2_rec = next(r for r in result["live_apply_records"] if r["device"] == "R2")
        assert r1_rec["apply_success"] is True
        assert r1_rec["rollback_done"] is False
        assert r2_rec["apply_success"] is False
        assert r2_rec["rollback_done"] is True


# ---------------------------------------------------------------------------
# live_apply_node — エラーケース
# ---------------------------------------------------------------------------


class TestLiveApplyNodeEdgeCases:
    def test_inventory_not_found_returns_error(self, tmp_path, monkeypatch):
        from agentic_ni.graph import live_apply_node

        monkeypatch.chdir(tmp_path)
        state = _make_state(live_inventory_path="")
        result = live_apply_node(state)

        assert result.get("error_log")

    def test_device_missing_from_inventory_is_skipped(self, tmp_path):
        """インベントリに存在しないデバイスはスキップされること。"""
        from agentic_ni.graph import live_apply_node

        inv_path = _write_inventory(tmp_path)
        state = _make_state(
            live_inventory_path=inv_path,
            live_apply_records=[
                _make_precheck_record("R1", "192.168.100.1", R1_BACKUP),
                _make_precheck_record("R99", "192.168.100.99", "hostname R99\n"),  # インベントリにない
            ],
        )

        apply_ok = {"device": "R1", "success": True, "output": "end\n", "error": ""}
        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.apply_config", return_value=apply_ok):
            result = live_apply_node(state)

        # R99 はスキップされたが R1 は投入されている
        records = {r["device"]: r for r in result["live_apply_records"]}
        assert records["R1"]["apply_success"] is True
        assert records["R99"]["applied_config"] == ""  # スキップ = 未投入

    def test_empty_config_device_is_skipped(self, tmp_path):
        """コンフィグが空のデバイスはスキップされること。"""
        from agentic_ni.graph import live_apply_node

        inv_path = _write_inventory(tmp_path)
        state = _make_state(
            live_inventory_path=inv_path,
            device_configs={"R1": "", "R2": R2_NEW_CONFIG},
            live_apply_records=[
                _make_precheck_record("R1", "192.168.100.1", R1_BACKUP),
                _make_precheck_record("R2", "192.168.100.2", R2_BACKUP),
            ],
        )

        apply_ok = {"device": "R2", "success": True, "output": "end\n", "error": ""}

        call_count = {"n": 0}
        def apply_side(device_name, cfg, config_text):
            call_count["n"] += 1
            return apply_ok

        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.apply_config", side_effect=apply_side):
            live_apply_node(state)

        # R1 はスキップされ R2 のみ呼ばれる
        assert call_count["n"] == 1


# ---------------------------------------------------------------------------
# live_rollback_node — 正常系
# ---------------------------------------------------------------------------


class TestLiveRollbackNodeSuccess:
    def _run(self, tmp_path):
        from agentic_ni.graph import live_rollback_node

        inv_path = _write_inventory(tmp_path)
        state = _make_state(
            live_inventory_path=inv_path,
            live_human_decision="rollback-only",
            live_apply_records=[
                _make_precheck_record("R1", "192.168.100.1", R1_BACKUP),
                _make_precheck_record("R2", "192.168.100.2", R2_BACKUP),
            ],
        )

        rb_ok = {"device": "R1", "success": True, "output": "end\n", "error": ""}
        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.rollback_config", return_value=rb_ok):
            return live_rollback_node(state)

    def test_returns_live_apply_records(self, tmp_path):
        result = self._run(tmp_path)
        assert len(result["live_apply_records"]) == 2

    def test_rollback_done_is_true(self, tmp_path):
        result = self._run(tmp_path)
        for rec in result["live_apply_records"]:
            assert rec["rollback_done"] is True

    def test_error_log_empty_on_success(self, tmp_path):
        result = self._run(tmp_path)
        assert result.get("error_log", "") == ""

    def test_rollback_uses_backup_config(self, tmp_path):
        from agentic_ni.graph import live_rollback_node

        inv_path = _write_inventory(tmp_path)
        state = _make_state(
            live_inventory_path=inv_path,
            live_apply_records=[
                _make_precheck_record("R1", "192.168.100.1", R1_BACKUP),
            ],
        )

        rb_ok = {"device": "R1", "success": True, "output": "end\n", "error": ""}
        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.rollback_config", return_value=rb_ok) as mock_rb:
            live_rollback_node(state)

        # rollback_config に渡した backup_config が正しいことを確認
        call_args = mock_rb.call_args[0]
        assert call_args[0] == "R1"        # device_name
        assert call_args[2] == R1_BACKUP   # backup_config


# ---------------------------------------------------------------------------
# live_rollback_node — 失敗ケース
# ---------------------------------------------------------------------------


class TestLiveRollbackNodeFailure:
    def test_error_log_set_on_rollback_failure(self, tmp_path):
        from agentic_ni.graph import live_rollback_node

        inv_path = _write_inventory(tmp_path)
        state = _make_state(
            live_inventory_path=inv_path,
            live_apply_records=[_make_precheck_record("R1", "192.168.100.1", R1_BACKUP)],
        )

        rb_fail = {"device": "R1", "success": False, "output": "", "error": "SSH Timeout"}
        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.rollback_config", return_value=rb_fail):
            result = live_rollback_node(state)

        assert result.get("error_log")
        assert "R1" in result["error_log"]

    def test_empty_backup_is_skipped(self, tmp_path):
        from agentic_ni.graph import live_rollback_node

        inv_path = _write_inventory(tmp_path)
        state = _make_state(
            live_inventory_path=inv_path,
            live_apply_records=[_make_precheck_record("R1", "192.168.100.1", backup_config="")],
        )

        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.rollback_config") as mock_rb:
            result = live_rollback_node(state)

        mock_rb.assert_not_called()

    def test_inventory_not_found_returns_error(self, tmp_path, monkeypatch):
        from agentic_ni.graph import live_rollback_node

        monkeypatch.chdir(tmp_path)
        state = _make_state(live_inventory_path="")
        result = live_rollback_node(state)

        assert result.get("error_log")


# ---------------------------------------------------------------------------
# compile_graph_apply_to_live のテスト
# ---------------------------------------------------------------------------


class TestCompileGraphApplyToLive:
    def test_graph_compiles_successfully(self):
        from agentic_ni.graph import compile_graph_apply_to_live

        app = compile_graph_apply_to_live()
        assert app is not None

    def test_full_flow_yes_all_success(self, tmp_path):
        """precheck → confirm(yes) → apply(全成功) の E2E テスト。"""
        from langgraph.types import Command
        from agentic_ni.graph import compile_graph_apply_to_live, initial_state_apply_to_live

        inv_path = _write_inventory(tmp_path)
        state = initial_state_apply_to_live(
            requirement="テスト要件",
            lab_id="lab-001",
            inventory_path=inv_path,
        )
        state["device_configs"] = {"R1": R1_NEW_CONFIG, "R2": R2_NEW_CONFIG}
        state["test_results"] = [{"test": "ospf", "result": "PASS", "detail": "OK"}]

        apply_ok = {"device": "", "success": True, "output": "end\n", "error": ""}
        app = compile_graph_apply_to_live()
        thread = {"configurable": {"thread_id": "e2e-yes-01"}}

        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.check_connectivity", return_value={"R1": True, "R2": True}), \
             patch("agentic_ni.tools.pyats_tools.backup_running_config", return_value={"R1": R1_BACKUP, "R2": R2_BACKUP}), \
             patch("agentic_ni.tools.pyats_tools.apply_config", return_value=apply_ok):
            app.invoke(state, thread)  # precheck → interrupt
            result = app.invoke(Command(resume={"decision": "yes"}), thread)

        assert result["live_human_decision"] == "yes"
        assert result["error_log"] == ""
        applied = [r for r in result["live_apply_records"] if r["apply_success"]]
        assert len(applied) == 2

    def test_full_flow_yes_with_failure_and_rollback(self, tmp_path):
        """precheck → confirm(yes) → apply(R2 失敗 → 自動ロールバック) の E2E テスト。"""
        from langgraph.types import Command
        from agentic_ni.graph import compile_graph_apply_to_live, initial_state_apply_to_live

        inv_path = _write_inventory(tmp_path)
        state = initial_state_apply_to_live(
            requirement="テスト要件",
            inventory_path=inv_path,
        )
        state["device_configs"] = {"R1": R1_NEW_CONFIG, "R2": R2_NEW_CONFIG}

        def apply_side(device_name, cfg, config_text):
            if device_name == "R1":
                return {"device": "R1", "success": True, "output": "end\n", "error": ""}
            return {"device": "R2", "success": False, "output": "", "error": "Auth failed"}

        rb_ok = {"device": "R2", "success": True, "output": "end\n", "error": ""}
        app = compile_graph_apply_to_live()
        thread = {"configurable": {"thread_id": "e2e-fail-01"}}

        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.check_connectivity", return_value={"R1": True, "R2": True}), \
             patch("agentic_ni.tools.pyats_tools.backup_running_config", return_value={"R1": R1_BACKUP, "R2": R2_BACKUP}), \
             patch("agentic_ni.tools.pyats_tools.apply_config", side_effect=apply_side), \
             patch("agentic_ni.tools.pyats_tools.rollback_config", return_value=rb_ok):
            app.invoke(state, thread)
            result = app.invoke(Command(resume={"decision": "yes"}), thread)

        records = {r["device"]: r for r in result["live_apply_records"]}
        assert records["R1"]["apply_success"] is True
        assert records["R2"]["apply_success"] is False
        assert records["R2"]["rollback_done"] is True
        assert result["error_log"]  # エラーログあり

    def test_full_flow_rollback_only(self, tmp_path):
        """precheck → confirm(rollback-only) → live_rollback の E2E テスト。"""
        from langgraph.types import Command
        from agentic_ni.graph import compile_graph_apply_to_live, initial_state_apply_to_live

        inv_path = _write_inventory(tmp_path)
        state = initial_state_apply_to_live(
            requirement="テスト要件",
            inventory_path=inv_path,
        )

        rb_ok = {"device": "", "success": True, "output": "end\n", "error": ""}
        app = compile_graph_apply_to_live()
        thread = {"configurable": {"thread_id": "e2e-rollback-01"}}

        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.check_connectivity", return_value={"R1": True, "R2": True}), \
             patch("agentic_ni.tools.pyats_tools.backup_running_config", return_value={"R1": R1_BACKUP, "R2": R2_BACKUP}), \
             patch("agentic_ni.tools.pyats_tools.rollback_config", return_value=rb_ok):
            app.invoke(state, thread)
            result = app.invoke(Command(resume={"decision": "rollback-only"}), thread)

        assert result["live_human_decision"] == "rollback-only"
        rolled = [r for r in result["live_apply_records"] if r["rollback_done"]]
        assert len(rolled) == 2

    def test_full_flow_cancelled(self, tmp_path):
        """precheck → confirm(no) → END（取り消し）の E2E テスト。"""
        from langgraph.types import Command
        from agentic_ni.graph import compile_graph_apply_to_live, initial_state_apply_to_live

        inv_path = _write_inventory(tmp_path)
        state = initial_state_apply_to_live(
            requirement="テスト要件",
            inventory_path=inv_path,
        )

        app = compile_graph_apply_to_live()
        thread = {"configurable": {"thread_id": "e2e-cancel-01"}}

        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.check_connectivity", return_value={"R1": True, "R2": True}), \
             patch("agentic_ni.tools.pyats_tools.backup_running_config", return_value={"R1": R1_BACKUP, "R2": R2_BACKUP}):
            app.invoke(state, thread)
            result = app.invoke(Command(resume={"decision": "no", "reason": "変更凍結"}), thread)

        assert result["live_human_decision"] == "no"
        assert "取り消し" in result["final_report"]
        # apply_config / rollback_config は一切呼ばれない
