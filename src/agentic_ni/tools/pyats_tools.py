"""pyATS/Genie を使ったネットワーク検証ツール群。

pyATS と Genie はオプション依存のため、インポートはすべて遅延評価する。
インストール: pip install pyats genie
"""

from __future__ import annotations

import io
import textwrap
from typing import Any

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# 内部ヘルパー
# ---------------------------------------------------------------------------

_PYATS_IMPORT_ERROR = (
    "pyATS/Genie が必要です。Phase 4 用にインストールしてください:\n"
    "  .venv/bin/pip install pyats genie"
)


def _require_pyats() -> Any:
    """pyATSのloaderモジュールを返す（未インストール時は明示的エラー）。"""
    try:
        from pyats.topology import loader  # type: ignore[import-untyped]

        return loader
    except ImportError as exc:
        raise ImportError(_PYATS_IMPORT_ERROR) from exc


def _load_testbed(testbed_yaml: str) -> Any:
    """YAML文字列からpyATSテストベッドオブジェクトを生成して返す。"""
    loader = _require_pyats()
    return loader.load(io.StringIO(testbed_yaml))


def _connect_device(testbed: Any, device_name: str) -> Any:
    """テストベッドから指定デバイスに接続して返す。

    Raises:
        KeyError: デバイス名がテストベッドに存在しない場合。
    """
    if device_name not in testbed.devices:
        raise KeyError(f"デバイスがテストベッドに見つかりません: {device_name!r}")
    device = testbed.devices[device_name]
    device.connect(log_stdout=False)
    return device


# ---------------------------------------------------------------------------
# 公開ツール関数
# ---------------------------------------------------------------------------


def build_testbed(lab_id: str, device_configs: dict[str, str]) -> str:
    """CMLのラボ情報から pyATS テストベッド YAML を生成する。

    virl2_client の `Lab.get_pyats_testbed()` を使用して
    CMLのコンソールサーバー接続情報を含むテストベッドを取得する。
    pyATS のインストールは不要（virl2_client のみ使用）。

    生成されたYAMLには terminal_server の認証情報がプレースホルダー
    ("change_me") になっている場合があるため、.env の CML 認証情報で上書きする。
    また pyATS が servers: セクションでプロキシを探すため、同情報を追加する。

    Args:
        lab_id: テストベッドを生成する対象ラボのID。
        device_configs: 機器名 → コンフィグテキストのマッピング。

    Returns:
        str: pyATS テストベッド YAML 文字列。

    Raises:
        EnvironmentError: CML接続情報が未設定の場合。
        KeyError: lab_id が存在しない場合。
    """
    import os
    import yaml as _yaml
    from agentic_ni.tools.cml_tools import _get_client, _get_lab

    client = _get_client()
    lab = _get_lab(client, lab_id)
    testbed_yaml = lab.get_pyats_testbed()

    # --- testbed YAML のパッチ処理 ---
    data = _yaml.safe_load(testbed_yaml)
    cml_username = os.getenv("CML_USERNAME", "")
    cml_password = os.getenv("CML_PASSWORD", "")

    ts_device = data.get("devices", {}).get("terminal_server")
    if ts_device:
        # 1) change_me の認証情報を実際の CML 認証情報で上書き
        ts_creds = ts_device.setdefault("credentials", {})
        if not ts_creds.get("default") or ts_creds["default"].get("username") == "change_me":
            ts_creds["default"] = {
                "username": cml_username,
                "password": cml_password,
            }

        # 2) pyATS が proxy を servers: セクションで探すため、同内容を追加
        data.setdefault("servers", {})["terminal_server"] = ts_device

    return _yaml.dump(data, default_flow_style=False, allow_unicode=True)


def run_show_command(testbed_yaml: str, device_name: str, command: str) -> dict:
    """showコマンドを実行し、Genie でパースした結果を返す。

    Genie がパーサーを持つコマンドは構造化dictで返る。
    パーサーが存在しない場合は {"raw_output": <テキスト>} を返す。

    Args:
        testbed_yaml: pyATS テストベッド YAML 文字列。
        device_name: コマンドを実行するデバイス名。
        command: 実行する show コマンド（例: "show ip ospf neighbor detail"）。

    Returns:
        dict: パース済み出力。

    Raises:
        ImportError: pyATS/Genie が未インストールの場合。
        KeyError: デバイス名がテストベッドに存在しない場合。
    """
    testbed = _load_testbed(testbed_yaml)
    device = _connect_device(testbed, device_name)
    try:
        try:
            output = device.parse(command)
        except Exception:
            # Genie パーサーが存在しない場合はテキスト出力をそのまま返す
            raw = device.execute(command)
            output = {"raw_output": raw}
        return output
    finally:
        device.disconnect()


