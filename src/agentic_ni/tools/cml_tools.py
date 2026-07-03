"""CML操作ツール群。virl2_client の薄いラッパー。

各関数はLLMエージェントから呼び出されるツールとして設計されており、
認証情報はすべて環境変数から読み込む。
"""

from __future__ import annotations

import logging
import os
import time
import warnings

import yaml
from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# 内部ヘルパー
# ---------------------------------------------------------------------------


def _patch_topology_yaml(topology_yaml: str) -> str:
    """CML 2.10 の必須フィールドを補完し、不正値を修正する。

    LLM が生成した YAML に以下の問題が起きることがあるため、
    CML API に送る前にここで安全に修正する。

    * lab.version が存在しない → "0.1.0" を設定
    * links[].label が空文字 → link の id と同じ値を設定
    """
    data = yaml.safe_load(topology_yaml)

    # lab.version が必須 (CML 2.x)
    lab = data.setdefault("lab", {})
    if not lab.get("version"):
        lab["version"] = "0.1.0"

    # links[].label は空文字不可
    for i, link in enumerate(data.get("links", [])):
        if not link.get("label"):
            link["label"] = link.get("id", f"l{i}")

    return yaml.dump(data, default_flow_style=False, allow_unicode=True)


def _get_client():
    """環境変数から認証情報を読み込み ClientLibrary インスタンスを返す。

    Returns:
        virl2_client.ClientLibrary: 認証済みクライアント

    Raises:
        EnvironmentError: 必須環境変数が未設定の場合
        virl2_client.InitializationError: CMLへの接続に失敗した場合
    """

    # SSL Verification disabled のログが鬱陶しいので、ERRORのみに抑制
    logging.getLogger("virl2_client.virl2_client").setLevel(logging.ERROR)
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

    ローカルキャッシュにない場合（別の ClientLibrary インスタンスで作成したラボなど）は
    サーバーから全ラボを同期してから再検索する。

    Raises:
        KeyError: 指定した lab_id が存在しない場合
    """
    lab = client.get_local_lab(lab_id)
    if lab is None:
        # キャッシュミス: サーバーから同期して再試行
        client.join_existing_labs()
        lab = client.get_local_lab(lab_id)
    if lab is None:
        raise KeyError(f"ラボが見つかりません: lab_id={lab_id!r}")
    return lab


# ---------------------------------------------------------------------------
# 公開ツール関数
# ---------------------------------------------------------------------------


def _remove_lab(lab) -> None:
    """ラボを停止・wipe・削除する内部ヘルパー。

    CML 2.x では stop() → wipe() → remove() の順序が必要。
    wipe() を省略すると 'Lab is not wiped' エラーが発生する。
    """
    if lab.is_active():
        lab.stop()
    lab.wipe()
    lab.remove()


def deploy_lab(
    topology_yaml: str,
    device_configs: dict[str, str],
    title: str = "agentic-ni-lab",
    timeout: int = 300,
) -> str:
    """ラボのインポート・コンフィグ投入・起動・起動待ちを単一クライアントで一括実行する。

    複数の関数呼び出しで ClientLibrary インスタンスが分かれるとキャッシュ不整合が
    起きるため、デプロイシーケンス全体をこの関数内で完結させる。

    Args:
        topology_yaml: CML形式のトポロジー定義（YAML文字列）。
        device_configs: デバイス名 → コンフィグテキストのマッピング。
        title: CML上でのラボ名。同名ラボが存在する場合は削除される。
        timeout: 全ノード起動待機のタイムアウト秒数（デフォルト: 300秒）。

    Returns:
        str: 作成されたラボのID。

    Raises:
        EnvironmentError: CML接続情報が未設定の場合。
        KeyError: device_configs に含まれるノードがトポロジーに存在しない場合。
        RuntimeError: ノードが規定時間内に起動しなかった場合。
        Exception: ラボのインポートまたは起動に失敗した場合。
    """
    client = _get_client()

    # 同名ラボが既に存在する場合はすべて削除する
    for existing_lab in client.all_labs():
        if existing_lab.title == title:
            _remove_lab(existing_lab)

    # YAMLの補完・修正後にインポート（起動はしない）
    patched_yaml = _patch_topology_yaml(topology_yaml)
    lab = client.import_lab(topology=patched_yaml, title=title)

    # 同一クライアント・同一ラボオブジェクトでコンフィグを投入（Day-0 config）
    for node_name, config in device_configs.items():
        node = lab.get_node_by_label(node_name)
        if node is None:
            raise KeyError(
                f"ノードが見つかりません: node_name={node_name!r}, lab_id={lab.id!r}"
            )
        node.configuration = config

    # コンフィグ投入後に起動
    lab.start()

    # 同一クライアント・同一ラボオブジェクトで起動待ち
    deadline = time.monotonic() + timeout
    poll_interval = 5
    while time.monotonic() < deadline:
        lab.sync_states()
        if lab.has_converged():
            return lab.id
        time.sleep(poll_interval)

    raise RuntimeError(
        f"ノードが規定時間内に起動しませんでした (lab_id={lab.id}, timeout={timeout}s)"
    )


def create_lab(topology_yaml: str, title: str = "agentic-ni-lab") -> str:
    """トポロジーYAMLからCMLラボをインポートする（起動はしない）。

    .. note::
        デプロイ全体には :func:`deploy_lab` を使用すること。
        この関数単体で使う場合は push_config() → start_lab() の順に呼び出すこと。

    Args:
        topology_yaml: CML形式のトポロジー定義（YAML文字列）。
        title: CML上でのラボ名。同名ラボが存在する場合は削除される。

    Returns:
        str: 作成されたラボのID。
    """
    client = _get_client()

    for existing_lab in client.all_labs():
        if existing_lab.title == title:
            _remove_lab(existing_lab)

    patched_yaml = _patch_topology_yaml(topology_yaml)
    lab = client.import_lab(topology=patched_yaml, title=title)
    return lab.id


def start_lab(lab_id: str) -> None:
    """指定したラボを起動する。

    コンフィグ投入（push_config）の後に呼び出すこと。

    Args:
        lab_id: 起動対象ラボのID。

    Raises:
        KeyError: lab_id が存在しない場合。
    """
    client = _get_client()
    lab = _get_lab(client, lab_id)
    lab.start()


def delete_lab(lab_id: str) -> None:
    """指定したラボを停止・削除する。

    Args:
        lab_id: 削除対象ラボのID。

    Raises:
        KeyError: lab_id が存在しない場合。
    """
    client = _get_client()
    lab = _get_lab(client, lab_id)
    _remove_lab(lab)


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
