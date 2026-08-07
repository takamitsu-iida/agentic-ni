"""AIエージェントコンテスト向けデモンストレーション実行スクリプト。

コマンド例::

    agentic-ni-demo                              # OSPF タイマーミスマッチ（デフォルト）
    agentic-ni-demo --scenario link-down         # 物理リンク断
    agentic-ni-demo --scenario attack-dual       # 攻撃エージェント vs. 分散防御
    agentic-ni-demo --scripted                   # API キー不要のスクリプト実行モード

デモシナリオ:
  ospf-timer   : R1-R2 間 OSPF Hello タイマーミスマッチ → 設定エラー検出
  link-down    : R1-R2 間物理リンク断 → 障害切り分け
  attack-dual  : 攻撃エージェントが時間差で二重リンク断を発動 → R1 孤立 → 3 エージェント協調診断

判定ポイント（おぉ）:
  1. 複数エージェントが自律的に並行稼働
  2. エージェント同士が自然言語で対話
  3. show コマンドを自律実行してログ解析
  4. 攻撃と防御がリアルタイムで交錯する
  5. 根本原因を特定して管理者に構造化レポートを送信
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# ANSI カラー定数（ライブラリ不要）
# ---------------------------------------------------------------------------
_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_DIM    = "\033[2m"
_CYAN   = "\033[96m"
_YELLOW = "\033[93m"
_GREEN  = "\033[92m"
_RED    = "\033[91m"
_MAGENTA = "\033[95m"
_BLUE   = "\033[94m"
_WHITE  = "\033[97m"

_USE_COLOR = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"{code}{text}{_RESET}" if _USE_COLOR else text


# ---------------------------------------------------------------------------
# シナリオ定義（MockDeviceToolkit 用の show コマンド応答）
# ---------------------------------------------------------------------------

_SCENARIOS: dict[str, dict] = {
    "ospf-timer": {
        "title": "シナリオ A: OSPF Hello タイマーミスマッチ",
        "subtitle": "設定エラーにより OSPF ネイバーが確立しない",
        "syslog": {
            "device": "R1",
            "message": "%OSPF-5-ADJCHG: Process 1, Nbr 10.0.12.2 on GigabitEthernet0/0 from LOADING to DOWN, Neighbor Down: Dead timer expired",
            "severity": "5",
        },
        "show_responses": {
            "R1": {
                "show ip ospf neighbor": (
                    "Neighbor ID  Pri  State     Dead Time  Address    Interface\n"
                    "(ネイバーなし)\n"
                ),
                "show ip ospf interface GigabitEthernet0/0": (
                    "GigabitEthernet0/0 is up, line protocol is up\n"
                    "  Hello due in 00:00:03\n"
                    "  Timer intervals configured, Hello 10, Dead 40\n"
                ),
                "show interfaces brief": (
                    "GigabitEthernet0/0  10.0.12.1    YES manual up    up\n"
                    "Loopback0           1.1.1.1      YES manual up    up\n"
                ),
            },
            "R2": {
                "show ip ospf neighbor": (
                    "Neighbor ID  Pri  State     Dead Time  Address    Interface\n"
                    "(ネイバーなし)\n"
                ),
                "show ip ospf interface GigabitEthernet0/0": (
                    "GigabitEthernet0/0 is up, line protocol is up\n"
                    "  Hello due in 00:00:27\n"
                    "  Timer intervals configured, Hello 30, Dead 120\n"  # ← ミスマッチ
                ),
                "show interfaces brief": (
                    "GigabitEthernet0/0  10.0.12.2    YES manual up    up\n"
                    "Loopback0           2.2.2.2      YES manual up    up\n"
                ),
            },
        },
        "running_config": {
            "R1": (
                "hostname R1\n!\ninterface GigabitEthernet0/0\n"
                " ip address 10.0.12.1 255.255.255.252\n"
                " ip ospf hello-interval 10\n"
                " ip ospf dead-interval 40\n"
                "!\nrouter ospf 1\n router-id 1.1.1.1\n"
                " network 10.0.12.0 0.0.0.3 area 0\n"
                " network 1.1.1.1 0.0.0.0 area 0\n"
            ),
            "R2": (
                "hostname R2\n!\ninterface GigabitEthernet0/0\n"
                " ip address 10.0.12.2 255.255.255.252\n"
                " ip ospf hello-interval 30\n"   # ← 30 ≠ R1 の 10 がミスマッチ
                " ip ospf dead-interval 120\n"
                "!\nrouter ospf 1\n router-id 2.2.2.2\n"
                " network 10.0.12.0 0.0.0.3 area 0\n"
                " network 2.2.2.2 0.0.0.0 area 0\n"
            ),
        },
    },
    "link-down": {
        "title": "シナリオ B: 物理リンク断",
        "subtitle": "R1-R2 間の物理インターフェースがダウン",
        "syslog": {
            "device": "R1",
            "message": "%LINK-3-UPDOWN: Interface GigabitEthernet0/0, changed state to down",
            "severity": "3",
        },
        "show_responses": {
            "R1": {
                "show interfaces brief": (
                    "GigabitEthernet0/0  unassigned   YES unset  down    down\n"
                    "Loopback0           1.1.1.1      YES manual up      up\n"
                ),
                "show ip ospf neighbor": "(ネイバーなし)\n",
            },
            "R2": {
                "show interfaces brief": (
                    "GigabitEthernet0/0  10.0.12.2    YES manual down    down\n"
                    "Loopback0           2.2.2.2      YES manual up      up\n"
                ),
                "show ip ospf neighbor": "(ネイバーなし)\n",
            },
        },
        "running_config": {
            "R1": "hostname R1\ninterface GigabitEthernet0/0\n ip address 10.0.12.1 255.255.255.252\n",
            "R2": "hostname R2\ninterface GigabitEthernet0/0\n ip address 10.0.12.2 255.255.255.252\n",
        },
    },
    # attack-dual は専用の run_attack_dual_demo() で処理するため show_responses/syslog は不要
    "attack-dual": {
        "title": "シナリオ C: 攻撃エージェント vs. 防御エージェント — 二重リンク断",
        "subtitle": "攻撃が R1 を孤立させる。3 エージェントが協調して診断。",
    },
}

_P2P_TOPOLOGY = {
    "lab": {"title": "demo-lab", "version": "0.1.0"},
    "nodes": [
        {
            "id": "n0", "label": "R1", "node_definition": "iosv",
            "interfaces": [{"id": "i0", "label": "GigabitEthernet0/0"}],
        },
        {
            "id": "n1", "label": "R2", "node_definition": "iosv",
            "interfaces": [{"id": "i0", "label": "GigabitEthernet0/0"}],
        },
    ],
    "links": [
        {"id": "l0", "n1": "n0", "i1": "i0", "n2": "n1", "i2": "i0", "label": "l0"},
    ],
}

# 二重攻撃デモ用: R1-R2-R3 三角形トポロジー
_TRIANGLE_TOPOLOGY = {
    "lab": {"title": "attack-demo-lab", "version": "0.1.0"},
    "nodes": [
        {
            "id": "n0", "label": "R1", "node_definition": "iosv",
            "interfaces": [
                {"id": "i0", "label": "GigabitEthernet0/0"},
                {"id": "i1", "label": "GigabitEthernet0/1"},
            ],
        },
        {
            "id": "n1", "label": "R2", "node_definition": "iosv",
            "interfaces": [
                {"id": "i0", "label": "GigabitEthernet0/0"},
                {"id": "i1", "label": "GigabitEthernet0/1"},
            ],
        },
        {
            "id": "n2", "label": "R3", "node_definition": "iosv",
            "interfaces": [
                {"id": "i0", "label": "GigabitEthernet0/0"},
                {"id": "i1", "label": "GigabitEthernet0/1"},
            ],
        },
    ],
    "links": [
        {"id": "l0", "n1": "n0", "i1": "i0", "n2": "n1", "i2": "i0", "label": "l0"},
        {"id": "l1", "n1": "n0", "i1": "i1", "n2": "n2", "i2": "i0", "label": "l1"},
        {"id": "l2", "n1": "n1", "i1": "i1", "n2": "n2", "i2": "i1", "label": "l2"},
    ],
}


# ---------------------------------------------------------------------------
# DemoDisplay — リアルタイム会話表示
# ---------------------------------------------------------------------------

class DemoDisplay:
    """エージェント間の対話をリアルタイムで端末に表示するオブザーバー。"""

    _AGENT_COLORS = {
        "Agent-R1": _CYAN,
        "Agent-R2": _MAGENTA,
        "Agent-R3": _BLUE,
    }

    def __init__(self) -> None:
        self._start_time = time.monotonic()
        self._msg_count = 0
        self._attack_count = 0
        self.human_received = asyncio.Event()
        self.human_items: list[dict] = []

    def elapsed(self) -> str:
        s = time.monotonic() - self._start_time
        return f"{s:5.1f}s"

    def _agent_color(self, agent_id: str) -> str:
        return self._AGENT_COLORS.get(agent_id, _WHITE)

    def on_tool_call(self, agent_id: str, tool_name: str, args: dict) -> None:
        color = self._agent_color(agent_id)
        args_str = ", ".join(f"{k}={v!r}" for k, v in args.items()) if args else ""
        print(
            f"  {_c(_DIM, self.elapsed())}  "
            f"{_c(color, agent_id)}  "
            f"{_c(_YELLOW, '⚙')}  "
            f"{_c(_YELLOW + _BOLD, tool_name)}({_c(_DIM, args_str)})"
        )

    def on_bus_message(self, agent_id: str, to_agent: str, msg_type: str, content: str) -> None:
        color = self._agent_color(agent_id)
        if to_agent.upper() == "ALL":
            dest = _c(_BLUE, "📡 ALL")
        elif to_agent.upper() == "HUMAN":
            dest = _c(_GREEN + _BOLD, "🚨 HUMAN")
        elif to_agent.upper() == "LOG":
            dest = _c(_DIM, "📝 LOG")
        else:
            dest = _c(self._agent_color(to_agent), f"→ {to_agent}")

        arrow = "▶" if msg_type == "query" else "◀"
        self._msg_count += 1
        print(
            f"  {_c(_DIM, self.elapsed())}  "
            f"{_c(color + _BOLD, agent_id)} {dest}"
        )
        # 本文を 100 文字以内に折り畳む
        for line in content.strip().splitlines():
            if line.strip():
                print(f"              {_c(_DIM, '│')} {line.strip()[:120]}")

    def on_attack(self, attack: Any) -> None:
        width = 62
        print()
        print(_c(_RED + _BOLD, "  " + "▓" * width))
        print(_c(_RED + _BOLD, f"  ⚔️  攻撃エージェント発動  [{attack.attack_id}]"))
        print(_c(_YELLOW + _BOLD, f"  {attack.description}"))
        print(_c(_RED + _BOLD, "  " + "▓" * width))
        print()
        self._attack_count += 1

    def on_human_escalation(self, from_agent: str, content: str) -> None:
        width = 62
        print()
        print(_c(_RED + _BOLD, "  " + "━" * width))
        print(_c(_RED + _BOLD, f"  🚨  HUMAN エスカレーション  ←  {from_agent}"))
        print(_c(_RED + _BOLD, "  " + "━" * width))
        for line in content.strip().splitlines():
            print(f"  {line}")
        print(_c(_RED + _BOLD, "  " + "━" * width))
        print()
        self.human_items.append({"from": from_agent, "content": content})
        self.human_received.set()

    def print_summary(self) -> None:
        elapsed = time.monotonic() - self._start_time
        print()
        print(_c(_GREEN + _BOLD, "  ✅  診断完了"))
        print(f"  診断所要時間    : {_c(_BOLD, f'{elapsed:.1f} 秒')}")
        if self._attack_count:
            print(f"  発動された攻撃  : {_c(_RED + _BOLD, str(self._attack_count))} 件")
        print(f"  エージェント間通信: {_c(_BOLD, str(self._msg_count))} 件")
        print()


# ---------------------------------------------------------------------------
# スクリプト実行モード用 MockLLM シーケンス
# ---------------------------------------------------------------------------

_SCRIPTED_SEQUENCES: dict[str, dict[str, list]] = {
    "ospf-timer": {
        "R1": [
            {"name": "run_show",
             "args": {"command": "show ip ospf neighbor"}, "id": "r1_c1"},
            {"name": "run_show",
             "args": {"command": "show ip ospf interface GigabitEthernet0/0"}, "id": "r1_c2"},
            "TO: Agent-R2 | MSG: R1 の OSPF ネイバーが消失しました。物理リンクは UP ですが OSPF が確立していません。Hello タイマーは 10 秒です。そちらの Hello タイマー設定を確認してください。",
            "TO: HUMAN | MSG: 【障害診断レポート】\n"
            "- 症状    : R1-R2 間 OSPF ネイバー消失\n"
            "- 根本原因: Hello タイマーのミスマッチ（R1=10秒、R2=30秒）\n"
            "- 影響範囲: R1-R2 間のすべての OSPF 経路\n"
            "- 推奨対応:\n"
            "  R2 の GigabitEthernet0/0 で以下を実行してください:\n"
            "    ip ospf hello-interval 10\n"
            "    ip ospf dead-interval 40",
        ],
        "R2": [
            {"name": "run_show",
             "args": {"command": "show ip ospf interface GigabitEthernet0/0"}, "id": "r2_c1"},
            "TO: Agent-R1 | MSG: R2 の Hello タイマーは 30 秒です（Dead=120秒）。R1 と異なっているようです。これが OSPF 確立失敗の原因の可能性があります。",
        ],
    },
    "link-down": {
        "R1": [
            {"name": "get_interface_status", "args": {}, "id": "r1_c1"},
            "TO: Agent-R2 | MSG: R1 の GigabitEthernet0/0 が物理的に DOWN しています。そちらの GigabitEthernet0/0 の状態を確認してください。",
            "TO: HUMAN | MSG: 【障害診断レポート】\n"
            "- 症状    : R1 GigabitEthernet0/0 物理ダウン\n"
            "- 根本原因: R1-R2 間の物理リンク断（双方の I/F が DOWN）\n"
            "- 影響範囲: R1-R2 間の全通信・OSPF ネイバー\n"
            "- 推奨対応: ケーブル・SFP・対向ポートを物理確認してください",
        ],
        "R2": [
            {"name": "get_interface_status", "args": {}, "id": "r2_c1"},
            "TO: Agent-R1 | MSG: R2 の GigabitEthernet0/0 も DOWN です。双方でリンクが落ちています。物理障害と判断します。",
        ],
    },
    # attack-dual: 3 エージェント向け（R1 が 2 回攻撃される）
    "attack-dual": {
        "R1": [
            # Event 1: 第一撃（R1-R2 リンク断）
            {"name": "get_interface_status", "args": {}, "id": "r1_c1"},
            "TO: Agent-R2 | MSG: R1 の GigabitEthernet0/0 が DOWN！R2 方向のリンクが切れています。そちらの状態を確認してください。",
            # Event 1b: R2 の返答を受けてログ記録
            "TO: LOG | MSG: R1-R2 リンク断を確認。R1-R3 経路で監視継続中。",
            # Event 2: 第二撃（R1-R3 リンクも断 → 完全孤立）
            {"name": "get_interface_status", "args": {}, "id": "r1_c2"},
            "TO: ALL | MSG: 🚨 緊急！R1 の全インターフェースがダウン！R1 が完全孤立しました！\n"
            "TO: HUMAN | MSG: 【重大障害：二重リンク断 — R1 完全孤立】\n"
            "- GigabitEthernet0/0: DOWN（R2 方向）— 第一撃\n"
            "- GigabitEthernet0/1: DOWN（R3 方向）— 第二撃\n"
            "- R1 がネットワークから完全に孤立しました\n"
            "- 根本原因: R1 の上位スイッチまたは集約ポイントの障害が強く疑われます\n"
            "- 推奨対応:\n"
            "  1. R1 が接続するスイッチのポートランプを確認\n"
            "  2. 上位スイッチの障害ログを確認\n"
            "  3. R1 への物理アクセスを確保してください",
        ],
        "R2": [
            # Event 1: R1 からの問い合わせを受けて調査
            {"name": "get_interface_status", "args": {}, "id": "r2_c1"},
            "TO: Agent-R1 | MSG: R2 の GigabitEthernet0/0 も DOWN です。R1-R2 リンク断を確認しました。R3 への経路は正常です。",
            # Event 2: R1 の ALL ブロードキャストを受けて
            {"name": "get_interface_status", "args": {}, "id": "r2_c2"},
            "TO: Agent-R3 | MSG: R1 から緊急ブロードキャストあり。R1 が孤立中。R2-R3 間の状態を確認してください。",
            # 以降: R3 の返答などを受け取っても LOG のみ（ループ防止）
            "TO: LOG | MSG: R3 の応答を受領。二重障害を確認。R1-R2 間と R1-R3 間の双方が断線。",
        ],
        "R3": [
            # Event: R1 の ALL ブロードキャストを受けて調査
            {"name": "get_interface_status", "args": {}, "id": "r3_c1"},
            "TO: Agent-R2 | MSG: R3 の GigabitEthernet0/0（R1 方向）も DOWN しています！R1-R3 リンク断を独立して確認。R2-R3 間（Gi0/1）は正常です。",
            # 以降: R2 の返答などを受け取っても LOG のみ（ループ防止）
            "TO: LOG | MSG: R2 との連絡確認済み。調査完了。二重障害確定。",
        ],
    },
}


# ---------------------------------------------------------------------------
# メインデモ実行
# ---------------------------------------------------------------------------

async def run_demo(scenario_name: str, scripted: bool = False) -> None:
    if scenario_name == "attack-dual":
        await run_attack_dual_demo(scripted=scripted)
        return

    import tempfile, os
    import yaml as _yaml

    from agentic_ni.distributed.bus import InMemoryBus
    from agentic_ni.distributed.device_agent import SyslogEvent
    from agentic_ni.distributed.device_tools import MockDeviceToolkit
    from agentic_ni.distributed.message import AgentMessage
    from agentic_ni.distributed.orchestrator import AgentOrchestrator
    from agentic_ni.distributed.reporter import ConversationRecorder, generate_report, save_report

    scenario = _SCENARIOS[scenario_name]
    display = DemoDisplay()

    # ---- バナー ----
    _print_banner(scenario["title"], scenario["subtitle"])
    await asyncio.sleep(0.5)

    # ---- バス / Bus の初期化 ----
    bus = InMemoryBus()
    await bus.connect()

    recorder = ConversationRecorder(bus)
    await recorder.start()

    # ---- トポロジーファイル作成 ----
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False
    ) as f:
        f.write(_yaml.dump(_P2P_TOPOLOGY))
        topo_file = f.name

    # ---- ツールキットファクトリー ----
    show_map: dict = scenario.get("show_responses", {})
    config_map: dict = scenario.get("running_config", {})

    def toolkit_factory(device_name: str) -> MockDeviceToolkit:
        return MockDeviceToolkit(
            device_name,
            show_responses=show_map.get(device_name, {}),
            running_config=config_map.get(device_name, "! no config\n"),
            readonly=True,
        )

    # ---- LLM の選択 ----
    if scripted:
        llm_map = _build_scripted_llm_map(scenario_name)
    else:
        llm_map = None  # 各エージェントが get_llm() で取得

    # ---- オーケストレーター起動 ----
    orch = AgentOrchestrator(bus=bus, toolkit_factory=toolkit_factory)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False
    ) as f:
        f.write(_yaml.dump(_P2P_TOPOLOGY))
        topo_file2 = f.name

    _print_step("1", "エージェント起動中...")
    await orch.start_from_topology(topo_file2)
    os.unlink(topo_file2)

    for agent_id, agent in orch.get_all_agents().items():
        if llm_map and agent.device_name in llm_map:
            agent._llm = llm_map[agent.device_name]
        # 表示フックを設定
        agent.on_tool_call = display.on_tool_call

    _print_agent_list(orch)

    # ---- バスメッセージの購読・表示 ----
    async def _on_bus_msg(topic: str, msg: AgentMessage) -> None:
        if msg.to_agent.upper() == "HUMAN":
            display.on_human_escalation(msg.from_agent, msg.content)
        elif msg.to_agent.upper() != "LOG":
            display.on_bus_message(
                msg.from_agent, msg.to_agent, msg.msg_type, msg.content
            )

    await bus.subscribe("network/agents/#", _on_bus_msg)

    # human_queue のトラッキング（表示 + 完了検知を同期的に実行）
    original_put = orch.human_queue.put_nowait
    def _tracking_put(item):
        original_put(item)
        if "from_agent" in item:
            display.on_human_escalation(item["from_agent"], item.get("content", ""))
        elif item.get("type") == "config_change_request":
            display.on_human_escalation(item.get("device", "?"), item.get("commands", ""))
        display.human_received.set()
    orch.human_queue.put_nowait = _tracking_put

    # ---- 障害注入 ----
    await asyncio.sleep(0.8)
    syslog_cfg = scenario["syslog"]
    _print_step("2", "障害発生 — syslog 注入")
    _print_alert(syslog_cfg["message"])

    agent_id = f"Agent-{syslog_cfg['device']}"
    target_agent = orch.get_agent(agent_id)
    await target_agent.inject_event(SyslogEvent(
        raw_text=syslog_cfg["message"],
        severity=syslog_cfg["severity"],
    ))

    # ---- エージェント対話の監視 ----
    print()
    _print_step("3", "エージェント自律診断開始")
    print()

    try:
        await asyncio.wait_for(display.human_received.wait(), timeout=60.0)
    except asyncio.TimeoutError:
        print(_c(_RED, "\n  [タイムアウト] HUMAN エスカレーションを受信できませんでした。"))

    # 処理が残っていれば少し待つ
    await asyncio.sleep(0.3)

    # ---- サマリー ----
    display.print_summary()

    # ---- レポート保存 ----
    _print_step("4", "診断レポート生成")
    report = generate_report(
        orch, recorder,
        topology_path="demo2/topology.yaml",
        scenario_name=scenario["title"],
    )
    saved = save_report(report, prefix="demo")
    print(f"  レポート保存先: {_c(_CYAN, str(saved))}")

    await orch.stop_all()
    await recorder.stop()
    await bus.close()
    os.unlink(topo_file)


def _build_scripted_llm_map(scenario_name: str) -> dict:
    """スクリプトモード用 MockLLM を装置別に生成して返す。"""
    from langchain_core.messages import AIMessage, BaseMessage

    sequences = _SCRIPTED_SEQUENCES.get(scenario_name, {})
    llm_map = {}
    for device_name, seq in sequences.items():
        class _MockLLM:
            def __init__(self, s):
                self._s = list(s)
                self._i = 0
            def bind_tools(self, t): return self
            async def ainvoke(self, msgs: list[BaseMessage]) -> AIMessage:
                item = self._s[min(self._i, len(self._s) - 1)]
                self._i += 1
                if isinstance(item, str):
                    return AIMessage(content=item)
                return AIMessage(content="", tool_calls=[{
                    "name": item["name"], "args": item.get("args", {}),
                    "id": item.get("id", f"c{self._i}"), "type": "tool_call",
                }])
        llm_map[device_name] = _MockLLM(seq)
    return llm_map


# ---------------------------------------------------------------------------
# 表示ヘルパー
# ---------------------------------------------------------------------------

def _print_banner(title: str, subtitle: str) -> None:
    width = 64
    print()
    print(_c(_CYAN + _BOLD, "  ╔" + "═" * width + "╗"))
    print(_c(_CYAN + _BOLD, "  ║") + _c(_BOLD, f"  自律分散型ネットワーク AI エージェント".center(width)) + _c(_CYAN + _BOLD, "║"))
    print(_c(_CYAN + _BOLD, "  ║") + _c(_BOLD, f"  Autonomous Distributed Network Troubleshooting".center(width)) + _c(_CYAN + _BOLD, "║"))
    print(_c(_CYAN + _BOLD, "  ╠" + "═" * width + "╣"))
    print(_c(_CYAN + _BOLD, "  ║") + f"  {title}".ljust(width) + _c(_CYAN + _BOLD, "║"))
    print(_c(_CYAN + _BOLD, "  ║") + _c(_DIM, f"  {subtitle}".ljust(width)) + _c(_CYAN + _BOLD, "║"))
    print(_c(_CYAN + _BOLD, "  ╚" + "═" * width + "╝"))
    print()


def _print_step(num: str, text: str) -> None:
    print(f"\n  {_c(_BOLD + _WHITE, f'[{num}]')} {_c(_BOLD, text)}")


def _print_alert(message: str) -> None:
    print()
    print(f"  {_c(_YELLOW + _BOLD, '⚠️  syslog')}")
    print(f"  {_c(_YELLOW, message)}")
    print()


def _print_agent_list(orch: Any) -> None:
    for agent_id, agent in orch.get_all_agents().items():
        neighbors = ", ".join(agent._memory.neighbor_map.keys()) or "なし"
        tools = len(agent._tools)
        print(
            f"  {_c(_GREEN, '✓')} {_c(_BOLD, agent_id)}"
            f"  ({agent._device_type})  隣接: {neighbors}  ツール: {tools} 個"
        )


# ---------------------------------------------------------------------------
# 二重攻撃デモ（attack-dual シナリオ専用）
# ---------------------------------------------------------------------------

async def run_attack_dual_demo(scripted: bool = False) -> None:
    """攻撃エージェント vs. 防御エージェントの二重リンク断デモを実行する。"""
    import os
    import tempfile

    import yaml as _yaml

    from agentic_ni.distributed.attack_agent import AttackAgent, DUAL_LINK_DOWN_CAMPAIGN
    from agentic_ni.distributed.bus import InMemoryBus
    from agentic_ni.distributed.device_tools import MockDeviceToolkit
    from agentic_ni.distributed.message import AgentMessage
    from agentic_ni.distributed.orchestrator import AgentOrchestrator
    from agentic_ni.distributed.reporter import ConversationRecorder, generate_report, save_report

    scenario = _SCENARIOS["attack-dual"]
    display = DemoDisplay()

    _print_banner(scenario["title"], scenario["subtitle"])
    await asyncio.sleep(0.5)

    bus = InMemoryBus()
    await bus.connect()
    recorder = ConversationRecorder(bus)
    await recorder.start()

    # 正常状態の初期 show レスポンス
    _NORMAL: dict[str, dict[str, str]] = {
        "R1": {
            "show interfaces brief": (
                "GigabitEthernet0/0  10.0.12.1    YES  up      up\n"
                "GigabitEthernet0/1  10.0.13.1    YES  up      up\n"
                "Loopback0           1.1.1.1      YES  up      up\n"
            ),
            "show ip ospf neighbor": (
                "Neighbor ID  Pri  State     Dead Time  Address    Interface\n"
                "2.2.2.2        1  FULL/DR   00:00:38   10.0.12.2  GigabitEthernet0/0\n"
                "3.3.3.3        1  FULL/DR   00:00:36   10.0.13.2  GigabitEthernet0/1\n"
            ),
        },
        "R2": {
            "show interfaces brief": (
                "GigabitEthernet0/0  10.0.12.2    YES  up      up\n"
                "GigabitEthernet0/1  10.0.23.1    YES  up      up\n"
                "Loopback0           2.2.2.2      YES  up      up\n"
            ),
            "show ip ospf neighbor": (
                "1.1.1.1        1  FULL/DR   00:00:35   10.0.12.1  GigabitEthernet0/0\n"
                "3.3.3.3        1  FULL/DR   00:00:39   10.0.23.2  GigabitEthernet0/1\n"
            ),
        },
        "R3": {
            "show interfaces brief": (
                "GigabitEthernet0/0  10.0.13.2    YES  up      up\n"
                "GigabitEthernet0/1  10.0.23.2    YES  up      up\n"
                "Loopback0           3.3.3.3      YES  up      up\n"
            ),
            "show ip ospf neighbor": (
                "1.1.1.1        1  FULL/DR   00:00:37   10.0.13.1  GigabitEthernet0/0\n"
                "2.2.2.2        1  FULL/DR   00:00:36   10.0.23.1  GigabitEthernet0/1\n"
            ),
        },
    }

    # ツールキット（攻撃エージェントが後から書き換える）
    toolkits: dict[str, MockDeviceToolkit] = {}

    def toolkit_factory(device_name: str) -> MockDeviceToolkit:
        tk = MockDeviceToolkit(
            device_name,
            show_responses=dict(_NORMAL.get(device_name, {})),
            readonly=True,
        )
        toolkits[device_name] = tk
        return tk

    # LLM の選択
    llm_map = _build_scripted_llm_map("attack-dual") if scripted else None

    orch = AgentOrchestrator(bus=bus, toolkit_factory=toolkit_factory)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(_yaml.dump(_TRIANGLE_TOPOLOGY))
        topo_file = f.name

    _print_step("1", "防御エージェント 3 台 — 起動中...")
    await orch.start_from_topology(topo_file)
    os.unlink(topo_file)

    for agent_id, agent in orch.get_all_agents().items():
        if llm_map and agent.device_name in llm_map:
            agent._llm = llm_map[agent.device_name]
        agent.on_tool_call = display.on_tool_call

    _print_agent_list(orch)

    # バスメッセージ表示
    async def _on_bus_msg(topic: str, msg: AgentMessage) -> None:
        if msg.to_agent.upper() not in ("LOG",):
            display.on_bus_message(
                msg.from_agent, msg.to_agent, msg.msg_type, msg.content
            )

    await bus.subscribe("network/agents/#", _on_bus_msg)

    # HUMAN キュートラッキング
    original_put = orch.human_queue.put_nowait
    def _tracking_put(item):
        original_put(item)
        if "from_agent" in item:
            display.on_human_escalation(item["from_agent"], item.get("content", ""))
        display.human_received.set()
    orch.human_queue.put_nowait = _tracking_put

    # 攻撃エージェントの準備
    attacker = AttackAgent(
        orchestrator=orch,
        toolkits=toolkits,
        on_attack=display.on_attack,
    )

    await asyncio.sleep(0.8)
    _print_step("2", "攻撃エージェント始動 — 二重リンク断キャンペーン開始")
    print()
    print(f"  {_c(_DIM, '第一撃: 今すぐ  |  第二撃: 3.5 秒後')}")
    print()

    _print_step("3", "攻撃 vs. 防御 — リアルタイム対話")
    print()

    # 攻撃キャンペーンと HUMAN 待機を並行実行
    try:
        await asyncio.wait_for(
            asyncio.gather(
                attacker.run_campaign(DUAL_LINK_DOWN_CAMPAIGN),
                display.human_received.wait(),
            ),
            timeout=60.0,
        )
    except asyncio.TimeoutError:
        print(_c(_RED, "\n  [タイムアウト]"))

    await asyncio.sleep(0.5)
    display.print_summary()

    _print_step("4", "診断レポート生成")
    report = generate_report(
        orch, recorder,
        topology_path="triangle/topology.yaml",
        scenario_name=scenario["title"],
    )
    saved = save_report(report, prefix="demo-attack")
    print(f"  レポート保存先: {_c(_CYAN, str(saved))}")

    await orch.stop_all()
    await recorder.stop()
    await bus.close()


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="自律分散型ネットワーク AI エージェント — コンテストデモ",
    )
    parser.add_argument(
        "--scenario", default="ospf-timer",
        choices=list(_SCENARIOS.keys()),
        help="デモシナリオ（デフォルト: ospf-timer）",
    )
    parser.add_argument(
        "--scripted", action="store_true",
        help="API キー不要のスクリプト実行モード",
    )
    args = parser.parse_args()

    asyncio.run(run_demo(args.scenario, scripted=args.scripted))


if __name__ == "__main__":
    main()
