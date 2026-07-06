"""ベクトルRAGツール。

2 種類の RAG コレクションを管理する:

1. successful_runs  — 実行ログ RAG（廃止）
   設計エージェントの成功ケースを保存する機能。現在は廃止され知識ベース RAGに一本化。

2. knowledge_base   — 知識ベース RAG（--rag-index）
   rag/ ディレクトリに置いたテキストファイルをチャンク分割して索引化し、
   設計・診断の際に参考資料として検索する。

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
_KNOWLEDGE_COLLECTION_NAME = "knowledge_base"
# コサイン距離の閾値: これ以下の距離（＝高い類似度）の事例のみ採用
_DISTANCE_THRESHOLD = 0.8
# 知識ベース チャンク設定
_CHUNK_SIZE = 1000     # 1 チャンクあたりの最大文字数
_CHUNK_OVERLAP = 150   # チャンク間のオーバーラップ文字数
# 知識ベースで対応するファイル拡張子
_SUPPORTED_EXTENSIONS = {".txt", ".md", ".json"}


# ---------------------------------------------------------------------------
# 内部ヘルパー（実行ログ RAG）
# ---------------------------------------------------------------------------


def _get_client():
    """ChromaDB PersistentClient を返す（遅延インポート）。

    古い SQLite 環境（< 3.35.0）では pysqlite3-binary でモンキーパッチする。
    """
    # SQLite バージョンチェック: chromadb は sqlite3 >= 3.35.0 を要求する
    # 古い環境では pysqlite3-binary で代替する（標準的な回避策）
    try:
        import pysqlite3  # type: ignore[import-untyped]
        import sys
        sys.modules["sqlite3"] = pysqlite3
    except ImportError:
        pass  # pysqlite3-binary 未インストール時はシステムの sqlite3 を使用

    try:
        import chromadb
    except ImportError as exc:
        raise ImportError(
            "chromadb が未インストールです。\n"
            "  uv sync --extra rag  または  pip install chromadb pysqlite3-binary  を実行してください。"
        ) from exc

    db_dir = Path(os.environ.get("RAG_STORE_PATH", str(_DEFAULT_DB_DIR)))
    db_dir.mkdir(parents=True, exist_ok=True)
    import chromadb
    return chromadb.PersistentClient(path=str(db_dir))


def _get_collection():
    """実行ログ RAG コレクション (successful_runs) を返す。"""
    client = _get_client()
    return client.get_or_create_collection(
        name=_COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


# ---------------------------------------------------------------------------
# 内部ヘルパー（知識ベース RAG）
# ---------------------------------------------------------------------------


def _get_knowledge_collection():
    """知識ベース RAG コレクション (knowledge_base) を返す。"""
    client = _get_client()
    return client.get_or_create_collection(
        name=_KNOWLEDGE_COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def _chunk_text(text: str, source_file: str) -> list[dict]:
    """テキストをオーバーラップ付きチャンクに分割する。

    Args:
        text: 分割するテキスト。
        source_file: チャンクのメタデータに付与するファイル名。

    Returns:
        list[dict]: 各要素は {"text": str, "source_file": str, "chunk_idx": int}。
    """
    chunks: list[dict] = []
    start = 0
    chunk_idx = 0
    while start < len(text):
        end = min(start + _CHUNK_SIZE, len(text))
        chunk_body = text[start:end].strip()
        if chunk_body:
            chunks.append({
                "text": chunk_body,
                "source_file": source_file,
                "chunk_idx": chunk_idx,
            })
        if end >= len(text):
            break
        start += _CHUNK_SIZE - _CHUNK_OVERLAP
        chunk_idx += 1
    return chunks


# ---------------------------------------------------------------------------
# 知識ベース RAG 公開関数
# ---------------------------------------------------------------------------


def index_knowledge_files(rag_dir: str | Path = "rag") -> int:
    """rag/ ディレクトリのテキストファイルを知識ベースに索引化する。

    既存インデックスは全消去してから再構築するため冪等に実行できる。
    対応ファイル形式: .txt / .md / .json

    Args:
        rag_dir: 索引化するディレクトリのパス（デフォルト: "rag"）。

    Returns:
        int: 索引化したチャンク総数。

    Raises:
        FileNotFoundError: rag_dir が存在しない場合。
        ImportError: chromadb が未インストールの場合。
    """
    rag_dir = Path(rag_dir)
    if not rag_dir.exists():
        raise FileNotFoundError(
            f"ディレクトリが見つかりません: {rag_dir}\n"
            f"  rag/ ディレクトリを作成し、テキストファイルを置いてください。"
        )

    # 既存インデックスをクリアして再構築
    clear_knowledge_base()
    collection = _get_knowledge_collection()

    all_documents: list[str] = []
    all_metadatas: list[dict] = []
    all_ids: list[str] = []

    for file_path in sorted(rag_dir.rglob("*")):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in _SUPPORTED_EXTENSIONS:
            continue
        if file_path.name.startswith("."):
            continue  # .gitkeep などを除外

        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore").strip()
        except OSError:
            continue
        if not text:
            continue

        source_name = file_path.name
        for chunk in _chunk_text(text, source_name):
            all_documents.append(chunk["text"])
            all_metadatas.append({
                "source_file": chunk["source_file"],
                "chunk_idx": chunk["chunk_idx"],
            })
            all_ids.append(str(uuid.uuid4()))
        print(
            f"    {source_name}: {len(_chunk_text(text, source_name))} チャンク",
            flush=True,
        )

    if all_documents:
        collection.add(
            documents=all_documents,
            metadatas=all_metadatas,
            ids=all_ids,
        )

    return len(all_documents)


def search_knowledge(query: str, k: int = 3) -> list[dict]:
    """クエリに関連する知識チャンクを検索する。

    chromadb が未インストール、またはインデックスが空の場合は空リストを返す。

    Args:
        query: 検索クエリ（要件や問題説明など）。
        k: 返すチャンクの最大件数。

    Returns:
        list[dict]: 各要素は {"content": str, "source_file": str, "distance": float}。
    """
    try:
        collection = _get_knowledge_collection()
    except ImportError:
        return []

    total = collection.count()
    if total == 0:
        return []

    results = collection.query(
        query_texts=[query],
        n_results=min(k, total),
        include=["documents", "metadatas", "distances"],
    )

    knowledge: list[dict] = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        if dist > _DISTANCE_THRESHOLD:
            continue
        knowledge.append({
            "content": doc,
            "source_file": meta.get("source_file", ""),
            "distance": dist,
        })

    return knowledge


def clear_knowledge_base() -> None:
    """知識ベースのインデックスを全消去する。

    Raises:
        ImportError: chromadb が未インストールの場合。
    """
    client = _get_client()
    try:
        client.delete_collection(name=_KNOWLEDGE_COLLECTION_NAME)
    except Exception:  # noqa: BLE001
        pass  # コレクションが存在しない場合は何もしない（chromadb バージョンに依存しない）


# ---------------------------------------------------------------------------
# 実行ログ RAG 公開関数
# ---------------------------------------------------------------------------


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
    """RAGストアの統計情報を返す。

    Returns:
        dict: {
            "total_cases": int,      — 実行ログ RAG の保存事例数
            "knowledge_chunks": int, — 知識ベースのチャンク数
            "db_path": str,          — ChromaDB の保存ディレクトリ
        }
    """
    db_dir = Path(os.environ.get("RAG_STORE_PATH", str(_DEFAULT_DB_DIR)))
    result: dict = {"db_path": str(db_dir)}

    try:
        collection = _get_collection()
        result["total_cases"] = collection.count()
    except Exception:  # noqa: BLE001
        result["total_cases"] = 0

    try:
        knowledge_collection = _get_knowledge_collection()
        result["knowledge_chunks"] = knowledge_collection.count()
    except Exception:  # noqa: BLE001
        result["knowledge_chunks"] = 0

    return result
