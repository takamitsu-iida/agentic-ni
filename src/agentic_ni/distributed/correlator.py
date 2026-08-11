"""EventCorrelator — 複数の SYSLOG を時間窓で束ねて NetworkIncident を生成する。

相関キー抽出戦略（優先順）:
  1. インターフェース名 + topology → リンク ID ペア "link:{n_a}-{n_b}"
  2. BGP/OSPF ネイバー IP → /30 サブネットキー "subnet:{masked_int}"
  3. フォールバック: "device:{hostname}"

同一相関キーのイベントが window_seconds 以内に到着した場合は 1 件の
NetworkIncident に束ね、window 経過後に on_incident コールバックへ渡す。
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from agentic_ni.distributed.incident import NetworkIncident

logger = logging.getLogger(__name__)

# インターフェース名を SYSLOG テキストから抽出する正規表現
_IFACE_RE = re.compile(
    r"[Ii]nterface\s+((?:GigabitEthernet|FastEthernet|Ethernet|Serial|Loopback|Vlan)"
    r"[\d/.:]+)",
    re.IGNORECASE,
)

# BGP/OSPF ネイバー IP を抽出する正規表現
_NEIGHBOR_IP_RE = re.compile(
    r"(?:[Nn]eighbor|Nbr)\s+(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"
)

# node configuration から「interface X / ip address A B」を抽出する正規表現
_CONFIG_IFACE_RE = re.compile(r"^\s*interface\s+(\S+)", re.MULTILINE | re.IGNORECASE)
_CONFIG_IP_RE = re.compile(r"^\s*ip address\s+(\d[\d.]+)\s+(\d[\d.]+)", re.MULTILINE | re.IGNORECASE)


@dataclass
class _IncidentBuffer:
    """相関窓内で蓄積中のイベントバッファ。"""

    correlation_key: str
    affected_devices: set[str] = field(default_factory=set)
    syslog_events: list[str] = field(default_factory=list)
    first_seen: float = field(default_factory=time.monotonic)


class EventCorrelator:
    """SYSLOG ストリームを相関させて NetworkIncident を生成する。

    Args:
        topology_data:    topology.yaml を yaml.safe_load した辞書。
        window_seconds:   相関時間窓（秒）。窓が閉じると Incident を発火する。
        on_incident:      Incident 発火時に呼ばれる async コールバック。
    """

    def __init__(
        self,
        topology_data: dict,
        window_seconds: float = 5.0,
        on_incident: Callable[[NetworkIncident], Awaitable[None]] | None = None,
    ) -> None:
        self._window = window_seconds
        self._on_incident = on_incident

        # 相関キー → バッファ / フラッシュタスク
        self._buffers: dict[str, _IncidentBuffer] = {}
        self._flush_tasks: dict[str, asyncio.Task] = {}

        # topology からルックアップテーブルを構築
        self._node_id_by_label: dict[str, str] = {}
        self._iface_id_by_label: dict[tuple[str, str], str] = {}
        # (node_id, iface_id) → sorted node ペアキー "nA-nB"
        self._link_key_by_iface: dict[tuple[str, str], str] = {}
        # /30 サブネット整数 → リンクキー（BGP/OSPF ネイバー IP の解決用）
        self._subnet_to_link_key: dict[int, str] = {}
        self._build_topology_index(topology_data)

    # ------------------------------------------------------------------
    # topology インデックス構築
    # ------------------------------------------------------------------

    def _build_topology_index(self, data: dict) -> None:
        for node in data.get("nodes", []):
            node_id: str = node["id"]
            label: str = node["label"]
            self._node_id_by_label[label] = node_id
            for iface in node.get("interfaces", []):
                # (node_id, interface_label) → interface_id
                self._iface_id_by_label[(node_id, iface["label"])] = iface["id"]

        for link in data.get("links", []):
            n1, i1 = link.get("n1", ""), link.get("i1", "")
            n2, i2 = link.get("n2", ""), link.get("i2", "")
            if not (n1 and i1 and n2 and i2):
                continue
            # 両端から同じキーで引けるよう sorted ペアを登録
            link_key = "-".join(sorted([n1, n2]))
            self._link_key_by_iface[(n1, i1)] = link_key
            self._link_key_by_iface[(n2, i2)] = link_key

        # node configuration の P2P IP から /30 サブネット → リンクキー を構築
        self._build_subnet_index(data)

    def _build_subnet_index(self, data: dict) -> None:
        """各ノードの configuration テキストから P2P IP を抽出してサブネットマップを作る。"""
        node_id_by_label = self._node_id_by_label
        for node in data.get("nodes", []):
            config = node.get("configuration", "")
            if not config:
                continue
            node_id = node_id_by_label.get(node["label"], "")
            if not node_id:
                continue

            # configuration テキストをインターフェースブロックに分割して IP を抽出
            current_iface_label: str | None = None
            for line in config.splitlines():
                m_iface = _CONFIG_IFACE_RE.match(line)
                if m_iface:
                    current_iface_label = m_iface.group(1)
                    continue
                if current_iface_label:
                    m_ip = _CONFIG_IP_RE.match(line)
                    if m_ip:
                        ip_str = m_ip.group(1)
                        iface_id = self._iface_id_by_label.get((node_id, current_iface_label))
                        if iface_id:
                            link_key = self._link_key_by_iface.get((node_id, iface_id))
                            if link_key:
                                try:
                                    subnet_int = int(ipaddress.ip_address(ip_str)) & 0xFFFFFFFC
                                    self._subnet_to_link_key[subnet_int] = link_key
                                except ValueError:
                                    pass

    # ------------------------------------------------------------------
    # 相関キー抽出
    # ------------------------------------------------------------------

    def _extract_correlation_key(self, hostname: str, raw_text: str) -> str:
        node_id = self._node_id_by_label.get(hostname)
        if not node_id:
            logger.debug("topology に未登録のホスト: %s → device キーを使用", hostname)
            return f"device:{hostname}"

        # 優先度 1: インターフェース名 → topology リンクキー
        m = _IFACE_RE.search(raw_text)
        if m:
            iface_label = m.group(1).rstrip(",")
            iface_id = self._iface_id_by_label.get((node_id, iface_label))
            if iface_id:
                link_key = self._link_key_by_iface.get((node_id, iface_id))
                if link_key:
                    return f"link:{link_key}"

        # 優先度 2: BGP/OSPF ネイバー IP → topology の P2P IP テーブル → リンクキー
        m2 = _NEIGHBOR_IP_RE.search(raw_text)
        if m2:
            try:
                subnet_int = int(ipaddress.ip_address(m2.group(1))) & 0xFFFFFFFC
                link_key = self._subnet_to_link_key.get(subnet_int)
                if link_key:
                    return f"link:{link_key}"
                # topology に IP 情報がない場合はサブネットキーにフォールバック
                return f"subnet:{subnet_int}"
            except ValueError:
                pass

        return f"device:{hostname}"

    # ------------------------------------------------------------------
    # SYSLOG 受信
    # ------------------------------------------------------------------

    async def receive_syslog(self, hostname: str, raw_text: str) -> None:
        """SYSLOG を受信してバッファに蓄積する。窓が新規なら flush タスクを起動する。"""
        key = self._extract_correlation_key(hostname, raw_text)
        logger.debug("SYSLOG 相関キー: %s ← %s", key, hostname)

        if key not in self._buffers:
            self._buffers[key] = _IncidentBuffer(correlation_key=key)
            loop = asyncio.get_event_loop()
            task = loop.create_task(self._flush_after(key))
            self._flush_tasks[key] = task

        buf = self._buffers[key]
        buf.affected_devices.add(hostname)
        buf.syslog_events.append(raw_text)

    # ------------------------------------------------------------------
    # フラッシュ（窓クローズ → Incident 発火）
    # ------------------------------------------------------------------

    async def _flush_after(self, key: str) -> None:
        await asyncio.sleep(self._window)
        await self._emit(key)

    async def _emit(self, key: str) -> None:
        buf = self._buffers.pop(key, None)
        self._flush_tasks.pop(key, None)
        if buf is None:
            return
        incident = NetworkIncident(
            correlation_key=buf.correlation_key,
            affected_devices=sorted(buf.affected_devices),
            syslog_events=list(buf.syslog_events),
        )
        logger.info(
            "Incident 発火: key=%s devices=%s events=%d",
            key, incident.affected_devices, len(incident.syslog_events),
        )
        if self._on_incident:
            await self._on_incident(incident)

    async def flush_all(self) -> None:
        """残存するすべてのバッファを即座にフラッシュする（シャットダウン用）。"""
        for task in list(self._flush_tasks.values()):
            task.cancel()
        self._flush_tasks.clear()
        for key in list(self._buffers.keys()):
            await self._emit(key)
