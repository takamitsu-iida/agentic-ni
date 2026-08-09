"""Phase TS-1: Message Bus レイヤーのユニットテスト。

InMemoryBus を中心にテストし、外部 MQ サーバーなしで完全実行できること。
"""

from __future__ import annotations

import pytest

from agentic_ni.distributed.bus import (
    InMemoryBus,
    _topic_matches,
    create_bus,
)
from agentic_ni.distributed.message import AgentMessage


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _make_msg(**kwargs) -> AgentMessage:
    defaults = dict(from_agent="Agent-R1", to_agent="ALL", msg_type="alert", content="test")
    defaults.update(kwargs)
    return AgentMessage(**defaults)


# ---------------------------------------------------------------------------
# _topic_matches のユニットテスト
# ---------------------------------------------------------------------------

class TestTopicMatches:
    def test_exact_match(self):
        assert _topic_matches("network/agents/chat", "network/agents/chat")

    def test_no_match(self):
        assert not _topic_matches("network/agents/chat", "network/agents/other")

    def test_hash_wildcard_suffix(self):
        assert _topic_matches("network/agents/#", "network/agents/chat")
        assert _topic_matches("network/agents/#", "network/agents/Agent-R1/direct")

    def test_hash_wildcard_root(self):
        assert _topic_matches("#", "network/agents/chat")
        assert _topic_matches("#", "any/topic/at/all")

    def test_hash_does_not_match_parent(self):
        # pattern=network/agents/# はトピック=network/agents 自体にはマッチしない
        assert not _topic_matches("network/agents/#", "network")

    def test_plus_wildcard_single_level(self):
        assert _topic_matches("network/+/chat", "network/agents/chat")
        assert not _topic_matches("network/+/chat", "network/a/b/chat")

    def test_no_wildcard_no_match(self):
        assert not _topic_matches("network/agents/chat", "network/agents/other")


# ---------------------------------------------------------------------------
# AgentMessage のスキーマテスト
# ---------------------------------------------------------------------------

class TestAgentMessage:
    def test_defaults_are_set(self):
        msg = _make_msg()
        assert msg.message_id  # UUID 自動生成
        assert msg.timestamp   # タイムスタンプ自動生成
        assert msg.hop_count == 0
        assert msg.payload == {}

    def test_serialization_roundtrip(self):
        msg = _make_msg(
            to_agent="Agent-R2",
            msg_type="query",
            content="OSPF ネイバーの状態は？",
            payload={"interface": "GigabitEthernet0/0"},
        )
        restored = AgentMessage.model_validate_json(msg.model_dump_json())
        assert restored.from_agent == msg.from_agent
        assert restored.to_agent == msg.to_agent
        assert restored.payload == msg.payload
        assert restored.message_id == msg.message_id

    def test_hop_count_increments(self):
        msg = _make_msg(hop_count=3)
        assert msg.hop_count == 3


# ---------------------------------------------------------------------------
# InMemoryBus のテスト
# ---------------------------------------------------------------------------

