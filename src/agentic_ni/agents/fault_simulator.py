"""障害シミュレーションエージェント。

Phase A（通常検証）成功後に呼び出され、各リンクを順番に断して
ネットワークの冗長性・フェイルオーバー・復旧を自動検証する。

フロー:
  1. CML からリンク一覧を取得
  2. LLM が障害シナリオ計画（どのリンクを断するか）を立案
  3. 各シナリオで:
       a. リンク断 → 収束待ち → テスト実行（障害中）
       b. リンク復旧 → 収束待ち → テスト実行（復旧後）
  4. 結果を FaultScenarioResult のリストとして返す
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field

from agentic_ni.agents.prompts import load_agent_prompt
from agentic_ni.logger import get_logger
from agentic_ni.llm import get_llm
from agentic_ni.state import AgentState, FaultScenarioResult, TestResult

logger = get_logger(__name__)

# リンク断後にルーティングプロトコルの収束を待つデフォルト秒数
# interface shutdown は終了待機不要なため loss条件アプローチ（死死時間=40s）より大幅に短い
_DEFAULT_WAIT_SECONDS: int = 15


# ---------------------------------------------------------------------------
# Pydantic スキーマ
# ---------------------------------------------------------------------------


class FaultScenario(BaseModel):
    """障害シミュレーションの 1 シナリオ。"""

    link_id: str = Field(
        description="障害対象リンクの CML ID（get_lab_links で取得した id フィールド）。"
    )
    link_label: str = Field(
        description="リンクの表示名（例: 'R1 <-> R2'）。ログ・レポートに使用する。"
    )
    scenario_name: str = Field(
        description="シナリオの簡潔な説明（例: '上位リンク断時の冗長切り替え確認'）。"
    )
    wait_seconds: int = Field(
        default=_DEFAULT_WAIT_SECONDS,
        description=(
            "インターフェース shutdown/no shutdown 後にルーティング収束を待つ秒数。"
            "interface shutdown は即時応答のため 15 秒程度で十分。"
        ),
    )
    expected_ospf_neighbors: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "障害中に各デバイスで期待される OSPF ネイバー数。"
            "デバイス名 → 期待ネイバー数のマッピング。"
            "例: {'R1': 1, 'R2': 1} → R1・R2 はこのリンクが断されてネイバー数が 1 であることを確認する。"
            "指定したデバイスに対してのみ完全一致チェックが行われ，未指定デバイスはネイバーが 1 以上であれば PASS。"
        ),
    )


class FaultPlan(BaseModel):
    """LLM が生成する障害シミュレーション計画。"""

    scenarios: list[FaultScenario] = Field(
        description="実行する障害シナリオのリスト。重要度の高い順に並べること。"
    )
    rationale: str = Field(
        description="シナリオ選択の根拠（どのリンクを選んだ理由、何を検証したいかを簡潔に）。"
    )


# ---------------------------------------------------------------------------
# プロンプト構築
# ---------------------------------------------------------------------------


def _load_system_prompt() -> str:
    """fault_simulator プロンプトを返す（prompts.load_agent_prompt のラッパー）。"""
    return load_agent_prompt("fault_simulator")


def _build_fault_plan_messages(
    state: AgentState,
    links: list[dict],
) -> list[dict[str, str]]:
    """障害計画立案用のメッセージを組み立てる。"""
    system_prompt = _load_system_prompt()
    links_text = "\n".join(
        f"- id={lk['id']}: {lk['node_a']} <-> {lk['node_b']}"
        for lk in links
    )
    user_content = (
        "## 障害シミュレーション計画立案依頼\n\n"
        "以下のネットワーク要件とトポロジーに基づき、"
        "障害シミュレーションシナリオを計画してください。\n\n"
        f"### 要件\n{state['requirement']}\n\n"
        f"### ラボ内リンク一覧\n{links_text or '(リンクなし)'}\n\n"
        "### トポロジーYAML\n"
        f"```yaml\n{state.get('topology_yaml', '(なし)')}\n```\n\n"
        "冗長性・フェイルオーバー・復旧の検証に重要なリンクを選択し、"
        "障害シナリオを計画してください。"
        "各シナリオで使用する link_id はリンク一覧の id フィールドの値を使用してください。"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


# ---------------------------------------------------------------------------
# テスト実行ヘルパー
# ---------------------------------------------------------------------------


def _check_ospf_exact_count(
    item: Any,
    testbed_yaml: str,
    expected_count: int,
) -> TestResult:
    """OSPF ネイバー数を期待値と完全一致で確認する。

    _execute_test の ospf_neighbors チェック（> 0）と異なり、
    障害中の「正確に N 本のネイバーを持つこと」を検証する。
    """
    from agentic_ni.tools import pyats_tools

    # テスト名に期待ネイバー数を明示（元の説明文の「」内容に依存しない）
    test_name = f"OSPF ネイバー数確認: {item.device} （障害中の期待値: {expected_count}）"

    try:
        data = pyats_tools.check_ospf_neighbors(testbed_yaml, item.device)
        actual = data["neighbors_up"]
        ok = actual == expected_count
        detail = f"{actual} neighbor(s) FULL (expected: {expected_count})"
        return TestResult(
            test=test_name,
            result="PASS" if ok else "FAIL",
            detail=detail,
        )
    except Exception as exc:  # noqa: BLE001
        return TestResult(
            test=test_name,
            result="FAIL",
            detail=f"テスト実行エラー: {type(exc).__name__}: {exc}",
        )


def _run_test_items(
    test_items_dicts: list[dict],
    testbed_yaml: str,
    label: str,
    expected_neighbor_counts: dict[str, int] | None = None,
) -> list[TestResult]:
    """test_plan_items（dict リスト）を TestItem に変換して実行する。

    Args:
        test_items_dicts: state["test_plan_items"] の各要素（dict）。
        testbed_yaml: pyATS テストベッド YAML 文字列。
        label: ログ表示用ラベル（例: "障害中", "復旧後"）。
        expected_neighbor_counts: 障害中に期待される OSPF ネイバー数。
            指定されたデバイスの ospf_neighbors チェックは完全一致になる。

    Returns:
        list[TestResult]: 各テスト項目の実行結果。
    """
    from agentic_ni.agents.validator import TestItem, _execute_test

    test_items = [TestItem(**d) for d in test_items_dicts]
    results: list[TestResult] = []
    for i, item in enumerate(test_items, 1):
        # 障害中の ospf_neighbors で期待値指定ありの場合は期待値入りの説明文を表示
        if (
            item.test_type == "ospf_neighbors"
            and expected_neighbor_counts
            and item.device in expected_neighbor_counts
        ):
            display_desc = (
                f"OSPF ネイバー数確認: {item.device}"
                f" （期待値: {expected_neighbor_counts[item.device]}）"
            )
        else:
            display_desc = item.description
        logger.info(
            f"        ({i}/{len(test_items)}) [{label}] {display_desc}",
        )
        # ospf_neighbors かつ期待値指定ありの場合は完全一致チェック
        if (
            item.test_type == "ospf_neighbors"
            and expected_neighbor_counts
            and item.device in expected_neighbor_counts
        ):
            result = _check_ospf_exact_count(
                item, testbed_yaml, expected_neighbor_counts[item.device]
            )
        else:
            result = _execute_test(item, testbed_yaml)
        mark = "✅ PASS" if result["result"] == "PASS" else "❌ FAIL"
        logger.info(f"               → {mark}  {result['detail']}")
        results.append(result)
    return results


# ---------------------------------------------------------------------------
# メインのエージェント関数
# ---------------------------------------------------------------------------


def run(state: AgentState) -> dict[str, Any]:
    """障害シミュレーションエージェントの LangGraph ノード関数。

    1. CML からリンク一覧を取得
    2. LLM で障害シナリオ計画を立案
    3. 各シナリオでリンク断 → テスト → リンク復旧 → テストを実施
    4. 結果を fault_scenario_results に格納して返す

    Args:
        state: 現在のエージェントステート。lab_id と test_plan_items が必須。

    Returns:
        dict: AgentState の更新差分。
    """
    from agentic_ni.tools import cml_tools, pyats_tools

    lab_id = state.get("lab_id", "")
    test_plan_items: list[dict] = state.get("test_plan_items", [])

    # ラボ ID が未設定の場合はスキップ
    if not lab_id:
        logger.info("  [障害シミュレーション] lab_id が未設定のためスキップします。")
        return {
            "fault_scenario_results": [],
        }

    # テスト計画が未設定の場合も何もできないのでスキップ
    if not test_plan_items:
        logger.info("  [障害シミュレーション] test_plan_items が空のためスキップします。")
        return {
            "fault_scenario_results": [],
        }

    # --- 1. リンク一覧取得 ---
    logger.info("  [障害シミュレーション 1/3] CML からリンク一覧を取得中...")
    try:
        links = cml_tools.get_lab_links(lab_id)
    except Exception as exc:  # noqa: BLE001
        logger.info(f"  [障害シミュレーション] リンク取得失敗: {exc}")
        return {
            "fault_scenario_results": [],
        }

    if not links:
        logger.info("  [障害シミュレーション] リンクが存在しないためスキップします。")
        return {
            "fault_scenario_results": [],
        }

    # --- 2. 障害計画立案 ---
    logger.info(
        f"  [障害シミュレーション 2/3] 障害シナリオを LLM に立案させています"
        f" ({len(links)} リンク)...",
    )
    llm = get_llm()
    structured_llm = llm.with_structured_output(FaultPlan, method="function_calling")
    plan: FaultPlan = structured_llm.invoke(_build_fault_plan_messages(state, links))
    logger.info(
        f"  [障害シミュレーション 2/3] 計画完了 ({len(plan.scenarios)} シナリオ): {plan.rationale}",
    )

    # --- 3. テストベッド取得 ---
    testbed_yaml = pyats_tools.build_testbed(
        lab_id, state.get("device_configs", {})
    )

    # --- 4. 各シナリオを実行 ---
    logger.info(f"  [障害シミュレーション 3/3] 障害シナリオを実行中...")
    scenario_results: list[FaultScenarioResult] = []

    for idx, scenario in enumerate(plan.scenarios, 1):
        logger.info(
            f"\n  ▶ シナリオ {idx}/{len(plan.scenarios)}: {scenario.scenario_name}",
        )

        # 4a. CML リンク停止（両端同時に line protocol down）
        try:
            cml_tools.set_link_state(lab_id, scenario.link_id, up=False)
        except KeyError as exc:
            logger.info(
                f"    ⚠ リンクが見つかりません ({exc})、シナリオをスキップします。",
            )
            continue
        logger.info(
            f"    CML リンク DOWN: {scenario.link_label}"
            f" ({scenario.wait_seconds}s 待機中...)",
        )
        time.sleep(scenario.wait_seconds)

        # 4b. 障害中テスト
        logger.info(f"    テスト実行（障害中）:")
        during_results = _run_test_items(
            test_plan_items,
            testbed_yaml,
            "障害中",
            expected_neighbor_counts=scenario.expected_ospf_neighbors or None,
        )

        # 4c. CML リンク復旧（両端同時に line protocol up）
        try:
            cml_tools.set_link_state(lab_id, scenario.link_id, up=True)
        except Exception as exc:  # noqa: BLE001
            logger.info(f"    ⚠ リンク復旧失敗 ({exc})")
        logger.info(
            f"    CML リンク UP（復旧）: {scenario.link_label}"
            f" ({scenario.wait_seconds}s 待機中...)",
        )
        time.sleep(scenario.wait_seconds)

        # 4d. 復旧後テスト
        logger.info(f"    テスト実行（復旧後）:")
        recovery_results = _run_test_items(test_plan_items, testbed_yaml, "復旧後")

        # 4e. 結果集計（判定基準: 復旧後のテストが全 PASS）
        passed = all(r["result"] == "PASS" for r in recovery_results)
        scenario_results.append(
            FaultScenarioResult(
                scenario_name=scenario.scenario_name,
                link_id=scenario.link_id,
                link_label=scenario.link_label,
                tests_during_fault=during_results,
                tests_after_recovery=recovery_results,
                passed=passed,
            )
        )
        mark = "✅ PASS" if passed else "❌ FAIL"
        logger.info(f"    シナリオ結果: {mark}")

    passed_count = sum(1 for r in scenario_results if r["passed"])
    logger.info(
        f"\n  [障害シミュレーション 完了] {passed_count}/{len(scenario_results)} シナリオ PASS",
    )

    return {
        "fault_scenario_results": scenario_results,
    }
