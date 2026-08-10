"""AIエージェントコンテスト向けデモンストレーション実行スクリプト。

コマンド例::

    agentic-ni-demo --scenario core-link-down --scripted   # CML コアリンク停止（10台構成）
    agentic-ni-demo --scenario node-failure --scripted     # CML ノード停止（10台構成）
    agentic-ni-demo --scenario core-link-down              # リアル LLM モード

デモシナリオ:
  core-link-down  : 10台構成 — CML コアリンク停止 (l0) → R1/R2 クロスチェックで診断
  node-failure    : 10台構成 — CML ディストリビューションルータ停止 (R4) → 4方向クロスチェック
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
    "core-link-down": {
        "title": "シナリオ D: CML コアリンク停止 — 10台構成",
        "subtitle": "R1-R2 間コアリンク (l0) を CML で停止。迂回経路に再収束後、5 エージェントが協調診断。",
        # CML 操作: PUT /api/v0/labs/{lab_id}/links/l0/state {"state": "stopped"}
        "syslogs": [
            {
                "device": "R1",
                "message": "%LINK-3-UPDOWN: Interface GigabitEthernet0/0, changed state to down",
                "severity": "3",
            },
        ],
        "show_responses": {
            "R1": {
                "show ip ospf neighbor": (
                    "Neighbor ID  Pri  State     Dead Time  Address        Interface\n"
                    "3.3.3.3        1  FULL/-    00:00:38   10.0.13.2      GigabitEthernet0/1\n"
                    "4.4.4.4        1  FULL/-    00:00:36   10.0.14.2      GigabitEthernet0/2\n"
                    "5.5.5.5        1  FULL/-    00:00:39   10.0.15.2      GigabitEthernet0/3\n"
                    "(2.2.2.2 が消失 — コアリンク断)\n"
                ),
                "show interfaces brief": (
                    "Interface              IP-Address      OK? Method Status   Protocol\n"
                    "GigabitEthernet0/0     10.0.12.1       YES NVRAM  down     down\n"
                    "GigabitEthernet0/1     10.0.13.1       YES NVRAM  up       up  \n"
                    "GigabitEthernet0/2     10.0.14.1       YES NVRAM  up       up  \n"
                    "GigabitEthernet0/3     10.0.15.1       YES NVRAM  up       up  \n"
                    "Loopback0              1.1.1.1         YES NVRAM  up       up  \n"
                ),
                "show ip route ospf": (
                    "O   2.2.2.2 [110/21] via 10.0.13.2, 00:01:08, GigabitEthernet0/1\n"
                    "O   3.3.3.3 [110/11] via 10.0.13.2, 00:05:14, GigabitEthernet0/1\n"
                    "O   4.4.4.4 [110/11] via 10.0.14.2, 00:05:14, GigabitEthernet0/2\n"
                    "O   5.5.5.5 [110/11] via 10.0.15.2, 00:05:14, GigabitEthernet0/3\n"
                    "O   6.6.6.6 [110/21] via 10.0.13.2, 00:01:08, GigabitEthernet0/1\n"
                    "O   7.7.7.7 [110/21] via 10.0.13.2, 00:01:08, GigabitEthernet0/1\n"
                    "O   8.8.8.8 [110/21] via 10.0.14.2, 00:01:08, GigabitEthernet0/2\n"
                    "O   9.9.9.9 [110/21] via 10.0.14.2, 00:01:08, GigabitEthernet0/2\n"
                    "O   10.10.10.10 [110/21] via 10.0.15.2, 00:01:08, GigabitEthernet0/3\n"
                ),
            },
            "R2": {
                "show ip ospf neighbor": (
                    "Neighbor ID  Pri  State     Dead Time  Address        Interface\n"
                    "3.3.3.3        1  FULL/-    00:00:36   10.0.23.2      GigabitEthernet0/1\n"
                    "4.4.4.4        1  FULL/-    00:00:38   10.0.24.2      GigabitEthernet0/2\n"
                    "5.5.5.5        1  FULL/-    00:00:34   10.0.25.2      GigabitEthernet0/3\n"
                    "(1.1.1.1 が消失 — コアリンク断)\n"
                ),
                "show interfaces brief": (
                    "Interface              IP-Address      OK? Method Status   Protocol\n"
                    "GigabitEthernet0/0     10.0.12.2       YES NVRAM  down     down\n"
                    "GigabitEthernet0/1     10.0.23.1       YES NVRAM  up       up  \n"
                    "GigabitEthernet0/2     10.0.24.1       YES NVRAM  up       up  \n"
                    "GigabitEthernet0/3     10.0.25.1       YES NVRAM  up       up  \n"
                    "Loopback0              2.2.2.2         YES NVRAM  up       up  \n"
                ),
            },
            "R3": {
                "show ip ospf neighbor": (
                    "Neighbor ID  Pri  State     Dead Time  Address        Interface\n"
                    "1.1.1.1        1  FULL/-    00:00:38   10.0.13.1      GigabitEthernet0/0\n"
                    "2.2.2.2        1  FULL/-    00:00:36   10.0.23.1      GigabitEthernet0/1\n"
                    "6.6.6.6        1  FULL/-    00:00:39   10.0.36.2      GigabitEthernet0/2\n"
                    "7.7.7.7        1  FULL/-    00:00:35   10.0.37.2      GigabitEthernet0/3\n"
                ),
                "show ip route ospf": (
                    "O   1.1.1.1 [110/11] via 10.0.13.1, 00:05:14, GigabitEthernet0/0\n"
                    "O   2.2.2.2 [110/11] via 10.0.23.1, 00:05:14, GigabitEthernet0/1\n"
                    "O   4.4.4.4 [110/21] via 10.0.13.1, 00:01:08, GigabitEthernet0/0\n"
                    "O   5.5.5.5 [110/21] via 10.0.13.1, 00:01:08, GigabitEthernet0/0\n"
                    "O   8.8.8.8 [110/31] via 10.0.13.1, 00:01:08, GigabitEthernet0/0\n"
                    "O   9.9.9.9 [110/31] via 10.0.13.1, 00:01:08, GigabitEthernet0/0\n"
                    "O   10.10.10.10 [110/31] via 10.0.13.1, 00:01:08, GigabitEthernet0/0\n"
                ),
            },
        },
    },

    "node-failure": {
        "title": "シナリオ E: CML ディストリビューションルータ停止 — 10台構成",
        "subtitle": "R4 ノード (n3) を CML で停止。R8/R9 が孤立。4方向クロスチェックで根本原因を特定。",
        # CML 操作: PUT /api/v0/labs/{lab_id}/nodes/n3/state {"state": "stopped"}
        "syslogs": [
            {
                "device": "R1",
                "message": "%OSPF-5-ADJCHG: Process 1, Nbr 4.4.4.4 on GigabitEthernet0/2 from FULL to DOWN, Neighbor Down: Dead timer expired",
                "severity": "5",
            },
            {
                "device": "R8",
                "message": "%LINK-3-UPDOWN: Interface GigabitEthernet0/0, changed state to down",
                "severity": "3",
            },
            {
                "device": "R9",
                "message": "%LINK-3-UPDOWN: Interface GigabitEthernet0/0, changed state to down",
                "severity": "3",
            },
        ],
        "show_responses": {
            "R1": {
                "show ip ospf neighbor": (
                    "Neighbor ID  Pri  State     Dead Time  Address        Interface\n"
                    "2.2.2.2        1  FULL/-    00:00:38   10.0.12.2      GigabitEthernet0/0\n"
                    "3.3.3.3        1  FULL/-    00:00:36   10.0.13.2      GigabitEthernet0/1\n"
                    "5.5.5.5        1  FULL/-    00:00:39   10.0.15.2      GigabitEthernet0/3\n"
                    "(4.4.4.4 が消失 — R4 停止)\n"
                ),
                "show interfaces brief": (
                    "Interface              IP-Address      OK? Method Status   Protocol\n"
                    "GigabitEthernet0/0     10.0.12.1       YES NVRAM  up       up  \n"
                    "GigabitEthernet0/1     10.0.13.1       YES NVRAM  up       up  \n"
                    "GigabitEthernet0/2     10.0.14.1       YES NVRAM  down     down\n"
                    "GigabitEthernet0/3     10.0.15.1       YES NVRAM  up       up  \n"
                    "Loopback0              1.1.1.1         YES NVRAM  up       up  \n"
                ),
                "show ip route ospf": (
                    "O   2.2.2.2 [110/11] via 10.0.12.2, 00:05:14, GigabitEthernet0/0\n"
                    "O   3.3.3.3 [110/11] via 10.0.13.2, 00:05:14, GigabitEthernet0/1\n"
                    "O   5.5.5.5 [110/11] via 10.0.15.2, 00:05:14, GigabitEthernet0/3\n"
                    "O   6.6.6.6 [110/21] via 10.0.13.2, 00:05:14, GigabitEthernet0/1\n"
                    "O   7.7.7.7 [110/21] via 10.0.13.2, 00:05:14, GigabitEthernet0/1\n"
                    "O   10.10.10.10 [110/21] via 10.0.15.2, 00:05:14, GigabitEthernet0/3\n"
                    "(4.4.4.4 / 8.8.8.8 / 9.9.9.9 の経路が消失)\n"
                ),
            },
            "R2": {
                "show ip ospf neighbor": (
                    "Neighbor ID  Pri  State     Dead Time  Address        Interface\n"
                    "1.1.1.1        1  FULL/-    00:00:38   10.0.12.1      GigabitEthernet0/0\n"
                    "3.3.3.3        1  FULL/-    00:00:36   10.0.23.2      GigabitEthernet0/1\n"
                    "5.5.5.5        1  FULL/-    00:00:39   10.0.25.2      GigabitEthernet0/3\n"
                    "(4.4.4.4 が消失 — R4 停止)\n"
                ),
                "show interfaces brief": (
                    "Interface              IP-Address      OK? Method Status   Protocol\n"
                    "GigabitEthernet0/0     10.0.12.2       YES NVRAM  up       up  \n"
                    "GigabitEthernet0/1     10.0.23.1       YES NVRAM  up       up  \n"
                    "GigabitEthernet0/2     10.0.24.1       YES NVRAM  down     down\n"
                    "GigabitEthernet0/3     10.0.25.1       YES NVRAM  up       up  \n"
                    "Loopback0              2.2.2.2         YES NVRAM  up       up  \n"
                ),
            },
            "R8": {
                "show ip ospf neighbor": "(ネイバーなし — R4 停止により完全孤立)\n",
                "show interfaces brief": (
                    "Interface              IP-Address      OK? Method Status   Protocol\n"
                    "GigabitEthernet0/0     10.0.48.2       YES NVRAM  down     down\n"
                    "Loopback0              8.8.8.8         YES NVRAM  up       up  \n"
                ),
            },
            "R9": {
                "show ip ospf neighbor": "(ネイバーなし — R4 停止により完全孤立)\n",
                "show interfaces brief": (
                    "Interface              IP-Address      OK? Method Status   Protocol\n"
                    "GigabitEthernet0/0     10.0.49.2       YES NVRAM  down     down\n"
                    "Loopback0              9.9.9.9         YES NVRAM  up       up  \n"
                ),
            },
        },
    },
}

# 10台ルータ 3階層型トポロジー (コアリンク断・ノード停止デモ用)
_LARGE_TOPOLOGY = {
    "lab": {"title": "large-demo-lab", "version": "0.1.0"},
    "nodes": [
        # Core layer
        {
            "id": "n0", "label": "R1", "node_definition": "iosv",
            "x": -150, "y": -200,
            "interfaces": [
                {"id": "i0", "label": "GigabitEthernet0/0", "slot": 0, "type": "physical"},
                {"id": "i1", "label": "GigabitEthernet0/1", "slot": 1, "type": "physical"},
                {"id": "i2", "label": "GigabitEthernet0/2", "slot": 2, "type": "physical"},
                {"id": "i3", "label": "GigabitEthernet0/3", "slot": 3, "type": "physical"},
            ],
        },
        {
            "id": "n1", "label": "R2", "node_definition": "iosv",
            "x": 150, "y": -200,
            "interfaces": [
                {"id": "i0", "label": "GigabitEthernet0/0", "slot": 0, "type": "physical"},
                {"id": "i1", "label": "GigabitEthernet0/1", "slot": 1, "type": "physical"},
                {"id": "i2", "label": "GigabitEthernet0/2", "slot": 2, "type": "physical"},
                {"id": "i3", "label": "GigabitEthernet0/3", "slot": 3, "type": "physical"},
            ],
        },
        # Distribution layer
        {
            "id": "n2", "label": "R3", "node_definition": "iosv",
            "x": -300, "y": 0,
            "interfaces": [
                {"id": "i0", "label": "GigabitEthernet0/0", "slot": 0, "type": "physical"},
                {"id": "i1", "label": "GigabitEthernet0/1", "slot": 1, "type": "physical"},
                {"id": "i2", "label": "GigabitEthernet0/2", "slot": 2, "type": "physical"},
                {"id": "i3", "label": "GigabitEthernet0/3", "slot": 3, "type": "physical"},
            ],
        },
        {
            "id": "n3", "label": "R4", "node_definition": "iosv",
            "x": 0, "y": 0,
            "interfaces": [
                {"id": "i0", "label": "GigabitEthernet0/0", "slot": 0, "type": "physical"},
                {"id": "i1", "label": "GigabitEthernet0/1", "slot": 1, "type": "physical"},
                {"id": "i2", "label": "GigabitEthernet0/2", "slot": 2, "type": "physical"},
                {"id": "i3", "label": "GigabitEthernet0/3", "slot": 3, "type": "physical"},
            ],
        },
        {
            "id": "n4", "label": "R5", "node_definition": "iosv",
            "x": 300, "y": 0,
            "interfaces": [
                {"id": "i0", "label": "GigabitEthernet0/0", "slot": 0, "type": "physical"},
                {"id": "i1", "label": "GigabitEthernet0/1", "slot": 1, "type": "physical"},
                {"id": "i2", "label": "GigabitEthernet0/2", "slot": 2, "type": "physical"},
            ],
        },
        # Access layer
        {
            "id": "n5", "label": "R6", "node_definition": "iosv",
            "x": -425, "y": 200,
            "interfaces": [{"id": "i0", "label": "GigabitEthernet0/0", "slot": 0, "type": "physical"}],
        },
        {
            "id": "n6", "label": "R7", "node_definition": "iosv",
            "x": -200, "y": 200,
            "interfaces": [{"id": "i0", "label": "GigabitEthernet0/0", "slot": 0, "type": "physical"}],
        },
        {
            "id": "n7", "label": "R8", "node_definition": "iosv",
            "x": -75, "y": 200,
            "interfaces": [{"id": "i0", "label": "GigabitEthernet0/0", "slot": 0, "type": "physical"}],
        },
        {
            "id": "n8", "label": "R9", "node_definition": "iosv",
            "x": 125, "y": 200,
            "interfaces": [{"id": "i0", "label": "GigabitEthernet0/0", "slot": 0, "type": "physical"}],
        },
        {
            "id": "n9", "label": "R10", "node_definition": "iosv",
            "x": 350, "y": 200,
            "interfaces": [{"id": "i0", "label": "GigabitEthernet0/0", "slot": 0, "type": "physical"}],
        },
    ],
    "links": [
        # Core-Core (シナリオD で停止: l0)
        {"id": "l0",  "n1": "n0", "i1": "i0", "n2": "n1", "i2": "i0", "label": "l0"},   # R1-R2
        # Core-Distribution (R1 側)
        {"id": "l1",  "n1": "n0", "i1": "i1", "n2": "n2", "i2": "i0", "label": "l1"},   # R1-R3
        {"id": "l2",  "n1": "n0", "i1": "i2", "n2": "n3", "i2": "i0", "label": "l2"},   # R1-R4
        {"id": "l3",  "n1": "n0", "i1": "i3", "n2": "n4", "i2": "i0", "label": "l3"},   # R1-R5
        # Core-Distribution (R2 側)
        {"id": "l4",  "n1": "n1", "i1": "i1", "n2": "n2", "i2": "i1", "label": "l4"},   # R2-R3
        {"id": "l5",  "n1": "n1", "i1": "i2", "n2": "n3", "i2": "i1", "label": "l5"},   # R2-R4
        {"id": "l6",  "n1": "n1", "i1": "i3", "n2": "n4", "i2": "i1", "label": "l6"},   # R2-R5
        # Distribution-Access
        {"id": "l7",  "n1": "n2", "i1": "i2", "n2": "n5", "i2": "i0", "label": "l7"},   # R3-R6
        {"id": "l8",  "n1": "n2", "i1": "i3", "n2": "n6", "i2": "i0", "label": "l8"},   # R3-R7
        {"id": "l9",  "n1": "n3", "i1": "i2", "n2": "n7", "i2": "i0", "label": "l9"},   # R4-R8
        {"id": "l10", "n1": "n3", "i1": "i3", "n2": "n8", "i2": "i0", "label": "l10"},  # R4-R9
        {"id": "l11", "n1": "n4", "i1": "i2", "n2": "n9", "i2": "i0", "label": "l11"},  # R5-R10
    ],
}


# ---------------------------------------------------------------------------
# DemoDisplay — リアルタイム会話表示
# ---------------------------------------------------------------------------

class DemoDisplay:
    """エージェント間の対話をリアルタイムで端末に表示するオブザーバー。"""

    _AGENT_COLORS = {
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

    def __init__(self) -> None:
        self._start_time = time.monotonic()
        self._msg_count = 0
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
        print(f"  エージェント間通信: {_c(_BOLD, str(self._msg_count))} 件")
        print()


# ---------------------------------------------------------------------------
# スクリプト実行モード用 MockLLM シーケンス
# ---------------------------------------------------------------------------

_SCRIPTED_SEQUENCES: dict[str, dict[str, list]] = {
    # ── 10台構成: コアリンク断 ─────────────────────────────────────────────────
    # イベントフロー:
    #   R1(syslog) → run_show×2 → TO:R2 (direct only)
    #   R2(bus←R1 direct) → get_interface_status → TO:R1
    #   R1(bus←R2) → TO:ALL + TO:HUMAN
    #   R2(bus←R1 ALL) → TO:LOG (パディング)
    #   R3(bus←R1 ALL) → run_show + TO:LOG
    "core-link-down": {
        "R1": [
            {"name": "run_show",
             "args": {"command": "show ip ospf neighbor"}, "id": "r1_c1"},
            {"name": "get_interface_status", "args": {}, "id": "r1_c2"},
            # Event1 最終: R2 に直接問い合わせ（ALL はここではしない）
            "TO: Agent-R2 | MSG: R1-R2 間コアリンク（Gi0/0, 10.0.12.x）が DOWN しています。"
            "そちらの GigabitEthernet0/0 の状態と OSPF ネイバーを確認してください。",
            # Event2: R2 の返答を受けて診断確定 → ALL ブロードキャスト + HUMAN
            "TO: ALL | MSG: 【診断確定】R1-R2 コアリンク（l0）断。R2 が独立確認。"
            "ディストリビューション経由の迂回経路で継続稼働中。"
            "\nTO: HUMAN | MSG: 【障害診断レポート】\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "症状    : R1-R2 間コアリンク（GigabitEthernet0/0）断絶\n"
            "根本原因: CML リンク l0（R1 Gi0/0 ↔ R2 Gi0/0）の停止または物理断線\n"
            "影響範囲:\n"
            "  - R1-R2 間の直接通信不可\n"
            "  - コアトラフィックがディストリビューション（R3/R4/R5）経由に迂回\n"
            "  - 迂回によるホップ数増加・遅延増大の可能性\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "確認済み（クロスチェック）:\n"
            "  - Agent-R1: Gi0/0 DOWN、R2 の OSPF ネイバー消失（直接確認）\n"
            "  - Agent-R2: Gi0/0 DOWN、R1 の OSPF ネイバー消失（独立確認）\n"
            "  - Agent-R3: R1/R2 双方と OSPF 維持中、迂回経路として機能中\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "推奨対応:\n"
            "  1. CML コンソールでリンク l0 を復旧してください\n"
            "     CML API: PUT /api/v0/labs/{lab_id}/links/l0/state {\"state\": \"started\"}\n"
            "  2. 復旧後: show ip ospf neighbor で R1-R2 間 FULL 状態を確認\n"
            "  3. 物理環境では R1-R2 間のケーブル・SFP・スイッチポートを点検",
            # Event3 以降のパディング（R3 等からの追加バスメッセージに備える）
            "TO: LOG | MSG: 追加報告を受領。調査完了済み。待機中。",
            "TO: LOG | MSG: 待機中。",
        ],
        "R2": [
            {"name": "get_interface_status", "args": {}, "id": "r2_c1"},
            # Event1 最終: R1 へ回答
            "TO: Agent-R1 | MSG: R2 の GigabitEthernet0/0 も DOWN を確認。"
            "R1（1.1.1.1）の OSPF ネイバーが消失しています。"
            "R3/R4/R5 経由の迂回経路は維持中。コアリンク断と判断します。",
            # Event2 以降のパディング（R1 の診断確定 ALL を受けた場合）
            "TO: LOG | MSG: 診断確定の報告を受領。調査完了済み。",
            "TO: LOG | MSG: 待機中。",
        ],
        "R3": [
            # Event1: R1 の診断確定 ALL ブロードキャストを受けた場合
            {"name": "run_show",
             "args": {"command": "show ip route ospf"}, "id": "r3_c1"},
            "TO: LOG | MSG: コアリンク断の診断報告を受領。"
            "R3 は R1/R2 双方と OSPF 維持中。迂回経路として機能中。自装置への影響なし。",
            "TO: LOG | MSG: 待機中。",
        ],
    },

    # ── 10台構成: ノード停止 ────────────────────────────────────────────────
    # イベントフロー:
    #   R1(syslog) → run_show×2 → TO:R2 (direct)
    #   R8(syslog) → get_interface_status → TO:ALL
    #   R9(syslog) → get_interface_status → TO:ALL
    #   R2(bus←R1 direct) → run_show×2 → TO:R1
    #   R1(bus←R2) → TO:HUMAN
    #   R1/R2 に R8/R9 の ALL が届くのでパディング必須
    "node-failure": {
        "R1": [
            {"name": "run_show",
             "args": {"command": "show ip ospf neighbor"}, "id": "r1_c1"},
            {"name": "get_interface_status", "args": {}, "id": "r1_c2"},
            # Event1 最終: R2 に直接問い合わせ
            "TO: Agent-R2 | MSG: R4 の OSPF ネイバーが消失し Gi0/2 が DOWN しました。"
            "R2 側から見た R4 の状態を確認してください。",
            # Event2: R2 の返答を受けて HUMAN 報告
            "TO: HUMAN | MSG: 【重大障害診断レポート】\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "症状    : ディストリビューションルータ R4 の完全停止\n"
            "根本原因: CML ノード n3（R4）の停止またはハードウェア障害\n"
            "影響範囲:\n"
            "  - R8（10.0.48.2）: 完全孤立 → サービス断\n"
            "  - R9（10.0.49.2）: 完全孤立 → サービス断\n"
            "  - R1/R2 の R4 方向接続断（Gi0/2）\n"
            "  - R4 経由のすべての OSPF 経路が消失\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "確認済み（4方向クロスチェック）:\n"
            "  - Agent-R1: R4 OSPF 消失、Gi0/2 DOWN（直接確認）\n"
            "  - Agent-R2: R4 OSPF 消失、Gi0/2 DOWN（独立確認）\n"
            "  - Agent-R8: Gi0/0 DOWN、完全孤立を自己報告\n"
            "  - Agent-R9: Gi0/0 DOWN、完全孤立を自己報告\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "推奨対応（優先度順）:\n"
            "  1. 【緊急】CML コンソールで R4 ノードを再起動してください\n"
            "     CML API: PUT /api/v0/labs/{lab_id}/nodes/n3/state {\"state\": \"started\"}\n"
            "  2. 起動後: show ip ospf neighbor で R1/R2 との FULL 状態を確認\n"
            "  3. ping 8.8.8.8 source 1.1.1.1 で R8 サービス復旧を確認\n"
            "  4. 物理環境では R4 の電源・ハードウェア状態を点検",
            # Event3 以降のパディング（R8/R9 の ALL ブロードキャストを受けた場合）
            "TO: LOG | MSG: R8/R9 の孤立報告を受領。HUMAN への報告は完了済み。",
            "TO: LOG | MSG: 待機中。",
            "TO: LOG | MSG: 待機中。",
        ],
        "R2": [
            {"name": "run_show",
             "args": {"command": "show ip ospf neighbor"}, "id": "r2_c1"},
            {"name": "get_interface_status", "args": {}, "id": "r2_c2"},
            # Event1 最終: R1 へ回答
            "TO: Agent-R1 | MSG: R2 側でも R4（4.4.4.4）の OSPF ネイバーが消失。"
            "GigabitEthernet0/2 が DOWN。R1/R2 双方から R4 が見えなくなりました。"
            "R4 ノード停止と判断。R8/R9 は孤立中と推定されます。",
            # Event2 以降のパディング（R8/R9 の ALL を受けた場合）
            "TO: LOG | MSG: R8/R9 の孤立報告を受領。調査完了済み。",
            "TO: LOG | MSG: 待機中。",
            "TO: LOG | MSG: 待機中。",
        ],
        "R8": [
            {"name": "get_interface_status", "args": {}, "id": "r8_c1"},
            "TO: ALL | MSG: 【孤立警告】R8 の GigabitEthernet0/0 が DOWN。"
            "OSPF ネイバーが全消失。上位ルータ R4 への接続が切断されました。"
            "R8 はネットワークから完全孤立しています。早急な対応を要請します。",
            # Event2 以降のパディング（R9 の ALL を受けた場合）
            "TO: LOG | MSG: R9 の孤立報告を受領。共通の上位障害（R4）と推定。",
            "TO: LOG | MSG: 待機中。",
        ],
        "R9": [
            {"name": "get_interface_status", "args": {}, "id": "r9_c1"},
            "TO: ALL | MSG: 【孤立警告】R9 の GigabitEthernet0/0 が DOWN。"
            "OSPF ネイバーが全消失。上位ルータ R4 への接続が切断されました。"
            "R9 もネットワークから完全孤立しています。R8 と同じ上位障害と判断。",
            # Event2 以降のパディング（R8 の ALL を受けた場合）
            "TO: LOG | MSG: R8 の孤立報告を受領。共通の上位障害（R4）と推定。",
            "TO: LOG | MSG: 待機中。",
        ],
    },
}


# ---------------------------------------------------------------------------
# メインデモ実行
# ---------------------------------------------------------------------------

async def run_demo(scenario_name: str, scripted: bool = False) -> None:
    await run_large_demo(scenario_name, scripted=scripted)


def _make_silent_llm():
    """バスメッセージを受信しても LOG のみ返すサイレント MockLLM を生成する。"""
    from langchain_core.messages import AIMessage

    class _SilentLLM:
        def bind_tools(self, t): return self
        async def ainvoke(self, msgs):
            return AIMessage(content="TO: LOG | MSG: 対象外のため待機中。")
    return _SilentLLM()


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
# 10台構成デモ（core-link-down / node-failure シナリオ専用）
# ---------------------------------------------------------------------------

async def run_large_demo(scenario_name: str, scripted: bool = False) -> None:
    """10台ルータ 3階層型トポロジーでの大規模デモを実行する。"""
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

    _print_banner(scenario["title"], scenario["subtitle"])
    await asyncio.sleep(0.5)

    bus = InMemoryBus()
    await bus.connect()
    recorder = ConversationRecorder(bus)
    await recorder.start()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(_yaml.dump(_LARGE_TOPOLOGY))
        topo_file = f.name

    show_map: dict = scenario.get("show_responses", {})
    config_map: dict = scenario.get("running_config", {})

    def toolkit_factory(device_name: str) -> MockDeviceToolkit:
        return MockDeviceToolkit(
            device_name,
            show_responses=show_map.get(device_name, {}),
            running_config=config_map.get(device_name, "! no config\n"),
            readonly=True,
        )

    llm_map = _build_scripted_llm_map(scenario_name) if scripted else None

    orch = AgentOrchestrator(bus=bus, toolkit_factory=toolkit_factory)

    _print_step("1", "10 台エージェント 起動中...")
    await orch.start_from_topology(topo_file)
    os.unlink(topo_file)

    for agent_id, agent in orch.get_all_agents().items():
        if llm_map and agent.device_name in llm_map:
            agent._llm = llm_map[agent.device_name]
        elif scripted:
            # スクリプト対象外のエージェントは LOG 専用サイレント LLM を設定
            agent._llm = _make_silent_llm()
        agent.on_tool_call = display.on_tool_call

    _print_agent_list(orch)

    async def _on_bus_msg(topic: str, msg: AgentMessage) -> None:
        if msg.to_agent.upper() == "HUMAN":
            display.on_human_escalation(msg.from_agent, msg.content)
        elif msg.to_agent.upper() != "LOG":
            display.on_bus_message(msg.from_agent, msg.to_agent, msg.msg_type, msg.content)

    await bus.subscribe("network/agents/#", _on_bus_msg)

    original_put = orch.human_queue.put_nowait
    def _tracking_put(item):
        original_put(item)
        if "from_agent" in item:
            display.on_human_escalation(item["from_agent"], item.get("content", ""))
        elif item.get("type") == "config_change_request":
            display.on_human_escalation(item.get("device", "?"), item.get("commands", ""))
        display.human_received.set()
    orch.human_queue.put_nowait = _tracking_put

    await asyncio.sleep(0.8)
    _print_step("2", "CML 障害発生 — 複数 syslog 注入")
    # すべての syslog を先に注入してから [3] ヘッダーを表示する
    syslogs = scenario.get("syslogs", [])
    for syslog_cfg in syslogs:
        agent_id = f"Agent-{syslog_cfg['device']}"
        target_agent = orch.get_agent(agent_id)
        if target_agent:
            _print_alert(syslog_cfg["message"])
            await target_agent.inject_event(SyslogEvent(
                raw_text=syslog_cfg["message"],
                severity=syslog_cfg["severity"],
            ))

    print()
    _print_step("3", "10 台エージェント 自律診断開始")
    print()

    try:
        await asyncio.wait_for(display.human_received.wait(), timeout=90.0)
    except asyncio.TimeoutError:
        print(_c(_RED, "\n  [タイムアウト] HUMAN エスカレーションを受信できませんでした。"))

    await asyncio.sleep(0.5)
    display.print_summary()

    _print_step("4", "診断レポート生成")
    report = generate_report(
        orch, recorder,
        topology_path="demo-large/topology.yaml",
        scenario_name=scenario["title"],
    )
    saved = save_report(report, prefix="demo-large")
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
        "--scenario", default="core-link-down",
        choices=list(_SCENARIOS.keys()),
        help="デモシナリオ（デフォルト: core-link-down）",
    )
    parser.add_argument(
        "--scripted", action="store_true",
        help="API キー不要のスクリプト実行モード",
    )
    args = parser.parse_args()

    asyncio.run(run_demo(args.scenario, scripted=args.scripted))


if __name__ == "__main__":
    main()
