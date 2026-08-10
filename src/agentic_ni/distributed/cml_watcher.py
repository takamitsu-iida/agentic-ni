"""CML 状態ポーリングによる自動障害検知ウォッチャー。

lab.sync_states() を定期実行してリンク/ノードの状態変化を検知し、
対応するエージェントに SyslogEvent を自動注入する。

使用方法::

    watcher = CMLStateWatcher(lab_id="abc123", orchestrator=orch, poll_interval=3.0)
    await watcher.start()
    # ... CML でリンクを停止するとエージェントが自動的に動き出す
    await watcher.stop()
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

from agentic_ni.distributed.device_agent import SyslogEvent
from agentic_ni.distributed.orchestrator import AgentOrchestrator

logger = logging.getLogger(__name__)

# CML リンク/ノード DOWN → syslog テンプレート
_LINK_DOWN_TMPL = "%LINK-3-UPDOWN: Interface {iface}, changed state to down"
_LINK_UP_TMPL   = "%LINK-3-UPDOWN: Interface {iface}, changed state to up"

# CML が返す "停止中" とみなすステート値
_DOWN_STATES = frozenset({"STOPPED", "DEFINED_ON_CORE"})
_UP_STATES   = frozenset({"STARTED", "BOOTED"})

_USE_COLOR = sys.stdout.isatty()
_YELLOW = "\033[93m"
_RED    = "\033[91m"
_RESET  = "\033[0m"
_DIM    = "\033[2m"


def _c(code: str, text: str) -> str:
    return f"{code}{text}{_RESET}" if _USE_COLOR else text


class CMLStateWatcher:
    """CML のリンク/ノード状態を非同期でポーリングし、変化を AgentOrchestrator に注入する。

    Args:
        lab_id:        監視対象 CML ラボの ID。
        orchestrator:  起動済みの AgentOrchestrator インスタンス。
        poll_interval: ポーリング間隔（秒）。デフォルト 3.0 秒。
        on_change:     状態変化時に呼ばれるコールバック (device_label, syslog_msg) → None。
    """

    def __init__(
        self,
        lab_id: str,
        orchestrator: AgentOrchestrator,
        poll_interval: float = 3.0,
        on_change: Any | None = None,
    ) -> None:
        self._lab_id = lab_id
        self._orchestrator = orchestrator
        self._poll_interval = poll_interval
        self._on_change = on_change
        self._lab: Any = None
        self._link_states: dict[str, str] = {}  # link_id → CML state
        self._node_states: dict[str, str] = {}  # node_label → CML state
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # 公開 API
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """CML に接続して初期スナップショットを取得し、ポーリングループを起動する。"""
        self._lab = await asyncio.to_thread(self._connect)
        await asyncio.to_thread(self._lab.sync_states)
        self._snapshot()
        self._task = asyncio.create_task(self._poll_loop(), name="cml-watcher")
        logger.info(
            "CMLStateWatcher 起動: lab_id=%s  poll_interval=%.1fs  "
            "links=%d  nodes=%d",
            self._lab_id,
            self._poll_interval,
            len(self._link_states),
            len(self._node_states),
        )

    async def stop(self) -> None:
        """ポーリングループを停止する。"""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # ------------------------------------------------------------------
    # 内部実装
    # ------------------------------------------------------------------

    def _connect(self) -> Any:
        from agentic_ni.tools.cml_tools import _get_client, _get_lab  # noqa: PLC0415
        client = _get_client()
        return _get_lab(client, self._lab_id)

    def _snapshot(self) -> None:
        for link in self._lab.links():
            self._link_states[link.id] = link.state
        for node in self._lab.nodes():
            self._node_states[node.label] = node.state

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self._poll_interval)
            try:
                await asyncio.to_thread(self._lab.sync_states)
                await self._detect_changes()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("CML ポーリングエラー（次回も継続）: %s", exc)

    async def _detect_changes(self) -> None:
        for link in self._lab.links():
            prev = self._link_states.get(link.id)
            curr = link.state
            if prev is not None and prev != curr:
                await self._on_link_change(link, prev, curr)
            self._link_states[link.id] = curr

        for node in self._lab.nodes():
            prev = self._node_states.get(node.label)
            curr = node.state
            if prev is not None and prev != curr:
                await self._on_node_change(node, prev, curr)
            self._node_states[node.label] = curr

    async def _on_link_change(self, link: Any, prev: str, curr: str) -> None:
        if curr in _DOWN_STATES:
            tmpl, severity = _LINK_DOWN_TMPL, "3"
        elif curr in _UP_STATES and prev in _DOWN_STATES:
            tmpl, severity = _LINK_UP_TMPL, "5"
        else:
            return

        node_a  = link.node_a.label
        iface_a = link.interface_a.label
        node_b  = link.node_b.label
        iface_b = link.interface_b.label

        logger.info(
            "リンク状態変化: %s %s → %s  (%s:%s <-> %s:%s)",
            link.id, prev, curr, node_a, iface_a, node_b, iface_b,
        )
        await self._inject(node_a, tmpl.format(iface=iface_a), severity)
        await self._inject(node_b, tmpl.format(iface=iface_b), severity)

    async def _on_node_change(self, node: Any, prev: str, curr: str) -> None:
        if curr not in _DOWN_STATES:
            return

        stopped_label = node.label
        logger.info("ノード状態変化: %s %s → %s", stopped_label, prev, curr)

        # 停止ノードに繋がっている全隣接ノードへ LINK-3-UPDOWN を注入
        for link in self._lab.links():
            if link.node_a.label == stopped_label:
                neighbor_label = link.node_b.label
                neighbor_iface = link.interface_b.label
            elif link.node_b.label == stopped_label:
                neighbor_label = link.node_a.label
                neighbor_iface = link.interface_a.label
            else:
                continue
            await self._inject(
                neighbor_label,
                _LINK_DOWN_TMPL.format(iface=neighbor_iface),
                severity="3",
            )

    async def _inject(self, device_label: str, syslog_msg: str, severity: str) -> None:
        agent_id = f"Agent-{device_label}"
        try:
            agent = self._orchestrator.get_agent(agent_id)
        except KeyError:
            logger.debug("エージェント未登録のためスキップ: %s", agent_id)
            return

        # ターミナル表示
        print(
            f"\n  {_c(_YELLOW, '🔔 CML 検知')} "
            f"[{_c(_YELLOW, device_label)}] "
            f"{_c(_DIM, syslog_msg)}"
        )

        await agent.inject_event(SyslogEvent(raw_text=syslog_msg, severity=severity))

        if self._on_change is not None:
            self._on_change(device_label, syslog_msg)
