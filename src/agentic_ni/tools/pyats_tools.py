"""pyATS/Genie を使ったネットワーク検証ツール群。

pyATS と Genie はオプション依存のため、インポートはすべて遅延評価する。
インストール: pip install pyats genie
"""

from __future__ import annotations

import io
import ipaddress
import logging
import os
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

# ログ保存先ディレクトリ（プロジェクトルートの logs/）
_LOGS_DIR = Path(__file__).parent.parent.parent.parent / "logs"

# セッションごとのログファイル（初回呼び出し時に確定）
_session_log_path: Path | None = None


def _setup_pyats_file_logging() -> Path:
    """初回呼び出し時にディレクトリとログファイルを作成し、ロガーを設定する。

    同一セッション内では同じファイルに追記する。

    Returns:
        Path: 作成または再利用したログファイルのパス。
    """
    global _session_log_path
    if _session_log_path is not None:
        return _session_log_path

    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = _LOGS_DIR / f"agentic-ni-{timestamp}.log"

    # ファイルハンドラーを作成
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter("%(asctime)s  %(name)s  %(levelname)s  %(message)s")
    )

    # pyATS / Unicon 関連ロガーにファイルハンドラーを追加
    for logger_name in ("unicon", "pyats", "genie"):
        lgr = logging.getLogger(logger_name)
        lgr.setLevel(logging.DEBUG)
        if not any(isinstance(h, logging.FileHandler) and h.baseFilename == str(log_path)
                   for h in lgr.handlers):
            lgr.addHandler(handler)

    _session_log_path = log_path
    return log_path

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

    # ログファイルの初期化（初回のみ）
    log_path = _setup_pyats_file_logging()
    logging.getLogger("pyats").info("build_testbed: lab_id=%s  log=%s", lab_id, log_path)

    # --- testbed YAML のパッチ処理 ---
    data = _yaml.safe_load(testbed_yaml)
    cml_username = os.getenv("CML_USERNAME", "")
    cml_password = os.getenv("CML_PASSWORD", "")

    ts_device = data.get("devices", {}).get("terminal_server")
    if ts_device:
        # change_me の認証情報を実際の CML 認証情報で上書き
        ts_creds = ts_device.setdefault("credentials", {})
        if not ts_creds.get("default") or ts_creds["default"].get("username") == "change_me":
            ts_creds["default"] = {
                "username": cml_username,
                "password": cml_password,
            }

    return _yaml.dump(data, default_flow_style=False, allow_unicode=True)


def _device_type_to_pyats_os(device_type: str) -> str:
    """Netmiko の device_type 文字列を pyATS の ``os`` フィールド値に変換する。

    対応表に存在しない device_type は ``"ios"`` にフォールバックする。
    """
    mapping: dict[str, str] = {
        "cisco_ios": "ios",
        "cisco_xe": "iosxe",
        "cisco_ios_xe": "iosxe",
        "cisco_nxos": "nxos",
        "cisco_nxos_ssh": "nxos",
        "cisco_xr": "iosxr",
        "arista_eos": "eos",
        "juniper_junos": "junos",
    }
    return mapping.get(device_type.lower(), "ios")


def build_testbed_from_inventory(devices: dict[str, dict]) -> str:
    """インベントリ辞書から pyATS テストベッド YAML を生成する（実機接続用）。

    CML 経由ではなく SSH で直接接続するテストベッドを生成する。
    ``--live-verify`` オプション使用時に :func:`live_verify_node` から呼び出される。

    Args:
        devices: :func:`~agentic_ni.tools.netmiko_tools.load_inventory` が返すデバイス辞書。
                 各エントリは ``{host, device_type, username, password, port, ...}``。

    Returns:
        str: pyATS テストベッド YAML 文字列。
    """
    import yaml as _yaml

    testbed_devices: dict = {}
    for name, cfg in devices.items():
        os_type = _device_type_to_pyats_os(cfg.get("device_type", "cisco_ios"))
        testbed_devices[name] = {
            "os": os_type,
            "type": "router",
            "credentials": {
                "default": {
                    "username": cfg["username"],
                    "password": cfg["password"],
                }
            },
            "connections": {
                "default": {
                    "class": "unicon.Unicon",
                    "protocol": "ssh",
                    "ip": cfg["host"],
                    "port": int(cfg.get("port", 22)),
                }
            },
        }

    return _yaml.dump(
        {"devices": testbed_devices},
        default_flow_style=False,
        allow_unicode=True,
    )


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


