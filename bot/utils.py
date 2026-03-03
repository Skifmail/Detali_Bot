from __future__ import annotations

import re

from aiogram import Bot
from aiogram.types import CallbackQuery, Message

from .database.db import Database

_PHONE_REGEX = re.compile(r"^(?:\+7|8)\d{10}$")


def get_db(bot: Bot) -> Database:
    """Возвращает экземпляр базы данных, прикреплённый к боту.

    Args:
        bot (Bot): Экземпляр бота aiogram.

    Returns:
        Database: Экземпляр базы данных, хранящийся в атрибуте bot.db.

    Raises:
        RuntimeError: Если атрибут bot.db не инициализирован или имеет некорректный тип.
    """

    db_attr = getattr(bot, "db", None)
    if not isinstance(db_attr, Database):
        raise RuntimeError("bot.db не инициализирован или имеет некорректный тип")
    return db_attr


def is_admin(user_id: int, bot: Bot) -> bool:
    """Проверяет, является ли пользователь администратором бота.

    Args:
        user_id (int): Telegram ID пользователя.
        bot (Bot): Экземпляр бота aiogram.

    Returns:
        bool: True, если пользователь входит в список admin_ids.
    """

    admin_ids: set[int] = getattr(bot, "admin_ids", set())
    return user_id in admin_ids


def normalize_phone(source: str) -> str | None:
    """Нормализует телефон в человеко-читаемый формат и проверяет по внутреннему regex.

    Args:
        source (str): Исходная строка с номером телефона.

    Returns:
        str | None: Нормализованный номер или None, если формат некорректен.
    """

    digits_only = re.sub(r"\D", "", source)
    if len(digits_only) == 11 and digits_only.startswith("7"):
        phone_normalized = f"+7{digits_only[1:]}"
    elif len(digits_only) == 11 and digits_only.startswith("8"):
        phone_normalized = f"8{digits_only[1:]}"
    else:
        phone_normalized = source.strip().replace(" ", "")

    if not _PHONE_REGEX.match(phone_normalized):
        return None
    return phone_normalized


def get_db_from_message(message: Message) -> Database:
    """Удобный хелпер для получения базы из объекта Message."""

    return get_db(message.bot)


def get_db_from_callback(callback: CallbackQuery) -> Database:
    """Удобный хелпер для получения базы из объекта CallbackQuery."""

    return get_db(callback.bot)
