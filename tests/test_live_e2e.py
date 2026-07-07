"""Phase I 実機適用モード E2E テスト。

_run_live_apply_flow と main() の --apply-to-live 統合をテストする。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# ヘルパー関数
# ---------------------------------------------------------------------------


def _write_inventory(tmp_path: Path) -> str:
    content = """\
metadata:
  description: "E2Eテスト用インベントリ"
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
    inv_file = tmp_path / "demo.yaml"
    inv_file.write_text(content, encoding="utf-8")
    return str(inv_file)


SAMPLE_DEVICES = {
    "R1": {
        "host": "192.168.100.1", "device_type": "cisco_ios",
        "username": "admin", "password": "password", "port": 22, "apply_mode": "config_merge",
    },
    "R2": {
        "host": "192.168.100.2", "device_type": "cisco_ios",
        "username": "admin", "password": "password", "port": 22, "apply_mode": "config_merge",
    },
}

R1_BACKUP = "hostname R1\ninterface Gi0/0\n ip address 10.0.12.1 255.255.255.252\n"
R2_BACKUP = "hostname R2\ninterface Gi0/0\n ip address 10.0.12.2 255.255.255.252\n"
R1_NEW = "hostname R1\nrouter ospf 1\n network 0.0.0.0 255.255.255.255 area 0\n"
R2_NEW = "hostname R2\nrouter ospf 1\n network 0.0.0.0 255.255.255.255 area 0\n"

CML_STATE_ALL_PASS = {
    "requirement": "R1とR2をOSPFで接続する",
    "prompt_set": "demo",
    "lab_id": "lab-e2e-001",
    "device_configs": {"R1": R1_NEW, "R2": R2_NEW},
    "topology_yaml": "lab:\n  title: test\nnodes: []\nlinks: []",
    "test_results": [
        {"test": "ospf_neighbor", "result": "PASS", "detail": "2 neighbors FULL"},
        {"test": "ping_2.2.2.2", "result": "PASS", "detail": "ping OK"},
    ],
    "test_plan_items": [
        {"test_type": "ospf_neighbors", "device": "R1", "target": None, "description": "OSPF確認"},
    ],
    "final_report": "# 検証成功レポート\n\n全テスト PASS。",
}

CML_STATE_WITH_FAIL = {
    **CML_STATE_ALL_PASS,
    "test_results": [
        {"test": "ospf_neighbor", "result": "FAIL", "detail": "neighbor down"},
    ],
}


# ---------------------------------------------------------------------------
# _run_live_apply_flow のテスト
# ---------------------------------------------------------------------------


