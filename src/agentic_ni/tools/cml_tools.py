"""CML操作ツール群。virl2_client の薄いラッパー。

各関数はLLMエージェントから呼び出されるツールとして設計されており、
認証情報はすべて環境変数から読み込む。
"""

from __future__ import annotations

import os
import time
import warnings

from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# 内部ヘルパー
# ---------------------------------------------------------------------------


def _get_client():
    """環境変数から認証情報を読み込み ClientLibrary インスタンスを返す。

    Returns:
        virl2_client.ClientLibrary: 認証済みクライアント

    Raises:
        EnvironmentError: 必須環境変数が未設定の場合
        virl2_client.InitializationError: CMLへの接続に失敗した場合
    """
    from virl2_client import ClientLibrary

    url = os.getenv("CML_URL")
    username = os.getenv("CML_USERNAME")
    password = os.getenv("CML_PASSWORD")

    if not url or not username or not password:
        raise EnvironmentError(
            "CML_URL / CML_USERNAME / CML_PASSWORD を .env に設定してください。"
        )

    ssl_verify: bool | str = os.getenv("CML_VERIFY_SSL", "true").lower() != "false"

    # SSL検証無効時は urllib3 の InsecureRequestWarning を抑制
    if not ssl_verify:
        import urllib3  # type: ignore[import-untyped]

        warnings.filterwarnings("ignore", category=urllib3.exceptions.InsecureRequestWarning)

    return ClientLibrary(
        url=url,
        username=username,
        password=password,
        ssl_verify=ssl_verify,
    )


def _get_lab(client, lab_id: str):
    """lab_id から Lab オブジェクトを取得する。

    Raises:
        KeyError: 指定した lab_id が存在しない場合
    """
    lab = client.get_local_lab(lab_id)
    if lab is None:
        raise KeyError(f"ラボが見つかりません: lab_id={lab_id!r}")
    return lab


# ---------------------------------------------------------------------------
# 公開ツール関数
# ---------------------------------------------------------------------------


def create_lab(topology_yaml: str, title: str = "agentic-ni-lab") -> str:
    """トポロジーYAMLからCMLラボを作成・起動する。

    YAML文字列をCMLにインポートし、ラボを起動する。
    ノードの起動完了は wait_for_nodes_ready() で別途待機すること。

    Args:
        topology_yaml: CML形式のトポロジー定義（YAML文字列）。
        title: CML上でのラボ名。

    Returns:
        str: 作成されたラボのID。

    Raises:
        EnvironmentError: CML接続情報が未設定の場合。
        Exception: ラボのインポートまたは起動に失敗した場合。
    """
    client = _get_client()
    lab = client.import_lab(topology=topology_yaml, title=title)
    lab.start()
    return lab.id


def delete_lab(lab_id: str) -> None:
    """指定したラボを停止・削除する。

    Args:
        lab_id: 削除対象ラボのID。

    Raises:
        KeyError: lab_id が存在しない場合。
    """
    client = _get_client()
    lab = _get_lab(client, lab_id)
    if lab.is_active():
        lab.stop()
    lab.remove()


def push_config(lab_id: str, node_name: str, config: str) -> None:
    """指定ノードに初期コンフィグをセットする。

    ノードの起動前にコンフィグを埋め込む方式（Day-0 config）。
    すでに起動済みのノードに対しては extract_configuration() で
    実行コンフィグを同期後、configuration を更新する。

    Args:
        lab_id: 対象ラボのID。
        node_name: ノードのラベル名（例: "R1"）。
        config: 機器に流し込むコンフィグテキスト。

    Raises:
        KeyError: lab_id またはノード名が存在しない場合。
    """
    client = _get_client()
    lab = _get_lab(client, lab_id)
    node = lab.get_node_by_label(node_name)
    if node is None:
        raise KeyError(f"ノードが見つかりません: node_name={node_name!r}, lab_id={lab_id!r}")
    node.configuration = config


def set_link_state(lab_id: str, link_id: str, up: bool) -> None:
    """リンクのUP/DOWN状態を制御する（障害シミュレーション）。

    * up=False: 100% パケットロス条件を付与してリンク断を模擬する。
    * up=True : 条件を削除して正常状態に戻す。

    Args:
        lab_id: 対象ラボのID。
        link_id: 対象リンクのID。
        up: True でリンクUP、False でリンクDOWN（100% loss）。

    Raises:
        KeyError: lab_id またはリンクIDが存在しない場合。
    """
    client = _get_client()
    lab = _get_lab(client, lab_id)
    link = lab.get_link_by_id(link_id)
    if link is None:
        raise KeyError(f"リンクが見つかりません: link_id={link_id!r}, lab_id={lab_id!r}")

    if up:
        link.remove_condition()
    else:
        # 100% パケットロスでリンク断を模擬
        link.set_condition(loss=100.0)


def wait_for_nodes_ready(lab_id: str, timeout: int = 300) -> bool:
    """すべてのノードが起動（BOOTED）するまで待機する。

    Args:
        lab_id: 対象ラボのID。
        timeout: 最大待機秒数（デフォルト: 300秒）。

    Returns:
        bool: timeout 以内に全ノードが起動完了した場合 True、タイムアウトした場合 False。

    Raises:
        KeyError: lab_id が存在しない場合。
    """
    client = _get_client()
    lab = _get_lab(client, lab_id)

    deadline = time.monotonic() + timeout
    poll_interval = 5  # 秒

    while time.monotonic() < deadline:
        lab.sync_states()
        if lab.has_converged():
            return True
        time.sleep(poll_interval)

    return False


def get_lab_nodes(lab_id: str) -> list[dict]:
    """ラボ内のノード一覧とその状態を返す。

    Args:
        lab_id: 対象ラボのID。

    Returns:
        list[dict]: ノード情報のリスト。各要素は {"id", "label", "state"} を持つ。
    """
    client = _get_client()
    lab = _get_lab(client, lab_id)
    lab.sync_states()
    return [
        {"id": node.id, "label": node.label, "state": node.state}
        for node in lab.nodes()
    ]


def get_lab_links(lab_id: str) -> list[dict]:
    """ラボ内のリンク一覧を返す。

    Args:
        lab_id: 対象ラボのID。

    Returns:
        list[dict]: リンク情報のリスト。各要素は {"id", "node_a", "node_b"} を持つ。
    """
    client = _get_client()
    lab = _get_lab(client, lab_id)
    return [
        {
            "id": link.id,
            "node_a": link.node_a.label,
            "node_b": link.node_b.label,
        }
        for link in lab.links()
    ]
