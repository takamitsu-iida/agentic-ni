"""分散型トラブルシューティングのレポート生成。

ConversationRecorder でバスメッセージを記録し、
generate_report() で既存の reports/ 形式に合わせた Markdown を生成する。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from agentic_ni.distributed.bus import MessageBus
from agentic_ni.distributed.message import AgentMessage

if TYPE_CHECKING:
    from agentic_ni.distributed.orchestrator import AgentOrchestrator

_REPORTS_DIR = Path(__file__).parents[3] / "reports"


# ---------------------------------------------------------------------------
# ConversationRecorder
# ---------------------------------------------------------------------------

class ConversationRecorder:
    """バスを流れる全 AgentMessage を時系列で記録するオブザーバー。

    start() で network/agents/# を購読し、stop() で解除する。
    """

    def __init__(self, bus: MessageBus) -> None:
        self._bus = bus
        self._log: list[tuple[str, AgentMessage]] = []  # (topic, message)

    async def start(self) -> None:
        await self._bus.subscribe("network/agents/#", self._on_message)

    async def stop(self) -> None:
        await self._bus.unsubscribe("network/agents/#", self._on_message)

    async def _on_message(self, topic: str, msg: AgentMessage) -> None:
        self._log.append((topic, msg))

    def get_log(self) -> list[tuple[str, AgentMessage]]:
        return list(self._log)

    def format_conversation(self) -> str:
        """エージェント間対話を時系列テキストで返す。"""
        if not self._log:
            return "（メッセージなし）"
        lines: list[str] = []
        for topic, msg in self._log:
            ts = msg.timestamp.astimezone().strftime("%H:%M:%S")
            direction = (
                f"{msg.from_agent} → {msg.to_agent}"
                if msg.to_agent != "ALL"
                else f"{msg.from_agent} → [BROADCAST]"
            )
            lines.append(f"[{ts}] **{direction}** ({msg.msg_type})")
            lines.append(f"> {msg.content}")
            lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# レポート生成
# ---------------------------------------------------------------------------

def generate_report(
    orchestrator: "AgentOrchestrator",
    recorder: ConversationRecorder,
    topology_path: str | Path | None = None,
    scenario_name: str = "トラブルシューティング",
) -> str:
    """エージェント間対話ログと診断結果を Markdown レポートとして返す。"""
    now = datetime.now()
    ts_str = now.strftime("%Y-%m-%d %H:%M:%S")

    # --- エージェント構成テーブル ---
    agent_rows: list[str] = []
    for agent_id, agent in orchestrator.get_all_agents().items():
        neighbors = ", ".join(agent._memory.neighbor_map.keys()) or "（なし）"
        open_incs = len(agent._memory.get_open_incidents())
        agent_rows.append(
            f"| {agent_id} | {agent.device_name} | {agent._device_type} "
            f"| {neighbors} | {open_incs} |"
        )
    agents_table = (
        "| エージェント | 装置名 | タイプ | 隣接エージェント | 未解決インシデント |\n"
        "|---|---|---|---|---|\n"
        + "\n".join(agent_rows)
    ) if agent_rows else "（エージェントなし）"

    # --- HUMAN エスカレーション抽出 ---
    human_items: list[dict] = []
    try:
        while not orchestrator.human_queue.empty():
            human_items.append(orchestrator.human_queue.get_nowait())
    except asyncio.QueueEmpty:
        pass

    if human_items:
        human_section = "\n\n".join(
            f"**{item.get('from_agent', '?')} より:**\n\n{item.get('content', '')}"
            for item in human_items
        )
    else:
        human_section = "（HUMAN エスカレーションなし）"

    # --- 全エージェントのステータス履歴サマリー ---
    status_sections: list[str] = []
    for agent_id, agent in orchestrator.get_all_agents().items():
        summary = agent._memory.recent_status_summary(n=20)
        if summary and summary != "（ステータス履歴なし）":
            status_sections.append(f"### {agent_id}\n```\n{summary}\n```")
    status_section = "\n\n".join(status_sections) if status_sections else "（履歴なし）"

    topo_line = f"`{topology_path}`" if topology_path else "（未指定）"

    report = (
        f"# 分散型トラブルシューティングレポート\n\n"
        f"**生成日時**: {ts_str}\n"
        f"**シナリオ**: {scenario_name}\n"
        f"**トポロジー**: {topo_line}\n\n"
        f"---\n\n"
        f"## エージェント構成\n\n"
        f"{agents_table}\n\n"
        f"---\n\n"
        f"## エージェント間対話ログ\n\n"
        f"{recorder.format_conversation()}\n\n"
        f"---\n\n"
        f"## HUMAN エスカレーション\n\n"
        f"{human_section}\n\n"
        f"---\n\n"
        f"## エージェント調査ログ（ステータス履歴）\n\n"
        f"{status_section}\n"
    )
    return report


def save_report(
    report: str,
    prefix: str = "ts",
) -> Path:
    """レポートを reports/ ディレクトリに保存してパスを返す。"""
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = _REPORTS_DIR / f"{prefix}-{timestamp}.md"
    path.write_text(report, encoding="utf-8")
    return path
