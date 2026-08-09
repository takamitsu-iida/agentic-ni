"""イベント・メッセージの重複検知モジュール。

Classes:
    MessageDeduplicator  -- バスメッセージの message_id 重複チェック。
    SyslogDeduplicator   -- syslog テキストのフィンガープリント重複チェック。
"""

from __future__ import annotations

import re
import time
from typing import Optional


# ---------------------------------------------------------------------------
# MessageDeduplicator
# ---------------------------------------------------------------------------

class MessageDeduplicator:
    """同一 message_id の二重配信を window_seconds 秒間ブロックする。"""

    def __init__(self, window_seconds: int = 300) -> None:
        self._window = window_seconds
        # message_id -> 最初に見た UNIX タイムスタンプ
        self._seen: dict[str, float] = {}

    def is_duplicate(self, message_id: str) -> bool:
        """既に処理済みの message_id なら True を返す。"""
        ts = self._seen.get(message_id)
        if ts is None:
            return False
        return (time.monotonic() - ts) < self._window

    def mark_seen(self, message_id: str) -> None:
        """message_id を処理済みとして記録する。"""
        self._seen[message_id] = time.monotonic()

    def cleanup(self) -> None:
        """期限切れエントリを削除してメモリを解放する。"""
        now = time.monotonic()
        expired = [mid for mid, ts in self._seen.items() if (now - ts) >= self._window]
        for mid in expired:
            del self._seen[mid]


# ---------------------------------------------------------------------------
# SyslogDeduplicator
# ---------------------------------------------------------------------------

# タイムスタンプ・シーケンス番号・可変カウンターを除去するパターン群
_NORMALIZE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # IOS スタイルのタイムスタンプ: *Aug  9 12:34:56.789: や %Aug  9 ...
    (re.compile(r"[*%]?\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?:\s*"), ""),
    # シーケンス番号: 000123: や 000123 :
    (re.compile(r"^\d+:\s*", re.MULTILINE), ""),
    # 特定の IP アドレスを汎化（隣接 IP 等の変動値）
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "<IP>"),
    # インターフェース番号の末尾数値を汎化（Gi0/1 → Gi*）
    (re.compile(r"((?:Gi|Fa|Te|Et|Se|Vl|Po)\w*)\d+(?:/\d+)*"), r"\1*"),
    # 純粋な数値トークン（カウンター・シーケンス）を汎化
    (re.compile(r"\b\d{4,}\b"), "<N>"),
]


def _fingerprint(raw_text: str) -> str:
    """syslog テキストから可変部分を除去した正規化フィンガープリントを返す。

    例:
        入力: "*Aug  9 12:34:56: %OSPF-5-ADJCHG: Nbr 10.0.0.2 on Gi0/1 from FULL to DOWN"
        出力: "%OSPF-5-ADJCHG: Nbr <IP> on Gi* from FULL to DOWN"
    """
    text = raw_text.strip()
    for pattern, replacement in _NORMALIZE_PATTERNS:
        text = pattern.sub(replacement, text)
    return " ".join(text.split())  # 連続空白を正規化


class SyslogDeduplicator:
    """同一障害由来の syslog を window_seconds 秒間ブロックする。

    (agent_id, fingerprint) をキーに TTL 付き辞書で管理する。
    """

    def __init__(self, window_seconds: int = 60) -> None:
        self._window = window_seconds
        # (agent_id, fingerprint) -> 最初に見た UNIX タイムスタンプ
        self._seen: dict[tuple[str, str], float] = {}

    def fingerprint(self, raw_text: str) -> str:
        """外部から参照可能なフィンガープリント計算インターフェース。"""
        return _fingerprint(raw_text)

    def is_duplicate(self, agent_id: str, raw_text: str) -> bool:
        """window_seconds 秒以内に同一フィンガープリントを処理済みなら True を返す。"""
        key = (agent_id, _fingerprint(raw_text))
        ts = self._seen.get(key)
        if ts is None:
            return False
        return (time.monotonic() - ts) < self._window

    def mark_seen(self, agent_id: str, raw_text: str) -> None:
        """フィンガープリントを処理済みとして記録する。"""
        key = (agent_id, _fingerprint(raw_text))
        self._seen[key] = time.monotonic()

    def cleanup(self) -> None:
        """期限切れエントリを削除してメモリを解放する。"""
        now = time.monotonic()
        expired = [k for k, ts in self._seen.items() if (now - ts) >= self._window]
        for k in expired:
            del self._seen[k]