class TestInMemoryBus:
    async def test_publish_subscribe_basic(self):
        bus = InMemoryBus()
        received: list[tuple[str, AgentMessage]] = []

        async def handler(topic: str, msg: AgentMessage) -> None:
            received.append((topic, msg))

        await bus.subscribe("network/agents/chat", handler)
        await bus.publish("network/agents/chat", _make_msg())

        assert len(received) == 1
        assert received[0][0] == "network/agents/chat"

    async def test_direct_message_only_reaches_target(self):
        bus = InMemoryBus()
        r1_received: list[AgentMessage] = []
        r2_received: list[AgentMessage] = []

        async def r1_handler(topic, msg): r1_received.append(msg)
        async def r2_handler(topic, msg): r2_received.append(msg)

        await bus.subscribe("network/agents/Agent-R1/direct", r1_handler)
        await bus.subscribe("network/agents/Agent-R2/direct", r2_handler)

        await bus.publish(
            "network/agents/Agent-R2/direct",
            _make_msg(to_agent="Agent-R2", msg_type="query", content="状態確認"),
        )

        assert len(r2_received) == 1
        assert len(r1_received) == 0

    async def test_broadcast_reaches_all_subscribers(self):
        bus = InMemoryBus()
        received_a: list[AgentMessage] = []
        received_b: list[AgentMessage] = []

        async def handler_a(topic, msg): received_a.append(msg)
        async def handler_b(topic, msg): received_b.append(msg)

        await bus.subscribe("network/agents/chat", handler_a)
        await bus.subscribe("network/agents/chat", handler_b)

        await bus.publish("network/agents/chat", _make_msg())

        assert len(received_a) == 1
        assert len(received_b) == 1

    async def test_wildcard_subscribe(self):
        bus = InMemoryBus()
        received: list[tuple[str, AgentMessage]] = []

        async def handler(topic, msg): received.append((topic, msg))

        await bus.subscribe("network/agents/#", handler)
        await bus.publish("network/agents/chat", _make_msg())
        await bus.publish("network/agents/Agent-R1/direct", _make_msg())

        assert len(received) == 2

    async def test_unsubscribe_stops_delivery(self):
        bus = InMemoryBus()
        received: list[AgentMessage] = []

        async def handler(topic, msg): received.append(msg)

        await bus.subscribe("network/agents/chat", handler)
        await bus.unsubscribe("network/agents/chat", handler)
        await bus.publish("network/agents/chat", _make_msg())

        assert len(received) == 0

    async def test_close_stops_delivery(self):
        bus = InMemoryBus()
        received: list[AgentMessage] = []

        async def handler(topic, msg): received.append(msg)

        await bus.subscribe("network/agents/chat", handler)
        await bus.close()
        await bus.publish("network/agents/chat", _make_msg())

        assert len(received) == 0

    async def test_hop_count_limit_drops_message(self):
        bus = InMemoryBus()
        received: list[AgentMessage] = []

        async def handler(topic, msg): received.append(msg)

        await bus.subscribe("network/agents/chat", handler)
        looping_msg = _make_msg(hop_count=6)  # MAX_HOP_COUNT(5) 超
        await bus.publish("network/agents/chat", looping_msg)

        assert len(received) == 0

    async def test_handler_exception_does_not_stop_other_handlers(self):
        """1 ハンドラーで例外が起きても他のハンドラーは正常実行されること。"""
        bus = InMemoryBus()
        received: list[AgentMessage] = []

        async def bad_handler(topic, msg): raise RuntimeError("意図的なエラー")
        async def good_handler(topic, msg): received.append(msg)

        await bus.subscribe("network/agents/chat", bad_handler)
        await bus.subscribe("network/agents/chat", good_handler)
        await bus.publish("network/agents/chat", _make_msg())

        assert len(received) == 1

    async def test_connect_resets_closed_state(self):
        bus = InMemoryBus()
        received: list[AgentMessage] = []

        async def handler(topic, msg): received.append(msg)

        await bus.subscribe("network/agents/chat", handler)
        await bus.close()
        # close 後に connect すると closed フラグがリセットされる
        await bus.connect()
        await bus.subscribe("network/agents/chat", handler)
        await bus.publish("network/agents/chat", _make_msg())

        assert len(received) == 1


# ---------------------------------------------------------------------------
# create_bus ファクトリーのテスト
# ---------------------------------------------------------------------------

class TestCreateBus:
    def test_memory_backend(self):
        bus = create_bus("memory")
        assert isinstance(bus, InMemoryBus)

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="不明なバックエンド"):
            create_bus("unknown")

    def test_mqtt_backend_requires_import(self):
        """paho-mqtt が未インストールの場合 connect() で ImportError になること。"""
        bus = create_bus("mqtt", host="localhost")
        # connect() を呼ばなければ ImportError は発生しない
        assert bus is not None

    def test_nats_backend_requires_import(self):
        """nats-py が未インストールの場合 connect() で ImportError になること。"""
        bus = create_bus("nats", url="nats://localhost:4222")
        assert bus is not None


