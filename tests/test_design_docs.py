"""Phase D 設計ドキュメント生成のユニットテスト。

_parse_ip_ledger / _parse_routing_config / _generate_design_docs を LLM/CML なしで検証する。
"""

from __future__ import annotations

import csv
import tempfile
from pathlib import Path

import pytest

from agentic_ni.graph import _generate_design_docs, _parse_ip_ledger, _parse_routing_config
from agentic_ni.state import AgentState

# ---------------------------------------------------------------------------
# テスト用サンプルデータ
# ---------------------------------------------------------------------------

_R1_CONFIG = """\
hostname R1
!
interface GigabitEthernet0/0
 ip address 10.0.12.1 255.255.255.252
 no shutdown
!
interface Loopback0
 ip address 1.1.1.1 255.255.255.255
!
router ospf 1
 router-id 1.1.1.1
 network 10.0.12.0 0.0.0.3 area 0
 network 1.1.1.1 0.0.0.0 area 0
!
router bgp 65000
 bgp log-neighbor-changes
 neighbor 2.2.2.2 remote-as 65000
!
end
"""

_R2_CONFIG = """\
hostname R2
!
interface GigabitEthernet0/0
 ip address 10.0.12.2 255.255.255.252
 no shutdown
!
interface Loopback0
 ip address 2.2.2.2 255.255.255.255
!
router ospf 1
 router-id 2.2.2.2
 network 10.0.12.0 0.0.0.3 area 0
 network 2.2.2.2 0.0.0.0 area 0
!
router bgp 65000
 bgp log-neighbor-changes
 neighbor 1.1.1.1 remote-as 65000
!
end
"""

_SAMPLE_TOPOLOGY_YAML = """\
lab:
  title: test-lab
  version: "0.1.0"
nodes:
  - id: "n0"
    label: "R1"
    node_definition: "iosv"
    interfaces:
      - id: "i0"
        label: "GigabitEthernet0/0"
        slot: 0
        type: physical
  - id: "n1"
    label: "R2"
    node_definition: "iosv"
    interfaces:
      - id: "i0"
        label: "GigabitEthernet0/0"
        slot: 0
        type: physical
links:
  - id: "l0"
    n1: "n0"
    i1: "i0"
    n2: "n1"
    i2: "i0"
"""

_OSPF_ONLY_CONFIG = """\
hostname R3
interface GigabitEthernet0/0
 ip address 10.0.13.1 255.255.255.252
router ospf 1
 router-id 3.3.3.3
 network 10.0.13.0 0.0.0.3 area 0
end
"""


