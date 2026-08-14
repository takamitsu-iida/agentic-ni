"""agentic-ni-ubuntu: CML 内 Ubuntu ノード上でデバイスエージェントを起動する CLI。

CML 内の Ubuntu ノード上で実行することを想定しています。
ネットワーク装置から SYSLOG を UDP で受信し、全デバイスエージェントへブロードキャストします。
各エージェントは自分の担当装置からの SYSLOG を検知したら調査を開始します。
エージェントは SSH（pyATS）で各装置に直接接続します。

アーキテクチャ::

    CML 内ネットワーク装置 → SYSLOG UDP → Ubuntu (本プログラム)
                                                      |
                                          ┌───────────┴───────────┐
                                          │  Agent-R1 Agent-R2 …  │
                                          │  (自分宛SYSLOGのみ処理) │
                                          └───────────┬───────────┘
                                                      |
                                          SSH/pyATS → 各装置

ネットワーク装置側の事前設定（全装置に適用）::

    logging host <ubuntu_ip>
    logging trap informational
    service timestamps log datetime msec

使用方法::

    # ファイル監視モード（rsyslog 経由、root 不要）
    agentic-ni-ubuntu \\
        --topology configs/demo2/topology.yaml \\
        --syslog-file /var/log/network-syslog.log \\
        --testbed testbed.yaml

    # 基本（UDP 514 受信、root 権限が必要）
    sudo agentic-ni-ubuntu \\
        --topology configs/demo2/topology.yaml \\
        --testbed testbed.yaml

    # テスト用（非特権ポート + モックツール）
    agentic-ni-ubuntu \\
        --topology configs/demo2/topology.yaml \\
        --syslog-port 5140 \\
        --mock-tools

    # SYSLOG 手動送信によるテスト（別ターミナルから）
    echo '<190>Aug 10 12:34:56 R1 %OSPF-5-ADJCHG: Nbr 10.0.0.2 to DOWN' | \\
        nc -u -w1 127.0.0.1 5140
"""

from __future__ import annotations

import asyncio
import argparse
import signal
import sys
from pathlib import Path
from typing import Any

from pathlib import Path as _Path

from agentic_ni.distributed.bus import create_bus
from agentic_ni.distributed.device_tools import DeviceToolkit, MockDeviceToolkit
from agentic_ni.distributed.message import AgentMessage
from agentic_ni.distributed.orchestrator import AgentOrchestrator
from agentic_ni.distributed.syslog_server import SyslogFileWatcher, SyslogServer
from agentic_ni.logger import configure_logging, get_logger

logger = get_logger(__name__)

# src/agentic_ni/distributed/ → プロジェクトルート → trouble_shooting/configs/
_CONFIGS_DIR = _Path(__file__).parent.parent.parent.parent / "trouble_shooting" / "configs"

# ---------------------------------------------------------------------------
# ANSI カラー
# ---------------------------------------------------------------------------

_RESET   = "\033[0m"
_BOLD    = "\033[1m"
_DIM     = "\033[2m"
_CYAN    = "\033[96m"
_YELLOW  = "\033[93m"
_GREEN   = "\033[92m"
_RED     = "\033[91m"
_MAGENTA = "\033[95m"
_BLUE    = "\033[94m"
_WHITE   = "\033[97m"

_USE_COLOR = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"{code}{text}{_RESET}" if _USE_COLOR else text


# ---------------------------------------------------------------------------
# Human エスカレーション表示
# ---------------------------------------------------------------------------

def _make_human_handler(shutdown_event: asyncio.Event):
    async def handle_human_message(msg: AgentMessage) -> None:
        print(f"\n{_c(_BOLD + _RED, '━' * 60)}")
        print(_c(_BOLD + _RED, f"  [{msg.from_agent} → HUMAN]"))
        print(msg.content)
        print(_c(_BOLD + _RED, "━" * 60))
    return handle_human_message


# ---------------------------------------------------------------------------
# ステータス表示
# ---------------------------------------------------------------------------

def _print_rag_status() -> None:
    """RAG 知識ベースの統計を表示する。"""
    try:
        from agentic_ni.tools import rag_tools
        stats = rag_tools.get_knowledge_stats()
    except Exception:  # noqa: BLE001
        stats = {"available": False, "total_chunks": 0, "source_files": []}

    if not stats["available"]:
        print(f"  RAG               : {_c(_DIM, '無効 (chromadb 未インストール  uv sync --extra rag)')}")
        return

    chunks: int = stats["total_chunks"]
    files: list[str] = stats["source_files"]
    if chunks == 0:
        print(f"  RAG               : {_c(_YELLOW, '索引なし')}  "
              f"{_c(_DIM, '(agentic-ni --rag-index で構築)')}")
    else:
        print(f"  RAG               : {_c(_GREEN, f'{chunks} チャンク')}  "
              f"({len(files)} ファイル: {', '.join(files)})")


