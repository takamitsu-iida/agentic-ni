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
    * nodes[].interfaces に loopback 型または slot < 0 のものが含まれる
      → CML はLoopbackをトポロジーで管理しないため除去する
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

    # Loopbackインターフェース（type=loopback または slot<0）をノードから除去する
    # LoopbackはCMLトポロジーに含める必要がなく、slot:-1等で含めるとAPIエラーになる
    for node in data.get("nodes", []):
        original = node.get("interfaces", [])
        filtered = [
            iface for iface in original
            if iface.get("type") != "loopback" and iface.get("slot", 0) >= 0
        ]
        if len(filtered) != len(original):
            node["interfaces"] = filtered

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

    get_local_lab() はキャッシュ未登録時に None ではなく LabNotFound 例外を
    投げる場合があるため、例外もキャッチしてサーバーから同期後に再試行する。

    Raises:
        KeyError: 同期後も指定した lab_id が存在しない場合
    """
    try:
        lab = client.get_local_lab(lab_id)
        if lab is not None:
            return lab
    except Exception:  # noqa: BLE001  # LabNotFound 也含む
        pass

    # キャッシュミス・例外: サーバーから該当ラボを同期して再試行
    try:
        client.join_existing_lab(lab_id)
        lab = client.get_local_lab(lab_id)
        if lab is not None:
            return lab
    except Exception:  # noqa: BLE001
        pass

    raise KeyError(f"ラボが見つかりません: lab_id={lab_id!r}")


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
    existing = [lab for lab in client.all_labs() if lab.title == title]
    if existing:
        print(f"    同名ラボ {len(existing)} 件を削除中...", flush=True)
        for existing_lab in existing:
            _remove_lab(existing_lab)

    # YAMLの補完・修正後にインポート（起動はしない）
    print("    ラボをインポート中...", flush=True)
    patched_yaml = _patch_topology_yaml(topology_yaml)
    lab = client.import_lab(topology=patched_yaml, title=title)

    # 同一クライアント・同一ラボオブジェクトでコンフィグを投入（Day-0 config）
    print(f"    コンフィグを投入中 ({len(device_configs)} ノード)...", flush=True)
    for node_name, config in device_configs.items():
        node = lab.get_node_by_label(node_name)
        if node is None:
            raise KeyError(
                f"ノードが見つかりません: node_name={node_name!r}, lab_id={lab.id!r}"
            )
        node.configuration = config

    # コンフィグ投入後に起動
    print("    ラボを起動中...", flush=True)
    lab.start()

    # 同一クライアント・同一ラボオブジェクトで起動待ち
    print("    ノードの起動を待機中...", flush=True)
    deadline = time.monotonic() + timeout
    poll_interval = 5
    while time.monotonic() < deadline:
        lab.sync_states()
        if lab.has_converged():
            print(f"    起動完了 (lab_id={lab.id})", flush=True)
            return lab.id
        time.sleep(poll_interval)

    raise RuntimeError(
        f"ノードが規定時間内に起動しませんでした (lab_id={lab.id}, timeout={timeout}s)"
    )


def update_configs_and_restart(
    lab_id: str,
    device_configs: dict[str, str],
    timeout: int = 300,
) -> str:
    """既存ラボのコンフィグを更新して再起動する（トポロジーはそのまま）。

    ラボを停止・wipe後にコンフィグを差し替えて再起動し、収束を待つ。
    トポロジー構造が変わらず設定のみ修正するリトライ時に使用する。

    Args:
        lab_id: 更新対象ラボのID。
        device_configs: デバイス名 → 新しいコンフィグテキスト。
        timeout: 起動待機のタイムアウト秒数。

    Returns:
        str: ラボID（変更なし）。

    Raises:
        KeyError: lab_id が存在しない、またはノードが見つからない場合。
        RuntimeError: 起動タイムアウトの場合。
    """
    client = _get_client()
    client.join_existing_lab(lab_id)
    lab = client.get_local_lab(lab_id)
    if lab is None:
        raise KeyError(f"ラボが見つかりません: lab_id={lab_id!r}")

    # 停止・wipe（Day-0 configを再適用できる状態にする）
    print("    既存ラボを停止・wipe中...", flush=True)
    if lab.is_active():
        lab.stop()
    lab.wipe()

    # コンフィグを更新
    print(f"    コンフィグを更新中 ({len(device_configs)} ノード)...", flush=True)
    for node_name, config in device_configs.items():
        node = lab.get_node_by_label(node_name)
        if node is None:
            raise KeyError(
                f"ノードが見つかりません: {node_name!r} (lab_id={lab_id!r})"
            )
        node.configuration = config

    # 再起動・収束待ち
    print("    ラボを再起動中...", flush=True)
    lab.start()
    print("    ノードの起動を待機中...", flush=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        lab.sync_states()
        if lab.has_converged():
            print(f"    起動完了 (lab_id={lab_id})", flush=True)
            return lab_id
        time.sleep(5)

    raise RuntimeError(
        f"ノードが規定時間内に起動しませんでした (lab_id={lab_id}, timeout={timeout}s)"
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
    """CML レベルでリンクを DOWN/UP する（障害シミュレーション）。

    link.stop() / link.start() を使い CML インフラレイヤーでリンクを制御する。
    ルーター 2 台のインターフェースが同時に line protocol down になるため、
    片側 shutdown より客観的な障害シミュレーションが可能。

    * up=False: link.stop() でリンクを停止する。
    * up=True : link.start() でリンクを再開する。

    Args:
        lab_id: 対象ラボのID。
        link_id: 対象リンクのID。
        up: True でリンクUP（start）、False でリンクDOWN（stop）。

    Raises:
        KeyError: lab_id またはリンクIDが存在しない場合。
    """
    client = _get_client()
    lab = _get_lab(client, lab_id)
    link = lab.get_link_by_id(link_id)
    if link is None:
        raise KeyError(f"リンクが見つかりません: link_id={link_id!r}, lab_id={lab_id!r}")

    if up:
        link.start()
    else:
        link.stop()


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
        list[dict]: リンク情報のリスト。各要素は
            {"id", "node_a", "node_b", "interface_a", "interface_b"} を持つ。
    """
    client = _get_client()
    lab = _get_lab(client, lab_id)
    return [
        {
            "id": link.id,
            "node_a": link.node_a.label,
            "node_b": link.node_b.label,
            "interface_a": link.interface_a.label,
            "interface_b": link.interface_b.label,
        }
        for link in lab.links()
    ]
