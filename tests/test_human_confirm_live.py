"""human_confirm_live_node のユニットテスト。

``interrupt()`` をモックすることで実際のグラフ一時停止なしにノードをテストする。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# ヘルパー関数
# ---------------------------------------------------------------------------


def _make_state(**overrides) -> dict:
    """最小限の AgentState 風辞書を返す。"""
    base = {
        "requirement": "R1 と R2 を OSPF で接続する",
        "prompt_set": "demo",
        "fault_simulation_enabled": False,
        "skip_deploy": False,
        "error_history": [],
        "topology_yaml": "",
        "device_configs": {"R1": "hostname R1\n", "R2": "hostname R2\n"},
        "lab_id": "lab-test-001",
        "test_results": [
            {"test": "ospf_neighbor", "result": "PASS", "detail": "2 neighbors FULL"},
            {"test": "ping_2.2.2.2",  "result": "PASS", "detail": "ping OK"},
        ],
        "test_plan_items": [],
        "error_log": "",
        "retry_count": 0,
        "fault_scenario_results": [],
        "fault_report": "",
        "troubleshoot_lab_id": "",
        "troubleshoot_issue": "",
        "collected_state": {},
        "diagnosis": "",
        "fix_records": [],
        "troubleshoot_retry_count": 0,
        "troubleshoot_report": "",
        "analyze_request": "",
        "analysis_result": "",
        "live_inventory_path": "",
        "live_apply_records": [
            {
                "device": "R1",
                "host": "192.168.100.1",
                "apply_mode": "config_merge",
                "connectivity_ok": True,
                "backup_config": "hostname R1\n",
                "backup_lines": 1,
                "applied_config": "",
                "apply_success": False,
                "apply_output": "",
                "apply_error": "",
                "rollback_done": False,
                "rollback_error": "",
            },
            {
                "device": "R2",
                "host": "192.168.100.2",
                "apply_mode": "config_merge",
                "connectivity_ok": True,
                "backup_config": "hostname R2\n",
                "backup_lines": 1,
                "applied_config": "",
                "apply_success": False,
                "apply_output": "",
                "apply_error": "",
                "rollback_done": False,
                "rollback_error": "",
            },
        ],
        "live_verify_enabled": False,
        "live_human_decision": "",
        "live_report": "",
        "final_report": "# 検証成功レポート\n\n全テスト PASS。",
    }
    base.update(overrides)
    return base


def _write_inventory(tmp_path: Path, content: str) -> str:
    inv_file = tmp_path / "demo.yaml"
    inv_file.write_text(content, encoding="utf-8")
    return str(inv_file)


VALID_INVENTORY_YAML = """\
metadata:
  description: "テスト用インベントリ"

devices:
  R1:
    host: "192.168.100.1"
    device_type: "cisco_ios"
    username: "admin"
    password: "password"
    port: 22
    apply_mode: "config_merge"
  R2:
    host: "192.168.100.2"
    device_type: "cisco_ios"
    username: "admin"
    password: "password"
    port: 22
    apply_mode: "config_merge"