def _print_status(orchestrator: AgentOrchestrator, syslog_source: str) -> None:
    print(f"\n{_c(_BOLD, '=' * 60)}")
    print(_c(_BOLD + _GREEN, "  agentic-ni-ubuntu: エージェント起動完了"))
    print(_c(_BOLD, "=" * 60))
    print(f"  SYSLOG ソース     : {syslog_source}")
    print(f"  稼働エージェント  : {orchestrator.agent_count()} 台")
    for agent_id, agent in orchestrator.get_all_agents().items():
        neighbors = list(agent._memory.neighbor_map.keys())
        print(f"  {_c(_CYAN, agent_id)} ({agent._device_type})")
        if neighbors:
            print(f"    隣接: {neighbors}")
    _print_rag_status()
    print(_c(_BOLD, "=" * 60))
    print(_c(_DIM, "\n  ネットワーク装置から SYSLOG を受信すると自動的に調査を開始します。"))
    print(_c(_DIM, "  終了するには Ctrl+C を押してください。"))
    if sys.stdin.isatty():
        print()
        print(_c(_BOLD, "  ── 対話コマンド ──"))
        print(f"  {_c(_CYAN, '  <装置名>: <指示・質問>')}  例: R1: 現在のOSPFネイバー状態を確認してください")
        print(f"  {_c(_DIM, '  入力例: R1: show ip route  /  R2: コンフィグのBGP設定を見せて')}")
    print()


async def _check_and_print_connectivity(orchestrator: AgentOrchestrator) -> None:
    """全装置への TCP 到達性を確認して結果を表示する。"""
    results = await orchestrator.check_all_connectivity(timeout=5.0)
    if not results:
        return
    print(f"{_c(_BOLD, '  ── 起動時接続確認 (TCP port 22) ──')}")
    for agent_id, (ok, msg) in sorted(results.items()):
        mark = _c(_GREEN, "✓") if ok else _c(_RED, "✗")
        color = _GREEN if ok else _RED
        print(f"  {mark}  {_c(_CYAN, agent_id)}: {_c(color, msg)}")
    print()


