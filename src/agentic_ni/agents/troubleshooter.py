"""トラブルシューティングエージェント (Phase H)。

既存の稼働中 CML ラボに接続し、
「診断 → インクリメンタル修正 → 検証」のサイクルで問題を解決する。

Phase A/B との違い:
  - 起点: 既存ラボ ID（要件入力からの設計・デプロイなし）
  - 修正方式: configure terminal による差分適用（wipe + restart なし）
  - 主体: 診断エージェント → 修正エージェント（設計エージェントは使わない）

フロー:
  1. collect  — 全機器の running-config と show コマンド出力を収集
  2. diagnose — LLM が根本原因を診断
  3. fix      — LLM が差分コマンドを生成し pyATS で投入
  4. verify   — テストを実行（graph.py 側で実施）
  5. 全 PASS → レポート / FAIL & リトライ残 → collect に戻る
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from agentic_ni.llm import get_llm
from agentic_ni.state import AgentState, TroubleshootFixRecord

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


# ---------------------------------------------------------------------------
# Pydantic スキーマ
# ---------------------------------------------------------------------------


class DiagnosisResult(BaseModel):
    """LLM が生成する診断結果。"""

    root_cause: str = Field(
        description="根本原因を 1〜2 文で具体的に記述する。"
    )
    affected_devices: list[str] = Field(
        description="影響を受けているデバイス名のリスト（ノードラベルと一致させること）。"
    )
    severity: Literal["config_error", "topology_error", "timing_issue", "unknown"] = Field(
        description=(
            "config_error: コンフィグミス（network 文漏れ、neighbor 設定ミスなど）。"
            "topology_error: 接続構成・IP アドレス設計の問題。"
            "timing_issue: タイマーや収束待ちに関する問題。"
            "unknown: 原因が特定できない場合。"
        )
    )
    summary: str = Field(
        description="診断の概要（1〜3 文）。修正方針の方向性も含める。"
    )


class FixCommand(BaseModel):
    """1 デバイスへの修正コマンド 1 件。"""

    device: str = Field(
        description="修正対象デバイス名（ノードラベルと一致させること）。"
    )
    commands: str = Field(
        description=(
            "configure terminal に流すコマンド（複数行可）。"
            "interface/router セクションへの入り方も含めること。"
            "例:\n"
            "  interface GigabitEthernet0/0\n"
            "   ip ospf network point-to-point\n"
        )
    )
    rollback_commands: str = Field(
        default="",
        description=(
            "修正を元に戻す no コマンド（ロールバック用）。"
            "ロールバックが不要または不明な場合は空文字にする。"
        ),
    )
    description: str = Field(
        description="この修正の目的・内容を 1 文で説明する。"
    )


class FixPlan(BaseModel):
    """LLM が生成する修正計画。"""

    fixes: list[FixCommand] = Field(
        description="適用する修正のリスト。依存関係がある場合は順序を考慮して並べること。"
    )
    rationale: str = Field(
        description="この修正計画の根拠（なぜこの修正で問題が解決するか）を説明する。"
    )


# ---------------------------------------------------------------------------
# プロンプト構築
# ---------------------------------------------------------------------------


def _load_system_prompt() -> str:
    """troubleshooter_system.md を読み込んで返す。"""
    path = _PROMPTS_DIR / "troubleshooter_system.md"
    if not path.exists():
        return (
            "あなたはネットワークトラブルシューティングの専門家です。"
            "稼働中のルーターの状態を分析し、根本原因を特定して"
            "インクリメンタルな修正コマンドを生成してください。"
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
        # 非常に長い場合は重要セクションのみ残す
        if len(cfg) > 2500:
            cfg = cfg[:2500] + "\n... (省略)"
        parts.append(f"**running-config:**\n```\n{cfg}\n```")
        for cmd, output in state_data.get("show_outputs", {}).items():
            parts.append(
                f"**{cmd}:**\n```\n{str(output)[:600]}\n```"
            )
    return "\n\n".join(parts) if parts else "(状態なし)"


def _format_fix_history(fix_records: list[TroubleshootFixRecord]) -> str:
    """修正履歴を LLM 向けにフォーマットする。"""
    if not fix_records:
        return "(なし)"
    lines: list[str] = []
    for i, r in enumerate(fix_records, 1):
        status = "✅ 成功" if r.get("success") else "❌ 失敗"
        lines.append(
            f"{i}. [{r['device']}] {status} — {r.get('description', '')}\n"
            f"   ```\n{r['commands']}\n   ```"
        )
        if r.get("error"):
            lines.append(f"   エラー: {r['error']}")
    return "\n".join(lines)


def _build_diagnosis_messages(
    state: AgentState, collected_state: dict
) -> list[dict[str, str]]:
    """診断用のメッセージを組み立てる。"""
    system_prompt = _load_system_prompt()
    device_state_text = _format_collected_state(collected_state)
    fix_history_text = _format_fix_history(state.get("fix_records", []))
    failed_tests_text = "\n".join(
        f"- [{r['test']}] {r['detail']}"
        for r in state.get("test_results", [])
        if r["result"] == "FAIL"
    ) or "(テスト未実施または全 PASS)"

    user_content = (
        "## 診断依頼\n\n"
        f"### 問題の説明\n{state.get('troubleshoot_issue') or '(説明なし)'}\n\n"
        f"### 要件（本来あるべき状態）\n{state.get('requirement', '(なし)')}\n\n"
        f"### 現在の機器状態\n\n{device_state_text}\n\n"
        f"### 失敗しているテスト\n{failed_tests_text}\n\n"
        f"### これまでの修正試行\n{fix_history_text}\n\n"
        "根本原因を特定し、診断結果を返してください。\n"
        "これまでの修正試行がある場合は、それらを考慮して新しい根本原因を推論してください。"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


def _build_fix_plan_messages(
    state: AgentState,
    diagnosis: str,
    collected_state: dict,
) -> list[dict[str, str]]:
    """修正計画立案用のメッセージを組み立てる。"""
    system_prompt = _load_system_prompt()
    device_state_text = _format_collected_state(collected_state)
    fix_history_text = _format_fix_history(state.get("fix_records", []))

    user_content = (
        "## 修正計画立案依頼\n\n"
        f"### 診断結果\n{diagnosis}\n\n"
        f"### 現在の機器状態\n\n{device_state_text}\n\n"
        f"### これまでの修正試行\n{fix_history_text}\n\n"
        "以下の制約に従って修正コマンドを生成してください:\n"
        "- `configure terminal` モードで適用できる IOS コマンドのみ使用する\n"
        "- 既に試みた修正の繰り返しは避ける\n"
        "- 各 FixCommand の device はノードラベルと一致させること\n"
        "- rollback_commands には no コマンドで元に戻す方法を記述すること"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


# ---------------------------------------------------------------------------
# エージェント関数（グラフノードから呼ばれる）
# ---------------------------------------------------------------------------


def run_collect(state: AgentState) -> dict[str, Any]:
    """既存ラボの全機器から running-config と show コマンド出力を収集する。

    Args:
        state: 現在のエージェントステート。troubleshoot_lab_id または lab_id が必須。

    Returns:
        dict: collected_state と device_configs の更新差分。
    """
    from agentic_ni.tools import cml_tools, pyats_tools

    lab_id = state.get("troubleshoot_lab_id") or state.get("lab_id", "")
    if not lab_id:
        print("  [収集] lab_id が未設定のためスキップします。", flush=True)
        return {"collected_state": {}, "device_configs": {}}

    # CML からデバイス一覧を取得
    nodes = cml_tools.get_lab_nodes(lab_id)
    booted_nodes = [n for n in nodes if n["state"] == "BOOTED"]
    if not booted_nodes:
        print(f"  [収集] 起動済みノードがありません (lab_id={lab_id})", flush=True)
        return {"collected_state": {}, "device_configs": {}}

    # 空の device_configs でテストベッドを構築（デバイス名を渡すだけで接続情報は CML から取得）
    device_names: dict[str, str] = {n["label"]: "" for n in booted_nodes}
    testbed_yaml = pyats_tools.build_testbed(lab_id, device_names)

    collected_state: dict[str, Any] = {}
    device_configs: dict[str, str] = {}

    for node in booted_nodes:
        device_name = node["label"]
        print(f"    収集中: {device_name} ...", flush=True)
        try:
            state_data = pyats_tools.collect_device_state(testbed_yaml, device_name)
            collected_state[device_name] = state_data
            device_configs[device_name] = state_data.get("running_config", "")
        except Exception as exc:  # noqa: BLE001
            print(f"    ⚠ {device_name} の収集失敗: {exc}", flush=True)
            collected_state[device_name] = {"error": str(exc)}
            device_configs[device_name] = ""

    return {
        "collected_state": collected_state,
        "device_configs": device_configs,
        "lab_id": lab_id,
    }


def run_diagnose(state: AgentState) -> dict[str, Any]:
    """収集した状態と失敗テストを LLM で分析し、根本原因を診断する。

    Args:
        state: collected_state・test_results・fix_records を含むステート。

    Returns:
        dict: diagnosis（診断テキスト）の更新差分。
    """
    collected_state = state.get("collected_state", {})
    llm = get_llm()
    structured_llm = llm.with_structured_output(DiagnosisResult, method="function_calling")
    result: DiagnosisResult = structured_llm.invoke(
        _build_diagnosis_messages(state, collected_state)
    )
    diagnosis_text = (
        f"## 根本原因\n{result.root_cause}\n\n"
        f"## 影響デバイス\n{', '.join(result.affected_devices) or '(なし)'}\n\n"
        f"## 重大度\n{result.severity}\n\n"
        f"## 概要\n{result.summary}"
    )
    print(f"  診断: [{result.severity}] {result.root_cause}", flush=True)
    return {"diagnosis": diagnosis_text}


def run_fix(state: AgentState) -> dict[str, Any]:
    """診断結果に基づき修正コマンドを生成し、各デバイスに適用する。

    Args:
        state: diagnosis・collected_state・fix_records を含むステート。

    Returns:
        dict: fix_records（更新済み）と troubleshoot_retry_count の更新差分。
    """
    from agentic_ni.tools import pyats_tools

    collected_state = state.get("collected_state", {})
    diagnosis = state.get("diagnosis", "")
    lab_id = state.get("troubleshoot_lab_id") or state.get("lab_id", "")
    device_configs = state.get("device_configs", {})

    # 修正計画を LLM に生成させる
    llm = get_llm()
    structured_llm = llm.with_structured_output(FixPlan, method="function_calling")
    fix_plan: FixPlan = structured_llm.invoke(
        _build_fix_plan_messages(state, diagnosis, collected_state)
    )
    print(
        f"  修正計画: {len(fix_plan.fixes)} 件 — {fix_plan.rationale}",
        flush=True,
    )

    if not fix_plan.fixes:
        print("  修正コマンドがありません。スキップします。", flush=True)
        return {
            "fix_records": list(state.get("fix_records", [])),
            "troubleshoot_retry_count": state.get("troubleshoot_retry_count", 0) + 1,
        }

    # テストベッドを構築して各修正を適用
    testbed_yaml = pyats_tools.build_testbed(lab_id, device_configs)
    new_fix_records = list(state.get("fix_records", []))

    for fix in fix_plan.fixes:
        print(f"    適用中 [{fix.device}]: {fix.description}", flush=True)
        try:
            pyats_tools.apply_incremental_config(testbed_yaml, fix.device, fix.commands)
            new_fix_records.append(
                TroubleshootFixRecord(
                    device=fix.device,
                    commands=fix.commands,
                    rollback_commands=fix.rollback_commands,
                    success=True,
                    error="",
                    description=fix.description,
                )
            )
            print(f"    ✅ 適用成功: {fix.device}", flush=True)
        except Exception as exc:  # noqa: BLE001
            error_msg = f"{type(exc).__name__}: {exc}"
            new_fix_records.append(
                TroubleshootFixRecord(
                    device=fix.device,
                    commands=fix.commands,
                    rollback_commands=fix.rollback_commands,
                    success=False,
                    error=error_msg,
                    description=fix.description,
                )
            )
            print(f"    ❌ 適用失敗: {fix.device} — {error_msg}", flush=True)

    return {
        "fix_records": new_fix_records,
        "troubleshoot_retry_count": state.get("troubleshoot_retry_count", 0) + 1,
    }
