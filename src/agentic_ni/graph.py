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
    trial = state.get("retry_count", 0) + 1
    mode = "修正設計" if state.get("error_log") else "初回設計"
    print(f"\n{'='*60}", flush=True)
    print(f"[第{trial}回 / 上限{MAX_RETRIES}回]  設計エージェント  ({mode})", flush=True)
    print(f"{'='*60}", flush=True)
    print("  >>> LLM にトポロジーとコンフィグを生成させています...", flush=True)
    result = architect.run(state)
    print("  <<< 設計完了", flush=True)
    return result


def validator_node(state: AgentState) -> dict:
    """検証エージェント。CMLへデプロイし、テスト実行・失敗推論を行う。"""
    trial = state.get("retry_count", 0) + 1
    print(f"\n[第{trial}回 / 上限{MAX_RETRIES}回]  検証エージェント  開始", flush=True)
    result = validator.run(state)
    return result


def report_node(state: AgentState) -> dict:
    """全PASS時の最終レポートを生成する。"""
    print("\n  >>> 全テスト PASS! 最終レポートを生成しています...", flush=True)
    results = state.get("test_results", [])
    passed = [r for r in results if r["result"] == "PASS"]
    failed = [r for r in results if r["result"] == "FAIL"]

    result_lines = "\n".join(
        f"| {r['test']} | {'✅ PASS' if r['result'] == 'PASS' else '❌ FAIL'} | {r['detail']} |"
        for r in results
    )

    # 機器コンフィグのセクション
    device_configs: dict[str, str] = state.get("device_configs", {})
    config_section = "\n\n".join(
        f"### {dev}\n```\n{cfg.strip()}\n```"
        for dev, cfg in device_configs.items()
    ) or "(コンフィグなし)"

    report = (
        f"# 検証成功レポート\n\n"
        f"**生成日時**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"## 要件\n{state.get('requirement', '')}\n\n"
        f"## 概要\n"
        f"- 試行回数: {state.get('retry_count', 0)} 回\n"
        f"- PASSテスト: {len(passed)} 件\n"
        f"- FAILテスト: {len(failed)} 件\n"
        f"- ラボID: {state.get('lab_id', '(不明)')}\n\n"
        f"## ネットワーク設計\n\n"
        f"### トポロジー定義（CML YAML）\n"
        f"```yaml\n{state.get('topology_yaml', '(なし)').strip()}\n```\n\n"
        f"### 機器コンフィグ\n\n"
        f"{config_section}\n\n"
        f"## 検証テスト結果\n\n"
        f"| テスト名 | 結果 | 詳細 |\n"
        f"|---|---|---|\n"
        f"{result_lines}\n\n"
        f"すべてのテストが PASS しました。要件を満たすネットワーク設計が確認されました。"
    )
    _save_to_rag(state)
    return {"final_report": report}


def _save_to_rag(state: AgentState) -> None:
    """成功した実行のエラー履歴をRAGストアに保存する。use_rag=False の場合は何もしない。"""
    if not state.get("use_rag", False):
        return
    error_history = state.get("error_history", [])
    if not error_history:
        return
    try:
        from agentic_ni.tools import rag_tools
        saved = rag_tools.save_successful_run(
            requirement=state.get("requirement", ""),
            error_history=error_history,
            topology_yaml=state.get("topology_yaml", ""),
            device_configs=state.get("device_configs", {}),
        )
        print(f"  >>> RAGストアに {saved} 件の事例を保存しました。", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"  >>> RAG保存スキップ: {exc}", flush=True)


