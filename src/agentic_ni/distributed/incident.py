"""Coordinator + Worker パターンで使用するインシデント関連データモデル。

EventCorrelator が複数の SYSLOG を束ねて NetworkIncident を生成し、
IncidentCoordinator が DeviceQueryRequest を発行して DeviceQueryResponse を集約する。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Literal

IncidentStatus = Literal["open", "investigating", "resolved", "duplicate"]


@dataclass
class NetworkIncident:
    """複数の SYSLOG イベントを相関させた 1 件のネットワーク障害。

    EventCorrelator が同一時間窓・同一リンク由来の SYSLOG を束ねて生成する。
    """

    incident_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    correlation_key: str = ""
    # topology の node id ペアを使用: "link:n0-n4" / フォールバック: "device:Spine1"
    affected_devices: list[str] = field(default_factory=list)
    # hostname リスト（例: ["Spine1", "Leaf3"]）
    syslog_events: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.monotonic)
    status: IncidentStatus = "open"


@dataclass
class DeviceQueryRequest:
    """IncidentCoordinator から DeviceAgent への調査依頼。"""

    incident_id: str
    target_device: str
    # 調査の文脈を渡す（例: "Spine1-Leaf3 間リンクダウン疑い。Spine1 側 Gi0/2 が down"）
    symptom_summary: str


@dataclass
class DeviceQueryResponse:
    """DeviceAgent から IncidentCoordinator への調査結果。"""

    incident_id: str
    from_device: str
    # LLM が整理した調査結果の要約
    findings: str
    # 実行した show コマンドと生出力のマッピング
    show_outputs: dict[str, str] = field(default_factory=dict)
    # タイムアウト・接続エラー等の場合 True
    error: bool = False
    error_detail: str = ""
