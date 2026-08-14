"""IncidentCoordinator — インシデントの調査ライフサイクルを一元管理する。

EventCorrelator から NetworkIncident を受け取り、
affected_devices に DeviceQueryRequest を並列発行して DeviceQueryResponse を集約し、
LLM による根本原因分析（RCA）を最大 2 ラウンドで完了させて human_queue へ報告する。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, Callable, Protocol

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from agentic_ni.distributed.incident import (
    DeviceQueryRequest,
    DeviceQueryResponse,
    NetworkIncident,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# RCA 判定 LLM へのシステムプロンプト
_RCA_SYSTEM_PROMPT = """\
あなたはネットワーク障害の根本原因分析（RCA）を行う専門エンジニアです。
複数のネットワーク装置から収集した調査結果を統合し、根本原因を特定してください。

## 出力ルール
調査結果から根本原因が特定できる場合は、以下の形式で最終レポートを出力してください:

FINAL_REPORT:
- 症状: [観測した症状の概要]
- 根本原因: [特定した根本原因]
- 影響範囲: [影響を受けているサービス・経路・装置]
- 推奨対応:
  1. [対応手順1]
  2. [対応手順2]

根本原因の特定に特定の装置の追加情報が必要な場合は、以下の形式のみで出力してください:

NEED_MORE_INFO:
DEVICE: [装置名]
REASON: [何故その情報が必要か]

