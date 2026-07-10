"""ロギング設定モジュール。

全エージェント・ツールで共通するロガー設定を一元管理する。

使い方::

    # 各モジュールのモジュールレベルで:
    from agentic_ni.logger import get_logger
    logger = get_logger(__name__)

    # main() で一度だけ:
    from agentic_ni.logger import configure_logging
    configure_logging(verbose=args.verbose, quiet=args.quiet)

ログレベル:
    INFO    通常の進捗メッセージ（デフォルトで表示）
    DEBUG   詳細なデバッグ情報（``--verbose`` 時のみ表示）
    WARNING 警告（常に表示）
    ERROR   エラー（常に表示）
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# コンソール向け: メッセージのみ（既存の print と同等の見た目）
_FMT_SIMPLE = "%(message)s"
# verbose 向け: タイムスタンプ + ロガー名 + レベル付き
_FMT_VERBOSE = "%(asctime)s  %(name)-35s  %(levelname)-8s  %(message)s"
# ファイル向け: 常に詳細フォーマット
_FMT_FILE = "%(asctime)s  %(name)s  %(levelname)s  %(message)s"

# suppress_libs に列挙したライブラリのログを WARNING 以上に抑制する
_SUPPRESS_LIBS = (
    "virl2_client",
    "urllib3",
    "httpx",
    "httpcore",
    "pyats",
    "unicon",
    "genie",
)


def get_logger(name: str) -> logging.Logger:
    """モジュール名でロガーを取得する。

    各モジュールのモジュールレベルで呼び出す::

        logger = get_logger(__name__)

    Args:
        name: ロガー名。通常は ``__name__`` を渡す。

    Returns:
        logging.Logger: 設定済みのロガーオブジェクト。
    """
    return logging.getLogger(name)


def configure_logging(
    verbose: bool = False,
    quiet: bool = False,
    log_file: str | None = None,
) -> None:
    """アプリケーション全体のログ設定を行う。

    main() または CLI エントリポイントから **1 度だけ** 呼び出す。
    2 度呼び出してもハンドラーは重複しない。

    Args:
        verbose: True の場合 DEBUG レベルまで表示（タイムスタンプ付き）。
        quiet: True の場合 WARNING レベル以上のみ表示。verbose より優先度が低い。
        log_file: ファイルにも出力する場合のパス。None の場合はファイル出力なし。
                  ``logs/`` ディレクトリが存在しない場合は自動作成する。
    """
    root = logging.getLogger("agentic_ni")

    # 重複ハンドラー防止（2 回目の呼び出しは無視）
    if root.handlers:
        return

    if verbose:
        level = logging.DEBUG
        fmt = _FMT_VERBOSE
    elif quiet:
        level = logging.WARNING
        fmt = _FMT_SIMPLE
    else:
        level = logging.INFO
        fmt = _FMT_SIMPLE

    root.setLevel(level)

    # --- コンソールハンドラー ---
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(logging.Formatter(fmt))
    root.addHandler(console)

    # --- ファイルハンドラー（オプション） ---
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)  # ファイルには常に全レベルを記録
        fh.setFormatter(logging.Formatter(_FMT_FILE))
        root.addHandler(fh)

    # --- サードパーティライブラリのログを抑制 ---
    for lib in _SUPPRESS_LIBS:
        logging.getLogger(lib).setLevel(logging.WARNING)
