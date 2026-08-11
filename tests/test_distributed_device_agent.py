"""Phase TS-2: DeviceAgent コア実装のユニットテスト。

LLM は MockLLM で差し替え、外部 API なしで完全実行できること。
"""

from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import AIMessage, BaseMessage

from agentic_ni.distributed.bus import InMemoryBus
from agentic_ni.distributed.device_agent import (
    DeviceAgent,
    PollEvent,
    SyslogEvent,
    _event_source,
    _event_summary,
    parse_agent_output,
)
from agentic_ni.distributed.incident import DeviceQueryRequest, DeviceQueryResponse
from agentic_ni.distributed.memory import DeviceMemory, NeighborInfo
from agentic_ni.distributed.prompts import build_device_prompt


# ---------------------------------------------------------------------------
# テスト用 Mock LLM
# ---------------------------------------------------------------------------

class _MockLLM:
    """LLM を差し替えるシンプルなモック。"""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.call_count = 0
        self.received_messages: list[list[BaseMessage]] = []

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages: list[BaseMessage]) -> AIMessage:
        self.received_messages.append(messages)
        idx = min(self.call_count, len(self._responses) - 1)
        self.call_count += 1
        return AIMessage(content=self._responses[idx])


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _make_agent(
    responses: list[str],
    agent_id: str = "Agent-R1",
    human_queue: asyncio.Queue | None = None,
) -> tuple[DeviceAgent, InMemoryBus, _MockLLM]:
    bus = InMemoryBus()
    memory = DeviceMemory(device_name="R1")
    llm = _MockLLM(responses)
    agent = DeviceAgent(
        device_name="R1",
        agent_id=agent_id,
        bus=bus,
        memory=memory,
        device_type="router",
        management_ip="192.168.0.1",
        llm=llm,
        human_queue=human_queue,
    )
    return agent, bus, llm


# ---------------------------------------------------------------------------
# parse_agent_output のユニットテスト
# ---------------------------------------------------------------------------

class TestParseAgentOutput:
    def test_single_target(self):
        result = parse_agent_output("TO: Agent-R2 | MSG: OSPF の状態を確認してください")
        assert result == [("Agent-R2", "OSPF の状態を確認してください")]

    def test_multiple_targets(self):
        text = "TO: Agent-R2 | MSG: 調査依頼\nTO: HUMAN | MSG: 重大障害の可能性"
        result = parse_agent_output(text)
        assert len(result) == 2
        assert result[0] == ("Agent-R2", "調査依頼")
        assert result[1] == ("HUMAN", "重大障害の可能性")

    def test_case_insensitive(self):
        result = parse_agent_output("to: ALL | msg: ブロードキャスト")
        assert result[0][0] == "ALL"

    def test_fallback_to_human_when_no_match(self):
        result = parse_agent_output("フォーマットなし自由テキスト")
        assert result == [("HUMAN", "フォーマットなし自由テキスト")]

    def test_empty_string_fallback(self):
        result = parse_agent_output("")
        assert result == [("HUMAN", "")]

    def test_ignores_non_matching_lines(self):
        text = "調査中...\nTO: Agent-R2 | MSG: 結果報告\n完了"
        result = parse_agent_output(text)
        # プリアンブル「調査中...」は無視、「完了」は継続行として連結される
        assert result == [("Agent-R2", "結果報告\n完了")]


# ---------------------------------------------------------------------------
# DeviceMemory のテスト
# ---------------------------------------------------------------------------

class TestDeviceMemory:
    def test_add_and_summarize_status(self):
        mem = DeviceMemory("R1")
        mem.add_status("syslog", "インターフェースダウン")
        mem.add_status("poll", "OSPF ネイバー消失")
        summary = mem.recent_status_summary()
        assert "インターフェースダウン" in summary
        assert "OSPF ネイバー消失" in summary

    def test_status_history_capped(self):
        from agentic_ni.distributed.memory import _STATUS_HISTORY_MAX
        mem = DeviceMemory("R1")
        for i in range(_STATUS_HISTORY_MAX + 10):
            mem.add_status("poll", f"entry {i}")
        assert len(mem.status_history) == _STATUS_HISTORY_MAX

    def test_neighbor_map(self):
        mem = DeviceMemory("R1")
        mem.set_neighbor("Agent-R2", NeighborInfo(
            agent_id="Agent-R2",
            device_name="R2",
            interface="GigabitEthernet0/1",
            management_ip="192.168.0.2",
        ))
        summary = mem.neighbors_summary()
        assert "R2" in summary
        assert "Agent-R2" in summary

    def test_incident_lifecycle(self):
        mem = DeviceMemory("R1")
        inc = mem.open_incident("R1-R2 リンクダウン")
        assert inc.status == "open"
        assert len(mem.get_open_incidents()) == 1

        mem.update_incident_status(inc.incident_id, "resolved")
        assert len(mem.get_open_incidents()) == 0


# ---------------------------------------------------------------------------
# プロンプトビルダーのテスト
# ---------------------------------------------------------------------------

