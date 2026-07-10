"""get_llm() のユニットテスト。実際のAPIキーは不要。"""

import importlib
import os
from unittest.mock import MagicMock, patch

import pytest


def test_get_llm_openai(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")

    mock_instance = MagicMock()
    with patch("langchain_openai.ChatOpenAI", return_value=mock_instance) as mock_cls:
        from agentic_ni.llm import get_llm

        result = get_llm()
        mock_cls.assert_called_once_with(model="gpt-4o", api_key="test-key")
        assert result is mock_instance


def test_get_llm_anthropic(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")

    mock_instance = MagicMock()
    with patch("langchain_anthropic.ChatAnthropic", return_value=mock_instance) as mock_cls:
        import importlib

        import agentic_ni.llm as llm_module

        importlib.reload(llm_module)
        result = llm_module.get_llm()
        mock_cls.assert_called_once_with(
            model="claude-3-5-sonnet-20241022", api_key="test-key"
        )
        assert result is mock_instance


def test_get_llm_ollama(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.1:70b")

    mock_instance = MagicMock()
    with patch("langchain_ollama.ChatOllama", return_value=mock_instance) as mock_cls:
        import agentic_ni.llm as llm_module

        importlib.reload(llm_module)
        result = llm_module.get_llm()
        mock_cls.assert_called_once_with(
            base_url="http://localhost:11434", model="llama3.1:70b"
        )
        assert result is mock_instance


def test_get_llm_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "unknown_provider")

    import agentic_ni.llm as llm_module

    importlib.reload(llm_module)
    # 未対応プロバイダーは SystemExit(1) で終了する（スタックトレースなし）
    with pytest.raises(SystemExit) as exc_info:
        llm_module.get_llm()
    assert exc_info.value.code == 1


def test_get_llm_missing_openai_key_exits(monkeypatch):
    """OPENAI_API_KEY 未設定時に SystemExit(1) が発生すること。"""
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    import agentic_ni.llm as llm_module

    importlib.reload(llm_module)
    with pytest.raises(SystemExit) as exc_info:
        llm_module.get_llm()
    assert exc_info.value.code == 1


def test_get_llm_missing_anthropic_key_exits(monkeypatch):
    """ANTHROPIC_API_KEY 未設定時に SystemExit(1) が発生すること。"""
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    import agentic_ni.llm as llm_module

    importlib.reload(llm_module)
    with pytest.raises(SystemExit) as exc_info:
        llm_module.get_llm()
    assert exc_info.value.code == 1
