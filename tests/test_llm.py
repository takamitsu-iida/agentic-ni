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
    with pytest.raises(ValueError, match="未対応のLLMプロバイダー"):
        llm_module.get_llm()