class TestBuildDevicePrompt:
    def test_variables_substituted(self):
        mem = DeviceMemory("R1")
        mem.set_neighbor("Agent-R2", NeighborInfo(
            agent_id="Agent-R2", device_name="R2",
            interface="Gi0/1", management_ip="192.168.0.2",
        ))
        prompt = build_device_prompt(
            device_name="R1",
            device_type="router",
            management_ip="192.168.0.1",
            neighbors=mem.neighbor_map,
        )
        assert "R1" in prompt
        assert "router" in prompt
        assert "192.168.0.1" in prompt
        assert "Agent-R2" in prompt

    def test_no_neighbors(self):
        prompt = build_device_prompt("SW1", "switch", "10.0.0.1")
        assert "隣接装置なし" in prompt

    def test_no_placeholders_remain(self):
        """テンプレート変数が未置換のまま残らないこと。"""
        prompt = build_device_prompt("R1", "router", "192.168.0.1")
        assert "{device_name}" not in prompt
        assert "{device_type}" not in prompt
        assert "{management_ip}" not in prompt
        assert "{neighbors}" not in prompt


# ---------------------------------------------------------------------------
# イベントユーティリティのテスト
# ---------------------------------------------------------------------------

class TestEventHelpers:
    def test_syslog_event_summary(self):
        ev = SyslogEvent(raw_text="%LINK-3-UPDOWN: GigabitEthernet0/0, changed state to down")
        assert "SYSLOG" in _event_summary(ev)

    def test_poll_event_summary(self):
        ev = PollEvent(source="run_show", content="show ip ospf neighbor\n...")
        assert "POLL" in _event_summary(ev)

    def test_event_source_tags(self):
        assert _event_source(SyslogEvent(raw_text="x")) == "syslog"
        assert _event_source(PollEvent(source="show", content="x")) == "poll"


# ---------------------------------------------------------------------------
# DeviceAgent.execute_query のテスト
# ---------------------------------------------------------------------------

class TestExecuteQuery:
    async def test_execute_query_returns_response(self):
        """execute_query が DeviceQueryResponse を返すこと。"""
        agent, bus, llm = _make_agent(
            ["TO: COORDINATOR | MSG: Gi0/2 は down 状態です。BGP ネイバー消失を確認しました。"]
        )
        await agent.start()
        req = DeviceQueryRequest(
            incident_id="test-incident-id",
            target_device="R1",
            symptom_summary="Spine1-Leaf3 間リンクダウン症状。Gi0/2 確認を依頼。",
        )
        resp = await agent.execute_query(req)
        await agent.stop()

        assert isinstance(resp, DeviceQueryResponse)
        assert resp.incident_id == "test-incident-id"
        assert resp.from_device == "R1"
        assert "down" in resp.findings
        assert not resp.error

    async def test_execute_query_with_tool_calls(self):
        """execute_query で LLM がツール呼び出しをした後に結果を返すこと。"""
        tool_call_item = {"name": "run_show", "args": {"command": "show ip interface brief"}, "id": "c1", "type": "tool_call"}

        class _ToolLLM:
            def __init__(self):
                self._step = 0
            def bind_tools(self, tools):
                return self
            async def ainvoke(self, messages):
                from langchain_core.messages import AIMessage
                if self._step == 0:
                    self._step += 1
                    return AIMessage(content="", tool_calls=[tool_call_item])
                return AIMessage(content="TO: COORDINATOR | MSG: インターフェースダウンを確認しました")

        from langchain_core.tools import StructuredTool
        from pydantic import BaseModel, Field as PydanticField

        class _ShowInput(BaseModel):
            command: str = PydanticField(default="")

        mock_tool = StructuredTool(
            name="run_show",
            description="show command",
            args_schema=_ShowInput,
            func=lambda command="": "Interface GigabitEthernet0/2 is down",
        )

        bus = InMemoryBus()
        memory = DeviceMemory(device_name="R1")
        agent = DeviceAgent(
            device_name="R1", agent_id="Agent-R1",
            bus=bus, memory=memory,
            llm=_ToolLLM(), tools=[mock_tool],
        )
        await agent.start()
        req = DeviceQueryRequest(
            incident_id="t2", target_device="R1",
            symptom_summary="調査依頼",
        )
        resp = await agent.execute_query(req)
        await agent.stop()

        assert not resp.error
        assert "show ip interface brief" in resp.show_outputs
        assert "インターフェースダウン" in resp.findings

    async def test_execute_query_llm_error_returns_error_response(self):
        """LLM エラー時に error=True のレスポンスを返すこと。"""
        class _ErrorLLM:
            def bind_tools(self, tools): return self
            async def ainvoke(self, messages):
                raise RuntimeError("モックエラー")

        bus = InMemoryBus()
        memory = DeviceMemory(device_name="R1")
        agent = DeviceAgent(
            device_name="R1", agent_id="Agent-R1",
            bus=bus, memory=memory, llm=_ErrorLLM(),
        )
        await agent.start()
        req = DeviceQueryRequest(
            incident_id="t3", target_device="R1", symptom_summary="テスト",
        )
        resp = await agent.execute_query(req)
        await agent.stop()

        assert resp.error
        assert "モックエラー" in resp.error_detail


# ---------------------------------------------------------------------------
# 溌: 旧 inject_event テスト（PollEvent のみ、排他核し）
# ---------------------------------------------------------------------------

class TestInjectPollEvent:
    async def test_poll_event_triggers_processing(self):
        """PollEvent が inject_event でキューに積まれること。"""
        human_q: asyncio.Queue = asyncio.Queue()
        agent, bus, llm = _make_agent(
            ["TO: HUMAN | MSG: ポーリング結果受存"],
            human_queue=human_q,
        )
        await agent.start()
        await agent.inject_event(PollEvent(source="run_show", content="show ip route\n..."))
        await agent.wait_idle()
        await agent.stop()

        assert not human_q.empty()
