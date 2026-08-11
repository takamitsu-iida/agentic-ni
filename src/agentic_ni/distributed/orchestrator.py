"""オーケストレーター — topology.yaml から全 DeviceAgent を起動・管理する。

使用方法（CLI）::

    agentic-ni-ts --topology configs/demo2/topology.yaml --mock-tools
    agentic-ni-ts --topology configs/demo2/topology.yaml --mock-tools \\
                  --inject-syslog "R1:%OSPF-5-ADJCHG: Nbr 10.0.0.2 changed to DOWN"

Human 承認ワークフロー:
    エージェントが ``TO: HUMAN | MSG: ...`` を出力すると human_queue に積まれる。
    CLIの承認ループが標準入力でユーザーに確認し、結果をログに出力する。
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from agentic_ni.distributed.bus import MessageBus, create_bus
from agentic_ni.distributed.coordinator import IncidentCoordinator
from agentic_ni.distributed.correlator import EventCorrelator
from agentic_ni.distributed.device_agent import DeviceAgent, SyslogEvent
from agentic_ni.distributed.device_tools import MockDeviceToolkit, create_device_toolkit
from agentic_ni.distributed.memory import DeviceMemory, NeighborInfo
from agentic_ni.logger import get_logger

logger = get_logger(__name__)

# node_definition → 人間可読な装置タイプへのマッピング
_NODE_DEFINITION_MAP: dict[str, str] = {
    "iosv": "router",
    "iosv_l2": "switch",
    "iosvl2": "switch",
    "iol-xe": "router",
    "iol_xe": "router",
    "nxosv": "switch",
    "nxosv9000": "switch",
    "asav": "firewall",
    "alpine": "host",
    "desktop": "host",
    "server": "host",
    "ubuntu": "host",
    "external_connector": "host",
    "unmanaged_switch": "switch",
}


# ---------------------------------------------------------------------------
# トポロジーパーサー
# ---------------------------------------------------------------------------

@dataclass
class NodeInfo:
    """topology.yaml の 1 ノード分の情報。"""

    id: str               # CML ノード ID（例: "n0"）
    label: str            # 表示名（例: "R1"）
    node_definition: str  # CML ノード定義（例: "iosv"）
    interfaces: dict[str, str] = field(default_factory=dict)  # iface_id → iface_label

    @property
    def device_type(self) -> str:
        return _NODE_DEFINITION_MAP.get(self.node_definition.lower(), "router")

    @property
    def agent_id(self) -> str:
        return f"Agent-{self.label}"


def parse_topology(data: dict) -> tuple[list[NodeInfo], dict[str, list[NeighborInfo]]]:
    """topology.yaml の辞書からノード情報と隣接マップを生成する。

    Returns:
        (node_infos, neighbor_map)
        neighbor_map: {node_id → list[NeighborInfo]}  隣接装置の一覧（装置ごと）
    """
    nodes_raw = data.get("nodes", [])
    node_infos: list[NodeInfo] = []
    node_by_id: dict[str, NodeInfo] = {}

    for n in nodes_raw:
        interfaces = {
            iface["id"]: iface.get("label", iface["id"])
            for iface in n.get("interfaces", [])
        }
        info = NodeInfo(
            id=n["id"],
            label=n["label"],
            node_definition=n.get("node_definition", "iosv"),
            interfaces=interfaces,
        )
        node_infos.append(info)
        node_by_id[info.id] = info

    # リンクから隣接マップを構築
    neighbor_map: dict[str, list[NeighborInfo]] = {n.id: [] for n in node_infos}

    for link in data.get("links", []):
        n1_id = link.get("n1") or link.get("node_a")
        i1_id = link.get("i1") or link.get("interface_a", "")
        n2_id = link.get("n2") or link.get("node_b")
        i2_id = link.get("i2") or link.get("interface_b", "")

        n1 = node_by_id.get(n1_id)
        n2 = node_by_id.get(n2_id)
        if not n1 or not n2:
            logger.warning("リンクのノードが見つかりません: %s <-> %s", n1_id, n2_id)
            continue

        # n1 にとって n2 は隣接（n1 の i1 インターフェース経由）
        neighbor_map[n1_id].append(NeighborInfo(
            agent_id=n2.agent_id,
            device_name=n2.label,
            interface=n1.interfaces.get(i1_id, i1_id),
            management_ip="",
        ))
        # n2 にとって n1 は隣接（n2 の i2 インターフェース経由）
        neighbor_map[n2_id].append(NeighborInfo(
            agent_id=n1.agent_id,
            device_name=n1.label,
            interface=n2.interfaces.get(i2_id, i2_id),
            management_ip="",
        ))

    return node_infos, neighbor_map


# ---------------------------------------------------------------------------
# AgentOrchestrator
# ---------------------------------------------------------------------------

class AgentOrchestrator:
    """topology.yaml を読み込み、DeviceAgent を生成・起動・管理するオーケストレーター。

    Args:
        bus:              使用する MessageBus（connect 済みであること）。
        llm:              全エージェントで共用する LLM インスタンス。None で get_llm() を使用。
        toolkit_factory:  ``(device_name: str) -> DeviceToolkit`` を返す callable。
                          None の場合はツールなしでエージェントを起動する。
        readonly:         Write ツールを無効化するフラグ（デフォルト True）。
        log_poll_interval: 各エージェントが show logging をポーリングする間隔（秒）。
                           0.0 の場合はログポーリングを無効化する（デフォルト）。
    """

    def __init__(
        self,
        bus: MessageBus,
        llm: Any | None = None,
        toolkit_factory: Callable[[str], Any] | None = None,
        readonly: bool = True,
        log_poll_interval: float = 0.0,
    ) -> None:
        self._bus = bus
        self._llm = llm
        self._toolkit_factory = toolkit_factory
        self._readonly = readonly
        self._log_poll_interval = log_poll_interval
        self._agents: dict[str, DeviceAgent] = {}  # agent_id → DeviceAgent
        self.human_queue: asyncio.Queue = asyncio.Queue()
        # Correlator / Coordinator は start_from_topology() で初期化する
        self._correlator: EventCorrelator | None = None
        self._coordinator: IncidentCoordinator | None = None

    # ------------------------------------------------------------------
    # 起動・停止
    # ------------------------------------------------------------------

    async def start_from_topology(self, topology_path: str | Path) -> None:
        """topology.yaml を読み込んで全 DeviceAgent を起動する。"""
        path = Path(topology_path)
        if not path.exists():
            raise FileNotFoundError(f"topology.yaml が見つかりません: {path}")

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        node_infos, neighbor_map = parse_topology(data)

        if not node_infos:
            logger.warning("topology.yaml にノードが定義されていません: %s", path)
            return

        logger.info(
            "トポロジー '%s' を読み込みました: %d ノード",
            data.get("lab", {}).get("title", path.name),
            len(node_infos),
        )

        for node_info in node_infos:
            await self._start_agent(node_info, neighbor_map.get(node_info.id, []))

        logger.info("%d 台のエージェントを起動しました。", len(self._agents))

        # device_name → DeviceAgent の registry を構築
        device_registry = {agent.device_name: agent for agent in self._agents.values()}
        self._coordinator = IncidentCoordinator(
            agent_registry=device_registry,
            llm=self._llm,
            human_queue=self.human_queue,
        )
        self._correlator = EventCorrelator(
            topology_data=data,
            on_incident=self._coordinator.handle_incident,
        )
        logger.info("EventCorrelator / IncidentCoordinator を初期化しました。")

    async def _start_agent(
        self, node_info: NodeInfo, neighbors: list[NeighborInfo]
    ) -> DeviceAgent:
        memory = DeviceMemory(node_info.label)
        for neighbor in neighbors:
            memory.set_neighbor(neighbor.agent_id, neighbor)

        toolkit = None
        tools = []
        if self._toolkit_factory is not None:
            toolkit = self._toolkit_factory(node_info.label)
            tools = toolkit.get_tools()

        agent = DeviceAgent(
            device_name=node_info.label,
            agent_id=node_info.agent_id,
            bus=self._bus,
            memory=memory,
            device_type=node_info.device_type,
            llm=self._llm,
            tools=tools,
            human_queue=self.human_queue,
        )

        if self._log_poll_interval > 0 and toolkit is not None and hasattr(toolkit, "make_log_poller"):
            agent._log_poller = toolkit.make_log_poller(agent, poll_interval=self._log_poll_interval)

        await agent.start()
        self._agents[node_info.agent_id] = agent
        logger.info(
            "  起動: %s (%s) — 隣接: %s%s",
            node_info.agent_id,
            node_info.device_type,
            [n.agent_id for n in neighbors] or "なし",
            f"  [ログ監視: {self._log_poll_interval}s]" if self._log_poll_interval > 0 else "",
        )
        return agent

    async def stop_all(self) -> None:
        """Correlator をフラッシュして全 DeviceAgent を停止する。"""
        if self._correlator is not None:
            await self._correlator.flush_all()
        for agent in list(self._agents.values()):
            await agent.stop()
        self._agents.clear()
        logger.info("全エージェントを停止しました。")

    # ------------------------------------------------------------------
    # エージェント参照
    # ------------------------------------------------------------------

    def get_agent(self, agent_id: str) -> DeviceAgent:
        """エージェント ID で DeviceAgent を取得する。"""
        if agent_id not in self._agents:
            raise KeyError(f"エージェントが見つかりません: {agent_id!r}")
        return self._agents[agent_id]

    def get_all_agents(self) -> dict[str, DeviceAgent]:
        return dict(self._agents)

    def agent_count(self) -> int:
        return len(self._agents)

    # ------------------------------------------------------------------
    # SYSLOG 受信（SyslogServer / ubuntu_cli からの呼び出し）
    # ------------------------------------------------------------------

    async def receive_syslog(
        self,
        source_hostname: str,
        raw_msg: str,
        severity: str = "unknown",
    ) -> None:
        """SYSLOG を EventCorrelator に渡して相関・Incident 化する。"""
        if self._correlator is None:
            logger.warning("Correlator 未初期化。start_from_topology 実行後に呼び出してください。")
            return
        await self._correlator.receive_syslog(source_hostname, raw_msg)

    async def broadcast_syslog_to_all(
        self,
        source_hostname: str,
        raw_msg: str,
        severity: str = "unknown",
    ) -> int:
        """非推奨: receive_syslog() を使用してください。将来廃止予定。"""
        logger.debug(
            "broadcast_syslog_to_all は非推奨です。receive_syslog を使用してください。"
        )
        await self.receive_syslog(source_hostname, raw_msg, severity)
        return len(self._agents)

    # ------------------------------------------------------------------
    # Human 承認ワークフロー
    # ------------------------------------------------------------------

    async def run_approval_loop(self, shutdown_event: asyncio.Event) -> None:
        """設定変更承認リクエストを監視して CLI でユーザーに確認する。

        非対話環境（stdin が TTY でない場合）は自動で拒否する。
        """
        while not shutdown_event.is_set():
            try:
                request = await asyncio.wait_for(
                    self.human_queue.get(), timeout=0.5
                )
            except asyncio.TimeoutError:
                continue

            await self._handle_approval(request)
            self.human_queue.task_done()

    async def _handle_approval(self, request: dict) -> None:
        req_type = request.get("type", "unknown")
        device = request.get("device", "unknown")
        content = request.get("content") or request.get("commands", "")

        if req_type == "config_change_request":
            print(f"\n{'='*60}")
            print(f"[Human 承認リクエスト] 設定変更 — デバイス: {device}")
            print(f"{'='*60}")
            print(content)
            print(f"{'='*60}")
            answer = await _ask_yes_no(f"{device} への設定変更を承認しますか？ [y/N]: ")
            if answer:
                logger.info("[承認] %s の設定変更を承認しました。", device)
                # 実際の適用は TS-5 以降で実装（ここではログのみ）
            else:
                logger.info("[拒否] %s の設定変更を拒否しました。", device)
        else:
            # 一般的な HUMAN エスカレーション
            from_agent = request.get("from_agent", "unknown")
            print(f"\n[{from_agent} → HUMAN] {content}")


# ---------------------------------------------------------------------------
# CLI ヘルパー
# ---------------------------------------------------------------------------

async def _ask_yes_no(prompt: str) -> bool:
    """標準入力が TTY の場合はプロンプトを表示して y/n を受け取る。非 TTY は False。"""
    if not sys.stdin.isatty():
        return False
    try:
        answer = await asyncio.to_thread(input, prompt)
        return answer.strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def _print_status(orchestrator: AgentOrchestrator) -> None:
    print(f"\n{'='*60}")
    print(f"  稼働中エージェント: {orchestrator.agent_count()} 台")
    for agent_id, agent in orchestrator.get_all_agents().items():
        neighbors = list(agent._memory.neighbor_map.keys())
        print(f"  - {agent_id} ({agent._device_type}) 隣接: {neighbors or '(なし)'}")
    print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------

def main() -> None:
    """agentic-ni-ts コマンドのエントリポイント。"""
    import argparse

    parser = argparse.ArgumentParser(
        description="自律分散型トラブルシューティングエージェント",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "例:\n"
            "  agentic-ni-ts --topology configs/demo2/topology.yaml --mock-tools\n"
            "  agentic-ni-ts --topology configs/demo2/topology.yaml --mock-tools \\\n"
            "                --inject-syslog 'R1:%%OSPF-5-ADJCHG: Nbr changed to DOWN'\n"
        ),
    )
    parser.add_argument(
        "--topology", required=True,
        help="topology.yaml のパス（例: configs/demo2/topology.yaml）",
    )
    parser.add_argument(
        "--bus", default="memory", choices=["memory", "mqtt", "nats"],
        help="メッセージバスのバックエンド（デフォルト: memory）",
    )
    parser.add_argument(
        "--bus-host", default="localhost",
        help="MQTT/NATS ブローカーのホスト名（--bus=mqtt/nats 時に使用）",
    )
    parser.add_argument(
        "--mock-tools", action="store_true",
        help="モックツールを使用する（pyATS/CML 不要）",
    )
    parser.add_argument(
        "--readonly", action="store_true", default=True,
        help="Write ツールを無効化する（デフォルト: True）",
    )
    parser.add_argument(
        "--inject-syslog",
        help=(
            "起動後に特定エージェントへ syslog イベントを注入する。"
            "書式: 'DeviceLabel:syslogメッセージ'（例: 'R1:%%LINK-3-UPDOWN: Gi0/0 down'）"
        ),
    )
    args = parser.parse_args()

    asyncio.run(_async_main(args))


async def _async_main(args: Any) -> None:
    """実際の非同期メイン処理。"""
    # Bus の生成と接続
    bus_kwargs: dict = {}
    if args.bus == "mqtt":
        bus_kwargs = {"host": args.bus_host}
    elif args.bus == "nats":
        bus_kwargs = {"url": f"nats://{args.bus_host}:4222"}

    bus = create_bus(args.bus, **bus_kwargs)
    await bus.connect()

    # ツールキットファクトリーの設定
    toolkit_factory = None
    if args.mock_tools:
        def toolkit_factory(device_name: str):
            return MockDeviceToolkit(device_name, readonly=args.readonly)

    # オーケストレーター起動
    orchestrator = AgentOrchestrator(
        bus=bus,
        toolkit_factory=toolkit_factory,
        readonly=args.readonly,
    )

    shutdown_event = asyncio.Event()

    def _handle_sigint(signum, frame):
        print("\n  Ctrl+C を受信しました。シャットダウンしています...")
        shutdown_event.set()

    signal.signal(signal.SIGINT, _handle_sigint)

    try:
        await orchestrator.start_from_topology(args.topology)
        _print_status(orchestrator)

        # テスト用 syslog イベントの注入
        if args.inject_syslog:
            await _inject_syslog(orchestrator, args.inject_syslog)

        # Human 承認ループとシャットダウン待機を並行実行
        await asyncio.gather(
            orchestrator.run_approval_loop(shutdown_event),
            _wait_for_shutdown(shutdown_event),
        )

    finally:
        await orchestrator.stop_all()
        await bus.close()
        print("  シャットダウン完了。")


async def _inject_syslog(orchestrator: AgentOrchestrator, spec: str) -> None:
    """'DeviceLabel:message' 形式で指定されたエージェントに syslog イベントを注入する。"""
    if ":" not in spec:
        logger.error("--inject-syslog の書式エラー: 'DeviceLabel:message' 形式で指定してください。")
        return
    device_label, _, message = spec.partition(":")
    agent_id = f"Agent-{device_label.strip()}"
    try:
        agent = orchestrator.get_agent(agent_id)
        await agent.inject_event(SyslogEvent(raw_text=message.strip()))
        logger.info("syslog イベントを %s に注入しました: %s", agent_id, message[:60])
    except KeyError:
        logger.error("エージェントが見つかりません: %s", agent_id)


async def _wait_for_shutdown(shutdown_event: asyncio.Event) -> None:
    """シャットダウンシグナルを待つ（非対話環境では stdin の EOF も検知する）。"""
    if sys.stdin.isatty():
        print("  エージェント稼働中... 終了するには Ctrl+C を押してください。\n")
    await shutdown_event.wait()
