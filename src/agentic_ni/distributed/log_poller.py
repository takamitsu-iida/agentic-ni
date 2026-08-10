"""DeviceLogPoller — 装置の show logging を定期ポーリングして SyslogEvent を生成する。

各 DeviceAgent が自装置のログを直接監視するためのコンポーネント。
CMLStateWatcher のような外部注入に依存せず、装置自身の syslog を検知する。
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Callable

from agentic_ni.logger import get_logger

if TYPE_CHECKING:
    from agentic_ni.distributed.device_agent import DeviceAgent, SyslogEvent

logger = get_logger(__name__)

# Cisco IOS syslog ライン識別パターン: %FACILITY-SEVERITY-MNEMONIC:
_SYSLOG_LINE_RE = re.compile(r"%[A-Z][\w-]+-\d+-[A-Z_]+:")

# seen セットの上限サイズ（メモリ肥大化防止）
_SEEN_BUFFER_SIZE = 200


class DeviceLogPoller:
    """装置のログバッファを定期ポーリングして新規 syslog を DeviceAgent へ注入する。

    Args:
        device_name:   装置名（ログ表示用）。
        run_command:   同期的に show コマンドを実行して出力文字列を返す callable。
        agent:         イベントの注入先 DeviceAgent。
        poll_interval: ポーリング間隔（秒）。デフォルト 10.0 秒。
        command:       実行するコマンド。デフォルト "show logging".
    """

    def __init__(
        self,
        device_name: str,
        run_command: Callable[[str], str],
        agent: "DeviceAgent",
        poll_interval: float = 10.0,
        command: str = "show logging",
    ) -> None:
        self._device_name = device_name
        self._run_command = run_command
        self._agent = agent
        self._poll_interval = poll_interval
        self._command = command
        self._seen: set[str] = set()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """ポーリングタスクを起動する。"""
        self._task = asyncio.create_task(
            self._poll_loop(), name=f"log-poller-{self._device_name}"
        )
        logger.info(
            "[%s] ログポーリング開始: interval=%.1fs  cmd=%r",
            self._device_name, self._poll_interval, self._command,
        )

    async def stop(self) -> None:
        """ポーリングタスクを停止する。"""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[%s] ログポーリング停止。", self._device_name)

    async def _poll_loop(self) -> None:
        # 初回: 起動前から存在するログを既読にして不要な再処理を防ぐ
        try:
            await asyncio.to_thread(self._snapshot_existing)
        except Exception as exc:
            logger.warning("[%s] 初期スナップショット取得エラー: %s", self._device_name, exc)

        while True:
            await asyncio.sleep(self._poll_interval)
            try:
                new_events = await asyncio.to_thread(self._read_new_syslogs)
                for event in new_events:
                    await self._agent.inject_event(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("[%s] ログポーリングエラー: %s", self._device_name, exc)

    def _snapshot_existing(self) -> None:
        """起動時に既存ログを既読としてマークする。"""
        try:
            output = self._run_command(self._command)
        except Exception as exc:
            logger.debug("[%s] 初期スナップショット失敗: %s", self._device_name, exc)
            return
        for line in output.splitlines():
            if _SYSLOG_LINE_RE.search(line):
                self._seen.add(_normalize(line))

    def _read_new_syslogs(self) -> "list[SyslogEvent]":
        """show logging を実行して未検知の syslog を SyslogEvent リストで返す。"""
        from agentic_ni.distributed.device_agent import SyslogEvent  # 循環 import 回避

        output = self._run_command(self._command)
        new_events: list[SyslogEvent] = []

        for line in output.splitlines():
            if not _SYSLOG_LINE_RE.search(line):
                continue
            key = _normalize(line)
            if key in self._seen:
                continue

            self._seen.add(key)
            _trim_seen(self._seen)

            severity = _extract_severity(line)
            logger.debug("[%s] 新規 syslog 検知: %s", self._device_name, line[:80])
            new_events.append(SyslogEvent(raw_text=line.strip(), severity=severity))

        return new_events


# ---------------------------------------------------------------------------
# ヘルパー関数
# ---------------------------------------------------------------------------

def _normalize(line: str) -> str:
    """タイムスタンプを除去して syslog 行を正規化する（重複検知キー用）。

    例: "*Aug 10 12:00:00.000: %OSPF-5-..." → "%OSPF-5-..."
    """
    m = re.search(r"(%[A-Z][\w-]+-\d+-[A-Z_]+:.*)", line)
    return m.group(1).strip() if m else line.strip()


def _extract_severity(line: str) -> str:
    """syslog 行からセベリティ番号を文字列で返す。見つからなければ "unknown"。"""
    m = re.search(r"%[A-Z][\w-]+-(\d+)-[A-Z_]+:", line)
    return m.group(1) if m else "unknown"


def _trim_seen(seen: set[str]) -> None:
    """seen セットが上限を超えたとき古い要素を削除してメモリを抑制する。"""
    if len(seen) > _SEEN_BUFFER_SIZE:
        excess = len(seen) - _SEEN_BUFFER_SIZE
        for old in list(seen)[:excess]:
            seen.discard(old)
