"""装置エージェント用プロンプトビルダー。

``prompts/device_agent_system.md`` をベーステンプレートとして読み込み、
装置固有の情報（装置名・隣接装置等）を埋め込んで返す。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentic_ni.distributed.memory import DesiredState, NeighborInfo

# distributed/prompts.py → agentic_ni → src → <project root> → prompts/
_BASE_PROMPT_PATH = Path(__file__).parents[3] / "prompts" / "device_agent_system.md"


def build_device_prompt(
    device_name: str,
    device_type: str,
    management_ip: str,
    neighbors: "dict[str, NeighborInfo] | None" = None,
    desired_state: "DesiredState | None" = None,
) -> str:
    """装置固有の情報をシステムプロンプトに埋め込んで返す。

    Args:
        device_name:    装置名（例: "R1"）。
        device_type:    装置タイプ（例: "router", "switch"）。
        management_ip:  管理 IP アドレス。
        neighbors:      隣接装置マップ（agent_id → NeighborInfo）。
        desired_state:  この装置の正常（期待）状態。

    Returns:
        str: 装置固有情報が埋め込まれたシステムプロンプト。

    Raises:
        FileNotFoundError: ベースプロンプトファイルが存在しない場合。
    """
    if not _BASE_PROMPT_PATH.exists():
        raise FileNotFoundError(
            f"装置エージェント用ベースプロンプトが見つかりません: {_BASE_PROMPT_PATH}"
        )
    template = _BASE_PROMPT_PATH.read_text(encoding="utf-8")

    neighbor_text = _build_neighbor_text(neighbors or {})
    desired_state_text = desired_state.to_text() if desired_state else "（期待状態の定義なし）"

    # str.replace を使用（Markdown 内の {} と競合しないため format() は使わない）
    return (
        template
        .replace("{device_name}", device_name)
        .replace("{device_type}", device_type)
        .replace("{management_ip}", management_ip or "未設定")
        .replace("{neighbors}", neighbor_text)
        .replace("{desired_state}", desired_state_text)
    )


def _build_neighbor_text(neighbors: "dict[str, NeighborInfo]") -> str:
    if not neighbors:
        return "  （隣接装置なし）"
    return "\n".join(
        f"  - {info.device_name} "
        f"(Agent ID: {agent_id}, I/F: {info.interface}, IP: {info.management_ip})"
        for agent_id, info in neighbors.items()
    )
