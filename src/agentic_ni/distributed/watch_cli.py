"""agentic-ni-watch: CML をリアルタイム監視して障害検知でエージェントを自動起動する CLI。

CML でリンクまたはノードを停止すると、ウォッチャーが検知して対応するエージェントに
syslog イベントを注入し、エージェントが自律的に診断を開始します。

使用方法::

    # CML 経由で実機に show コマンドを実行する（推奨）
    agentic-ni-watch --lab-id <lab_id> \\
        --topology configs/demo-large/topology.yaml

    # show コマンドはモック、LLM は実際に呼ぶ（CML 接続不要のオフライン確認用）
    agentic-ni-watch --lab-id <lab_id> \\
        --topology configs/demo-large/topology.yaml \\
        --mock-tools
"""

from __future__ import annotations

import asyncio
import argparse
import signal
import sys
import time
from pathlib import Path

from agentic_ni.distributed.bus import create_bus
from agentic_ni.distributed.cml_watcher import CMLStateWatcher
from agentic_ni.distributed.device_tools import CMLDeviceToolkit, MockDeviceToolkit
from agentic_ni.distributed.message import AgentMessage
from agentic_ni.distributed.orchestrator import AgentOrchestrator
from agentic_ni.logger import configure_logging, get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# ANSI カラー定数
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

_AGENT_COLORS: dict[str, str] = {
    "Agent-R1":  _CYAN,
    "Agent-R2":  _MAGENTA,
    "Agent-R3":  _BLUE,
    "Agent-R4":  _GREEN,
    "Agent-R5":  _YELLOW,
    "Agent-R6":  _RED,
    "Agent-R7":  _WHITE,
    "Agent-R8":  _CYAN,
    "Agent-R9":  _MAGENTA,
    "Agent-R10": _BLUE,
}


# ---------------------------------------------------------------------------
# バスメッセージ表示
# ---------------------------------------------------------------------------

def _print_bus_message(msg: AgentMessage, elapsed: float) -> None:
    color = _AGENT_COLORS.get(msg.from_agent, _WHITE)
    to = msg.to_agent or "ALL"

    if to.upper() == "ALL":
        dest = _c(_BLUE + _BOLD, "📡 ALL")
    elif to.upper() == "HUMAN":
        dest = _c(_RED + _BOLD, "🚨 HUMAN")
    elif to.upper() == "LOG":
        return  # LOG は非表示（ノイズ削減）
    else:
        dest_color = _AGENT_COLORS.get(to, _WHITE)
        dest = _c(dest_color, f"→ {to}")

    print(
        f"  {_c(_DIM, f'{elapsed:5.1f}s')}  "
        f"{_c(color + _BOLD, msg.from_agent)} {dest}"
    )
    for line in (msg.content or "").strip().splitlines():
        if line.strip():
            print(f"              {_c(_DIM, '│')} {line.strip()[:120]}")

    # HUMAN エスカレーションは区切り線付きで強調表示
    if to.upper() == "HUMAN":
        width = 60
        print()
        print(_c(_RED + _BOLD, "  " + "━" * width))
        print(_c(_RED + _BOLD, f"  🚨  HUMAN エスカレーション  ←  {msg.from_agent}"))
        print(_c(_RED + _BOLD, "  " + "━" * width))
        for line in (msg.content or "").strip().splitlines():
            print(f"  {line}")
        print(_c(_RED + _BOLD, "  " + "━" * width))
        print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="CML リアルタイム監視 + 自律エージェント自動起動",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "例:\n"
            "  # CML 経由で実機に show コマンドを実行する（推奨）\n"
            "  agentic-ni-watch --lab-id abc123 \\\n"
            "      --topology configs/demo-large/topology.yaml\n"
            "\n"
            "  # show コマンドをモックにする（オフライン確認用）\n"
            "  agentic-ni-watch --lab-id abc123 \\\n"
            "      --topology configs/demo-large/topology.yaml --mock-tools\n"
            "\n"
            "  # ポーリング間隔を 5 秒に変更\n"
            "  agentic-ni-watch --lab-id abc123 \\\n"
            "      --topology configs/demo-large/topology.yaml --poll-interval 5\n"
        ),
    )
    parser.add_argument(
        "--lab-id", required=True,
        help="監視対象の CML ラボ ID（agentic-ni-lab deploy で表示される）",
    )
    parser.add_argument(
        "--topology", required=True,
        help="topology.yaml のパス（エージェント構成の定義）",
    )
    parser.add_argument(
        "--poll-interval", type=float, default=3.0, metavar="SEC",
        help="CML 状態ポーリング間隔（秒、デフォルト: 3.0）",
    )
    parser.add_argument(
        "--mock-tools", action="store_true",
        help="show コマンドをモックにする（CML接続・pyATS不要）。オフライン確認用",
    )
    parser.add_argument(
        "--log-poll-interval", type=float, default=0.0, metavar="SEC",
        help=(
            "各エージェントが show logging をポーリングする間隔（秒）。"
            "0（デフォルト）の場合は無効。例: --log-poll-interval 10"
        ),
    )
    parser.add_argument(
        "--bus", default="memory", choices=["memory", "mqtt", "nats"],
        help="メッセージバスのバックエンド（デフォルト: memory）",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="DEBUG レベルのログ + スタックトレースを表示する",
    )
    parser.add_argument(
        "--log-file", metavar="FILE",
        help="ログを指定ファイルにも保存する",
    )
    args = parser.parse_args()
    configure_logging(verbose=args.verbose, log_file=args.log_file)
    asyncio.run(_async_main(args))