def check_route_table(testbed_yaml: str, device_name: str, prefix: str) -> dict:
    """ルーティングテーブルに指定プレフィックスが存在するかを確認する。

    Args:
        testbed_yaml: pyATS テストベッド YAML 文字列。
        device_name: 確認するデバイス名。
        prefix: 確認するプレフィックス（例: "1.1.1.1/32", "10.0.0.0/8", "192.168.1.1"）。
                CIDR 表記は内部でドット付き 10 進マスク形式に変換する。

    Returns:
        dict:
            "found": bool — プレフィックスが存在するか
            "protocol": str — 学習プロトコル（"ospf", "bgp", "static", "connected" 等）
            "next_hop": str — ネクストホップアドレス（存在する場合）
            "raw": dict — Genie パース結果
    """
    # IOS-XE は "show ip route 1.1.1.1/32" を受け付けないため、
    # CIDR 表記をドット付き 10 進マスク形式に変換する
    # 例: "1.1.1.1/32" → "1.1.1.1 255.255.255.255"
    #     "10.0.0.0/8"  → "10.0.0.0 255.0.0.0"
    if "/" in prefix:
        try:
            net = ipaddress.ip_network(prefix, strict=False)
            show_arg = f"{net.network_address} {net.netmask}"
        except ValueError:
            show_arg = prefix.split("/")[0]  # フォールバック: マスク部分を除去
    else:
        show_arg = prefix

    raw = run_show_command(testbed_yaml, device_name, f"show ip route {show_arg}")

    found = False
    protocol = ""
    next_hop = ""

    # Genie パース結果構造:
    # { "entry": { <prefix>: { "ip": "...", "mask": "...",
    #   "next_hop": { "next_hop_list": { 1: { "next_hop": "...", ... } } },
    #   "source_protocol": "ospf", ... } } }
    try:
        for entry_prefix, entry_data in raw.get("entry", {}).items():
            if prefix.split("/")[0] in entry_prefix or entry_prefix in prefix:
                found = True
                protocol = entry_data.get("source_protocol", "")
                nh_list = entry_data.get("next_hop", {}).get("next_hop_list", {})
                if nh_list:
                    first = next(iter(nh_list.values()), {})
                    next_hop = first.get("next_hop", "")
                break
    except (AttributeError, TypeError):
        pass

    # パース失敗時はraw出力のテキストから判定
    if not found and isinstance(raw, str):
        found = prefix.split("/")[0] in raw and "%" not in raw

    return {
        "found": found,
        "protocol": protocol,
        "next_hop": next_hop,
        "raw": raw,
    }


def check_interface_status(testbed_yaml: str, device_name: str, interface: str) -> dict:
    """指定インターフェースの up/down 状態を確認する。

    Args:
        testbed_yaml: pyATS テストベッド YAML 文字列。
        device_name: 確認するデバイス名。
        interface: インターフェース名（例: "GigabitEthernet0/0", "Loopback0"）。

    Returns:
        dict:
            "line_up": bool — ライン状態が up か
            "protocol_up": bool — プロトコル状態が up か
            "both_up": bool — 両方 up か
            "raw": dict — Genie パース結果
    """
    raw = run_show_command(testbed_yaml, device_name, f"show interfaces {interface}")

    line_up = False
    protocol_up = False

    # Genie パース結果構造:
    # { "GigabitEthernet0/0": { "enabled": true, "line_protocol": "up",
    #   "oper_status": "up", ... } }
    try:
        # Genie は { "GigabitEthernet0/0": { "oper_status": "up", ... } } を返す
        intf_data = raw.get(interface)
        if isinstance(intf_data, dict):
            line_up = intf_data.get("oper_status", "").lower() == "up"
            protocol_up = intf_data.get("line_protocol", "").lower() == "up"
        else:
            # インターフェース名が省略形の場合など、最初に見つかったエントリを使用
            for val in raw.values():
                if isinstance(val, dict):
                    line_up = val.get("oper_status", "").lower() == "up"
                    protocol_up = val.get("line_protocol", "").lower() == "up"
                    break
    except (AttributeError, TypeError):
        pass

    return {
        "line_up": line_up,
        "protocol_up": protocol_up,
        "both_up": line_up and protocol_up,
        "raw": raw,
    }


