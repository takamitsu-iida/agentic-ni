"""装置アクセスツール群（Read-Only + Human 承認ゲート付き Write）。

クラス:
  DeviceToolkit      : pyATS 経由で実機に接続するツールキット
  MockDeviceToolkit  : テスト・オフライン用のモックツールキット

使用方法::

    toolkit = DeviceToolkit(
        device_name="R1",
        testbed_yaml=testbed_yaml_str,
        human_queue=approval_queue,
        readonly=True,         # デフォルト: 環境変数 DEVICE_AGENT_READONLY
    )
    agent = DeviceAgent(..., tools=toolkit.get_tools())

セキュリティ:
  - 環境変数 ``DEVICE_AGENT_READONLY=true``（デフォルト）のとき、
    Write ツール（apply_config）は ``get_tools()`` に含まれない。
  - ``DEVICE_AGENT_READONLY=false`` に設定しても、apply_config は
    実際には設定を投入せず human_queue に積むだけ（Human-in-the-Loop）。
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from agentic_ni.logger import get_logger

logger = get_logger(__name__)

# デフォルトは Read-Only（明示的に "false" にしない限り有効）
_DEFAULT_READONLY = os.getenv("DEVICE_AGENT_READONLY", "true").lower() != "false"


# ---------------------------------------------------------------------------
# 入力スキーマ
# ---------------------------------------------------------------------------

class _RunShowInput(BaseModel):
    command: str = Field(description="実行する show コマンド（例: 'show ip ospf neighbor'）")


class _ApplyConfigInput(BaseModel):
    commands: str = Field(
        description="configure terminal に流すコマンド（複数行可）。"
        "例: 'router ospf 1\\n network 10.0.0.0 0.0.0.255 area 0'"
    )


# ---------------------------------------------------------------------------
# DeviceToolkit
# ---------------------------------------------------------------------------

class DeviceToolkit:
    """1 台のネットワーク装置に対応する LangChain ツールキット。

    pyATS/Genie 経由で show コマンドを実行する Read-Only ツール群と、
    Human 承認ゲート付きの Write ツール（apply_config）を提供する。
    pyATS は遅延 import のため、未インストール環境でも import 自体は通る。
    """

    def __init__(
        self,
        device_name: str,
        testbed_yaml: str | None = None,
        human_queue: asyncio.Queue | None = None,
        readonly: bool = _DEFAULT_READONLY,
    ) -> None:
        self._device_name = device_name
        self._testbed_yaml = testbed_yaml
        self._human_queue = human_queue
        self._readonly = readonly

    def get_tools(self) -> list[StructuredTool]:
        """DeviceAgent の bind_tools() に渡すツールリストを返す。"""
        tools: list[StructuredTool] = [
            self._make_run_show(),
            self._make_get_running_config(),
            self._make_get_interface_status(),
            self._make_get_routing_table(),
        ]
        if not self._readonly:
            tools.append(self._make_apply_config())
        return tools

    # ------------------------------------------------------------------
    # Read-Only ツール生成
    # ------------------------------------------------------------------

    def _make_run_show(self) -> StructuredTool:
        device_name = self._device_name
        testbed_yaml = self._testbed_yaml

        def run_show(command: str) -> str:
            """任意の show コマンドを実行してテキスト出力を返す。"""
            _assert_testbed(testbed_yaml, device_name)
            from agentic_ni.tools.pyats_tools import run_show_command
            result = run_show_command(testbed_yaml, device_name, command)
            return result.get("raw_output", json.dumps(result, ensure_ascii=False))

        return StructuredTool.from_function(
            func=run_show,
            name="run_show",
            description=f"[{device_name}] 任意の show コマンドを実行してテキスト出力を返す。",
            args_schema=_RunShowInput,
        )

    def _make_get_running_config(self) -> StructuredTool:
        device_name = self._device_name
        testbed_yaml = self._testbed_yaml

        def get_running_config() -> str:
            """装置の running-config をテキストで取得して返す。"""
            _assert_testbed(testbed_yaml, device_name)
            from agentic_ni.tools.pyats_tools import run_show_command
            result = run_show_command(testbed_yaml, device_name, "show running-config")
            return result.get("raw_output", str(result))

        return StructuredTool.from_function(
            func=get_running_config,
            name="get_running_config",
            description=f"[{device_name}] running-config をテキストで取得して返す。",
        )

    def _make_get_interface_status(self) -> StructuredTool:
        device_name = self._device_name
        testbed_yaml = self._testbed_yaml

        def get_interface_status() -> str:
            """全インターフェースの状態一覧（up/down, speed 等）を取得して返す。"""
            _assert_testbed(testbed_yaml, device_name)
            from agentic_ni.tools.pyats_tools import run_show_command
            result = run_show_command(testbed_yaml, device_name, "show interfaces brief")
            return result.get("raw_output", json.dumps(result, ensure_ascii=False))

        return StructuredTool.from_function(
            func=get_interface_status,
            name="get_interface_status",
            description=f"[{device_name}] 全インターフェースの状態一覧を取得して返す。",
        )

    def _make_get_routing_table(self) -> StructuredTool:
        device_name = self._device_name
        testbed_yaml = self._testbed_yaml

        def get_routing_table() -> str:
            """ルーティングテーブル（show ip route）を取得して返す。"""
            _assert_testbed(testbed_yaml, device_name)
            from agentic_ni.tools.pyats_tools import run_show_command
            result = run_show_command(testbed_yaml, device_name, "show ip route")
            return result.get("raw_output", json.dumps(result, ensure_ascii=False))

        return StructuredTool.from_function(
            func=get_routing_table,
            name="get_routing_table",
            description=f"[{device_name}] ルーティングテーブル（show ip route）を取得して返す。",
        )

    # ------------------------------------------------------------------
    # Write ツール生成（Human 承認ゲート付き）
    # ------------------------------------------------------------------

    def _make_apply_config(self) -> StructuredTool:
        device_name = self._device_name
        human_queue = self._human_queue

        def apply_config(commands: str) -> str:
            """設定変更リクエストを Human 承認キューに積む。実際の変更は承認後に実施される。"""
            if human_queue is None:
                raise PermissionError(
                    f"[{device_name}] human_queue が未設定のため設定変更できません。"
                )
            # put_nowait はイベントループ内の同期コードから呼び出し可能
            human_queue.put_nowait({
                "type": "config_change_request",
                "device": device_name,
                "commands": commands,
            })
            return (
                f"[{device_name}] 設定変更リクエストを承認キューに積みました。"
                f"管理者の承認後に適用されます。\n投入コマンド:\n{commands}"
            )

        return StructuredTool.from_function(
            func=apply_config,
            name="apply_config",
            description=(
                f"[{device_name}] 設定変更を Human 承認キューに積む。"
                "実際の変更は管理者の承認後に実施される。"
            ),
            args_schema=_ApplyConfigInput,
        )


# ---------------------------------------------------------------------------
# MockDeviceToolkit — テスト・オフライン用
# ---------------------------------------------------------------------------

class MockDeviceToolkit:
    """pyATS/CML 不要のモックツールキット。

    テストおよびオフライン開発で使用する。
    ``show_responses`` に ``{コマンド: レスポンス}`` を渡してレスポンスを制御できる。
    """

    def __init__(
        self,
        device_name: str,
        show_responses: dict[str, str] | None = None,
        running_config: str = "! mock running-config\n",
        interface_status: str = "GigabitEthernet0/0  up    up\n",
        routing_table: str = "C    10.0.0.0/24 is directly connected, GigabitEthernet0/0\n",
        human_queue: asyncio.Queue | None = None,
        readonly: bool = True,
    ) -> None:
        self._device_name = device_name
        self._show_responses = show_responses or {}
        self._running_config = running_config
        self._interface_status = interface_status
        self._routing_table = routing_table
        self._human_queue = human_queue
        self._readonly = readonly

    def get_tools(self) -> list[StructuredTool]:
        """モックツールのリストを返す。"""
        tools: list[StructuredTool] = [
            self._make_run_show(),
            self._make_get_running_config(),
            self._make_get_interface_status(),
            self._make_get_routing_table(),
        ]
        if not self._readonly:
            tools.append(self._make_apply_config())
        return tools

    def _make_run_show(self) -> StructuredTool:
        device_name = self._device_name
        tk = self  # 動的更新できるよう self 参照をクロージャに渡す

        def run_show(command: str) -> str:
            return tk._show_responses.get(command, f"[{device_name}] (mock) show output for: {command}")

        return StructuredTool.from_function(
            func=run_show,
            name="run_show",
            description=f"[{device_name}] 任意の show コマンドを実行してテキスト出力を返す。",
            args_schema=_RunShowInput,
        )

    def _make_get_running_config(self) -> StructuredTool:
        device_name = self._device_name
        tk = self

        def get_running_config() -> str:
            return tk._running_config

        return StructuredTool.from_function(
            func=get_running_config,
            name="get_running_config",
            description=f"[{device_name}] running-config をテキストで取得して返す。",
        )

    def _make_get_interface_status(self) -> StructuredTool:
        device_name = self._device_name
        tk = self

        def get_interface_status() -> str:
            # show_responses を優先参照（攻撃エージェントによる動的書き換えに対応）
            return tk._show_responses.get("show interfaces brief", tk._interface_status)

        return StructuredTool.from_function(
            func=get_interface_status,
            name="get_interface_status",
            description=f"[{device_name}] 全インターフェースの状態一覧を取得して返す。",
        )

    def _make_get_routing_table(self) -> StructuredTool:
        device_name = self._device_name
        tk = self

        def get_routing_table() -> str:
            return tk._show_responses.get("show ip route", tk._routing_table)

        return StructuredTool.from_function(
            func=get_routing_table,
            name="get_routing_table",
            description=f"[{device_name}] ルーティングテーブル（show ip route）を取得して返す。",
        )

    def _make_apply_config(self) -> StructuredTool:
        device_name = self._device_name
        tk = self

        def apply_config(commands: str) -> str:
            if tk._human_queue is None:
                raise PermissionError(
                    f"[{device_name}] human_queue が未設定のため設定変更できません。"
                )
            tk._human_queue.put_nowait({
                "type": "config_change_request",
                "device": device_name,
                "commands": commands,
            })
            return f"[{device_name}] 設定変更リクエストをキューに積みました。"

        return StructuredTool.from_function(
            func=apply_config,
            name="apply_config",
            description=f"[{device_name}] 設定変更を Human 承認キューに積む。",
            args_schema=_ApplyConfigInput,
        )

    def update_show_responses(self, responses: dict[str, str]) -> None:
        """show コマンドのレスポンスを動的に更新する（AttackAgent から呼び出す）。"""
        self._show_responses.update(responses)

    def set_running_config(self, config: str) -> None:
        self._running_config = config


# ---------------------------------------------------------------------------
# CMLDeviceToolkit — CML 組み込み pyATS 経由（testbed YAML 不要）
# ---------------------------------------------------------------------------

class CMLDeviceToolkit:
    """virl2_client の run_pyats_command を使って CML ノードに直接コマンドを実行するツールキット。

    pyATS testbed YAML の作成が不要。CML_URL / CML_USERNAME / CML_PASSWORD
    の環境変数だけで動作する。
    """

    def __init__(
        self,
        device_name: str,
        lab_id: str,
        human_queue: asyncio.Queue | None = None,
        readonly: bool = _DEFAULT_READONLY,
    ) -> None:
        self._device_name = device_name
        self._lab_id = lab_id
        self._human_queue = human_queue
        self._readonly = readonly
        self._cached_client = None
        self._cached_lab = None  # lab と pyATS testbed をキャッシュ

    def _get_node(self):
        """CML から対応ノードオブジェクトを取得する（sync_testbed は初回のみ）。"""
        import os
        from agentic_ni.tools.cml_tools import _get_client, _get_lab
        if self._cached_lab is None:
            self._cached_client = _get_client()
            self._cached_lab = _get_lab(self._cached_client, self._lab_id)
            username = os.getenv("CML_USERNAME", "")
            password = os.getenv("CML_PASSWORD", "")
            self._cached_lab.pyats.sync_testbed(username, password)
        self._cached_lab.sync_states()
        for node in self._cached_lab.nodes():
            if node.label == self._device_name:
                return node
        raise RuntimeError(
            f"[{self._device_name}] ラボ {self._lab_id} にノードが見つかりません。"
        )

    def _run(self, command: str) -> str:
        node = self._get_node()
        try:
            return node.run_pyats_command(command)
        except Exception as exc:
            # PyatsNotInstalled は文字列比較で判定（import 不要）
            if type(exc).__name__ == "PyatsNotInstalled":
                msg = (
                    "ERROR: pyATS が実行環境にインストールされていません。"
                    " 実行環境で 'uv sync --extra network' を実行してください。"
                )
                logger.error("[%s] %s", self._device_name, msg)
                raise RuntimeError(msg) from None
            logger.error(
                "[%s] run_pyats_command(%r) 失敗: %s: %s",
                self._device_name, command, type(exc).__name__, exc,
                exc_info=True,
            )
            raise

    def get_tools(self) -> list[StructuredTool]:
        tools: list[StructuredTool] = [
            self._make_run_show(),
            self._make_get_running_config(),
            self._make_get_interface_status(),
            self._make_get_routing_table(),
        ]
        if not self._readonly:
            tools.append(self._make_apply_config())
        return tools

    def _make_run_show(self) -> StructuredTool:
        tk = self

        def run_show(command: str) -> str:
            return tk._run(command)

        return StructuredTool.from_function(
            func=run_show,
            name="run_show",
            description=f"[{self._device_name}] 任意の show コマンドを実行してテキスト出力を返す。",
            args_schema=_RunShowInput,
        )

    def _make_get_running_config(self) -> StructuredTool:
        tk = self

        def get_running_config() -> str:
            return tk._run("show running-config")

        return StructuredTool.from_function(
            func=get_running_config,
            name="get_running_config",
            description=f"[{self._device_name}] running-config をテキストで取得して返す。",
        )

    def _make_get_interface_status(self) -> StructuredTool:
        tk = self

        def get_interface_status() -> str:
            return tk._run("show interfaces")

        return StructuredTool.from_function(
            func=get_interface_status,
            name="get_interface_status",
            description=f"[{self._device_name}] 全インターフェースの状態一覧を取得して返す。",
        )

    def _make_get_routing_table(self) -> StructuredTool:
        tk = self

        def get_routing_table() -> str:
            return tk._run("show ip route")

        return StructuredTool.from_function(
            func=get_routing_table,
            name="get_routing_table",
            description=f"[{self._device_name}] ルーティングテーブル（show ip route）を取得して返す。",
        )

    def _make_apply_config(self) -> StructuredTool:
        device_name = self._device_name
        human_queue = self._human_queue

        def apply_config(commands: str) -> str:
            if human_queue is None:
                raise PermissionError(
                    f"[{device_name}] human_queue が未設定のため設定変更できません。"
                )
            human_queue.put_nowait({
                "type": "config_change_request",
                "device": device_name,
                "commands": commands,
            })
            return (
                f"[{device_name}] 設定変更リクエストを承認キューに積みました。"
                f"管理者の承認後に適用されます。\n投入コマンド:\n{commands}"
            )

        return StructuredTool.from_function(
            func=apply_config,
            name="apply_config",
            description=(
                f"[{device_name}] 設定変更を Human 承認キューに積む。"
                "実際の変更は管理者の承認後に実施される。"
            ),
            args_schema=_ApplyConfigInput,
        )


# ---------------------------------------------------------------------------
# ファクトリー
# ---------------------------------------------------------------------------

def create_device_toolkit(
    device_name: str,
    testbed_yaml: str | None = None,
    human_queue: asyncio.Queue | None = None,
    readonly: bool = _DEFAULT_READONLY,
    mock: bool = False,
    **mock_kwargs: Any,
) -> DeviceToolkit | MockDeviceToolkit:
    """ツールキットを生成するファクトリー関数。

    Args:
        device_name:  装置名。
        testbed_yaml: pyATS テストベッド YAML（mock=False 時に使用）。
        human_queue:  Human 承認キュー。
        readonly:     True の場合 apply_config をツールに含めない。
        mock:         True の場合 MockDeviceToolkit を返す（テスト用）。
        **mock_kwargs: MockDeviceToolkit に追加で渡すキーワード引数。
    """
    if mock:
        return MockDeviceToolkit(
            device_name=device_name,
            human_queue=human_queue,
            readonly=readonly,
            **mock_kwargs,
        )
    return DeviceToolkit(
        device_name=device_name,
        testbed_yaml=testbed_yaml,
        human_queue=human_queue,
        readonly=readonly,
    )


# ---------------------------------------------------------------------------
# 内部ヘルパー
# ---------------------------------------------------------------------------

def _assert_testbed(testbed_yaml: str | None, device_name: str) -> None:
    if testbed_yaml is None:
        raise RuntimeError(
            f"[{device_name}] testbed_yaml が設定されていません。"
            "DeviceToolkit の初期化時に testbed_yaml を指定してください。"
        )