async def _async_main(args: argparse.Namespace) -> None:
    topo_path = Path(args.topology)
    if not topo_path.exists():
        print(f"[ERROR] topology.yaml が見つかりません: {topo_path}", file=sys.stderr)
        sys.exit(1)

    # Bus 接続
    bus = create_bus(args.bus)
    await bus.connect()

    start_time = time.monotonic()

    # バスメッセージをリアルタイム表示
    async def _on_bus_msg(topic: str, msg: AgentMessage) -> None:
        elapsed = time.monotonic() - start_time
        _print_bus_message(msg, elapsed)

    await bus.subscribe("network/agents/#", _on_bus_msg)

    # ツールキットファクトリー
    if args.mock_tools:
        toolkit_factory = lambda name: MockDeviceToolkit(name)
    else:
        # CML 組み込み pyATS 経由で実機にコマンドを実行する（testbed YAML 不要）
        lab_id = args.lab_id
        toolkit_factory = lambda name: CMLDeviceToolkit(name, lab_id=lab_id)

    # エージェント起動
    orchestrator = AgentOrchestrator(
        bus=bus,
        toolkit_factory=toolkit_factory,
        log_poll_interval=args.log_poll_interval,
    )
    await orchestrator.start_from_topology(topo_path)

    print()
    print(_c(_BOLD, f"  {'─' * 60}"))
    print(_c(_BOLD, f"  エージェント起動完了: {orchestrator.agent_count()} 台"))
    for aid, agent in orchestrator.get_all_agents().items():
        neighbors = list(agent._memory.neighbor_map.keys())
        color = _AGENT_COLORS.get(aid, _WHITE)
        neighbor_str = ", ".join(neighbors) if neighbors else "(なし)"
        print(
            f"  {_c(_GREEN, '✓')} {_c(color + _BOLD, f'{aid:<15}')} "
            f"{_c(_DIM, f'隣接: {neighbor_str}')}"
        )
    print(_c(_BOLD, f"  {'─' * 60}"))
    print()

    # CML ウォッチャー起動（--log-poll-interval 指定時はスキップ）
    watcher: CMLStateWatcher | None = None
    if args.log_poll_interval <= 0:
        watcher = CMLStateWatcher(
            lab_id=args.lab_id,
            orchestrator=orchestrator,
            poll_interval=args.poll_interval,
        )
        try:
            await watcher.start()
        except Exception as exc:
            print(
                f"{_c(_RED, '[ERROR]')} CML 接続に失敗しました: {exc}\n"
                f"  .env の CML_URL / CML_USERNAME / CML_PASSWORD を確認してください。",
                file=sys.stderr,
            )
            await orchestrator.stop_all()
            await bus.close()
            sys.exit(1)

        print(
            f"  {_c(_YELLOW + _BOLD, '👁  CML 監視中')}"
            f"  lab_id={_c(_BOLD, args.lab_id)}"
            f"  poll={args.poll_interval}s"
        )
        print(f"  {_c(_DIM, 'CML でリンクまたはノードを停止するとエージェントが自動的に動き出します。')}")
    else:
        print(
            f"  {_c(_YELLOW + _BOLD, '📋 ログ直接監視モード')}"
            f"  log-poll={args.log_poll_interval}s"
            f"  (CMLStateWatcher 無効)"
        )
        print(f"  {_c(_DIM, 'CML でリンクまたはノードを停止するとエージェントが自動的に動き出します。')}")
    else:
        print(
            f"  {_c(_YELLOW + _BOLD, '📋 ログ直接監視モード')}"
            f"  log-poll={args.log_poll_interval}s"
            f"  (CMLStateWatcher 無効)"
        )
        print(f"  {_c(_DIM, '各エージェントが自装置の show logging を直接ポーリングします。')}")

    # 対話モードのヘルプを表示
    if sys.stdin.isatty():
        print()
        print(_c(_BOLD, "  ── 対話コマンド ──"))
        print(f"  {_c(_CYAN, '  <装置名>: <指示・質問>')}  例: R1: 現在のOSPFネイバー状態を確認してください")
        print(f"  {_c(_DIM, '  入力例: R1: show ip route  /  R2: BGPセッションの状態は？')}")
    print(f"  {_c(_DIM, 'Ctrl+C で停止します。')}")
    print()

    # シャットダウン待機
    shutdown_event = asyncio.Event()

    def _handle_sigint(signum: int, frame: object) -> None:
        print(f"\n  {_c(_DIM, 'シャットダウンしています...')}")
        shutdown_event.set()

    signal.signal(signal.SIGINT, _handle_sigint)

    tasks = []
    if sys.stdin.isatty():
        tasks.append(asyncio.create_task(
            _run_human_input_loop(orchestrator, shutdown_event),
            name="human-input",
        ))
    tasks.append(asyncio.create_task(
        _drain_human_responses(orchestrator, shutdown_event),
        name="human-responses",
    ))

    try:
        await shutdown_event.wait()
    finally:
        for t in tasks:
            t.cancel()
        if watcher is not None:
            await watcher.stop()
        await orchestrator.stop_all()
        await bus.close()
        print(f"  {_c(_GREEN, '✓')} シャットダウン完了。")


