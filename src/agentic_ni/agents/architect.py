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


class DeviceConfig(BaseModel):
    """1台分の機器コンフィグ。dict[str, str] は LLM の JSON schema で扱いにくいためリスト形式で受け取る。"""

    device_name: str = Field(
        description="ノードの label と一致するデバイス名（例: 'R1', 'R2'）。"
    )
    config_text: str = Field(
        description="デバイスの設定テキスト（IOS 形式）。"
    )


class DesignOutput(BaseModel):
    """設計エージェントの構造化出力スキーマ。"""

    topology_yaml: str = Field(
        description="CMLに読み込ませるトポロジー定義（YAML文字列）。"
    )
    device_configs: list[DeviceConfig] = Field(
        description="機器ごとのコンフィグリスト。"
        "各要素は device_name（ノードlabelと一致）と config_text を持つ。"
    )
    design_rationale: str = Field(
        description="設計意図・選択理由の簡潔な説明（ログ・デバッグ用）。"
    )


# ---------------------------------------------------------------------------
# プロンプト構築
# ---------------------------------------------------------------------------


def _load_system_prompt(prompt_set: str = "default") -> str:
    """指定されたプロンプトセットの architect_system.md を読み込んで返す。"""
    path = _PROMPTS_DIR / prompt_set / "architect_system.md"
    if not path.exists():
        raise FileNotFoundError(
            f"プロンプトセット '{prompt_set}' が見つかりません: {path}\n"
            f"利用可能なセット: {list_prompt_sets()}"
        )
    return path.read_text(encoding="utf-8")


def list_prompt_sets() -> list[str]:
    """利用可能なプロンプトセット一覧を返す。"""
    return sorted(
        d.name for d in _PROMPTS_DIR.iterdir()
        if d.is_dir() and (d / "architect_system.md").exists()
    )


def _build_messages(state: AgentState) -> list[dict[str, str]]:
    """Stateからチャットメッセージリストを組み立てる。

    * error_log が空 → 要件からゼロ設計
    * error_log に内容あり → 差分修正モード
    """
    system_prompt = _load_system_prompt(state.get("prompt_set", "default"))

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

    # 構造化出力モード（function_calling: dict[str, str] など strict 非対応型を含むため）
    structured_llm = llm.with_structured_output(DesignOutput, method="function_calling")

    messages = _build_messages(state)
    result: DesignOutput = structured_llm.invoke(messages)

    if state.get("error_log"):
        print(f"  【設計方針】 {result.design_rationale}", flush=True)

    return {
        "topology_yaml": result.topology_yaml,
        "device_configs": {dc.device_name: dc.config_text for dc in result.device_configs},
        # 修正設計を出力したらエラーログをクリア（次の検証で上書きされる）
        "error_log": "",
    }
