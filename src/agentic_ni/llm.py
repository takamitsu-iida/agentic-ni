"""LLMファクトリー。LLM_PROVIDER環境変数でプロバイダーを切り替える。"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel

load_dotenv()


def get_llm() -> BaseChatModel:
    """環境変数 LLM_PROVIDER に応じた LLM インスタンスを返す。

    対応プロバイダー:
        openai    : ChatOpenAI (デフォルト)
        anthropic : ChatAnthropic
        ollama    : ChatOllama (ローカルLLM)
    """
    provider = os.getenv("LLM_PROVIDER", "openai").lower()

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=os.getenv("OPENAI_MODEL", "gpt-4o"),
            api_key=os.getenv("OPENAI_API_KEY"),
        )

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"),
            api_key=os.getenv("ANTHROPIC_API_KEY"),
        )

    if provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            model=os.getenv("OLLAMA_MODEL", "llama3.1:70b"),
        )

    raise ValueError(
        f"未対応のLLMプロバイダー: '{provider}' "
        "(LLM_PROVIDER は 'openai' / 'anthropic' / 'ollama' のいずれかを指定)"
    )
