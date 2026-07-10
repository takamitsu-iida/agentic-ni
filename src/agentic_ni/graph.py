"""LangGraph グラフの組み立て。

Phase 7: Human-in-the-Loop、レポートフォーマット整備、E2E統合済み。
障害シミュレーション（リンク断・復旧・再テスト）対応済み。
Phase D: 設計ドキュメント自動生成（IP台帳・ルーティング設計書・コンフィグ保存）対応済み。
Phase H: トラブルシューティングモード（既存ラボへのインクリメンタル修正）対応済み。
Phase E: 設計分析（--analyze）・改善計画生成（--improve）対応済み。
Phase I Step 2: 実機適用 live_precheck_node 対応済み。
"""

from __future__ import annotations

import csv
import io
import ipaddress
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from agentic_ni.agents import architect, analyzer, fault_simulator, troubleshooter, validator
from agentic_ni.state import AgentState, FaultScenarioResult, LiveApplyRecord, load_device_configs

load_dotenv()

MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "5"))
TROUBLESHOOT_MAX_RETRIES: int = int(os.getenv("TROUBLESHOOT_MAX_RETRIES", "3"))


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
    if state.get("use_provided_topology"):
        print("  >>> LLM にコンフィグを生成させています（トポロジーは提供済み）...", flush=True)
    else:
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
    device_configs = load_device_configs(state)
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
    # Phase D: 設計ドキュメントを生成・保存する
    prompt_set: str = state.get("prompt_set", "demo")
    out_dir = Path("configs") / prompt_set
    docs_section = _generate_design_docs(state, out_dir)
    report += docs_section
    _save_to_rag(state)
    return {"final_report": report}


def _save_to_rag(state: AgentState) -> None:
    """(deprecated: 実行ログ RAG 機能は廃止されました。何もしない。)"""
    return


# ---------------------------------------------------------------------------
# Phase D: 設計ドキュメント生成
# ---------------------------------------------------------------------------


def _parse_ip_ledger(device_configs: dict[str, str]) -> list[dict]:
    """デバイスコンフィグから全インターフェースの IP アドレスを抽出する。

    Args:
        device_configs: {デバイス名: コンフィグテキスト} のマッピング。

    Returns:
        list[dict]: 各インターフェースのレコードリスト。
            各要素は {"device", "interface", "ip_address", "prefix_length", "cidr", "subnet"}。
    """
    rows: list[dict] = []
    for device, config in device_configs.items():
        current_intf: str | None = None
        for line in config.splitlines():
            m = re.match(r"^interface\s+(\S+)", line)
            if m:
                current_intf = m.group(1)
                continue
            if current_intf is None:
                continue
            m = re.match(
                r"\s+ip address\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+\.\d+\.\d+\.\d+)", line
            )
            if m:
                ip_addr, mask = m.group(1), m.group(2)
                try:
                    net = ipaddress.IPv4Network(f"{ip_addr}/{mask}", strict=False)
                    prefix_len = net.prefixlen
                    subnet = str(net)
                except ValueError:
                    prefix_len, subnet = 0, ""
                rows.append(
                    {
                        "device": device,
                        "interface": current_intf,
                        "ip_address": ip_addr,
                        "prefix_length": prefix_len,
                        "cidr": f"{ip_addr}/{prefix_len}",
                        "subnet": subnet,
                    }
                )
    return rows


def _parse_routing_config(
    device_configs: dict[str, str],
) -> tuple[dict[str, dict], dict[str, dict]]:
    """デバイスコンフィグから OSPF / BGP 設定を抽出する。

    Args:
        device_configs: {デバイス名: コンフィグテキスト} のマッピング。

    Returns:
        tuple[ospf_info, bgp_info]:
            ospf_info: {デバイス名: {"process_id", "router_id", "networks", "areas"}}
            bgp_info:  {デバイス名: {"local_as", "neighbors"}}
    """
    ospf_info: dict[str, dict] = {}
    bgp_info: dict[str, dict] = {}

    for device, config in device_configs.items():
        section: str | None = None
        cur: dict = {}
        for line in config.splitlines():
            m = re.match(r"^router ospf\s+(\d+)", line)
            if m:
                section = "ospf"
                cur = {
                    "process_id": m.group(1),
                    "router_id": None,
                    "networks": [],
                    "areas": [],
                }
                ospf_info[device] = cur
                continue
            m = re.match(r"^router bgp\s+(\d+)", line)
            if m:
                section = "bgp"
                cur = {"local_as": m.group(1), "neighbors": []}
                bgp_info[device] = cur
                continue
            # セクション終了（インデントなし行 または ! コメント）
            if line and not line[0].isspace() and section:
                section = None
                cur = {}
                continue

            if section == "ospf":
                m = re.match(r"\s+router-id\s+(\S+)", line)
                if m:
                    cur["router_id"] = m.group(1)
                m = re.match(r"\s+network\s+(\S+)\s+(\S+)\s+area\s+(\S+)", line)
                if m:
                    area = m.group(3)
                    cur["networks"].append(
                        {"network": m.group(1), "wildcard": m.group(2), "area": area}
                    )
                    if area not in cur["areas"]:
                        cur["areas"].append(area)

            elif section == "bgp":
                m = re.match(r"\s+neighbor\s+(\S+)\s+remote-as\s+(\d+)", line)
                if m:
                    cur["neighbors"].append(
                        {"peer": m.group(1), "remote_as": m.group(2)}
                    )

    return ospf_info, bgp_info