class TestRunLiveApplyFlow:
    def _mock_patches(self):
        """共通モックのコンテキストマネージャリストを返す。"""
        return [
            patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES),
            patch("agentic_ni.tools.pyats_tools.check_connectivity", return_value={"R1": True, "R2": True}),
            patch("agentic_ni.tools.pyats_tools.backup_running_config", return_value={"R1": R1_BACKUP, "R2": R2_BACKUP}),
            patch("agentic_ni.tools.pyats_tools.apply_config",
                  return_value={"device": "", "success": True, "output": "end\n", "error": ""}),
        ]

    def test_yes_decision_runs_full_flow(self, tmp_path):
        """yes 決定時: precheck → apply → report が完走すること。"""
        from agentic_ni.graph import _run_live_apply_flow

        inv_path = _write_inventory(tmp_path)
        state = dict(CML_STATE_ALL_PASS)

        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.check_connectivity", return_value={"R1": True, "R2": True}), \
             patch("agentic_ni.tools.pyats_tools.backup_running_config", return_value={"R1": R1_BACKUP, "R2": R2_BACKUP}), \
             patch("agentic_ni.tools.pyats_tools.apply_config",
                   return_value={"device": "", "success": True, "output": "end\n", "error": ""}), \
             patch("builtins.input", return_value="yes"), \
             patch("builtins.print"):
            _run_live_apply_flow(
                cml_state=state,
                prompt_set="demo",
                inventory_path=inv_path,
                live_verify=False,
            )
        # 例外なく完走すれば OK

    def test_no_decision_exits_gracefully(self, tmp_path):
        """no 決定時: 適用なしで処理が終了すること（例外なし）。"""
        from agentic_ni.graph import _run_live_apply_flow

        inv_path = _write_inventory(tmp_path)
        state = dict(CML_STATE_ALL_PASS)

        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.check_connectivity", return_value={"R1": True, "R2": True}), \
             patch("agentic_ni.tools.pyats_tools.backup_running_config", return_value={"R1": R1_BACKUP, "R2": R2_BACKUP}), \
             patch("builtins.input", side_effect=["no", ""]), \
             patch("builtins.print"):
            _run_live_apply_flow(
                cml_state=state,
                prompt_set="demo",
                inventory_path=inv_path,
                live_verify=False,
            )

    def test_rollback_only_decision(self, tmp_path):
        """rollback-only 決定時: ロールバックが実行されること。"""
        from agentic_ni.graph import _run_live_apply_flow

        inv_path = _write_inventory(tmp_path)
        state = dict(CML_STATE_ALL_PASS)

        rb_ok = {"device": "", "success": True, "output": "end\n", "error": ""}
        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.check_connectivity", return_value={"R1": True, "R2": True}), \
             patch("agentic_ni.tools.pyats_tools.backup_running_config", return_value={"R1": R1_BACKUP, "R2": R2_BACKUP}), \
             patch("agentic_ni.tools.pyats_tools.rollback_config", return_value=rb_ok), \
             patch("builtins.input", side_effect=["rollback-only", ""]), \
             patch("builtins.print"):
            _run_live_apply_flow(
                cml_state=state,
                prompt_set="demo",
                inventory_path=inv_path,
                live_verify=False,
            )

    def test_precheck_failure_exits(self, tmp_path, monkeypatch):
        """プレチェック失敗時: sys.exit(1) が呼ばれること。"""
        import sys
        from agentic_ni.graph import _run_live_apply_flow

        monkeypatch.chdir(tmp_path)  # インベントリなし

        with patch("builtins.print"), pytest.raises(SystemExit) as exc_info:
            _run_live_apply_flow(
                cml_state=CML_STATE_ALL_PASS,
                prompt_set="demo",
                inventory_path="",
                live_verify=False,
            )
        assert exc_info.value.code == 1

    def test_with_live_verify_enabled(self, tmp_path):
        """--live-verify 指定時: live_verify_node が実行されること。"""
        from agentic_ni.graph import _run_live_apply_flow

        inv_path = _write_inventory(tmp_path)
        state = dict(CML_STATE_ALL_PASS)

        pass_r = {"test": "OSPF確認", "result": "PASS", "detail": "OK"}
        apply_ok = {"device": "", "success": True, "output": "end\n", "error": ""}

        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.check_connectivity", return_value={"R1": True, "R2": True}), \
             patch("agentic_ni.tools.pyats_tools.backup_running_config", return_value={"R1": R1_BACKUP, "R2": R2_BACKUP}), \
             patch("agentic_ni.tools.pyats_tools.apply_config", return_value=apply_ok), \
             patch("agentic_ni.tools.pyats_tools.build_testbed_from_inventory", return_value="testbed: yaml"), \
             patch("agentic_ni.agents.validator._execute_test", return_value=pass_r), \
             patch("builtins.input", return_value="yes"), \
             patch("builtins.print"):
            _run_live_apply_flow(
                cml_state=state,
                prompt_set="demo",
                inventory_path=inv_path,
                live_verify=True,
            )

    def test_inventory_path_is_passed_to_state(self, tmp_path):
        """--inventory パスが正しく live apply フローに渡されること。"""
        from agentic_ni.graph import _run_live_apply_flow

        inv_path = _write_inventory(tmp_path)
        state = dict(CML_STATE_ALL_PASS)

        captured_states = []

        original_precheck = None

        def capture_precheck(s):
            captured_states.append(s)
            return {"error_log": "abort_for_test", "live_apply_records": [],
                    "final_report": "precheck failed"}

        with patch("agentic_ni.graph.live_precheck_node", side_effect=capture_precheck), \
             patch("builtins.print"), pytest.raises(SystemExit):
            _run_live_apply_flow(
                cml_state=state,
                prompt_set="demo",
                inventory_path=inv_path,
                live_verify=False,
            )

        # live_precheck_node に渡されたステートに inventory_path が設定されている
        assert captured_states
        assert captured_states[0]["live_inventory_path"] == inv_path