"""

SAMPLE_DEVICES = {
    "R1": {
        "host": "192.168.100.1",
        "device_type": "cisco_ios",
        "username": "admin",
        "password": "password",
        "port": 22,
        "apply_mode": "config_merge",
    },
    "R2": {
        "host": "192.168.100.2",
        "device_type": "cisco_ios",
        "username": "admin",
        "password": "password",
        "port": 22,
        "apply_mode": "config_merge",
    },
}

R1_CONFIG = "hostname R1\ninterface GigabitEthernet0/0\n ip address 10.0.12.1 255.255.255.252\n"
R2_CONFIG = "hostname R2\ninterface GigabitEthernet0/0\n ip address 10.0.12.2 255.255.255.252\n"


# ---------------------------------------------------------------------------
# _build_confirmation_message のテスト
# ---------------------------------------------------------------------------


class TestBuildConfirmationMessage:
    def test_contains_device_names(self):
        from agentic_ni.graph import _build_confirmation_message

        state = _make_state()
        msg = _build_confirmation_message(state)
        assert "R1" in msg
        assert "R2" in msg

    def test_contains_host_addresses(self):
        from agentic_ni.graph import _build_confirmation_message

        state = _make_state()
        msg = _build_confirmation_message(state)
        assert "192.168.100.1" in msg
        assert "192.168.100.2" in msg

    def test_contains_apply_mode(self):
        from agentic_ni.graph import _build_confirmation_message

        state = _make_state()
        msg = _build_confirmation_message(state)
        assert "config_merge" in msg

    def test_contains_backup_lines(self):
        from agentic_ni.graph import _build_confirmation_message

        state = _make_state()
        msg = _build_confirmation_message(state)
        assert "バックアップ取得済み" in msg

    def test_contains_test_results(self):
        from agentic_ni.graph import _build_confirmation_message

        state = _make_state()
        msg = _build_confirmation_message(state)
        assert "CML テスト結果" in msg
        assert "ospf_neighbor" in msg

    def test_contains_decision_prompt(self):
        from agentic_ni.graph import _build_confirmation_message

        state = _make_state()
        msg = _build_confirmation_message(state)
        assert "yes" in msg
        assert "no" in msg
        assert "rollback-only" in msg

    def test_connectivity_ok_shows_checkmark(self):
        from agentic_ni.graph import _build_confirmation_message

        state = _make_state()
        msg = _build_confirmation_message(state)
        assert "✅" in msg

    def test_connectivity_fail_shows_cross(self):
        from agentic_ni.graph import _build_confirmation_message

        state = _make_state()
        state["live_apply_records"][0]["connectivity_ok"] = False
        msg = _build_confirmation_message(state)
        assert "❌" in msg

    def test_empty_records(self):
        from agentic_ni.graph import _build_confirmation_message

        state = _make_state(live_apply_records=[])
        msg = _build_confirmation_message(state)
        assert "続行しますか？" in msg

    def test_no_test_results(self):
        from agentic_ni.graph import _build_confirmation_message

        state = _make_state(test_results=[])
        msg = _build_confirmation_message(state)
        # テスト結果なしでも基本構造は出力される
        assert "【適用対象】" in msg
        assert "続行しますか？" in msg


# ---------------------------------------------------------------------------
# human_confirm_live_node — "yes" 決定
# ---------------------------------------------------------------------------


class TestHumanConfirmLiveNodeYes:
    def test_sets_decision_yes(self):
        from agentic_ni.graph import human_confirm_live_node

        state = _make_state()
        with patch("agentic_ni.graph.interrupt", return_value={"decision": "yes"}):
            result = human_confirm_live_node(state)

        assert result["live_human_decision"] == "yes"

    def test_does_not_modify_final_report(self):
        from agentic_ni.graph import human_confirm_live_node

        original_report = "# 検証成功レポート\n\n全テスト PASS。"
        state = _make_state(final_report=original_report)
        with patch("agentic_ni.graph.interrupt", return_value={"decision": "yes"}):
            result = human_confirm_live_node(state)

        assert "final_report" not in result  # 変更なし

    def test_yes_with_reason_is_ignored(self):
        from agentic_ni.graph import human_confirm_live_node

        state = _make_state()
        with patch("agentic_ni.graph.interrupt", return_value={"decision": "yes", "reason": "確認済み"}):
            result = human_confirm_live_node(state)

        assert result["live_human_decision"] == "yes"

    def test_yes_case_insensitive(self):
        from agentic_ni.graph import human_confirm_live_node

        state = _make_state()
        with patch("agentic_ni.graph.interrupt", return_value={"decision": "YES"}):
            result = human_confirm_live_node(state)

        assert result["live_human_decision"] == "yes"


# ---------------------------------------------------------------------------
# human_confirm_live_node — "no" 決定
# ---------------------------------------------------------------------------


class TestHumanConfirmLiveNodeNo:
    def test_sets_decision_no(self):
        from agentic_ni.graph import human_confirm_live_node

        state = _make_state()
        with patch("agentic_ni.graph.interrupt", return_value={"decision": "no"}):
            result = human_confirm_live_node(state)

        assert result["live_human_decision"] == "no"

    def test_appends_cancellation_to_final_report(self):
        from agentic_ni.graph import human_confirm_live_node

        state = _make_state()
        with patch("agentic_ni.graph.interrupt", return_value={"decision": "no"}):
            result = human_confirm_live_node(state)

        assert "final_report" in result
        assert "取り消し" in result["final_report"]
        assert "実機への設定投入は行われませんでした" in result["final_report"]

    def test_cancellation_includes_reason(self):
        from agentic_ni.graph import human_confirm_live_node

        state = _make_state()
        with patch("agentic_ni.graph.interrupt", return_value={"decision": "no", "reason": "メンテ中"}):
            result = human_confirm_live_node(state)

        assert "メンテ中" in result["final_report"]

    def test_cancellation_with_no_reason(self):
        from agentic_ni.graph import human_confirm_live_node

        state = _make_state()
        with patch("agentic_ni.graph.interrupt", return_value={"decision": "no"}):
            result = human_confirm_live_node(state)

        assert "理由なし" in result["final_report"]

    def test_preserves_existing_final_report(self):
        from agentic_ni.graph import human_confirm_live_node

        original = "# 検証成功レポート\n\n全テスト PASS。"
        state = _make_state(final_report=original)
        with patch("agentic_ni.graph.interrupt", return_value={"decision": "no"}):
            result = human_confirm_live_node(state)

        assert result["final_report"].startswith(original)

    def test_unknown_decision_treated_as_no(self):
        from agentic_ni.graph import human_confirm_live_node

        state = _make_state()
        with patch("agentic_ni.graph.interrupt", return_value={"decision": "abort"}):
            result = human_confirm_live_node(state)

        assert result["live_human_decision"] == "no"

    def test_missing_decision_treated_as_no(self):
        from agentic_ni.graph import human_confirm_live_node

        state = _make_state()
        with patch("agentic_ni.graph.interrupt", return_value={}):
            result = human_confirm_live_node(state)

        assert result["live_human_decision"] == "no"


# ---------------------------------------------------------------------------
# human_confirm_live_node — "rollback-only" 決定
# ---------------------------------------------------------------------------


class TestHumanConfirmLiveNodeRollbackOnly:
    def test_sets_decision_rollback_only(self):
        from agentic_ni.graph import human_confirm_live_node

        state = _make_state()
        with patch("agentic_ni.graph.interrupt", return_value={"decision": "rollback-only"}):
            result = human_confirm_live_node(state)

        assert result["live_human_decision"] == "rollback-only"

    def test_appends_rollback_notice_to_report(self):
        from agentic_ni.graph import human_confirm_live_node

        state = _make_state()
        with patch("agentic_ni.graph.interrupt", return_value={"decision": "rollback-only"}):
            result = human_confirm_live_node(state)

        assert "final_report" in result
        assert "rollback-only" in result["final_report"]
        assert "バックアップを使ってロールバック" in result["final_report"]

    def test_rollback_alias_rollback_only(self):
        from agentic_ni.graph import human_confirm_live_node

        state = _make_state()
        with patch("agentic_ni.graph.interrupt", return_value={"decision": "rollback"}):
            result = human_confirm_live_node(state)

        assert result["live_human_decision"] == "rollback-only"

    def test_rollback_with_reason(self):
        from agentic_ni.graph import human_confirm_live_node

        state = _make_state()
        with patch("agentic_ni.graph.interrupt", return_value={"decision": "rollback-only", "reason": "変更凍結"}):
            result = human_confirm_live_node(state)

        assert "変更凍結" in result["final_report"]


# ---------------------------------------------------------------------------
# _should_continue_after_confirm のテスト
# ---------------------------------------------------------------------------


class TestShouldContinueAfterConfirm:
    def test_yes_returns_apply(self):
        from agentic_ni.graph import _should_continue_after_confirm

        state = _make_state(live_human_decision="yes")
        assert _should_continue_after_confirm(state) == "apply"

    def test_no_returns_cancelled(self):
        from agentic_ni.graph import _should_continue_after_confirm

        state = _make_state(live_human_decision="no")
        assert _should_continue_after_confirm(state) == "cancelled"

    def test_rollback_only_returns_rollback(self):
        from agentic_ni.graph import _should_continue_after_confirm

        state = _make_state(live_human_decision="rollback-only")
        assert _should_continue_after_confirm(state) == "rollback"

    def test_empty_decision_returns_cancelled(self):
        from agentic_ni.graph import _should_continue_after_confirm

        state = _make_state(live_human_decision="")
        assert _should_continue_after_confirm(state) == "cancelled"

    def test_unknown_decision_returns_cancelled(self):
        from agentic_ni.graph import _should_continue_after_confirm

        state = _make_state(live_human_decision="maybe")
        assert _should_continue_after_confirm(state) == "cancelled"


# ---------------------------------------------------------------------------
# compile_graph_live_precheck_confirm のテスト
# ---------------------------------------------------------------------------


class TestCompileGraphLivePrecheckConfirm:
    def test_graph_compiles_successfully(self):
        from agentic_ni.graph import compile_graph_live_precheck_confirm

        app = compile_graph_live_precheck_confirm()
        assert app is not None

    def test_graph_runs_with_yes_decision(self, tmp_path):
        """precheck 成功 → human_confirm で "yes" → END。"""
        from langgraph.types import Command
        from agentic_ni.graph import compile_graph_live_precheck_confirm, initial_state_apply_to_live

        inv_path = _write_inventory(tmp_path, VALID_INVENTORY_YAML)
        state = initial_state_apply_to_live(
            requirement="テスト要件",
            lab_id="lab-001",
            inventory_path=inv_path,
        )
        state["test_results"] = [
            {"test": "ospf_neighbor", "result": "PASS", "detail": "OK"},
        ]

        app = compile_graph_live_precheck_confirm()
        thread = {"configurable": {"thread_id": "test-yes-01"}}

        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.check_connectivity", return_value={"R1": True, "R2": True}), \
             patch("agentic_ni.tools.pyats_tools.backup_running_config", return_value={"R1": R1_CONFIG, "R2": R2_CONFIG}):
            # 1回目: precheck まで実行して interrupt で停止
            result = app.invoke(state, thread)

        # interrupt で停止しているので None が返るか、interrupt の情報が含まれる
        # 次に Command で "yes" を送って再開
        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.check_connectivity", return_value={"R1": True, "R2": True}), \
             patch("agentic_ni.tools.pyats_tools.backup_running_config", return_value={"R1": R1_CONFIG, "R2": R2_CONFIG}):
            result = app.invoke(
                Command(resume={"decision": "yes"}),
                thread,
            )

        assert result["live_human_decision"] == "yes"
        assert result["error_log"] == ""

    def test_graph_runs_with_no_decision(self, tmp_path):
        """precheck 成功 → human_confirm で "no" → END（取り消しレポート）。"""
        from langgraph.types import Command
        from agentic_ni.graph import compile_graph_live_precheck_confirm, initial_state_apply_to_live

        inv_path = _write_inventory(tmp_path, VALID_INVENTORY_YAML)
        state = initial_state_apply_to_live(
            requirement="テスト要件",
            lab_id="lab-001",
            inventory_path=inv_path,
        )

        app = compile_graph_live_precheck_confirm()
        thread = {"configurable": {"thread_id": "test-no-01"}}

        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.check_connectivity", return_value={"R1": True, "R2": True}), \
             patch("agentic_ni.tools.pyats_tools.backup_running_config", return_value={"R1": R1_CONFIG, "R2": R2_CONFIG}):
            app.invoke(state, thread)
            result = app.invoke(Command(resume={"decision": "no", "reason": "作業中止"}), thread)

        assert result["live_human_decision"] == "no"
        assert "取り消し" in result["final_report"]
        assert "作業中止" in result["final_report"]

    def test_graph_aborts_on_precheck_failure(self, tmp_path, monkeypatch):
        """precheck 失敗 → abort → END（human_confirm はスキップ）。"""
        from agentic_ni.graph import compile_graph_live_precheck_confirm, initial_state_apply_to_live

        monkeypatch.chdir(tmp_path)  # インベントリなし
        state = initial_state_apply_to_live(requirement="テスト要件")

        app = compile_graph_live_precheck_confirm()
        thread = {"configurable": {"thread_id": "test-abort-01"}}
        result = app.invoke(state, thread)

        assert result.get("error_log")
        # precheck で中断されたので human_decision は未設定（空文字）
        assert result.get("live_human_decision", "") == ""

    def test_graph_runs_with_rollback_only(self, tmp_path):
        """precheck 成功 → human_confirm で "rollback-only" → END。"""
        from langgraph.types import Command
        from agentic_ni.graph import compile_graph_live_precheck_confirm, initial_state_apply_to_live

        inv_path = _write_inventory(tmp_path, VALID_INVENTORY_YAML)
        state = initial_state_apply_to_live(
            requirement="テスト要件",
            inventory_path=inv_path,
        )

        app = compile_graph_live_precheck_confirm()
        thread = {"configurable": {"thread_id": "test-rollback-01"}}

        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.check_connectivity", return_value={"R1": True, "R2": True}), \
             patch("agentic_ni.tools.pyats_tools.backup_running_config", return_value={"R1": R1_CONFIG, "R2": R2_CONFIG}):
            app.invoke(state, thread)
            result = app.invoke(Command(resume={"decision": "rollback-only"}), thread)

        assert result["live_human_decision"] == "rollback-only"
        assert "rollback-only" in result.get("final_report", "")
