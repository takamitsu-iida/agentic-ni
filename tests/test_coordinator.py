"""Refactor-3: IncidentCoordinator のユニットテスト。"""

from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import AIMessage, BaseMessage

from agentic_ni.distributed.coordinator import IncidentCoordinator
from agentic_ni.distributed.incident import (
    DeviceQueryRequest,
    DeviceQueryResponse,
    NetworkIncident,
)


# ---------------------------------------------------------------------------
# テスト用 Mock
# ---------------------------------------------------------------------------

class _MockLLM:
    """シーケンス再生モック LLM。"""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self._idx = 0
        self.calls: list[list[BaseMessage]] = []

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages: list[BaseMessage]) -> AIMessage:
        self.calls.append(messages)
        text = self._responses[min(self._idx, len(self._responses) - 1)]
        self._idx += 1
        return AIMessage(content=text)


class _MockAgent:
    """execute_query を即座に返すモックエージェント。"""

    def __init__(self, device_name: str, findings: str = "異常なし", error: bool = False) -> None:
        self.device_name = device_name
        self._findings = findings
        self._error = error
        self.received_requests: list[DeviceQueryRequest] = []

    async def execute_query(self, request: DeviceQueryRequest) -> DeviceQueryResponse:
        self.received_requests.append(request)
        return DeviceQueryResponse(
            incident_id=request.incident_id,
            from_device=self.device_name,
            findings=self._findings,
            error=self._error,
        )


def _make_incident(
    key: str = "link:n0-n4",
    devices: list[str] | None = None,
) -> NetworkIncident:
    return NetworkIncident(
        correlation_key=key,
        affected_devices=devices or ["Spine1", "Leaf3"],
        syslog_events=[
            "%LINEPROTO-5-UPDOWN: Line protocol on Interface GigabitEthernet0/2, changed state to down",
            "%BGP-5-ADJCHANGE: neighbor 10.1.13.2 Down Interface flap",
        ],
    )


_FINAL_REPORT = (
    "FINAL_REPORT:\n"
    "- 症状: Spine1-Leaf3 間リンクダウン\n"
    "- 根本原因: 物理ケーブル障害\n"
    "- 影響範囲: BGP セッション断\n"
    "- 推奨対応:\n"
    "  1. ケーブルを確認・交換してください"
)


# ---------------------------------------------------------------------------
# テスト
# ---------------------------------------------------------------------------

class TestDuplicateSuppress:
    @pytest.mark.asyncio
    async def test_duplicate_incident_is_skipped(self):
        """同一 correlation_key が dedup_window 以内に来たらスキップ。"""
        llm = _MockLLM([_FINAL_REPORT])
        agents = {
            "Spine1": _MockAgent("Spine1", "Gi0/2 down"),
            "Leaf3":  _MockAgent("Leaf3",  "Gi0/0 down"),
        }
        queue: asyncio.Queue = asyncio.Queue()
        coord = IncidentCoordinator(agents, llm=llm, human_queue=queue, dedup_window=60.0)

        await coord.handle_incident(_make_incident())
        assert queue.qsize() == 1

        await coord.handle_incident(_make_incident())   # 重複
        assert queue.qsize() == 1  # 増えていない

    @pytest.mark.asyncio
    async def test_different_key_is_not_suppressed(self):
        """異なる correlation_key は別インシデントとして処理される。"""
        llm = _MockLLM([_FINAL_REPORT, _FINAL_REPORT])
        agents = {"Spine1": _MockAgent("Spine1")}
        queue: asyncio.Queue = asyncio.Queue()
        coord = IncidentCoordinator(agents, llm=llm, human_queue=queue, dedup_window=60.0)

        await coord.handle_incident(_make_incident("link:n0-n4", ["Spine1"]))
        await coord.handle_incident(_make_incident("link:n1-n4", ["Spine1"]))
        assert queue.qsize() == 2


