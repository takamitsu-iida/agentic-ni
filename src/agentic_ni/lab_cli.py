"""CML ラボ管理 CLI。

デモ用ラボを CML 上に作成・起動するためのコマンドラインツール。

使用例::

    # シナリオ D/E 用ラボを作成（10台構成）
    agentic-ni-lab deploy --config demo-large

    # 既存ラボの一覧確認
    agentic-ni-lab list

    # ラボ削除
    agentic-ni-lab delete --lab-id <lab_id>
    agentic-ni-lab delete --title agentic-ni-large-demo

設定ファイルは configs/<config-name>/ 以下に配置する。
topology.yaml と R1.cfg 〜 Rn.cfg が存在すること。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agentic_ni.logger import get_logger

logger = get_logger(__name__)

# プロジェクトルートからの configs ディレクトリ
_CONFIGS_DIR = Path(__file__).parent.parent.parent / "configs"


def _load_configs(config_name: str) -> tuple[str, dict[str, str]]:
    """configs/<config_name>/ から topology.yaml とデバイス設定を読み込む。

    Returns:
        (topology_yaml_str, {device_name: config_text})
    """
    config_dir = _CONFIGS_DIR / config_name
    if not config_dir.is_dir():
        raise FileNotFoundError(f"設定ディレクトリが見つかりません: {config_dir}")

    topo_path = config_dir / "topology.yaml"
    if not topo_path.exists():
        raise FileNotFoundError(f"topology.yaml が見つかりません: {topo_path}")

    topology_yaml = topo_path.read_text(encoding="utf-8")

    device_configs: dict[str, str] = {}
    for cfg_path in sorted(config_dir.glob("R*.cfg")):
        device_name = cfg_path.stem  # ファイル名から拡張子を除いた部分（例: R1）
        device_configs[device_name] = cfg_path.read_text(encoding="utf-8")

    if not device_configs:
        raise FileNotFoundError(f"デバイス設定ファイル（R*.cfg）が見つかりません: {config_dir}")

    return topology_yaml, device_configs


def _cmd_deploy(args: argparse.Namespace) -> int:
    """deploy サブコマンド: ラボを作成・起動する。"""
    from agentic_ni.tools import cml_tools

    print(f"設定を読み込み中: configs/{args.config}/")
    topology_yaml, device_configs = _load_configs(args.config)
    print(f"  topology.yaml: 読み込み完了")
    print(f"  デバイス設定: {sorted(device_configs)} ({len(device_configs)} 台)")

    title = args.title or f"agentic-ni-{args.config}"
    print(f"\nCML にラボをデプロイ中...")
    print(f"  ラボ名  : {title}")
    print(f"  ノード数: {len(device_configs)} 台")
    print(f"  ※ 起動完了まで数分かかります...\n")

    try:
        lab_id = cml_tools.deploy_lab(
            topology_yaml=topology_yaml,
            device_configs=device_configs,
            title=title,
            timeout=args.timeout,
        )
    except RuntimeError as e:
        print(f"\n[ERROR] {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"\n[ERROR] デプロイに失敗しました: {e}", file=sys.stderr)
        return 1

    print(f"\n✅ ラボ作成完了!")
    print(f"   Lab ID : {lab_id}")
    print(f"   タイトル: {title}")
    print()
    print("次のステップ:")
    print(f"  # シナリオD デモ実行（モック）:")
    print(f"  agentic-ni-demo --scenario core-link-down --scripted")
    print()
    print(f"  # シナリオD コアリンクを停止（CML障害注入）:")
    print(f"  agentic-ni-lab fault --lab-id {lab_id} --link l0 --down")
    print()
    print(f"  # ラボ削除:")
    print(f"  agentic-ni-lab delete --lab-id {lab_id}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    """list サブコマンド: CML上のラボ一覧を表示する。"""
    from agentic_ni.tools import cml_tools

    client = cml_tools._get_client()
    labs = client.all_labs()
    if not labs:
        print("ラボが存在しません。")
        return 0

    print(f"{'Lab ID':<36}  {'タイトル':<30}  状態")
    print("-" * 80)
    for lab in labs:
        try:
            lab.sync_states()
            state = "active" if lab.is_active() else "stopped"
        except Exception:
            state = "unknown"
        print(f"{lab.id:<36}  {lab.title:<30}  {state}")
    return 0


def _cmd_delete(args: argparse.Namespace) -> int:
    """delete サブコマンド: ラボを停止・削除する。"""
    from agentic_ni.tools import cml_tools

    client = cml_tools._get_client()

    if args.lab_id:
        lab = cml_tools._get_lab(client, args.lab_id)
        labs = [lab]
    elif args.title:
        labs = [lab for lab in client.all_labs() if lab.title == args.title]
        if not labs:
            print(f"[ERROR] タイトル '{args.title}' のラボが見つかりません。", file=sys.stderr)
            return 1
    else:
        print("[ERROR] --lab-id または --title を指定してください。", file=sys.stderr)
        return 1

    for lab in labs:
        print(f"削除中: {lab.id} ({lab.title}) ...")
        cml_tools._remove_lab(lab)
        print(f"  ✅ 削除完了")
    return 0


def _cmd_fault(args: argparse.Namespace) -> int:
    """fault サブコマンド: リンクまたはノードの状態を変更する（障害注入）。"""
    from agentic_ni.tools import cml_tools

    if args.link:
        up = not args.down
        action = "停止" if args.down else "復旧"
        print(f"リンク {args.link} を{action}中 (lab_id={args.lab_id}) ...")
        cml_tools.set_link_state(args.lab_id, args.link, up=up)
        print(f"  ✅ リンク {args.link} を{action}しました。")
    else:
        print("[ERROR] --link を指定してください（現在 --node は未実装）。", file=sys.stderr)
        return 1
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    """status サブコマンド: ラボのノード・リンク状態を表示する。"""
    from agentic_ni.tools import cml_tools

    nodes = cml_tools.get_lab_nodes(args.lab_id)
    if not nodes:
        print("ノードが存在しません。")
        return 0

    print(f"ラボ: {args.lab_id}")
    print(f"{'ノード ID':<12}  {'名前':<8}  状態")
    print("-" * 40)
    for n in nodes:
        print(f"{n['id']:<12}  {n['label']:<8}  {n['state']}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="agentic-ni-lab",
        description="CML ラボ管理ツール",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # deploy
    p_deploy = sub.add_parser("deploy", help="ラボを作成・起動する")
    p_deploy.add_argument(
        "--config", default="demo-large",
        help="configs/ 以下の設定ディレクトリ名（デフォルト: demo-large）",
    )
    p_deploy.add_argument(
        "--title", default=None,
        help="CML上のラボ名（省略時は agentic-ni-<config> になる）",
    )
    p_deploy.add_argument(
        "--timeout", type=int, default=None,
        help="ノード起動待機タイムアウト秒数（省略時は自動計算）",
    )

    # list
    sub.add_parser("list", help="CML上のラボ一覧を表示する")

    # delete
    p_del = sub.add_parser("delete", help="ラボを停止・削除する")
    p_del.add_argument("--lab-id", default=None, help="削除するラボID")
    p_del.add_argument("--title", default=None, help="削除するラボのタイトル")

    # fault
    p_fault = sub.add_parser("fault", help="リンク障害を注入または復旧する")
    p_fault.add_argument("--lab-id", required=True, help="対象ラボID")
    p_fault.add_argument("--link", default=None, help="対象リンクID（例: l0）")
    p_fault.add_argument("--down", action="store_true", help="リンクを停止する（省略時は復旧）")

    # status
    p_status = sub.add_parser("status", help="ラボのノード状態を確認する")
    p_status.add_argument("--lab-id", required=True, help="対象ラボID")

    args = parser.parse_args()

    handlers = {
        "deploy": _cmd_deploy,
        "list": _cmd_list,
        "delete": _cmd_delete,
        "fault": _cmd_fault,
        "status": _cmd_status,
    }

    sys.exit(handlers[args.command](args))


if __name__ == "__main__":
    main()
