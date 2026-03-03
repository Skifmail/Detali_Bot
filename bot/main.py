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
from .handlers import account, admin, cart, catalog, order, payment
from .keyboards.kb import TEXTS as KB_TEXTS
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
    # Поддержка запуска из корня проекта: подгружаем bot/.env
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
    if skip_sync or not db.list_categories():
        db.seed_demo_catalog_if_empty()

    bot = Bot(
        token=bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    # сохраняем контекст на объекте бота, чтобы использовать его в хендлерах
    bot.db = db
    bot.admin_ids = _load_admin_ids()

    dp = Dispatcher()
    dp.update.middleware(UserMiddleware())

    dp.include_router(catalog.router)
    dp.include_router(cart.router)
    dp.include_router(order.router)
    dp.include_router(payment.router)
    dp.include_router(account.router)
    dp.include_router(admin.router)

    from aiogram import F
    from aiogram.types import Message

    @dp.message(F.text.in_({"/start", KB_TEXTS["start_over"]}))
    async def handle_start_and_main_menu(message: Message) -> None:
        """Обрабатывает /start и кнопку главного меню.

        Args:
            message (Message): Сообщение пользователя.

        Returns:
            None: Ничего не возвращает.
        """

        from .keyboards.kb import build_main_menu_keyboard

        text = (message.text or "").strip()
        is_admin = message.from_user.id in getattr(bot, "admin_ids", set())
        keyboard = build_main_menu_keyboard(is_admin=is_admin)

        if text == "/start":
            if is_admin:
                welcome = (
                    "🌸 Добро пожаловать в демо-бот floraldetails.ru!\n\n"
                    "Используйте меню: Каталог, Заказы, Статистика, Рассылка, Ещё."
                )
            else:
                welcome = (
                    "🌸 Добро пожаловать в демо-бот floraldetails.ru!\n\n"
                    "Используйте меню ниже, чтобы посмотреть каталог, корзину и личный кабинет."
                )
            await message.answer(welcome, reply_markup=keyboard)
        else:
            await message.answer(
                "🏠 Возвращаемся в главное меню.",
                reply_markup=keyboard,
            )

    await _set_bot_commands(bot)
    logger.info("Запуск бота floraldetails demo")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