最終ラウンドの場合は必ず FINAL_REPORT を出力してください。
"""

# DeviceAgent に期待するインターフェース（Protocol）
class QueryableAgent(Protocol):
    device_name: str

    async def execute_query(self, request: DeviceQueryRequest) -> DeviceQueryResponse: ...


class IncidentCoordinator:
    """インシデント調査ライフサイクルを所有するコーディネーター。

    Args:
        agent_registry:  device_name → QueryableAgent のマッピング。
        llm:             RCA 判定に使用する LLM。None の場合は get_llm() で取得。
        human_queue:     最終レポートの送信先キュー。
        query_timeout:   1 台あたりのクエリタイムアウト秒数。
        dedup_window:    同一 correlation_key の重複インシデント抑制窓（秒）。
    """

    def __init__(
        self,
        agent_registry: dict[str, QueryableAgent],
        llm: BaseChatModel | None = None,
        human_queue: asyncio.Queue | None = None,
        query_timeout: float = 30.0,
        dedup_window: float = 60.0,
    ) -> None:
        self._agents = agent_registry
        self._llm = llm
        self._human_queue = human_queue
        self._query_timeout = query_timeout
        self._dedup_window = dedup_window

        # correlation_key → 最後にオープンした時刻（重複抑制用）
        self._open_incidents: dict[str, float] = {}

    # ------------------------------------------------------------------
    # 公開 API
    # ------------------------------------------------------------------

    async def handle_incident(self, incident: NetworkIncident) -> None:
        """NetworkIncident を受け取り調査・報告を完結させる。"""
        if self._is_duplicate(incident.correlation_key):
            logger.info(
                "Incident 重複スキップ: key=%s (%.0fs 以内に同一キーが処理済み)",
                incident.correlation_key, self._dedup_window,
            )
            return

        self._open_incidents[incident.correlation_key] = time.monotonic()
        logger.info(
            "Incident 調査開始: id=%s key=%s devices=%s events=%d",
            incident.incident_id[:8], incident.correlation_key,
            incident.affected_devices, len(incident.syslog_events),
        )

        symptom_summary = self._build_symptom_summary(incident)

        # Round 1: 全 affected_devices に並列クエリ
        responses = await self._query_devices(
            incident, incident.affected_devices, symptom_summary, round_num=1
        )

        # LLM による RCA 判定（Round 1）
        rca_text, need_more = await self._analyze(incident, responses, final_round=False)

        # Round 2: LLM が追加情報を要求した場合のみ実施
        if need_more:
            extra_device, extra_reason = need_more
            logger.info(
                "Round 2 クエリ: device=%s reason=%s", extra_device, extra_reason
            )
            extra_summary = f"{symptom_summary}\n\n追加調査依頼: {extra_reason}"
            extra_responses = await self._query_devices(
                incident, [extra_device], extra_summary, round_num=2
            )
            all_responses = responses + extra_responses
            rca_text, _ = await self._analyze(incident, all_responses, final_round=True)

        await self._report(incident, rca_text)

    # ------------------------------------------------------------------
    # 内部メソッド
    # ------------------------------------------------------------------

    def _is_duplicate(self, correlation_key: str) -> bool:
        last = self._open_incidents.get(correlation_key)
        if last is None:
            return False
        return (time.monotonic() - last) < self._dedup_window

    def _build_symptom_summary(self, incident: NetworkIncident) -> str:
        events_text = "\n".join(f"  - {e}" for e in incident.syslog_events[:10])
        return (
            f"インシデント ID: {incident.incident_id[:8]}\n"
            f"相関キー: {incident.correlation_key}\n"
            f"関連装置: {', '.join(incident.affected_devices)}\n"
            f"検知された SYSLOG:\n{events_text}"
        )

    async def _query_devices(
        self,
        incident: NetworkIncident,
        device_names: list[str],
        symptom_summary: str,
        round_num: int,
    ) -> list[DeviceQueryResponse]:
        tasks = []
        for name in device_names:
            agent = self._agents.get(name)
            if agent is None:
                logger.warning("エージェントが見つかりません: device=%s", name)
                continue
            req = DeviceQueryRequest(
                incident_id=incident.incident_id,
                target_device=name,
                symptom_summary=symptom_summary,
            )
            tasks.append(self._query_one(agent, req, round_num))

        if not tasks:
            return []

        results = await asyncio.gather(*tasks, return_exceptions=True)
        responses: list[DeviceQueryResponse] = []
        for r in results:
            if isinstance(r, DeviceQueryResponse):
                responses.append(r)
            else:
                logger.warning("クエリ結果エラー: %s", r)
        return responses

    async def _query_one(
        self, agent: QueryableAgent, req: DeviceQueryRequest, round_num: int
    ) -> DeviceQueryResponse:
        logger.info(
            "[Round %d] クエリ送信: device=%s", round_num, req.target_device
        )
        try:
            return await asyncio.wait_for(
                agent.execute_query(req), timeout=self._query_timeout
            )
        except asyncio.TimeoutError:
            logger.warning("クエリタイムアウト: device=%s", req.target_device)
            return DeviceQueryResponse(
                incident_id=req.incident_id,
                from_device=req.target_device,
                findings="タイムアウトのため調査結果を取得できませんでした。",
                error=True,
                error_detail="query timeout",
            )
        except Exception as exc:
            logger.warning("クエリエラー: device=%s error=%s", req.target_device, exc)
            return DeviceQueryResponse(
                incident_id=req.incident_id,
                from_device=req.target_device,
                findings=f"エラー: {exc}",
                error=True,
                error_detail=str(exc),
            )

    async def _analyze(
        self,
        incident: NetworkIncident,
        responses: list[DeviceQueryResponse],
        final_round: bool,
    ) -> tuple[str, tuple[str, str] | None]:
        """LLM に RCA を依頼し、(rca_text, need_more) を返す。

        need_more は (device_name, reason) または None。
        final_round=True のときは必ず FINAL_REPORT を返すよう指示する。
        """
        llm = self._get_llm()
        findings_text = self._format_findings(responses)
        symptom_summary = self._build_symptom_summary(incident)

        round_note = "【最終ラウンド: 必ず FINAL_REPORT を出力してください】\n" if final_round else ""
        user_content = (
            f"{round_note}"
            f"## インシデント情報\n{symptom_summary}\n\n"
            f"## 各装置の調査結果\n{findings_text}"
        )
        try:
            from agentic_ni.tools import rag_tools
            rca_query = " ".join(incident.syslog_events[:5])
            knowledge = rag_tools.search_knowledge(rca_query, k=3)
            if knowledge:
                knowledge_text = "\n\n".join(
                    f"**{k['source_file']}** （関連度: {1.0 - k['distance']:.0%}）\n```\n{k['content']}\n```"
                    for k in knowledge
                )
                user_content += f"\n\n## 参考資料（知識ベース）\n{knowledge_text}"
        except Exception:  # noqa: BLE001
            pass

        messages = [
            SystemMessage(content=_RCA_SYSTEM_PROMPT),
            HumanMessage(content=user_content),
        ]

        try:
            response = await llm.ainvoke(messages)
            text = response.content if hasattr(response, "content") else str(response)
        except Exception as exc:
            logger.exception("LLM RCA 呼び出しエラー: %s", exc)
            text = f"FINAL_REPORT:\n- 症状: LLM エラーにより RCA 不完全\n- 根本原因: 不明\n- 影響範囲: 不明\n- 推奨対応:\n  1. 手動調査を実施してください"

        # NEED_MORE_INFO の解析
        if not final_round and "NEED_MORE_INFO:" in text:
            device, reason = self._parse_need_more(text)
            if device and device in self._agents:
                return text, (device, reason)

        return text, None

    def _parse_need_more(self, text: str) -> tuple[str, str]:
        device, reason = "", ""
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("DEVICE:"):
                device = line[len("DEVICE:"):].strip()
            elif line.startswith("REASON:"):
                reason = line[len("REASON:"):].strip()
        return device, reason

    def _format_findings(self, responses: list[DeviceQueryResponse]) -> str:
        if not responses:
            return "  （調査結果なし）"
        parts = []
        for r in responses:
            status = "⚠️ エラー" if r.error else "✅ 正常"
            parts.append(
                f"### {r.from_device} [{status}]\n"
                f"{r.findings}\n"
            )
        return "\n".join(parts)

    async def _report(self, incident: NetworkIncident, rca_text: str) -> None:
        """RCA レポートを human_queue に送信する。"""
        logger.info(
            "Incident 調査完了: id=%s key=%s",
            incident.incident_id[:8], incident.correlation_key,
        )
        if self._human_queue is None:
            return
        await self._human_queue.put({
            "type": "rca_report",
            "incident_id": incident.incident_id,
            "correlation_key": incident.correlation_key,
            "affected_devices": incident.affected_devices,
            "content": rca_text,
            "from_agent": "IncidentCoordinator",
        })

    def _get_llm(self) -> BaseChatModel:
        if self._llm is not None:
            return self._llm
        from agentic_ni.llm import get_llm
        return get_llm()
