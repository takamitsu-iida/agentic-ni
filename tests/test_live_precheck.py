"""live_precheck_node のユニットテスト。実機は不要（すべてモック）。"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# ヘルパー関数
# ---------------------------------------------------------------------------


def _make_state(**overrides) -> dict:
    """最小限の AgentState 風辞書を返す。"""
    base = {
        "requirement": "R1 と R2 を OSPF で接続する",
        "prompt_set": "demo",
        "fault_simulation_enabled": False,
        "skip_deploy": False,
        "error_history": [],
        "topology_yaml": "",
        "device_configs": {"R1": "hostname R1\n", "R2": "hostname R2\n"},
        "lab_id": "lab-test-001",
        "test_results": [
            {"test": "ospf_neighbor", "result": "PASS", "detail": "OK"},
            {"test": "ping_2.2.2.2", "result": "PASS", "detail": "OK"},
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
        "live_apply_records": [],
        "live_verify_enabled": False,
        "live_report": "",
        "final_report": "# 検証成功レポート\n\n全テスト PASS。",
    }
    base.update(overrides)
    return base


def _write_inventory(tmp_path: Path, content: str) -> str:
    inv_file = tmp_path / "demo.yaml"
    inv_file.write_text(content, encoding="utf-8")
    return str(inv_file)


VALID_INVENTORY_YAML = """\
metadata:
  description: "テスト用インベントリ"

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

R1_CONFIG = "hostname R1\ninterface GigabitEthernet0/0\n ip address 10.0.12.1 255.255.255.252\n"
R2_CONFIG = "hostname R2\ninterface GigabitEthernet0/0\n ip address 10.0.12.2 255.255.255.252\n"


# ---------------------------------------------------------------------------
# _resolve_inventory_path のテスト
# ---------------------------------------------------------------------------


