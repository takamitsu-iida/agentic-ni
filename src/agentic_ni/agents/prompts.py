"""エージェントプロンプト読み込みユーティリティ。

各エージェントで重複していた ``_load_system_prompt()`` と ``_PROMPTS_DIR`` を一元管理する。
エージェントファイルはこのモジュールをインポートするだけで済む。

読み込みルール（``load_agent_prompt``）:
  1. ``prompts/<agent>_system.md`` をベースとして読み込む
  2. ``prompt_set`` が指定された場合:
       a. ``prompts/<set>/<agent>_system.md`` が存在すれば単独使用（後方互換・旧形式）
       b. ``prompts/<set>/<agent>.md`` が存在すればベースに結合（新形式）
       c. どちらも存在しなければベースのみ返す
  3. ``prompt_set=None`` の場合はベースのみ返す（シンプルな 1 ファイル読み込み）
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def load_agent_prompt(agent_name: str, prompt_set: str | None = None) -> str:
    """エージェントのシステムプロンプトを読み込んで返す。

    Args:
        agent_name: エージェント識別子（例: "architect", "validator", "troubleshooter"）。
                    ``prompts/<agent_name>_system.md`` が対象ファイルになる。
        prompt_set: プロンプトセット名（例: "demo", "demo2"）。
                    ``None`` の場合はベースプロンプトのみを返す。

    Returns:
        str: 読み込んだシステムプロンプト文字列。

    Raises:
        FileNotFoundError: ベースプロンプトファイルが存在しない場合。
    """
    base_path = PROMPTS_DIR / f"{agent_name}_system.md"
    if not base_path.exists():
        raise FileNotFoundError(
            f"{agent_name}_system.md が見つかりません: {base_path}"
        )

    if prompt_set is None:
        return base_path.read_text(encoding="utf-8")

    # 後方互換: セット内に <agent>_system.md があれば単独使用（旧形式）
    legacy_path = PROMPTS_DIR / prompt_set / f"{agent_name}_system.md"
    if legacy_path.exists():
        return legacy_path.read_text(encoding="utf-8")

    base = base_path.read_text(encoding="utf-8")

    # セット固有プロンプト: prompts/<set>/<agent>.md
    set_specific_path = PROMPTS_DIR / prompt_set / f"{agent_name}.md"
    if set_specific_path.exists():
        specific = set_specific_path.read_text(encoding="utf-8")
        return f"{base}\n\n---\n\n{specific}"

    return base


def list_prompt_sets() -> list[str]:
    """``requirement.md`` を持つプロンプトセット名の一覧を返す。"""
    return sorted(
        d.name for d in PROMPTS_DIR.iterdir()
        if d.is_dir() and (d / "requirement.md").exists()
    )


# ---------------------------------------------------------------------------
# Q4: プロンプトキャッシング
# ---------------------------------------------------------------------------

# Anthropic のキャッシュ対象となる最低トークン数目安（公式: 1024 トークン以上）
_ANTHROPIC_CACHE_MIN_CHARS = 1500  # 約 1024 トークン相当の文字数（目安）


def make_system_message(text: str) -> dict[str, Any]:
    """システムプロンプトのメッセージ辞書を返す（Q4: プロンプトキャッシング対応）。

    プロバイダーに応じてキャッシュ制御ヘッダーを自動付与する:

    * **Anthropic** — ``cache_control: {type: ephemeral}`` を付与。
      1024 トークン以上のプロンプトが API サーバー側でキャッシュされ、
      2 回目以降の呼び出しでキャッシュ読み出しコスト（約 1/10）が適用される。
    * **OpenAI** — 変更なし。gpt-4o 系はサーバーサイドで自動キャッシングが有効。
    * **Ollama** — 変更なし。

    Args:
        text: システムプロンプトのテキスト。

    Returns:
        dict: LangChain に渡す ``{"role": "system", "content": ...}`` 形式の辞書。
    """
    provider = os.getenv("LLM_PROVIDER", "openai").lower()

    if provider == "anthropic" and len(text) >= _ANTHROPIC_CACHE_MIN_CHARS:
        # Anthropic プロンプトキャッシング形式:
        # content をリスト形式にして cache_control ブロックを付与する
        return {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": text,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        }

    # OpenAI / Ollama / Anthropic（短すぎる場合）: 通常形式
    return {"role": "system", "content": text}



def load_agent_prompt(agent_name: str, prompt_set: str | None = None) -> str:
    """エージェントのシステムプロンプトを読み込んで返す。

    Args:
        agent_name: エージェント識別子（例: "architect", "validator", "troubleshooter"）。
                    ``prompts/<agent_name>_system.md`` が対象ファイルになる。
        prompt_set: プロンプトセット名（例: "demo", "demo2"）。
                    ``None`` の場合はベースプロンプトのみを返す。

    Returns:
        str: 読み込んだシステムプロンプト文字列。

    Raises:
        FileNotFoundError: ベースプロンプトファイルが存在しない場合。
    """
    base_path = PROMPTS_DIR / f"{agent_name}_system.md"
    if not base_path.exists():
        raise FileNotFoundError(
            f"{agent_name}_system.md が見つかりません: {base_path}"
        )

    if prompt_set is None:
        return base_path.read_text(encoding="utf-8")

    # 後方互換: セット内に <agent>_system.md があれば単独使用（旧形式）
    legacy_path = PROMPTS_DIR / prompt_set / f"{agent_name}_system.md"
    if legacy_path.exists():
        return legacy_path.read_text(encoding="utf-8")

    base = base_path.read_text(encoding="utf-8")

    # セット固有プロンプト: prompts/<set>/<agent>.md
    set_specific_path = PROMPTS_DIR / prompt_set / f"{agent_name}.md"
    if set_specific_path.exists():
        specific = set_specific_path.read_text(encoding="utf-8")
        return f"{base}\n\n---\n\n{specific}"

    return base


def list_prompt_sets() -> list[str]:
    """``requirement.md`` を持つプロンプトセット名の一覧を返す。"""
    return sorted(
        d.name for d in PROMPTS_DIR.iterdir()
        if d.is_dir() and (d / "requirement.md").exists()
    )
