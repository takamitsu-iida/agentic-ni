"""ベクトルRAGツール。

成功した実行のエラー→修正事例を ChromaDB に保存し、
類似エラーが発生したときに過去の成功事例を検索する。

使用するには chromadb のインストールが必要:
    uv sync --extra rag
    # または
    pip install chromadb
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

# RAGストアの保存先（環境変数で上書き可能）
_DEFAULT_DB_DIR = Path.home() / ".agentic_ni" / "rag_store"
_COLLECTION_NAME = "successful_runs"
# コサイン距離の閾値: これ以下の距離（＝高い類似度）の事例のみ採用
_DISTANCE_THRESHOLD = 0.8


def _get_collection():
    """ChromaDB コレクションを返す（遅延インポート）。"""
    try:
        import chromadb
    except ImportError as exc:
        raise ImportError(
            "chromadb が未インストールです。\n"
            "  uv sync --extra rag  または  pip install chromadb  を実行してください。"
        ) from exc

    db_dir = Path(os.environ.get("RAG_STORE_PATH", str(_DEFAULT_DB_DIR)))
    db_dir.mkdir(parents=True, exist_ok=True)

    client = chromadb.PersistentClient(path=str(db_dir))
    return client.get_or_create_collection(
        name=_COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def save_successful_run(
    requirement: str,
    error_history: list[str],
    topology_yaml: str,
    device_configs: dict[str, str],
) -> int:
    """成功した実行のエラー→成功設計の対応をRAGストアに保存する。

    error_history の各エラーログに対して、最終的に成功したトポロジーと
    機器コンフィグを紐付けてドキュメントとして保存する。

    Args:
        requirement: 人間が入力した要件。
        error_history: 実行中に発生したエラーログの履歴（時系列順）。
        topology_yaml: 最終的に成功したトポロジーYAML。
        device_configs: 最終的に成功した機器コンフィグ。

    Returns:
        int: 保存したドキュメント数。
    """
    if not error_history:
        return 0

    collection = _get_collection()
    configs_json = json.dumps(device_configs, ensure_ascii=False)

    documents: list[str] = []
    metadatas: list[dict] = []
    ids: list[str] = []

    for error_log in error_history:
        if not error_log.strip():
            continue
        documents.append(error_log)
        metadatas.append({
            "requirement": requirement,
            "topology_yaml": topology_yaml,
            "device_configs": configs_json,
        })
        ids.append(str(uuid.uuid4()))

    if documents:
        collection.add(documents=documents, metadatas=metadatas, ids=ids)

    return len(documents)


def search_similar_errors(error_log: str, k: int = 3) -> list[dict]:
    """現在のエラーに類似した過去の成功事例を検索する。

    Args:
        error_log: 検索クエリとなるエラーログ。
        k: 返す事例の最大件数。

    Returns:
        list[dict]: 類似事例のリスト。各要素は以下のキーを持つ:
            - past_error: 過去のエラーログ
            - requirement: そのときの要件
            - topology_yaml: 最終的に成功したトポロジーYAML
            - device_configs: 最終的に成功した機器コンフィグ (dict)
            - distance: コサイン距離（0=同一, 1=直交, 小さいほど類似）
    """
    collection = _get_collection()

    total = collection.count()
    if total == 0:
        return []

    results = collection.query(
        query_texts=[error_log],
        n_results=min(k, total),
        include=["documents", "metadatas", "distances"],
    )

    cases: list[dict] = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        if dist > _DISTANCE_THRESHOLD:
            continue  # 類似度が低い事例はスキップ
        cases.append({
            "past_error": doc,
            "requirement": meta.get("requirement", ""),
            "topology_yaml": meta.get("topology_yaml", ""),
            "device_configs": json.loads(meta.get("device_configs", "{}")),
            "distance": dist,
        })

    return cases


def get_store_stats() -> dict:
    """RAGストアの統計情報を返す。"""
    db_dir = Path(os.environ.get("RAG_STORE_PATH", str(_DEFAULT_DB_DIR)))
    try:
        collection = _get_collection()
        return {
            "total_cases": collection.count(),
            "db_path": str(db_dir),
        }
    except Exception:  # noqa: BLE001
        return {
            "total_cases": 0,
            "db_path": str(db_dir),
        }
