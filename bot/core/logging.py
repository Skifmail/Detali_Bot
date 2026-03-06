from __future__ import annotations

import os
import sys
from pathlib import Path

from loguru import logger


def setup_logging() -> None:
    """Настраивает логирование для Telegram-бота.

    Всегда пишет в stdout (терминал / journald). Дополнительно в файл с ротацией,
    если задана переменная окружения BOT_LOG_PATH (путь к файлу логов).
    Ротация: один файл до 1 MB, хранится до 3 файлов — диск не забивается.

    Returns:
        None: Ничего не возвращает.
    """
    log_format = "{time:YYYY-MM-DD HH:mm:ss} | {level} | {name}:{function}:{line} - {message}"
    logger.remove()
    logger.add(
        sys.stdout,
        level="INFO",
        format=log_format,
        backtrace=True,
        diagnose=False,
    )
    log_path = (os.getenv("BOT_LOG_PATH") or "").strip()
    if log_path:
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            path,
            level="INFO",
            format=log_format,
            rotation="1 MB",
            retention=3,
            backtrace=True,
            diagnose=False,
        )
