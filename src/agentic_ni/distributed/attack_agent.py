"""攻撃エージェント — デモンストレーション用ネットワーク障害シミュレーター。

``AttackAgent`` は障害を時間差で発動し、分散型トラブルシューティングエージェントが
自律的に検知・診断・報告する様子を視覚的に示す。

設計思想:
  - 実際のネットワーク装置には一切触れない（完全シミュレーション）
  - ``MockDeviceToolkit.update_show_responses()`` で show コマンド応答を差し替え
  - 対象 DeviceAgent に SyslogEvent を直接注入してリアルな障害を模擬する

デモシナリオ:
  - ``DUAL_LINK_DOWN_CAMPAIGN`` : R1 が 2 本のリンクを時間差で失い孤立する
  - ``OSPF_STORM_CAMPAIGN``     : 3 装置の OSPF タイマーが同時にミスマッチになる
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from agentic_ni.distributed.device_agent import SyslogEvent

if TYPE_CHECKING:
    from agentic_ni.distributed.device_tools import MockDeviceToolkit
    from agentic_ni.distributed.orchestrator import AgentOrchestrator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Attack データクラス
# ---------------------------------------------------------------------------

@dataclass
class AttackEvent:
    """1 台の装置に対して発動する単一攻撃イベント。"""

    target: str
    """被害装置のラベル（例: "R1"）。"""

    syslog: str | None = None
    """注入する syslog メッセージ。None の場合は注入しない（ツールキット更新のみ）。"""

    severity: str = "3"
    """syslog の重大度（1=Emergency … 7=Debug）。"""

    toolkit_update: dict[str, str] = field(default_factory=dict)
    """show コマンド → 応答 のマッピング。攻撃後の応答に書き換える。"""


@dataclass
class Attack:
    """1 つの攻撃シナリオ（複数装置・複数コマンドの更新をまとめる）。"""

    attack_id: str
    description: str
    events: list[AttackEvent]


# ---------------------------------------------------------------------------
# 事前定義シナリオ
# ---------------------------------------------------------------------------

# 三角形トポロジー（R1-R2-R3）向け二重リンク断
# Attack 1: R1-R2 リンク断（t=0）
ATTACK_R1_R2_DOWN = Attack(
    attack_id="r1_r2_down",
    description="R1-R2 リンク断（GigabitEthernet0/0）",
    events=[
        AttackEvent(
            target="R1",
            syslog="%LINK-3-UPDOWN: Interface GigabitEthernet0/0, changed state to down",
            severity="3",
            toolkit_update={
                "show interfaces brief": (
                    "GigabitEthernet0/0  unassigned   YES  down    down  ← 障害中\n"
                    "GigabitEthernet0/1  10.0.13.1    YES  up      up\n"
                    "Loopback0           1.1.1.1      YES  up      up\n"
                ),
                "show ip ospf neighbor": (
                    "Neighbor ID   Pri  State     Dead Time  Address    Interface\n"
                    "3.3.3.3         1  FULL/DR   00:00:36   10.0.13.2  GigabitEthernet0/1\n"
                ),
            },
        ),
        AttackEvent(
            target="R2",
            syslog=None,  # R2 は syslog を受け取らない（R1 が問い合わせて検知させる）
            toolkit_update={
                "show interfaces brief": (
                    "GigabitEthernet0/0  unassigned   YES  down    down  ← 障害中\n"
                    "GigabitEthernet0/1  10.0.23.1    YES  up      up\n"
                    "Loopback0           2.2.2.2      YES  up      up\n"
                ),
                "show ip ospf neighbor": (
                    "Neighbor ID   Pri  State     Dead Time  Address    Interface\n"
                    "3.3.3.3         1  FULL/DR   00:00:38   10.0.23.2  GigabitEthernet0/1\n"
                ),
            },
        ),
    ],
)

# Attack 2: R1-R3 リンクも断（t=3s） → R1 が完全孤立
ATTACK_R1_R3_DOWN = Attack(
    attack_id="r1_r3_down",
    description="R1-R3 リンク断（GigabitEthernet0/1）— 二重障害！R1 完全孤立！",
    events=[
        AttackEvent(
            target="R1",
            syslog="%LINK-3-UPDOWN: Interface GigabitEthernet0/1, changed state to down",
            severity="3",
            toolkit_update={
                "show interfaces brief": (
                    "GigabitEthernet0/0  unassigned   YES  down    down  ← 障害中\n"
                    "GigabitEthernet0/1  unassigned   YES  down    down  ← 障害中\n"
                    "Loopback0           1.1.1.1      YES  up      up\n"
                ),
                "show ip ospf neighbor": "（ネイバーなし — R1 完全孤立）\n",
                "show ip route": "（ルーティングテーブルなし — 孤立状態）\n",
            },
        ),
        AttackEvent(
            target="R3",
            syslog=None,
            toolkit_update={
                "show interfaces brief": (
                    "GigabitEthernet0/0  unassigned   YES  down    down  ← R1方向 障害中\n"
                    "GigabitEthernet0/1  10.0.23.2    YES  up      up\n"
                    "Loopback0           3.3.3.3      YES  up      up\n"
                ),
                "show ip ospf neighbor": (
                    "Neighbor ID   Pri  State     Dead Time  Address    Interface\n"
                    "2.2.2.2         1  FULL/DR   00:00:37   10.0.23.1  GigabitEthernet0/1\n"
                ),
            },
        ),
    ],
)

# 二重リンク断キャンペーン: t=0 で第一撃、t=3s で第二撃
DUAL_LINK_DOWN_CAMPAIGN: list[tuple[float, Attack]] = [
    (0.0, ATTACK_R1_R2_DOWN),
    (3.5, ATTACK_R1_R3_DOWN),
]


# ---------------------------------------------------------------------------
# AttackAgent
# ---------------------------------------------------------------------------

class AttackAgent:
    """ネットワーク障害を模擬発動するデモ専用エージェント。

    Args:
        orchestrator: 防御側エージェント群を管理するオーケストレーター。
        toolkits:     装置名 → MockDeviceToolkit のマッピング。
        on_attack:    攻撃発動時に呼ばれるコールバック（表示用）。
    """

    def __init__(
        self,
        orchestrator: "AgentOrchestrator",
        toolkits: dict[str, "MockDeviceToolkit"],
        on_attack: Callable[[Attack], None] | None = None,
    ) -> None:
        self._orch = orchestrator
        self._toolkits = toolkits
        self._on_attack = on_attack
        self._active: dict[str, Attack] = {}

    async def launch(self, attack: Attack, delay: float = 0.0) -> None:
        """攻撃を発動する。delay 秒待ってから実行する。"""
        if delay > 0:
            await asyncio.sleep(delay)

        logger.info("【攻撃】%s: %s", attack.attack_id, attack.description)
        if self._on_attack:
            self._on_attack(attack)

        for ev in attack.events:
            # ツールキットを「障害状態」に更新
            tk = self._toolkits.get(ev.target)
            if tk and ev.toolkit_update:
                tk.update_show_responses(ev.toolkit_update)
                logger.debug(
                    "  [%s] ツールキット更新: %s",
                    ev.target, list(ev.toolkit_update.keys()),
                )

            # syslog を対象エージェントに注入
            if ev.syslog:
                try:
                    agent = self._orch.get_agent(f"Agent-{ev.target}")
                    await agent.inject_event(SyslogEvent(
                        raw_text=ev.syslog, severity=ev.severity
                    ))
                    logger.debug("  [%s] syslog 注入: %s", ev.target, ev.syslog[:60])
                except KeyError:
                    logger.warning("  エージェント Agent-%s が見つかりません", ev.target)

        self._active[attack.attack_id] = attack

    async def run_campaign(
        self,
        campaign: list[tuple[float, Attack]],
    ) -> None:
        """時間差で複数の攻撃を発動するキャンペーンを実行する。

        Args:
            campaign: (発動遅延秒数, Attack) のリスト。
        """
        tasks = [
            asyncio.create_task(self.launch(attack, delay))
            for delay, attack in campaign
        ]
        await asyncio.gather(*tasks)

    @property
    def active_attacks(self) -> dict[str, Attack]:
        return dict(self._active)
