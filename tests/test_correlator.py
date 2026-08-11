"""Refactor-2: EventCorrelator のユニットテスト。"""

from __future__ import annotations

import asyncio

import pytest

from agentic_ni.distributed.correlator import EventCorrelator
from agentic_ni.distributed.incident import NetworkIncident

# ---------------------------------------------------------------------------
# テスト用トポロジー（Spine1-Leaf3 リンクのみ抜粋）
# ---------------------------------------------------------------------------

_TOPO = {
    "lab": {"title": "test"},
    "nodes": [
        {
            "id": "n0",
            "label": "Spine1",
            "node_definition": "iosv",
            "interfaces": [
                {"id": "i0", "label": "GigabitEthernet0/0"},
                {"id": "i1", "label": "GigabitEthernet0/1"},
                {"id": "i2", "label": "GigabitEthernet0/2"},
            ],
            "configuration": (
                "hostname Spine1\n"
                "interface GigabitEthernet0/2\n"
                " ip address 10.1.13.1 255.255.255.252\n"
            ),
        },
        {
            "id": "n4",
            "label": "Leaf3",
            "node_definition": "iosv",
            "interfaces": [
                {"id": "i0", "label": "GigabitEthernet0/0"},
                {"id": "i1", "label": "GigabitEthernet0/1"},
            ],
            "configuration": (
                "hostname Leaf3\n"
                "interface GigabitEthernet0/0\n"
                " ip address 10.1.13.2 255.255.255.252\n"
            ),
        },
        {
            "id": "n5",
            "label": "Ubuntu",
            "node_definition": "ubuntu",
            "interfaces": [],
        },
    ],
    "links": [
        {"id": "l2", "n1": "n0", "i1": "i2", "n2": "n4", "i2": "i0", "label": "Spine1-Leaf3"},
    ],
}

_SYSLOG_SPINE1_LINEPROTO = (
    "%LINEPROTO-5-UPDOWN: Line protocol on Interface GigabitEthernet0/2, changed state to down"
)
_SYSLOG_SPINE1_BGP = (
    "%BGP-5-ADJCHANGE: neighbor 10.1.13.2 Down Interface flap"
)
_SYSLOG_LEAF3_LINEPROTO = (
    "%LINEPROTO-5-UPDOWN: Line protocol on Interface GigabitEthernet0/0, changed state to down"
)
_SYSLOG_UNRELATED = (
    "%SYS-5-CONFIG_I: Configured from console by cisco on vty0"
)


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _make_correlator(window: float = 0.05) -> tuple[EventCorrelator, list[NetworkIncident]]:
    incidents: list[NetworkIncident] = []

    async def collect(inc: NetworkIncident) -> None:
        incidents.append(inc)

    return EventCorrelator(_TOPO, window_seconds=window, on_incident=collect), incidents


# ---------------------------------------------------------------------------
# テスト
# ---------------------------------------------------------------------------

class TestCorrelationKey:
    def test_interface_maps_to_link_key(self):
        corr, _ = _make_correlator()
        key = corr._extract_correlation_key("Spine1", _SYSLOG_SPINE1_LINEPROTO)
        assert key == "link:n0-n4"

    def test_leaf3_interface_maps_to_same_link_key(self):
        corr, _ = _make_correlator()
        key = corr._extract_correlation_key("Leaf3", _SYSLOG_LEAF3_LINEPROTO)
        assert key == "link:n0-n4"

    def test_bgp_neighbor_ip_maps_to_link_key_via_subnet(self):
        """BGP ネイバー IP が topology の P2P IP と照合されリンクキーに変換される。"""
        corr, _ = _make_correlator()
        key = corr._extract_correlation_key("Spine1", _SYSLOG_SPINE1_BGP)
        assert key == "link:n0-n4"

    def test_unknown_host_falls_back_to_device_key(self):
        corr, _ = _make_correlator()
        key = corr._extract_correlation_key("UnknownRouter", _SYSLOG_UNRELATED)
        assert key == "device:UnknownRouter"

    def test_known_host_no_match_falls_back_to_device_key(self):
        corr, _ = _make_correlator()
        key = corr._extract_correlation_key("Ubuntu", _SYSLOG_UNRELATED)
        assert key == "device:Ubuntu"


class TestWindowCorrelation:
    @pytest.mark.asyncio
    async def test_events_within_window_produce_one_incident(self):
        """5秒以内の Spine1 + Leaf3 の SYSLOG 群 → 1 Incident に束まる。"""
        corr, incidents = _make_correlator(window=0.1)

        await corr.receive_syslog("Spine1", _SYSLOG_SPINE1_LINEPROTO)
        await corr.receive_syslog("Leaf3", _SYSLOG_LEAF3_LINEPROTO)
        await corr.receive_syslog("Spine1", _SYSLOG_SPINE1_BGP)

        await asyncio.sleep(0.15)

        assert len(incidents) == 1
        inc = incidents[0]
        assert inc.correlation_key == "link:n0-n4"
        assert set(inc.affected_devices) == {"Spine1", "Leaf3"}
        assert len(inc.syslog_events) == 3

    @pytest.mark.asyncio
    async def test_event_after_window_produces_new_incident(self):
        """窓が閉じた後に同一キーの SYSLOG が来たら別 Incident になる。"""
        corr, incidents = _make_correlator(window=0.05)

        await corr.receive_syslog("Spine1", _SYSLOG_SPINE1_LINEPROTO)
        await asyncio.sleep(0.1)   # 窓クローズ待ち

        await corr.receive_syslog("Leaf3", _SYSLOG_LEAF3_LINEPROTO)
        await asyncio.sleep(0.1)   # 2件目の窓クローズ待ち

        assert len(incidents) == 2
        assert incidents[0].correlation_key == "link:n0-n4"
        assert incidents[1].correlation_key == "link:n0-n4"

    @pytest.mark.asyncio
    async def test_unmatched_syslog_produces_device_incident(self):
        """相関キー抽出できない SYSLOG → 'device:{hostname}' キーで単独 Incident。"""
        corr, incidents = _make_correlator(window=0.05)

        await corr.receive_syslog("Spine1", _SYSLOG_UNRELATED)
        await asyncio.sleep(0.1)

        assert len(incidents) == 1
        assert incidents[0].correlation_key == "device:Spine1"

    @pytest.mark.asyncio
    async def test_flush_all_emits_pending_buffers(self):
        """flush_all() は残存バッファを即座に発火する。"""
        corr, incidents = _make_correlator(window=60.0)  # 窓は長いが即時フラッシュ

        await corr.receive_syslog("Spine1", _SYSLOG_SPINE1_LINEPROTO)
        assert len(incidents) == 0  # まだ窓内

        await corr.flush_all()

        assert len(incidents) == 1