def check_traceroute(testbed_yaml: str, device_name: str, target: str) -> dict:
    """traceroute を実行して到達可否と最終ホップを確認する。

    Args:
        testbed_yaml: pyATS テストベッド YAML 文字列。
        device_name: traceroute を実行するデバイス名。
        target: 宛先 IP アドレス。

    Returns:
        dict:
            "reached": bool — 宛先に到達できたか
            "hops": list[str] — ホップのIPアドレスリスト
            "hop_count": int — ホップ数
            "raw_output": str — show コマンドの生テキスト出力
    """
    testbed = _load_testbed(testbed_yaml)
    device = _connect_device(testbed, device_name)
    try:
        output: str = device.execute(f"traceroute {target}")
        lines = output.splitlines()
        hops: list[str] = []
        reached = False

        import re
        for line in lines:
            # IOS traceroute出力: "  1  10.0.12.2  4 msec  4 msec  4 msec"
            match = re.search(r"\d+\s+(\d+\.\d+\.\d+\.\d+)", line)
            if match:
                hop_ip = match.group(1)
                if hop_ip not in hops:
                    hops.append(hop_ip)
                if hop_ip == target:
                    reached = True

        return {
            "reached": reached,
            "hops": hops,
            "hop_count": len(hops),
            "raw_output": output,
        }
    finally:
        device.disconnect()


def check_bgp_path(testbed_yaml: str, device_name: str, prefix: str) -> dict:
    """BGP ルーティングテーブルで指定プレフィックスの best path を確認する。

    Args:
        testbed_yaml: pyATS テストベッド YAML 文字列。
        device_name: 確認するデバイス名。
        prefix: 確認するプレフィックス（例: "2.2.2.2/32"）。

    Returns:
        dict:
            "found": bool — プレフィックスが BGP テーブルに存在するか
            "best_next_hop": str — best path のネクストホップ
            "origin": str — BGP origin (i=IGP, e=EGP, ?=incomplete)
            "local_pref": int | None — Local preference 値
            "raw": dict — Genie パース結果
    """
    raw = run_show_command(testbed_yaml, device_name, f"show bgp all {prefix}")

    found = False
    best_next_hop = ""
    origin = ""
    local_pref = None

    # Genie パース結果構造:
    # { "vrf": { "default": { "address_family": { "ipv4 unicast": {
    #   "prefixes": { <prefix>: { "paths": { 1: {
    #     "best_path": true, "next_hop": "...", "origin_codes": "i", ... } } } } } } } } }
    try:
        for vrf_data in raw.get("vrf", {}).values():
            for af_data in vrf_data.get("address_family", {}).values():
                for pfx, pfx_data in af_data.get("prefixes", {}).items():
                    if prefix.split("/")[0] in pfx or pfx in prefix:
                        found = True
                        for path_data in pfx_data.get("paths", {}).values():
                            if path_data.get("best_path"):
                                best_next_hop = path_data.get("next_hop", "")
                                origin = path_data.get("origin_codes", "")
                                local_pref = path_data.get("localpref")
                                break
                        break
    except (AttributeError, TypeError):
        pass

    return {
        "found": found,
        "best_next_hop": best_next_hop,
        "origin": origin,
        "local_pref": local_pref,
        "raw": raw,
    }