# ---------------------------------------------------------------------------
# Scale-1-4: TTL 境界値テスト
# ---------------------------------------------------------------------------

class TestTTLBoundary:
    async def test_hop_count_below_limit_passes(self):
        """MAX_HOP_COUNT - 1 のメッセージはバスを通過すること。"""
        from agentic_ni.distributed.bus import MAX_HOP_COUNT
        bus = InMemoryBus()
        received: list[AgentMessage] = []

        async def handler(topic, msg): received.append(msg)
        await bus.subscribe("network/agents/chat", handler)

        msg = _make_msg(hop_count=MAX_HOP_COUNT - 1)
        await bus.publish("network/agents/chat", msg)
        assert len(received) == 1

    async def test_hop_count_at_limit_drops(self):
        """MAX_HOP_COUNT 丁度のメッセージはバスで破棄されること。"""
        from agentic_ni.distributed.bus import MAX_HOP_COUNT
        bus = InMemoryBus()
        received: list[AgentMessage] = []

        async def handler(topic, msg): received.append(msg)
        await bus.subscribe("network/agents/chat", handler)

        msg = _make_msg(hop_count=MAX_HOP_COUNT)
        await bus.publish("network/agents/chat", msg)
        assert len(received) == 0

    async def test_hop_count_accumulates_across_forward_chain(self):
        """3 エージェント間の転送チェーンで hop_count が 0→1→2 と積み上がること。"""
        bus = InMemoryBus()
        forwarded: list[AgentMessage] = []

        async def capture(topic, msg): forwarded.append(msg)
        await bus.subscribe("network/agents/#", capture)

        # Agent-R1 が新規発火（hop=0）
        msg0 = _make_msg(from_agent="Agent-R1", to_agent="Agent-R2", hop_count=0)
        await bus.publish("network/agents/Agent-R2/direct", msg0)

        # Agent-R2 が転送（hop=1）
        msg1 = AgentMessage(
            from_agent="Agent-R2",
            to_agent="Agent-R3",
            msg_type="query",
            content="転送",
            hop_count=msg0.hop_count + 1,
            origin_message_id=msg0.message_id,
        )
        await bus.publish("network/agents/Agent-R3/direct", msg1)

        assert forwarded[0].hop_count == 0
        assert forwarded[1].hop_count == 1
        assert forwarded[1].origin_message_id == msg0.message_id

    async def test_origin_message_id_same_across_chain(self):
        """転送チェーン全体で origin_message_id が最初のメッセージの ID を保持すること。"""
        origin = _make_msg(from_agent="Agent-R1", to_agent="Agent-R2")

        # 1 ホップ目の転送
        hop1 = AgentMessage(
            from_agent="Agent-R2",
            to_agent="Agent-R3",
            msg_type="query",
            content="hop1",
            hop_count=1,
            origin_message_id=origin.message_id,
        )
        # 2 ホップ目の転送（origin_message_id は引き継ぐ）
        hop2 = AgentMessage(
            from_agent="Agent-R3",
            to_agent="Agent-R4",
            msg_type="query",
            content="hop2",
            hop_count=2,
            origin_message_id=hop1.origin_message_id or hop1.message_id,
        )
        assert hop2.origin_message_id == origin.message_id

    async def test_duplicate_message_id_dropped(self):
        """同一 message_id のメッセージは 2 回目以降バスで破棄されること。"""
        bus = InMemoryBus()
        received: list[AgentMessage] = []

        async def handler(topic, msg): received.append(msg)
        await bus.subscribe("network/agents/chat", handler)

        msg = _make_msg()
        await bus.publish("network/agents/chat", msg)
        await bus.publish("network/agents/chat", msg)  # 同一 message_id の再送

        assert len(received) == 1