def escalate_node(state: AgentState) -> dict:
    """最大リトライ超過時のエスカレーションレポートを生成する。"""
    print(f"\n  >>> 上限に達しました。エスカレーションレポートを生成しています...", flush=True)
    results = state.get("test_results", [])
    result_lines = "\n".join(
        f"| {r['test']} | {'✅ PASS' if r['result'] == 'PASS' else '❌ FAIL'} | {r['detail']} |"
        for r in results
    ) or "| (テスト未実施) | - | - |"

    # 機器コンフィグのセクション
    device_configs: dict[str, str] = state.get("device_configs", {})
    config_section = "\n\n".join(
        f"### {dev}\n```\n{cfg.strip()}\n```"
        for dev, cfg in device_configs.items()
    ) or "(コンフィグなし)"

    report = (
        f"# エスカレーションレポート\n\n"
        f"**生成日時**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"## 要件\n{state.get('requirement', '')}\n\n"
        f"## 概要\n"
        f"- 試行回数: {state.get('retry_count', 0)} 回（上限: {MAX_RETRIES} 回）\n"
        f"- 自動修正での解決に失敗しました\n"
        f"- ラボID: {state.get('lab_id', '(不明)')}\n\n"
        f"## 最終ネットワーク設計\n\n"
        f"### トポロジー定義（CML YAML）\n"
        f"```yaml\n{state.get('topology_yaml', '(なし)').strip()}\n```\n\n"
        f"### 機器コンフィグ\n\n"
        f"{config_section}\n\n"
        f"## 最終テスト結果\n\n"
        f"| テスト名 | 結果 | 詳細 |\n"
        f"|---|---|---|\n"
        f"{result_lines}\n\n"
        f"## AIの推論（失敗原因）\n"
        f"{state.get('error_log', '(なし)')}\n\n"
        f"## 推奨アクション\n"
        f"自動修正の上限（{MAX_RETRIES}回）に達しました。"
        f"以下を手動で確認してください:\n"
        f"1. 上記の最終コンフィグと失敗原因を参考に手動でコンフィグを修正する\n"
        f"2. CMLラボ `{state.get('lab_id', '(不明)')}` で現状を確認する\n"
        f"3. 要件の曖昧さや矛盾がないか見直す"
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

    # インフラ・ツールエラー（ネットワーク設計の問題ではない）は即時エスカレーション
    if test_results and all(
        "テスト実行エラー" in r.get("detail", "") for r in test_results
    ):
        return "escalate"

    # デプロイ自体が失敗した場合も即時エスカレーション
    error_log = state.get("error_log", "")
    if error_log.startswith("デプロイ失敗:") and not test_results:
        return "escalate"

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


def initial_state(requirement: str, prompt_set: str = "default", use_rag: bool = False) -> AgentState:
    """初期ステートを生成するファクトリー関数。

    Args:
        requirement: ネットワーク要件の自然言語テキスト。
        prompt_set: 使用するプロンプトセット名（prompts/ 配下のサブディレクトリ名）。
        use_rag: True の場合、修正設計時に過去の類似成功事例をプロンプトに追加する。
    """
    return AgentState(
        requirement=requirement,
        prompt_set=prompt_set,
        use_rag=use_rag,
        error_history=[],
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

    args = sys.argv[1:]

    # --list-sets: 利用可能なプロンプトセット一覧を表示して終了
    if "--list-sets" in args:
        from agentic_ni.agents.architect import list_prompt_sets
        sets = list_prompt_sets()
        print("利用可能なプロンプトセット:")
        for s in sets:
            print(f"  - {s}")
        return

    # --rag-stats: RAGストアの統計情報を表示して終了
    if "--rag-stats" in args:
        from agentic_ni.tools import rag_tools
        stats = rag_tools.get_store_stats()
        print(f"RAGストア統計:")
        print(f"  保存済み事例数: {stats['total_cases']} 件")
        print(f"  保存場所: {stats['db_path']}")
        return

    interactive = "--interactive" in args or "-i" in args
    use_rag = "--use-rag" in args

    # --prompt-set <name> の解析
    prompt_set = "default"
    if "--prompt-set" in args:
        idx = args.index("--prompt-set")
        if idx + 1 < len(args):
            prompt_set = args[idx + 1]
            args = args[:idx] + args[idx + 2:]
        else:
            print("エラー: --prompt-set の後にセット名を指定してください。", file=sys.stderr)
            sys.exit(1)

    # 残りの引数をスペース結合して要件とする
    filtered = [a for a in args if a not in ("--interactive", "-i", "--use-rag")]
    requirement = " ".join(filtered) or "R1とR2をOSPFで接続する"

    if interactive:
        import uuid

        app = compile_graph_interactive()
        config = {"configurable": {"thread_id": str(uuid.uuid4())}}

        print(f"要件: {requirement}")
        print(f"プロンプトセット: {prompt_set}")
        if use_rag:
            print(f"RAG: 有効")
        print("処理を開始します...\n")

        for event in app.stream(initial_state(requirement, prompt_set, use_rag), config):
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
        result = app.invoke(initial_state(requirement, prompt_set, use_rag))
        print(result.get("final_report", "(レポートなし)"))


if __name__ == "__main__":
    main()
