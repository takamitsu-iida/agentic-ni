"""Phase TS-3: Device Tools のユニットテストおよびツール呼び出し統合テスト。

MockDeviceToolkit を使用してオフライン（pyATS/CML 不要）で完全実行できること。
"""

from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import AIMessage, BaseMessage

from agentic_ni.distributed.bus import InMemoryBus
from agentic_ni.distributed.device_agent import DeviceAgent, SyslogEvent
from agentic_ni.distributed.device_tools import (
    MockDeviceToolkit,
    DeviceToolkit,
    create_device_toolkit,
)
from agentic_ni.distributed.memory import DeviceMemory
from agentic_ni.distributed.message import AgentMessage


# ---------------------------------------------------------------------------
# テスト用モック LLM（TS-2 と同様）
# ---------------------------------------------------------------------------

class _SimpleMockLLM:
    """テキスト応答のみを返すシンプルなモック LLM。"""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.call_count = 0

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages: list[BaseMessage]) -> AIMessage:
        idx = min(self.call_count, len(self._responses) - 1)
        self.call_count += 1
        return AIMessage(content=self._responses[idx])


class _ToolCallingMockLLM:
    """ツール呼び出しをシミュレートするモック LLM。

    ``sequence`` の各要素:
      - str       : テキスト応答（最終応答として扱われる）
      - dict      : tool_call を発行する（{"name": ..., "args": ..., "id": ...}）
    """

    def __init__(self, sequence: list[str | dict]) -> None:
        self._sequence = list(sequence)
        self.call_count = 0
        self.received_messages: list[list] = []

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages: list[BaseMessage]) -> AIMessage:
        self.received_messages.append(list(messages))
        item = self._sequence[min(self.call_count, len(self._sequence) - 1)]
        self.call_count += 1

        if isinstance(item, str):
            return AIMessage(content=item)

        return AIMessage(
            content="",
            tool_calls=[{
                "name": item["name"],
                "args": item.get("args", {}),
                "id": item.get("id", f"call_{self.call_count}"),
                "type": "tool_call",
            }],
        )


# ---------------------------------------------------------------------------
# MockDeviceToolkit の基本テスト
# ---------------------------------------------------------------------------

class TestMockDeviceToolkit:
    def test_readonly_returns_four_tools(self):
        tk = MockDeviceToolkit("R1", readonly=True)
        tools = tk.get_tools()
        names = {t.name for t in tools}
        assert names == {"run_show", "get_running_config", "get_interface_status", "get_routing_table"}

    def test_readonly_false_includes_apply_config(self):
        tk = MockDeviceToolkit("R1", readonly=False, human_queue=asyncio.Queue())
        tools = tk.get_tools()
        names = {t.name for t in tools}
        assert "apply_config" in names
        assert len(tools) == 5

    def test_run_show_returns_default_response(self):
        tk = MockDeviceToolkit("R1")
        tool = next(t for t in tk.get_tools() if t.name == "run_show")
        result = tool.invoke({"command": "show ip ospf neighbor"})
        assert "R1" in result
        assert "show ip ospf neighbor" in result

    def test_run_show_returns_custom_response(self):
        tk = MockDeviceToolkit(
            "R1",
            show_responses={"show ip ospf neighbor": "Neighbor 10.0.0.2 is FULL"}
        )
        tool = next(t for t in tk.get_tools() if t.name == "run_show")
        result = tool.invoke({"command": "show ip ospf neighbor"})
        assert result == "Neighbor 10.0.0.2 is FULL"

    def test_get_running_config_returns_mock(self):
        tk = MockDeviceToolkit("R1", running_config="hostname R1\n")
        tool = next(t for t in tk.get_tools() if t.name == "get_running_config")
        result = tool.invoke({})
        assert "hostname R1" in result

    def test_get_interface_status_returns_mock(self):
        tk = MockDeviceToolkit("R1", interface_status="Gi0/0  up  up\n")
        tool = next(t for t in tk.get_tools() if t.name == "get_interface_status")
        result = tool.invoke({})
        assert "Gi0/0" in result

    def test_get_routing_table_returns_mock(self):
        tk = MockDeviceToolkit("R1", routing_table="C  10.0.0.0/24 is directly connected\n")
        tool = next(t for t in tk.get_tools() if t.name == "get_routing_table")
        result = tool.invoke({})
        assert "10.0.0.0" in result

    def test_apply_config_queues_to_human_queue(self):
        human_q: asyncio.Queue = asyncio.Queue()
        tk = MockDeviceToolkit("R1", readonly=False, human_queue=human_q)
        tool = next(t for t in tk.get_tools() if t.name == "apply_config")
        result = tool.invoke({"commands": "router ospf 1\n network 10.0.0.0 0.0.0.255 area 0"})
        assert "キュー" in result
        assert not human_q.empty()
        item = human_q.get_nowait()
        assert item["type"] == "config_change_request"
        assert item["device"] == "R1"
        assert "router ospf 1" in item["commands"]

    def test_apply_config_raises_without_human_queue(self):
        tk = MockDeviceToolkit("R1", readonly=False, human_queue=None)
        tool = next(t for t in tk.get_tools() if t.name == "apply_config")
        with pytest.raises(PermissionError):
            tool.invoke({"commands": "some command"})


# ---------------------------------------------------------------------------
# DeviceToolkit の設定フラグテスト
# ---------------------------------------------------------------------------

