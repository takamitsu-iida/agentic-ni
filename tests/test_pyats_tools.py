"""pyats_tools のユニットテスト。pyATS/Genie 未インストールでも動作（すべてモック）。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch, call
import pytest


# ---------------------------------------------------------------------------
# テスト用フィクスチャ
# ---------------------------------------------------------------------------

SAMPLE_TESTBED_YAML = """\
testbed:
  name: test-lab
devices:
  R1:
    os: iosxe
    type: router
    connections:
      defaults:
        class: unicon.Unicon
      a:
        protocol: telnet
        ip: 192.168.0.1
        port: 5000
    credentials:
      default:
        username: admin
        password: admin
  R2:
    os: iosxe
    type: router
    connections:
      defaults:
        class: unicon.Unicon
      a:
        protocol: telnet
        ip: 192.168.0.1
        port: 5001
    credentials:
      default:
        username: admin
        password: admin
"""


def _make_mock_device(name: str = "R1") -> MagicMock:
    """モックデバイスを生成する。"""
    device = MagicMock()
    device.name = name
    return device


def _make_mock_testbed(devices: dict[str, MagicMock] | None = None) -> MagicMock:
    """モックテストベッドを生成する。"""
    testbed = MagicMock()
    _devices = devices or {"R1": _make_mock_device("R1")}
    testbed.devices = _devices
    return testbed


# ---------------------------------------------------------------------------
# _load_testbed / _require_pyats
# ---------------------------------------------------------------------------


class TestRequirePyats:
    def test_raises_import_error_when_pyats_not_installed(self):
        """pyATS未インストール時に明示的な ImportError を送出する。"""
        with patch.dict("sys.modules", {"pyats": None, "pyats.topology": None}):
            import importlib
            import agentic_ni.tools.pyats_tools as m

            importlib.reload(m)
            with pytest.raises(ImportError, match="pyATS/Genie"):
                m._require_pyats()


class TestLoadTestbed:
    def test_calls_loader_load_with_stringio(self):
        mock_loader = MagicMock()
        mock_testbed = MagicMock()
        mock_loader.load.return_value = mock_testbed

        with patch("agentic_ni.tools.pyats_tools._require_pyats", return_value=mock_loader):
            from agentic_ni.tools.pyats_tools import _load_testbed

            result = _load_testbed(SAMPLE_TESTBED_YAML)

        mock_loader.load.assert_called_once()
        assert result is mock_testbed


# ---------------------------------------------------------------------------
# build_testbed
# ---------------------------------------------------------------------------


class TestBuildTestbed:
    def test_returns_testbed_yaml_from_cml(self, monkeypatch):
        expected_yaml = "testbed:\n  name: lab\n"
        mock_lab = MagicMock()
        mock_lab.get_pyats_testbed.return_value = expected_yaml
        mock_client = MagicMock()
        mock_client.get_local_lab.return_value = mock_lab

        with patch("agentic_ni.tools.cml_tools._get_client", return_value=mock_client):
            with patch("agentic_ni.tools.cml_tools._get_lab", return_value=mock_lab):
                from agentic_ni.tools.pyats_tools import build_testbed

                result = build_testbed("lab-abc", {"R1": "hostname R1\n"})

        assert result == expected_yaml
        mock_lab.get_pyats_testbed.assert_called_once()

    def test_raises_when_lab_not_found(self, monkeypatch):
        with patch("agentic_ni.tools.cml_tools._get_client", return_value=MagicMock()):
            with patch(
                "agentic_ni.tools.cml_tools._get_lab",
                side_effect=KeyError("lab-missing"),
            ):
                from agentic_ni.tools.pyats_tools import build_testbed

                with pytest.raises(KeyError, match="lab-missing"):
                    build_testbed("lab-missing", {})


# ---------------------------------------------------------------------------
# run_show_command
# ---------------------------------------------------------------------------


class TestRunShowCommand:
    def _patch_connect(self, mock_device: MagicMock):
        """_load_testbed と _connect_device をまとめてモックするコンテキスト。"""
        mock_testbed = _make_mock_testbed({"R1": mock_device})
        return (
            patch("agentic_ni.tools.pyats_tools._load_testbed", return_value=mock_testbed),
            patch("agentic_ni.tools.pyats_tools._connect_device", return_value=mock_device),
        )

    def test_returns_parsed_dict_when_genie_succeeds(self):
        mock_device = _make_mock_device()
        expected = {"vrf": {"default": {}}}
        mock_device.parse.return_value = expected

        p1, p2 = self._patch_connect(mock_device)
        with p1, p2:
            from agentic_ni.tools.pyats_tools import run_show_command

            result = run_show_command(SAMPLE_TESTBED_YAML, "R1", "show ip ospf neighbor detail")

        assert result == expected
        mock_device.disconnect.assert_called_once()

    def test_falls_back_to_raw_output_when_parse_fails(self):
        mock_device = _make_mock_device()
        mock_device.parse.side_effect = Exception("no parser")
        mock_device.execute.return_value = "raw output text"

        p1, p2 = self._patch_connect(mock_device)
        with p1, p2:
            from agentic_ni.tools.pyats_tools import run_show_command

            result = run_show_command(SAMPLE_TESTBED_YAML, "R1", "show version")

        assert result == {"raw_output": "raw output text"}

    def test_disconnects_even_on_exception(self):
        mock_device = _make_mock_device()
        mock_device.parse.side_effect = Exception("parse error")
        mock_device.execute.side_effect = RuntimeError("execute error")

        p1, p2 = self._patch_connect(mock_device)
        with p1, p2:
            from agentic_ni.tools.pyats_tools import run_show_command

            with pytest.raises(RuntimeError):
                run_show_command(SAMPLE_TESTBED_YAML, "R1", "show version")

        mock_device.disconnect.assert_called_once()


# ---------------------------------------------------------------------------
# check_ospf_neighbors
# ---------------------------------------------------------------------------


class TestCheckOspfNeighbors:
    def _ospf_genie_output(self, neighbor_id: str = "2.2.2.2", state: str = "FULL/DR") -> dict:
        """Genie の show ip ospf neighbor detail パース結果を模倣。"""
        return {
            "vrf": {
                "default": {
                    "address_family": {
                        "ipv4": {
                            "instance": {
                                "1": {
                                    "areas": {
                                        "0.0.0.0": {
                                            "interfaces": {
                                                "GigabitEthernet1": {
                                                    "neighbors": {
                                                        neighbor_id: {
                                                            "state": state,
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

    def test_returns_neighbors_up_count_when_full(self):
        ospf_output = self._ospf_genie_output(state="FULL/DR")
        with patch(
            "agentic_ni.tools.pyats_tools.run_show_command", return_value=ospf_output
        ):
            from agentic_ni.tools.pyats_tools import check_ospf_neighbors

            result = check_ospf_neighbors(SAMPLE_TESTBED_YAML, "R1")

        assert result["neighbors_up"] == 1
        assert result["neighbors"][0]["state"] == "FULL/DR"

    def test_returns_zero_when_neighbor_down(self):
        ospf_output = self._ospf_genie_output(state="INIT")
        with patch(
            "agentic_ni.tools.pyats_tools.run_show_command", return_value=ospf_output
        ):
            from agentic_ni.tools.pyats_tools import check_ospf_neighbors

            result = check_ospf_neighbors(SAMPLE_TESTBED_YAML, "R1")

        assert result["neighbors_up"] == 0

    def test_handles_empty_ospf_output_gracefully(self):
        with patch("agentic_ni.tools.pyats_tools.run_show_command", return_value={}):
            from agentic_ni.tools.pyats_tools import check_ospf_neighbors

            result = check_ospf_neighbors(SAMPLE_TESTBED_YAML, "R1")

        assert result["neighbors_up"] == 0
        assert result["neighbors"] == []


# ---------------------------------------------------------------------------
# check_bgp_summary
# ---------------------------------------------------------------------------


class TestCheckBgpSummary:
    def _bgp_genie_output(self, peer_ip: str = "10.0.0.2", state=100) -> dict:
        """Genie の show bgp all summary パース結果を模倣。"""
        return {
            "vrf": {
                "default": {
                    "neighbor": {
                        peer_ip: {
                            "address_family": {
                                "ipv4 unicast": {
                                    "state_pfxrcd": state,
                                }
                            }
                        }
                    }
                }
            }
        }

    def test_counts_established_peers_by_numeric_prefix_count(self):
        bgp_output = self._bgp_genie_output(state=5)
        with patch("agentic_ni.tools.pyats_tools.run_show_command", return_value=bgp_output):
            from agentic_ni.tools.pyats_tools import check_bgp_summary

            result = check_bgp_summary(SAMPLE_TESTBED_YAML, "R1")

        assert result["peers_established"] == 1
        assert result["peers"][0]["established"] is True

    def test_counts_zero_when_peer_not_established(self):
        bgp_output = self._bgp_genie_output(state="Active")
        with patch("agentic_ni.tools.pyats_tools.run_show_command", return_value=bgp_output):
            from agentic_ni.tools.pyats_tools import check_bgp_summary

            result = check_bgp_summary(SAMPLE_TESTBED_YAML, "R1")

        assert result["peers_established"] == 0

    def test_handles_empty_bgp_output_gracefully(self):
        with patch("agentic_ni.tools.pyats_tools.run_show_command", return_value={}):
            from agentic_ni.tools.pyats_tools import check_bgp_summary

            result = check_bgp_summary(SAMPLE_TESTBED_YAML, "R1")

        assert result["peers_established"] == 0


# ---------------------------------------------------------------------------
# check_ping
# ---------------------------------------------------------------------------


class TestCheckPing:
    def _patch_device(self, execute_output: str):
        mock_device = _make_mock_device()
        mock_device.execute.return_value = execute_output
        mock_testbed = _make_mock_testbed({"R1": mock_device})
        return (
            patch("agentic_ni.tools.pyats_tools._load_testbed", return_value=mock_testbed),
            patch("agentic_ni.tools.pyats_tools._connect_device", return_value=mock_device),
        )

    def test_returns_true_on_success_rate_100(self):
        output = "Success rate is 100 percent (5/5), round-trip min/avg/max = 1/1/1 ms"
        p1, p2 = self._patch_device(output)
        with p1, p2:
            from agentic_ni.tools.pyats_tools import check_ping

            assert check_ping(SAMPLE_TESTBED_YAML, "R1", "192.168.1.1") is True

    def test_returns_false_on_success_rate_0(self):
        output = "Success rate is 0 percent (0/5)"
        p1, p2 = self._patch_device(output)
        with p1, p2:
            from agentic_ni.tools.pyats_tools import check_ping

            assert check_ping(SAMPLE_TESTBED_YAML, "R1", "192.168.1.1") is False

    def test_returns_true_when_exclamation_mark_present(self):
        output = "Sending 5, 100-byte ICMP Echos\n!!!!!\n"
        p1, p2 = self._patch_device(output)
        with p1, p2:
            from agentic_ni.tools.pyats_tools import check_ping

            assert check_ping(SAMPLE_TESTBED_YAML, "R1", "10.0.0.1") is True

    def test_returns_false_when_unreachable(self):
        output = "Network is unreachable"
        p1, p2 = self._patch_device(output)
        with p1, p2:
            from agentic_ni.tools.pyats_tools import check_ping

            assert check_ping(SAMPLE_TESTBED_YAML, "R1", "192.168.1.1") is False


# ---------------------------------------------------------------------------
# check_vlan_interfaces
# ---------------------------------------------------------------------------


class TestCheckVlanInterfaces:
    def _vlan_genie_output(self) -> dict:
        return {
            "vlans": {
                "10": {"name": "SALES", "state": "active"},
                "20": {"name": "MGMT", "state": "active"},
            }
        }

    def _intf_genie_output(self) -> dict:
        return {
            "interfaces": {
                "GigabitEthernet0/1": {"line_protocol": "up"},
                "GigabitEthernet0/2": {"line_protocol": "down"},
                "Vlan10": {"line_protocol": "up"},
            }
        }

    def test_returns_vlan_and_interface_info(self):
        with patch(
            "agentic_ni.tools.pyats_tools.run_show_command",
            side_effect=[self._vlan_genie_output(), self._intf_genie_output()],
        ):
            from agentic_ni.tools.pyats_tools import check_vlan_interfaces

            result = check_vlan_interfaces(SAMPLE_TESTBED_YAML, "SW1")

        assert result["vlans"] == {"10": "active", "20": "active"}
        assert result["interfaces_up"] == 2  # Gi0/1 と Vlan10

    def test_handles_empty_output_gracefully(self):
        with patch(
            "agentic_ni.tools.pyats_tools.run_show_command", side_effect=[{}, {}]
        ):
            from agentic_ni.tools.pyats_tools import check_vlan_interfaces

            result = check_vlan_interfaces(SAMPLE_TESTBED_YAML, "SW1")

        assert result["vlans"] == {}
        assert result["interfaces_up"] == 0
