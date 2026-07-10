"""architect エージェントのユニットテスト。LLM APIは不要（モック使用）。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agentic_ni.agents.architect import DesignOutput, DeviceConfig, _build_messages, run
from agentic_ni.state import AgentState, load_device_configs, write_device_configs


# ---------------------------------------------------------------------------
# テスト用ヘルパー
# ---------------------------------------------------------------------------

_SAMPLE_TOPOLOGY_YAML = """\
lab:
  title: OSPF Lab
  description: R1-R2 OSPF接続
  notes: ""
  timestamp: 0
nodes:
  - id: n0
    label: R1
    node_definition: iosv
    x: -200
    y: 0
    configuration: ""
    interfaces:
      - id: i0
        label: GigabitEthernet0/0
        slot: 0
        type: physical
  - id: n1
    label: R2
    node_definition: iosv
    x: 200
    y: 0
    configuration: ""
    interfaces:
      - id: i0
        label: GigabitEthernet0/0
        slot: 0
        type: physical
links:
  - id: l0
    n1: n0
    i1: i0
    n2: n1
    i2: i0
    label: ""
"""

_SAMPLE_CONFIGS = {
    "R1": "hostname R1\ninterface GigabitEthernet0/0\n ip address 10.0.0.1 255.255.255.252\n no shutdown\nrouter ospf 1\n router-id 1.1.1.1\n network 10.0.0.0 0.0.0.3 area 0\n",
    "R2": "hostname R2\ninterface GigabitEthernet0/0\n ip address 10.0.0.2 255.255.255.252\n no shutdown\nrouter ospf 1\n router-id 2.2.2.2\n network 10.0.0.0 0.0.0.3 area 0\n",
}

_SAMPLE_DESIGN_OUTPUT = DesignOutput(
    topology_yaml=_SAMPLE_TOPOLOGY_YAML,
    device_configs=[
        DeviceConfig(device_name="R1", config_text=_SAMPLE_CONFIGS["R1"]),
        DeviceConfig(device_name="R2", config_text=_SAMPLE_CONFIGS["R2"]),
    ],
    design_rationale="R1-R2間をOSPFエリア0で接続。10.0.0.0/30を使用。",
)


def _base_state(**overrides) -> AgentState:
    base: AgentState = {
        "requirement": "R1とR2をOSPFで接続する",
        "topology_yaml": "",
        "device_configs": {},
        "lab_id": "",
        "test_results": [],
        "error_log": "",
        "retry_count": 0,
        "final_report": "",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# DesignOutput スキーマのテスト
# ---------------------------------------------------------------------------


class TestDesignOutput:
    def test_valid_schema_creation(self):
        output = DesignOutput(
            topology_yaml="lab:\n  title: test\n",
            device_configs=[DeviceConfig(device_name="R1", config_text="hostname R1\n")],
            design_rationale="テスト設計",
        )
        assert output.topology_yaml == "lab:\n  title: test\n"
        assert output.device_configs[0].device_name == "R1"
        assert output.device_configs[0].config_text == "hostname R1\n"
        assert output.design_rationale == "テスト設計"

    def test_device_configs_can_be_empty(self):
        output = DesignOutput(
            topology_yaml="lab:\n  title: test\n",
            device_configs=[],
            design_rationale="空の設計",
        )
        assert output.device_configs == []

    def test_schema_has_expected_fields(self):
        fields = DesignOutput.model_fields
        assert "topology_yaml" in fields
        assert "device_configs" in fields
        assert "design_rationale" in fields


# ---------------------------------------------------------------------------
# _build_messages のテスト
# ---------------------------------------------------------------------------


class TestBuildMessages:
    def test_zero_design_mode_when_no_error_log(self):
        state = _base_state(requirement="R1とR2をOSPFで接続")
        messages = _build_messages(state)

        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        # ゼロ設計モード: 修正依頼の文言が含まれない
        assert "設計依頼" in messages[1]["content"]
        assert "修正依頼" not in messages[1]["content"]
        assert "R1とR2をOSPFで接続" in messages[1]["content"]

    def test_correction_mode_when_error_log_present(self):
        state = _base_state(
            requirement="R1とR2をOSPFで接続",
            topology_yaml=_SAMPLE_TOPOLOGY_YAML,
            device_configs=_SAMPLE_CONFIGS,
            error_log="OSPFエリア番号のミスマッチ: R1はarea 0, R2はarea 1になっている",
        )
        messages = _build_messages(state)

        assert messages[1]["role"] == "user"
        content = messages[1]["content"]
        assert "修正依頼" in content
        assert "OSPFエリア番号のミスマッチ" in content
        assert "前回のトポロジーYAML" in content
        assert "前回の機器コンフィグ" in content

    def test_system_prompt_loaded_from_file(self):
        state = _base_state()
        messages = _build_messages(state)
        # architect_system.md の内容が含まれることを確認
        assert "CCIE" in messages[0]["content"]
        assert "CML" in messages[0]["content"]

    def test_correction_mode_includes_both_configs(self):
        state = _base_state(
            error_log="エラーあり",
            device_configs={"R1": "config R1", "R2": "config R2"},
        )
        messages = _build_messages(state)
        content = messages[1]["content"]
        assert "R1" in content
        assert "R2" in content


# ---------------------------------------------------------------------------
# run() のテスト
# ---------------------------------------------------------------------------


class TestArchitectRun:
    def _mock_llm(self, return_value: DesignOutput) -> MagicMock:
        """structured_llm.invoke() が return_value を返すモックを構築する。"""
        mock_structured = MagicMock()
        mock_structured.invoke.return_value = return_value
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        return mock_llm

    def test_run_returns_topology_and_configs(self, tmp_path, monkeypatch):
        """Strategy E: device_config_paths が設定され、ファイルが書き出されること。"""
        # write_device_configs がファイルを書き出す先を tmp_path に向ける
        monkeypatch.chdir(tmp_path)
        state = _base_state(requirement="R1とR2をOSPFで接続")
        mock_llm = self._mock_llm(_SAMPLE_DESIGN_OUTPUT)

        with patch("agentic_ni.agents.architect.get_llm", return_value=mock_llm):
            result = run(state)

        # topology_yaml はラボ名が書き換えられること
        assert "agentic-ni-" in result["topology_yaml"]
        # Strategy E: device_configs は空になる
        assert result["device_configs"] == {}
        # Strategy E: device_config_paths にパスが設定される
        assert "R1" in result["device_config_paths"]
        assert "R2" in result["device_config_paths"]
        # 実際にファイルが書き出されること
        r1_path = Path(result["device_config_paths"]["R1"])
        assert r1_path.exists()
        assert r1_path.read_text(encoding="utf-8") == _SAMPLE_CONFIGS["R1"]

    def test_load_device_configs_reads_from_paths(self, tmp_path, monkeypatch):
        """load_device_configs が device_config_paths からファイルを読み込めること。"""
        monkeypatch.chdir(tmp_path)
        # ファイルを書き出してパスを取得
        paths = write_device_configs(_SAMPLE_CONFIGS, "demo")
        # device_configs が空でも device_config_paths からロードできること
        state = _base_state(device_configs={}, device_config_paths=paths)
        loaded = load_device_configs(state)
        assert loaded["R1"] == _SAMPLE_CONFIGS["R1"]
        assert loaded["R2"] == _SAMPLE_CONFIGS["R2"]

    def test_load_device_configs_fallback_to_memory(self):
        """device_config_paths が空の場合は device_configs からフォールバックすること。"""
        state = _base_state(device_configs=_SAMPLE_CONFIGS)
        loaded = load_device_configs(state)
        assert loaded["R1"] == _SAMPLE_CONFIGS["R1"]

    def test_run_clears_error_log(self):
        """修正設計出力後にerror_logがクリアされること。"""
        state = _base_state(
            error_log="前回のエラー",
            topology_yaml=_SAMPLE_TOPOLOGY_YAML,
        )
        mock_llm = self._mock_llm(_SAMPLE_DESIGN_OUTPUT)

        with patch("agentic_ni.agents.architect.get_llm", return_value=mock_llm):
            result = run(state)

        assert result["error_log"] == ""

    def test_run_calls_with_structured_output(self):
        """LLMがwith_structured_output(DesignOutput)で呼ばれること。"""
        state = _base_state()
        mock_llm = self._mock_llm(_SAMPLE_DESIGN_OUTPUT)

        with patch("agentic_ni.agents.architect.get_llm", return_value=mock_llm):
            run(state)

        mock_llm.with_structured_output.assert_called_once_with(DesignOutput, method="function_calling")

    def test_run_zero_design_sends_correct_message_count(self):
        """LLMに渡すメッセージが2件（system + user）であること。"""
        state = _base_state(requirement="OSPFテスト", error_log="")
        mock_llm = self._mock_llm(_SAMPLE_DESIGN_OUTPUT)

        with patch("agentic_ni.agents.architect.get_llm", return_value=mock_llm):
            run(state)

        mock_structured = mock_llm.with_structured_output.return_value
        call_args = mock_structured.invoke.call_args[0][0]
        assert len(call_args) == 2
        assert call_args[0]["role"] == "system"
        assert call_args[1]["role"] == "user"

    def test_run_correction_mode_includes_error_log_in_prompt(self):
        """差分修正モードでエラーログがプロンプトに含まれること。"""
        error_msg = "OSPFエリア番号ミスマッチ"
        state = _base_state(
            error_log=error_msg,
            topology_yaml=_SAMPLE_TOPOLOGY_YAML,
            device_configs=_SAMPLE_CONFIGS,
        )
        mock_llm = self._mock_llm(_SAMPLE_DESIGN_OUTPUT)

        with patch("agentic_ni.agents.architect.get_llm", return_value=mock_llm):
            run(state)

        mock_structured = mock_llm.with_structured_output.return_value
        messages = mock_structured.invoke.call_args[0][0]
        user_content = messages[1]["content"]
        assert error_msg in user_content