class TestNormalFlow:
    @pytest.mark.asyncio
    async def test_rca_report_generated_for_two_devices(self):
        """2台のレスポンスを集約して HUMAN へ RCA レポートが送信される。"""
        llm = _MockLLM([_FINAL_REPORT])
        spine = _MockAgent("Spine1", "Gi0/2 は down 状態")
        leaf  = _MockAgent("Leaf3",  "Gi0/0 は down 状態")
        queue: asyncio.Queue = asyncio.Queue()
        coord = IncidentCoordinator(
            {"Spine1": spine, "Leaf3": leaf}, llm=llm, human_queue=queue
        )

        await coord.handle_incident(_make_incident())

        assert queue.qsize() == 1
        report = await queue.get()
        assert report["type"] == "rca_report"
        assert "FINAL_REPORT" in report["content"]
        assert set(report["affected_devices"]) == {"Spine1", "Leaf3"}

    @pytest.mark.asyncio
    async def test_queries_are_sent_to_all_affected_devices(self):
        """affected_devices 全台に QueryRequest が送られる。"""
        llm = _MockLLM([_FINAL_REPORT])
        spine = _MockAgent("Spine1")
        leaf  = _MockAgent("Leaf3")
        coord = IncidentCoordinator({"Spine1": spine, "Leaf3": leaf}, llm=llm)

        await coord.handle_incident(_make_incident())

        assert len(spine.received_requests) == 1
        assert len(leaf.received_requests) == 1
        assert spine.received_requests[0].target_device == "Spine1"

    @pytest.mark.asyncio
    async def test_unknown_device_is_skipped_gracefully(self):
        """registry に存在しない装置はスキップしてもクラッシュしない。"""
        llm = _MockLLM([_FINAL_REPORT])
        agents = {"Spine1": _MockAgent("Spine1")}
        queue: asyncio.Queue = asyncio.Queue()
        coord = IncidentCoordinator(agents, llm=llm, human_queue=queue)

        incident = _make_incident(devices=["Spine1", "UnknownDevice"])
        await coord.handle_incident(incident)

        assert queue.qsize() == 1


class TestRound2:
    @pytest.mark.asyncio
    async def test_round2_triggered_when_llm_needs_more_info(self):
        """LLM が NEED_MORE_INFO を返したら Round 2 クエリが発行される。"""
        need_more = (
            "NEED_MORE_INFO:\n"
            "DEVICE: Spine1\n"
            "REASON: ルーティングテーブルを確認したい"
        )
        llm = _MockLLM([need_more, _FINAL_REPORT])
        spine = _MockAgent("Spine1", "BGP セッション断")
        leaf  = _MockAgent("Leaf3",  "インターフェースダウン")
        queue: asyncio.Queue = asyncio.Queue()
        coord = IncidentCoordinator({"Spine1": spine, "Leaf3": leaf}, llm=llm, human_queue=queue)

        await coord.handle_incident(_make_incident())

        # Round 1 (Spine1 + Leaf3) + Round 2 (Spine1) = Spine1 に 2 回クエリ
        assert len(spine.received_requests) == 2
        assert len(leaf.received_requests) == 1
        # 最終的に FINAL_REPORT が届く
        assert queue.qsize() == 1
        report = await queue.get()
        assert "FINAL_REPORT" in report["content"]

    @pytest.mark.asyncio
    async def test_query_timeout_returns_error_response(self):
        """タイムアウトした場合でも RCA レポートが生成される。"""

        class _SlowAgent:
            device_name = "SlowDevice"

            async def execute_query(self, req: DeviceQueryRequest) -> DeviceQueryResponse:
                await asyncio.sleep(10)  # タイムアウト想定
                return DeviceQueryResponse(  # pragma: no cover
                    incident_id=req.incident_id, from_device=self.device_name, findings=""
                )

        llm = _MockLLM([_FINAL_REPORT])
        queue: asyncio.Queue = asyncio.Queue()
        coord = IncidentCoordinator(
            {"SlowDevice": _SlowAgent()},
            llm=llm,
            human_queue=queue,
            query_timeout=0.05,
        )

        incident = _make_incident(devices=["SlowDevice"])
        await coord.handle_incident(incident)

        assert queue.qsize() == 1
