"""Ubuntu ノードで SYSLOG を受信してデバイスエージェントへブロードキャストするサーバー。

ネットワーク装置から UDP で送られてくる RFC 3164 形式の SYSLOG を受信し、
オーケストレーター経由で全エージェントに SyslogEvent を配布する。

各エージェントは受信した SYSLOG が自分の担当装置からのものか判定し、
関係ある場合のみ調査を開始する。

ネットワーク装置側の設定例（Cisco IOS）::

    logging host <ubuntu_ip>
    logging trap informational
    service timestamps log datetime msec

使用方法::

    server = SyslogServer(orchestrator=orch, host="0.0.0.0", port=514)
    await server.start()
    ...
    await server.stop()

注意:
    UDP 514 のバインドには root 権限が必要。
    テスト時は --syslog-port 5140 等の非特権ポートを使用すること。
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from agentic_ni.logger import get_logger

if TYPE_CHECKING:
    from agentic_ni.distributed.orchestrator import AgentOrchestrator

logger = get_logger(__name__)

# RFC 3164: <priority>timestamp hostname message
# 例: <190>Aug 10 12:34:56 R1 %OSPF-5-ADJCHG: Process 1, Nbr 10.0.0.2 to DOWN
_RFC3164_RE = re.compile(
    r"^<(\d{1,3})>"                            # <priority>
    r"(\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})"  # timestamp
    r"\s+(\S+)"                                # hostname
    r"\s+(.+)$",                               # message
    re.DOTALL,
)

# Cisco IOS 独自形式 (logging origin-id hostname 有効時):
# <priority>seq: hostname: [*]timestamp: message
# 例: <189>38: Leaf1: *Aug 11 05:37:33.194: %LINEPROTO-5-UPDOWN: ...
_CISCO_IOS_RE = re.compile(
    r"^<(\d{1,3})>"                                       # <priority>
    r"\d+:\s+"                                            # seq_no:
    r"(\S+):\s+"                                          # hostname:
    r"[*.]?"                                              # optional * (時刻未同期マーク)
    r"(\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?)"  # timestamp (小数点以下も許容)
    r":\s+"                                               # :
    r"(.+)$",                                             # message
    re.DOTALL,
)

# 重大度レベル数値 → 文字列（RFC 5424 準拠）
_SEVERITY_NAMES = ["emerg", "alert", "crit", "err", "warning", "notice", "info", "debug"]


# ---------------------------------------------------------------------------
# パース結果
# ---------------------------------------------------------------------------

@dataclass
class ParsedSyslog:
    """RFC 3164 パース結果。"""

    raw: str
    source_hostname: str
    facility: int
    severity: int
    severity_name: str
    timestamp_str: str
    message: str


def parse_rfc3164(data: bytes) -> ParsedSyslog | None:
    """RFC 3164 SYSLOG メッセージをパースする。

    パース失敗時は None を返す。
    プライオリティなし（rsyslog 転送など）の簡易フォーマットも許容する。
    """
    try:
        raw = data.decode("utf-8", errors="replace").strip()
    except Exception:
        return None

    m = _RFC3164_RE.match(raw)
    if m:
        priority = int(m.group(1))
        facility = priority >> 3
        severity = priority & 0x07
        return ParsedSyslog(
            raw=raw,
            source_hostname=m.group(3),
            facility=facility,
            severity=severity,
            severity_name=_SEVERITY_NAMES[severity] if severity < 8 else "unknown",
            timestamp_str=m.group(2),
            message=m.group(4),
        )

    # Cisco IOS 形式: <priority>seq: hostname: [*]timestamp: message
    m = _CISCO_IOS_RE.match(raw)
    if m:
        priority = int(m.group(1))
        facility = priority >> 3
        severity = priority & 0x07
        return ParsedSyslog(
            raw=raw,
            source_hostname=m.group(2),
            facility=facility,
            severity=severity,
            severity_name=_SEVERITY_NAMES[severity] if severity < 8 else "unknown",
            timestamp_str=m.group(3),
            message=m.group(4),
        )

    # プライオリティなし簡易フォーマット: "hostname message"
    parts = raw.split(None, 1)
    if len(parts) >= 2:
        return ParsedSyslog(
            raw=raw,
            source_hostname=parts[0],
            facility=23,   # local7
            severity=6,    # info
            severity_name="info",
            timestamp_str="",
            message=parts[1],
        )

    return None


# ---------------------------------------------------------------------------
# asyncio UDP プロトコル
# ---------------------------------------------------------------------------

class _SyslogUDPProtocol(asyncio.DatagramProtocol):
    """asyncio UDP プロトコル実装。受信データをコールバックに渡す。"""

    def __init__(self, on_receive) -> None:
        self._on_receive = on_receive
        self._transport: asyncio.BaseTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._transport = transport

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        parsed = parse_rfc3164(data)
        if parsed is not None:
            asyncio.create_task(self._on_receive(parsed, addr))
        else:
            logger.debug("SYSLOG パース失敗 from %s: %r", addr[0], data[:80])

    def error_received(self, exc: Exception) -> None:
        logger.warning("SyslogServer UDP エラー: %s", exc)

    def connection_lost(self, exc: Exception | None) -> None:
        if exc:
            logger.warning("SyslogServer 接続断: %s", exc)


# ---------------------------------------------------------------------------
# SyslogServer
# ---------------------------------------------------------------------------

class SyslogServer:
    """UDP SYSLOG を受信して全デバイスエージェントにブロードキャストするサーバー。

    Args:
        orchestrator:  SyslogEvent をブロードキャストする先の AgentOrchestrator。
        host:          リッスンアドレス（デフォルト 0.0.0.0）。
        port:          リッスンポート（デフォルト 514。1024以下は root 権限が必要）。
    """

    def __init__(
        self,
        orchestrator: "AgentOrchestrator",
        host: str = "0.0.0.0",
        port: int = 514,
    ) -> None:
        self._orchestrator = orchestrator
        self._host = host
        self._port = port
        self._transport: asyncio.BaseTransport | None = None

    async def start(self) -> None:
        """UDP ソケットをバインドしてリッスンを開始する。"""
        loop = asyncio.get_running_loop()
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: _SyslogUDPProtocol(self._on_syslog),
            local_addr=(self._host, self._port),
        )
        logger.info("SyslogServer: UDP %s:%d でリッスン開始", self._host, self._port)

    async def stop(self) -> None:
        """UDP ソケットをクローズする。"""
        if self._transport:
            self._transport.close()
            self._transport = None
        logger.info("SyslogServer: 停止しました。")

    async def _on_syslog(self, parsed: ParsedSyslog, addr: tuple) -> None:
        """受信した SYSLOG をパースして全エージェントにブロードキャストする。"""
        logger.info(
            "SYSLOG受信 [%s] %s: %s",
            addr[0], parsed.source_hostname, parsed.message[:100],
        )
        await self._orchestrator.broadcast_syslog_to_all(
            source_hostname=parsed.source_hostname,
            raw_msg=parsed.message,
            severity=parsed.severity_name,
        )


# ---------------------------------------------------------------------------
# SyslogFileWatcher
# ---------------------------------------------------------------------------

class SyslogFileWatcher:
    """rsyslog が書き出したログファイルを tail -F して SyslogEvent を配布する。

    UDP ポートを直接バインドしないため root 権限が不要。
    ログローテーション（inode 変化）にも対応。

    Args:
        orchestrator:   SyslogEvent をブロードキャストする先の AgentOrchestrator。
        path:           監視対象のログファイルパス。
        poll_interval:  新着行のポーリング間隔（秒）。
    """

    def __init__(
        self,
        orchestrator: "AgentOrchestrator",
        path: str,
        poll_interval: float = 0.5,
    ) -> None:
        self._orchestrator = orchestrator
        self._path = Path(path)
        self._poll_interval = poll_interval
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(
            self._tail_loop(), name="syslog-file-watcher"
        )
        logger.info("SyslogFileWatcher: %s の監視開始", self._path)

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("SyslogFileWatcher: 停止しました。")

    async def _tail_loop(self) -> None:
        while not self._path.exists():
            logger.debug("SyslogFileWatcher: ファイル待機中: %s", self._path)
            await asyncio.sleep(self._poll_interval)

        inode = self._path.stat().st_ino
        fp = open(self._path, "r", encoding="utf-8", errors="replace")  # noqa: SIM115
        fp.seek(0, 2)  # 起動前のログをスキップ

        try:
            while True:
                line = fp.readline()
                if line:
                    await self._dispatch(line.rstrip("\n\r"))
                    continue

                await asyncio.sleep(self._poll_interval)
                try:
                    new_inode = self._path.stat().st_ino
                except FileNotFoundError:
                    continue
                if new_inode != inode:
                    fp.close()
                    fp = open(self._path, "r", encoding="utf-8", errors="replace")  # noqa: SIM115
                    inode = new_inode
                    logger.info("SyslogFileWatcher: ログローテーション検知、再オープン")
        finally:
            fp.close()

    async def _dispatch(self, line: str) -> None:
        if not line:
            return
        parsed = parse_rfc3164(line.encode())
        if parsed is None:
            logger.debug("SyslogFileWatcher: パース失敗: %r", line[:80])
            return
        logger.info(
            "SYSLOG受信 [file] %s: %s",
            parsed.source_hostname, parsed.message[:100],
        )
        await self._orchestrator.broadcast_syslog_to_all(
            source_hostname=parsed.source_hostname,
            raw_msg=parsed.message,
            severity=parsed.severity_name,
        )
