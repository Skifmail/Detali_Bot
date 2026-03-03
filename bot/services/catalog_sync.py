"""Синхронизация каталога из БД OpenCart в SQLite бота."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

import pymysql
from loguru import logger

from bot.core.opencart_config import (
    OpenCartDbConfig,
    get_opencart_config,
    get_opencart_db_config,
)
from bot.database.db import Database
from bot.infrastructure.opencart_db import OpenCartDb

DESCRIPTION_MAX_LENGTH: int = 5000


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
        raise

    try:
        oc_api_config = get_opencart_config()
        base_url = oc_api_config.base_url.rstrip("/")
    except RuntimeError:
        base_url = ""
    if not base_url:
        logger.warning(
            "Синхронизация каталога: OPENCART_BASE_URL не задан, " "URL изображений товаров будет пустым.",
        )

    try:
        await _run_sync(db, oc_db_config, base_url)
    except (pymysql.err.OperationalError, OSError) as e:
        logger.warning(
            "MySQL OpenCart недоступен (бот запущен не на хостинге?): {}. Каталог не обновлён.",
            e,
        )
        raise


async def _run_sync(db: Database, oc_db_config: OpenCartDbConfig, base_url: str) -> None:
    """Выполняет загрузку категорий и товаров из OpenCart в SQLite.

    Важно:
    - Сначала полностью читаем категории и товары из MySQL OpenCart.
      Если соединение оборвётся, локальная БД бота не трогаем.
    - Только после успешного чтения деактивируем все товары в SQLite и
      начинаем апсертить новые данные.
    """
    async with OpenCartDb(oc_db_config) as oc_db:
        categories = await oc_db.fetch_categories(parent_id=0)
        products_by_category: dict[int, list[dict[str, object]]] = {}
        for cat in categories:
            oc_id = int(cat["category_id"])
            products = await oc_db.fetch_products_by_category(oc_id)
            products_by_category[oc_id] = products

    db.deactivate_all_products_for_sync()

    oc_to_our: dict[int, int] = {}
    for cat in categories:
        oc_id_raw = cat.get("category_id")
        oc_id = int(oc_id_raw) if oc_id_raw is not None else 0
        name_raw = cat.get("name")
        name = str(name_raw).strip() if name_raw is not None else ""
        our_id = db.get_or_create_category_by_opencart_id(oc_id, name)
        oc_to_our[oc_id] = our_id

    for cat in categories:
        oc_id_raw = cat.get("category_id")
        oc_id = int(oc_id_raw) if oc_id_raw is not None else 0
        our_cat_id = oc_to_our[oc_id]
        products = products_by_category.get(oc_id, [])
        logger.debug(
            "Категория '{name}' (opencart_id={oc_id}): {count} товаров",
            name=name,
            oc_id=oc_id,
            count=len(products),
        )
        for p in products:
            pid_raw = p.get("product_id")
            if isinstance(pid_raw, (str | int)):
                try:
                    pid = int(pid_raw)
                except (TypeError, ValueError):
                    logger.warning("Пропускаем товар с некорректным product_id: {raw}", raw=pid_raw)
                    continue
            else:
                logger.warning("Пропускаем товар с некорректным типом product_id: {raw}", raw=pid_raw)
                continue
            title_raw = p.get("name")
            title = str(title_raw).strip() if title_raw is not None else ""
            raw_price = p.get("price")
            price: int
            if raw_price is None:
                price = 0
            else:
                try:
                    price_decimal = Decimal(str(raw_price))
                    price = int(price_decimal.to_integral_value())
                except (InvalidOperation, ValueError, TypeError) as exc:
                    logger.warning(
                        "Некорректная цена товара product_id={pid}: {raw}. Ошибка: {err}. Устанавливаем 0.",
                        pid=pid,
                        raw=raw_price,
                        err=exc,
                    )
                    price = 0
            desc_raw = p.get("description")
            desc = str(desc_raw).strip() if desc_raw is not None else ""
            if len(desc) > DESCRIPTION_MAX_LENGTH:
                desc = desc[: DESCRIPTION_MAX_LENGTH - 3] + "..."
            image_raw = p.get("image")
            image_path = str(image_raw).strip() if image_raw is not None else ""
            image_url = f"{base_url}/image/{image_path}" if image_path else ""
            try:
                db.upsert_product_from_opencart(
                    opencart_product_id=pid,
                    category_id=our_cat_id,
                    title=title,
                    description=desc,
                    price=price,
                    image_url=image_url or "",
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Не удалось синхронизировать товар product_id={pid}: {err}",
                    pid=pid,
                    err=exc,
                )
                continue

    logger.info(
        "Синхронизация каталога OpenCart завершена: {cat_count} категорий, товары обновлены",
        cat_count=len(categories),
    )
