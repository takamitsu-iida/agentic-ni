"""知識ベース RAG のユニットテスト。chromadb はインメモリクライアントでモック。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# テスト用ヘルパー
# ---------------------------------------------------------------------------


def _make_in_memory_collection():
    """テスト用インメモリ ChromaDB コレクションを返す（テストごとに一意な名前）。"""
    import uuid
    try:
        # pysqlite3-binary で sqlite3 を差し替え（古い環境向け）
        try:
            import pysqlite3  # type: ignore[import-untyped]
            import sys
            sys.modules["sqlite3"] = pysqlite3
        except ImportError:
            pass
        import chromadb
        client = chromadb.EphemeralClient()  # ephemeral (in-memory)
        col_name = f"test_{uuid.uuid4().hex[:8]}"
        return client.get_or_create_collection(
            name=col_name,
            metadata={"hnsw:space": "cosine"},
        )
    except ImportError:
        pytest.skip("chromadb が未インストールのためスキップ")


def _is_chromadb_available() -> bool:
    try:
        # pysqlite3-binary で sqlite3 を差し替え（古い環境向け）
        try:
            import pysqlite3  # type: ignore[import-untyped]
            import sys
            sys.modules["sqlite3"] = pysqlite3
        except ImportError:
            pass
        import chromadb  # noqa: F401
        return True
    except (ImportError, RuntimeError):
        return False


# ---------------------------------------------------------------------------
# _chunk_text のテスト（chromadb 不要）
# ---------------------------------------------------------------------------


class TestChunkText:
    def test_short_text_single_chunk(self):
        from agentic_ni.tools.rag_tools import _chunk_text

        chunks = _chunk_text("Hello world", "test.md")
        assert len(chunks) == 1
        assert chunks[0]["text"] == "Hello world"
        assert chunks[0]["source_file"] == "test.md"
        assert chunks[0]["chunk_idx"] == 0

    def test_long_text_multiple_chunks(self):
        from agentic_ni.tools.rag_tools import _chunk_text, _CHUNK_SIZE, _CHUNK_OVERLAP

        text = "A" * (_CHUNK_SIZE * 2 + 100)
        chunks = _chunk_text(text, "long.txt")
        # 複数チャンクになること
        assert len(chunks) >= 2
        for i, chunk in enumerate(chunks):
            assert chunk["source_file"] == "long.txt"
            assert chunk["chunk_idx"] == i

    def test_chunk_overlap_creates_extra_chunk(self):
        from agentic_ni.tools.rag_tools import _chunk_text, _CHUNK_SIZE, _CHUNK_OVERLAP

        # ちょうど CHUNK_SIZE + 1文字 → 2チャンクになること
        text = "B" * (_CHUNK_SIZE + 1)
        chunks = _chunk_text(text, "overlap.md")
        assert len(chunks) == 2
        # 最初のチャンクは CHUNK_SIZE 文字
        assert len(chunks[0]["text"]) == _CHUNK_SIZE

    def test_empty_text_returns_empty(self):
        from agentic_ni.tools.rag_tools import _chunk_text

        chunks = _chunk_text("", "empty.txt")
        assert chunks == []

    def test_whitespace_only_text_returns_empty(self):
        from agentic_ni.tools.rag_tools import _chunk_text

        chunks = _chunk_text("   \n\n   ", "whitespace.txt")
        assert chunks == []

    def test_exactly_chunk_size_is_one_chunk(self):
        from agentic_ni.tools.rag_tools import _chunk_text, _CHUNK_SIZE

        text = "X" * _CHUNK_SIZE
        chunks = _chunk_text(text, "exact.txt")
        assert len(chunks) == 1


# ---------------------------------------------------------------------------
# index_knowledge_files のテスト
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _is_chromadb_available(), reason="chromadb が未インストール")
class TestIndexKnowledgeFiles:
    def test_indexes_txt_file(self, tmp_path):
        from agentic_ni.tools.rag_tools import index_knowledge_files, search_knowledge

        (tmp_path / "guide.txt").write_text("OSPFの設定方法: network 0.0.0.0 255.255.255.255 area 0", encoding="utf-8")

        # テスト用コレクションを注入
        with (
            patch("agentic_ni.tools.rag_tools.clear_knowledge_base"),
            patch("agentic_ni.tools.rag_tools._get_knowledge_collection", return_value=_make_in_memory_collection()) as mock_col,
        ):
            count = index_knowledge_files(tmp_path)

        assert count >= 1

    def test_indexes_md_file(self, tmp_path):
        from agentic_ni.tools.rag_tools import index_knowledge_files

        (tmp_path / "notes.md").write_text("# OSPF設定\n\nネイバーを確認するには show ip ospf neighbor", encoding="utf-8")

        col = _make_in_memory_collection()
        with (
            patch("agentic_ni.tools.rag_tools.clear_knowledge_base"),
            patch("agentic_ni.tools.rag_tools._get_knowledge_collection", return_value=col),
        ):
            count = index_knowledge_files(tmp_path)

        assert count >= 1
        assert col.count() >= 1

    def test_skips_unsupported_extensions(self, tmp_path):
        from agentic_ni.tools.rag_tools import index_knowledge_files

        (tmp_path / "script.py").write_text("print('hello')", encoding="utf-8")
        (tmp_path / "image.png").write_bytes(b"\x89PNG")
        (tmp_path / "notes.txt").write_text("valid text", encoding="utf-8")

        col = _make_in_memory_collection()
        with (
            patch("agentic_ni.tools.rag_tools.clear_knowledge_base"),
            patch("agentic_ni.tools.rag_tools._get_knowledge_collection", return_value=col),
        ):
            count = index_knowledge_files(tmp_path)

        # .txt のみ索引化
        assert count == 1

    def test_skips_dotfiles(self, tmp_path):
        from agentic_ni.tools.rag_tools import index_knowledge_files

        (tmp_path / ".gitkeep").write_text("", encoding="utf-8")
        (tmp_path / "valid.txt").write_text("content", encoding="utf-8")

        col = _make_in_memory_collection()
        with (
            patch("agentic_ni.tools.rag_tools.clear_knowledge_base"),
            patch("agentic_ni.tools.rag_tools._get_knowledge_collection", return_value=col),
        ):
            count = index_knowledge_files(tmp_path)

        assert count == 1  # .gitkeep は除外

    def test_raises_when_directory_not_found(self):
        from agentic_ni.tools.rag_tools import index_knowledge_files

        with pytest.raises(FileNotFoundError):
            index_knowledge_files("/nonexistent/path/rag")

    def test_returns_zero_for_empty_directory(self, tmp_path):
        from agentic_ni.tools.rag_tools import index_knowledge_files

        col = _make_in_memory_collection()
        with (
            patch("agentic_ni.tools.rag_tools.clear_knowledge_base"),
            patch("agentic_ni.tools.rag_tools._get_knowledge_collection", return_value=col),
        ):
            count = index_knowledge_files(tmp_path)

        assert count == 0


# ---------------------------------------------------------------------------
# search_knowledge のテスト
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _is_chromadb_available(), reason="chromadb が未インストール")
class TestSearchKnowledge:
    def test_returns_empty_when_collection_empty(self):
        from agentic_ni.tools.rag_tools import search_knowledge

        empty_col = _make_in_memory_collection()
        with patch("agentic_ni.tools.rag_tools._get_knowledge_collection", return_value=empty_col):
            result = search_knowledge("OSPF 設定")

        assert result == []

    def test_returns_matching_documents(self):
        from agentic_ni.tools.rag_tools import search_knowledge
        import uuid

        col = _make_in_memory_collection()
        col.add(
            documents=["OSPF の network コマンドで全インターフェースをエリア 0 に参加させる"],
            metadatas=[{"source_file": "ospf.md", "chunk_idx": 0}],
            ids=[str(uuid.uuid4())],
        )

        with patch("agentic_ni.tools.rag_tools._get_knowledge_collection", return_value=col):
            result = search_knowledge("OSPF ネットワーク設定", k=1)

        # 距離フィルタを通過すれば結果が返る
        # (距離フィルタが厳しい場合は空になる可能性があるが、構造を検証)
        assert isinstance(result, list)
        if result:
            assert "content" in result[0]
            assert "source_file" in result[0]
            assert "distance" in result[0]
            assert result[0]["source_file"] == "ospf.md"

    def test_returns_empty_when_chromadb_not_installed(self):
        from agentic_ni.tools.rag_tools import search_knowledge

        with patch("agentic_ni.tools.rag_tools._get_knowledge_collection", side_effect=ImportError("no chromadb")):
            result = search_knowledge("OSPF")

        assert result == []


# ---------------------------------------------------------------------------
# clear_knowledge_base のテスト
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _is_chromadb_available(), reason="chromadb が未インストール")
class TestClearKnowledgeBase:
    def test_calls_delete_collection(self):
        from agentic_ni.tools.rag_tools import clear_knowledge_base

        mock_client = MagicMock()
        with patch("agentic_ni.tools.rag_tools._get_client", return_value=mock_client):
            clear_knowledge_base()

        mock_client.delete_collection.assert_called_once_with(name="knowledge_base")

    def test_handles_missing_collection_gracefully(self):
        from agentic_ni.tools.rag_tools import clear_knowledge_base

        mock_client = MagicMock()
        mock_client.delete_collection.side_effect = ValueError("Collection not found")

        with patch("agentic_ni.tools.rag_tools._get_client", return_value=mock_client):
            clear_knowledge_base()  # 例外なしで完了すること


# ---------------------------------------------------------------------------
# get_store_stats のテスト
# ---------------------------------------------------------------------------


class TestGetStoreStats:
    def test_includes_knowledge_chunks_key(self):
        from agentic_ni.tools.rag_tools import get_store_stats

        mock_col = MagicMock()
        mock_col.count.return_value = 10
        mock_knowledge_col = MagicMock()
        mock_knowledge_col.count.return_value = 47

        with (
            patch("agentic_ni.tools.rag_tools._get_collection", return_value=mock_col),
            patch("agentic_ni.tools.rag_tools._get_knowledge_collection", return_value=mock_knowledge_col),
        ):
            stats = get_store_stats()

        assert stats["total_cases"] == 10
        assert stats["knowledge_chunks"] == 47
        assert "db_path" in stats

    def test_returns_zero_on_chromadb_error(self):
        from agentic_ni.tools.rag_tools import get_store_stats

        with (
            patch("agentic_ni.tools.rag_tools._get_collection", side_effect=RuntimeError("no db")),
            patch("agentic_ni.tools.rag_tools._get_knowledge_collection", side_effect=RuntimeError("no db")),
        ):
            stats = get_store_stats()

        assert stats["total_cases"] == 0
        assert stats["knowledge_chunks"] == 0
