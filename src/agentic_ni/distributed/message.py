"""エージェント間メッセージのスキーマ定義。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


class AgentMessage(BaseModel):
    """エージェント間で交換されるメッセージの共通スキーマ。

    出力フォーマット（概念設計書 §4 準拠）:
      - to_agent == "ALL"   → ブロードキャスト（全エージェント）
      - to_agent == "HUMAN" → 人間の管理者へのエスカレーション
      - to_agent == <ID>    → 特定エージェントへのダイレクトメッセージ
    """

    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    from_agent: str
    """送信元 Agent ID（例: "Agent-R1"）。"""

    to_agent: str
    """宛先 Agent ID。"ALL" でブロードキャスト、"HUMAN" で管理者エスカレーション。"""

    msg_type: Literal["query", "response", "alert", "report"]
    """メッセージ種別。"""

    content: str
    """自然言語による本文。"""

    payload: dict[str, Any] = Field(default_factory=dict)
    """構造化データ（診断結果・show コマンド出力等）。"""

    hop_count: int = 0
    """エージェント間転送回数。ループ検知に使用。"""

    origin_message_id: str | None = None
    """転送チェーンの根源 message_id。最初の送信者は None のまま送り、転送側が元の message_id をセットする。"""
