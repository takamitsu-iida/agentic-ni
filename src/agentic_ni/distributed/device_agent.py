"""装置エージェント本体。

1 装置 = 1 DeviceAgent。asyncio タスクとして非同期に動作し、
イベントキューからイベントを取り出して LLM 推論 → バスへの出力ルーティングを行う。

イベントの種類:
  - SyslogEvent    : syslog メッセージ（外部からの inject_event() で注入）
  - BusMessageEvent: 他エージェントからのバスメッセージ（自動サブスクライブ）
  - PollEvent      : ポーリング結果（外部からの inject_event() で注入）

出力フォーマット（LLM が生成することを期待する形式）:
  TO: [宛先] | MSG: [メッセージ本文]
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Union

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from agentic_ni.distributed.bus import MessageBus
from agentic_ni.distributed.dedup import SyslogDeduplicator
from agentic_ni.distributed.log_poller import DeviceLogPoller
from agentic_ni.distributed.memory import DeviceMemory
from agentic_ni.distributed.message import AgentMessage
from agentic_ni.distributed.prompts import build_device_prompt

logger = logging.getLogger(__name__)

# LLM 出力の1行からターゲットとメッセージを抽出する正規表現
_OUTPUT_LINE_RE = re.compile(r"TO:\s*(\S+)\s*\|\s*MSG:\s*(.*)", re.IGNORECASE)

# ツール呼び出しループの上限（無限ループ防止）
_MAX_TOOL_CALLS = 10


# ---------------------------------------------------------------------------
# イベント型定義
# ---------------------------------------------------------------------------

@dataclass
class SyslogEvent:
    """syslog メッセージから生成されるイベント。"""
    raw_text: str
    severity: str = "unknown"
    source_hostname: str = ""  # SYSLOGを送信した装置のホスト名（空の場合はフィルタなし）


@dataclass
class BusMessageEvent:
    """他エージェントからのバスメッセージから生成されるイベント。"""
    message: AgentMessage


@dataclass
class PollEvent:
    """定期ポーリングの結果から生成されるイベント。"""
    source: str    # ツール名（例: "run_show"）
    content: str   # コマンド出力テキスト


AgentEvent = Union[SyslogEvent, BusMessageEvent, PollEvent]


# ---------------------------------------------------------------------------
# 出力パーサー
# ---------------------------------------------------------------------------

def parse_agent_output(text: str) -> list[tuple[str, str]]:
    """LLM 出力から ``TO: X | MSG: Y`` パターンをすべて抽出する。

    ``TO: X | MSG:`` の次行以降で次の ``TO:`` が現れるまでの行は
    同じメッセージの継続行として連結する（多行レポート対応）。
    先頭に ``TO:`` パターンがない行はプリアンブルとして無視する。
    マッチしない場合は全文を ``HUMAN`` 宛にフォールバックする。
    """
    results: list[tuple[str, str]] = []
    current_target: str | None = None
    current_lines: list[str] = []

    for line in text.splitlines():
        m = _OUTPUT_LINE_RE.match(line.strip())
        if m:
            if current_target is not None:
                results.append((current_target, "\n".join(current_lines).strip()))
            current_target = m.group(1).strip()
            first = m.group(2).strip()
            current_lines = [first] if first else []
        elif current_target is not None:
            current_lines.append(line)
        # current_target が None のうちはプリアンブルとして無視する

    if current_target is not None:
        results.append((current_target, "\n".join(current_lines).strip()))

    return results if results else [("HUMAN", text.strip())]


# ---------------------------------------------------------------------------
# DeviceAgent
# ---------------------------------------------------------------------------

class DeviceAgent:
    """ネットワーク装置 1 台に対応する自律 AI エージェント。

    Args:
        device_name:    装置名（例: "R1"）。
        agent_id:       このエージェントの一意 ID（例: "Agent-R1"）。
        bus:            メッセージバスインスタンス（connect 済みであること）。
        memory:         このエージェントのローカルメモリ。
        device_type:    装置タイプ（例: "router", "switch"）。
        management_ip:  装置の管理 IP アドレス。
        llm:            LangChain 互換の LLM インスタンス。None の場合は get_llm() を使用。
        tools:          LLM にバインドするツールのリスト（TS-3 で追加）。
        human_queue:    HUMAN 宛メッセージの配信先キュー（None の場合は破棄）。
    """

    def __init__(
        self,
        device_name: str,
        agent_id: str,
        bus: MessageBus,
        memory: DeviceMemory,
        device_type: str = "router",
        management_ip: str = "",
        llm: BaseChatModel | None = None,
        tools: list[Any] | None = None,
        human_queue: asyncio.Queue | None = None,
        log_poller: DeviceLogPoller | None = None,
    ) -> None:
        self.device_name = device_name
        self.agent_id = agent_id

        self._bus = bus
        self._memory = memory
        self._device_type = device_type
        self._management_ip = management_ip
        self._tools = tools or []
        self._human_queue = human_queue
        self._log_poller = log_poller

        # LLM は遅延初期化（テスト時は外部から注入）
        self._llm: BaseChatModel | None = llm

        self._event_queue: asyncio.Queue[AgentEvent] = asyncio.Queue()
        self._running = False
        self._task: asyncio.Task | None = None
        self._current_event: AgentEvent | None = None  # hop_count 引き継ぎ用
        self._syslog_dedup = SyslogDeduplicator(window_seconds=60)
        # デモ・可視化用フック（None の場合は無効）
        self.on_tool_call: "Callable[[str, str, dict], None] | None" = None
        self.on_llm_turn: "Callable[[str, int], None] | None" = None

    # ------------------------------------------------------------------
    # ライフサイクル
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """バスをサブスクライブしてイベントループを起動する。"""
        await self._bus.subscribe(
            f"network/agents/{self.agent_id}/direct", self._on_bus_message
        )
        await self._bus.subscribe("network/agents/chat", self._on_bus_message)
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name=f"agent-{self.agent_id}")
        if self._log_poller:
            await self._log_poller.start()
        logger.info("[%s] 起動しました。", self.agent_id)

    async def stop(self) -> None:
        """イベントループを停止してバスのサブスクリプションを解除する。"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._log_poller:
            await self._log_poller.stop()
        await self._bus.unsubscribe(
            f"network/agents/{self.agent_id}/direct", self._on_bus_message
        )
        await self._bus.unsubscribe("network/agents/chat", self._on_bus_message)
        logger.info("[%s] 停止しました。", self.agent_id)

    def _is_my_syslog(self, source_hostname: str) -> bool:
        """SYSLOGの送信元が自分の担当装置かどうかを判定する。"""
        return source_hostname.lower() == self.device_name.lower()

    async def inject_event(self, event: AgentEvent) -> None:
        """外部からイベントを注入する（syslog・ポーリング結果等）。"""
        if isinstance(event, SyslogEvent):
            # source_hostname が設定されており自分と無関係なら無視（ブロードキャスト時のフィルタ）
            if event.source_hostname and not self._is_my_syslog(event.source_hostname):
                logger.debug(
                    "[%s] 無関係な SYSLOG を無視: source=%s", self.agent_id, event.source_hostname
                )
                return
            if self._syslog_dedup.is_duplicate(self.agent_id, event.raw_text):
                logger.info(
                    "[%s] 重複 syslog を破棄: %s", self.agent_id, event.raw_text[:80]
                )
                return
            self._syslog_dedup.mark_seen(self.agent_id, event.raw_text)
        await self._event_queue.put(event)

    async def wait_idle(self) -> None:
        """キューが空になるまで待機する（テスト・同期待ち用）。"""
        await self._event_queue.join()

    # ------------------------------------------------------------------
    # 内部ループ
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        while self._running:
            try:
                event = await asyncio.wait_for(self._event_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            try:
                await self._process_event(event)
            except Exception:
                logger.exception("[%s] イベント処理中にエラーが発生しました。", self.agent_id)
            finally:
                self._event_queue.task_done()

    async def _on_bus_message(self, topic: str, msg: AgentMessage) -> None:
        """バス経由のメッセージを受信してキューに積む。"""
        if msg.from_agent == self.agent_id:
            return  # 自分が送ったメッセージはスキップ
        await self._event_queue.put(BusMessageEvent(message=msg))

    # ------------------------------------------------------------------
    # イベント処理
    # ------------------------------------------------------------------

    async def _process_event(self, event: AgentEvent) -> None:
        """イベントを LLM に渡して推論し、ツール呼び出しループ後に結果をルーティングする。"""
        self._current_event = event
        summary = _event_summary(event)
        self._memory.add_status(_event_source(event), summary)
        logger.debug("[%s] イベント処理: %s", self.agent_id, summary[:80])

        messages = self._build_messages(event)
        llm = self._get_llm()
        llm_with_tools = llm.bind_tools(self._tools) if self._tools else llm

        for _turn in range(_MAX_TOOL_CALLS):
            try:
                response = await llm_with_tools.ainvoke(messages)
            except Exception:
                logger.exception("[%s] LLM 呼び出しに失敗しました。", self.agent_id)
                return

            messages.append(response)

            # ツール呼び出しがない → 最終応答としてルーティング
            tool_calls = getattr(response, "tool_calls", None)
            if not tool_calls:
                output_text: str = (
                    response.content if hasattr(response, "content") else str(response)
                )
                await self._route_output(output_text)
                return

            # ツール呼び出しを実行してメッセージに追記
            from langchain_core.messages import ToolMessage
            for tc in tool_calls:
                result = await self._execute_tool(tc)
                messages.append(ToolMessage(content=result, tool_call_id=tc["id"]))

        logger.warning("[%s] ツール呼び出し上限(%d)に達しました。", self.agent_id, _MAX_TOOL_CALLS)

    async def _execute_tool(self, tool_call: dict) -> str:
        """ツール呼び出し辞書を受け取り、対応するツールを実行して結果文字列を返す。"""
        tool_name = tool_call.get("name", "")
        tool_args = tool_call.get("args", {})

        if self.on_tool_call:
            self.on_tool_call(self.agent_id, tool_name, tool_args)

        for t in self._tools:
            if t.name == tool_name:
                try:
                    # sync ツールをスレッドプールで実行してイベントループをブロックしない
                    result = await asyncio.to_thread(t.invoke, tool_args)
                    self._memory.add_status("tool", f"{tool_name}: {str(result)[:200]}")
                    logger.debug("[%s] ツール %s 実行完了", self.agent_id, tool_name)
                    return str(result)
                except Exception as exc:
                    error_msg = f"ツール {tool_name!r} 実行エラー: {type(exc).__name__}: {exc}"
                    logger.warning("[%s] %s", self.agent_id, error_msg, exc_info=True)
                    return error_msg

        available = [t.name for t in self._tools]
        return f"ツール '{tool_name}' が見つかりません。利用可能なツール: {available}"

    def _build_messages(self, event: AgentEvent) -> list:
        """LLM に渡すメッセージリストを組み立てる。"""
        system_prompt = build_device_prompt(
            device_name=self.device_name,
            device_type=self._device_type,
            management_ip=self._management_ip,
            neighbors=self._memory.neighbor_map,
        )
        event_text = _event_summary(event)
        context = self._memory.recent_status_summary(n=5)

        user_content = (
            f"## 受信イベント\n{event_text}\n\n"
            f"## 直近のステータス履歴\n{context}"
        )
        return [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_content),
        ]

    async def _route_output(self, text: str) -> None:
        """LLM 出力を解析してバス送信 / 人間キュー / ログに振り分ける。"""
        for target, content in parse_agent_output(text):
            if target.upper() == "LOG":
                self._memory.add_status("llm", content)
                logger.debug("[%s] LOG: %s", self.agent_id, content[:80])

            elif target.upper() == "HUMAN":
                logger.info("[%s] → HUMAN: %s", self.agent_id, content[:120])
                if self._human_queue is not None:
                    await self._human_queue.put({
                        "from_agent": self.agent_id,
                        "content": content,
                    })

            else:
                # 他エージェント or ALL へバス送信
                is_broadcast = target.upper() == "ALL"
                # BusMessageEvent の場合は転送なのでホップ数をインクリメントして引き継ぐ
                if isinstance(self._current_event, BusMessageEvent):
                    hop = self._current_event.message.hop_count + 1
                    origin_id = (
                        self._current_event.message.origin_message_id
                        or self._current_event.message.message_id
                    )
                else:  # SyslogEvent / PollEvent は新規発火
                    hop = 0
                    origin_id = None
                msg = AgentMessage(
                    from_agent=self.agent_id,
                    to_agent=target,
                    msg_type="alert" if is_broadcast else "query",
                    content=content,
                    hop_count=hop,
                    origin_message_id=origin_id,
                )
                topic = (
                    "network/agents/chat"
                    if is_broadcast
                    else f"network/agents/{target}/direct"
                )
                await self._bus.publish(topic, msg)
                logger.info("[%s] → %s: %s", self.agent_id, target, content[:80])

    # ------------------------------------------------------------------
    # 内部ヘルパー
    # ------------------------------------------------------------------

    def _get_llm(self) -> BaseChatModel:
        """LLM インスタンスを返す。未設定の場合は get_llm() で初期化する。"""
        if self._llm is None:
            from agentic_ni.llm import get_llm
            self._llm = get_llm()
        return self._llm


# ---------------------------------------------------------------------------
# イベントユーティリティ
# ---------------------------------------------------------------------------

def _event_summary(event: AgentEvent) -> str:
    """イベントの要約文字列を返す（メモリ記録・ログ用）。"""
    if isinstance(event, SyslogEvent):
        return f"[SYSLOG/{event.severity}] {event.raw_text}"
    if isinstance(event, BusMessageEvent):
        msg = event.message
        return f"[BUS from {msg.from_agent}] {msg.content}"
    if isinstance(event, PollEvent):
        return f"[POLL/{event.source}] {event.content[:200]}"
    return str(event)


def _event_source(event: AgentEvent) -> str:
    """メモリに記録する source タグを返す。"""
    if isinstance(event, SyslogEvent):
        return "syslog"
    if isinstance(event, BusMessageEvent):
        return "bus"
    return "poll"