def _generate_design_docs(state: AgentState, out_dir: Path) -> str:
    """設計ドキュメントを生成してファイルに保存し、Markdown サマリーを返す。

    生成ドキュメント:
        topology.yaml      — CML トポロジー定義（topology_yaml が存在する場合）
        <device>.cfg       — 機器ごとのコンフィグ
        ip_ledger.md       — IP アドレス台帳（Markdown テーブル）
        ip_ledger.csv      — IP アドレス台帳（CSV）
        routing_design.md  — ルーティング設計書（OSPF / BGP サマリー）

    Args:
        state: 現在のエージェントステート。
        out_dir: 保存先ディレクトリ（configs/<prompt_set>/）。

    Returns:
        str: final_report に追記する Markdown テキスト。
    """
    device_configs = load_device_configs(state)
    topology_yaml: str = state.get("topology_yaml", "")
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []

    # ------------------------------------------------------------------
    # 1. topology.yaml + .cfg ファイル保存
    # ------------------------------------------------------------------
    if topology_yaml.strip():
        topo_path = out_dir / "topology.yaml"
        topo_path.write_text(topology_yaml, encoding="utf-8")
        saved.append(str(topo_path))

    for device, cfg in device_configs.items():
        cfg_path = out_dir / f"{device}.cfg"
        cfg_path.write_text(cfg, encoding="utf-8")
        saved.append(str(cfg_path))

    # ------------------------------------------------------------------
    # 2. IP アドレス台帳の生成・保存
    # ------------------------------------------------------------------
    ip_rows = _parse_ip_ledger(device_configs)

    if ip_rows:
        ip_md_rows = "\n".join(
            f"| {r['device']} | {r['interface']} | {r['cidr']} | {r['subnet']} |"
            for r in ip_rows
        )
        ip_ledger_md = (
            "# IP アドレス台帳\n\n"
            f"**生成日時**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            "| デバイス | インターフェース | アドレス（CIDR） | サブネット |\n"
            "|---|---|---|---|\n"
            f"{ip_md_rows}\n"
        )
    else:
        ip_ledger_md = "# IP アドレス台帳\n\n(IP アドレスが設定されたインターフェースがありません)\n"

    ip_ledger_path = out_dir / "ip_ledger.md"
    ip_ledger_path.write_text(ip_ledger_md, encoding="utf-8")
    saved.append(str(ip_ledger_path))

    csv_buf = io.StringIO()
    csv_writer = csv.DictWriter(
        csv_buf,
        fieldnames=["device", "interface", "ip_address", "prefix_length", "cidr", "subnet"],
    )
    csv_writer.writeheader()
    csv_writer.writerows(ip_rows)
    ip_csv_path = out_dir / "ip_ledger.csv"
    ip_csv_path.write_text(csv_buf.getvalue(), encoding="utf-8")
    saved.append(str(ip_csv_path))

    # ------------------------------------------------------------------
    # 3. ルーティング設計書の生成・保存
    # ------------------------------------------------------------------
    ospf_info, bgp_info = _parse_routing_config(device_configs)

    routing_parts: list[str] = [
        "# ルーティング設計書\n",
        f"**生成日時**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
    ]

    if ospf_info:
        routing_parts.append("\n## OSPF 設定\n")
        for device, info in ospf_info.items():
            areas = ", ".join(f"エリア {a}" for a in info["areas"]) or "(なし)"
            nets = (
                "\n".join(
                    f"  - `network {n['network']} {n['wildcard']} area {n['area']}`"
                    for n in info["networks"]
                )
                or "  (なし)"
            )
            routing_parts.append(
                f"### {device}\n"
                f"- プロセス ID: `ospf {info['process_id']}`\n"
                f"- Router-ID: `{info['router_id'] or '(未設定)'}`\n"
                f"- エリア: {areas}\n"
                f"- network 文:\n{nets}\n"
            )
    else:
        routing_parts.append("\n## OSPF 設定\n\n(OSPF 設定なし)\n")

    if bgp_info:
        routing_parts.append("\n## BGP 設定\n")
        for device, info in bgp_info.items():
            neighbors = (
                "\n".join(
                    f"  - `{n['peer']}` (remote-as {n['remote_as']})"
                    for n in info["neighbors"]
                )
                or "  (なし)"
            )
            routing_parts.append(
                f"### {device}\n"
                f"- ローカル AS: `{info['local_as']}`\n"
                f"- ネイバー:\n{neighbors}\n"
            )
    else:
        routing_parts.append("\n## BGP 設定\n\n(BGP 設定なし)\n")

    routing_design_md = "\n".join(routing_parts)
    routing_path = out_dir / "routing_design.md"
    routing_path.write_text(routing_design_md, encoding="utf-8")
    saved.append(str(routing_path))

    # ------------------------------------------------------------------
    # 4. final_report に追記する Markdown サマリー
    # ------------------------------------------------------------------
    files_list = "\n".join(f"- `{f}`" for f in saved)

    # IP台帳インライン表示（20 行まで）
    if ip_rows:
        display_rows = ip_rows[:20]
        ip_inline_rows = "\n".join(
            f"| {r['device']} | {r['interface']} | {r['cidr']} |"
            for r in display_rows
        )
        if len(ip_rows) > 20:
            ip_inline_rows += f"\n| ... | ... | ({len(ip_rows) - 20} 件のみ省略) |"
        ip_inline = (
            "| デバイス | インターフェース | アドレス（CIDR） |\n"
            "|---|---|---|\n"
            f"{ip_inline_rows}"
        )
    else:
        ip_inline = "(IP アドレス設定なし)"

    # ルーティングサマリーインライン表示
    routing_summary_parts: list[str] = []
    if ospf_info:
        all_areas: list[str] = []
        for info in ospf_info.values():
            for a in info["areas"]:
                if a not in all_areas:
                    all_areas.append(a)
        ospf_devices = ", ".join(
            f"`{d}` (プロセス {info['process_id']})"
            for d, info in ospf_info.items()
        )
        routing_summary_parts.append(
            f"**OSPF**: {ospf_devices}  \n"
            f"エリア: {', '.join(f'エリア {a}' for a in all_areas)}"
        )
    if bgp_info:
        bgp_devices = ", ".join(
            f"`{d}` (AS {info['local_as']}, {len(info['neighbors'])} ネイバー)"
            for d, info in bgp_info.items()
        )
        routing_summary_parts.append(f"**BGP**: {bgp_devices}")
    if not routing_summary_parts:
        routing_summary_parts.append("(ルーティングプロトコル設定なし)")

    routing_inline = "\n\n".join(routing_summary_parts)

    summary_md = (
        f"\n\n---\n\n"
        f"## 設計ドキュメント（Phase D）\n\n"
        f"### IP アドレス台帳\n\n{ip_inline}\n\n"
        f"### ルーティング設計サマリー\n\n{routing_inline}\n\n"
        f"### 保存先ファイル\n\n{files_list}"
    )

    print(
        f"  [Phase D] 設計ドキュメント生成完了: {out_dir} ({len(saved)} ファイル)",
        flush=True,
    )
    return summary_md


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
    device_configs_esc = load_device_configs(state)
    config_section = "\n\n".join(
        f"### {dev}\n```\n{cfg.strip()}\n```"
        for dev, cfg in device_configs_esc.items()
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
    """Phase C/D: ドライランモードの出力ノード。設計結果を表示しファイルに保存する。"""
    topology_yaml: str = state.get("topology_yaml", "")
    device_configs_dry = load_device_configs(state)
    prompt_set: str = state.get("prompt_set", "demo")

    out_dir = Path("configs") / prompt_set

    # コンフィグ保存 + IP台帳 + ルーティング設計書を生成・保存する
    docs_section = _generate_design_docs(state, out_dir)

    device_section = "\n\n".join(
        f"### {dev}\n```\n{cfg.strip()}\n```"
        for dev, cfg in device_configs_dry.items()
    ) or "(コンフィグなし)"

    report = (
        f"# 設計レポート（ドライラン）\n\n"
        f"**生成日時**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"## 要件\n{state.get('requirement', '')}\n\n"
        f"## トポロジー定義（CML YAML）\n\n"
        f"```yaml\n{topology_yaml.strip()}\n```\n\n"
        f"## 機器コンフィグ\n\n"
        f"{device_section}"
        f"{docs_section}"
    )
    return {"final_report": report}


# ---------------------------------------------------------------------------
# Phase H: トラブルシューティングノード
# ---------------------------------------------------------------------------


def troubleshoot_collect_node(state: AgentState) -> dict:
    """Phase H: 既存ラボの全機器から running-config と show コマンドを収集する。"""
    print(f"\n{'='*60}", flush=True)
    print("[トラブルシューティング] 機器状態を収集中...", flush=True)
    print(f"{'='*60}", flush=True)
    return troubleshooter.run_collect(state)


def troubleshoot_diagnose_node(state: AgentState) -> dict:
    """Phase H: 収集した状態と失敗テストを LLM で診断する。"""
    retry = state.get("troubleshoot_retry_count", 0) + 1
    print(
        f"\n[トラブルシューティング 診断 {retry}/{TROUBLESHOOT_MAX_RETRIES}]"
        " 根本原因を分析中...",
        flush=True,
    )
    return troubleshooter.run_diagnose(state)


def troubleshoot_fix_node(state: AgentState) -> dict:
    """Phase H: 診断結果に基づき差分修正コマンドを生成・投入する。"""
    print("\n[トラブルシューティング] 修正コマンドを生成・投入中...", flush=True)
    return troubleshooter.run_fix(state)


def troubleshoot_verify_node(state: AgentState) -> dict:
    """Phase H: 修正後にテストを実行して検証する（デプロイなし）。"""
    print("\n[トラブルシューティング] 検証テストを実行中...", flush=True)
    from agentic_ni.agents.validator import TestPlan, _build_test_plan_messages, _execute_test
    from agentic_ni.llm import get_llm
    from agentic_ni.tools import pyats_tools

    llm = get_llm()
    structured_llm = llm.with_structured_output(TestPlan, method="function_calling")
    plan: TestPlan = structured_llm.invoke(_build_test_plan_messages(state))
    print(f"  テスト計画: {len(plan.tests)} 件", flush=True)

    testbed_yaml = pyats_tools.build_testbed(
        state.get("lab_id", ""),
        load_device_configs(state),
    )
    test_results = []
    for i, item in enumerate(plan.tests, 1):
        print(f"  ({i}/{len(plan.tests)}) {item.description}", flush=True)
        result = _execute_test(item, testbed_yaml)
        mark = "✅ PASS" if result["result"] == "PASS" else "❌ FAIL"
        print(f"       → {mark}  {result['detail']}", flush=True)
        test_results.append(result)

    return {
        "test_results": test_results,
        "test_plan_items": [item.model_dump() for item in plan.tests],
    }


def troubleshoot_report_node(state: AgentState) -> dict:
    """Phase H: トラブルシューティング完了レポートを生成する。"""
    print("\n  >>> トラブルシューティング完了レポートを生成しています...", flush=True)
    fix_records = state.get("fix_records", [])
    test_results = state.get("test_results", [])
    passed = [r for r in test_results if r["result"] == "PASS"]
    failed = [r for r in test_results if r["result"] == "FAIL"]

    fix_section = "\n\n".join(
        f"### 修正 {i}: [{r['device']}] {r.get('description', '')}\n"
        f"- 結果: {'✅ 成功' if r['success'] else '❌ 失敗'}\n"
        f"```\n{r['commands']}\n```"
        + (f"\n- エラー: {r['error']}" if r.get("error") else "")
        for i, r in enumerate(fix_records, 1)
    ) or "(修正なし)"

    result_rows = "\n".join(
        f"| {r['test']} | {'✅ PASS' if r['result'] == 'PASS' else '❌ FAIL'} | {r['detail']} |"
        for r in test_results
    ) or "| (テスト未実施) | - | - |"

    verdict = (
        "✅ すべてのテストが PASS しました。問題が解決されました。"
        if not failed
        else f"⚠️ {len(failed)} 件のテストが FAIL のままです。手動確認が必要な可能性があります。"
    )

    report = (
        f"# トラブルシューティング完了レポート\n\n"
        f"**生成日時**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"## 対象ラボ\n`{state.get('troubleshoot_lab_id', state.get('lab_id', '(不明)'))}`\n\n"
        f"## 問題の説明\n{state.get('troubleshoot_issue') or '(説明なし)'}\n\n"
        f"## 診断結果\n{state.get('diagnosis', '(診断なし)')}\n\n"
        f"## 適用した修正（{len(fix_records)} 件）\n\n{fix_section}\n\n"
        f"## 最終テスト結果\n\n"
        f"| テスト名 | 結果 | 詳細 |\n"
        f"|---|---|---|\n"
        f"{result_rows}\n\n"
        f"## 判定\n{verdict}"
    )
    return {"final_report": report, "troubleshoot_report": report}


def should_continue_troubleshoot(
    state: AgentState,
) -> Literal["complete", "retry", "escalate"]:
    """トラブルシューティングの検証後ルーティングを決定する。"""
    test_results = state.get("test_results", [])
    retry_count = state.get("troubleshoot_retry_count", 0)

    if test_results and all(r["result"] == "PASS" for r in test_results):
        return "complete"
    if retry_count >= TROUBLESHOOT_MAX_RETRIES:
        return "escalate"
    return "retry"


def compile_graph_troubleshoot() -> Any:
    """トラブルシューティングモード用コンパイル済みグラフを返す。

    フロー: collect → diagnose → fix → verify → (retry: collect に戻る | complete: report)
    """
    graph = StateGraph(AgentState)

    graph.add_node("collect", troubleshoot_collect_node)
    graph.add_node("diagnose", troubleshoot_diagnose_node)
    graph.add_node("fix", troubleshoot_fix_node)
    graph.add_node("verify", troubleshoot_verify_node)
    graph.add_node("report", troubleshoot_report_node)
    graph.add_node("escalate", escalate_node)

    graph.set_entry_point("collect")
    graph.add_edge("collect", "diagnose")
    graph.add_edge("diagnose", "fix")
    graph.add_edge("fix", "verify")

    graph.add_conditional_edges(
        "verify",
        should_continue_troubleshoot,
        {
            "complete": "report",
            "retry": "collect",   # 修正適用後に状態を再収集して再診断
            "escalate": "escalate",
        },
    )
    graph.add_edge("report", END)
    graph.add_edge("escalate", END)
    return graph.compile()


def initial_state_troubleshoot(
    lab_id: str,
    issue: str = "",
    prompt_set: str = "demo",
) -> AgentState:
    """トラブルシューティングモードの初期ステートを生成するファクトリー関数。

    Args:
        lab_id: 問題が発生している既存 CML ラボの ID。
        issue: 問題の説明（自然言語）。省略可。
        prompt_set: 使用するプロンプトセット名（要件コンテキストと system prompt に使用）。
    """
    # requirement.md が存在すれば本来あるべき要件として使う
    try:
        requirement = load_requirement(prompt_set)
    except FileNotFoundError:
        requirement = issue or f"ラボ {lab_id} のトラブルシューティング"

    return AgentState(
        requirement=requirement,
        prompt_set=prompt_set,
        fault_simulation_enabled=False,
        skip_deploy=False,
        error_history=[],
        topology_yaml="",
        device_configs={},
        device_config_paths={},
        lab_id=lab_id,
        test_results=[],
        test_plan_items=[],
        error_log="",
        retry_count=0,
        failed_devices=[],
        fault_scenario_results=[],
        fault_report="",
        # トラブルシューティング固有
        troubleshoot_lab_id=lab_id,
        troubleshoot_issue=issue,
        collected_state={},
        diagnosis="",
        fix_records=[],
        troubleshoot_retry_count=0,
        troubleshoot_report="",
        analyze_request="",
        analysis_result="",
        # Phase I
        live_inventory_path="",
        live_apply_records=[],
        live_verify_enabled=False,
        live_human_decision="",
        live_test_results=[],
        live_report="",
        final_report="",
    )


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


def _load_topology_from_file(prompt_set: str) -> str:
    """configs/<prompt_set>/topology.yaml からトポロジーYAMLを読み込んで返す。

    ファイルが存在しない場合は FileNotFoundError を送出する。
    """
    topology_path = Path("configs") / prompt_set / "topology.yaml"
    if not topology_path.exists():
        raise FileNotFoundError(
            f"トポロジーファイルが見つかりません: {topology_path}\n"
            f"use_provided_topology=True の場合は configs/{prompt_set}/topology.yaml が必要です。"
        )
    return topology_path.read_text(encoding="utf-8")


def initial_state(
    requirement: str,
    prompt_set: str = "demo",
    fault_simulation_enabled: bool = False,
    skip_deploy: bool = False,
    lab_id: str = "",
    use_provided_topology: bool = False,
) -> AgentState:
    """初期ステートを生成するファクトリー関数。

    Args:
        requirement: ネットワーク要件の自然言語テキスト。
        prompt_set: 使用するプロンプトセット名。
        fault_simulation_enabled: True の場合、Phase A 成功後に障害シミュレーションを実行する。
        skip_deploy: True の場合、検証エージェントのデプロイをスキップし既存ラボを再利用する。
        lab_id: skip_deploy=True 時に指定する既存ラボID。
        use_provided_topology: True の場合、configs/<prompt_set>/topology.yaml をトポロジーとして
            使用し、設計エージェントはコンフィグ生成のみ行う。
    """
    # use_provided_topology=True の場合、ファイルからトポロジーを事前ロード
    topology_yaml = ""
    if use_provided_topology:
        topology_yaml = _load_topology_from_file(prompt_set)
        print(f"  [トポロジー] configs/{prompt_set}/topology.yaml を読み込みました（コンフィグのみ生成モード）", flush=True)

    return AgentState(
        requirement=requirement,
        prompt_set=prompt_set,
        use_provided_topology=use_provided_topology,
        fault_simulation_enabled=fault_simulation_enabled,
        skip_deploy=skip_deploy,
        error_history=[],
        topology_yaml=topology_yaml,
        device_configs={},
        device_config_paths={},
        lab_id=lab_id,
        test_results=[],
        test_plan_items=[],
        error_log="",
        retry_count=0,
        failed_devices=[],
        fault_scenario_results=[],
        fault_report="",
        troubleshoot_lab_id="",
        troubleshoot_issue="",
        collected_state={},
        diagnosis="",
        fix_records=[],
        troubleshoot_retry_count=0,
        troubleshoot_report="",
        analyze_request="",
        analysis_result="",
        # Phase I
        live_inventory_path="",
        live_apply_records=[],
        live_verify_enabled=False,
        live_human_decision="",
        live_test_results=[],
        live_report="",
        final_report="",
    )

def initial_state_analyze(
    lab_id: str,
    prompt_set: str = "demo",
) -> AgentState:
    """分析モードの初期ステートを生成するファクトリー関数。

    Args:
        lab_id: 分析対象の既存 CML ラボの ID。
        prompt_set: 要件コンテキストに使用するプロンプトセット名。
    """
    try:
        requirement = load_requirement(prompt_set)
    except FileNotFoundError:
        requirement = f"ラボ {lab_id} の設計分析"
    return AgentState(
        requirement=requirement,
        prompt_set=prompt_set,
        fault_simulation_enabled=False,
        skip_deploy=False,
        error_history=[],
        topology_yaml="",
        device_configs={},
        device_config_paths={},
        lab_id=lab_id,
        test_results=[],
        test_plan_items=[],
        error_log="",
        retry_count=0,
        failed_devices=[],
        fault_scenario_results=[],
        fault_report="",
        troubleshoot_lab_id=lab_id,
        troubleshoot_issue="",
        collected_state={},
        diagnosis="",
        fix_records=[],
        troubleshoot_retry_count=0,
        troubleshoot_report="",
        analyze_request="",
        analysis_result="",
        # Phase I
        live_inventory_path="",
        live_apply_records=[],
        live_verify_enabled=False,
        live_human_decision="",
        live_test_results=[],
        live_report="",
        final_report="",
    )


def initial_state_improve(
    lab_id: str,
    analyze_request: str = "",
    prompt_set: str = "demo",
) -> AgentState:
    """改善モードの初期ステートを生成するファクトリー関数。

    Args:
        lab_id: 改善対象の既存 CML ラボの ID。
        analyze_request: 改善要求の自然言語テキスト。
        prompt_set: 要件コンテキストに使用するプロンプトセット名。
    """
    try:
        requirement = load_requirement(prompt_set)
    except FileNotFoundError:
        requirement = f"ラボ {lab_id} の設計改善"
    return AgentState(
        requirement=requirement,
        prompt_set=prompt_set,
        fault_simulation_enabled=False,
        skip_deploy=False,
        error_history=[],
        topology_yaml="",
        device_configs={},
        device_config_paths={},
        lab_id=lab_id,
        test_results=[],
        test_plan_items=[],
        error_log="",
        retry_count=0,
        failed_devices=[],
        fault_scenario_results=[],
        fault_report="",
        troubleshoot_lab_id=lab_id,
        troubleshoot_issue="",
        collected_state={},
        diagnosis="",
        fix_records=[],
        troubleshoot_retry_count=0,
        troubleshoot_report="",
        analyze_request=analyze_request,
        analysis_result="",
        # Phase I
        live_inventory_path="",
        live_apply_records=[],
        live_verify_enabled=False,
        live_human_decision="",
        live_test_results=[],
        live_report="",
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


# ---------------------------------------------------------------------------
# Phase I: 実機適用ノード
# ---------------------------------------------------------------------------


def _get_live_tools(state: AgentState = None):
    """Live ツールモジュールを返す（pyATS/Unicon バックエンド固定）。"""
    from agentic_ni.tools import pyats_tools
    return pyats_tools



def _resolve_inventory_path(state: AgentState) -> str:
    """インベントリパスを解決する。

    ``live_inventory_path`` が設定されている場合はそれを使用し、
    省略時は ``inventory/<prompt_set>.yaml`` を自動使用する。

    Raises:
        FileNotFoundError: インベントリファイルが存在しない場合。
    """
    explicit = state.get("live_inventory_path", "")
    if explicit:
        return explicit
    prompt_set = state.get("prompt_set", "demo")
    default_path = Path("inventory") / f"{prompt_set}.yaml"
    if not default_path.exists():
        raise FileNotFoundError(
            f"インベントリファイルが見つかりません: {default_path}\n"
            f"inventory/{prompt_set}.yaml を作成するか --inventory で明示指定してください。"
        )
    return str(default_path)


def live_precheck_node(state: AgentState) -> dict:
    """Phase I Step 2: インベントリ読込・SSH 疎通確認・running-config バックアップを実行する。

    多段安全機構:
        Level 1 — インベントリファイルの存在チェック
        Level 2 — SSH 疎通確認（繋がらないデバイスがあれば中断）
        Level 3 — バックアップ取得の成否確認（失敗すれば中断）

    state 更新キー:
        live_apply_records  — デバイスごとの precheck 結果リスト
        error_log           — 中断理由（成功時は空文字）
        final_report        — 中断時のエラーレポート

    Raises:
        この関数は例外を送出しない。失敗時は error_log と final_report を更新して
        グラフの後続ノードへ制御を渡す（後続で error_log を見て分岐する）。
    """
    from agentic_ni.tools import pyats_tools

    print(f"\n{'='*60}", flush=True)
    print("[Phase I] 実機適用プレチェック 開始", flush=True)
    print(f"{'='*60}", flush=True)

    tools = _get_live_tools(state)
    # ------------------------------------------------------------------
    # Level 1: インベントリファイルの存在チェック
    # ------------------------------------------------------------------
    try:
        inventory_path = _resolve_inventory_path(state)
        devices = pyats_tools.load_inventory(inventory_path)
        print(f"  [✅ Level 1] インベントリ読み込み完了: {inventory_path} ({len(devices)} デバイス)", flush=True)
    except (FileNotFoundError, ValueError) as exc:
        msg = f"インベントリ読み込みエラー: {exc}"
        print(f"  [❌ Level 1] {msg}", flush=True)
        return {
            "error_log": msg,
            "live_apply_records": [],
            "final_report": f"# Phase I プレチェック失敗\n\n{msg}",
        }

    # ------------------------------------------------------------------
    # Level 2: SSH 疎通確認
    # ------------------------------------------------------------------
    print(f"  [Level 2] SSH 疎通確認中...", flush=True)
    connectivity = tools.check_connectivity(devices)
    failed_conn = [name for name, ok in connectivity.items() if not ok]

    for name, ok in connectivity.items():
        host = devices[name].get("host", "?")
        mark = "✅" if ok else "❌"
        print(f"    {mark} {name} ({host})", flush=True)

    if failed_conn:
        msg = f"SSH 疎通確認に失敗したデバイス: {', '.join(failed_conn)}"
        print(f"  [❌ Level 2] {msg}", flush=True)
        # 失敗デバイスだけ記録して中断
        records: list[LiveApplyRecord] = [
            LiveApplyRecord(
                device=name,
                host=devices[name].get("host", ""),
                apply_mode=devices[name].get("apply_mode", "config_merge"),
                connectivity_ok=connectivity.get(name, False),
                backup_config="",
                backup_lines=0,
                applied_config="",
                apply_success=False,
                apply_output="",
                apply_error="",
                rollback_done=False,
                rollback_error="",
            )
            for name in devices
        ]
        return {
            "error_log": msg,
            "live_apply_records": records,
            "final_report": f"# Phase I プレチェック失敗\n\n{msg}",
        }
    print(f"  [✅ Level 2] 全 {len(devices)} デバイスの SSH 疎通確認 OK", flush=True)

    # ------------------------------------------------------------------
    # Level 3: running-config バックアップ取得
    # ------------------------------------------------------------------
    print(f"  [Level 3] running-config バックアップ取得中...", flush=True)
    try:
        backups = tools.backup_running_config(devices)
        print(f"  [✅ Level 3] バックアップ取得完了", flush=True)
        for name, cfg in backups.items():
            lines = len([l for l in cfg.splitlines() if l.strip()])
            print(f"    ✅ {name} — {lines} 行", flush=True)
    except RuntimeError as exc:
        msg = str(exc)
        print(f"  [❌ Level 3] {msg}", flush=True)
        records = [
            LiveApplyRecord(
                device=name,
                host=devices[name].get("host", ""),
                apply_mode=devices[name].get("apply_mode", "config_merge"),
                connectivity_ok=True,
                backup_config="",
                backup_lines=0,
                applied_config="",
                apply_success=False,
                apply_output="",
                apply_error="",
                rollback_done=False,
                rollback_error="",
            )
            for name in devices
        ]
        return {
            "error_log": msg,
            "live_apply_records": records,
            "final_report": f"# Phase I プレチェック失敗\n\n{msg}",
        }

    # ------------------------------------------------------------------
    # 成功: live_apply_records を組み立てて返す
    # ------------------------------------------------------------------
    records = [
        LiveApplyRecord(
            device=name,
            host=devices[name].get("host", ""),
            apply_mode=devices[name].get("apply_mode", "config_merge"),
            connectivity_ok=True,
            backup_config=backups.get(name, ""),
            backup_lines=len([
                l for l in backups.get(name, "").splitlines() if l.strip()
            ]),
            applied_config="",
            apply_success=False,
            apply_output="",
            apply_error="",
            rollback_done=False,
            rollback_error="",
        )
        for name in devices
    ]

    print(f"\n  [Phase I] プレチェック完了 — {len(records)} デバイス準備 OK", flush=True)
    return {
        "live_apply_records": records,
        "error_log": "",
    }


def _should_continue_after_precheck(
    state: AgentState,
) -> Literal["confirm", "abort"]:
    """プレチェック後の分岐: エラーがあれば abort、なければ confirm へ。"""
    if state.get("error_log"):
        return "abort"
    return "confirm"


def _build_confirmation_message(state: AgentState) -> str:
    """Human 確認画面のメッセージ文字列を組み立てる。"""
    records: list[LiveApplyRecord] = state.get("live_apply_records", [])
    test_results: list = state.get("test_results", [])

    lines = [
        "⚠️  実機へのコンフィグ適用を開始しようとしています",
        "",
        "【適用対象】",
    ]
    for rec in records:
        mark = "✅" if rec.get("connectivity_ok") else "❌"
        backup_lines = rec.get("backup_lines", 0)
        lines.append(
            f"  {mark} {rec['device']} ({rec['host']})"
            f" — {rec['apply_mode']}"
            f" — バックアップ取得済み ({backup_lines} 行)"
        )

    if test_results:
        lines.append("")
        lines.append("【CML テスト結果（検証済み）】")
        parts = []
        for r in test_results:
            mark = "✅" if r["result"] == "PASS" else "❌"
            parts.append(f"{mark} {r['test']}: {r['result']}")
        lines.append("  " + "  ".join(parts))

    lines.append("")
    lines.append("続行しますか？ (yes / no / rollback-only)")
    return "\n".join(lines)


def human_confirm_live_node(state: AgentState) -> dict:
    """Phase I Step 3: Human による最終承認ノード（スキップ不可）。

    LangGraph の :func:`interrupt` を使用してグラフを一時停止し、Human の
    入力を待つ。承認なしには先へ進まない。

    **interrupt に渡すペイロード**::

        {
            "type": "live_confirm",
            "message": <確認画面テキスト>,
            "live_apply_records": [...],
            "test_results": [...],
        }

    **再開時に受け取るペイロード**::

        {
            "decision": "yes" | "no" | "rollback-only",
            "reason": "<任意のコメント>",
        }

    **state 更新キー**:

    * ``live_human_decision`` — "yes" / "no" / "rollback-only"
    * ``final_report`` — "no" / "rollback-only" 時は対応メッセージを追記
    """
    confirmation_msg = _build_confirmation_message(state)

    print(f"\n{'='*60}", flush=True)
    print("[Phase I] Human 承認待ち...", flush=True)
    print(f"{'='*60}", flush=True)
    print(confirmation_msg, flush=True)

    decision_payload: dict = interrupt(
        {
            "type": "live_confirm",
            "message": confirmation_msg,
            "live_apply_records": state.get("live_apply_records", []),
            "test_results": state.get("test_results", []),
        }
    )

    raw_decision: str = str(decision_payload.get("decision", "no")).strip().lower()
    reason: str = str(decision_payload.get("reason", ""))

    # 正規化: "yes" / "rollback-only" 以外はすべて "no" として扱う
    if raw_decision == "yes":
        decision = "yes"
    elif raw_decision in ("rollback-only", "rollback_only", "rollback"):
        decision = "rollback-only"
    else:
        decision = "no"

    print(f"\n  Human の決定: {decision}", flush=True)

    updates: dict = {"live_human_decision": decision}

    if decision == "no":
        cancel_section = (
            "\n\n---\n\n"
            "## ⚠️ 実機適用 — Human による取り消し\n\n"
            f"取り消し理由: {reason or '(理由なし)'}\n\n"
            "実機への設定投入は行われませんでした。  \n"
            "取得済みバックアップは `live_apply_records` に保持されています。"
        )
        updates["final_report"] = state.get("final_report", "") + cancel_section

    elif decision == "rollback-only":
        rollback_section = (
            "\n\n---\n\n"
            "## ⏪ 実機適用 — rollback-only モード\n\n"
            f"理由: {reason or '(理由なし)'}\n\n"
            "新規コンフィグの投入はスキップします。  \n"
            "取得済みバックアップを使ってロールバックを実施します。"
        )
        updates["final_report"] = state.get("final_report", "") + rollback_section

    return updates


def _should_continue_after_confirm(
    state: AgentState,
) -> Literal["apply", "cancelled", "rollback"]:
    """human_confirm_live_node 後のルーティングを決定する。

    * ``yes``           → ``apply``     （live_apply_node、Step 4 以降）
    * ``rollback-only`` → ``rollback``  （live_rollback_node、Step 4 以降）
    * ``no`` / その他   → ``cancelled`` （処理終了）
    """
    decision = state.get("live_human_decision", "no")
    if decision == "yes":
        return "apply"
    elif decision == "rollback-only":
        return "rollback"
    return "cancelled"


def compile_graph_live_precheck_confirm():
    """Phase I Step 3: precheck → human_confirm までを実行するグラフ（テスト用）。

    フロー::

        live_precheck
            ↓ confirm (precheck 成功)
        human_confirm
            ↓ yes        → END  (Step 4 で live_apply_node に差し替え)
            ↓ cancelled  → END  (final_report に取り消しメッセージ追記済み)
            ↓ rollback   → END  (Step 4+ で rollback_node に差し替え)
            ↓
        abort (precheck 失敗)
            → END

    ``interrupt()`` を使用するため :class:`MemorySaver` チェックポインターが必須。
    """
    from langgraph.checkpoint.memory import MemorySaver

    graph = StateGraph(AgentState)
    graph.add_node("live_precheck", live_precheck_node)
    graph.add_node("human_confirm", human_confirm_live_node)

    graph.set_entry_point("live_precheck")

    graph.add_conditional_edges(
        "live_precheck",
        _should_continue_after_precheck,
        {
            "confirm": "human_confirm",
            "abort": END,
        },
    )

    # Step 4 実装前の暫定ルーティング: すべて END へ
    graph.add_conditional_edges(
        "human_confirm",
        _should_continue_after_confirm,
        {
            "apply": END,
            "cancelled": END,
            "rollback": END,
        },
    )

    return graph.compile(checkpointer=MemorySaver())


# ---------------------------------------------------------------------------
# Phase I Step 4: コンフィグ投入ノード・ロールバックノード
# ---------------------------------------------------------------------------


def live_apply_node(state: AgentState) -> dict:
    """Phase I Step 4: Netmiko で各デバイスにコンフィグを投入する。

    CML で検証済みの ``device_configs`` を実機に投入する。
    投入に失敗したデバイスは ``backup_config`` を使って自動ロールバックする
    （多段安全機構 Level 6）。

    **処理フロー**:

    1. インベントリを再読み込みして接続パラメータを取得
    2. 各デバイスに対して :func:`~agentic_ni.tools.pyats_tools.apply_config` を実行
    3. 失敗したデバイスに対して :func:`~agentic_ni.tools.pyats_tools.rollback_config` を実行
    4. ``live_apply_records`` を更新して返す

    **state 更新キー**:

    * ``live_apply_records`` — apply / rollback 結果を各レコードに追記
    * ``error_log``          — 1 台以上失敗した場合にエラーメッセージを設定
    """
    from agentic_ni.tools import pyats_tools

    records: list[LiveApplyRecord] = list(state.get("live_apply_records", []))
    device_configs = load_device_configs(state)

    print(f"\n{'='*60}", flush=True)
    print("[Phase I] 実機コンフィグ投入 開始", flush=True)
    print(f"{'='*60}", flush=True)

    tools = _get_live_tools(state)
    # インベントリを再読み込みして接続パラメータを取得
    try:
        inventory_path = _resolve_inventory_path(state)
        devices = pyats_tools.load_inventory(inventory_path)
    except (FileNotFoundError, ValueError) as exc:
        msg = f"インベントリ読み込みエラー（apply フェーズ）: {exc}"
        print(f"  [❌] {msg}", flush=True)
        return {"error_log": msg}

    updated_records: list[LiveApplyRecord] = []
    failed_devices: list[str] = []

    for rec in records:
        name = rec["device"]
        cfg = devices.get(name)
        if cfg is None:
            # インベントリにないデバイスはスキップ（エラーにしない）
            print(f"  ⚠️  {name}: インベントリに見つからないためスキップ", flush=True)
            updated_records.append(rec)
            continue

        config_text = device_configs.get(name, "")
        if not config_text.strip():
            print(f"  ⚠️  {name}: コンフィグが空のためスキップ", flush=True)
            updated_records.append(rec)
            continue

        print(f"  [{name}] コンフィグ投入中 ({cfg['host']}, {cfg.get('apply_mode', 'config_merge')})...", flush=True)
        apply_result = tools.apply_config(name, cfg, config_text)

        updated_rec: LiveApplyRecord = {
            **rec,
            "applied_config": config_text,
            "apply_success": apply_result["success"],
            "apply_output": apply_result["output"],
            "apply_error": apply_result["error"],
            "rollback_done": False,
            "rollback_error": "",
        }

        if apply_result["success"]:
            print(f"    ✅ {name}: 投入成功", flush=True)
        else:
            print(f"    ❌ {name}: 投入失敗 — {apply_result['error']}", flush=True)
            failed_devices.append(name)

            # Level 6: 自動ロールバック
            backup = rec.get("backup_config", "")
            if backup.strip():
                print(f"    ⏪ {name}: バックアップを使って自動ロールバック中...", flush=True)
                rb_result = tools.rollback_config(name, cfg, backup)
                updated_rec["rollback_done"] = rb_result["success"]
                updated_rec["rollback_error"] = rb_result["error"]
                mark = "✅" if rb_result["success"] else "❌"
                print(f"    {mark} {name}: ロールバック {'成功' if rb_result['success'] else '失敗'}", flush=True)
            else:
                print(f"    ⚠️  {name}: バックアップなし — ロールバックをスキップ", flush=True)

        updated_records.append(updated_rec)

    error_log = ""
    if failed_devices:
        error_log = (
            f"実機適用に失敗したデバイス: {', '.join(failed_devices)}\n"
            "自動ロールバックを実施しました。"
        )

    total = len([r for r in updated_records if r.get("applied_config")])
    succeeded = len([r for r in updated_records if r.get("apply_success")])
    print(f"\n  [Phase I] 投入完了 — {succeeded}/{total} デバイス成功", flush=True)

    return {
        "live_apply_records": updated_records,
        "error_log": error_log,
    }


def live_rollback_node(state: AgentState) -> dict:
    """Phase I Step 4: rollback-only モード — バックアップコンフィグを実機に復元する。

    ``human_confirm_live_node`` で ``"rollback-only"`` が選択された場合に実行される。
    新規コンフィグの投入は行わず、取得済みバックアップを使って各デバイスを元の状態に戻す。

    **state 更新キー**:

    * ``live_apply_records`` — rollback 結果を各レコードに追記
    * ``error_log``          — 1 台以上失敗した場合にエラーメッセージを設定
    """
    from agentic_ni.tools import pyats_tools

    records: list[LiveApplyRecord] = list(state.get("live_apply_records", []))

    print(f"\n{'='*60}", flush=True)
    print("[Phase I] rollback-only モード — バックアップを復元中", flush=True)
    print(f"{'='*60}", flush=True)

    tools = _get_live_tools(state)
    try:
        inventory_path = _resolve_inventory_path(state)
        devices = pyats_tools.load_inventory(inventory_path)
    except (FileNotFoundError, ValueError) as exc:
        msg = f"インベントリ読み込みエラー（rollback フェーズ）: {exc}"
        print(f"  [❌] {msg}", flush=True)
        return {"error_log": msg}

    updated_records: list[LiveApplyRecord] = []
    failed_rollbacks: list[str] = []

    for rec in records:
        name = rec["device"]
        cfg = devices.get(name)
        if cfg is None:
            print(f"  ⚠️  {name}: インベントリに見つからないためスキップ", flush=True)
            updated_records.append(rec)
            continue

        backup = rec.get("backup_config", "")
        if not backup.strip():
            print(f"  ⚠️  {name}: バックアップなし — スキップ", flush=True)
            updated_records.append(rec)
            continue

        print(f"  [{name}] バックアップを復元中 ({cfg['host']})...", flush=True)
        rb_result = tools.rollback_config(name, cfg, backup)

        updated_rec: LiveApplyRecord = {
            **rec,
            "rollback_done": rb_result["success"],
            "rollback_error": rb_result["error"],
        }
        updated_records.append(updated_rec)

        mark = "✅" if rb_result["success"] else "❌"
        print(f"    {mark} {name}: ロールバック {'成功' if rb_result['success'] else '失敗 — ' + rb_result['error']}", flush=True)
        if not rb_result["success"]:
            failed_rollbacks.append(name)

    error_log = ""
    if failed_rollbacks:
        error_log = f"ロールバックに失敗したデバイス: {', '.join(failed_rollbacks)}"

    done = len([r for r in updated_records if r.get("rollback_done")])
    print(f"\n  [Phase I] ロールバック完了 — {done}/{len(records)} デバイス成功", flush=True)

    return {
        "live_apply_records": updated_records,
        "error_log": error_log,
    }


# ---------------------------------------------------------------------------
# Phase I Step 6: 実機 pyATS 検証ノード（任意）
# ---------------------------------------------------------------------------


def _should_verify_after_apply(
    state: AgentState,
) -> Literal["verify", "report"]:
    """``live_apply_node`` 後のルーティングを決定する。

    ``live_verify_enabled=True`` かつ ``live_human_decision="yes"`` の場合のみ
    ``live_verify_node`` へ進む。それ以外は直接 ``live_report_node`` へ。
    """
    if (
        state.get("live_verify_enabled")
        and state.get("live_human_decision") == "yes"
    ):
        return "verify"
    return "report"


def live_verify_node(state: AgentState) -> dict:
    """Phase I Step 6: 実機に対して pyATS で同一テスト計画を実行する（任意）。

    CML 検証で使用した ``test_plan_items`` を実機に対して再実行する。
    ``--live-verify`` フラグが指定された場合のみ実行される。

    実機向け testbed YAML はインベントリから生成する
    (:func:`~agentic_ni.tools.pyats_tools.build_testbed_from_inventory`)。
    pyATS/Genie が未インストールの場合はエラーを記録してスキップする。

    **state 更新キー**:

    * ``live_test_results`` — 実機テスト結果リスト（:class:`~agentic_ni.state.TestResult`）
    """
    from agentic_ni.tools import pyats_tools
    from agentic_ni.agents.validator import TestItem, _execute_test

    print(f"\n{'='*60}", flush=True)
    print("[Phase I] 実機 pyATS 検証 開始", flush=True)
    print(f"{'='*60}", flush=True)

    test_plan_items: list[dict] = state.get("test_plan_items", [])
    if not test_plan_items:
        print("  ⚠️  テスト計画がありません（test_plan_items が空）—スキップ", flush=True)
        return {"live_test_results": []}

    # インベントリを読み込んで実機向け testbed YAML を生成
    try:
        inventory_path = _resolve_inventory_path(state)
        devices = pyats_tools.load_inventory(inventory_path)
        testbed_yaml = pyats_tools.build_testbed_from_inventory(devices)
    except (FileNotFoundError, ValueError) as exc:
        msg = f"インベントリ読み込みエラー（verify フェーズ）: {exc}"
        print(f"  [❌] {msg}", flush=True)
        return {
            "live_test_results": [
                {"test": "live_verify_setup", "result": "FAIL", "detail": msg}
            ]
        }

    # テスト計画を復元して実行
    test_items: list[TestItem] = []
    for item_dict in test_plan_items:
        try:
            test_items.append(TestItem(**item_dict))
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠️  TestItem の復元に失敗: {exc}", flush=True)

    print(f"  テスト計画: {len(test_items)} 件 [実機]", flush=True)
    live_test_results: list = []

    for i, item in enumerate(test_items, 1):
        print(f"  ({i}/{len(test_items)}) {item.description} [実機]", flush=True)
        try:
            result = _execute_test(item, testbed_yaml)
        except ImportError:
            result = {
                "test": item.description,
                "result": "FAIL",
                "detail": "pyATS/Genie が未インストールです。uv sync --extra network を実行してください。",
            }
        mark = "✅ PASS" if result["result"] == "PASS" else "❌ FAIL"
        print(f"       → {mark}  {result['detail']}", flush=True)
        live_test_results.append(result)

    passed = sum(1 for r in live_test_results if r["result"] == "PASS")
    print(
        f"\n  [Phase I] 実機検証完了 — {passed}/{len(live_test_results)} テスト PASS",
        flush=True,
    )

    return {"live_test_results": live_test_results}


def live_report_node(state: AgentState) -> dict:
    """Phase I Step 5: 実機適用結果レポートを生成し ``final_report`` に追記する。

    ``live_apply_node`` または ``live_rollback_node`` の直後に実行される。

    **state 更新キー**:

    * ``live_report``  — 実機適用の詳細レポート（Markdown）
    * ``final_report`` — ``live_report`` を末尾に追記
    """
    records: list[LiveApplyRecord] = state.get("live_apply_records", [])
    decision: str = state.get("live_human_decision", "yes")
    is_rollback_only: bool = decision == "rollback-only"

    print("\n  >>> 実機適用レポートを生成しています...", flush=True)

    lines: list[str] = [
        "",
        "---",
        "",
        f"## 実機適用レポート（Phase I）"
        + ("  — rollback-only モード" if is_rollback_only else ""),
        "",
        f"**生成日時**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ]

    if is_rollback_only:
        # ----------------------------------------------------------------
        # rollback-only モードのレポート
        # ----------------------------------------------------------------
        lines.append("**操作**: バックアップへのロールバック")
        lines += [
            "",
            "### ロールバック結果サマリー",
            "",
            "| デバイス | ホスト | ロールバック結果 |",
            "|---|---|---|",
        ]
        for rec in records:
            if not rec.get("backup_config", "").strip():
                rb_mark = "⚠️ スキップ（バックアップなし）"
            elif rec.get("rollback_done"):
                rb_mark = "✅ 成功"
            else:
                rb_mark = "❌ 失敗"
            lines.append(f"| {rec['device']} | {rec['host']} | {rb_mark} |")

        skipped = [r for r in records if not r.get("backup_config", "").strip()]
        failed_rb = [
            r for r in records
            if r.get("backup_config", "").strip() and not r.get("rollback_done")
        ]
        done_rb = [r for r in records if r.get("rollback_done")]

        if failed_rb:
            verdict = f"⚠️ {len(failed_rb)} 台のロールバックに失敗しました"
        elif skipped:
            verdict = (
                f"✅ ロールバック完了"
                f"（{len(skipped)} 台はバックアップなしのためスキップ）"
            )
        else:
            verdict = f"✅ 全 {len(done_rb)} デバイスのロールバックが成功しました"

        lines += ["", f"**判定**: {verdict}"]

        if failed_rb:
            lines += ["", "### 失敗詳細"]
            for rec in failed_rb:
                err = rec.get("rollback_error") or "不明なエラー"
                lines.append(f"- **{rec['device']}** ({rec['host']}): `{err}`")

    else:
        # ----------------------------------------------------------------
        # apply モードのレポート
        # ----------------------------------------------------------------
        modes = list({rec.get("apply_mode", "config_merge") for rec in records})
        mode_str = modes[0] if len(modes) == 1 else "混在"
        lines.append(f"**操作**: 新規コンフィグ投入 ({mode_str})")
        lines += [
            "",
            "### 適用結果サマリー",
            "",
            "| デバイス | ホスト | モード | 適用結果 | ロールバック |",
            "|---|---|---|---|---|",
        ]
        for rec in records:
            if not rec.get("applied_config", "").strip():
                apply_mark = "⏭️ スキップ"
                rb_mark = "—"
            elif rec.get("apply_success"):
                apply_mark = "✅ 成功"
                rb_mark = "—"
            else:
                apply_mark = "❌ 失敗"
                if rec.get("rollback_done"):
                    rb_mark = "✅ ロールバック成功"
                elif rec.get("rollback_error"):
                    rb_mark = "❌ ロールバック失敗"
                else:
                    rb_mark = "⚠️ ロールバック未実施"

            lines.append(
                f"| {rec['device']} | {rec['host']}"
                f" | {rec.get('apply_mode', 'config_merge')}"
                f" | {apply_mark} | {rb_mark} |"
            )

        applied = [r for r in records if r.get("applied_config", "").strip()]
        succeeded = [r for r in applied if r.get("apply_success")]
        failed_ap = [r for r in applied if not r.get("apply_success")]

        if not applied:
            verdict = "⚠️ 投入対象デバイスがありませんでした"
        elif failed_ap:
            rolled_back = [r for r in failed_ap if r.get("rollback_done")]
            verdict = (
                f"⚠️ {len(succeeded)} 台成功 / {len(failed_ap)} 台失敗"
                + (
                    f"（{len(rolled_back)} 台は自動ロールバック実施済み）"
                    if rolled_back
                    else ""
                )
            )
        else:
            verdict = f"✅ 全 {len(succeeded)} デバイスへの投入が成功しました"

        lines += ["", f"**判定**: {verdict}"]

        if failed_ap:
            lines += ["", "### 失敗詳細"]
            for rec in failed_ap:
                lines.append(f"#### {rec['device']} ({rec['host']}) — ❌ 失敗")
                lines.append(f"- エラー: `{rec.get('apply_error') or '不明'}`")
                if rec.get("rollback_done"):
                    backup_lines = rec.get("backup_lines", 0)
                    lines.append(
                        f"- ロールバック: ✅ 成功（バックアップ {backup_lines} 行を復元）"
                    )
                elif rec.get("rollback_error"):
                    lines.append(
                        f"- ロールバック: ❌ 失敗 — `{rec['rollback_error']}`"
                    )
                else:
                    lines.append("- ロールバック: ⚠️ 未実施")

    # ----------------------------------------------------------------
    # 実機 pyATS 検証結果セクション（--live-verify 時のみ）
    # ----------------------------------------------------------------
    live_test_results: list = state.get("live_test_results", [])
    if live_test_results:
        lines += [
            "",
            "### 実機 pyATS 検証結果（--live-verify）",
            "",
            "| テスト名 | 結果 | 詳細 |",
            "|---|---|---|",
        ]
        for r in live_test_results:
            mark = "✅ PASS" if r["result"] == "PASS" else "❌ FAIL"
            lines.append(f"| {r['test']} | {mark} | {r.get('detail', '')} |")

        passed = sum(1 for r in live_test_results if r["result"] == "PASS")
        failed = len(live_test_results) - passed
        if failed == 0:
            verify_verdict = f"✅ 実機検証: 全 {passed} テスト PASS"
        else:
            verify_verdict = f"⚠️ 実機検証: {passed} PASS / {failed} FAIL"
        lines += ["", f"**実機検証判定**: {verify_verdict}"]

    live_report = "\n".join(lines)

    return {
        "live_report": live_report,
        "final_report": state.get("final_report", "") + live_report,
    }


def compile_graph_apply_to_live():
    """Phase I: precheck → confirm → apply/rollback → (verify) → report のグラフ（全ステップ統合）。

    フロー::

        live_precheck
            ↓ confirm (success)      ↓ abort (failure) → END
        human_confirm
            ↓ apply                  ↓ cancelled → END    ↓ rollback
        live_apply              live_rollback
            ↓ verify (verify_enabled=True)        ↓
        live_verify             live_report ←──────
            ↓                       ↑
            └───────────────────────┘
                (verify_enabled=False or rollback)

    ``interrupt()`` を使用するため :class:`MemorySaver` チェックポインターが必須。
    """
    from langgraph.checkpoint.memory import MemorySaver

    graph = StateGraph(AgentState)
    graph.add_node("live_precheck", live_precheck_node)
    graph.add_node("human_confirm", human_confirm_live_node)
    graph.add_node("live_apply", live_apply_node)
    graph.add_node("live_rollback", live_rollback_node)
    graph.add_node("live_verify", live_verify_node)
    graph.add_node("live_report", live_report_node)

    graph.set_entry_point("live_precheck")

    graph.add_conditional_edges(
        "live_precheck",
        _should_continue_after_precheck,
        {"confirm": "human_confirm", "abort": END},
    )

    graph.add_conditional_edges(
        "human_confirm",
        _should_continue_after_confirm,
        {
            "apply": "live_apply",
            "rollback": "live_rollback",
            "cancelled": END,
        },
    )

    # live_apply 後: --live-verify 指定時は live_verify → live_report、それ以外は直接 live_report
    graph.add_conditional_edges(
        "live_apply",
        _should_verify_after_apply,
        {
            "verify": "live_verify",
            "report": "live_report",
        },
    )
    graph.add_edge("live_verify", "live_report")
    graph.add_edge("live_rollback", "live_report")
    graph.add_edge("live_report", END)

    return graph.compile(checkpointer=MemorySaver())


def initial_state_apply_to_live(
    requirement: str,
    prompt_set: str = "demo",
    inventory_path: str = "",
    live_verify_enabled: bool = False,
    lab_id: str = "",
) -> AgentState:
    """実機適用モードの初期ステートを生成するファクトリー関数。

    Args:
        requirement:         ネットワーク要件テキスト（CML 検証済み設計と一致すること）。
        prompt_set:          使用するプロンプトセット名。
        inventory_path:      インベントリ YAML のパス。省略時は inventory/<prompt_set>.yaml を使用。
        live_verify_enabled: True の場合、適用後に pyATS で実機テストを実行する。
        lab_id:              CML 上で検証済みのラボ ID。
    """
    return AgentState(
        requirement=requirement,
        prompt_set=prompt_set,
        fault_simulation_enabled=False,
        skip_deploy=False,
        error_history=[],
        topology_yaml="",
        device_configs={},
        device_config_paths={},
        lab_id=lab_id,
        test_results=[],
        test_plan_items=[],
        error_log="",
        retry_count=0,
        failed_devices=[],
        fault_scenario_results=[],
        fault_report="",
        troubleshoot_lab_id="",
        troubleshoot_issue="",
        collected_state={},
        diagnosis="",
        fix_records=[],
        troubleshoot_retry_count=0,
        troubleshoot_report="",
        analyze_request="",
        analysis_result="",
        live_inventory_path=inventory_path,
        live_apply_records=[],
        live_verify_enabled=live_verify_enabled,
        live_human_decision="",
        live_test_results=[],
        live_report="",
        final_report="",
    )


def compile_graph_live_precheck():
    """Phase I Step 2: プレチェックのみを実行する最小グラフ（テスト・デバッグ用）。

    フロー: live_precheck → END
    後続ステップ（human_confirm / live_apply 等）は Step 3 以降で追加する。
    """
    graph = StateGraph(AgentState)
    graph.add_node("live_precheck", live_precheck_node)
    graph.set_entry_point("live_precheck")
    graph.add_edge("live_precheck", END)
    return graph.compile()


# ---------------------------------------------------------------------------
# Phase I: CLI ヘルパー
# ---------------------------------------------------------------------------


def _run_live_apply_flow(
    cml_state: dict,
    prompt_set: str,
    inventory_path: str,
    live_verify: bool) -> None:
    """CML 検証済み状態から実機適用フローを実行する（CLI 専用）。

    LangGraph の interrupt/resume 機構を使って Human 承認を取得し、
    その結果に応じて apply または rollback を実行する。

    Args:
        cml_state:       CML 設計・検証フローの最終ステート。
        prompt_set:      プロンプトセット名（インベントリ自動解決に使用）。
        inventory_path:  インベントリ YAML のパス（空文字の場合は自動解決）。
        live_verify:     True の場合、適用後に pyATS 実機テストを実行する。
    """
    import time
    from langgraph.types import Command

    print(f"\n{'='*60}", flush=True)
    print("[Phase I] 実機適用モード 開始", flush=True)
    print(f"{'='*60}", flush=True)

    # CML 結果を引き継いで live apply 初期状態を構築
    live_state = initial_state_apply_to_live(
        requirement=cml_state.get("requirement", ""),
        prompt_set=prompt_set,
        inventory_path=inventory_path,
        live_verify_enabled=live_verify,
        lab_id=cml_state.get("lab_id", ""),
    )
    live_state["device_configs"] = cml_state.get("device_configs", {})
    live_state["topology_yaml"] = cml_state.get("topology_yaml", "")
    live_state["test_results"] = cml_state.get("test_results", [])
    live_state["test_plan_items"] = cml_state.get("test_plan_items", [])
    live_state["final_report"] = cml_state.get("final_report", "")

    live_app = compile_graph_apply_to_live()
    thread = {"configurable": {"thread_id": f"live-{prompt_set}-{int(time.time())}"}}

    # 第 1 回目 invoke: live_precheck → human_confirm (interrupt) で一時停止
    partial = live_app.invoke(live_state, thread)

    # precheck 失敗（グラフが abort → END まで進んだ場合）
    if partial and partial.get("error_log"):
        import sys
        print(f"\nエラー（プレチェック失敗）: {partial['error_log']}", file=sys.stderr)
        sys.exit(1)

    # 確認画面を表示（live_apply_records を使って組み立てる）
    if partial:
        msg_state: dict = {**live_state}
        msg_state.update(
            {k: partial[k] for k in ("live_apply_records", "test_results") if k in partial}
        )
        print("\n" + _build_confirmation_message(msg_state), flush=True)

    # 人間の入力を取得
    import sys
    print()
    try:
        response = input("続行しますか？ (yes / no / rollback-only): ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        response = "no"

    if response not in ("yes", "rollback-only"):
        response = "no"

    reason = ""
    if response in ("no", "rollback-only"):
        try:
            reason = input("理由（省略可、Enter でスキップ）: ").strip()
        except (KeyboardInterrupt, EOFError):
            reason = ""

    # 第 2 回目 invoke: 承認結果を渡してフローを再開
    result = live_app.invoke(
        Command(resume={"decision": response, "reason": reason}),
        thread,
    )

    print()
    print(result.get("final_report", "(レポートなし)"), flush=True)


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
            "  --list                 利用可能なプロンプトセット一覧を表示して終了する\n"
            "  --dry-run              CMLデプロイをスキップして設計・コンフィグ生成のみ行う\n"
            "  --use-topology         configs/<set>/topology.yaml をトポロジーとして使用し、コンフィグのみ生成する\n"
            "  --fault-sim            構成検証成功後に障害シミュレーション（リンク断・復旧・再テスト）を実行する\n"
            "  --troubleshoot [ID]    既存ラボをトラブルシュート（ID 省略時はラボ名で自動検索）\n"
            "  --issue '<説明>'       --troubleshoot と併用する問題の説明（任意）\n"
            "  --analyze [ID]         既存ラボの設計を分析してレポートを出力する（変更なし）\n"
            "  --improve [ID]         既存ラボのコンフィグを改善して configs/<set>/ に保存する\n"
            "  --request '<改善要求>' --improve と併用する改善要求テキスト（任意）\n"
            "  --apply-to-live        CML 検証成功後に実機へコンフィグ投入する（要インベントリ）\n"
            "  --inventory <path>     --apply-to-live で使うインベントリ YAML のパスを明示指定する\n"
            "  --live-verify          --apply-to-live 実行後に pyATS で実機テストを実行する\n"
            "  --rag-index [<dir>]    rag/ のテキストファイルを知識ベースに索引化する（要 chromadb）\n"
            "  --rag-clear-knowledge  知識ベースのインデックスを全消去する\n"
            "  --rag-stats            RAGストアの保存件数と保存場所を表示して終了する\n"
            "  -h / --help            このヘルプを表示して終了する\n"
            "\n"
            "例:\n"
            "  agentic-ni demo                              # demo セットの要件で実行\n"
            "  agentic-ni ospf_l3vpn                        # ospf_l3vpn セットの要件で実行\n"
            "  agentic-ni demo --dry-run                    # CMLなしでコンフィグ生成のみ\n"
            "  agentic-ni demo3 --use-topology --dry-run    # 手動作成トポロジーYAMLを使いコンフィグのみ生成\n"
            "  agentic-ni demo2 --fault-sim                 # 障害シミュレーションありで実行\n"
            "  agentic-ni demo2 --troubleshoot              # demo2 ラボを自動検索しトラブルシュート\n"
            "  agentic-ni demo2 --troubleshoot abc-1234     # lab_id を明示してトラブルシュート\n"
            "  agentic-ni demo --analyze                    # demo ラボの設計を分析する\n"
            "  agentic-ni demo --analyze abc-1234           # 指定 lab_id の設計を分析する\n"
            "  agentic-ni demo --improve --request 'OSPFにBFDを追加したい'\n"
            "  agentic-ni demo --apply-to-live              # CML検証後に実機投入\n"
            "  agentic-ni demo --apply-to-live --inventory inventory/prod.yaml --live-verify\n"
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
        print(f"  実行ログ RAG (成功事例): {stats['total_cases']} 件")
        print(f"  知識ベース RAG (テキストファイル): {stats.get('knowledge_chunks', 0)} チャンク")
        print(f"  保存場所: {stats['db_path']}")
        return

    # --rag-index [ディレクトリ]: 知識ベースを索引化して終了
    if "--rag-index" in args:
        from agentic_ni.tools import rag_tools
        # --rag-index の次の引数はディレクトリパス（省略時は "rag")
        rag_index_dir = "rag"
        for i, arg in enumerate(args):
            if arg == "--rag-index" and i + 1 < len(args) and not args[i + 1].startswith("-"):
                rag_index_dir = args[i + 1]
                break
        try:
            print(f"知識ベースを索引化中: {rag_index_dir}")
            count = rag_tools.index_knowledge_files(rag_index_dir)
            print(f"完了: 合計 {count} チャンクを知識ベースに登録しました。")
        except FileNotFoundError as exc:
            print(f"エラー: {exc}", file=sys.stderr)
            sys.exit(1)
        return

    # --rag-clear-knowledge: 知識ベースを全消去して終了
    if "--rag-clear-knowledge" in args:
        from agentic_ni.tools import rag_tools
        rag_tools.clear_knowledge_base()
        print("知識ベースのインデックスを全消去しました。")
        return

    dry_run = "--dry-run" in args
    fault_simulation_enabled = "--fault-sim" in args
    use_provided_topology: bool = "--use-topology" in args
    troubleshoot_mode: bool = "--troubleshoot" in args
    analyze_mode: bool = "--analyze" in args
    improve_mode: bool = "--improve" in args
    apply_to_live: bool = "--apply-to-live" in args
    live_verify: bool = "--live-verify" in args
    troubleshoot_lab_id: str | None = None
    troubleshoot_issue: str = ""
    analyze_lab_id: str | None = None
    improve_lab_id: str | None = None
    improve_request: str = ""
    live_inventory_path: str = ""
    # フラグが消費する値（positionals から除外する）
    _flag_consumed: set[str] = set()
    for i, arg in enumerate(args):
        if arg == "--troubleshoot" and i + 1 < len(args) and not args[i + 1].startswith("-"):
            troubleshoot_lab_id = args[i + 1]
            _flag_consumed.add(args[i + 1])
        if arg == "--issue" and i + 1 < len(args) and not args[i + 1].startswith("-"):
            troubleshoot_issue = args[i + 1]
            _flag_consumed.add(args[i + 1])
        if arg == "--analyze" and i + 1 < len(args) and not args[i + 1].startswith("-"):
            analyze_lab_id = args[i + 1]
            _flag_consumed.add(args[i + 1])
        if arg == "--improve" and i + 1 < len(args) and not args[i + 1].startswith("-"):
            improve_lab_id = args[i + 1]
            _flag_consumed.add(args[i + 1])
        if arg == "--request" and i + 1 < len(args) and not args[i + 1].startswith("-"):
            improve_request = args[i + 1]
            _flag_consumed.add(args[i + 1])
        if arg == "--inventory" and i + 1 < len(args) and not args[i + 1].startswith("-"):
            live_inventory_path = args[i + 1]
            _flag_consumed.add(args[i + 1])

    # 位置引数（フラグ以外かつフラグの値でないもの）= プロンプトセット名
    positional = [
        a for a in args
        if not a.startswith("-") and a not in _flag_consumed
    ]
    # --analyze / --improve はプロンプトセット省略可（デフォルト: "demo"）
    if not positional and (analyze_mode or improve_mode):
        prompt_set = "demo"
    elif not positional:
        print("エラー: プロンプトセット名を指定してください。", file=sys.stderr)
        print("  利用可能なセット確認: agentic-ni --list", file=sys.stderr)
        sys.exit(1)
    elif len(positional) > 1:
        print(f"エラー: 引数が多すぎます: {positional}", file=sys.stderr)
        sys.exit(1)
    else:
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
    if use_provided_topology:
        print(f"トポロジー: configs/{prompt_set}/topology.yaml を使用（コンフィグのみ生成モード）")
    if fault_simulation_enabled:
        print("障害シミュレーション: 有効")
    if troubleshoot_mode:
        if troubleshoot_lab_id:
            print(f"トラブルシューティングモード: ラボID={troubleshoot_lab_id}")
        else:
            print("トラブルシューティングモード: ラボ自動検索")
        if troubleshoot_issue:
            print(f"問題説明: {troubleshoot_issue}")
    if analyze_mode:
        print(f"分析モード: ラボID={analyze_lab_id or '(自動検索)'}")
    if improve_mode:
        print(f"改善モード: ラボID={improve_lab_id or '(自動検索)'}")
        if improve_request:
            print(f"改善要求: {improve_request}")
    if apply_to_live:
        print(f"実機適用モード: 有効")
        if live_inventory_path:
            print(f"  インベントリ: {live_inventory_path}")
        if live_verify:
            print(f"  実機 pyATS 検証: 有効")
    print()
    if not (analyze_mode or improve_mode):
        print("【要件】")
        for line in requirement.splitlines():
            print(f"  {line}")
        print()
    print("処理を開始します...\n")

    # 分析モード
    if analyze_mode:
        if not analyze_lab_id:
            lab_title = f"agentic-ni-{prompt_set}"
            from agentic_ni.tools import cml_tools as _cml
            found = _cml.find_lab_by_title(lab_title)
            if found is None:
                print(f"エラー: ラボ '{lab_title}' が見つかりません。", file=sys.stderr)
                print(f"  先に通常モードで実行してラボを作成するか、--analyze に lab_id を明示してください。", file=sys.stderr)
                sys.exit(1)
            analyze_lab_id = found
            print(f"ラボを自動検出: {lab_title} (ID={analyze_lab_id})")
        app = compile_graph_analyze()
        result = app.invoke(initial_state_analyze(analyze_lab_id, prompt_set))
        print(result.get("final_report", "(レポートなし)"))
        return

    # 改善モード
    if improve_mode:
        if not improve_lab_id:
            lab_title = f"agentic-ni-{prompt_set}"
            from agentic_ni.tools import cml_tools as _cml
            found = _cml.find_lab_by_title(lab_title)
            if found is None:
                print(f"エラー: ラボ '{lab_title}' が見つかりません。", file=sys.stderr)
                print(f"  先に通常モードで実行してラボを作成するか、--improve に lab_id を明示してください。", file=sys.stderr)
                sys.exit(1)
            improve_lab_id = found
            print(f"ラボを自動検出: {lab_title} (ID={improve_lab_id})")
        app = compile_graph_improve()
        result = app.invoke(initial_state_improve(improve_lab_id, improve_request, prompt_set))
        print(result.get("final_report", "(レポートなし)"))
        return

    # トラブルシューティングモード
    if troubleshoot_mode:
        if not troubleshoot_lab_id:
            # lab_id 省略時はラボタイトル "agentic-ni-{prompt_set}" で自動検索
            lab_title = f"agentic-ni-{prompt_set}"
            from agentic_ni.tools import cml_tools as _cml
            found = _cml.find_lab_by_title(lab_title)
            if found is None:
                print(
                    f"エラー: ラボ '{lab_title}' が見つかりません。",
                    file=sys.stderr,
                )
                print(
                    f"  先に通常モードで実行してラボを作成するか、--troubleshoot に lab_id を明示してください。",
                    file=sys.stderr,
                )
                print(f"    利用例: agentic-ni {prompt_set}", file=sys.stderr)
                sys.exit(1)
            troubleshoot_lab_id = found
            print(f"ラボを自動検出: {lab_title} (ID={troubleshoot_lab_id})")
        app = compile_graph_troubleshoot()
        result = app.invoke(
            initial_state_troubleshoot(troubleshoot_lab_id, troubleshoot_issue, prompt_set)
        )
        print(result.get("final_report", "(レポートなし)"))
        return

    app = compile_graph_dry_run() if dry_run else compile_graph()

    # --use-topology: トポロジーファイルの存在チェック
    if use_provided_topology:
        topology_path = Path("configs") / prompt_set / "topology.yaml"
        if not topology_path.exists():
            print(
                f"エラー: --use-topology が指定されましたが、トポロジーファイルが見つかりません。\n"
                f"  期待パス: {topology_path}",
                file=sys.stderr,
            )
            sys.exit(1)

    # --fault-sim 時に同名ラボが既存かどうか確認し、あればデプロイをスキップ
    skip_deploy = False
    existing_lab_id = ""
    if fault_simulation_enabled and not dry_run:
        lab_title = f"agentic-ni-{prompt_set}"
        try:
            from agentic_ni.tools import cml_tools as _cml
            found = _cml.find_lab_by_title(lab_title)
            if found:
                skip_deploy = True
                existing_lab_id = found
                print(f"既存ラボを検出: {lab_title} (ID={found}) → デプロイをスキップして障害検証を実施します")
        except Exception:  # noqa: BLE001
            pass  # CML 未接続時は無視して通常フローへ

    result = app.invoke(initial_state(requirement, prompt_set, fault_simulation_enabled, skip_deploy, existing_lab_id, use_provided_topology))
    print(result.get("final_report", "(レポートなし)"))

    # Phase I: 実機適用モード（--apply-to-live 指定時）
    if apply_to_live and not dry_run:
        test_results = result.get("test_results", [])
        if not test_results or not all(r["result"] == "PASS" for r in test_results):
            print(
                "\nCML 検証がすべて PASS していないため、実機適用をスキップします。",
                file=sys.stderr,
            )
            sys.exit(1)
        _run_live_apply_flow(
            cml_state=result,
            prompt_set=prompt_set,
            inventory_path=live_inventory_path,
            live_verify=live_verify,
        )


if __name__ == "__main__":
    main()
