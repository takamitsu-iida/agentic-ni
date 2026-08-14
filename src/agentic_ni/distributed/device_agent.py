"""装置エージェント本体（Worker モード）。

Coordinator から DeviceQueryRequest を受け取り、show コマンドで自装置を調査して
DeviceQueryResponse を返す。P2P メッセージングは廃止。

イベントの種類（PollEvent のみ）:
  - PollEvent : ポーリング結果（inject_event() で注入）

出力フォーマット（LLM が生成することを期待する形式）:
  TO: COORDINATOR | MSG: [調査結果]
  TO: LOG         | MSG: [ローカル記録]
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
from agentic_ni.distributed.connectivity_monitor import ConnectivityMonitor
from agentic_ni.distributed.incident import DeviceQueryRequest, DeviceQueryResponse
from agentic_ni.distributed.log_poller import DeviceLogPoller
from agentic_ni.distributed.memory import DesiredState, DeviceMemory
from agentic_ni.distributed.prompts import build_device_prompt

logger = logging.getLogger(__name__)

# LLM 出力の1行からターゲットとメッセージを抽出する正規表現
_OUTPUT_LINE_RE = re.compile(r"TO:\s*(\S+)\s*\|\s*MSG:\s*(.*)", re.IGNORECASE)

# ツール呼び出しループの上限（無限ループ防止）
_MAX_TOOL_CALLS = 10

# ツール出力の最大文字数（RateLimit 防止のため切り詰める）
_MAX_TOOL_OUTPUT_CHARS = 3000


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
class PollEvent:
    """定期ポーリングの結果から生成されるイベント。"""
    source: str    # ツール名（例: "run_show"）
    content: str   # コマンド出力テキスト


@dataclass
class ConnectivityLostEvent:
    """担当ノードとの通信断を通知するイベント。"""
    host: str
    port: int


@dataclass
class ConnectivityRestoredEvent:
    """担当ノードとの通信復旧を通知するイベント。"""
    host: str
    port: int


@dataclass
class HumanCommandEvent:
    """人間オペレーターからの直接指示・質問イベント。"""
    request: str  # 人間からの指示・質問テキスト


AgentEvent = Union[SyslogEvent, PollEvent, ConnectivityLostEvent, ConnectivityRestoredEvent, HumanCommandEvent]


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
        connectivity_port: int = 22,
        connectivity_poll_interval: float = 30.0,
        connectivity_timeout: float = 5.0,
        desired_state: DesiredState | None = None,
    ) -> None:
        self.device_name = device_name
        self.agent_id = agent_id

        self._bus = bus
        self._memory = memory
        if desired_state is not None:
            self._memory.desired_state = desired_state
        self._device_type = device_type
        self._management_ip = management_ip
        self._tools = tools or []
        self._human_queue = human_queue
        self._log_poller = log_poller
        self._connectivity_port = connectivity_port
        self._connectivity_poll_interval = connectivity_poll_interval
        self._connectivity_timeout = connectivity_timeout
        self._connectivity_monitor: ConnectivityMonitor | None = None

        # LLM は遅延初期化（テスト時は外部から注入）
        self._llm: BaseChatModel | None = llm

        self._event_queue: asyncio.Queue[AgentEvent] = asyncio.Queue()
        self._reachable: bool = True  # management_ip 未設定時は常に到達可能とみなす
        self._running = False
        self._task: asyncio.Task | None = None
        # デモ・可視化用フック（None の場合は無効）
        self.on_tool_call: "Callable[[str, str, dict], None] | None" = None
        self.on_llm_turn: "Callable[[str, int], None] | None" = None

    # ------------------------------------------------------------------
    # ライフサイクル
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """イベントループを起動する。"""
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name=f"agent-{self.agent_id}")
        if self._log_poller:
            await self._log_poller.start()
        if self._management_ip:
            self._connectivity_monitor = ConnectivityMonitor(
                device_name=self.device_name,
                host=self._management_ip,
                port=self._connectivity_port,
                poll_interval=self._connectivity_poll_interval,
                timeout=self._connectivity_timeout,
                on_lost=self._on_connectivity_lost,
                on_restored=self._on_connectivity_restored,
            )
            await self._connectivity_monitor.start()
        logger.info("[%s] 起動しました。", self.agent_id)

    async def stop(self) -> None:
        """イベントループを停止する。"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._log_poller:
            await self._log_poller.stop()
        if self._connectivity_monitor:
            await self._connectivity_monitor.stop()
        logger.info("[%s] 停止しました。", self.agent_id)

    # ------------------------------------------------------------------
    # 通信監視コールバック
    # ------------------------------------------------------------------

    async def _on_connectivity_lost(self) -> None:
        """ConnectivityMonitor から呼ばれる通信断コールバック。"""
        await self._event_queue.put(
            ConnectivityLostEvent(host=self._management_ip, port=self._connectivity_port)
        )

    async def _on_connectivity_restored(self) -> None:
        """ConnectivityMonitor から呼ばれる通信復旧コールバック。"""
        await self._event_queue.put(
            ConnectivityRestoredEvent(host=self._management_ip, port=self._connectivity_port)
        )

    async def inject_event(self, event: AgentEvent) -> None:
        """外部からイベントを注入する（PollEvent のみ）。"""
        await self._event_queue.put(event)

    async def inject_human_command(self, request: str) -> None:
        """人間オペレーターからの指示・質問をイベントキューに積む。"""
        await self._event_queue.put(HumanCommandEvent(request=request))

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

    # ------------------------------------------------------------------
    # イベント処理（PollEvent 用ループ）
    # ------------------------------------------------------------------

    async def _process_event(self, event: AgentEvent) -> None:
        """イベント種別に応じて処理をディスパッチする。"""
        if isinstance(event, ConnectivityLostEvent):
            await self._handle_connectivity_lost(event)
            return
        if isinstance(event, ConnectivityRestoredEvent):
            await self._handle_connectivity_restored(event)
            return
        if not self._reachable:
            if isinstance(event, HumanCommandEvent):
                logger.info("[%s] 到達不可のため人間コマンドをスキップ", self.agent_id)
                if self._human_queue is not None:
                    await self._human_queue.put({
                        "from_agent": self.agent_id,
                        "content": f"[{self.device_name}] 担当装置に現在到達できないため調査できません。",
                    })
            else:
                logger.debug("[%s] 到達不可のためイベントをスキップ", self.agent_id)
            return
        if isinstance(event, HumanCommandEvent):
            await self._handle_human_command(event)
            return
        await self._process_poll_or_syslog(event)

    async def _handle_connectivity_lost(self, event: ConnectivityLostEvent) -> None:
        """通信断を記録し、LLM を介さず直接人間に報告する。"""
        self._reachable = False
        msg = (
            f"[{self.device_name}] 担当ノードとの通信が途絶えました。"
            f" 管理 IP: {event.host}:{event.port}"
        )
        self._memory.add_status("connectivity", msg)
        logger.warning("[%s] %s", self.agent_id, msg)
        if self._human_queue is not None:
            await self._human_queue.put({"from_agent": self.agent_id, "content": msg})

    async def _handle_connectivity_restored(self, event: ConnectivityRestoredEvent) -> None:
        """通信復旧を記録し、LLM を介さず直接人間に報告する。"""
        self._reachable = True
        msg = (
            f"[{self.device_name}] 担当ノードとの通信が復旧しました。"
            f" 管理 IP: {event.host}:{event.port}"
        )
        self._memory.add_status("connectivity", msg)
        logger.info("[%s] %s", self.agent_id, msg)
        if self._human_queue is not None:
            await self._human_queue.put({"from_agent": self.agent_id, "content": msg})

    async def _handle_human_command(self, event: HumanCommandEvent) -> None:
        """人間からの指示・質問を LLM + ツールで処理し、結果を human_queue に返す。"""
        self._memory.add_status("human", event.request[:200])
        logger.info("[%s] 人間からの指示を受信: %s", self.agent_id, event.request[:80])

        messages = self._build_human_command_messages(event.request)
        llm = self._get_llm()
        llm_with_tools = llm.bind_tools(self._tools) if self._tools else llm

        for _turn in range(_MAX_TOOL_CALLS):
            logger.info("[%s] LLM問い合わせ中 (ターン %d/%d)...", self.agent_id, _turn + 1, _MAX_TOOL_CALLS)
            try:
                response = await llm_with_tools.ainvoke(messages)
            except Exception:
                logger.exception("[%s] LLM 呼び出しに失敗しました。", self.agent_id)
                if self._human_queue is not None:
                    await self._human_queue.put({
                        "from_agent": self.agent_id,
                        "content": f"[{self.device_name}] LLM エラーにより処理できませんでした。",
                    })
                return

            messages.append(response)
            tool_calls = getattr(response, "tool_calls", None)

            if not tool_calls:
                output_text: str = (
                    response.content if hasattr(response, "content") else str(response)
                )
                # 人間コマンドの応答は常に human_queue へ直接送る
                await self._deliver_to_human(output_text)
                return

            tool_names = [tc.get("name", "?") for tc in tool_calls]
            logger.info("[%s] ツール呼び出し: %s", self.agent_id, tool_names)
            from langchain_core.messages import ToolMessage
            for tc in tool_calls:
                result = await self._execute_tool(tc)
                messages.append(ToolMessage(content=result, tool_call_id=tc["id"]))

        logger.warning("[%s] ツール上限(%d)に達しました。中間結果を返します。", self.agent_id, _MAX_TOOL_CALLS)
        if self._human_queue is not None:
            await self._human_queue.put({
                "from_agent": self.agent_id,
                "content": f"[{self.device_name}] ツール呼び出し上限に達しました。調査を中断します。",
            })

    async def _deliver_to_human(self, text: str) -> None:
        """LLM 出力を解析し、HUMAN 宛メッセージを human_queue に届ける。"""
        delivered = False
        for target, content in parse_agent_output(text):
            if target.upper() in ("HUMAN", "LOG", "COORDINATOR"):
                if self._human_queue is not None and target.upper() != "LOG":
                    await self._human_queue.put({"from_agent": self.agent_id, "content": content})
                    delivered = True
                elif target.upper() == "LOG":
                    self._memory.add_status("llm", content)
        # フォールバック: TO: フォーマットがない場合も全文を送る
        if not delivered and self._human_queue is not None:
            await self._human_queue.put({"from_agent": self.agent_id, "content": text.strip()})

    def _build_human_command_messages(self, request: str) -> list:
        """人間からの指示用 LLM メッセージリストを組み立てる。"""
        system_prompt = build_device_prompt(
            device_name=self.device_name,
            device_type=self._device_type,
            management_ip=self._management_ip,
            neighbors=self._memory.neighbor_map,
            desired_state=self._memory.desired_state,
        )
        context = self._memory.recent_status_summary(n=5)
        user_content = (
            f"## 人間オペレーターからの指示\n{request}\n\n"
            f"## 直近のステータス履歴\n{context}\n\n"
            "show コマンドで必要な情報を収集し、"
            "結果を **TO: HUMAN | MSG: ...** 形式で回答してください。\n"
            "ログへの記録は **TO: LOG | MSG: ...** を使ってください。"
        )
        return [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_content),
        ]

    async def _process_poll_or_syslog(self, event: AgentEvent) -> None:
        """PollEvent / SyslogEvent を LLM に渡して推論し、ツール呼び出しループ後に結果をルーティングする。"""
        summary = _event_summary(event)
        self._memory.add_status(_event_source(event), summary)
        logger.info("[%s] 調査開始: %s", self.agent_id, summary[:80])

        messages = self._build_messages(event)
        llm = self._get_llm()
        llm_with_tools = llm.bind_tools(self._tools) if self._tools else llm

        for _turn in range(_MAX_TOOL_CALLS):
            logger.info("[%s] LLM問い合わせ中 (ターン %d/%d)...", self.agent_id, _turn + 1, _MAX_TOOL_CALLS)
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
            tool_names = [tc.get("name", "?") for tc in tool_calls]
            logger.info("[%s] LLM応答: ツール呼び出し %s", self.agent_id, tool_names)
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
                    logger.info("[%s] ツール実行中: %s %s", self.agent_id, tool_name, str(tool_args)[:80])
                    # sync ツールをスレッドプールで実行してイベントループをブロックしない
                    result = await asyncio.wait_for(
                        asyncio.to_thread(t.invoke, tool_args),
                        timeout=60.0,
                    )
                    logger.info("[%s] ツール完了: %s", self.agent_id, tool_name)
                    result_str = str(result)
                    self._memory.add_status("tool", f"{tool_name}: {result_str[:200]}")
                    logger.debug("[%s] ツール %s 実行完了", self.agent_id, tool_name)
                    # LLM への送信前に出力を切り詰め（RateLimit 防止）
                    if len(result_str) > _MAX_TOOL_OUTPUT_CHARS:
                        result_str = result_str[:_MAX_TOOL_OUTPUT_CHARS] + "\n... (出力が長いため省略)"
                    return result_str
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
            desired_state=self._memory.desired_state,
        )
        event_text = _event_summary(event)
        context = self._memory.recent_status_summary(n=10)

        user_content = (
            f"## 受信イベント\n{event_text}\n\n"
            f"## 直近のステータス履歴\n{context}"
        )
        return [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_content),
        ]

    async def _route_output(self, text: str) -> None:
        """LLM 出力を解析して LOG / HUMAN に振り分ける（Worker モード）。"""
        for target, content in parse_agent_output(text):
            if target.upper() == "LOG":
                self._memory.add_status("llm", content)
                logger.debug("[%s] LOG: %s", self.agent_id, content[:80])

            elif target.upper() in ("HUMAN", "COORDINATOR"):
                logger.info("[%s] → %s: %s", self.agent_id, target, content[:120])
                if self._human_queue is not None:
                    await self._human_queue.put({
                        "from_agent": self.agent_id,
                        "content": content,
                    })

            else:
                # Worker モードではエージェント間バス送信は行わない
                logger.debug("[%s] バス送信スキップ (Worker モード): target=%s", self.agent_id, target)

    # ------------------------------------------------------------------
    # 内部ヘルパー
    # ------------------------------------------------------------------

    def _get_llm(self) -> BaseChatModel:
        """LLM インスタンスを返す。未設定の場合は get_llm() で初期化する。"""
        if self._llm is None:
            from agentic_ni.llm import get_llm
            self._llm = get_llm()
        return self._llm

    # ------------------------------------------------------------------
    # Worker API（Coordinator から直接呼ばれる）
    # ------------------------------------------------------------------

    async def execute_query(self, request: DeviceQueryRequest) -> DeviceQueryResponse:
        """Coordinator からの QueryRequest を処理して調査結果を返す。"""
        logger.info("[%s] クエリ受信: incident=%s", self.agent_id, request.incident_id[:8])
        self._memory.add_status("coordinator", request.symptom_summary[:200])

        messages = self._build_query_messages(request)
        llm = self._get_llm()
        llm_with_tools = llm.bind_tools(self._tools) if self._tools else llm
        show_outputs: dict[str, str] = {}

        for _turn in range(_MAX_TOOL_CALLS):
            logger.info("[%s] LLM問い合わせ中 (ターン %d/%d)...", self.agent_id, _turn + 1, _MAX_TOOL_CALLS)
            try:
                response = await llm_with_tools.ainvoke(messages)
            except Exception as exc:
                logger.exception("[%s] LLM 呼び出しに失敗しました。", self.agent_id)
                return DeviceQueryResponse(
                    incident_id=request.incident_id,
                    from_device=self.device_name,
                    findings=f"LLM エラー: {exc}",
                    show_outputs=show_outputs,
                    error=True,
                    error_detail=str(exc),
                )

            messages.append(response)
            tool_calls = getattr(response, "tool_calls", None)

            if not tool_calls:
                findings = response.content if hasattr(response, "content") else str(response)
                logger.info("[%s] クエリ完了: %s...", self.agent_id, findings[:80])
                return DeviceQueryResponse(
                    incident_id=request.incident_id,
                    from_device=self.device_name,
                    findings=findings,
                    show_outputs=show_outputs,
                )

            tool_names = [tc.get("name", "?") for tc in tool_calls]
            logger.info("[%s] LLM応答: ツール呼び出し %s", self.agent_id, tool_names)
            from langchain_core.messages import ToolMessage
            for tc in tool_calls:
                result = await self._execute_tool(tc)
                messages.append(ToolMessage(content=result, tool_call_id=tc["id"]))
                cmd = tc.get("args", {}).get("command", tc.get("name", ""))
                if cmd:
                    show_outputs[cmd] = result[:_MAX_TOOL_OUTPUT_CHARS]

        logger.warning("[%s] ツール上限に達しました。中間結果を返します。", self.agent_id)
        return DeviceQueryResponse(
            incident_id=request.incident_id,
            from_device=self.device_name,
            findings="ツール呼び出し上限に達しました。中間調査結果を参照してください。",
            show_outputs=show_outputs,
        )

    def _build_query_messages(self, request: DeviceQueryRequest) -> list:
        """QueryRequest 用の LLM メッセージリストを組み立てる。"""
        system_prompt = build_device_prompt(
            device_name=self.device_name,
            device_type=self._device_type,
            management_ip=self._management_ip,
            neighbors=self._memory.neighbor_map,
            desired_state=self._memory.desired_state,
        )
        context = self._memory.recent_status_summary(n=5)
        user_content = (
            f"## 調査依頼\n{request.symptom_summary}\n\n"
            f"## 直近のステータス履歴\n{context}\n\n"
            "正常状態の定義（ベースライン）と現在の状態を比較し、"
            "差異を優先的に調査して TO: COORDINATOR | MSG: ... 形式で報告してください。"
        )
        try:
            from agentic_ni.tools import rag_tools
            knowledge = rag_tools.search_knowledge(request.symptom_summary, k=3)
            if knowledge:
                knowledge_text = "\n\n".join(
                    f"**{k['source_file']}** （関連度: {1.0 - k['distance']:.0%}）\n```\n{k['content']}\n```"
                    for k in knowledge
                )
                user_content += f"\n\n## 参考資料（知識ベース）\n{knowledge_text}"
        except Exception:  # noqa: BLE001
            pass
        return [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_content),
        ]


# ---------------------------------------------------------------------------
# イベントユーティリティ
# ---------------------------------------------------------------------------

def _event_summary(event: AgentEvent) -> str:
    """イベントの要約文字列を返す（メモリ記録・ログ用）。"""
    if isinstance(event, SyslogEvent):
        return f"[SYSLOG/{event.severity}] {event.raw_text}"
    if isinstance(event, PollEvent):
        return f"[POLL/{event.source}] {event.content[:200]}"
    if isinstance(event, ConnectivityLostEvent):
        return f"[CONNECTIVITY/LOST] {event.host}:{event.port}"
    if isinstance(event, ConnectivityRestoredEvent):
        return f"[CONNECTIVITY/RESTORED] {event.host}:{event.port}"
    if isinstance(event, HumanCommandEvent):
        return f"[HUMAN] {event.request[:200]}"
    return str(event)


def _event_source(event: AgentEvent) -> str:
    """メモリに記録する source タグを返す。"""
    if isinstance(event, SyslogEvent):
        return "syslog"
    if isinstance(event, (ConnectivityLostEvent, ConnectivityRestoredEvent)):
        return "connectivity"
    if isinstance(event, HumanCommandEvent):
        return "human"
    return "poll"
