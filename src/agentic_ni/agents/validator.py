"""検証エージェント。

CMLへのデプロイ・テスト実行・失敗推論を行う LangGraph ノード。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from agentic_ni.llm import get_llm
from agentic_ni.state import AgentState, TestResult

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

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


# ---------------------------------------------------------------------------
# プロンプト構築
# ---------------------------------------------------------------------------


def _load_system_prompt(prompt_set: str = "demo") -> str:
    """validator プロンプトを構築して返す。

    読み込み方針:
    1. prompts/validator_system.md をベースとして読み込む
    2. prompts/<set>/validator.md が存在すれば、セット固有要件として末尾に結合する

    後方互換:
    - prompts/<set>/validator_system.md が存在する場合は単独使用（旧形式）
    """
    base_path = _PROMPTS_DIR / "validator_system.md"
    set_specific_path = _PROMPTS_DIR / prompt_set / "validator.md"
    set_legacy_path = _PROMPTS_DIR / prompt_set / "validator_system.md"

    # 後方互換: セット内に validator_system.md があれば単独使用
    if set_legacy_path.exists():
        return set_legacy_path.read_text(encoding="utf-8")

    if not base_path.exists():
        raise FileNotFoundError(
            f"validator_system.md が見つかりません: {base_path}"
        )
    base = base_path.read_text(encoding="utf-8")

    if set_specific_path.exists():
        specific = set_specific_path.read_text(encoding="utf-8")
        return f"{base}\n\n---\n\n{specific}"

    return base


def list_prompt_sets() -> list[str]:
    """利用可能なプロンプトセット一覧を返す。"""
    return sorted(
        d.name for d in _PROMPTS_DIR.iterdir()
        if d.is_dir() and (d / "validator_system.md").exists()
    )


def _build_test_plan_messages(state: AgentState) -> list[dict[str, str]]:
    """テスト計画立案用のメッセージを組み立てる。"""
    system_prompt = _load_system_prompt(state.get("prompt_set", "demo"))
    nodes_info = "\n".join(
        f"- {dev}" for dev in state.get("device_configs", {}).keys()
    )
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
        for dev, cfg in state.get("device_configs", {}).items()
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
# デプロイ処理
# ---------------------------------------------------------------------------


def _deploy(state: AgentState) -> str:
    """CMLにトポロジーをデプロイし、全ノードが起動するまで待機する。

    Returns:
        str: デプロイしたラボのID。

    Raises:
        RuntimeError: デプロイまたは起動待機に失敗した場合。
    """
    from agentic_ni.tools import cml_tools

    topology_yaml = state.get("topology_yaml", "")
    device_configs = state.get("device_configs", {})

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
    print(f"  [1/4] CML にデプロイ中...", flush=True)
    try:
        lab_id = _deploy(state)
    except Exception as exc:  # noqa: BLE001
        print(f"  [1/4] デプロイ失敗: {exc}", flush=True)
        return {
            "lab_id": state.get("lab_id", ""),
            "test_results": [],
            "error_log": f"デプロイ失敗: {type(exc).__name__}: {exc}",
            "retry_count": new_retry_count,
        }
    print(f"  [1/4] デプロイ完了 (lab_id={lab_id})", flush=True)

    # --- 2. テスト計画立案 ---
    print(f"  [2/4] テスト計画を立案中...", flush=True)
    structured_llm = llm.with_structured_output(TestPlan, method="function_calling")
    plan: TestPlan = structured_llm.invoke(_build_test_plan_messages(state))
    print(f"  [2/4] テスト計画完了 ({len(plan.tests)} 件)", flush=True)

    # --- 3. テストベッド取得 ---
    from agentic_ni.tools import pyats_tools

    testbed_yaml = pyats_tools.build_testbed(lab_id, state.get("device_configs", {}))

    # --- 4. テスト実行 ---
    print(f"  [3/4] テストを実行中...", flush=True)
    test_results: list[TestResult] = []
    for i, item in enumerate(plan.tests, 1):
        print(f"        ({i}/{len(plan.tests)}) {item.description}", flush=True)
        result = _execute_test(item, testbed_yaml)
        mark = "✅ PASS" if result["result"] == "PASS" else "❌ FAIL"
        print(f"               → {mark}  {result['detail']}", flush=True)
        test_results.append(result)

    # --- 5. 失敗分析 ---
    failed = [r for r in test_results if r["result"] == "FAIL"]
    error_log = ""

    if failed:
        print(f"  [4/4] 失敗原因を AI が分析中... ({len(failed)} 件失敗)", flush=True)
        analysis_llm = llm.with_structured_output(FailureAnalysis, method="function_calling")
        analysis: FailureAnalysis = analysis_llm.invoke(
            _build_analysis_messages(state, failed)
        )
        error_log = (
            f"## 根本原因\n{analysis.root_cause}\n\n"
            f"## 修正依頼\n{analysis.suggestion}"
        )
        print(f"  [4/4] 分析完了", flush=True)
        print(f"", flush=True)
        print(f"  『根本原因』 {analysis.root_cause}", flush=True)
        print(f"  『修正依頼』 {analysis.suggestion}", flush=True)
        print(f"", flush=True)
    else:
        print(f"  [4/4] 全テスト PASS", flush=True)

    # error_history に今回のエラーを追記（RAG保存用）
    error_history = list(state.get("error_history", []))
    if error_log:
        error_history.append(error_log)

    return {
        "lab_id": lab_id,
        "test_results": test_results,
        "error_log": error_log,
        "error_history": error_history,
        "retry_count": new_retry_count,
    }
