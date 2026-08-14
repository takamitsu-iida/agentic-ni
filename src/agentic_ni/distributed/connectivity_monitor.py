"""ConnectivityMonitor — 管理 IP への TCP 到達性を定期確認し、状態変化をコールバックで通知する。

DeviceAgent に組み込んで使い、担当ノードとの通信断・復旧を自律検知する。
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from agentic_ni.logger import get_logger

logger = get_logger(__name__)


class ConnectivityMonitor:
    """管理 IP への TCP 接続を定期試行して通信断/復旧を検知する。

    Args:
        device_name:    装置名（ログ表示用）。
        host:           接続先ホスト（管理 IP）。
        port:           接続先ポート（デフォルト 22 = SSH）。
        poll_interval:  チェック間隔（秒）。デフォルト 30.0 秒。
        timeout:        接続タイムアウト（秒）。デフォルト 5.0 秒。
        on_lost:        通信断検知時に呼ばれる非同期コールバック。
        on_restored:    通信復旧検知時に呼ばれる非同期コールバック。
    """

    def __init__(
        self,
        device_name: str,
        host: str,
        port: int = 22,
        poll_interval: float = 30.0,
        timeout: float = 5.0,
        on_lost: Callable[[], Awaitable[None]] | None = None,
        on_restored: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._device_name = device_name
        self._host = host
        self._port = port
        self._poll_interval = poll_interval
        self._timeout = timeout
        self._on_lost = on_lost
        self._on_restored = on_restored
        self._reachable: bool | None = None  # 初回は不明
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """監視タスクを起動する。"""
        self._task = asyncio.create_task(
            self._monitor_loop(),
            name=f"connectivity-{self._device_name}",
        )
        logger.info(
            "[%s] 通信監視開始: %s:%d  interval=%.0fs",
            self._device_name, self._host, self._port, self._poll_interval,
        )

    async def stop(self) -> None:
        """監視タスクを停止する。"""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[%s] 通信監視停止。", self._device_name)

    @property
    def is_reachable(self) -> bool | None:
        """最後に確認した到達性（None = 未チェック）。"""
        return self._reachable

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    async def _monitor_loop(self) -> None:
        while True:
            reachable = await self._check_once()
            prev = self._reachable

            if prev is None:
                # 初回チェック: 到達不能のときのみ通知
                self._reachable = reachable
                if not reachable:
                    logger.warning(
                        "[%s] 起動直後から通信不可: %s:%d",
                        self._device_name, self._host, self._port,
                    )
                    if self._on_lost:
                        await self._on_lost()
            elif reachable != prev:
                self._reachable = reachable
                if not reachable:
                    logger.warning(
                        "[%s] 通信断検知: %s:%d",
                        self._device_name, self._host, self._port,
                    )
                    if self._on_lost:
                        await self._on_lost()
                else:
                    logger.info(
                        "[%s] 通信復旧検知: %s:%d",
                        self._device_name, self._host, self._port,
                    )
                    if self._on_restored:
                        await self._on_restored()

            await asyncio.sleep(self._poll_interval)

    async def _check_once(self) -> bool:
        """management_ip:port への TCP 接続を試みて到達可能かどうかを返す。"""
        if not self._host:
            return True  # IP 未設定の場合は監視スキップ
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port),
                timeout=self._timeout,
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True
        except Exception:
            return False
