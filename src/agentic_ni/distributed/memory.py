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
class DesiredState:
    """装置の正常（期待）状態の定義。

    運用者がエージェント起動時に設定し、LLM が現在状態と比較する際の
    ベースラインとして使用する。
    """

    # インターフェースの期待状態: {"GigabitEthernet0/1": "up", ...}
    interfaces: dict[str, str] = field(default_factory=dict)
    # 期待するルーティングネイバー（プロトコル/ピア IP などの自由記述リスト）
    routing_neighbors: list[str] = field(default_factory=list)
    # 期待する到達先プレフィックスや経路（自由記述リスト）
    routes: list[str] = field(default_factory=list)
    # その他の期待状態（自由記述）
    notes: str = ""

    def to_text(self) -> str:
        """LLM に渡すテキスト形式に変換する。"""
        lines: list[str] = []
        if self.interfaces:
            lines.append("### 期待インターフェース状態")
            for intf, state in self.interfaces.items():
                lines.append(f"  - {intf}: {state}")
        if self.routing_neighbors:
            lines.append("### 期待ルーティングネイバー")
            for nb in self.routing_neighbors:
                lines.append(f"  - {nb}")
        if self.routes:
            lines.append("### 期待経路（到達性）")
            for r in self.routes:
                lines.append(f"  - {r}")
        if self.notes:
            lines.append("### その他の期待状態")
            lines.append(f"  {self.notes}")
        return "\n".join(lines) if lines else "（期待状態の定義なし）"


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

    def __init__(self, device_name: str, desired_state: DesiredState | None = None) -> None:
        self.device_name = device_name
        self.desired_state: DesiredState = desired_state or DesiredState()
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