# ---------------------------------------------------------------------------
# main() の --apply-to-live フラグ統合テスト
# ---------------------------------------------------------------------------


class TestMainApplyToLive:
    def test_help_text_contains_apply_to_live(self):
        """ヘルプに --apply-to-live が記載されていること。"""
        import sys
        from io import StringIO
        from agentic_ni.graph import main

        with patch.object(sys, "argv", ["agentic-ni", "--help"]), \
             patch("builtins.print") as mock_print:
            main()

        all_output = " ".join(str(c) for c in mock_print.call_args_list)
        assert "--apply-to-live" in all_output
        assert "--inventory" in all_output
        assert "--live-verify" in all_output

    def test_apply_to_live_flag_skips_when_tests_fail(self, tmp_path, monkeypatch):
        """CML テストが FAIL の場合、実機適用がスキップされて sys.exit(1) すること。"""
        import sys
        from agentic_ni.graph import main

        monkeypatch.setattr(sys, "argv", ["agentic-ni", "demo", "--apply-to-live"])

        # CML フローを失敗テスト結果で返すようにモック
        fail_result = dict(CML_STATE_WITH_FAIL)

        with patch("agentic_ni.graph.load_requirement", return_value="R1とR2をOSPFで接続する"), \
             patch("agentic_ni.graph.compile_graph") as mock_compile, \
             patch("builtins.print"), \
             pytest.raises(SystemExit) as exc_info:
            mock_app = MagicMock()
            mock_app.invoke.return_value = fail_result
            mock_compile.return_value = mock_app
            main()

        assert exc_info.value.code == 1

    def test_apply_to_live_skipped_in_dry_run(self, tmp_path, monkeypatch):
        """--dry-run と --apply-to-live を同時指定した場合、実機適用はスキップされること。"""
        import sys
        from agentic_ni.graph import main

        monkeypatch.setattr(sys, "argv", ["agentic-ni", "demo", "--dry-run", "--apply-to-live"])

        dry_run_result = {
            **CML_STATE_ALL_PASS,
            "final_report": "# ドライランレポート",
        }

        run_live_called = {"n": 0}

        def fake_run_live(**kwargs):
            run_live_called["n"] += 1

        with patch("agentic_ni.graph.load_requirement", return_value="テスト要件"), \
             patch("agentic_ni.graph.compile_graph_dry_run") as mock_compile, \
             patch("agentic_ni.graph._run_live_apply_flow", side_effect=fake_run_live), \
             patch("builtins.print"):
            mock_app = MagicMock()
            mock_app.invoke.return_value = dry_run_result
            mock_compile.return_value = mock_app
            main()

        # --dry-run 時は _run_live_apply_flow が呼ばれないこと
        assert run_live_called["n"] == 0

    def test_inventory_flag_parsed_correctly(self, tmp_path, monkeypatch):
        """--inventory <path> が正しく解析されること。"""
        import sys
        from agentic_ni.graph import main

        inv_path = str(tmp_path / "prod.yaml")
        monkeypatch.setattr(sys, "argv", [
            "agentic-ni", "demo", "--apply-to-live",
            "--inventory", inv_path,
        ])

        all_pass_result = dict(CML_STATE_ALL_PASS)
        captured_kwargs = {}

        def fake_run_live(cml_state, prompt_set, inventory_path, live_verify):
            captured_kwargs["inventory_path"] = inventory_path

        with patch("agentic_ni.graph.load_requirement", return_value="テスト要件"), \
             patch("agentic_ni.graph.compile_graph") as mock_compile, \
             patch("agentic_ni.graph._run_live_apply_flow", side_effect=fake_run_live), \
             patch("builtins.print"):
            mock_app = MagicMock()
            mock_app.invoke.return_value = all_pass_result
            mock_compile.return_value = mock_app
            main()

        assert captured_kwargs.get("inventory_path") == inv_path

    def test_live_verify_flag_passed_to_flow(self, tmp_path, monkeypatch):
        """--live-verify フラグが _run_live_apply_flow に渡されること。"""
        import sys
        from agentic_ni.graph import main

        monkeypatch.setattr(sys, "argv", [
            "agentic-ni", "demo", "--apply-to-live", "--live-verify",
        ])

        all_pass_result = dict(CML_STATE_ALL_PASS)
        captured_kwargs = {}

        def fake_run_live(cml_state, prompt_set, inventory_path, live_verify):
            captured_kwargs["live_verify"] = live_verify

        with patch("agentic_ni.graph.load_requirement", return_value="テスト要件"), \
             patch("agentic_ni.graph.compile_graph") as mock_compile, \
             patch("agentic_ni.graph._run_live_apply_flow", side_effect=fake_run_live), \
             patch("builtins.print"):
            mock_app = MagicMock()
            mock_app.invoke.return_value = all_pass_result
            mock_compile.return_value = mock_app
            main()

        assert captured_kwargs.get("live_verify") is True


