"""Scale-2-4: EventDeduplicator のユニットテスト。"""

from __future__ import annotations

import time

import pytest

from agentic_ni.distributed.dedup import MessageDeduplicator, SyslogDeduplicator


# ---------------------------------------------------------------------------
# MessageDeduplicator
# ---------------------------------------------------------------------------

class TestMessageDeduplicator:
    def test_first_occurrence_not_duplicate(self):
        dedup = MessageDeduplicator(window_seconds=60)
        assert not dedup.is_duplicate("msg-001")

    def test_second_occurrence_is_duplicate(self):
        dedup = MessageDeduplicator(window_seconds=60)
        dedup.mark_seen("msg-001")
        assert dedup.is_duplicate("msg-001")

    def test_different_id_not_duplicate(self):
        dedup = MessageDeduplicator(window_seconds=60)
        dedup.mark_seen("msg-001")
        assert not dedup.is_duplicate("msg-002")

    def test_expired_entry_not_duplicate(self, monkeypatch):
        """window を過ぎたエントリは重複とみなさないこと。"""
        dedup = MessageDeduplicator(window_seconds=10)
        dedup.mark_seen("msg-001")

        original_monotonic = time.monotonic

        def fast_forward():
            return original_monotonic() + 11

        monkeypatch.setattr(time, "monotonic", fast_forward)
        assert not dedup.is_duplicate("msg-001")

    def test_cleanup_removes_expired(self, monkeypatch):
        dedup = MessageDeduplicator(window_seconds=10)
        dedup.mark_seen("msg-001")
        dedup.mark_seen("msg-002")

        original_monotonic = time.monotonic

        def fast_forward():
            return original_monotonic() + 11

        monkeypatch.setattr(time, "monotonic", fast_forward)
        dedup.cleanup()
        assert len(dedup._seen) == 0

    def test_cleanup_keeps_fresh_entries(self, monkeypatch):
        dedup = MessageDeduplicator(window_seconds=60)
        dedup.mark_seen("msg-fresh")
        dedup.cleanup()
        assert "msg-fresh" in dedup._seen


# ---------------------------------------------------------------------------
# SyslogDeduplicator — フィンガープリント
# ---------------------------------------------------------------------------

class TestSyslogFingerprint:
    def test_timestamp_removed(self):
        dedup = SyslogDeduplicator()
        fp1 = dedup.fingerprint("*Aug  9 12:34:56.789: %OSPF-5-ADJCHG: Nbr 10.0.0.2 Down")
        fp2 = dedup.fingerprint("*Aug  9 13:00:00.000: %OSPF-5-ADJCHG: Nbr 10.0.0.2 Down")
        assert fp1 == fp2

    def test_different_severity_different_fingerprint(self):
        dedup = SyslogDeduplicator()
        fp1 = dedup.fingerprint("%OSPF-5-ADJCHG: Nbr 10.0.0.2 Down")
        fp2 = dedup.fingerprint("%BGP-3-NOTIFICATION: Nbr 10.0.0.2 Down")
        assert fp1 != fp2

    def test_ip_address_generalized(self):
        dedup = SyslogDeduplicator()
        fp1 = dedup.fingerprint("%OSPF-5-ADJCHG: Nbr 10.0.0.2 from FULL to DOWN")
        fp2 = dedup.fingerprint("%OSPF-5-ADJCHG: Nbr 192.168.1.1 from FULL to DOWN")
        assert fp1 == fp2

    def test_interface_number_generalized(self):
        dedup = SyslogDeduplicator()
        fp1 = dedup.fingerprint("%LINEPROTO-5-UPDOWN: Line protocol on Gi0/0 changed to down")
        fp2 = dedup.fingerprint("%LINEPROTO-5-UPDOWN: Line protocol on Gi0/1 changed to down")
        assert fp1 == fp2


# ---------------------------------------------------------------------------
# SyslogDeduplicator — 重複検知
# ---------------------------------------------------------------------------

class TestSyslogDeduplicator:
    SYSLOG = "*Aug  9 12:34:56: %OSPF-5-ADJCHG: Process 1, Nbr 10.0.0.2 from FULL to DOWN"

    def test_first_occurrence_not_duplicate(self):
        dedup = SyslogDeduplicator(window_seconds=60)
        assert not dedup.is_duplicate("Agent-R1", self.SYSLOG)

    def test_second_occurrence_within_window_is_duplicate(self):
        dedup = SyslogDeduplicator(window_seconds=60)
        dedup.mark_seen("Agent-R1", self.SYSLOG)
        assert dedup.is_duplicate("Agent-R1", self.SYSLOG)

    def test_different_agent_not_duplicate(self):
        """同一フィンガープリントでもエージェントが異なれば重複とみなさないこと。"""
        dedup = SyslogDeduplicator(window_seconds=60)
        dedup.mark_seen("Agent-R1", self.SYSLOG)
        assert not dedup.is_duplicate("Agent-R2", self.SYSLOG)

    def test_different_fingerprint_not_duplicate(self):
        """異なる障害（異なるフィンガープリント）は重複とみなさないこと。"""
        dedup = SyslogDeduplicator(window_seconds=60)
        dedup.mark_seen("Agent-R1", self.SYSLOG)
        other = "%BGP-3-NOTIFICATION: peer 10.0.0.3 went DOWN"
        assert not dedup.is_duplicate("Agent-R1", other)

    def test_expired_entry_not_duplicate(self, monkeypatch):
        """window を過ぎた後は同一 syslog を重複とみなさないこと。"""
        dedup = SyslogDeduplicator(window_seconds=10)
        dedup.mark_seen("Agent-R1", self.SYSLOG)

        original_monotonic = time.monotonic

        def fast_forward():
            return original_monotonic() + 11

        monkeypatch.setattr(time, "monotonic", fast_forward)
        assert not dedup.is_duplicate("Agent-R1", self.SYSLOG)

    def test_cleanup_removes_expired(self, monkeypatch):
        dedup = SyslogDeduplicator(window_seconds=10)
        dedup.mark_seen("Agent-R1", self.SYSLOG)
        dedup.mark_seen("Agent-R2", self.SYSLOG)

        original_monotonic = time.monotonic

        def fast_forward():
            return original_monotonic() + 11

        monkeypatch.setattr(time, "monotonic", fast_forward)
        dedup.cleanup()
        assert len(dedup._seen) == 0

    def test_both_independent_faults_processed(self):
        """2 種類の独立した障害がどちらも処理されること（片方が重複にならないこと）。"""
        dedup = SyslogDeduplicator(window_seconds=60)
        fault_a = "%OSPF-5-ADJCHG: Nbr 10.0.0.2 from FULL to DOWN"
        fault_b = "%BGP-3-NOTIFICATION: Nbr 10.0.0.3 Notification received"

        assert not dedup.is_duplicate("Agent-R1", fault_a)
        dedup.mark_seen("Agent-R1", fault_a)

        assert not dedup.is_duplicate("Agent-R1", fault_b)
        dedup.mark_seen("Agent-R1", fault_b)

        # fault_a の 2 回目は重複
        assert dedup.is_duplicate("Agent-R1", fault_a)
        # fault_b の 2 回目は重複
        assert dedup.is_duplicate("Agent-R1", fault_b)
