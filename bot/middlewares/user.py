from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from loguru import logger

from ..database.db import Database


class UserMiddleware(BaseMiddleware):
    """Мидлварь, обеспечивающая автосоздание пользователя в БД и добавление его в контекст.

    Мидлварь читает пользователя из события (`event_from_user`), гарантирует его наличие
    в базе данных и прокидывает объект текущего пользователя в `data["current_user"]`.
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

        db_from_ctx = data.get("db")
        if isinstance(db_from_ctx, Database):
            db: Database = db_from_ctx
        else:
            bot = data.get("bot")
            if bot is None:
                logger.error("UserMiddleware: в data отсутствует bot, пропускаем создание пользователя")
                return await handler(event, data)

            db_attr = getattr(bot, "db", None)
            if not isinstance(db_attr, Database):
                logger.error(
                    "UserMiddleware: bot.db не является экземпляром Database "
                    "(type={type_name}), пропускаем создание пользователя",
                    type_name=type(db_attr).__name__,
                )
                return await handler(event, data)

            db = db_attr
        try:
            current_user = db.get_or_create_user(
                tg_id=event_from_user.id,
                first_name=event_from_user.first_name,
                last_name=event_from_user.last_name,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "UserMiddleware: не удалось создать/получить пользователя tg_id={tg_id}: {err}",
                tg_id=event_from_user.id,
                err=exc,
            )
            return await handler(event, data)

        data["current_user"] = current_user
        return await handler(event, data)
