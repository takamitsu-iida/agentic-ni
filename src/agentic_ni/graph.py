"""LangGraph グラフの組み立て。

Phase 7: Human-in-the-Loop、レポートフォーマット整備、E2E統合済み。
障害シミュレーション（リンク断・復旧・再テスト）対応済み。
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Literal

from dotenv import load_dotenv
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from agentic_ni.agents import architect, fault_simulator, validator
from agentic_ni.state import AgentState, FaultScenarioResult

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


# ---------------------------------------------------------------------------
# 障害シミュレーション ノード定義
# ---------------------------------------------------------------------------


def fault_simulate_node(state: AgentState) -> dict:
    """障害シミュレーションエージェント。インターフェース shutdown/no shutdown + 再テストを実行する。"""
    print(f"\n{'='*60}", flush=True)
    print("[障害シミュレーション]  開始", flush=True)
    print(f"{'='*60}", flush=True)
    result = fault_simulator.run(state)
    return result


def fault_report_node(state: AgentState) -> dict:
    """障害シミュレーション結果レポートを生成し final_report に追記する。"""
    print("\n  >>> 障害シミュレーションレポートを生成しています...", flush=True)
    scenario_results: list[FaultScenarioResult] = state.get("fault_scenario_results", [])

    if not scenario_results:
        fault_report_md = (
            "## 障害シミュレーション結果\n\n"
            "実行するシナリオがありませんでした（リンクなし、またはスキップ）。\n"
        )
    else:
        passed_count = sum(1 for r in scenario_results if r["passed"])
        failed_count = len(scenario_results) - passed_count

        scenario_sections = []
        for r in scenario_results:
            mark = "✅ PASS" if r["passed"] else "❌ FAIL"

            def _rows(results: list) -> str:
                return "\n".join(
                    f"| {t['test']} | {'✅ PASS' if t['result'] == 'PASS' else '❌ FAIL'}"
                    f" | {t['detail']} |"
                    for t in results
                ) or "| (テストなし) | - | - |"

            scenario_sections.append(
                f"### {r['scenario_name']} ({r['link_label']}) — {mark}\n\n"
                f"**障害中テスト結果**\n\n"
                f"| テスト名 | 結果 | 詳細 |\n"
                f"|---|---|---|\n"
                f"{_rows(r['tests_during_fault'])}\n\n"
                f"**復旧後テスト結果**\n\n"
                f"| テスト名 | 結果 | 詳細 |\n"
                f"|---|---|---|\n"
                f"{_rows(r['tests_after_recovery'])}"
            )

        verdict = "✅ 全シナリオで復旧を確認" if failed_count == 0 else f"⚠️ {failed_count} シナリオで復旧未確認"
        fault_report_md = (
            f"## 障害シミュレーション結果\n\n"
            f"- 実施シナリオ数: {len(scenario_results)} 件\n"
            f"- PASS（復旧確認）: {passed_count} 件\n"
            f"- FAIL（復旧未確認）: {failed_count} 件\n"
            f"- **判定: {verdict}**\n\n"
            + "\n\n".join(scenario_sections)
        )

    # final_report に障害シミュレーション結果を追記
    phase_a_report = state.get("final_report", "")
    combined_report = (
        phase_a_report
        + "\n\n---\n\n"
        + fault_report_md
    )
    return {"final_report": combined_report, "fault_report": fault_report_md}


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


def _should_run_fault_sim(
    state: AgentState,
) -> Literal["fault_simulate", "done"]:
    """構成検証成功後に障害シミュレーションを実行するか判定する。"""
    if state.get("fault_simulation_enabled", False):
        return "fault_simulate"
    return "done"


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
    graph.add_node("fault_simulate", fault_simulate_node)
    graph.add_node("fault_report", fault_report_node)

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

    # report 後に障害シミュレーションを実行するか分岐
    graph.add_edge("fault_simulate", "fault_report")

    # 終端ノード（HITL あり: human_review、なし: END）
    terminal: str | type = "human_review" if human_in_the_loop else END

    graph.add_conditional_edges(
        "report",
        _should_run_fault_sim,
        {
            "fault_simulate": "fault_simulate",
            "done": terminal,
        },
    )
    graph.add_edge("fault_report", terminal)
    graph.add_edge("escalate", terminal)

    if human_in_the_loop:
        graph.add_node("human_review", human_review_node)
        graph.add_edge("human_review", END)

    return graph


def compile_graph():
    """コンパイル済みグラフを返す（Human-in-the-Loop なし）。テスト・バッチ実行用。"""
    return build_graph(human_in_the_loop=False).compile()


def dry_run_node(state: AgentState) -> dict:
    """ドライランモードの出力ノード。設計結果を表示してファイルに保存する。"""
    import os
    from pathlib import Path

    topology_yaml: str = state.get("topology_yaml", "")
    device_configs: dict[str, str] = state.get("device_configs", {})
    prompt_set: str = state.get("prompt_set", "demo")

    # configs/<set_name>/ ディレクトリに保存
    out_dir = Path("configs") / prompt_set
    out_dir.mkdir(parents=True, exist_ok=True)

    # topology.yaml を保存
    topo_path = out_dir / "topology.yaml"
    topo_path.write_text(topology_yaml, encoding="utf-8")

    # 機器ごとのコンフィグを <device>.cfg として保存
    saved_files: list[str] = [str(topo_path)]
    for device, config in device_configs.items():
        cfg_path = out_dir / f"{device}.cfg"
        cfg_path.write_text(config, encoding="utf-8")
        saved_files.append(str(cfg_path))

    # コンソール表示
    sep = "=" * 60
    device_section = "\n\n".join(
        f"### {dev}\n```\n{cfg.strip()}\n```"
        for dev, cfg in device_configs.items()
    ) or "(コンフィグなし)"

    report = (
        f"# 設計レポート（ドライラン）\n\n"
        f"**生成日時**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"## 要件\n{state.get('requirement', '')}\n\n"
        f"## トポロジー定義（CML YAML）\n\n"
        f"```yaml\n{topology_yaml.strip()}\n```\n\n"
        f"## 機器コンフィグ\n\n"
        f"{device_section}\n\n"
        f"## 保存先\n\n"
        + "\n".join(f"- `{f}`" for f in saved_files)
    )
    return {"final_report": report}


def compile_graph_dry_run():
    """ドライランモード（設計のみ・CMLデプロイなし）のコンパイル済みグラフを返す。"""
    graph = StateGraph(AgentState)
    graph.add_node("architect", architect_node)
    graph.add_node("dry_run", dry_run_node)
    graph.set_entry_point("architect")
    graph.add_edge("architect", "dry_run")
    graph.add_edge("dry_run", END)
    return graph.compile()


def compile_graph_interactive():
    """Human-in-the-Loop ありのコンパイル済みグラフを返す。

    interrupt() を使うため MemorySaver チェックポインターが必要。
    """
    from langgraph.checkpoint.memory import MemorySaver

    return build_graph(human_in_the_loop=True).compile(checkpointer=MemorySaver())


def initial_state(
    requirement: str,
    prompt_set: str = "demo",
    use_rag: bool = False,
    fault_simulation_enabled: bool = False,
) -> AgentState:
    """初期ステートを生成するファクトリー関数。

    Args:
        requirement: ネットワーク要件の自然言語テキスト。
        prompt_set: 使用するプロンプトセット名（prompts/ 配下のサブディレクトリ名）。
        use_rag: True の場合、修正設計時に過去の類似成功事例をプロンプトに追加する。
        fault_simulation_enabled: True の場合、Phase A 成功後に障害シミュレーションを実行する。
    """
    return AgentState(
        requirement=requirement,
        prompt_set=prompt_set,
        use_rag=use_rag,
        fault_simulation_enabled=fault_simulation_enabled,
        error_history=[],
        topology_yaml="",
        device_configs={},
        lab_id="",
        test_results=[],
        test_plan_items=[],
        error_log="",
        retry_count=0,
        fault_scenario_results=[],
        fault_report="",
        final_report="",
    )


def load_requirement(prompt_set: str) -> str:
    """prompt_set ディレクトリの requirement.md を読み込んで返す。"""
    from pathlib import Path
    prompts_dir = Path(__file__).parent / "prompts"
    path = prompts_dir / prompt_set / "requirement.md"
    if not path.exists():
        raise FileNotFoundError(
            f"プロンプトセット '{prompt_set}' に requirement.md が見つかりません: {path}\n"
            f"ファイルを作成して要件テキストを記載してください。"
        )
    return path.read_text(encoding="utf-8").strip()


def main() -> None:
    """CLI エントリポイント。"""
    import sys

    args = sys.argv[1:]

    # 引数なし or --help / -h: ヘルプを表示して終了
    if not args or "--help" in args or "-h" in args:
        print(
            "使い方: agentic-ni <プロンプトセット名> [オプション]\n"
            "\n"
            "要件はプロンプトセット内の requirement.md に記載してください。\n"
            "\n"
            "オプション:\n"
            "  --list               利用可能なプロンプトセット一覧を表示して終了する\n"
            "  --dry-run            CMLデプロイをスキップして設計・コンフィグ生成のみ行う\n"
            "  --use-rag            修正設計時に過去の成功事例をプロンプトに追加する（要 chromadb）\n"
            "  --fault-sim          構成検証成功後に障害シミュレーション（リンク断・復旧・再テスト）を実行する\n"
            "  --rag-stats          RAGストアの保存件数と保存場所を表示して終了する\n"
            "  -h / --help          このヘルプを表示して終了する\n"
            "\n"
            "例:\n"
            "  agentic-ni demo                  # demo セットの要件で実行\n"
            "  agentic-ni ospf_l3vpn            # ospf_l3vpn セットの要件で実行\n"
            "  agentic-ni demo --dry-run          # CMLなしでコンフィグ生成のみ\n"
            "  agentic-ni demo --use-rag        # RAGを有効にして実行\n"
            "  agentic-ni --list\n"
            "  agentic-ni --rag-stats"
        )
        return

    # --list: 利用可能なプロンプトセット一覧を表示して終了
    if "--list" in args:
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

    use_rag = "--use-rag" in args
    dry_run = "--dry-run" in args
    fault_simulation_enabled = "--fault-sim" in args

    # 位置引数（フラグ以外）= プロンプトセット名
    positional = [a for a in args if not a.startswith("-")]
    if not positional:
        print("エラー: プロンプトセット名を指定してください。", file=sys.stderr)
        print("  利用可能なセット確認: agentic-ni --list", file=sys.stderr)
        sys.exit(1)
    if len(positional) > 1:
        print(f"エラー: 引数が多すぎます: {positional}", file=sys.stderr)
        sys.exit(1)
    prompt_set = positional[0]

    # 要件はプロンプトセットの requirement.md から読み込む
    try:
        requirement = load_requirement(prompt_set)
    except FileNotFoundError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"プロンプトセット: {prompt_set}")
    if dry_run:
        print("モード: ドライラン（CMLデプロイなし）")
    if use_rag:
        print(f"RAG: 有効")
    if fault_simulation_enabled:
        print("障害シミュレーション: 有効")
    print()
    print("【要件】")
    for line in requirement.splitlines():
        print(f"  {line}")
    print()
    print("処理を開始します...\n")

    app = compile_graph_dry_run() if dry_run else compile_graph()
    result = app.invoke(initial_state(requirement, prompt_set, use_rag, fault_simulation_enabled))
    print(result.get("final_report", "(レポートなし)"))


if __name__ == "__main__":
    main()