def check_ospf_neighbors(testbed_yaml: str, device_name: str) -> dict:
    """OSPFネイバーの状態を確認する。

    Args:
        testbed_yaml: pyATS テストベッド YAML 文字列。
        device_name: 確認するデバイス名。

    Returns:
        dict: Genie パース済みのOSPFネイバー情報。
              キー "neighbors_up" に起動中ネイバー数が含まれる。
    """
    raw = run_show_command(testbed_yaml, device_name, "show ip ospf neighbor detail")

    # Genie パース結果から要約情報を抽出
    neighbors_up = 0
    neighbor_states: list[dict] = []

    # Genie の "show ip ospf neighbor detail" パース結果の構造:
    # { "vrf": { <vrf>: { "address_family": { <af>: { "instance": { <pid>:
    #   { "areas": { <area>: { "interfaces": { <intf>: { "neighbors": {
    #     <nbr_id>: { "state": "FULL/...", ... }
    # }}}}}}}}}}}
    try:
        for vrf_data in raw.get("vrf", {}).values():
            for af_data in vrf_data.get("address_family", {}).values():
                for inst_data in af_data.get("instance", {}).values():
                    for area_data in inst_data.get("areas", {}).values():
                        for intf_data in area_data.get("interfaces", {}).values():
                            for nbr_id, nbr_data in intf_data.get("neighbors", {}).items():
                                state = nbr_data.get("state", "UNKNOWN")
                                neighbor_states.append(
                                    {"neighbor_id": nbr_id, "state": state}
                                )
                                if "FULL" in state.upper():
                                    neighbors_up += 1
    except (AttributeError, TypeError):
        pass

    return {
        "neighbors_up": neighbors_up,
        "neighbors": neighbor_states,
        "raw": raw,
    }


def check_bgp_summary(testbed_yaml: str, device_name: str) -> dict:
    """BGPピアの接続状態を確認する。

    Args:
        testbed_yaml: pyATS テストベッド YAML 文字列。
        device_name: 確認するデバイス名。

    Returns:
        dict: BGP ピアの概要。"peers_established" にEstablished状態のピア数を含む。
    """
    raw = run_show_command(testbed_yaml, device_name, "show bgp all summary")

    peers_established = 0
    peer_states: list[dict] = []

    # Genie パース結果構造:
    # { "vrf": { <vrf>: { "neighbor": { <ip>: {
    #   "address_family": { <af>: { "state_pfxrcd": <int|str>, ... } }
    # }}}}
    try:
        for vrf_data in raw.get("vrf", {}).values():
            for peer_ip, peer_data in vrf_data.get("neighbor", {}).items():
                for af_data in peer_data.get("address_family", {}).values():
                    state = af_data.get("state_pfxrcd", "")
                    # 数値ならEstablished（受信プレフィックス数が入る）
                    is_established = isinstance(state, int) or (
                        isinstance(state, str) and state.isdigit()
                    )
                    peer_states.append(
                        {"peer": peer_ip, "established": is_established, "state": state}
                    )
                    if is_established:
                        peers_established += 1
    except (AttributeError, TypeError):
        pass

    return {
        "peers_established": peers_established,
        "peers": peer_states,
        "raw": raw,
    }


def check_ping(testbed_yaml: str, device_name: str, target: str) -> bool:
    """指定ターゲットへの疎通確認（ping）を実行する。

    Args:
        testbed_yaml: pyATS テストベッド YAML 文字列。
        device_name: ping を実行するデバイス名。
        target: ping 先の IP アドレスまたはホスト名。

    Returns:
        bool: ping が成功（1パケット以上受信）した場合 True。
    """
    testbed = _load_testbed(testbed_yaml)
    device = _connect_device(testbed, device_name)
    try:
        output: str = device.execute(f"ping {target}")
        # "Success rate is X percent" から成否を判定
        # IOS/IOS-XE: "Success rate is 100 percent (5/5)"
        # 0% は失敗扱い
        lower = output.lower()
        if "success rate is 0" in lower or "unreachable" in lower:
            return False
        if "success rate is" in lower:
            return True
        # 感嘆符(!)が含まれていればパケット受信あり
        return "!" in output
    finally:
        device.disconnect()


def check_vlan_interfaces(testbed_yaml: str, device_name: str) -> dict:
    """VLANおよびインターフェースの状態を確認する。

    Args:
        testbed_yaml: pyATS テストベッド YAML 文字列。
        device_name: 確認するデバイス名（主にスイッチ）。

    Returns:
        dict: VLAN情報とインターフェース状態。
              "vlans": VLAN ID→状態のマッピング
              "interfaces_up": UP状態のインターフェース数
    """
    vlan_raw = run_show_command(testbed_yaml, device_name, "show vlan brief")
    intf_raw = run_show_command(testbed_yaml, device_name, "show interfaces summary")

    vlans: dict[str, str] = {}
    interfaces_up = 0

    # VLAN情報の抽出
    # Genie パース結果: { "vlans": { <vlan_id>: { "name": ..., "state": ... } } }
    try:
        for vlan_id, vlan_data in vlan_raw.get("vlans", {}).items():
            vlans[vlan_id] = vlan_data.get("state", "unknown")
    except (AttributeError, TypeError):
        pass

    # インターフェースUP数の抽出
    # Genie パース結果: { "interfaces": { <name>: { "line_protocol": "up", ... } } }
    try:
        for intf_data in intf_raw.get("interfaces", {}).values():
            if intf_data.get("line_protocol", "").lower() == "up":
                interfaces_up += 1
    except (AttributeError, TypeError):
        pass

    return {
        "vlans": vlans,
        "interfaces_up": interfaces_up,
        "vlan_raw": vlan_raw,
        "intf_raw": intf_raw,
    }
