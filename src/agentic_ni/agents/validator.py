"""検証エージェント。

CMLへのデプロイ・テスト実行・失敗推論を行う LangGraph ノード。
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Literal

from pydantic import BaseModel, Field

from agentic_ni.agents.prompts import load_agent_prompt, list_prompt_sets
from agentic_ni.logger import get_logger
from agentic_ni.llm import get_llm
from agentic_ni.state import AgentState, TestResult, load_device_configs

logger = get_logger(__name__)

# テスト並列実行の最大ワーカー数（環境変数 MAX_TEST_WORKERS で上書き可能）。
# 1 を指定すると逐次実行（デバッグ・接続安定性優先）になる。
MAX_TEST_WORKERS: int = int(os.getenv("MAX_TEST_WORKERS", "8"))

# ---------------------------------------------------------------------------
# Pydantic スキーマ
# ---------------------------------------------------------------------------


class TestItem(BaseModel):
    """テスト計画の1項目。"""

    test_type: Literal[
        "ospf_neighbors",
        "bgp_summary",
        "ping",
        "vlan_interfaces",
        "route_table",
        "interface_status",
        "traceroute",
        "bgp_path",
    ] = Field(
        description="実行するテストの種別。"
    )
    device: str = Field(description="テストを実行するデバイス名（ノードlabelと一致させること）。")
    target: str | None = Field(
        default=None,
        description=(
            "テスト対象の値。"
            "ping/traceroute: 宛先IPアドレス。"
            "route_table/bgp_path: 確認するプレフィックス（例: '1.1.1.1/32'）。"
            "interface_status: インターフェース名（例: 'GigabitEthernet0/0'）。"
            "その他のテストでは null。"
        ),
    )
    description: str = Field(description="このテスト項目の目的説明（ログ用）。")


class TestPlan(BaseModel):
    """LLMが生成するテスト計画。"""

    tests: list[TestItem] = Field(description="実行するテスト項目のリスト。")
    rationale: str = Field(description="テスト計画の根拠説明。")


class FailureAnalysis(BaseModel):
    """失敗したテストに対するLLMの推論結果。"""

    root_cause: str = Field(
        description="失敗の根本原因（技術的に具体的に1〜2文で記述）。"
    )
    suggestion: str = Field(
        description="設計エージェントへの修正依頼。"
        "どのデバイスのどの設定を変更すべきかを具体的に記述する。"
    )
    affected_devices: list[str] = Field(
        description=(
            "コンフィグの修正が必要なデバイス名のリスト。"
            "直接 FAIL したデバイスだけでなく、原因となっている設定ミスを持つデバイスを含めること。"
            "例: ['R1', 'R3']。全デバイスが対象の場合は空リスト [] を返す。"
        )
    )


# ---------------------------------------------------------------------------
# プロンプト構築
# ---------------------------------------------------------------------------


def _load_system_prompt(prompt_set: str = "demo") -> str:
    """validator プロンプトを返す（prompts.load_agent_prompt のラッパー）。"""
    return load_agent_prompt("validator", prompt_set)


def _build_test_plan_messages(state: AgentState) -> list[dict[str, str]]:
    """テスト計画立案用のメッセージを組み立てる。"""
    system_prompt = _load_system_prompt(state.get("prompt_set", "demo"))
    # Strategy E: device_config_paths が設定されていればそこからデバイス名を取得
    device_names = list(
        state.get("device_config_paths", {}).keys()  # type: ignore[typeddict-item]
        or state.get("device_configs", {}).keys()
    )
    nodes_info = "\n".join(f"- {dev}" for dev in device_names)

    user_content = (
        "## テスト計画立案依頼\n\n"
        "以下の要件と設計内容に対して、検証すべきテスト計画を作成してください。\n\n"
        f"### 要件\n{state['requirement']}\n\n"
        f"### デプロイ済みノード\n{nodes_info or '(情報なし)'}\n\n"
        "### トポロジーYAML\n"
        f"```yaml\n{state.get('topology_yaml', '(なし)')}\n```\n\n"
        "要件を検証するために必要なテスト項目を列挙してください。"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


def _build_analysis_messages(
    state: AgentState, failed_results: list[TestResult]
) -> list[dict[str, str]]:
    """失敗分析用のメッセージを組み立てる。"""
    system_prompt = _load_system_prompt(state.get("prompt_set", "demo"))
    failures_text = "\n".join(
        f"- [{r['test']}] {r['detail']}" for r in failed_results
    )
    configs_text = "\n".join(
        f"**{dev}**:\n```\n{cfg}\n```"
        for dev, cfg in load_device_configs(state).items()
    )
    user_content = (
        "## 失敗分析依頼\n\n"
        "以下のテストが失敗しました。根本原因を推論し、"
        "設計エージェントへの修正依頼を作成してください。\n\n"
        f"### 要件\n{state['requirement']}\n\n"
        f"### 失敗したテスト\n{failures_text}\n\n"
        f"### 現在の機器コンフィグ\n{configs_text or '(情報なし)'}\n\n"
        "どの設定が原因で、どのように修正すべきかを具体的に記述してください。"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


# ---------------------------------------------------------------------------
# テスト実行ディスパッチャ
# ---------------------------------------------------------------------------


def _execute_test(item: TestItem, testbed_yaml: str) -> TestResult:
    """テスト項目を実行し TestResult を返す。

    pyATS/Genie が未インストールの場合は ImportError を送出する。
    CMLへの接続に失敗した場合はエラー詳細を TestResult に格納する。
    """
    from agentic_ni.tools import pyats_tools

    try:
        if item.test_type == "ospf_neighbors":
            data = pyats_tools.check_ospf_neighbors(testbed_yaml, item.device)
            ok = data["neighbors_up"] > 0
            detail = (
                f"{data['neighbors_up']} neighbor(s) FULL"
                if ok
                else f"neighbors_up=0, raw={data.get('neighbors', [])}"
            )

        elif item.test_type == "bgp_summary":
            data = pyats_tools.check_bgp_summary(testbed_yaml, item.device)
            ok = data["peers_established"] > 0
            if ok:
                detail = f"{data['peers_established']} peer(s) Established"
            else:
                peers = data.get("peers", [])
                if peers:
                    peer_info = ", ".join(
                        f"{p['peer']}:state={p.get('state', '?')}" for p in peers
                    )
                    detail = f"peers_established=0 ({peer_info})"
                else:
                    detail = "peers_established=0 (BGPピアが未検出: neighbor設定がない、またはセッションが開始されていない可能性)"

        elif item.test_type == "ping":
            if not item.target:
                return TestResult(test=item.description, result="FAIL", detail="target が未指定")
            # IPアドレスの検証（"R2's IP Address" のような説明文を弾く）
            import ipaddress
            try:
                ipaddress.ip_address(item.target)
            except ValueError:
                return TestResult(
                    test=item.description,
                    result="FAIL",
                    detail=f"テスト実行エラー: ping宛先が有効なIPアドレスではありません: {item.target!r} (例: '10.0.0.2')",
                )
            ok = pyats_tools.check_ping(testbed_yaml, item.device, item.target)
            detail = f"ping {item.target} {'OK' if ok else 'FAILED'}"

        elif item.test_type == "vlan_interfaces":
            data = pyats_tools.check_vlan_interfaces(testbed_yaml, item.device)
            ok = len(data["vlans"]) > 0 or data["interfaces_up"] > 0
            detail = (
                f"vlans={list(data['vlans'].keys())}, interfaces_up={data['interfaces_up']}"
            )

        elif item.test_type == "route_table":
            if not item.target:
                return TestResult(test=item.description, result="FAIL", detail="target（プレフィックス）が未指定")
            data = pyats_tools.check_route_table(testbed_yaml, item.device, item.target)
            ok = data["found"]
            detail = (
                f"prefix={item.target} found via {data['protocol']}, next_hop={data['next_hop']}"
                if ok
                else f"prefix={item.target} not found in routing table"
            )

        elif item.test_type == "interface_status":
            if not item.target:
                return TestResult(test=item.description, result="FAIL", detail="target（インターフェース名）が未指定")
            data = pyats_tools.check_interface_status(testbed_yaml, item.device, item.target)
            ok = data["both_up"]
            line = "up" if data["line_up"] else "down"
            proto = "up" if data["protocol_up"] else "down"
            detail = f"{item.target}: line={line}, protocol={proto}"

        elif item.test_type == "traceroute":
            if not item.target:
                return TestResult(test=item.description, result="FAIL", detail="target（宛先IP）が未指定")
            data = pyats_tools.check_traceroute(testbed_yaml, item.device, item.target)
            ok = data["reached"]
            detail = (
                f"reached {item.target} in {data['hop_count']} hop(s): {' -> '.join(data['hops'])}"
                if ok
                else f"could not reach {item.target}, hops={data['hops']}"
            )

        elif item.test_type == "bgp_path":
            if not item.target:
                return TestResult(test=item.description, result="FAIL", detail="target（プレフィックス）が未指定")
            data = pyats_tools.check_bgp_path(testbed_yaml, item.device, item.target)
            ok = data["found"]
            detail = (
                f"prefix={item.target} best_next_hop={data['best_next_hop']}, origin={data['origin']}"
                if ok
                else f"prefix={item.target} not found in BGP table"
            )

        else:
            return TestResult(test=item.description, result="FAIL", detail=f"未知のtest_type: {item.test_type}")

        return TestResult(
            test=item.description,
            result="PASS" if ok else "FAIL",
            detail=detail,
        )

    except Exception as exc:  # noqa: BLE001
        return TestResult(
            test=item.description,
            result="FAIL",
            detail=f"テスト実行エラー: {type(exc).__name__}: {exc}",
        )


# ---------------------------------------------------------------------------
# テスト実行（逐次 / 並列）
# ---------------------------------------------------------------------------


def _run_tests(plan: TestPlan, testbed_yaml: str) -> list[TestResult]:
    """テスト計画を実行し、元の順序で TestResult リストを返す（Strategy D: 並列実行）。

    ``MAX_TEST_WORKERS`` が 1 の場合、または計画が 1 件以下の場合は逐次実行する。
    それ以外は ``ThreadPoolExecutor`` で同時実行し、完了したテストから都度ログを出力する。
    結果は常に ``plan.tests`` の元の順序で返す。

    同一デバイスへの並列接続が問題になる場合は環境変数 ``MAX_TEST_WORKERS=1`` で
    逐次実行に切り替えられる。

    Args:
        plan: LLM が生成したテスト計画。
        testbed_yaml: pyATS テストベッド YAML 文字列。

    Returns:
        list[TestResult]: ``plan.tests`` と同じ順序のテスト結果リスト。
    """
    total = len(plan.tests)
    workers = MAX_TEST_WORKERS

    if workers <= 1 or total <= 1:
        # --- 逐次実行（従来動作 / デバッグモード）---
        results: list[TestResult] = []
        for i, item in enumerate(plan.tests, 1):
            logger.info(f"        ({i}/{total}) {item.description}")
            result = _execute_test(item, testbed_yaml)
            mark = "✅ PASS" if result["result"] == "PASS" else "❌ FAIL"
            logger.info(f"               → {mark}  {result['detail']}")
            results.append(result)
        return results

    # --- 並列実行 ---
    results_by_index: dict[int, TestResult] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_index = {
            executor.submit(_execute_test, item, testbed_yaml): i
            for i, item in enumerate(plan.tests)
        }
        for future in as_completed(future_to_index):
            idx = future_to_index[future]
            item = plan.tests[idx]
            result = future.result()
            results_by_index[idx] = result
            mark = "✅ PASS" if result["result"] == "PASS" else "❌ FAIL"
            logger.info(
                f"        [{idx + 1}/{total}] {item.description} → {mark}  {result['detail']}",
            )

    return [results_by_index[i] for i in range(total)]


# ---------------------------------------------------------------------------
# デプロイ処理
# ---------------------------------------------------------------------------


def _deploy(state: AgentState) -> str:
    """デプロイ済みラボの再利用、または新規デプロイを行う。

    skip_deploy=True の場合はデプロイを一切追わず、既存の lab_id を返す。
    """
    from agentic_ni.tools import cml_tools

    # skip_deploy=True: 既存ラボをそのまま利用（再起動・コンフィグ更新なし）
    if state.get("skip_deploy", False):
        existing_id = state.get("lab_id", "")
        if existing_id:
            logger.info(f"    既存ラボを再利用（デプロイスキップ）: lab_id={existing_id}")
            return existing_id

    topology_yaml = state.get("topology_yaml", "")
    device_configs = load_device_configs(state)

    # 既存ラボがあれば、まずコンフィグ更新・再起動を試みる（トポロジー再作成なし）
    old_lab_id = state.get("lab_id", "")
    if old_lab_id:
        try:
            return cml_tools.update_configs_and_restart(old_lab_id, device_configs)
        except Exception:  # noqa: BLE001
            # ラボが消えていた・ノード不一致などの場合はフルデプロイへ
            pass

    # 内部で起動待ちまで完了する
    lab_title = f"agentic-ni-{state.get('prompt_set', 'demo')}"
    lab_id = cml_tools.deploy_lab(topology_yaml, device_configs, title=lab_title)
    return lab_id


# ---------------------------------------------------------------------------
# メインのエージェント関数
# ---------------------------------------------------------------------------


def run(state: AgentState) -> dict[str, Any]:
    """検証エージェントの LangGraph ノード関数。

    1. CMLへトポロジーをデプロイ
    2. LLMにテスト計画を立案させる
    3. テストを実行
    4. 失敗があればLLMに原因推論させ error_log に格納
    5. 更新されたステートを返す

    Args:
        state: 現在のエージェントステート。

    Returns:
        dict: AgentState の更新差分。
    """
    llm = get_llm()
    new_retry_count = state.get("retry_count", 0) + 1
    trial = new_retry_count

    # --- 1. デプロイ ---
    logger.info(f"  [1/4] CML にデプロイ中...")
    try:
        lab_id = _deploy(state)
    except Exception as exc:  # noqa: BLE001
        logger.info(f"  [1/4] デプロイ失敗: {exc}")
        return {
            "lab_id": state.get("lab_id", ""),
            "test_results": [],
            "error_log": f"デプロイ失敗: {type(exc).__name__}: {exc}",
            "retry_count": new_retry_count,
        }
    logger.info(f"  [1/4] デプロイ完了 (lab_id={lab_id})")

    # --- 2. テスト計画立案 ---
    logger.info(f"  [2/4] テスト計画を立案中...")
    structured_llm = llm.with_structured_output(TestPlan, method="function_calling")
    plan: TestPlan = structured_llm.invoke(_build_test_plan_messages(state))
    logger.info(f"  [2/4] テスト計画完了 ({len(plan.tests)} 件)")

    # --- 3. テストベッド取得 ---
    from agentic_ni.tools import pyats_tools

    testbed_yaml = pyats_tools.build_testbed(lab_id, load_device_configs(state))

    # --- 4. テスト実行 ---
    mode_label = "逐次" if MAX_TEST_WORKERS <= 1 else f"並列 最大 {MAX_TEST_WORKERS} workers"
    logger.info(f"  [3/4] テストを実行中... ({mode_label})")
    test_results = _run_tests(plan, testbed_yaml)

    # --- 5. 失敗分析 ---
    failed = [r for r in test_results if r["result"] == "FAIL"]
    error_log = ""

    if failed:
        logger.info(f"  [4/4] 失敗原因を AI が分析中... ({len(failed)} 件失敗)")
        analysis_llm = llm.with_structured_output(FailureAnalysis, method="function_calling")
        analysis: FailureAnalysis = analysis_llm.invoke(
            _build_analysis_messages(state, failed)
        )
        error_log = (
            f"## 根本原因\n{analysis.root_cause}\n\n"
            f"## 修正依頼\n{analysis.suggestion}"
        )
        logger.info(f"  [4/4] 分析完了")
        logger.info(f"")
        logger.info(f"  『根本原因』 {analysis.root_cause}")
        logger.info(f"  『修正依頼』 {analysis.suggestion}")
        logger.info(f"")
    else:
        logger.info(f"  [4/4] 全テスト PASS")

    return {
        "lab_id": lab_id,
        "test_results": test_results,
        "test_plan_items": [item.model_dump() for item in plan.tests],
        "error_log": error_log,
        "retry_count": new_retry_count,
        "failed_devices": analysis.affected_devices if failed else [],
    }
