"""PacketLossDetector — 隣接デバイスペアのインターフェース統計を同時収集してパケットロスを検出する。

動作概要:
  1. topology.yaml のリンク定義から監視対象ペアを構築する。
  2. poll_interval 秒ごとに両端のインターフェースを asyncio.gather で同時ポーリングする。
  3. TX 増分（一方の output）と RX 増分（他方の input）を比較し、
     損失率 = (TX - RX) / TX が loss_threshold を超えたら MessageBus にアラートを送信する。
  4. 初回ポーリングは基準値の取得のみで比較を行わない。

使用方法::

    from agentic_ni.distributed.packet_loss_detector import PacketLossDetector, build_link_pairs

    link_pairs = build_link_pairs(topology_yaml_dict)
    run_show_fns = {
        "R1": r1_toolkit.run_show_direct,
        "R2": r2_toolkit.run_show_direct,
    }
    detector = PacketLossDetector(
        links=link_pairs,
        run_show_fns=run_show_fns,
        bus=bus,
        poll_interval=30.0,
        loss_threshold=0.01,
    )
    await detector.start()
    ...
    await detector.stop()
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable

from agentic_ni.distributed.bus import MessageBus
from agentic_ni.distributed.message import AgentMessage

logger = logging.getLogger(__name__)

# show interfaces 出力からパケットカウンタを抽出する正規表現
_INPUT_PKTS_RE = re.compile(r"(\d+)\s+packets\s+input", re.IGNORECASE)
_OUTPUT_PKTS_RE = re.compile(r"(\d+)\s+packets\s+output", re.IGNORECASE)

_DETECTOR_AGENT_ID = "PacketLossDetector"
_ALERT_TOPIC = "network/agents/alert"


# ---------------------------------------------------------------------------
# データクラス
# ---------------------------------------------------------------------------

@dataclass
class _LinkPair:
    """topology 上の 1 リンク（両端デバイス名とインターフェース名）。"""

    device_a: str
    iface_a: str
    device_b: str
    iface_b: str

    @property
    def label(self) -> str:
        return f"{self.device_a}/{self.iface_a} <-> {self.device_b}/{self.iface_b}"


@dataclass
class _IfaceCounters:
    """単一ポーリング時点のインターフェース統計。"""

    input_pkts: int = 0
    output_pkts: int = 0
    timestamp: float = field(default_factory=time.monotonic)


@dataclass
class PacketLossEvent:
    """検出したパケットロスの情報。"""

    link_label: str
    direction: str      # "A→B" or "B→A"
    tx_device: str
    tx_iface: str
    tx_delta: int
    rx_device: str
    rx_iface: str
    rx_delta: int
    loss_ratio: float   # 0.0 〜 1.0


# ---------------------------------------------------------------------------
# PacketLossDetector
# ---------------------------------------------------------------------------

class PacketLossDetector:
    """隣接デバイスペアのインターフェース統計を同時収集してパケットロスを検出する。

    Args:
        links:           監視対象のリンクペアリスト。
        run_show_fns:    device_name → show コマンド実行関数（同期）のマッピング。
        bus:             アラート送信に使う MessageBus。
        poll_interval:   ポーリング間隔（秒）。デフォルト 30.0。
        loss_threshold:  ロス判定しきい値（0.0〜1.0）。デフォルト 0.01（1%）。
    """

    def __init__(
        self,
        links: list[_LinkPair],
        run_show_fns: dict[str, Callable[[str], str]],
        bus: MessageBus,
        poll_interval: float = 30.0,
        loss_threshold: float = 0.01,
    ) -> None:
        self._links = links
        self._run_show_fns = run_show_fns
        self._bus = bus
        self._poll_interval = poll_interval
        self._loss_threshold = loss_threshold
        # (device_name, iface_name) → 前回カウンタ
        self._prev: dict[tuple[str, str], _IfaceCounters] = {}
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """ポーリングタスクを起動する。"""
        self._task = asyncio.create_task(
            self._poll_loop(),
            name="packet-loss-detector",
        )
        logger.info(
            "PacketLossDetector 開始: %d リンク監視, interval=%.0fs, threshold=%.1f%%",
            len(self._links),
            self._poll_interval,
            self._loss_threshold * 100,
        )

    async def stop(self) -> None:
        """ポーリングタスクを停止する。"""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("PacketLossDetector 停止。")

    # ------------------------------------------------------------------
    # ポーリングループ
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self._poll_interval)
            try:
                await self._poll_all_links()
            except Exception as exc:
                logger.warning("PacketLossDetector ポーリングエラー: %s", exc)

    async def _poll_all_links(self) -> None:
        """全リンクのインターフェースを同時ポーリングして増分を比較する。"""
        # 全インターフェース（重複なし）を収集
        ifaces: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for link in self._links:
            for key in ((link.device_a, link.iface_a), (link.device_b, link.iface_b)):
                if key not in seen:
                    ifaces.append(key)
                    seen.add(key)

        # 全インターフェースを同時取得
        results = await asyncio.gather(
            *[self._fetch_counters(dev, iface) for dev, iface in ifaces],
            return_exceptions=True,
        )

        current: dict[tuple[str, str], _IfaceCounters] = {}
        for (dev, iface), result in zip(ifaces, results):
            if isinstance(result, Exception):
                logger.warning("[%s %s] 統計取得エラー: %s", dev, iface, result)
            else:
                current[(dev, iface)] = result  # type: ignore[assignment]

        # 前回データがある場合のみ比較
        if self._prev:
            for link in self._links:
                self._check_link(link, current)

        self._prev = current

    async def _fetch_counters(self, device: str, iface: str) -> _IfaceCounters:
        """show interfaces <iface> を実行してカウンタを返す（スレッドで実行）。"""
        fn = self._run_show_fns.get(device)
        if fn is None:
            raise RuntimeError(f"run_show_direct が未登録: {device!r}")
        raw = await asyncio.to_thread(fn, f"show interfaces {iface}")
        return _parse_counters(raw)

    # ------------------------------------------------------------------
    # 増分比較・アラート送信
    # ------------------------------------------------------------------

    def _check_link(
        self,
        link: _LinkPair,
        current: dict[tuple[str, str], _IfaceCounters],
    ) -> None:
        key_a = (link.device_a, link.iface_a)
        key_b = (link.device_b, link.iface_b)

        cur_a = current.get(key_a)
        cur_b = current.get(key_b)
        prev_a = self._prev.get(key_a)
        prev_b = self._prev.get(key_b)

        if not all([cur_a, cur_b, prev_a, prev_b]):
            return  # 取得失敗のエンドポイントはスキップ

        # A→B: A の output 増分 vs B の input 増分
        self._evaluate_direction(
            link, "A→B",
            link.device_a, link.iface_a, cur_a.output_pkts - prev_a.output_pkts,  # type: ignore[union-attr]
            link.device_b, link.iface_b, cur_b.input_pkts - prev_b.input_pkts,   # type: ignore[union-attr]
        )
        # B→A: B の output 増分 vs A の input 増分
        self._evaluate_direction(
            link, "B→A",
            link.device_b, link.iface_b, cur_b.output_pkts - prev_b.output_pkts,  # type: ignore[union-attr]
            link.device_a, link.iface_a, cur_a.input_pkts - prev_a.input_pkts,    # type: ignore[union-attr]
        )

    def _evaluate_direction(
        self,
        link: _LinkPair,
        direction: str,
        tx_dev: str,
        tx_iface: str,
        tx_delta: int,
        rx_dev: str,
        rx_iface: str,
        rx_delta: int,
    ) -> None:
        if tx_delta <= 0:
            return  # 送信パケットなし → 比較不要

        loss_ratio = max(0.0, (tx_delta - rx_delta) / tx_delta)
        if loss_ratio < self._loss_threshold:
            return

        event = PacketLossEvent(
            link_label=link.label,
            direction=direction,
            tx_device=tx_dev,
            tx_iface=tx_iface,
            tx_delta=tx_delta,
            rx_device=rx_dev,
            rx_iface=rx_iface,
            rx_delta=rx_delta,
            loss_ratio=loss_ratio,
        )
        logger.warning(
            "パケットロス検出: %s %s 損失率=%.1f%% (TX=%d RX=%d)",
            link.label,
            direction,
            loss_ratio * 100,
            tx_delta,
            rx_delta,
        )
        asyncio.create_task(self._publish_alert(event), name="pld-alert")

    async def _publish_alert(self, event: PacketLossEvent) -> None:
        msg = AgentMessage(
            from_agent=_DETECTOR_AGENT_ID,
            to_agent="COORDINATOR",
            msg_type="alert",
            content=(
                f"[パケットロス検出] {event.link_label} 方向={event.direction} "
                f"損失率={event.loss_ratio:.1%} "
                f"({event.tx_device}/{event.tx_iface} TX={event.tx_delta} pkts → "
                f"{event.rx_device}/{event.rx_iface} RX={event.rx_delta} pkts)"
            ),
            payload={
                "type": "packet_loss",
                "link": event.link_label,
                "direction": event.direction,
                "tx_device": event.tx_device,
                "tx_iface": event.tx_iface,
                "tx_delta": event.tx_delta,
                "rx_device": event.rx_device,
                "rx_iface": event.rx_iface,
                "rx_delta": event.rx_delta,
                "loss_ratio": round(event.loss_ratio, 4),
            },
        )
        await self._bus.publish(_ALERT_TOPIC, msg)


# ---------------------------------------------------------------------------
# パーサー・ファクトリー
# ---------------------------------------------------------------------------

def _parse_counters(raw: str) -> _IfaceCounters:
    """show interfaces 出力からパケットカウンタを抽出する。"""
    m_in = _INPUT_PKTS_RE.search(raw)
    m_out = _OUTPUT_PKTS_RE.search(raw)
    return _IfaceCounters(
        input_pkts=int(m_in.group(1)) if m_in else 0,
        output_pkts=int(m_out.group(1)) if m_out else 0,
    )


def build_link_pairs(topology_data: dict) -> list[_LinkPair]:
    """topology.yaml の辞書からリンクペアリストを構築する。"""
    node_label_by_id: dict[str, str] = {}
    iface_label_by_node_iface: dict[tuple[str, str], str] = {}

    for node in topology_data.get("nodes", []):
        node_id: str = node["id"]
        node_label_by_id[node_id] = node["label"]
        for iface in node.get("interfaces", []):
            iface_label_by_node_iface[(node_id, iface["id"])] = iface.get("label", iface["id"])

    pairs: list[_LinkPair] = []
    for link in topology_data.get("links", []):
        n1_id = link.get("n1") or link.get("node_a", "")
        i1_id = link.get("i1") or link.get("interface_a", "")
        n2_id = link.get("n2") or link.get("node_b", "")
        i2_id = link.get("i2") or link.get("interface_b", "")

        label_a = node_label_by_id.get(n1_id, "")
        iface_a = iface_label_by_node_iface.get((n1_id, i1_id), i1_id)
        label_b = node_label_by_id.get(n2_id, "")
        iface_b = iface_label_by_node_iface.get((n2_id, i2_id), i2_id)

        if label_a and label_b:
            pairs.append(_LinkPair(label_a, iface_a, label_b, iface_b))

    return pairs
