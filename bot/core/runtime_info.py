"""Время запуска процесса бота для отображения uptime в админ-интерфейсе."""

from __future__ import annotations

from datetime import UTC, datetime

_bot_started_at: datetime | None = None


def set_bot_started_at() -> None:
    """Фиксирует момент старта процесса (вызывать один раз при запуске бота).

    Args:
        Нет.

    Returns:
        None: Ничего не возвращает.
    """

    global _bot_started_at
    _bot_started_at = datetime.now(UTC)


def get_bot_started_at() -> datetime | None:
    """Возвращает время старта процесса, если оно уже зафиксировано.

    Args:
        Нет.

    Returns:
        datetime | None: Время в UTC или None, если set_bot_started_at ещё не вызывался.
    """

    return _bot_started_at


def format_uptime_human() -> str:
    """Форматирует длительность работы процесса с момента set_bot_started_at.

    Args:
        Нет.

    Returns:
        str: Строка вида «Xd HH:MM:SS» или «—», если время старта неизвестно.
    """

    if _bot_started_at is None:
        return "—"
    delta = datetime.now(UTC) - _bot_started_at
    total_seconds = int(delta.total_seconds())
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    if days > 0:
        return f"{days}д {hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
