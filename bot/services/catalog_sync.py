"""Синхронизация каталога из БД OpenCart в SQLite бота."""

from __future__ import annotations

import pymysql
from loguru import logger

from bot.core.opencart_config import (
    OpenCartDbConfig,
    get_opencart_config,
    get_opencart_db_config,
)
from bot.database.db import Database
from bot.infrastructure.opencart_db import OpenCartDb


async def sync_catalog_from_opencart(db: Database) -> None:
    """Загружает категории и товары из MySQL OpenCart в локальную БД бота.

    Сначала деактивирует все товары (is_active=0), затем для каждой категории
    и товара из OpenCart создаёт или обновляет записи и ставит is_active=1.
    Товары, которых нет в OpenCart, остаются неактивными и не показываются в каталоге.

    Args:
        db: Экземпляр Database (SQLite бота).

    Raises:
        RuntimeError: Если не заданы конфиги OpenCart (БД или base_url).
    """
    try:
        oc_db_config = get_opencart_db_config()
    except RuntimeError as e:
        logger.warning(
            "Синхронизация каталога пропущена: не задана конфигурация БД OpenCart — {}",
            e,
        )
        return

    try:
        oc_api_config = get_opencart_config()
        base_url = oc_api_config.base_url.rstrip("/")
    except RuntimeError:
        base_url = ""
    if not base_url:
        logger.warning("Синхронизация каталога: OPENCART_BASE_URL не задан, " "URL изображений товаров будет пустым.")
        base_url = ""

    db.deactivate_all_products_for_sync()

    try:
        await _run_sync(db, oc_db_config, base_url)
    except (pymysql.err.OperationalError, OSError) as e:
        logger.warning(
            "MySQL OpenCart недоступен (бот запущен не на хостинге?): {}. Каталог не обновлён.",
            e,
        )


async def _run_sync(db: Database, oc_db_config: OpenCartDbConfig, base_url: str) -> None:
    """Выполняет загрузку категорий и товаров из OpenCart в SQLite."""
    async with OpenCartDb(oc_db_config) as oc_db:
        categories = await oc_db.fetch_categories(parent_id=0)
        oc_to_our: dict[int, int] = {}
        for cat in categories:
            oc_id = int(cat["category_id"])
            name = (cat.get("name") or "").strip()
            our_id = db.get_or_create_category_by_opencart_id(oc_id, name)
            oc_to_our[oc_id] = our_id

        for cat in categories:
            oc_id = int(cat["category_id"])
            our_cat_id = oc_to_our[oc_id]
            products = await oc_db.fetch_products_by_category(oc_id)
            for p in products:
                pid = int(p["product_id"])
                title = (p.get("name") or "").strip()
                raw_price = p.get("price")
                price = int(float(raw_price)) if raw_price is not None else 0
                desc = (p.get("description") or "").strip()
                if len(desc) > 5000:
                    desc = desc[:4997] + "..."
                image_path = (p.get("image") or "").strip()
                image_url = f"{base_url}/image/{image_path}" if image_path else ""
                db.upsert_product_from_opencart(
                    opencart_product_id=pid,
                    category_id=our_cat_id,
                    title=title,
                    description=desc,
                    price=price,
                    image_url=image_url or " ",
                )

    logger.info(
        "Синхронизация каталога OpenCart завершена: {} категорий, товары обновлены",
        len(categories),
    )