def configure_interface_shutdown(
    testbed_yaml: str,
    device_name: str,
    interface: str,
    shutdown: bool,
) -> None:
    """IOS 機器のインターフェースを shutdown / no shutdown する。

    インターフェースを管理的に down させることで「ケーブル抜き」相当の障害を
    模擬する。CML の損失条件（loss=100%）と異なり、line protocol が down になるため
    OSPF が即時に neighbor を削除しルーティングを再収束させる。

    Args:
        testbed_yaml: pyATS テストベッド YAML 文字列。
        device_name: 対象デバイス名（ノードラベルと一致）。
        interface: インターフェース名（例: "GigabitEthernet0/0"）。
        shutdown: True でシャットダウン、False で no shutdown（復旧）。

    Raises:
        KeyError: デバイス名がテストベッドに存在しない場合。
        ImportError: pyATS/Genie が未インストールの場合。
    """
    testbed = _load_testbed(testbed_yaml)
    dev = _connect_device(testbed, device_name)
    try:
        state_cmd = "shutdown" if shutdown else "no shutdown"
        dev.configure(f"interface {interface}\n {state_cmd}")
    finally:
        try:
            dev.disconnect()
        except Exception:  # noqa: BLE001
            pass


def get_running_config(testbed_yaml: str, device_name: str) -> str:
    """稼働中のデバイスから running-config を取得する。

    Args:
        testbed_yaml: pyATS テストベッド YAML 文字列。
        device_name: 対象デバイス名。

    Returns:
        str: `show running-config` の出力テキスト。

    Raises:
        KeyError: デバイス名がテストベッドに存在しない場合。
        ImportError: pyATS/Genie が未インストールの場合。
    """
    testbed = _load_testbed(testbed_yaml)
    dev = _connect_device(testbed, device_name)
    try:
        return dev.execute("show running-config")
    finally:
        try:
            dev.disconnect()
        except Exception:  # noqa: BLE001
            pass


def apply_incremental_config(
    testbed_yaml: str,
    device_name: str,
    config_commands: str,
) -> None:
    """稼働中のデバイスにインクリメンタルなコンフィグを投入する。

    `configure terminal` モードで指定コマンドを流し込む。
    Day-0 config（wipe + restart）とは異なり、ルーターを再起動せずに
    差分だけを適用できる。

    Args:
        testbed_yaml: pyATS テストベッド YAML 文字列。
        device_name: 対象デバイス名。
        config_commands: configure terminal に流す複数行コマンド。

    Raises:
        KeyError: デバイス名がテストベッドに存在しない場合。
        ImportError: pyATS/Genie が未インストールの場合。
        Exception: コンフィグ投入に失敗した場合。
    """
    testbed = _load_testbed(testbed_yaml)
    dev = _connect_device(testbed, device_name)
    try:
        dev.configure(config_commands)
    finally:
        try:
            dev.disconnect()
        except Exception:  # noqa: BLE001
            pass


def collect_device_state(testbed_yaml: str, device_name: str) -> dict:
    """デバイスの現在状態（running-config と主要 show コマンド）を収集する。

    トラブルシューティングモードで LLM に与えるコンテキストを取得するために使用する。
    接続は1回で複数コマンドを実行し、最後に切断する。

    Args:
        testbed_yaml: pyATS テストベッド YAML 文字列。
        device_name: 対象デバイス名。

    Returns:
        dict: {
            "running_config": str,
            "show_outputs": {
                "show ip ospf neighbor": str,
                "show ip route": str,
                "show ip interface brief": str,
            }
        }

    Raises:
        KeyError: デバイス名がテストベッドに存在しない場合。
        ImportError: pyATS/Genie が未インストールの場合。
    """
    testbed = _load_testbed(testbed_yaml)
    dev = _connect_device(testbed, device_name)
    try:
        running_config = dev.execute("show running-config")
        show_outputs: dict[str, str] = {}
        for cmd in (
            "show ip ospf neighbor",
            "show ip route",
            "show ip interface brief",
        ):
            try:
                show_outputs[cmd] = dev.execute(cmd)
            except Exception as exc:  # noqa: BLE001
                show_outputs[cmd] = f"(取得失敗: {exc})"
        return {
            "running_config": running_config,
            "show_outputs": show_outputs,
        }
    finally:
        try:
            dev.disconnect()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Phase I: pyATS/Unicon を使った Live 操作関数
# ---------------------------------------------------------------------------
#
# インベントリ YAML を読み込み、pyATS/Unicon で直接実機を操作する。
# netmiko は使用しない。
#
# 必要条件: uv sync --extra network （pyats + genie のインストール）
# ---------------------------------------------------------------------------


