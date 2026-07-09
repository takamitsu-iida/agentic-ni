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


class ConfigOnlyOutput(BaseModel):
    """コンフィグのみ生成モード用の構造化出力スキーマ（トポロジーは提供済み）。"""

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


def _load_system_prompt(prompt_set: str = "demo") -> str:
    """architect プロンプトを構築して返す。

    読み込み方針:
    1. prompts/architect_system.md をベースとして読み込む
    2. prompts/<set>/architect.md が存在すれば、セット固有ヒントとして末尾に結合する

    後方互換:
    - prompts/<set>/architect_system.md が存在する場合は単独使用（旧形式）
    """
    base_path = _PROMPTS_DIR / "architect_system.md"
    set_specific_path = _PROMPTS_DIR / prompt_set / "architect.md"
    set_legacy_path = _PROMPTS_DIR / prompt_set / "architect_system.md"

    # 後方互換: セット内に architect_system.md があれば単独使用
    if set_legacy_path.exists():
        return set_legacy_path.read_text(encoding="utf-8")

    if not base_path.exists():
        raise FileNotFoundError(
            f"architect_system.md が見つかりません: {base_path}"
        )
    base = base_path.read_text(encoding="utf-8")

    if set_specific_path.exists():
        specific = set_specific_path.read_text(encoding="utf-8")
        return f"{base}\n\n---\n\n{specific}"

    return base


def list_prompt_sets() -> list[str]:
    """利用可能なプロンプトセット一覧を返す。requirement.md を持つディレクトリを対象とする。"""
    return sorted(
        d.name for d in _PROMPTS_DIR.iterdir()
        if d.is_dir() and (d / "requirement.md").exists()
    )


def _build_rag_context(error_log: str) -> str:
    """類似過去事例をRAGで検索してプロンプト挿入用テキストを生成する。

    chromadb が未インストール、またはストアが空の場合は空文字を返す。
    """
    try:
        from agentic_ni.tools import rag_tools
        cases = rag_tools.search_similar_errors(error_log, k=3)
    except Exception:  # noqa: BLE001
        return ""

    if not cases:
        return ""

    lines = [
        "### 過去の類似失敗事例（RAG参考情報）",
        "以下は過去に類似エラーが発生し、最終的に成功した設計の例です。",
        "修正のヒントとして活用してください。",
        "",
    ]
    for i, case in enumerate(cases, 1):
        similarity = 1.0 - case["distance"]
        lines.append(f"#### 事例{i}（類似度: {similarity:.0%}）")
        lines.append(f"要件: {case['requirement']}")
        lines.append(f"\nエラー内容:\n```\n{case['past_error']}\n```")
        configs = case["device_configs"]
        if configs:
            configs_text = "\n".join(
                f"**{dev}**:\n```\n{cfg}\n```"
                for dev, cfg in configs.items()
            )
            lines.append(f"\n最終成功コンフィグ:\n{configs_text}")
        lines.append("---")

    return "\n".join(lines)


def _build_knowledge_context(requirement: str) -> str:
    """知識ベースから関連情報を検索してプロンプト挿入用テキストを生成する。

    rag/ ディレクトリがインデックス済みの場合のみ結果を返す。
    インデックスが空または chromadb が未インストールの場合は空文字を返す。
    """
    try:
        from agentic_ni.tools import rag_tools
        knowledge = rag_tools.search_knowledge(requirement, k=3)
    except Exception:  # noqa: BLE001
        return ""

    if not knowledge:
        return ""

    lines = [
        "### 参考資料（社内標準・設計ガイド）",
        "以下は知識ベースから検索された参考情報です。設計の参考にしてください。",
        "",
    ]
    for item in knowledge:
        similarity = 1.0 - item["distance"]
        lines.append(f"**出典: {item['source_file']}**（関連度: {similarity:.0%}）")
        lines.append(f"```\n{item['content']}\n```")
        lines.append("")

    return "\n".join(lines)


