"""LangGraph グラフの組み立て。

Phase 7: Human-in-the-Loop、レポートフォーマット整備、E2E統合済み。
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Literal

from dotenv import load_dotenv
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from agentic_ni.agents import architect, validator
from agentic_ni.state import AgentState

load_dotenv()

MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "5"))


# ---------------------------------------------------------------------------
# ノード定義
# ---------------------------------------------------------------------------


def architect_node(state: AgentState) -> dict:
    """設計エージェント。要件またはエラーログからトポロジーYAMLと機器コンフィグを生成する。"""
    return architect.run(state)


def validator_node(state: AgentState) -> dict:
    """検証エージェント。CMLへデプロイし、テスト実行・失敗推論を行う。"""
    return validator.run(state)


def report_node(state: AgentState) -> dict:
    """全PASS時の最終レポートを生成する。"""
    results = state.get("test_results", [])
    passed = [r for r in results if r["result"] == "PASS"]
    failed = [r for r in results if r["result"] == "FAIL"]

    result_lines = "\n".join(
        f"| {r['test']} | {'✅ PASS' if r['result'] == 'PASS' else '❌ FAIL'} | {r['detail']} |"
        for r in results
    )

    report = (
        f"# 検証成功レポート\n\n"
        f"**生成日時**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"## 要件\n{state.get('requirement', '')}\n\n"
        f"## 概要\n"
        f"- 試行回数: {state.get('retry_count', 0)} 回\n"
        f"- PASSテスト: {len(passed)} 件\n"
        f"- FAILテスト: {len(failed)} 件\n\n"
        f"## テスト結果\n"
        f"| テスト名 | 結果 | 詳細 |\n"
        f"|---|---|---|\n"
        f"{result_lines}\n\n"
        f"## デプロイ情報\n"
        f"- ラボID: {state.get('lab_id', '(不明)')}\n\n"
        f"すべてのテストが PASS しました。要件を満たすネットワーク設計が確認されました。"
    )
    return {"final_report": report}


def escalate_node(state: AgentState) -> dict:
    """最大リトライ超過時のエスカレーションレポートを生成する。"""
    results = state.get("test_results", [])
    result_lines = "\n".join(
        f"| {r['test']} | {'✅ PASS' if r['result'] == 'PASS' else '❌ FAIL'} | {r['detail']} |"
        for r in results
    ) or "| (テスト未実施) | - | - |"

    report = (
        f"# エスカレーションレポート\n\n"
        f"**生成日時**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"## 要件\n{state.get('requirement', '')}\n\n"
        f"## 概要\n"
        f"- 試行回数: {state.get('retry_count', 0)} 回（上限: {MAX_RETRIES} 回）\n"
        f"- 自動修正での解決に失敗しました\n\n"
        f"## 最終テスト結果\n"
        f"| テスト名 | 結果 | 詳細 |\n"
        f"|---|---|---|\n"
        f"{result_lines}\n\n"
        f"## 最終エラーログ（AIの推論）\n"
        f"{state.get('error_log', '(なし)')}\n\n"
        f"## 推奨アクション\n"
        f"自動修正の上限（{MAX_RETRIES}回）に達しました。"
        f"以下を手動で確認してください:\n"
        f"1. 要件の曖昧さや矛盾がないか確認する\n"
        f"2. 最終エラーログを参考に手動でコンフィグを修正する\n"
        f"3. CMLラボID `{state.get('lab_id', '(不明)')}` で現状を確認する"
    )
    return {"final_report": report}


def human_review_node(state: AgentState) -> dict[str, Any]:
    """Human-in-the-Loop: 最終レポートを人間に提示し、承認/却下を求める。

    LangGraph の interrupt() を使用してグラフを一時停止する。
    呼び出し元は `graph.invoke()` の後に `graph.resume(thread_id, {"approved": True})` を
    呼ぶことで処理を再開できる。

    Returns:
        dict: `approved=True` なら final_report をそのまま維持。
              `approved=False` なら final_report に却下理由を追記。
    """
    decision: dict = interrupt(
        {
            "type": "human_review",
            "message": "AIによる検証が完了しました。最終レポートを確認して承認/却下を選択してください。",
            "final_report": state.get("final_report", ""),
        }
    )
    approved: bool = decision.get("approved", True)
    reason: str = decision.get("reason", "")

    if not approved:
        updated_report = (
            state.get("final_report", "")
            + f"\n\n---\n## ⚠️ 人間による却下\n却下理由: {reason or '(理由なし)'}"
        )
        return {"final_report": updated_report}

    return {}


# ---------------------------------------------------------------------------
# 条件分岐
# ---------------------------------------------------------------------------


def should_continue(
    state: AgentState,
) -> Literal["complete", "escalate", "redesign"]:
    """検証エージェント実行後のルーティングを決定する。"""
    test_results = state.get("test_results", [])

    if test_results and all(r["result"] == "PASS" for r in test_results):
        return "complete"

    if state.get("retry_count", 0) >= MAX_RETRIES:
        return "escalate"

    return "redesign"


# ---------------------------------------------------------------------------
# グラフ構築
# ---------------------------------------------------------------------------


def build_graph(human_in_the_loop: bool = False) -> StateGraph:
    """LangGraph のグラフを構築して返す。

    Args:
        human_in_the_loop: True の場合、最終レポートの前に人間の承認ステップを挟む。
    """
    graph = StateGraph(AgentState)

    graph.add_node("architect", architect_node)
    graph.add_node("validator", validator_node)
    graph.add_node("report", report_node)
    graph.add_node("escalate", escalate_node)

    graph.set_entry_point("architect")
    graph.add_edge("architect", "validator")

    graph.add_conditional_edges(
        "validator",
        should_continue,
        {
            "complete": "report",
            "escalate": "escalate",
            "redesign": "architect",
        },
    )

    if human_in_the_loop:
        graph.add_node("human_review", human_review_node)
        graph.add_edge("report", "human_review")
        graph.add_edge("escalate", "human_review")
        graph.add_edge("human_review", END)
    else:
        graph.add_edge("report", END)
        graph.add_edge("escalate", END)

    return graph


def compile_graph():
    """コンパイル済みグラフを返す（Human-in-the-Loop なし）。テスト・バッチ実行用。"""
    return build_graph(human_in_the_loop=False).compile()


def compile_graph_interactive():
    """Human-in-the-Loop ありのコンパイル済みグラフを返す。

    interrupt() を使うため MemorySaver チェックポインターが必要。
    """
    from langgraph.checkpoint.memory import MemorySaver

    return build_graph(human_in_the_loop=True).compile(checkpointer=MemorySaver())


def initial_state(requirement: str) -> AgentState:
    """初期ステートを生成するファクトリー関数。"""
    return AgentState(
        requirement=requirement,
        topology_yaml="",
        device_configs={},
        lab_id="",
        test_results=[],
        error_log="",
        retry_count=0,
        final_report="",
    )


def main() -> None:
    """CLI エントリポイント。"""
    import sys

    requirement = " ".join(sys.argv[1:]) or "R1とR2をOSPFで接続する"
    interactive = "--interactive" in sys.argv or "-i" in sys.argv

    if interactive:
        import uuid

        app = compile_graph_interactive()
        config = {"configurable": {"thread_id": str(uuid.uuid4())}}

        print(f"要件: {requirement}")
        print("処理を開始します...\n")

        for event in app.stream(initial_state(requirement), config):
            for node_name, state_update in event.items():
                if node_name == "__interrupt__":
                    payload = state_update[0].value
                    print("\n" + "=" * 60)
                    print(payload.get("message", ""))
                    print("\n--- レポート ---")
                    print(payload.get("final_report", ""))
                    print("=" * 60)
                    choice = input("\n承認しますか？ [y/N]: ").strip().lower()
                    reason = ""
                    if choice != "y":
                        reason = input("却下理由を入力してください: ").strip()
                    app.invoke(
                        {"approved": choice == "y", "reason": reason},
                        config,
                        command={"resume": {"approved": choice == "y", "reason": reason}},
                    )
    else:
        app = compile_graph()
        result = app.invoke(initial_state(requirement))
        print(result.get("final_report", "(レポートなし)"))


if __name__ == "__main__":
    main()