import string as _string


def _expand_env_vars(value: str) -> str:
    """``${VAR_NAME}`` 形式の環境変数を展開する。

    未定義変数は元のプレースホルダーのまま残す（safe_substitute）。
    """
    return _string.Template(value).safe_substitute(os.environ)


def load_inventory(inventory_path: str) -> dict[str, dict]:
    """インベントリ YAML ファイルを読み込みデバイス辞書を返す。

    各フィールド値に含まれる ``${VAR_NAME}`` 形式のプレースホルダーは、
    実行時の環境変数（.env 含む）で展開される。

    Args:
        inventory_path: インベントリ YAML ファイルのパス（絶対 or 相対）。

    Returns:
        ``{device_name: {host, device_type, username, password, port, apply_mode, ...}}``

    Raises:
        FileNotFoundError: ファイルが存在しない場合。
        ValueError: YAML の形式が不正な場合。
    """
    from pathlib import Path
    import yaml as _yaml

    path = Path(inventory_path)
    if not path.exists():
        raise FileNotFoundError(
            f"インベントリファイルが見つかりません: {inventory_path}\n"
            "inventory/<プロンプトセット名>.yaml を作成してください。"
        )

    raw = path.read_text(encoding="utf-8")
    data = _yaml.safe_load(raw)

    if not isinstance(data, dict) or "devices" not in data:
        raise ValueError(
            f"インベントリファイルの形式が不正です: {inventory_path}\n"
            "'devices:' キーが必要です。"
        )

    devices: dict[str, dict] = {}
    for name, cfg in data["devices"].items():
        if not isinstance(cfg, dict):
            raise ValueError(
                f"デバイス '{name}' の設定が辞書形式ではありません: {inventory_path}"
            )
        expanded = {
            k: (_expand_env_vars(str(v)) if isinstance(v, str) else v)
            for k, v in cfg.items()
        }
        for required in ("host", "device_type", "username", "password"):
            if not expanded.get(required):
                raise ValueError(
                    f"デバイス '{name}' に必須キー '{required}' がありません: {inventory_path}"
                )
        expanded.setdefault("apply_mode", "config_merge")
        expanded.setdefault("port", 22)
        devices[name] = expanded

    return devices


def _compute_config_diff(current: str, target: str) -> list[str]:
    """incremental モード用: target にあって current にない行を返す。"""
    current_stripped = {line.strip() for line in current.splitlines() if line.strip()}
    return [
        line for line in target.splitlines()
        if line.strip() and line.strip() not in current_stripped
    ]


def check_connectivity(devices: dict[str, dict]) -> dict[str, bool]:
    """各デバイスへの SSH 疎通確認（pyATS/Unicon）。

    各デバイスに対して Unicon で接続を試み、すぐに切断する。

    Args:
        devices: :func:`load_inventory` が返すデバイス辞書。

    Returns:
        ``{device_name: True/False}``
    """
    results: dict[str, bool] = {}

    for name, cfg in devices.items():
        testbed_yaml = build_testbed_from_inventory({name: cfg})
        try:
            testbed = _load_testbed(testbed_yaml)
            dev = testbed.devices[name]
            dev.connect(log_stdout=False, learn_hostname=True)
            dev.disconnect()
            results[name] = True
        except ImportError:
            raise
        except Exception:  # noqa: BLE001
            results[name] = False

    return results


def backup_running_config(devices: dict[str, dict]) -> dict[str, str]:
    """各デバイスの running-config をバックアップして返す（pyATS/Unicon）。

    Args:
        devices: :func:`load_inventory` が返すデバイス辞書。

    Returns:
        ``{device_name: running_config_text}``

    Raises:
        RuntimeError: いずれかのデバイスでバックアップに失敗した場合。
    """
    backups: dict[str, str] = {}
    failures: list[str] = []

    for name, cfg in devices.items():
        testbed_yaml = build_testbed_from_inventory({name: cfg})
        try:
            testbed = _load_testbed(testbed_yaml)
            dev = _connect_device(testbed, name)
            try:
                output = dev.execute("show running-config")
                backups[name] = output
            finally:
                try:
                    dev.disconnect()
                except Exception:  # noqa: BLE001
                    pass
        except ImportError:
            raise
        except Exception as exc:  # noqa: BLE001
            failures.append(f"  {name} ({cfg.get('host', '?')}): {exc}")

    if failures:
        raise RuntimeError(
            "running-config のバックアップに失敗したデバイスがあります:\n"
            + "\n".join(failures)
        )

    return backups


