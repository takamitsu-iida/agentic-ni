"""Phase TS-5: E2E 統合テスト。

MockLLM を使って実際の LLM API なしでエージェント間協調の全フローを検証する。

シナリオ A: リンク断 → Agent-R1 検知 → Agent-R2 に問い合わせ → HUMAN エスカレーション
シナリオ B: OSPF ネイバー消失 → 3 エージェント協調調査 → レポート生成
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, BaseMessage

from agentic_ni.distributed.bus import InMemoryBus
from agentic_ni.distributed.device_agent import SyslogEvent
from agentic_ni.distributed.device_tools import MockDeviceToolkit
from agentic_ni.distributed.message import AgentMessage
from agentic_ni.distributed.orchestrator import AgentOrchestrator
from agentic_ni.distributed.reporter import ConversationRecorder, generate_report, save_report


# ---------------------------------------------------------------------------
# テスト用 MockLLM（TS-3 と同様のパターン）
# ---------------------------------------------------------------------------

class _SequenceLLM:
    """エージェントごとのシーケンスを再生するモック LLM。

    各 step:
      - str  → テキスト最終応答
      - dict → tool_call を発行 {"name", "args", "id"}
    """

    def __init__(self, sequence: list[str | dict], name: str = "") -> None:
        self._sequence = list(sequence)
        self._idx = 0
        self.name = name  # デバッグ用
        self.history: list[str] = []

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages: list[BaseMessage]) -> AIMessage:
        item = self._sequence[min(self._idx, len(self._sequence) - 1)]
        self._idx += 1
        self.history.append(str(item)[:80])

        if isinstance(item, str):
            return AIMessage(content=item)

        return AIMessage(
            content="",
            tool_calls=[{
                "name": item["name"],
                "args": item.get("args", {}),
                "id": item.get("id", f"call_{self._idx}"),
                "type": "tool_call",
            }],
        )


# ---------------------------------------------------------------------------
# トポロジーデータ
# ---------------------------------------------------------------------------

_P2P_TOPO = {
    "lab": {"title": "e2e-p2p"},
    "nodes": [
        {
            "id": "n0", "label": "R1", "node_definition": "iosv",
            "interfaces": [{"id": "i0", "label": "GigabitEthernet0/0"}],
        },
        {
            "id": "n1", "label": "R2", "node_definition": "iosv",
            "interfaces": [{"id": "i0", "label": "GigabitEthernet0/0"}],
        },
    ],
    "links": [
        {"id": "l0", "n1": "n0", "i1": "i0", "n2": "n1", "i2": "i0", "label": "l0"},
    ],
}

_TRIANGLE_TOPO = {
    "lab": {"title": "e2e-triangle"},
    "nodes": [
        {
            "id": "n0", "label": "R1", "node_definition": "iosv",
            "interfaces": [
                {"id": "i0", "label": "GigabitEthernet0/0"},
                {"id": "i1", "label": "GigabitEthernet0/1"},
            ],
        },
        {
            "id": "n1", "label": "R2", "node_definition": "iosv",
            "interfaces": [
                {"id": "i0", "label": "GigabitEthernet0/0"},
                {"id": "i1", "label": "GigabitEthernet0/1"},
            ],
        },
        {
            "id": "n2", "label": "R3", "node_definition": "iosv",
            "interfaces": [
                {"id": "i0", "label": "GigabitEthernet0/0"},
                {"id": "i1", "label": "GigabitEthernet0/1"},
            ],
        },
    ],
    "links": [
        {"id": "l0", "n1": "n0", "i1": "i0", "n2": "n1", "i2": "i0", "label": "l0"},
        {"id": "l1", "n1": "n0", "i1": "i1", "n2": "n2", "i2": "i0", "label": "l1"},
        {"id": "l2", "n1": "n1", "i1": "i1", "n2": "n2", "i2": "i1", "label": "l2"},
    ],
}


# ---------------------------------------------------------------------------
# ヘルパー：オーケストレーターのセットアップ
# ---------------------------------------------------------------------------

async def _setup_orchestrator(
    topology: dict,
    agent_llms: dict[str, _SequenceLLM],  # label → LLM
    mock_show: dict[str, dict[str, str]] | None = None,  # label → {command → response}
    tmp_path: Path | None = None,
) -> tuple[AgentOrchestrator, InMemoryBus, ConversationRecorder]:
    import yaml

    bus = InMemoryBus()
    await bus.connect()

    recorder = ConversationRecorder(bus)
    await recorder.start()

    show_map = mock_show or {}

    def toolkit_factory(device_name: str) -> MockDeviceToolkit:
        return MockDeviceToolkit(
            device_name,
            show_responses=show_map.get(device_name, {}),
            readonly=True,
        )

    class _PerDeviceLLM:
        """エージェントラベルに応じて異なる _SequenceLLM を返すファクトリ LLM。"""
        def bind_tools(self, tools): return self
        async def ainvoke(self, messages): raise NotImplementedError

    orch = AgentOrchestrator(bus=bus, toolkit_factory=toolkit_factory)

    if tmp_path:
        topo_file = tmp_path / "topology.yaml"
        topo_file.write_text(yaml.dump(topology))
        await orch.start_from_topology(topo_file)
    else:
        import tempfile, os
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(yaml.dump(topology))
            topo_file = f.name
        await orch.start_from_topology(topo_file)
        os.unlink(topo_file)

    # エージェントごとに LLM を差し込む
    for label, llm in agent_llms.items():
        agent_id = f"Agent-{label}"
        if agent_id in orch.get_all_agents():
            orch.get_agent(agent_id)._llm = llm

    return orch, bus, recorder


# ---------------------------------------------------------------------------
# シナリオ A: リンク断 → 2エージェント協調 → HUMAN エスカレーション
# ---------------------------------------------------------------------------

class TestScenarioA_LinkDown:
    async def test_full_flow(self, tmp_path: Path):
        """
        新フロー: receive_syslog → Correlator → Coordinator → execute_query ×2 → RCA → human_queue
        """
        r1_llm = _SequenceLLM(
            ["TO: COORDINATOR | MSG: R1 の Gi0/0 が down/down。物理リンク障害の疑い。"],
            name="R1",
        )
        r2_llm = _SequenceLLM(
            ["TO: COORDINATOR | MSG: R2 の Gi0/0 が down/down。リンク断を確認。"],
            name="R2",
        )
        coordinator_llm = _SequenceLLM([
            "FINAL_REPORT:\n"
            "- 症状: R1-R2 間リンクダウン\n"
            "- 根本原因: 物理ケーブル障害\n"
            "- 影響範囲: R1-R2 間の全通信\n"
            "- 推奨対応:\n"
            "  1. ケーブルを確認してください"
        ])

        orch, bus, recorder = await _setup_orchestrator(
            _P2P_TOPO, {"R1": r1_llm, "R2": r2_llm}, tmp_path=tmp_path
        )
        orch._coordinator._llm = coordinator_llm

        await orch.receive_syslog(
            "R1", "%LINK-3-UPDOWN: Interface GigabitEthernet0/0, changed state to down"
        )
        await orch.receive_syslog(
            "R2", "%LINK-3-UPDOWN: Interface GigabitEthernet0/0, changed state to down"
        )
        # Correlator を即時フラッシュして Incident を発火
        await orch._correlator.flush_all()
        await asyncio.sleep(0.3)

        await orch.stop_all()
        await recorder.stop()
        await bus.close()

        assert not orch.human_queue.empty(), "RCA レポートが human_queue に屁きませんでした"
        report = orch.human_queue.get_nowait()
        assert report["type"] == "rca_report"
        assert "FINAL_REPORT" in report["content"]
        assert "R1" in report["affected_devices"] or "R2" in report["affected_devices"]

    async def test_r1_tool_result_stored_in_memory(self, tmp_path: Path):
        """R1 のツール呼び出し結果がメモリに記録されること。"""
        r1_llm = _SequenceLLM([
            {"name": "get_interface_status", "args": {}, "id": "c1"},
            "TO: LOG | MSG: インターフェース状態確認完了",
        ], name="R1")
        r2_llm = _SequenceLLM(["TO: LOG | MSG: 待機中"], name="R2")

        orch, bus, recorder = await _setup_orchestrator(
            _P2P_TOPO, {"R1": r1_llm, "R2": r2_llm}, tmp_path=tmp_path
        )

        r1 = orch.get_agent("Agent-R1")
        await r1.inject_event(SyslogEvent(raw_text="テスト"))
        await r1.wait_idle()

        tool_entries = [s for s in r1._memory.status_history if s.source == "tool"]
        assert len(tool_entries) >= 1

        await orch.stop_all()
        await recorder.stop()
        await bus.close()


# ---------------------------------------------------------------------------
# シナリオ B: OSPF ネイバー消失 → 3エージェント協調調査
# ---------------------------------------------------------------------------

class TestScenarioB_OspfDown:
    async def test_three_agent_coordination(self, tmp_path: Path):
        """
        新フロー: OSPF SYSLOG を2台から受信 → Correlatorが1 Incidentに束ねる
        → Coordinator が両エージェントに execute_query → RCA レポート
        """
        r1_llm = _SequenceLLM(
            ["TO: COORDINATOR | MSG: R1 の OSPF ネイバーが全消失。プロセス専用メモリ榴溈の可能。"],
            name="R1",
        )
        r2_llm = _SequenceLLM(
            ["TO: COORDINATOR | MSG: R2 でも OSPF ネイバーが消失。対向リンクがダウンしている。"],
            name="R2",
        )
        coordinator_llm = _SequenceLLM([
            "FINAL_REPORT:\n"
            "- 症状: R1-R2 間 OSPF ネイバー全消失\n"
            "- 根本原因: 物理リンク障害による OSPF セッション断\n"
            "- 影響範囲: R1-R2 間のルーティング\n"
            "- 推奨対応:\n"
            "  1. 物理リンクを確認してください"
        ])

        orch, bus, recorder = await _setup_orchestrator(
            _P2P_TOPO, {"R1": r1_llm, "R2": r2_llm}, tmp_path=tmp_path
        )
        orch._coordinator._llm = coordinator_llm

        # R1/R2 両方から OSPF SYSLOG を送信—同一 Gi0/0 リンクに属するので 1 Incident
        await orch.receive_syslog(
            "R1",
            "%OSPF-5-ADJCHG: Process 1, Nbr 10.0.0.2 on GigabitEthernet0/0 from FULL to DOWN",
        )
        await orch.receive_syslog(
            "R2",
            "%OSPF-5-ADJCHG: Process 1, Nbr 10.0.0.1 on GigabitEthernet0/0 from FULL to DOWN",
        )
        await orch._correlator.flush_all()
        await asyncio.sleep(0.3)

        await orch.stop_all()
        await recorder.stop()
        await bus.close()

        assert not orch.human_queue.empty(), "RCA レポートが屑きませんでした"
        report = orch.human_queue.get_nowait()
        assert report["type"] == "rca_report"
        assert "FINAL_REPORT" in report["content"]


# ---------------------------------------------------------------------------
# レポート生成テスト
# ---------------------------------------------------------------------------

class TestReportGeneration:
    async def test_report_contains_key_sections(self, tmp_path: Path):
        """レポートに必須セクションが含まれること。"""
        r1_llm = _SequenceLLM([
            "TO: HUMAN | MSG: R1-R2 リンク障害を検知しました。"
        ], name="R1")
        r2_llm = _SequenceLLM(["TO: LOG | MSG: 待機中"], name="R2")

        orch, bus, recorder = await _setup_orchestrator(
            _P2P_TOPO, {"R1": r1_llm, "R2": r2_llm}, tmp_path=tmp_path
        )

        r1 = orch.get_agent("Agent-R1")
        await r1.inject_event(SyslogEvent(raw_text="%LINK-3-UPDOWN: GigabitEthernet0/0 down"))
        await r1.wait_idle()

        report = generate_report(
            orch, recorder,
            topology_path="configs/demo2/topology.yaml",
            scenario_name="シナリオA: リンク断",
        )

        assert "分散型トラブルシューティングレポート" in report
        assert "エージェント構成" in report
        assert "エージェント間対話ログ" in report
        assert "HUMAN エスカレーション" in report
        assert "Agent-R1" in report

        await orch.stop_all()
        await recorder.stop()
        await bus.close()

    async def test_report_saved_to_reports_dir(self, tmp_path: Path):
        """generate_report() + save_report() でファイルが生成されること。"""
        r1_llm = _SequenceLLM(["TO: LOG | MSG: テスト"], name="R1")
        r2_llm = _SequenceLLM(["TO: LOG | MSG: テスト"], name="R2")

        orch, bus, recorder = await _setup_orchestrator(
            _P2P_TOPO, {"R1": r1_llm, "R2": r2_llm}, tmp_path=tmp_path
        )
        await orch.stop_all()
        await recorder.stop()
        await bus.close()

        report = generate_report(orch, recorder, scenario_name="テスト")
        saved_path = save_report(report, prefix="ts-test")

        assert saved_path.exists()
        content = saved_path.read_text(encoding="utf-8")
        assert "分散型トラブルシューティングレポート" in content

        # テスト後にファイルを削除（クリーンアップ）
        saved_path.unlink()

    async def test_conversation_log_format(self, tmp_path: Path):
        """ConversationRecorder が対話ログを正しくフォーマットすること。"""
        bus = InMemoryBus()
        await bus.connect()
        recorder = ConversationRecorder(bus)
        await recorder.start()

        msg = AgentMessage(
            from_agent="Agent-R1", to_agent="Agent-R2",
            msg_type="query", content="OSPF 状態を確認してください",
        )
        await bus.publish("network/agents/Agent-R2/direct", msg)
        await asyncio.sleep(0.05)

        formatted = recorder.format_conversation()
        assert "Agent-R1" in formatted
        assert "Agent-R2" in formatted
        assert "OSPF" in formatted

        await recorder.stop()
        await bus.close()

    async def test_empty_recorder_format(self):
        """メッセージがない場合のフォーマットが空文字列にならないこと。"""
        bus = InMemoryBus()
        recorder = ConversationRecorder(bus)
        assert recorder.format_conversation() == "（メッセージなし）"


# ---------------------------------------------------------------------------
# システムプロンプトの品質テスト
# ---------------------------------------------------------------------------

class TestSystemPrompt:
    def test_prompt_contains_required_sections(self):
        """device_agent_system.md に必須セクションが含まれること。"""
        from agentic_ni.distributed.prompts import build_device_prompt

        prompt = build_device_prompt(
            device_name="R1", device_type="router",
            management_ip="192.168.0.1",
        )
        required_sections = [
            "あなたの役割",
            "行動指針",
            "出力フォーマット",
            "調査完了条件",
            "TO:",
        ]
        for section in required_sections:
            assert section in prompt, f"プロンプトに '{section}' が含まれていません"

    def test_prompt_tool_policy_mentioned(self):
        """ツール利用ポリシー（Read-Only）がプロンプトに明記されていること。"""
        from agentic_ni.distributed.prompts import build_device_prompt

        prompt = build_device_prompt("SW1", "switch", "10.0.0.1")
        assert "Read-Only" in prompt or "read-only" in prompt or "読み取り" in prompt or "参照のみ" in prompt

    def test_prompt_variables_all_substituted(self):
        """プロンプトのすべての変数が置換されること。"""
        from agentic_ni.distributed.prompts import build_device_prompt
        from agentic_ni.distributed.memory import DeviceMemory, NeighborInfo

        mem = DeviceMemory("R1")
        mem.set_neighbor("Agent-R2", NeighborInfo(
            agent_id="Agent-R2", device_name="R2",
            interface="GigabitEthernet0/0", management_ip="10.0.0.2",
        ))
        prompt = build_device_prompt(
            device_name="R1", device_type="router",
            management_ip="10.0.0.1", neighbors=mem.neighbor_map,
        )
        for var in ["{device_name}", "{device_type}", "{management_ip}", "{neighbors}"]:
            assert var not in prompt, f"変数 '{var}' が未置換です"

    def test_prompt_mentions_show_commands(self):
        """プロンプトに show コマンドの例が含まれること。"""
        from agentic_ni.distributed.prompts import build_device_prompt

        prompt = build_device_prompt("R1", "router", "192.168.0.1")
        assert "show" in prompt.lower()

    def test_prompt_includes_neighbor_info(self):
        """隣接装置情報がプロンプトに正しく埋め込まれること。"""
        from agentic_ni.distributed.prompts import build_device_prompt
        from agentic_ni.distributed.memory import DeviceMemory, NeighborInfo

        mem = DeviceMemory("R1")
        mem.set_neighbor("Agent-R2", NeighborInfo(
            agent_id="Agent-R2", device_name="R2",
            interface="GigabitEthernet0/1", management_ip="192.168.0.2",
        ))
        mem.set_neighbor("Agent-R3", NeighborInfo(
            agent_id="Agent-R3", device_name="R3",
            interface="GigabitEthernet0/2", management_ip="192.168.0.3",
        ))
        prompt = build_device_prompt(
            device_name="R1", device_type="router",
            management_ip="192.168.0.1", neighbors=mem.neighbor_map,
        )
        assert "Agent-R2" in prompt
        assert "Agent-R3" in prompt
        assert "GigabitEthernet0/1" in prompt
