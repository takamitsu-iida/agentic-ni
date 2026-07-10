"""LLMファクトリー。LLM_PROVIDER環境変数でプロバイダーを切り替える。"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel

load_dotenv()

# ---------------------------------------------------------------------------
# エラーメッセージテンプレート
# ---------------------------------------------------------------------------

_SETUP_HINT = (
    "\n.env ファイルが存在しない場合はテンプレートをコピーして設定してください:\n"
    "  cp .env.example .env\n"
    "  # .env を開いて必要な値を設定する"
)

_MISSING_KEY_MESSAGES: dict[str, str] = {
    "openai": (
        "OpenAI API キーが設定されていません。\n"
        ".env に以下を追加してください:\n"
        "  LLM_PROVIDER=openai\n"
        "  OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxxxxxx\n"
        "  OPENAI_MODEL=gpt-4o-mini"
    ),
    "anthropic": (
        "Anthropic API キーが設定されていません。\n"
        ".env に以下を追加してください:\n"
        "  LLM_PROVIDER=anthropic\n"
        "  ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxx\n"
        "  ANTHROPIC_MODEL=claude-3-5-sonnet-20241022"
    ),
    "ollama": (
        "Ollama の接続先が設定されていないか、サーバーが起動していません。\n"
        ".env に以下を追加し、Ollama サーバーを起動してください:\n"
        "  LLM_PROVIDER=ollama\n"
        "  OLLAMA_BASE_URL=http://localhost:11434\n"
        "  OLLAMA_MODEL=llama3.1:70b"
    ),
}


def _abort_missing_credentials(provider: str) -> None:
    """認証情報不足を分かりやすく報告してプロセスを終了する。"""
    msg = _MISSING_KEY_MESSAGES.get(provider, f"プロバイダー '{provider}' の認証情報が設定されていません。")
    print(f"\n[設定エラー] {msg}{_SETUP_HINT}\n", file=sys.stderr)
    sys.exit(1)


def get_llm() -> BaseChatModel:
    """環境変数 LLM_PROVIDER に応じた LLM インスタンスを返す。

    対応プロバイダー:
        openai    : ChatOpenAI (デフォルト)
        anthropic : ChatAnthropic
        ollama    : ChatOllama (ローカルLLM)

    .env が未設定の場合や API キーが空の場合は、スタックトレースではなく
    分かりやすいエラーメッセージを表示してプロセスを終了する。
    """
    provider = os.getenv("LLM_PROVIDER", "openai").lower()

    if provider == "openai":
        if not os.getenv("OPENAI_API_KEY"):
            _abort_missing_credentials("openai")
        from langchain_openai import ChatOpenAI
        try:
            return ChatOpenAI(
                model=os.getenv("OPENAI_MODEL", "gpt-4o"),
                api_key=os.getenv("OPENAI_API_KEY"),
            )
        except Exception:
            _abort_missing_credentials("openai")

    if provider == "anthropic":
        if not os.getenv("ANTHROPIC_API_KEY"):
            _abort_missing_credentials("anthropic")
        from langchain_anthropic import ChatAnthropic
        try:
            return ChatAnthropic(
                model=os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"),
                api_key=os.getenv("ANTHROPIC_API_KEY"),
            )
        except Exception:
            _abort_missing_credentials("anthropic")

    if provider == "ollama":
        from langchain_ollama import ChatOllama
        try:
            return ChatOllama(
                base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
                model=os.getenv("OLLAMA_MODEL", "llama3.1:70b"),
            )
        except Exception:
            _abort_missing_credentials("ollama")

    print(
        f"\n[設定エラー] 未対応のLLMプロバイダー: '{provider}'\n"
        "LLM_PROVIDER は 'openai' / 'anthropic' / 'ollama' のいずれかを指定してください。"
        f"{_SETUP_HINT}\n",
        file=sys.stderr,
    )
    sys.exit(1)
