from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from ..database.db import Database


class UserMiddleware(BaseMiddleware):
    """Мидлварь, обеспечивающая автосоздание пользователя в БД.

    Args:
        BaseMiddleware: Базовый класс мидлвари aiogram.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        """Гарантирует наличие пользователя в БД и добавляет его в контекст.

        Args:
            handler (Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]]): Обработчик следующего уровня.
            event (TelegramObject): Входящее событие Telegram.
            data (Dict[str, Any]): Контекстные данные aiogram.

        Returns:
            Any: Результат выполнения следующего обработчика.
        """

        event_from_user = data.get("event_from_user")
        if event_from_user is None:
            return await handler(event, data)

        bot = data["bot"]
        db: Database = bot.db

        current_user = db.get_or_create_user(
            tg_id=event_from_user.id,
            first_name=event_from_user.first_name,
            last_name=event_from_user.last_name,
        )
        data["current_user"] = current_user
        return await handler(event, data)