def _build_messages(state: AgentState) -> list[dict[str, str]]:
    """Stateからチャットメッセージリストを組み立てる。

    * error_log が空 → 要件からゼロ設計
    * error_log に内容あり → 差分修正モード
    """
    system_prompt = _load_system_prompt(state.get("prompt_set", "demo"))

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
        # --- 知識ベースコンテキスト付与（インデックス済みなら自動）---
        knowledge_context = _build_knowledge_context(state["requirement"])
        if knowledge_context:
            user_content += f"\n\n{knowledge_context}"
            print(
                f"  [知識ベース] rag/ の参考情報を設計プロンプトに追加しました。",
                flush=True,
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
        # --- 知識ベースコンテキスト付与（インデックス済みなら自動）---
        knowledge_context = _build_knowledge_context(state["requirement"])
        if knowledge_context:
            user_content += f"\n\n{knowledge_context}"
            print(
                f"  [知識ベース] rag/ の参考情報を設計プロンプトに追加しました。",
                flush=True,
            )
        else:
            print("  [知識ベース] 未インデックス（スキップ）。agentic-ni --rag-index で索引化できます。",
                flush=True,
            )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


def _build_messages_config_only(state: AgentState) -> list[dict[str, str]]:
    """コンフィグのみ生成モード用のメッセージを組み立てる。

    トポロジーYAMLは提供済み（state["topology_yaml"]）として、
    機器コンフィグのみをLLMに生成させる。
    """
    system_prompt = _load_system_prompt(state.get("prompt_set", "demo"))
    topology_yaml = state.get("topology_yaml", "")

    if state.get("error_log"):
        # --- コンフィグ修正モード ---
        user_content = (
            "## コンフィグ修正依頼\n\n"
            "前回のコンフィグに対して検証エージェントから以下のエラーが報告されました。\n"
            "**トポロジーYAMLは変更せず、コンフィグのみ修正してください。**\n\n"
            "### 元の要件\n"
            f"{state['requirement']}\n\n"
            "### 提供済みトポロジーYAML（変更禁止）\n"
            f"```yaml\n{topology_yaml}\n```\n\n"
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
        # --- 初回コンフィグ生成モード ---
        user_content = (
            "## コンフィグ生成依頼\n\n"
            "以下のトポロジーはすでに定義されています。\n"
            "**トポロジーYAMLは変更せず、各機器のコンフィグのみを生成してください。**\n\n"
            "### 要件\n"
            f"{state['requirement']}\n\n"
            "### 提供済みトポロジーYAML（変更禁止）\n"
            f"```yaml\n{topology_yaml}\n```\n\n"
            "上記トポロジーのノードとインターフェース構成に合わせた機器コンフィグを生成してください。"
        )
        # --- 知識ベースコンテキスト付与 ---
        knowledge_context = _build_knowledge_context(state["requirement"])
        if knowledge_context:
            user_content += f"\n\n{knowledge_context}"
            print(
                "  [知識ベース] rag/ の参考情報を設計プロンプトに追加しました。",
                flush=True,
            )
        else:
            print(
                "  [知識ベース] 未インデックス（スキップ）。agentic-ni --rag-index で索引化できます。",
                flush=True,
            )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


# ---------------------------------------------------------------------------
# エージェント実行
# ---------------------------------------------------------------------------


def _set_lab_title(topology_yaml: str, title: str) -> str:
    """topology_yaml の lab.title を指定値に上書きして返す。

    フォーマットを保持するため正規表現で title 行のみ置換する。
    title 行が存在しない場合は元の文字列をそのまま返す。
    """
    import re

    return re.sub(
        r'^(\s*title\s*:).*$',
        lambda m: f"{m.group(1)} {title}",
        topology_yaml,
        count=1,
        flags=re.MULTILINE,
    )


def run(state: AgentState) -> dict[str, Any]:
    """設計エージェントのLangGraphノード関数。

    要件またはエラーログを受け取り、LLMを呼び出して
    トポロジーYAMLと機器コンフィグを生成して返す。

    use_provided_topology=True の場合は topology_yaml を変更せず、
    機器コンフィグのみを生成する（コンフィグのみモード）。

    Args:
        state: 現在のエージェントステート。

    Returns:
        dict: AgentState の更新差分。
              topology_yaml / device_configs / error_log(クリア) を含む。
    """
    llm = get_llm()

    if state.get("use_provided_topology"):
        # --- コンフィグのみモード: トポロジーはステートから変更しない ---
        structured_llm = llm.with_structured_output(ConfigOnlyOutput, method="function_calling")
        messages = _build_messages_config_only(state)
        result: ConfigOnlyOutput = structured_llm.invoke(messages)

        if state.get("error_log"):
            print(f"  【修正方針】 {result.design_rationale}", flush=True)

        return {
            # topology_yaml はステートの値をそのまま維持（更新しない）
            "device_configs": {dc.device_name: dc.config_text for dc in result.device_configs},
            "error_log": "",
        }

    # --- 通常モード: トポロジーとコンフィグの両方を生成 ---
    structured_llm = llm.with_structured_output(DesignOutput, method="function_calling")

    messages = _build_messages(state)
    result: DesignOutput = structured_llm.invoke(messages)

    if state.get("error_log"):
        print(f"  【設計方針】 {result.design_rationale}", flush=True)

    topology_yaml = _set_lab_title(
        result.topology_yaml,
        f"agentic-ni-{state.get('prompt_set', 'demo')}",
    )

    return {
        "topology_yaml": topology_yaml,
        "device_configs": {dc.device_name: dc.config_text for dc in result.device_configs},
        # 修正設計を出力したらエラーログをクリア（次の検証で上書きされる）
        "error_log": "",
    }
