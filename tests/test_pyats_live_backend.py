"""pyATS バックエンド Live 操作関数のユニットテスト（Netmiko 不使用）。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------


def _make_mock_device(running_config: str = "hostname R1\n") -> MagicMock:
    dev = MagicMock()
    dev.execute.return_value = running_config
    dev.configure.return_value = "end\n"
    dev.connect.return_value = None
    dev.disconnect.return_value = None
    return dev


def _make_mock_testbed(device_name: str = "R1", running_config: str = "hostname R1\n") -> MagicMock:
    dev = _make_mock_device(running_config)
    tb = MagicMock()
    tb.devices = {device_name: dev}
    return tb


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
R1_NEW = "hostname R1\nrouter ospf 1\n network 0.0.0.0 255.255.255.255 area 0\n"


# ---------------------------------------------------------------------------
# load_inventory のテスト
# ---------------------------------------------------------------------------


class TestLoadInventory:
    def test_loads_valid_inventory(self, tmp_path):
        from agentic_ni.tools.pyats_tools import load_inventory

        content = """\
devices:
  R1:
    host: "192.168.100.1"
    device_type: "cisco_ios"
    username: "admin"
    password: "password"
"""
        f = tmp_path / "demo.yaml"
        f.write_text(content)
        devices = load_inventory(str(f))
        assert "R1" in devices
        assert devices["R1"]["host"] == "192.168.100.1"

    def test_raises_file_not_found(self):
        from agentic_ni.tools.pyats_tools import load_inventory

        with pytest.raises(FileNotFoundError):
            load_inventory("/nonexistent/inventory.yaml")

    def test_expands_env_vars(self, tmp_path, monkeypatch):
        from agentic_ni.tools.pyats_tools import load_inventory

        monkeypatch.setenv("TEST_USER", "netadmin")
        content = """\
devices:
  R1:
    host: "10.0.0.1"
    device_type: "cisco_ios"
    username: "${TEST_USER}"
    password: "pass"
"""
        f = tmp_path / "demo.yaml"
        f.write_text(content)
        devices = load_inventory(str(f))
        assert devices["R1"]["username"] == "netadmin"

    def test_default_apply_mode(self, tmp_path):
        from agentic_ni.tools.pyats_tools import load_inventory

        content = """\
devices:
  R1:
    host: "10.0.0.1"
    device_type: "cisco_ios"
    username: "admin"
    password: "pass"
"""
        f = tmp_path / "demo.yaml"
        f.write_text(content)
        devices = load_inventory(str(f))
        assert devices["R1"]["apply_mode"] == "config_merge"
        assert devices["R1"]["port"] == 22

    def test_raises_value_error_missing_key(self, tmp_path):
        from agentic_ni.tools.pyats_tools import load_inventory

        content = """\
devices:
  R1:
    host: "10.0.0.1"
    device_type: "cisco_ios"
    username: "admin"