async def _capture_and_print_baselines(orchestrator: AgentOrchestrator) -> None:
    """全装置から DesiredState を自動取得して結果を表示する。"""
    print(f"{_c(_BOLD, '  ── DesiredState 自動取得（正常状態スナップショット）──')}")
    print(_c(_DIM, "  show ip interface brief / ospf neighbor / bgp summary を実行中..."))
    results = await orchestrator.capture_all_baselines(timeout=15.0)
    if not results:
        print(_c(_DIM, "  （ツール設定なし、スキップ）"))
        print()
        return
    for agent_id, status in sorted(results.items()):
        if status.startswith("ok"):
            mark = _c(_GREEN, "✓")
            detail = _c(_DIM, status[3:])  # "(IF:3 NB:2)" 部分
            print(f"  {mark}  {_c(_CYAN, agent_id)}: ベースライン取得完了 {detail}")
        elif status == "skipped":
            print(f"  {_c(_DIM, '─')}  {_c(_CYAN, agent_id)}: {_c(_DIM, 'スキップ（モック）')}")
        elif status == "timeout":
            print(f"  {_c(_RED, '✗')}  {_c(_CYAN, agent_id)}: {_c(_YELLOW, 'タイムアウト（ベースラインなし）')}")
        else:
            print(f"  {_c(_RED, '✗')}  {_c(_CYAN, agent_id)}: {_c(_RED, status)}")
    print()


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main() -> None:
    """agentic-ni-ubuntu コマンドのエントリポイント。"""
    parser = argparse.ArgumentParser(
        description="Ubuntu ノード上でデバイスエージェントを起動して SYSLOG を監視する",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "例:\n"
            "  # --config でトポロジ・テストベッドを自動解決（推奨）\n"
            "  agentic-ni-ubuntu --config clos\n\n"
            "  # パスを直接指定\n"
            "  agentic-ni-ubuntu \\\n"
            "      --topology trouble_shooting/configs/clos/topology.yaml \\\n"
            "      --testbed trouble_shooting/configs/clos/testbed.yaml\n\n"
            "  # UDP 直接受信（root 権限が必要）\n"
            "  sudo agentic-ni-ubuntu --config clos --syslog-file ''\n\n"
            "  # テスト（モックツール + 非特権ポート）\n"
            "  agentic-ni-ubuntu --config clos --syslog-port 5140 --mock-tools\n\n"
            "対話コマンド（起動後に入力）:\n"
            "  <装置名>: <指示・質問>\n"
            "  例: R1: 現在のOSPFネイバー状態を確認してください\n"
            "  例: R2: show ip bgp summary の結果を教えて\n"
            "  例: R1: GigabitEthernet0/1 がダウンしている原因を調べて\n\n"
            "RAG 知識ベース:\n"
            "  rag/ ディレクトリのファイルを索引化すると、エージェントが障害診断時に\n"
            "  プロトコルガイドやトラブルシューティング知見を参照して精度が上がります。\n"
            "  索引構築: agentic-ni --rag-index\n"
            "  確認方法: 起動時のステータス表示で RAG 統計が表示されます。\n"
        ),
    )
    parser.add_argument(
        "--config",
        help=(
            "設定名（例: clos）。trouble_shooting/configs/<config>/ から "
            "topology.yaml と testbed.yaml を自動解決する。"
            "--topology / --testbed と同時指定不可。"
        ),
    )
    parser.add_argument(
        "--topology",
        help="topology.yaml のパス（--config 未使用時に指定）",
    )
    parser.add_argument(
        "--testbed",
        help=(
            "pyATS testbed YAML のパス（--config 未使用時に指定）。"
            "省略時はツールなしで起動（SYSLOG 受信のみ、show コマンド不可）。"
        ),
    )
    parser.add_argument(
        "--mock-tools", action="store_true",
        help="モックツールを使用する（SSH/pyATS 不要のオフラインテスト用）",
    )
    parser.add_argument(
        "--syslog-host", default="0.0.0.0",
        help="SYSLOG リッスンアドレス（デフォルト: 0.0.0.0）",
    )
    parser.add_argument(
        "--syslog-port", type=int, default=514,
        help="SYSLOG リッスンポート（デフォルト: 514。1024以下は root 権限が必要）",
    )
    parser.add_argument(
        "--syslog-file", default="/var/log/network-syslog.log",
        help=(
            "rsyslog が書き出したログファイルのパス（デフォルト: /var/log/network-syslog.log）。"
            "root 権限不要。UDP 直接受信に切り替える場合は --syslog-file '' を指定。"
        ),
    )
    parser.add_argument(
        "--bus", default="memory", choices=["memory", "mqtt", "nats"],
        help="エージェント間メッセージバスのバックエンド（デフォルト: memory）",
    )
    parser.add_argument(
        "--bus-host", default="localhost",
        help="MQTT/NATS ブローカーのホスト名（--bus=mqtt/nats 時に使用）",
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="ログレベル（デフォルト: INFO）",
    )
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    args = parser.parse_args()

    # --config からトポロジ・テストベッドパスを解決する
    if args.config:
        if args.topology or args.testbed:
            parser.error("--config と --topology / --testbed は同時に指定できません")
        config_dir = _CONFIGS_DIR / args.config
        if not config_dir.is_dir():
            parser.error(f"設定ディレクトリが見つかりません: {config_dir}")
        args.topology = str(config_dir / "topology.yaml")
        testbed_path = config_dir / "testbed.yaml"
        if testbed_path.exists() and not args.testbed:
            args.testbed = str(testbed_path)
    elif not args.topology:
        parser.error("--config または --topology のいずれかを指定してください")

    # .env が読み取れなければ早期終了
    from dotenv import find_dotenv, load_dotenv
    _env_file = find_dotenv(usecwd=True)
    if not _env_file:
        print(_c(_BOLD + _RED, "[ERROR] .env ファイルが見つかりません。"), file=sys.stderr)
        print("  LLM の API キーを .env に設定してください:", file=sys.stderr)
        print("    cp .env.example .env", file=sys.stderr)
        print("    # .env を開いて OPENAI_API_KEY などを設定する", file=sys.stderr)
        sys.exit(1)
    try:
        load_dotenv(_env_file)
    except OSError as e:
        print(_c(_BOLD + _RED, f"[ERROR] .env を読み込めませんでした: {e}"), file=sys.stderr)
        sys.exit(1)

    configure_logging(verbose=(args.log_level == "DEBUG"), quiet=(args.log_level in ("WARNING", "ERROR")))
    asyncio.run(_async_main(args))


