"""設計エージェント。

LLMを使って要件またはエラーログからCMLトポロジーYAMLと
機器コンフィグを生成する LangGraph ノード。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agentic_ni.llm import get_llm
from agentic_ni.state import AgentState

# ---------------------------------------------------------------------------
# 出力スキーマ（Pydantic v2）
# ---------------------------------------------------------------------------

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


class DesignOutput(BaseModel):
    """設計エージェントの構造化出力スキーマ。"""

    topology_yaml: str = Field(
        description="CMLに読み込ませるトポロジー定義（YAML文字列）。"
    )
    device_configs: dict[str, str] = Field(
        description="機器名をキー、コンフィグテキストを値とするマッピング。"
        "キーはトポロジーYAML内のノードlabelと一致させること。"
    )
    design_rationale: str = Field(
        description="設計意図・選択理由の簡潔な説明（ログ・デバッグ用）。"
    )


# ---------------------------------------------------------------------------
# プロンプト構築
# ---------------------------------------------------------------------------


def _load_system_prompt() -> str:
    """architect_system.md を読み込んで返す。"""
    path = _PROMPTS_DIR / "architect_system.md"
    return path.read_text(encoding="utf-8")


def _build_messages(state: AgentState) -> list[dict[str, str]]:
    """Stateからチャットメッセージリストを組み立てる。

    * error_log が空 → 要件からゼロ設計
    * error_log に内容あり → 差分修正モード
    """
    system_prompt = _load_system_prompt()

    if state.get("error_log"):
        # --- 差分修正モード ---
        user_content = (
            "## 修正依頼\n\n"
            "前回の設計に対して検証エージェントから以下のエラーが報告されました。\n"
            "原因箇所を特定し、最小限の修正を加えた設計を出力してください。\n\n"
            "### 元の要件\n"
            f"{state['requirement']}\n\n"
            "### 前回のトポロジーYAML\n"
            f"```yaml\n{state.get('topology_yaml', '(なし)')}\n```\n\n"
            "### 前回の機器コンフィグ\n"
            + "\n".join(
                f"**{dev}**:\n```\n{cfg}\n```"
                for dev, cfg in state.get("device_configs", {}).items()
            )
            + "\n\n### エラーログ（検証エージェントの推論含む）\n"
            f"```\n{state['error_log']}\n```\n\n"
            "修正点のみ変更し、問題のない箇所はそのまま維持してください。"
        )
    else:
        # --- ゼロ設計モード ---
        user_content = (
            "## 設計依頼\n\n"
            "以下の要件を満たすネットワークを設計してください。\n\n"
            "### 要件\n"
            f"{state['requirement']}\n\n"
            "CMLトポロジーYAMLと各機器の初期コンフィグを生成してください。"
        )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


# ---------------------------------------------------------------------------
# エージェント実行
# ---------------------------------------------------------------------------


def run(state: AgentState) -> dict[str, Any]:
    """設計エージェントのLangGraphノード関数。

    要件またはエラーログを受け取り、LLMを呼び出して
    トポロジーYAMLと機器コンフィグを生成して返す。

    Args:
        state: 現在のエージェントステート。

    Returns:
        dict: AgentState の更新差分。
              topology_yaml / device_configs / error_log(クリア) を含む。
    """
    llm = get_llm()

    # 構造化出力モード
    structured_llm = llm.with_structured_output(DesignOutput)

    messages = _build_messages(state)
    result: DesignOutput = structured_llm.invoke(messages)

    return {
        "topology_yaml": result.topology_yaml,
        "device_configs": result.device_configs,
        # 修正設計を出力したらエラーログをクリア（次の検証で上書きされる）
        "error_log": "",
    }