"""
        f = tmp_path / "demo.yaml"
        f.write_text(content)
        with pytest.raises(ValueError, match="password"):
            load_inventory(str(f))


# ---------------------------------------------------------------------------
# _compute_config_diff のテスト
# ---------------------------------------------------------------------------


class TestComputeConfigDiff:
    def test_returns_new_lines(self):
        from agentic_ni.tools.pyats_tools import _compute_config_diff

        current = "hostname R1\ninterface Gi0/0\n"
        target = "hostname R1\ninterface Gi0/0\nrouter ospf 1\n"
        diff = _compute_config_diff(current, target)
        assert "router ospf 1" in diff

    def test_empty_diff_when_no_change(self):
        from agentic_ni.tools.pyats_tools import _compute_config_diff

        config = "hostname R1\n"
        assert _compute_config_diff(config, config) == []

    def test_ignores_blank_lines(self):
        from agentic_ni.tools.pyats_tools import _compute_config_diff

        current = "hostname R1\n"
        assert _compute_config_diff(current, "\n\nhostname R1\n\n") == []


# ---------------------------------------------------------------------------
# check_connectivity のテスト
# ---------------------------------------------------------------------------


class TestCheckConnectivity:
    def test_returns_true_on_success(self):
        from agentic_ni.tools.pyats_tools import check_connectivity

        mock_tb = _make_mock_testbed("R1")
        with patch("agentic_ni.tools.pyats_tools._load_testbed", return_value=mock_tb):
            result = check_connectivity({"R1": SAMPLE_DEVICES["R1"]})

        assert result["R1"] is True
        mock_tb.devices["R1"].connect.assert_called_once()

    def test_returns_false_on_failure(self):
        from agentic_ni.tools.pyats_tools import check_connectivity

        mock_tb = _make_mock_testbed("R1")
        mock_tb.devices["R1"].connect.side_effect = Exception("refused")
        with patch("agentic_ni.tools.pyats_tools._load_testbed", return_value=mock_tb):
            result = check_connectivity({"R1": SAMPLE_DEVICES["R1"]})

        assert result["R1"] is False

    def test_empty_devices(self):
        from agentic_ni.tools.pyats_tools import check_connectivity

        assert check_connectivity({}) == {}


# ---------------------------------------------------------------------------
# backup_running_config のテスト
# ---------------------------------------------------------------------------


class TestBackupRunningConfig:
    def test_returns_config(self):
        from agentic_ni.tools.pyats_tools import backup_running_config

        mock_tb = _make_mock_testbed("R1", running_config=R1_BACKUP)
        with patch("agentic_ni.tools.pyats_tools._load_testbed", return_value=mock_tb), \
             patch("agentic_ni.tools.pyats_tools._connect_device", return_value=mock_tb.devices["R1"]):
            backups = backup_running_config({"R1": SAMPLE_DEVICES["R1"]})

        assert backups["R1"] == R1_BACKUP
        mock_tb.devices["R1"].execute.assert_called_once_with("show running-config")

    def test_raises_on_failure(self):
        from agentic_ni.tools.pyats_tools import backup_running_config

        mock_tb = _make_mock_testbed("R1")
        mock_tb.devices["R1"].execute.side_effect = Exception("SSH Error")
        with patch("agentic_ni.tools.pyats_tools._load_testbed", return_value=mock_tb), \
             patch("agentic_ni.tools.pyats_tools._connect_device", return_value=mock_tb.devices["R1"]), \
             pytest.raises(RuntimeError, match="バックアップに失敗"):
            backup_running_config({"R1": SAMPLE_DEVICES["R1"]})

    def test_disconnects_after_success(self):
        from agentic_ni.tools.pyats_tools import backup_running_config

        mock_tb = _make_mock_testbed("R1", running_config=R1_BACKUP)
        dev = mock_tb.devices["R1"]
        with patch("agentic_ni.tools.pyats_tools._load_testbed", return_value=mock_tb), \
             patch("agentic_ni.tools.pyats_tools._connect_device", return_value=dev):
            backup_running_config({"R1": SAMPLE_DEVICES["R1"]})

        dev.disconnect.assert_called_once()


# ---------------------------------------------------------------------------
# apply_config のテスト
# ---------------------------------------------------------------------------


class TestApplyConfig:
    def test_config_merge_success(self):
        from agentic_ni.tools.pyats_tools import apply_config

        mock_tb = _make_mock_testbed("R1")
        dev = mock_tb.devices["R1"]
        with patch("agentic_ni.tools.pyats_tools._load_testbed", return_value=mock_tb), \
             patch("agentic_ni.tools.pyats_tools._connect_device", return_value=dev):
            result = apply_config("R1", SAMPLE_DEVICES["R1"], R1_NEW)

        assert result["success"] is True
        dev.configure.assert_called_once()

    def test_incremental_with_diff(self):
        from agentic_ni.tools.pyats_tools import apply_config

        current_config = "hostname R1\ninterface Gi0/0\n"
        target_config = "hostname R1\ninterface Gi0/0\nrouter ospf 1\n"
        mock_tb = _make_mock_testbed("R1", running_config=current_config)
        dev = mock_tb.devices["R1"]
        cfg = {**SAMPLE_DEVICES["R1"], "apply_mode": "incremental"}

        with patch("agentic_ni.tools.pyats_tools._load_testbed", return_value=mock_tb), \
             patch("agentic_ni.tools.pyats_tools._connect_device", return_value=dev):
            result = apply_config("R1", cfg, target_config)

        assert result["success"] is True
        configure_call_arg = dev.configure.call_args[0][0]
        assert "router ospf 1" in configure_call_arg
        assert "hostname R1" not in configure_call_arg

    def test_incremental_no_diff(self):
        from agentic_ni.tools.pyats_tools import apply_config

        config = "hostname R1\ninterface Gi0/0\n"
        mock_tb = _make_mock_testbed("R1", running_config=config)
        dev = mock_tb.devices["R1"]
        cfg = {**SAMPLE_DEVICES["R1"], "apply_mode": "incremental"}

        with patch("agentic_ni.tools.pyats_tools._load_testbed", return_value=mock_tb), \
             patch("agentic_ni.tools.pyats_tools._connect_device", return_value=dev):
            result = apply_config("R1", cfg, config)

        assert "変更なし" in result["output"]
        dev.configure.assert_not_called()

    def test_returns_error_on_failure(self):
        from agentic_ni.tools.pyats_tools import apply_config

        mock_tb = _make_mock_testbed("R1")
        dev = mock_tb.devices["R1"]
        dev.configure.side_effect = Exception("Auth failed")

        with patch("agentic_ni.tools.pyats_tools._load_testbed", return_value=mock_tb), \
             patch("agentic_ni.tools.pyats_tools._connect_device", return_value=dev):
            result = apply_config("R1", SAMPLE_DEVICES["R1"], R1_NEW)

        assert result["success"] is False
        assert "Auth failed" in result["error"]

    def test_disconnects_after_apply(self):
        from agentic_ni.tools.pyats_tools import apply_config

        mock_tb = _make_mock_testbed("R1")
        dev = mock_tb.devices["R1"]
        with patch("agentic_ni.tools.pyats_tools._load_testbed", return_value=mock_tb), \
             patch("agentic_ni.tools.pyats_tools._connect_device", return_value=dev):
            apply_config("R1", SAMPLE_DEVICES["R1"], R1_NEW)

        dev.disconnect.assert_called_once()


# ---------------------------------------------------------------------------
# rollback_config のテスト
# ---------------------------------------------------------------------------


class TestRollbackConfig:
    def test_rollback_success(self):
        from agentic_ni.tools.pyats_tools import rollback_config

        mock_tb = _make_mock_testbed("R1")
        dev = mock_tb.devices["R1"]
        with patch("agentic_ni.tools.pyats_tools._load_testbed", return_value=mock_tb), \
             patch("agentic_ni.tools.pyats_tools._connect_device", return_value=dev):
            result = rollback_config("R1", SAMPLE_DEVICES["R1"], R1_BACKUP)

        assert result["success"] is True
        dev.configure.assert_called_once()

    def test_rollback_uses_backup_content(self):
        from agentic_ni.tools.pyats_tools import rollback_config

        mock_tb = _make_mock_testbed("R1")
        dev = mock_tb.devices["R1"]
        with patch("agentic_ni.tools.pyats_tools._load_testbed", return_value=mock_tb), \
             patch("agentic_ni.tools.pyats_tools._connect_device", return_value=dev):
            rollback_config("R1", SAMPLE_DEVICES["R1"], R1_BACKUP)

        configure_call_arg = dev.configure.call_args[0][0]
        assert "hostname R1" in configure_call_arg

    def test_returns_error_on_failure(self):
        from agentic_ni.tools.pyats_tools import rollback_config

        mock_tb = _make_mock_testbed("R1")
        dev = mock_tb.devices["R1"]
        dev.configure.side_effect = Exception("Connection lost")

        with patch("agentic_ni.tools.pyats_tools._load_testbed", return_value=mock_tb), \
             patch("agentic_ni.tools.pyats_tools._connect_device", return_value=dev):
            result = rollback_config("R1", SAMPLE_DEVICES["R1"], R1_BACKUP)

        assert result["success"] is False
        assert "Connection lost" in result["error"]


# ---------------------------------------------------------------------------
# _get_live_tools のテスト（pyats_tools を返すだけ）
# ---------------------------------------------------------------------------


class TestGetLiveTools:
    def test_returns_pyats_tools(self):
        from agentic_ni.graph import _get_live_tools
        from agentic_ni.tools import pyats_tools

        assert _get_live_tools({}) is pyats_tools

    def test_returns_pyats_tools_without_state(self):
        from agentic_ni.graph import _get_live_tools
        from agentic_ni.tools import pyats_tools

        assert _get_live_tools() is pyats_tools

    def test_has_required_functions(self):
        from agentic_ni.graph import _get_live_tools

        tools = _get_live_tools()
        for fn in ("load_inventory", "check_connectivity",
                   "backup_running_config", "apply_config", "rollback_config"):
            assert hasattr(tools, fn), f"Missing function: {fn}"