async def _async_main(args: Any) -> None:
    """非同期メイン処理。"""
    # Bus の生成と接続
    bus_kwargs: dict = {}
    if args.bus == "mqtt":
        bus_kwargs = {"host": args.bus_host}
    elif args.bus == "nats":
        bus_kwargs = {"url": f"nats://{args.bus_host}:4222"}

    bus = create_bus(args.bus, **bus_kwargs)
    await bus.connect()

    # ツールキットファクトリー
    # DeviceToolkit は pyATS で装置に直接 SSH する（testbed.yaml が必要）
    testbed_yaml: str | None = None
    if args.testbed:
        testbed_yaml = Path(args.testbed).read_text(encoding="utf-8")

    def toolkit_factory(device_name: str):
        if args.mock_tools:
            return MockDeviceToolkit(device_name)
        if not testbed_yaml:
            return None  # testbed 未指定時はツールなし（SYSLOG 受信のみ）
        return DeviceToolkit(
            device_name=device_name,
            testbed_yaml=testbed_yaml,
        )

    # オーケストレーター起動
    orchestrator = AgentOrchestrator(
        bus=bus,
        toolkit_factory=toolkit_factory,
    )

    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _on_sigint() -> None:
        print(f"\n{_c(_YELLOW, '  Ctrl+C を受信しました。シャットダウンしています...')}")
        shutdown_event.set()

    loop.add_signal_handler(signal.SIGINT, _on_sigint)

    # SYSLOG ソース（ファイル監視 or UDP 直接受信）
    if args.syslog_file:
        syslog_source = SyslogFileWatcher(orchestrator=orchestrator, path=args.syslog_file)
        syslog_source_str = f"ファイル {args.syslog_file}"
    else:
        syslog_source = SyslogServer(
            orchestrator=orchestrator,
            host=args.syslog_host,
            port=args.syslog_port,
        )
        syslog_source_str = f"UDP {args.syslog_port}"

    try:
        await orchestrator.start_from_topology(args.topology)
        await syslog_source.start()
        _print_status(orchestrator, syslog_source_str)
        await _check_and_print_connectivity(orchestrator)
        if not args.mock_tools:
            await _capture_and_print_baselines(orchestrator)

        tasks: list[asyncio.Task] = [
            asyncio.create_task(
                orchestrator.run_approval_loop(shutdown_event), name="approval"
            ),
            asyncio.create_task(
                _drain_human_responses(orchestrator, shutdown_event), name="human-responses"
            ),
        ]
        if sys.stdin.isatty():
            tasks.append(asyncio.create_task(
                _run_human_input_loop(orchestrator, shutdown_event), name="human-input"
            ))
            print(f"{_c(_BOLD + _GREEN, '  ✓ 準備完了 — 人間からの指示を受け付けています')}")
            print(f"  {_c(_DIM, '入力形式: <装置名>: <指示・質問>　例) R1: OSPFネイバーを確認してください')}")
            print()
        await shutdown_event.wait()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    finally:
        await syslog_source.stop()
        await orchestrator.stop_all()
        await bus.close()
        print(_c(_GREEN, "  シャットダウン完了。"))


async def _drain_human_responses(
    orchestrator: AgentOrchestrator, shutdown_event: asyncio.Event
) -> None:
    """human_queue からエージェントの回答を取り出して表示する。"""
    while not shutdown_event.is_set():
        try:
            msg = await asyncio.wait_for(orchestrator.human_queue.get(), timeout=0.5)
        except asyncio.TimeoutError:
            continue
        from_agent = msg.get("from_agent", "?")
        content = msg.get("content", "")
        width = 60
        print(f"\n{_c(_BOLD + _GREEN, '─' * width)}")
        print(f"{_c(_BOLD + _GREEN, '  💬  エージェント応答')}  {_c(_CYAN + _BOLD, from_agent)}")
        print(_c(_BOLD + _GREEN, "─" * width))
        for line in content.strip().splitlines():
            print(f"  {line}")
        print(_c(_BOLD + _GREEN, "─" * width))
        print()
        orchestrator.human_queue.task_done()


async def _run_human_input_loop(
    orchestrator: AgentOrchestrator, shutdown_event: asyncio.Event
) -> None:
    """標準入力から人間コマンドを読み取り、対応するエージェントに送る。

    入力フォーマット: ``<装置名>: <指示・質問>``
    例: ``R1: 現在の OSPF ネイバー状態を確認してください``
    """
    import select as _select
    loop = asyncio.get_running_loop()
    while not shutdown_event.is_set():
        try:
            ready = await loop.run_in_executor(
                None, lambda: _select.select([sys.stdin], [], [], 0.5)[0]
            )
        except (asyncio.CancelledError, OSError, ValueError):
            return
        if not ready:
            continue
        try:
            raw = sys.stdin.readline()
        except (EOFError, OSError):
            break
        if not raw:
            break  # EOF
        line = raw.strip()
        if not line:
            continue

        if ":" not in line:
            print(
                f"  {_c(_RED, '[!]')} フォーマットエラー: "
                f"'{_c(_BOLD, '<装置名>: <指示>')}' の形式で入力してください。"
            )
            continue

        device_name, _, request = line.partition(":")
        device_name = device_name.strip()
        request = request.strip()
        if not request:
            continue

        try:
            await orchestrator.send_human_command(device_name, request)
            print(
                f"  {_c(_DIM, '→')} {_c(_BOLD, f'Agent-{device_name}')} に送信しました: "
                f"{_c(_DIM, request[:60])}"
            )
        except KeyError as exc:
            print(f"  {_c(_RED, '[!]')} {exc}")
