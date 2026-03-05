from __future__ import annotations

import asyncio
import os
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from dotenv import load_dotenv
from loguru import logger

from .core.logging import setup_logging
from .database.db import create_default_database
from .handlers import account, admin, cart, catalog, contact_fallback, order, payment, start
from .middlewares.user import UserMiddleware
from .services.catalog_sync import sync_catalog_from_opencart


async def _set_bot_commands(bot: Bot) -> None:
    """Устанавливает список команд бота в интерфейсе Telegram.

    Args:
        bot (Bot): Экземпляр бота aiogram.

    Returns:
        None: Ничего не возвращает.
    """

    # Команда /admin не добавляем в меню — она видна только при вводе вручную; доступ по ADMIN_IDS.
    commands = [
        BotCommand(command="start", description="🏠 Главное меню"),
        BotCommand(command="catalog", description="🌸 Каталог"),
        BotCommand(command="cart", description="🛒 Корзина"),
        BotCommand(command="account", description="👤 Личный кабинет"),
    ]
    await bot.set_my_commands(commands)


def _load_admin_ids() -> set[int]:
    """Загружает идентификаторы администраторов из переменной окружения.

    Returns:
        Set[int]: Множество Telegram ID администраторов.
    """

    raw = os.getenv("ADMIN_IDS", "")
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            logger.warning("Пропущен некорректный ADMIN_ID: {value}", value=part)
    return ids


async def main() -> None:
    """Точка входа Telegram-бота floraldetails demo.

    Returns:
        None: Ничего не возвращает.
    """

    setup_logging()
    load_dotenv()
    # Поддержка запуска из корня проекта: дополнительно подгружаем bot/.env.
    # Переменные, уже заданные, не переопределяются (override=False по умолчанию).
    bot_dir = Path(__file__).resolve().parent
    load_dotenv(bot_dir / ".env")

    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("Не задана переменная окружения BOT_TOKEN")

    db = create_default_database()

    skip_sync = os.getenv("SKIP_OPENCART_SYNC", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if not skip_sync:
        try:
            await sync_catalog_from_opencart(db)
        except Exception:
            logger.exception("Ошибка синхронизации каталога из OpenCart, бот запущен с текущими данными")
    else:
        logger.info("Синхронизация каталога пропущена (SKIP_OPENCART_SYNC)")
    db.seed_demo_catalog_if_empty()

    bot = Bot(
        token=bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dp = Dispatcher()
    # Регистрируем зависимости в диспетчере для последующей инъекции в хэндлеры и мидлвари.
    dp["db"] = db
    dp["admin_ids"] = _load_admin_ids()
    # Временный бридж для существующего кода, использующего bot.db / bot.admin_ids.
    bot.db = db
    bot.admin_ids = dp["admin_ids"]
    dp.update.middleware(UserMiddleware())

    dp.include_router(start.router)
    dp.include_router(catalog.router)
    dp.include_router(cart.router)
    dp.include_router(order.router)
    dp.include_router(payment.router)
    dp.include_router(account.router)
    dp.include_router(admin.router)
    dp.include_router(contact_fallback.router)

    await _set_bot_commands(bot)
    logger.info("Запуск бота floraldetails demo")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
