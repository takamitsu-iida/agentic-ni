"""Phase TS-2: DeviceAgent コア実装のユニットテスト。

LLM は MockLLM で差し替え、外部 API なしで完全実行できること。
"""

from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import AIMessage, BaseMessage

from agentic_ni.distributed.bus import InMemoryBus
from agentic_ni.distributed.device_agent import (
    BusMessageEvent,
    DeviceAgent,
    PollEvent,
    SyslogEvent,
    _event_source,
    _event_summary,
    parse_agent_output,
)
from agentic_ni.distributed.memory import DeviceMemory, NeighborInfo
from agentic_ni.distributed.message import AgentMessage
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

    def test_bus_event_summary(self):
        msg = AgentMessage(
            from_agent="Agent-R2", to_agent="Agent-R1",
            msg_type="query", content="BGP セッション断",
        )
        ev = BusMessageEvent(message=msg)
        assert "Agent-R2" in _event_summary(ev)

    def test_poll_event_summary(self):
        ev = PollEvent(source="run_show", content="show ip ospf neighbor\n...")
        assert "POLL" in _event_summary(ev)

    def test_event_source_tags(self):
        assert _event_source(SyslogEvent(raw_text="x")) == "syslog"
        assert _event_source(BusMessageEvent(
            message=AgentMessage(from_agent="A", to_agent="B", msg_type="alert", content="x")
        )) == "bus"
        assert _event_source(PollEvent(source="show", content="x")) == "poll"


# ---------------------------------------------------------------------------
# DeviceAgent の統合テスト（MockLLM 使用）
# ---------------------------------------------------------------------------

class TestDeviceAgentIntegration:
    async def test_syslog_triggers_llm_and_routes_to_bus(self):
        """syslog イベントが LLM を起動し、バスへメッセージを Publish すること。"""
        agent, bus, llm = _make_agent(
            ["TO: Agent-R2 | MSG: インターフェースダウンを検知。状態を確認してください。"]
        )
        received: list[AgentMessage] = []

        async def capture(topic, msg):
            received.append(msg)

        await bus.subscribe("network/agents/Agent-R2/direct", capture)
        await agent.start()

        await agent.inject_event(SyslogEvent(
            raw_text="%LINK-3-UPDOWN: GigabitEthernet0/0, changed state to down",
            severity="3",
        ))
        await agent.wait_idle()
        await agent.stop()

        assert llm.call_count == 1
        assert len(received) == 1
        assert received[0].from_agent == "Agent-R1"
        assert received[0].to_agent == "Agent-R2"

    async def test_human_escalation_goes_to_queue(self):
        """HUMAN 宛メッセージが human_queue に積まれること。"""
        human_q: asyncio.Queue = asyncio.Queue()
        agent, bus, llm = _make_agent(
            ["TO: HUMAN | MSG: 設定変更が必要です。承認をお願いします。"],
            human_queue=human_q,
        )
        await agent.start()
        await agent.inject_event(SyslogEvent(raw_text="重大障害"))
        await agent.wait_idle()
        await agent.stop()

        assert not human_q.empty()
        item = human_q.get_nowait()
        assert item["from_agent"] == "Agent-R1"
        assert "設定変更" in item["content"]

    async def test_broadcast_goes_to_chat_topic(self):
        """ALL 宛メッセージが network/agents/chat トピックに Publish されること。"""
        agent, bus, llm = _make_agent(
            ["TO: ALL | MSG: リンク断を検知しました。"]
        )
        received: list[tuple[str, AgentMessage]] = []

        async def capture(topic, msg):
            received.append((topic, msg))

        await bus.subscribe("network/agents/chat", capture)
        await agent.start()
        await agent.inject_event(SyslogEvent(raw_text="リンク断"))
        await agent.wait_idle()
        await agent.stop()

        # 自分の送信は _on_bus_message でスキップされるので received に来る
        assert any(t == "network/agents/chat" for t, _ in received)

    async def test_log_target_updates_memory(self):
        """LOG 宛メッセージがバスに送信されずメモリに記録されること。"""
        agent, bus, llm = _make_agent(
            ["TO: LOG | MSG: インターフェース状態: UP"]
        )
        published: list[AgentMessage] = []

        async def capture(topic, msg):
            published.append(msg)

        await bus.subscribe("network/agents/#", capture)
        await agent.start()
        await agent.inject_event(SyslogEvent(raw_text="テスト"))
        await agent.wait_idle()
        await agent.stop()

        assert len(published) == 0
        # メモリのステータス履歴に "llm" ソースのエントリが記録されていること
        llm_entries = [s for s in agent._memory.status_history if s.source == "llm"]
        assert len(llm_entries) == 1

    async def test_bus_message_triggers_processing(self):
        """バス経由のメッセージを受信してイベントとして処理すること。"""
        agent, bus, llm = _make_agent(
            ["TO: Agent-SW1 | MSG: 調査結果を報告します。"]
        )
        received: list[AgentMessage] = []

        async def capture(topic, msg):
            received.append(msg)

        await bus.subscribe("network/agents/Agent-SW1/direct", capture)
        await agent.start()

        # 別エージェントからのメッセージをバスに Publish する
        incoming = AgentMessage(
            from_agent="Agent-SW1",
            to_agent="Agent-R1",
            msg_type="query",
            content="ポート状態を教えてください",
        )
        await bus.publish(f"network/agents/{agent.agent_id}/direct", incoming)
        await asyncio.sleep(0.05)  # バスのハンドラー実行を待つ
        await agent.wait_idle()
        await agent.stop()

        assert llm.call_count == 1
        assert len(received) == 1

    async def test_self_message_is_ignored(self):
        """自分が送ったバスメッセージは LLM を起動しないこと。"""
        agent, bus, llm = _make_agent(["TO: LOG | MSG: テスト"])
        await agent.start()

        # 自分の agent_id から送ったメッセージ
        self_msg = AgentMessage(
            from_agent="Agent-R1",  # 自分と同じ ID
            to_agent="ALL",
            msg_type="alert",
            content="自己送信テスト",
        )
        await bus.publish("network/agents/chat", self_msg)
        await asyncio.sleep(0.05)
        await agent.stop()

        assert llm.call_count == 0

    async def test_fallback_response_sent_to_human(self):
        """フォーマットなし LLM 出力は HUMAN に送られること。"""
        human_q: asyncio.Queue = asyncio.Queue()
        agent, bus, llm = _make_agent(
            ["フォーマットなしの応答テキストです。"],
            human_queue=human_q,
        )
        await agent.start()
        await agent.inject_event(SyslogEvent(raw_text="テスト"))
        await agent.wait_idle()
        await agent.stop()

        assert not human_q.empty()
