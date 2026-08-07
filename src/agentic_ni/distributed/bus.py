"""Message Bus 抽象化レイヤー。

バックエンド:
  - InMemoryBus : テスト・開発用（外部依存なし）
  - MQTTBus     : paho-mqtt>=2.0 が必要（uv sync --extra distributed）
  - NATSBus     : nats-py>=2.3 が必要（uv sync --extra distributed）

使用方法::

    bus = create_bus("memory")
    await bus.connect()

    async def on_message(topic: str, msg: AgentMessage) -> None:
        print(f"[{topic}] {msg.from_agent}: {msg.content}")

    await bus.subscribe("network/agents/chat", on_message)
    await bus.publish("network/agents/chat", AgentMessage(...))
    await bus.close()

トピック命名規則:
  - ``network/agents/chat``              全員参加のブロードキャスト
  - ``network/agents/{agent_id}/direct`` 1対1 ダイレクトメッセージ

ワイルドカード（InMemoryBus / MQTTBus のみ）:
  - ``#`` : 残りの全レベルにマッチ（例: ``network/agents/#``）
  - ``+`` : 1レベルにマッチ（例: ``network/agents/+/direct``）
"""

from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Awaitable, Callable

from agentic_ni.distributed.message import AgentMessage

logger = logging.getLogger(__name__)

# ハンドラーの型エイリアス
AsyncHandlerFunc = Callable[[str, AgentMessage], Awaitable[None]]

# メッセージループ検知の上限ホップ数
MAX_HOP_COUNT = 5


# ---------------------------------------------------------------------------
# MQTT スタイルのワイルドカードマッチング
# ---------------------------------------------------------------------------

def _topic_matches(pattern: str, topic: str) -> bool:
    """MQTT スタイルのワイルドカードでパターンとトピックを照合する。

    ``#`` は残りの全レベル、``+`` は1レベルにマッチする。
    """
    if pattern == topic:
        return True
    if "#" not in pattern and "+" not in pattern:
        return False

    p_parts = pattern.split("/")
    t_parts = topic.split("/")

    for i, seg in enumerate(p_parts):
        if seg == "#":
            return True  # 残りのすべてにマッチ
        if i >= len(t_parts):
            return False
        if seg != "+" and seg != t_parts[i]:
            return False

    return len(p_parts) == len(t_parts)


# ---------------------------------------------------------------------------
# 抽象基底クラス
# ---------------------------------------------------------------------------

class MessageBus(ABC):
    """Message Bus の抽象インターフェース。"""

    @abstractmethod
    async def connect(self) -> None:
        """バスへ接続する。"""

    @abstractmethod
    async def publish(self, topic: str, message: AgentMessage) -> None:
        """指定トピックへメッセージを送信する。"""

    @abstractmethod
    async def subscribe(self, topic: str, handler: AsyncHandlerFunc) -> None:
        """指定トピックのメッセージを受信するハンドラーを登録する。"""

    @abstractmethod
    async def unsubscribe(self, topic: str, handler: AsyncHandlerFunc) -> None:
        """ハンドラーの登録を解除する。"""

    @abstractmethod
    async def close(self) -> None:
        """バスとの接続を閉じる。"""


# ---------------------------------------------------------------------------
# InMemoryBus — テスト・開発用
# ---------------------------------------------------------------------------

class InMemoryBus(MessageBus):
    """外部依存なしのインプロセス Pub/Sub バス。

    テストおよびローカル開発に使用する。MQTT スタイルのワイルドカードをサポート。
    """

    def __init__(self) -> None:
        # pattern -> list[handler]
        self._handlers: dict[str, list[AsyncHandlerFunc]] = defaultdict(list)
        self._closed = False

    async def connect(self) -> None:
        self._closed = False

    async def publish(self, topic: str, message: AgentMessage) -> None:
        if self._closed:
            return
        if message.hop_count > MAX_HOP_COUNT:
            logger.warning(
                "メッセージのホップ数が上限（%d）を超えました。破棄します: %s",
                MAX_HOP_COUNT,
                message.message_id,
            )
            return

        for pattern, handlers in list(self._handlers.items()):
            if _topic_matches(pattern, topic):
                for handler in list(handlers):
                    try:
                        await handler(topic, message)
                    except Exception:
                        logger.exception(
                            "ハンドラー呼び出しでエラーが発生しました: topic=%s", topic
                        )

    async def subscribe(self, topic: str, handler: AsyncHandlerFunc) -> None:
        self._handlers[topic].append(handler)

    async def unsubscribe(self, topic: str, handler: AsyncHandlerFunc) -> None:
        handlers = self._handlers.get(topic, [])
        if handler in handlers:
            handlers.remove(handler)

    async def close(self) -> None:
        self._closed = True
        self._handlers.clear()


# ---------------------------------------------------------------------------
# MQTTBus — paho-mqtt>=2.0 が必要
# ---------------------------------------------------------------------------

