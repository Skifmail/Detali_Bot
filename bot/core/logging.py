from __future__ import annotations

import sys

from loguru import logger


def setup_logging() -> None:
    """Настраивает базовое логирование для Telegram-бота.

    Returns:
        None: Ничего не возвращает.
    """

    logger.remove()
    logger.add(
        sys.stdout,
        level="INFO",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {name}:{function}:{line} - {message}",
        backtrace=True,
        diagnose=False,
    )