def apply_config(device_name: str, cfg: dict, config_text: str) -> dict:
    """1 台のデバイスにコンフィグを投入する（pyATS/Unicon）。

    ``apply_mode`` に応じて投入方式を切り替える:

    * ``config_merge``   — ``device.configure()`` で行単位に追記（非破壊・デフォルト）。
    * ``config_replace`` — 同上（将来的に ``configure replace`` に拡張可能）。
    * ``incremental``    — 差分行のみ投入（最小変更）。

    Args:
        device_name: デバイス名（ログ・戻り値用）。
        cfg:         :func:`load_inventory` の 1 エントリ。
        config_text: 投入するコンフィグテキスト。

    Returns:
        ``{"device": str, "success": bool, "output": str, "error": str}``
    """
    apply_mode = cfg.get("apply_mode", "config_merge")
    testbed_yaml = build_testbed_from_inventory({device_name: cfg})

    try:
        testbed = _load_testbed(testbed_yaml)
        dev = _connect_device(testbed, device_name)
        try:
            if apply_mode == "incremental":
                current = dev.execute("show running-config")
                diff_lines = _compute_config_diff(current, config_text)
                if diff_lines:
                    output = dev.configure("\n".join(diff_lines))
                else:
                    output = "(変更なし — 差分なし)"
            else:
                lines = [ln for ln in config_text.splitlines() if ln.strip()]
                output = dev.configure("\n".join(lines))
        finally:
            try:
                dev.disconnect()
            except Exception:  # noqa: BLE001
                pass

        return {"device": device_name, "success": True, "output": output, "error": ""}

    except ImportError:
        raise
    except Exception as exc:  # noqa: BLE001
        return {"device": device_name, "success": False, "output": "", "error": str(exc)}


def rollback_config(device_name: str, cfg: dict, backup_config: str) -> dict:
    """バックアップコンフィグを使ってデバイスをロールバックする（pyATS/Unicon）。

    Args:
        device_name:   デバイス名（ログ・戻り値用）。
        cfg:           :func:`load_inventory` の 1 エントリ。
        backup_config: :func:`backup_running_config` で取得したバックアップテキスト。

    Returns:
        ``{"device": str, "success": bool, "output": str, "error": str}``
    """
    testbed_yaml = build_testbed_from_inventory({device_name: cfg})

    try:
        testbed = _load_testbed(testbed_yaml)
        dev = _connect_device(testbed, device_name)
        try:
            lines = [ln for ln in backup_config.splitlines() if ln.strip()]
            output = dev.configure("\n".join(lines))
        finally:
            try:
                dev.disconnect()
            except Exception:  # noqa: BLE001
                pass

        return {"device": device_name, "success": True, "output": output, "error": ""}

    except ImportError:
        raise
    except Exception as exc:  # noqa: BLE001
        return {"device": device_name, "success": False, "output": "", "error": str(exc)}

    """各デバイスへの SSH 疎通確認（pyATS/Unicon バックエンド）。

    各デバイスに対して Unicon で接続を試み、すぐに切断する。
    タイムアウト / 認証エラーなど例外が発生した場合は ``False`` を返す。

    Args:
        devices: :func:`~agentic_ni.tools.netmiko_tools.load_inventory` が返す辞書。

    Returns:
        ``{device_name: True/False}``
    """
    results: dict[str, bool] = {}

    for name, cfg in devices.items():
        testbed_yaml = build_testbed_from_inventory({name: cfg})
        try:
            testbed = _load_testbed(testbed_yaml)
            dev = testbed.devices[name]
            dev.connect(log_stdout=False, learn_hostname=True)
            dev.disconnect()
            results[name] = True
        except ImportError:
            raise  # pyATS 未インストールを呼び出し元に通知
        except Exception:  # noqa: BLE001
            results[name] = False

    return results
