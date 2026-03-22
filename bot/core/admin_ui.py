"""Вспомогательные функции для единого «слота» сообщений админ-панели в чате."""

from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest


def _tracked_store(bot: Bot) -> dict[int, list[int]]:
    """Возвращает словарь chat_id → список message_id отслеживаемых сообщений бота.

    Args:
        bot (Bot): Экземпляр бота (хранит атрибут на объекте).

    Returns:
        dict[int, list[int]]: Хранилище идентификаторов сообщений для последующего удаления.
    """

    if not hasattr(bot, "_admin_ui_tracked_message_ids"):
        bot._admin_ui_tracked_message_ids = {}
    store: dict[int, list[int]] = bot._admin_ui_tracked_message_ids
    return store


async def delete_tracked_admin_messages(bot: Bot, chat_id: int) -> None:
    """Удаляет ранее отслеживаемые сообщения бота в чате (очищает «слот» админ-экрана).

    Args:
        bot (Bot): Экземпляр бота.
        chat_id (int): Идентификатор чата.

    Returns:
        None: Ничего не возвращает.
    """

    store = _tracked_store(bot)
    for mid in store.pop(chat_id, []):
        try:
            await bot.delete_message(chat_id=chat_id, message_id=mid)
        except TelegramBadRequest:
            pass


def track_admin_messages(bot: Bot, chat_id: int, message_ids: list[int]) -> None:
    """Сохраняет id сообщений бота для последующей замены при следующем экране админки.

    Args:
        bot (Bot): Экземпляр бота.
        chat_id (int): Идентификатор чата.
        message_ids (list[int]): Список message_id (порядок не важен).

    Returns:
        None: Ничего не возвращает.
    """

    _tracked_store(bot)[chat_id] = list(message_ids)