def _base_state(**overrides) -> AgentState:
    base: AgentState = {
        "requirement": "R1 と R2 を OSPF エリア 0 で接続する",
        "prompt_set": "demo",
        "fault_simulation_enabled": False,
        "skip_deploy": False,
        "error_history": [],
        "topology_yaml": _SAMPLE_TOPOLOGY_YAML,
        "device_configs": {"R1": _R1_CONFIG, "R2": _R2_CONFIG},
        "lab_id": "lab-test-001",
        "test_results": [],
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
        "final_report": "",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# _parse_ip_ledger テスト
# ---------------------------------------------------------------------------


class TestParseIpLedger:
    def test_extracts_physical_interface_ip(self):
        rows = _parse_ip_ledger({"R1": _R1_CONFIG})
        gi = next((r for r in rows if r["interface"] == "GigabitEthernet0/0"), None)
        assert gi is not None
        assert gi["ip_address"] == "10.0.12.1"
        assert gi["prefix_length"] == 30

    def test_extracts_loopback_ip(self):
        rows = _parse_ip_ledger({"R1": _R1_CONFIG})
        lo = next((r for r in rows if r["interface"] == "Loopback0"), None)
        assert lo is not None
        assert lo["ip_address"] == "1.1.1.1"
        assert lo["prefix_length"] == 32

    def test_cidr_notation(self):
        rows = _parse_ip_ledger({"R1": _R1_CONFIG})
        lo = next(r for r in rows if r["interface"] == "Loopback0")
        assert lo["cidr"] == "1.1.1.1/32"

    def test_subnet_calculation(self):
        rows = _parse_ip_ledger({"R1": _R1_CONFIG})
        gi = next(r for r in rows if r["interface"] == "GigabitEthernet0/0")
        assert gi["subnet"] == "10.0.12.0/30"

    def test_multiple_devices(self):
        rows = _parse_ip_ledger({"R1": _R1_CONFIG, "R2": _R2_CONFIG})
        devices = {r["device"] for r in rows}
        assert devices == {"R1", "R2"}
        # R1: GigabitEthernet0/0 + Loopback0, R2: GigabitEthernet0/0 + Loopback0
        assert len(rows) == 4

    def test_device_field_is_set(self):
        rows = _parse_ip_ledger({"R1": _R1_CONFIG})
        for row in rows:
            assert row["device"] == "R1"

    def test_no_ip_config_returns_empty(self):
        cfg = "hostname R1\ninterface GigabitEthernet0/0\n no shutdown\n"
        assert _parse_ip_ledger({"R1": cfg}) == []

    def test_empty_configs_returns_empty(self):
        assert _parse_ip_ledger({}) == []

    def test_row_has_required_keys(self):
        rows = _parse_ip_ledger({"R1": _R1_CONFIG})
        assert rows  # 空でないこと
        required = {"device", "interface", "ip_address", "prefix_length", "cidr", "subnet"}
        for row in rows:
            assert required <= set(row.keys())


# ---------------------------------------------------------------------------
# _parse_routing_config テスト
# ---------------------------------------------------------------------------


class TestParseRoutingConfig:
    def test_ospf_process_id(self):
        ospf, _ = _parse_routing_config({"R1": _R1_CONFIG})
        assert "R1" in ospf
        assert ospf["R1"]["process_id"] == "1"

    def test_ospf_router_id(self):
        ospf, _ = _parse_routing_config({"R1": _R1_CONFIG})
        assert ospf["R1"]["router_id"] == "1.1.1.1"

    def test_ospf_networks_count(self):
        ospf, _ = _parse_routing_config({"R1": _R1_CONFIG})
        assert len(ospf["R1"]["networks"]) == 2

    def test_ospf_network_content(self):
        ospf, _ = _parse_routing_config({"R1": _R1_CONFIG})
        networks = ospf["R1"]["networks"]
        assert any(n["network"] == "10.0.12.0" and n["area"] == "0" for n in networks)
        assert any(n["network"] == "1.1.1.1" and n["area"] == "0" for n in networks)

    def test_ospf_areas_collected(self):
        ospf, _ = _parse_routing_config({"R1": _R1_CONFIG})
        assert "0" in ospf["R1"]["areas"]

    def test_bgp_local_as(self):
        _, bgp = _parse_routing_config({"R1": _R1_CONFIG})
        assert "R1" in bgp
        assert bgp["R1"]["local_as"] == "65000"

    def test_bgp_neighbor(self):
        _, bgp = _parse_routing_config({"R1": _R1_CONFIG})
        neighbors = bgp["R1"]["neighbors"]
        assert len(neighbors) == 1
        assert neighbors[0]["peer"] == "2.2.2.2"
        assert neighbors[0]["remote_as"] == "65000"

    def test_multiple_devices(self):
        ospf, bgp = _parse_routing_config({"R1": _R1_CONFIG, "R2": _R2_CONFIG})
        assert set(ospf.keys()) == {"R1", "R2"}
        assert set(bgp.keys()) == {"R1", "R2"}

    def test_ospf_only_no_bgp(self):
        ospf, bgp = _parse_routing_config({"R3": _OSPF_ONLY_CONFIG})
        assert "R3" in ospf
        assert bgp == {}

    def test_no_routing_protocols(self):
        cfg = "hostname R1\ninterface Gi0/0\n ip address 1.1.1.1 255.255.255.255\n"
        ospf, bgp = _parse_routing_config({"R1": cfg})
        assert ospf == {}
        assert bgp == {}

    def test_ospf_no_router_id(self):
        cfg = "hostname R1\nrouter ospf 1\n network 10.0.0.0 0.0.0.3 area 0\n"
        ospf, _ = _parse_routing_config({"R1": cfg})
        assert ospf["R1"]["router_id"] is None


# ---------------------------------------------------------------------------
# _generate_design_docs テスト
# ---------------------------------------------------------------------------


class TestGenerateDesignDocs:
    def test_saves_topology_yaml(self, tmp_path):
        _generate_design_docs(_base_state(), tmp_path)
        assert (tmp_path / "topology.yaml").exists()

    def test_saves_cfg_files(self, tmp_path):
        _generate_design_docs(_base_state(), tmp_path)
        assert (tmp_path / "R1.cfg").exists()
        assert (tmp_path / "R2.cfg").exists()

    def test_saves_ip_ledger_md(self, tmp_path):
        _generate_design_docs(_base_state(), tmp_path)
        assert (tmp_path / "ip_ledger.md").exists()

    def test_saves_ip_ledger_csv(self, tmp_path):
        _generate_design_docs(_base_state(), tmp_path)
        assert (tmp_path / "ip_ledger.csv").exists()

    def test_saves_routing_design_md(self, tmp_path):
        _generate_design_docs(_base_state(), tmp_path)
        assert (tmp_path / "routing_design.md").exists()

    def test_ip_ledger_csv_headers(self, tmp_path):
        _generate_design_docs(_base_state(), tmp_path)
        with open(tmp_path / "ip_ledger.csv", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            assert set(reader.fieldnames or []) >= {"device", "ip_address", "cidr", "subnet"}

    def test_ip_ledger_csv_row_count(self, tmp_path):
        _generate_design_docs(_base_state(), tmp_path)
        with open(tmp_path / "ip_ledger.csv", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 4  # R1×2 + R2×2

    def test_ip_ledger_md_contains_addresses(self, tmp_path):
        _generate_design_docs(_base_state(), tmp_path)
        content = (tmp_path / "ip_ledger.md").read_text(encoding="utf-8")
        assert "1.1.1.1/32" in content
        assert "2.2.2.2/32" in content
        assert "10.0.12.0/30" in content

    def test_routing_design_contains_ospf(self, tmp_path):
        _generate_design_docs(_base_state(), tmp_path)
        content = (tmp_path / "routing_design.md").read_text(encoding="utf-8")
        assert "OSPF" in content

    def test_routing_design_contains_router_id(self, tmp_path):
        _generate_design_docs(_base_state(), tmp_path)
        content = (tmp_path / "routing_design.md").read_text(encoding="utf-8")
        assert "1.1.1.1" in content  # R1 router-id

    def test_routing_design_contains_bgp(self, tmp_path):
        _generate_design_docs(_base_state(), tmp_path)
        content = (tmp_path / "routing_design.md").read_text(encoding="utf-8")
        assert "BGP" in content
        assert "65000" in content

    def test_summary_contains_ip_table(self, tmp_path):
        summary = _generate_design_docs(_base_state(), tmp_path)
        assert "IP アドレス台帳" in summary
        assert "1.1.1.1" in summary

    def test_summary_contains_routing_summary(self, tmp_path):
        summary = _generate_design_docs(_base_state(), tmp_path)
        assert "ルーティング設計サマリー" in summary
        assert "OSPF" in summary

    def test_summary_contains_file_list(self, tmp_path):
        summary = _generate_design_docs(_base_state(), tmp_path)
        assert "保存先ファイル" in summary
        assert "ip_ledger.md" in summary
        assert "routing_design.md" in summary

    def test_empty_topology_yaml_skips_topology_file(self, tmp_path):
        state = _base_state(topology_yaml="")
        _generate_design_docs(state, tmp_path)
        assert not (tmp_path / "topology.yaml").exists()

    def test_creates_output_dir_if_not_exists(self, tmp_path):
        out_dir = tmp_path / "new" / "nested" / "dir"
        _generate_design_docs(_base_state(), out_dir)
        assert out_dir.exists()

    def test_returns_string(self, tmp_path):
        result = _generate_design_docs(_base_state(), tmp_path)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_no_routing_protocols_shows_placeholder(self, tmp_path):
        cfg_no_routing = "hostname R1\ninterface Gi0/0\n ip address 10.0.0.1 255.255.255.0\n"
        state = _base_state(device_configs={"R1": cfg_no_routing}, topology_yaml="")
        _generate_design_docs(state, tmp_path)
        content = (tmp_path / "routing_design.md").read_text(encoding="utf-8")
        assert "設定なし" in content