class TestDeviceToolkitReadonly:
    def test_default_readonly_excludes_apply_config(self):
        """DEVICE_AGENT_READONLY のデフォルトは True のため apply_config が除外されること。"""
        tk = DeviceToolkit("R1", testbed_yaml=None, readonly=True)
        names = {t.name for t in tk.get_tools()}
        assert "apply_config" not in names

    def test_readonly_false_includes_apply_config(self):
        tk = DeviceToolkit("R1", testbed_yaml=None, readonly=False, human_queue=asyncio.Queue())
        names = {t.name for t in tk.get_tools()}
        assert "apply_config" in names

    def test_run_show_raises_without_testbed(self):
        """testbed_yaml が None の場合 run_show は RuntimeError を返すこと。"""
        tk = DeviceToolkit("R1", testbed_yaml=None, readonly=True)
        tool = next(t for t in tk.get_tools() if t.name == "run_show")
        # invoke は例外を文字列として返さず例外を送出する
        with pytest.raises(RuntimeError, match="testbed_yaml"):
            tool.invoke({"command": "show version"})


# ---------------------------------------------------------------------------
# create_device_toolkit ファクトリーのテスト
# ---------------------------------------------------------------------------

class TestCreateDeviceToolkit:
    def test_mock_factory(self):
        tk = create_device_toolkit("R1", mock=True)
        assert isinstance(tk, MockDeviceToolkit)

    def test_real_factory(self):
        tk = create_device_toolkit("R1", testbed_yaml=None, mock=False)
        assert isinstance(tk, DeviceToolkit)

    def test_mock_factory_with_kwargs(self):
        tk = create_device_toolkit(
            "R1", mock=True,
            show_responses={"show version": "IOS XE version 17.06"},
        )
        assert isinstance(tk, MockDeviceToolkit)
        tool = next(t for t in tk.get_tools() if t.name == "run_show")
        assert "17.06" in tool.invoke({"command": "show version"})


# ---------------------------------------------------------------------------
# DeviceAgent + ツール呼び出しの統合テスト（ReAct ループ）
# ---------------------------------------------------------------------------

class TestDeviceAgentWithTools:
    async def test_tool_call_then_final_response(self):
        """LLM が run_show を呼び出し → 結果を受け取り → COORDINATOR に応答を送ること。"""
        human_q: asyncio.Queue = asyncio.Queue()
        bus = InMemoryBus()
        memory = DeviceMemory("R1")

        toolkit = MockDeviceToolkit(
            "R1",
            show_responses={"show ip ospf neighbor": "Neighbor 10.0.0.2 is FULL/DR"},
        )
        llm = _ToolCallingMockLLM([
            # ターン 1: run_show を呼び出す
            {"name": "run_show", "args": {"command": "show ip ospf neighbor"}, "id": "call_1"},
            # ターン 2: ツール結果を受け取って COORDINATOR に最終報告（Worker モード）
            "TO: COORDINATOR | MSG: OSPF ネイバーは正常 (FULL/DR)。問題は R1 にはありません。",
        ])
        agent = DeviceAgent(
            device_name="R1",
            agent_id="Agent-R1",
            bus=bus,
            memory=memory,
            llm=llm,
            tools=toolkit.get_tools(),
            human_queue=human_q,
        )

        await agent.start()
        await agent.inject_event(SyslogEvent(raw_text="OSPF ネイバー確認依頼"))
        await agent.wait_idle()
        await agent.stop()

        assert llm.call_count == 2
        assert not human_q.empty()
        item = human_q.get_nowait()
        assert "FULL/DR" in item["content"]

    async def test_tool_result_stored_in_memory(self):
        """ツール実行結果がエージェントのメモリに記録されること。"""
        bus = InMemoryBus()
        memory = DeviceMemory("R1")
        toolkit = MockDeviceToolkit(
            "R1",
            show_responses={"show interfaces brief": "Gi0/0 is up"},
        )
        llm = _ToolCallingMockLLM([
            {"name": "get_interface_status", "args": {}, "id": "call_1"},
            "TO: LOG | MSG: インターフェース確認完了",
        ])
        agent = DeviceAgent(
            device_name="R1",
            agent_id="Agent-R1",
            bus=bus,
            memory=memory,
            llm=llm,
            tools=toolkit.get_tools(),
        )

        await agent.start()
        await agent.inject_event(SyslogEvent(raw_text="I/F 状態確認"))
        await agent.wait_idle()
        await agent.stop()

        # "tool" ソースのステータスが memory に記録されていること
        tool_entries = [s for s in memory.status_history if s.source == "tool"]
        assert len(tool_entries) >= 1
        assert "get_interface_status" in tool_entries[0].content

    async def test_unknown_tool_returns_error_message(self):
        """存在しないツール名が LLM から返されても処理が続行されること。"""
        bus = InMemoryBus()
        memory = DeviceMemory("R1")
        toolkit = MockDeviceToolkit("R1")
        llm = _ToolCallingMockLLM([
            {"name": "nonexistent_tool", "args": {}, "id": "call_1"},
            "TO: LOG | MSG: エラーハンドリング確認",
        ])
        agent = DeviceAgent(
            device_name="R1", agent_id="Agent-R1",
            bus=bus, memory=memory,
            llm=llm, tools=toolkit.get_tools(),
        )

        await agent.start()
        await agent.inject_event(SyslogEvent(raw_text="テスト"))
        await agent.wait_idle()
        await agent.stop()

        # ToolMessage に「見つかりません」が含まれていること
        assert llm.call_count == 2
        last_messages = llm.received_messages[-1]
        from langchain_core.messages import ToolMessage
        tool_msgs = [m for m in last_messages if isinstance(m, ToolMessage)]
        assert any("見つかりません" in m.content for m in tool_msgs)

    async def test_readonly_agent_has_no_apply_config(self):
        """readonly=True のツールキットでは apply_config がツールに含まれないこと。"""
        bus = InMemoryBus()
        memory = DeviceMemory("R1")
        toolkit = MockDeviceToolkit("R1", readonly=True)
        tools = toolkit.get_tools()
        assert not any(t.name == "apply_config" for t in tools)