class MQTTBus(MessageBus):
    """paho-mqtt を使った MQTT ブローカー接続バス。

    ``uv sync --extra distributed`` で paho-mqtt をインストールすること。
    """

    def __init__(self, host: str, port: int = 1883, client_id: str = "") -> None:
        self._host = host
        self._port = port
        self._client_id = client_id
        self._handlers: dict[str, list[AsyncHandlerFunc]] = defaultdict(list)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client = None  # paho.mqtt.client.Client（遅延初期化）

    async def connect(self) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:
            raise ImportError(
                "paho-mqtt が見つかりません。`uv sync --extra distributed` を実行してください。"
            ) from exc

        self._loop = asyncio.get_running_loop()
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=self._client_id or None,
        )
        self._client.on_message = self._on_message
        self._client.on_connect = self._on_connect
        await asyncio.to_thread(self._client.connect, self._host, self._port)
        self._client.loop_start()
        logger.info("MQTTBus: %s:%d に接続しました。", self._host, self._port)

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        # 再接続時に既存のサブスクリプションを復元する
        for topic in self._handlers:
            client.subscribe(topic)

    def _on_message(self, client, userdata, msg) -> None:
        if self._loop is None:
            return
        try:
            data = json.loads(msg.payload.decode())
            message = AgentMessage.model_validate(data)
        except Exception:
            logger.exception("MQTT メッセージのデシリアライズに失敗しました。")
            return
        asyncio.run_coroutine_threadsafe(
            self._dispatch(msg.topic, message), self._loop
        )

    async def _dispatch(self, topic: str, message: AgentMessage) -> None:
        for pattern, handlers in list(self._handlers.items()):
            if _topic_matches(pattern, topic):
                for handler in list(handlers):
                    try:
                        await handler(topic, message)
                    except Exception:
                        logger.exception(
                            "ハンドラー呼び出しでエラーが発生しました: topic=%s", topic
                        )

    async def publish(self, topic: str, message: AgentMessage) -> None:
        if self._client is None:
            raise RuntimeError("connect() を先に呼び出してください。")
        payload = message.model_dump_json()
        await asyncio.to_thread(self._client.publish, topic, payload)

    async def subscribe(self, topic: str, handler: AsyncHandlerFunc) -> None:
        self._handlers[topic].append(handler)
        if self._client and self._client.is_connected():
            self._client.subscribe(topic)

    async def unsubscribe(self, topic: str, handler: AsyncHandlerFunc) -> None:
        handlers = self._handlers.get(topic, [])
        if handler in handlers:
            handlers.remove(handler)
        if not handlers and self._client:
            self._client.unsubscribe(topic)

    async def close(self) -> None:
        if self._client:
            self._client.loop_stop()
            await asyncio.to_thread(self._client.disconnect)
        self._handlers.clear()
        logger.info("MQTTBus: 切断しました。")


# ---------------------------------------------------------------------------
# NATSBus — nats-py>=2.3 が必要
# ---------------------------------------------------------------------------

class NATSBus(MessageBus):
    """nats-py を使った NATS サーバー接続バス。

    ``uv sync --extra distributed`` で nats-py をインストールすること。
    NATS のサブジェクトは「.」区切りのため、トピックの「/」を自動変換する。
    """

    def __init__(self, url: str = "nats://localhost:4222") -> None:
        self._url = url
        self._nc = None  # nats.aio.client.Client（遅延初期化）
        self._subscriptions: list = []
        # トピック→ハンドラーのマッピングを保持（unsubscribe 用）
        self._handlers: dict[str, list[AsyncHandlerFunc]] = defaultdict(list)

    @staticmethod
    def _to_nats_subject(topic: str) -> str:
        """MQTT トピック形式を NATS サブジェクト形式に変換する。"""
        return topic.replace("/", ".").replace("#", ">").replace("+", "*")

    async def connect(self) -> None:
        try:
            import nats
        except ImportError as exc:
            raise ImportError(
                "nats-py が見つかりません。`uv sync --extra distributed` を実行してください。"
            ) from exc

        self._nc = await nats.connect(self._url)
        logger.info("NATSBus: %s に接続しました。", self._url)

    async def publish(self, topic: str, message: AgentMessage) -> None:
        if self._nc is None:
            raise RuntimeError("connect() を先に呼び出してください。")
        subject = self._to_nats_subject(topic)
        payload = message.model_dump_json().encode()
        await self._nc.publish(subject, payload)

    async def subscribe(self, topic: str, handler: AsyncHandlerFunc) -> None:
        if self._nc is None:
            raise RuntimeError("connect() を先に呼び出してください。")
        self._handlers[topic].append(handler)
        subject = self._to_nats_subject(topic)

        async def _cb(msg) -> None:
            try:
                data = json.loads(msg.data.decode())
                message = AgentMessage.model_validate(data)
            except Exception:
                logger.exception("NATS メッセージのデシリアライズに失敗しました。")
                return
            for h in list(self._handlers.get(topic, [])):
                try:
                    await h(topic, message)
                except Exception:
                    logger.exception(
                        "ハンドラー呼び出しでエラーが発生しました: topic=%s", topic
                    )

        sub = await self._nc.subscribe(subject, cb=_cb)
        self._subscriptions.append(sub)

    async def unsubscribe(self, topic: str, handler: AsyncHandlerFunc) -> None:
        handlers = self._handlers.get(topic, [])
        if handler in handlers:
            handlers.remove(handler)

    async def close(self) -> None:
        for sub in self._subscriptions:
            await sub.unsubscribe()
        self._subscriptions.clear()
        if self._nc:
            await self._nc.close()
        self._handlers.clear()
        logger.info("NATSBus: 切断しました。")


# ---------------------------------------------------------------------------
# ファクトリー関数
# ---------------------------------------------------------------------------

def create_bus(backend: str = "memory", **kwargs) -> MessageBus:
    """バックエンドを指定して MessageBus インスタンスを生成する。

    Args:
        backend: "memory" / "mqtt" / "nats"
        **kwargs: 各バックエンドのコンストラクター引数

    Examples::

        bus = create_bus("memory")
        bus = create_bus("mqtt", host="localhost", port=1883)
        bus = create_bus("nats", url="nats://localhost:4222")
    """
    if backend == "memory":
        return InMemoryBus()
    if backend == "mqtt":
        return MQTTBus(**kwargs)
    if backend == "nats":
        return NATSBus(**kwargs)
    raise ValueError(
        f"不明なバックエンド: {backend!r}。'memory', 'mqtt', 'nats' のいずれかを指定してください。"
    )
