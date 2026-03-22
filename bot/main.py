from __future__ import annotations

import asyncio
import os
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from aiohttp import BasicAuth
from dotenv import load_dotenv
from loguru import logger

from .core.logging import setup_logging
from .core.runtime_info import set_bot_started_at
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


def _telegram_proxy_for_session() -> str | tuple[str, BasicAuth] | None:
    """Возвращает аргумент proxy для AiohttpSession или None.

    Приоритет: BOT_TELEGRAM_PROXY_HOST (+ USER/PASSWORD) — пароль без проблем с символами в URL.
    Иначе: одна строка BOT_TELEGRAM_PROXY (как в документации aiogram).

    Returns:
        Строка URL, пара (URL, BasicAuth) или None.
    """
    host = (os.getenv("BOT_TELEGRAM_PROXY_HOST") or "").strip()
    if host:
        port = (os.getenv("BOT_TELEGRAM_PROXY_PORT") or "1080").strip()
        user = (os.getenv("BOT_TELEGRAM_PROXY_USER") or "").strip()
        password = (os.getenv("BOT_TELEGRAM_PROXY_PASSWORD") or "").strip()
        url = f"socks5://{host}:{port}"
        if user:
            return (url, BasicAuth(user, password))
        return url
    proxy_raw = (os.getenv("BOT_TELEGRAM_PROXY") or "").strip()
    return proxy_raw or None


async def main() -> None:
    """Точка входа Telegram-бота floraldetails demo.

    Returns:
        None: Ничего не возвращает.
    """

    setup_logging()
    set_bot_started_at()
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

    # При блокировке api.telegram.org задайте прокси (см. BOT_TELEGRAM_PROXY или BOT_TELEGRAM_PROXY_HOST).
    proxy_arg = _telegram_proxy_for_session()
    if proxy_arg:
        logger.info("Используется прокси для Telegram Bot API")
        session = AiohttpSession(proxy=proxy_arg)
        bot = Bot(
            token=bot_token,
            session=session,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
    else:
        bot = Bot(
            token=bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )

    dp = Dispatcher()
    admin_ids_from_env = _load_admin_ids()
    admin_ids_from_db = set(db.list_bot_admin_ids())
    merged_admin_ids = admin_ids_from_env | admin_ids_from_db
    dp["db"] = db
    dp["admin_ids"] = merged_admin_ids
    # Временный бридж для существующего кода, использующего bot.db / bot.admin_ids.
    bot.db = db
    bot.admin_ids = merged_admin_ids
    # Админы из env нельзя удалить через бота; при добавлении/удалении в БД обновляем bot.admin_ids.
    bot._admin_ids_from_env = admin_ids_from_env
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

    import uvicorn

    from .api.yookassa_webhook import create_yookassa_webhook_app
    from .core.yookassa_config import get_yookassa_config

    yookassa_config = get_yookassa_config()
    if yookassa_config is not None:
        webhook_app = create_yookassa_webhook_app(bot=bot, db=db)
        config = uvicorn.Config(
            webhook_app,
            host="0.0.0.0",
            port=yookassa_config.webhook_port,
            log_level="warning",
        )
        server = uvicorn.Server(config)
        asyncio.create_task(server.serve())
        logger.info(
            "ЮKassa webhook: HTTP-сервер запущен на порту {} (URL для настройки в ЛК ЮKassa: https://ваш-домен/webhook/yookassa)",
            yookassa_config.webhook_port,
        )
    else:
        logger.info("ЮKassa не настроена (YOOKASSA_SHOP_ID/SECRET_KEY не заданы), оплата в режиме демо")

    logger.info("Запуск бота floraldetails demo")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