# ---------------------------------------------------------------------------
# 対話入力・レスポンス表示
# ---------------------------------------------------------------------------

async def _drain_human_responses(
    orchestrator: AgentOrchestrator, shutdown_event: asyncio.Event
) -> None:
    """human_queue からレスポンスを取り出して表示する。"""
    while not shutdown_event.is_set():
        try:
            msg = await asyncio.wait_for(orchestrator.human_queue.get(), timeout=0.5)
        except asyncio.TimeoutError:
            continue
        from_agent = msg.get("from_agent", "?")
        content = msg.get("content", "")
        color = _AGENT_COLORS.get(from_agent, _WHITE)
        width = 64
        print()
        print(_c(_GREEN + _BOLD, "  " + "─" * width))
        print(
            f"  {_c(_GREEN + _BOLD, '💬  エージェント応答')}  "
            f"{_c(color + _BOLD, from_agent)}"
        )
        print(_c(_GREEN + _BOLD, "  " + "─" * width))
        for line in content.strip().splitlines():
            print(f"  {line}")
        print(_c(_GREEN + _BOLD, "  " + "─" * width))
        print()
        orchestrator.human_queue.task_done()


async def _run_human_input_loop(
    orchestrator: AgentOrchestrator, shutdown_event: asyncio.Event
) -> None:
    """標準入力から人間コマンドを読み取り、対応するエージェントに送る。

    入力フォーマット: ``<装置名>: <指示・質問>``
    例: ``R1: 現在の OSPF ネイバー状態を確認してください``
    """
    loop = asyncio.get_event_loop()
    while not shutdown_event.is_set():
        try:
            raw = await loop.run_in_executor(None, sys.stdin.readline)
        except asyncio.CancelledError:
            break
        if not raw:
            break  # EOF
        line = raw.strip()
        if not line:
            continue

        # "<装置名>: <指示>" 形式のパース
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


if __name__ == "__main__":
    main()