# ---------------------------------------------------------------------------
# compile_graph_apply_to_live — 完全 E2E テスト（全 Step 統合）
# ---------------------------------------------------------------------------


class TestPhaseIFullE2E:
    def test_complete_flow_yes_all_success(self, tmp_path):
        """Phase I 完全フロー: precheck → confirm(yes) → apply(全成功) → report。"""
        from langgraph.types import Command
        from agentic_ni.graph import compile_graph_apply_to_live, initial_state_apply_to_live

        inv_path = _write_inventory(tmp_path)
        state = initial_state_apply_to_live(
            requirement=CML_STATE_ALL_PASS["requirement"],
            lab_id=CML_STATE_ALL_PASS["lab_id"],
            inventory_path=inv_path,
            live_verify_enabled=False,
        )
        state.update({
            "device_configs": CML_STATE_ALL_PASS["device_configs"],
            "test_results": CML_STATE_ALL_PASS["test_results"],
            "test_plan_items": CML_STATE_ALL_PASS["test_plan_items"],
            "final_report": CML_STATE_ALL_PASS["final_report"],
        })

        apply_ok = {"device": "", "success": True, "output": "end\n", "error": ""}
        app = compile_graph_apply_to_live()
        thread = {"configurable": {"thread_id": "e2e-full-01"}}

        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.check_connectivity", return_value={"R1": True, "R2": True}), \
             patch("agentic_ni.tools.pyats_tools.backup_running_config",
                   return_value={"R1": R1_BACKUP, "R2": R2_BACKUP}), \
             patch("agentic_ni.tools.pyats_tools.apply_config", return_value=apply_ok):
            app.invoke(state, thread)
            result = app.invoke(Command(resume={"decision": "yes"}), thread)

        # final_report に CML レポートと実機適用レポートが両方含まれること
        assert "検証成功レポート" in result["final_report"]
        assert "実機適用レポート（Phase I）" in result["final_report"]
        assert "全 2 デバイスへの投入が成功" in result["final_report"]
        assert result["live_human_decision"] == "yes"
        assert result["error_log"] == ""

    def test_complete_flow_with_partial_failure_and_rollback(self, tmp_path):
        """Phase I: R2 失敗 → 自動ロールバック → レポートに詳細が含まれること。"""
        from langgraph.types import Command
        from agentic_ni.graph import compile_graph_apply_to_live, initial_state_apply_to_live

        inv_path = _write_inventory(tmp_path)
        state = initial_state_apply_to_live(
            requirement=CML_STATE_ALL_PASS["requirement"],
            inventory_path=inv_path,
        )
        state.update({
            "device_configs": CML_STATE_ALL_PASS["device_configs"],
            "final_report": CML_STATE_ALL_PASS["final_report"],
        })

        def apply_side(device_name, cfg, config_text):
            if device_name == "R2":
                return {"device": "R2", "success": False, "output": "", "error": "SSH Error"}
            return {"device": device_name, "success": True, "output": "end\n", "error": ""}

        rb_ok = {"device": "", "success": True, "output": "end\n", "error": ""}
        app = compile_graph_apply_to_live()
        thread = {"configurable": {"thread_id": "e2e-fail-01"}}

        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.check_connectivity", return_value={"R1": True, "R2": True}), \
             patch("agentic_ni.tools.pyats_tools.backup_running_config",
                   return_value={"R1": R1_BACKUP, "R2": R2_BACKUP}), \
             patch("agentic_ni.tools.pyats_tools.apply_config", side_effect=apply_side), \
             patch("agentic_ni.tools.pyats_tools.rollback_config", return_value=rb_ok):
            app.invoke(state, thread)
            result = app.invoke(Command(resume={"decision": "yes"}), thread)

        assert result["error_log"]
        assert "SSH Error" in result["live_report"]
        assert "ロールバック成功" in result["live_report"]
        assert "失敗詳細" in result["live_report"]

    def test_complete_flow_no_decision(self, tmp_path):
        """Phase I: no 決定 → final_report に取り消しメッセージが含まれること。"""
        from langgraph.types import Command
        from agentic_ni.graph import compile_graph_apply_to_live, initial_state_apply_to_live

        inv_path = _write_inventory(tmp_path)
        state = initial_state_apply_to_live(
            requirement=CML_STATE_ALL_PASS["requirement"],
            inventory_path=inv_path,
        )
        state["final_report"] = CML_STATE_ALL_PASS["final_report"]

        app = compile_graph_apply_to_live()
        thread = {"configurable": {"thread_id": "e2e-cancel-01"}}

        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.check_connectivity", return_value={"R1": True, "R2": True}), \
             patch("agentic_ni.tools.pyats_tools.backup_running_config",
                   return_value={"R1": R1_BACKUP, "R2": R2_BACKUP}):
            app.invoke(state, thread)
            result = app.invoke(Command(resume={"decision": "no", "reason": "凍結期間中"}), thread)

        assert "取り消し" in result["final_report"]
        assert "凍結期間中" in result["final_report"]
        assert result["live_human_decision"] == "no"

    def test_complete_flow_with_live_verify(self, tmp_path):
        """Phase I: yes → apply → live_verify → report の全フロー。"""
        from langgraph.types import Command
        from agentic_ni.graph import compile_graph_apply_to_live, initial_state_apply_to_live

        inv_path = _write_inventory(tmp_path)
        state = initial_state_apply_to_live(
            requirement=CML_STATE_ALL_PASS["requirement"],
            inventory_path=inv_path,
            live_verify_enabled=True,
        )
        state.update({
            "device_configs": CML_STATE_ALL_PASS["device_configs"],
            "test_plan_items": CML_STATE_ALL_PASS["test_plan_items"],
            "final_report": CML_STATE_ALL_PASS["final_report"],
        })

        apply_ok = {"device": "", "success": True, "output": "end\n", "error": ""}
        pass_r = {"test": "OSPF確認", "result": "PASS", "detail": "1 neighbor FULL"}
        app = compile_graph_apply_to_live()
        thread = {"configurable": {"thread_id": "e2e-verify-01"}}

        with patch("agentic_ni.tools.pyats_tools.load_inventory", return_value=SAMPLE_DEVICES), \
             patch("agentic_ni.tools.pyats_tools.check_connectivity", return_value={"R1": True, "R2": True}), \
             patch("agentic_ni.tools.pyats_tools.backup_running_config",
                   return_value={"R1": R1_BACKUP, "R2": R2_BACKUP}), \
             patch("agentic_ni.tools.pyats_tools.apply_config", return_value=apply_ok), \
             patch("agentic_ni.tools.pyats_tools.build_testbed_from_inventory", return_value="testbed: yaml"), \
             patch("agentic_ni.agents.validator._execute_test", return_value=pass_r):
            app.invoke(state, thread)
            result = app.invoke(Command(resume={"decision": "yes"}), thread)

        assert result["live_test_results"]
        assert result["live_test_results"][0]["result"] == "PASS"
        assert "実機 pyATS 検証結果" in result["live_report"]
        assert "全テスト PASS" in result["live_report"] or "PASS" in result["live_report"]

    def test_precheck_failure_aborts_flow(self, tmp_path, monkeypatch):
        """プレチェック失敗時: グラフが abort → END まで進むこと（interrupt なし）。"""
        from agentic_ni.graph import compile_graph_apply_to_live, initial_state_apply_to_live

        monkeypatch.chdir(tmp_path)  # インベントリなし
        state = initial_state_apply_to_live(requirement="テスト要件")

        app = compile_graph_apply_to_live()
        thread = {"configurable": {"thread_id": "e2e-precheck-fail-01"}}

        result = app.invoke(state, thread)

        assert result.get("error_log")
        assert "インベントリ" in result.get("final_report", "") or "エラー" in result.get("final_report", "")
