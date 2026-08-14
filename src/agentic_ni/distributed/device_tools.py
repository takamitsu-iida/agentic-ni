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
import re
from collections.abc import Sequence
from typing import Any, Callable

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

    async def check_connectivity(self, timeout: float = 5.0) -> tuple[bool, str]:
        """testbed.yaml の接続先 IP に TCP 接続を試みて到達性を確認する。"""
        ip, port = self._get_management_ip()
        if not ip:
            return False, "管理 IP が不明"
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, port),
                timeout=timeout,
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True, f"{ip}:{port} 到達可能"
        except Exception as exc:
            return False, f"{ip}:{port} 到達不可 ({type(exc).__name__})"

    def _get_management_ip(self) -> tuple[str, int]:
        """testbed.yaml から接続先 IP とポートを返す。

        ループバック系の接続名（loopback / lo / lb など）を優先し、
        該当がなければ先頭の接続を返す。
        """
        if not self._testbed_yaml:
            return "", 22
        import yaml as _yaml
        data = _yaml.safe_load(self._testbed_yaml)
        device_data = data.get("devices", {}).get(self._device_name, {})
        connections = device_data.get("connections", {})

        loopback_result: tuple[str, int] | None = None
        fallback_result: tuple[str, int] | None = None
        for name, conn in connections.items():
            ip = conn.get("ip", "")
            port = conn.get("port", 22)
            if not ip:
                continue
            entry = (str(ip), int(port))
            name_lower = name.lower()
            # ループバック系の接続名を優先
            if loopback_result is None and (
                "loopback" in name_lower or name_lower in {"lo", "lo0", "lb", "lb0"}
            ):
                loopback_result = entry
            elif fallback_result is None:
                fallback_result = entry

        return loopback_result or fallback_result or ("", 22)

    def run_show_direct(self, command: str) -> str:
        """show コマンドを直接実行してテキスト出力を返す（LangChain ツールラッパーなし）。"""
        _assert_testbed(self._testbed_yaml, self._device_name)
        from agentic_ni.tools.pyats_tools import run_show_command
        result = run_show_command(self._testbed_yaml, self._device_name, command)
        return result.get("raw_output", json.dumps(result, ensure_ascii=False))

    def capture_baseline_direct(self) -> "DesiredState":
        """1 回の pyATS セッションで全ベースライン show を実行して DesiredState を返す。

        run_show_direct を 3 回呼ぶと接続を 3 回確立してタイムアウトしやすいため、
        このメソッドでは接続 1 回で全コマンドを実行する。
        """
        _assert_testbed(self._testbed_yaml, self._device_name)
        from agentic_ni.tools.pyats_tools import _load_testbed, _connect_device

        testbed = _load_testbed(self._testbed_yaml)
        device = _connect_device(testbed, self._device_name)

        def _run(command: str) -> str:
            try:
                output = device.parse(command)
                return output.get("raw_output", json.dumps(output, ensure_ascii=False))
            except Exception:
                return device.execute(command)

        try:
            return capture_desired_state(_run)
        finally:
            device.disconnect()

    # ------------------------------------------------------------------
    # Read-Only ツール生成
    # ------------------------------------------------------------------

    def _make_run_show(self) -> StructuredTool:
        device_name = self._device_name
        testbed_yaml = self._testbed_yaml

        def run_show(command: str) -> str:
            """任意の show コマンドを実行してテキスト出力を返す。"""
            _assert_testbed(testbed_yaml, device_name)
            # 大量出力コマンドは RateLimit の原因になるため禁止
            _blocked = ("show tech-support", "show tech support")
            if command.strip().lower().startswith(_blocked):
                return (
                    f"コマンド '{command}' は出力が大きすぎるため禁止されています。"
                    "具体的なサブコマンド（show interfaces, show ip bgp summary 等）を使用してください。"
                )
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
            result = run_show_command(testbed_yaml, device_name, "show ip interface brief")
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

    async def check_connectivity(self, timeout: float = 5.0) -> tuple[bool, str]:
        return True, "モック（チェックなし）"

    def run_show_direct(self, command: str) -> str:
        """モック用 show コマンド直接実行。"""
        return self._show_responses.get(
            command,
            f"[{self._device_name}] (mock) show output for: {command}",
        )

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

    def run_show_direct(self, command: str) -> str:
        """CML 経由で show コマンドを直接実行してテキスト出力を返す。"""
        return self._run(command)

    def make_log_poller(
        self,
        agent: Any,
        poll_interval: float = 10.0,
        command: str = "show logging",
        ignore_patterns: Sequence[str] | None = None,
    ) -> "DeviceLogPoller":
        """このツールキットが管理する装置のログポーラーを生成して返す。"""
        from agentic_ni.distributed.log_poller import DeviceLogPoller
        from agentic_ni.distributed.syslog_server import DEFAULT_IGNORE_PATTERNS
        return DeviceLogPoller(
            device_name=self._device_name,
            run_command=self._run,
            agent=agent,
            poll_interval=poll_interval,
            command=command,
            ignore_patterns=DEFAULT_IGNORE_PATTERNS if ignore_patterns is None else ignore_patterns,
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


# ---------------------------------------------------------------------------
# DesiredState 自動生成
# ---------------------------------------------------------------------------

# show ip interface brief の各行を解析する正規表現
# 例: "GigabitEthernet0/0  10.0.12.1  YES NVRAM  up  up"
_IFACE_BRIEF_RE = re.compile(
    r"^(\S+)\s+\S+\s+\S+\s+\S+\s+(administratively\s+down|up|down)\s+(up|down)",
    re.MULTILINE | re.IGNORECASE,
)

# show ip ospf neighbor の各行を解析する正規表現（Dead Time を anchor として使う）
# 例: "2.2.2.2  1  FULL/  -  00:00:36  10.0.12.2  GigabitEthernet0/0"
_OSPF_NEIGHBOR_RE = re.compile(
    r"^(\d[\d.]+)\s+\d+\s+\S.*?\s+\d{2}:\d{2}:\d{2}\s+(\d[\d.]+)\s+(\S+)",
    re.MULTILINE,
)

# show ip bgp summary の確立済みネイバー行を解析する正規表現
# 末尾が数値（受信プレフィックス数）なら Established
# 例: "2.2.2.2  4  65000  8  8  5  0  0  00:05:21  0"
_BGP_ESTABLISHED_RE = re.compile(
    r"^(\d[\d.]+)\s+\d+\s+(\d+)\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+\S+\s+(\d+)\s*$",
    re.MULTILINE,
)


def capture_desired_state(run_show_fn: Callable[[str], str]) -> "DesiredState":
    """show コマンドの結果から DesiredState を自動生成する。

    Args:
        run_show_fn: コマンド文字列を受け取り出力文字列を返す同期 callable。
                     DeviceToolkit.run_show_direct がそのまま使える。

    Returns:
        DesiredState: 現在の稼働状態をベースラインとして構築した期待状態。
    """
    from agentic_ni.distributed.memory import DesiredState

    interfaces: dict[str, str] = {}
    routing_neighbors: list[str] = []

    # ── インターフェース状態 ──────────────────────────────────────────
    try:
        output = run_show_fn("show ip interface brief")
        for m in _IFACE_BRIEF_RE.finditer(output):
            intf = m.group(1)
            line_status = m.group(2).lower()
            proto_status = m.group(3).lower()
            if "administratively" in line_status:
                continue  # shutdown 状態は期待状態に含めない
            if proto_status == "up":
                interfaces[intf] = "up"
    except Exception:
        pass

    # ── OSPF ネイバー ─────────────────────────────────────────────────
    try:
        output = run_show_fn("show ip ospf neighbor")
        for m in _OSPF_NEIGHBOR_RE.finditer(output):
            neighbor_id, addr, intf = m.group(1), m.group(2), m.group(3)
            routing_neighbors.append(f"OSPF {neighbor_id} via {intf} (addr: {addr})")
    except Exception:
        pass

    # ── BGP ネイバー（Established のみ）──────────────────────────────
    try:
        output = run_show_fn("show ip bgp summary")
        for m in _BGP_ESTABLISHED_RE.finditer(output):
            neighbor, asn = m.group(1), m.group(2)
            routing_neighbors.append(f"BGP neighbor {neighbor} AS{asn}")
    except Exception:
        pass

    return DesiredState(
        interfaces=interfaces,
        routing_neighbors=routing_neighbors,
        notes="起動時スナップショットから自動生成",
    )