class TestResolveInventoryPath:
    def test_uses_explicit_path(self, tmp_path):
        from agentic_ni.graph import _resolve_inventory_path

        inv_path = tmp_path / "custom.yaml"
        inv_path.write_text("devices: {}", encoding="utf-8")
        state = _make_state(live_inventory_path=str(inv_path))
        assert _resolve_inventory_path(state) == str(inv_path)

    def test_auto_resolves_inventory_dir(self, tmp_path, monkeypatch):
        from agentic_ni.graph import _resolve_inventory_path

        # inventory/demo.yaml を作成して CWD をそのディレクトリにする
        inv_dir = tmp_path / "inventory"
        inv_dir.mkdir()
        (inv_dir / "demo.yaml").write_text("devices: {}", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        state = _make_state(live_inventory_path="", prompt_set="demo")
        path = _resolve_inventory_path(state)
        assert path.endswith("demo.yaml")

    def test_raises_when_no_inventory_found(self, tmp_path, monkeypatch):
        from agentic_ni.graph import _resolve_inventory_path

        monkeypatch.chdir(tmp_path)  # inventory/ ディレクトリが存在しない
        state = _make_state(live_inventory_path="")
        with pytest.raises(FileNotFoundError, match="インベントリファイルが見つかりません"):
            _resolve_inventory_path(state)


# ---------------------------------------------------------------------------
# live_precheck_node — 正常系
# ---------------------------------------------------------------------------


class TestLivePrecheckNodeSuccess:
    def _run_precheck(self, tmp_path) -> dict:
        """モックを使った正常系 precheck の実行ヘルパー。"""
        from agentic_ni.graph import live_precheck_node

        inv_path = _write_inventory(tmp_path, VALID_INVENTORY_YAML)
        state = _make_state(live_inventory_path=inv_path)

        # netmiko_tools の関数をすべてモック
        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.check_connectivity", return_value={"R1": True, "R2": True}), \
             patch("agentic_ni.tools.pyats_tools.backup_running_config", return_value={"R1": R1_CONFIG, "R2": R2_CONFIG}):
            return live_precheck_node(state)

    def test_returns_live_apply_records(self, tmp_path):
        result = self._run_precheck(tmp_path)
        assert "live_apply_records" in result
        assert len(result["live_apply_records"]) == 2

    def test_records_have_correct_device_names(self, tmp_path):
        result = self._run_precheck(tmp_path)
        device_names = {r["device"] for r in result["live_apply_records"]}
        assert device_names == {"R1", "R2"}

    def test_connectivity_ok_is_true(self, tmp_path):
        result = self._run_precheck(tmp_path)
        for rec in result["live_apply_records"]:
            assert rec["connectivity_ok"] is True

    def test_backup_config_is_stored(self, tmp_path):
        result = self._run_precheck(tmp_path)
        r1_rec = next(r for r in result["live_apply_records"] if r["device"] == "R1")
        assert r1_rec["backup_config"] == R1_CONFIG

    def test_backup_lines_is_counted(self, tmp_path):
        result = self._run_precheck(tmp_path)
        r1_rec = next(r for r in result["live_apply_records"] if r["device"] == "R1")
        # R1_CONFIG の非空行数を期待
        expected_lines = len([l for l in R1_CONFIG.splitlines() if l.strip()])
        assert r1_rec["backup_lines"] == expected_lines

    def test_error_log_is_empty_on_success(self, tmp_path):
        result = self._run_precheck(tmp_path)
        assert result.get("error_log", "") == ""

    def test_apply_mode_is_preserved(self, tmp_path):
        result = self._run_precheck(tmp_path)
        r1_rec = next(r for r in result["live_apply_records"] if r["device"] == "R1")
        assert r1_rec["apply_mode"] == "config_merge"

    def test_host_is_set(self, tmp_path):
        result = self._run_precheck(tmp_path)
        r1_rec = next(r for r in result["live_apply_records"] if r["device"] == "R1")
        assert r1_rec["host"] == "192.168.100.1"

    def test_apply_fields_are_empty_after_precheck(self, tmp_path):
        """precheck 後は apply / rollback フィールドは未設定（空）であること。"""
        result = self._run_precheck(tmp_path)
        for rec in result["live_apply_records"]:
            assert rec["applied_config"] == ""
            assert rec["apply_success"] is False
            assert rec["apply_output"] == ""
            assert rec["apply_error"] == ""
            assert rec["rollback_done"] is False
            assert rec["rollback_error"] == ""


# ---------------------------------------------------------------------------
# live_precheck_node — Level 1 失敗（インベントリ読み込みエラー）
# ---------------------------------------------------------------------------


class TestLivePrecheckNodeLevel1Failure:
    def test_returns_error_log_on_file_not_found(self, tmp_path, monkeypatch):
        from agentic_ni.graph import live_precheck_node

        monkeypatch.chdir(tmp_path)  # インベントリが存在しない
        state = _make_state(live_inventory_path="")
        result = live_precheck_node(state)

        assert "error_log" in result
        assert result["error_log"]  # 空でない
        assert "live_apply_records" in result
        assert result["live_apply_records"] == []

    def test_returns_error_log_on_invalid_inventory(self, tmp_path):
        from agentic_ni.graph import live_precheck_node

        inv_path = str(tmp_path / "bad.yaml")
        Path(inv_path).write_text("not_devices_key: {}", encoding="utf-8")
        state = _make_state(live_inventory_path=inv_path)

        with patch("agentic_ni.tools.pyats_tools.load_inventory", side_effect=ValueError("形式が不正")):
            result = live_precheck_node(state)

        assert result.get("error_log")
        assert "final_report" in result
        assert "プレチェック失敗" in result["final_report"]


# ---------------------------------------------------------------------------
# live_precheck_node — Level 2 失敗（SSH 疎通確認エラー）
# ---------------------------------------------------------------------------


class TestLivePrecheckNodeLevel2Failure:
    def test_returns_error_log_when_device_unreachable(self, tmp_path):
        from agentic_ni.graph import live_precheck_node

        inv_path = _write_inventory(tmp_path, VALID_INVENTORY_YAML)
        state = _make_state(live_inventory_path=inv_path)

        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.check_connectivity", return_value={"R1": True, "R2": False}):
            result = live_precheck_node(state)

        assert result.get("error_log")
        assert "R2" in result["error_log"]

    def test_records_are_populated_with_connectivity_status(self, tmp_path):
        from agentic_ni.graph import live_precheck_node

        inv_path = _write_inventory(tmp_path, VALID_INVENTORY_YAML)
        state = _make_state(live_inventory_path=inv_path)

        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.check_connectivity", return_value={"R1": False, "R2": False}):
            result = live_precheck_node(state)

        records = result.get("live_apply_records", [])
        assert len(records) == 2
        for rec in records:
            assert rec["connectivity_ok"] is False

    def test_final_report_contains_error_message(self, tmp_path):
        from agentic_ni.graph import live_precheck_node

        inv_path = _write_inventory(tmp_path, VALID_INVENTORY_YAML)
        state = _make_state(live_inventory_path=inv_path)

        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.check_connectivity", return_value={"R1": False, "R2": True}):
            result = live_precheck_node(state)

        assert "プレチェック失敗" in result.get("final_report", "")


# ---------------------------------------------------------------------------
# live_precheck_node — Level 3 失敗（バックアップ取得エラー）
# ---------------------------------------------------------------------------


class TestLivePrecheckNodeLevel3Failure:
    def test_returns_error_log_when_backup_fails(self, tmp_path):
        from agentic_ni.graph import live_precheck_node

        inv_path = _write_inventory(tmp_path, VALID_INVENTORY_YAML)
        state = _make_state(live_inventory_path=inv_path)

        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.check_connectivity", return_value={"R1": True, "R2": True}), \
             patch("agentic_ni.tools.pyats_tools.backup_running_config", side_effect=RuntimeError("バックアップに失敗")):
            result = live_precheck_node(state)

        assert result.get("error_log")
        assert "バックアップに失敗" in result["error_log"]

    def test_records_created_with_empty_backup_on_failure(self, tmp_path):
        from agentic_ni.graph import live_precheck_node

        inv_path = _write_inventory(tmp_path, VALID_INVENTORY_YAML)
        state = _make_state(live_inventory_path=inv_path)

        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.check_connectivity", return_value={"R1": True, "R2": True}), \
             patch("agentic_ni.tools.pyats_tools.backup_running_config", side_effect=RuntimeError("失敗")):
            result = live_precheck_node(state)

        records = result.get("live_apply_records", [])
        assert len(records) == 2
        for rec in records:
            assert rec["backup_config"] == ""
            assert rec["backup_lines"] == 0


# ---------------------------------------------------------------------------
# initial_state_apply_to_live のテスト
# ---------------------------------------------------------------------------


class TestInitialStateApplyToLive:
    def test_sets_live_inventory_path(self):
        from agentic_ni.graph import initial_state_apply_to_live

        state = initial_state_apply_to_live(
            requirement="テスト要件",
            inventory_path="/tmp/test.yaml",
        )
        assert state["live_inventory_path"] == "/tmp/test.yaml"

    def test_default_inventory_path_is_empty(self):
        from agentic_ni.graph import initial_state_apply_to_live

        state = initial_state_apply_to_live(requirement="テスト要件")
        assert state["live_inventory_path"] == ""

    def test_live_verify_enabled_default_false(self):
        from agentic_ni.graph import initial_state_apply_to_live

        state = initial_state_apply_to_live(requirement="テスト要件")
        assert state["live_verify_enabled"] is False

    def test_live_verify_enabled_can_be_set(self):
        from agentic_ni.graph import initial_state_apply_to_live

        state = initial_state_apply_to_live(requirement="テスト要件", live_verify_enabled=True)
        assert state["live_verify_enabled"] is True

    def test_live_apply_records_starts_empty(self):
        from agentic_ni.graph import initial_state_apply_to_live

        state = initial_state_apply_to_live(requirement="テスト要件")
        assert state["live_apply_records"] == []

    def test_live_report_starts_empty(self):
        from agentic_ni.graph import initial_state_apply_to_live

        state = initial_state_apply_to_live(requirement="テスト要件")
        assert state["live_report"] == ""

    def test_lab_id_is_set(self):
        from agentic_ni.graph import initial_state_apply_to_live

        state = initial_state_apply_to_live(requirement="テスト要件", lab_id="lab-abc-001")
        assert state["lab_id"] == "lab-abc-001"


# ---------------------------------------------------------------------------
# compile_graph_live_precheck のテスト
# ---------------------------------------------------------------------------


class TestCompileGraphLivePrecheck:
    def test_graph_compiles_successfully(self):
        from agentic_ni.graph import compile_graph_live_precheck

        app = compile_graph_live_precheck()
        assert app is not None

    def test_graph_runs_precheck_node(self, tmp_path):
        from agentic_ni.graph import compile_graph_live_precheck, initial_state_apply_to_live

        inv_path = _write_inventory(tmp_path, VALID_INVENTORY_YAML)
        state = initial_state_apply_to_live(
            requirement="テスト要件",
            lab_id="lab-001",
            inventory_path=inv_path,
        )

        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.check_connectivity", return_value={"R1": True, "R2": True}), \
             patch("agentic_ni.tools.pyats_tools.backup_running_config", return_value={"R1": R1_CONFIG, "R2": R2_CONFIG}):
            app = compile_graph_live_precheck()
            result = app.invoke(state)

        assert len(result["live_apply_records"]) == 2
        assert result["error_log"] == ""

    def test_graph_sets_error_on_precheck_failure(self, tmp_path, monkeypatch):
        from agentic_ni.graph import compile_graph_live_precheck, initial_state_apply_to_live

        monkeypatch.chdir(tmp_path)
        state = initial_state_apply_to_live(requirement="テスト要件")

        app = compile_graph_live_precheck()
        result = app.invoke(state)

        assert result.get("error_log")
