"""装置エージェントのローカルメモリ。

各 DeviceAgent が保持する装置固有の状態管理クラス群。
グローバルな AgentState とは独立し、装置単位で完結する。
"""

from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

# 10 秒ポーリングを想定した直近 1 時間分のスナップショット上限
_STATUS_HISTORY_MAX = 360


@dataclass
class StatusSnapshot:
    """装置ステータスの 1 スナップショット。"""

    timestamp: datetime
    source: str   # "poll" / "syslog" / "bus" / "llm"
    content: str  # raw テキスト（show コマンド出力・syslog メッセージ等）


@dataclass
class NeighborInfo:
    """隣接装置の情報（トポロジーマップ）。"""

    agent_id: str       # 隣接エージェントの ID（例: "Agent-R2"）
    device_name: str    # 隣接装置名
    interface: str      # 自装置側の接続インターフェース（例: "GigabitEthernet0/1"）
    management_ip: str  # 隣接装置の管理 IP アドレス


@dataclass
class Incident:
    """進行中インシデントの 1 件。"""

    incident_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    description: str = ""
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: Literal["open", "investigating", "resolved"] = "open"


class DeviceMemory:
    """装置エージェントのローカルメモリ。

    直近のステータス履歴・隣接装置マップ・進行中インシデントを保持する。
    スレッドセーフではない（asyncio シングルスレッド内での使用を前提とする）。
    """

    def __init__(self, device_name: str) -> None:
        self.device_name = device_name
        self.status_history: deque[StatusSnapshot] = deque(maxlen=_STATUS_HISTORY_MAX)
        self.neighbor_map: dict[str, NeighborInfo] = {}  # agent_id → NeighborInfo
        self.active_incidents: list[Incident] = []

    # ------------------------------------------------------------------
    # ステータス履歴
    # ------------------------------------------------------------------

    def add_status(self, source: str, content: str) -> None:
        """ステータスをヒストリーに追記する。"""
        self.status_history.append(StatusSnapshot(
            timestamp=datetime.now(timezone.utc),
            source=source,
            content=content,
        ))

    def recent_status_summary(self, n: int = 10) -> str:
        """直近 n 件のステータスを結合した文字列を返す（LLM コンテキスト用）。"""
        recent = list(self.status_history)[-n:]
        if not recent:
            return "（ステータス履歴なし）"
        return "\n".join(
            f"[{s.timestamp.strftime('%H:%M:%S')} {s.source}] {s.content}"
            for s in recent
        )

    # ------------------------------------------------------------------
    # 隣接装置マップ
    # ------------------------------------------------------------------

    def set_neighbor(self, agent_id: str, info: NeighborInfo) -> None:
        self.neighbor_map[agent_id] = info

    def neighbors_summary(self) -> str:
        """隣接装置一覧を文字列で返す（プロンプト埋め込み用）。"""
        if not self.neighbor_map:
            return "  （隣接装置なし）"
        return "\n".join(
            f"  - {info.device_name} "
            f"(Agent ID: {agent_id}, I/F: {info.interface}, IP: {info.management_ip})"
            for agent_id, info in self.neighbor_map.items()
        )

    # ------------------------------------------------------------------
    # インシデント管理
    # ------------------------------------------------------------------

    def open_incident(self, description: str) -> Incident:
        """新規インシデントをオープンして返す。"""
        incident = Incident(description=description)
        self.active_incidents.append(incident)
        return incident

    def update_incident_status(
        self, incident_id: str, status: Literal["investigating", "resolved"]
    ) -> None:
        for inc in self.active_incidents:
            if inc.incident_id == incident_id:
                inc.status = status
                return

    def get_open_incidents(self) -> list[Incident]:
        return [i for i in self.active_incidents if i.status != "resolved"]
