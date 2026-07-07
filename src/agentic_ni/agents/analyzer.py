"""設計分析・改善エージェント (Phase E)。

既存の稼働中 CML ラボに接続し、
「設計分析（--analyze）」または「改善計画生成（--improve）」を実行する。

Phase H (troubleshooter) との違い:
  - 分析（--analyze）: 稼働中の構成を評価し問題・改善提案をレポートする（変更なし）
  - 改善（--improve）: 要求に基づいて改善後のコンフィグを生成しファイルに保存する（deploy なし）
  - troubleshooter: 障害構成のインクリメンタル自動修正（deploy あり）

フロー (--analyze):
  collect → analyze → report

フロー (--improve):
  collect → improve → save_configs
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from agentic_ni.llm import get_llm
from agentic_ni.state import AgentState

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


# ---------------------------------------------------------------------------
# Pydantic スキーマ
# ---------------------------------------------------------------------------


class AnalysisIssue(BaseModel):
    """設計分析で検出された問題・改善点の 1 件。"""

    severity: Literal["critical", "warning", "info"] = Field(
        description=(
            "critical: 通信断や重大なセキュリティリスクになりうる問題。"
            "warning: 設計として望ましくないが即時影響はないもの。"
            "info: ベストプラクティスからの逸脱や参考情報。"
        )
    )
    device: str = Field(
        description="問題に関連するデバイス名。トポロジー全体の問題の場合は 'all' を指定する。"
    )
    description: str = Field(
        description="問題の内容を具体的に説明する（1〜2 文）。"
    )
    recommendation: str = Field(
        description="推奨される対応方法（コンフィグ例があれば含める）。"
    )


class AnalysisResult(BaseModel):
    """LLM が生成する設計分析結果。"""

    overall_rating: Literal["good", "acceptable", "needs_improvement", "critical"] = Field(
        description=(
            "good: 設計品質が高い。"
            "acceptable: 動作するが改善余地あり。"
            "needs_improvement: 複数の問題が存在し改善が必要。"
            "critical: 重大な問題がある。"
        )
    )
    summary: str = Field(
        description="設計全体の評価を 2〜4 文で述べる。"
    )
    issues: list[AnalysisIssue] = Field(
        description="検出された問題・改善点のリスト。問題がなければ空リスト。"
    )
    improvement_suggestions: list[str] = Field(
        description="改善提案のリスト（箇条書き形式の短い文）。"
    )


class ImprovementOutput(BaseModel):
    """LLM が生成する改善後のコンフィグ。"""

    device_configs: dict[str, str] = Field(
        description=(
            "改善後の機器コンフィグ。キーはデバイス名（ノードラベルと一致させること）、"
            "値は complete な IOS コンフィグテキスト。"
            "変更不要なデバイスは元のコンフィグをそのまま含めること。"
        )
    )
    changes_summary: list[str] = Field(
        description="加えた変更の一覧（箇条書き形式の短い文）。"
    )
    rationale: str = Field(
        description="この改善計画の根拠と期待される効果を説明する（2〜4 文）。"
    )


# ---------------------------------------------------------------------------
# プロンプト構築
# ---------------------------------------------------------------------------


def _load_system_prompt() -> str:
    """analyzer_system.md を読み込んで返す。"""
    path = _PROMPTS_DIR / "analyzer_system.md"
    if not path.exists():
        return (
            "あなたは上級ネットワークアーキテクトです。"
            "稼働中のネットワーク機器の running-config と show コマンド出力を精査し、"
            "設計品質を評価するか、改善要求に基づいて改善後のコンフィグを生成します。"
        )
    return path.read_text(encoding="utf-8")


def _format_collected_state(collected_state: dict) -> str:
    """収集した機器状態を LLM 向けにフォーマットする（トークン節約のため上限あり）。"""
    parts: list[str] = []
    for device, state_data in collected_state.items():
        parts.append(f"#### {device}")
        if "error" in state_data:
            parts.append(f"(状態収集エラー: {state_data['error']})")
            continue
        cfg = state_data.get("running_config", "")
        if len(cfg) > 2500:
            cfg = cfg[:2500] + "\n... (省略)"
        parts.append(f"**running-config:**\n```\n{cfg}\n```")
        for cmd, output in state_data.get("show_outputs", {}).items():
            parts.append(f"**{cmd}:**\n```\n{str(output)[:600]}\n```")
    return "\n\n".join(parts) if parts else "(状態なし)"


def _build_analysis_messages(
    state: AgentState, collected_state: dict
) -> list[dict[str, str]]:
    """分析用のメッセージを組み立てる。"""
    system_prompt = _load_system_prompt()
    device_state_text = _format_collected_state(collected_state)

    user_content = (
        "## 設計分析依頼\n\n"
        f"### 対象ラボ\n`{state.get('troubleshoot_lab_id') or state.get('lab_id', '(不明)')}`\n\n"
        f"### 本来あるべき要件\n{state.get('requirement', '(要件なし)')}\n\n"
        f"### 現在の機器状態\n\n{device_state_text}\n\n"
        "この設計を分析し、問題点・改善提案を返してください。\n"
        "動作している場合でも、ベストプラクティス・冗長性・セキュリティの観点から評価してください。"
    )

    # 知識ベースコンテキスト付与（インデックス済みなら自動）
    query = state.get("requirement", "") or "ネットワーク設計分析"
    try:
        from agentic_ni.tools import rag_tools
        knowledge = rag_tools.search_knowledge(query, k=3)
        if knowledge:
            knowledge_text = "\n\n".join(
                f"**{k['source_file']}** （関連度: {1.0 - k['distance']:.0%}）\n```\n{k['content']}\n```"
                for k in knowledge
            )
            user_content += f"\n\n### 参考資料（知識ベース）\n{knowledge_text}"
    except Exception:  # noqa: BLE001
        pass

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


def _build_improvement_messages(
    state: AgentState, collected_state: dict
) -> list[dict[str, str]]:
    """改善計画立案用のメッセージを組み立てる。"""
    system_prompt = _load_system_prompt()
    device_state_text = _format_collected_state(collected_state)

    user_content = (
        "## 改善計画立案依頼\n\n"
        f"### 対象ラボ\n`{state.get('troubleshoot_lab_id') or state.get('lab_id', '(不明)')}`\n\n"
        f"### 改善要求\n{state.get('analyze_request') or '(改善要求なし)'}\n\n"
        f"### 現在の機器状態\n\n{device_state_text}\n\n"
        "以下の制約に従って改善後のコンフィグを生成してください:\n"
        "- 改善要求を満たす最小限の変更にとどめる\n"
        "- すべてのデバイスの complete なコンフィグを出力する（変更のないデバイスも含む）\n"
        "- device_configs のキーは現在のノードラベルと完全に一致させること"
    )

    # 知識ベースコンテキスト付与
    query = state.get("analyze_request", "") or state.get("requirement", "")
    try:
        from agentic_ni.tools import rag_tools
        knowledge = rag_tools.search_knowledge(query, k=3)
        if knowledge:
            knowledge_text = "\n\n".join(
                f"**{k['source_file']}** （関連度: {1.0 - k['distance']:.0%}）\n```\n{k['content']}\n```"
                for k in knowledge
            )
            user_content += f"\n\n### 参考資料（知識ベース）\n{knowledge_text}"
    except Exception:  # noqa: BLE001
        pass

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


# ---------------------------------------------------------------------------
# エージェント関数（グラフノードから呼ばれる）
# ---------------------------------------------------------------------------


def run_analyze(state: AgentState) -> dict[str, Any]:
    """収集した機器状態を LLM で分析し、設計評価レポートを生成する。

    Args:
        state: collected_state を含むステート。

    Returns:
        dict: analysis_result の更新差分。
    """
    collected_state = state.get("collected_state", {})
    llm = get_llm()
    structured_llm = llm.with_structured_output(AnalysisResult, method="function_calling")
    result: AnalysisResult = structured_llm.invoke(
        _build_analysis_messages(state, collected_state)
    )

    rating_label = {
        "good": "✅ 良好",
        "acceptable": "⚠️ 許容範囲",
        "needs_improvement": "⚠️ 要改善",
        "critical": "❌ 重大な問題あり",
    }.get(result.overall_rating, result.overall_rating)

    issue_rows = "\n".join(
        f"| {i.severity.upper()} | {i.device} | {i.description} | {i.recommendation} |"
        for i in result.issues
    ) or "| - | - | 問題なし | - |"

    suggestions = "\n".join(f"- {s}" for s in result.improvement_suggestions) or "(なし)"

    analysis_text = (
        f"## 設計評価: {rating_label}\n\n"
        f"### サマリー\n{result.summary}\n\n"
        f"### 検出された問題 ({len(result.issues)} 件)\n\n"
        f"| 重大度 | デバイス | 問題 | 推奨対応 |\n"
        f"|---|---|---|---|\n"
        f"{issue_rows}\n\n"
        f"### 改善提案\n{suggestions}"
    )

    summary_preview = result.summary[:80] + ("..." if len(result.summary) > 80 else "")
    print(f"  分析結果: [{result.overall_rating}] {summary_preview}", flush=True)
    return {"analysis_result": analysis_text}


def run_improve(state: AgentState) -> dict[str, Any]:
    """改善要求に基づいて改善後のコンフィグを LLM で生成する。

    Args:
        state: collected_state・device_configs・analyze_request を含むステート。

    Returns:
        dict: device_configs と analysis_result の更新差分。
    """
    collected_state = state.get("collected_state", {})
    llm = get_llm()
    structured_llm = llm.with_structured_output(ImprovementOutput, method="function_calling")
    result: ImprovementOutput = structured_llm.invoke(
        _build_improvement_messages(state, collected_state)
    )

    changes = "\n".join(f"- {c}" for c in result.changes_summary) or "(変更なし)"
    improvement_summary = (
        f"## 改善計画\n\n"
        f"### 根拠\n{result.rationale}\n\n"
        f"### 変更内容 ({len(result.changes_summary)} 件)\n{changes}"
    )

    rationale_preview = result.rationale[:60] + ("..." if len(result.rationale) > 60 else "")
    print(
        f"  改善計画: {len(result.changes_summary)} 件の変更 — {rationale_preview}",
        flush=True,
    )
    return {
        "device_configs": result.device_configs,
        "analysis_result": improvement_summary,
    }
