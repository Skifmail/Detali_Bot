"""Подключение к MySQL БД OpenCart для чтения каталога (категории и товары)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import aiomysql
from loguru import logger

from bot.core.opencart_config import OpenCartDbConfig, get_opencart_db_config


class OpenCartDb:
    """Асинхронный доступ к БД OpenCart (MySQL): пул соединений и выборка каталога.

    Использование: как async context manager для получения пула и вызова
    fetch_categories / fetch_products_by_category.
    """

    def __init__(self, config: OpenCartDbConfig | None = None) -> None:
        """Инициализирует клиент конфигом БД.

        Args:
            config: Конфиг БД. Если None — загружается из env (get_opencart_db_config).
        """
        self._config = config or get_opencart_db_config()
        self._pool: aiomysql.Pool | None = None

    async def __aenter__(self) -> OpenCartDb:
        """Создаёт пул соединений к MySQL."""
        self._pool = await aiomysql.create_pool(
            host=self._config.host,
            port=self._config.port,
            user=self._config.user,
            password=self._config.password,
            db=self._config.database,
            minsize=1,
            maxsize=5,
            autocommit=True,
        )
        logger.info(
            "Пул MySQL OpenCart создан: host={}, db={}",
            self._config.host,
            self._config.database,
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Закрывает пул соединений."""
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None
            logger.debug("Пул MySQL OpenCart закрыт")

    @asynccontextmanager
    async def _conn(self) -> Any:
        """Внутренний контекст: одно соединение из пула с DictCursor."""
        if not self._pool:
            raise RuntimeError("Пул не создан. Используйте OpenCartDb как async with.")
        async with (
            self._pool.acquire() as conn,
            conn.cursor(aiomysql.DictCursor) as cur,
        ):
            yield cur

    async def fetch_categories(self, parent_id: int = 0) -> list[dict[str, Any]]:
        """Возвращает список категорий верхнего уровня (или дочерних по parent_id).

        Args:
            parent_id: ID родительской категории (0 — корневые).

        Returns:
            Список словарей с ключами category_id, name, sort_order.
        """
        p = self._config.prefix
        async with self._conn() as cur:
            await cur.execute(
                f"""
                SELECT c.category_id, cd.name, c.sort_order
                FROM {p}category c
                LEFT JOIN {p}category_description cd ON c.category_id = cd.category_id
                LEFT JOIN {p}category_to_store c2s ON c.category_id = c2s.category_id
                WHERE c.parent_id = %s AND cd.language_id = %s
                  AND c2s.store_id = %s AND c.status = 1
                ORDER BY c.sort_order, LCASE(cd.name)
                """,
                (parent_id, self._config.language_id, self._config.store_id),
            )
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def fetch_products_by_category(self, category_id: int) -> list[dict[str, Any]]:
        """Товары выбранной категории (доступные, магазин и язык из конфига).

        Args:
            category_id: ID категории в OpenCart.

        Returns:
            Список словарей: product_id, name, image, price, model, description.
        """
        p = self._config.prefix
        async with self._conn() as cur:
            await cur.execute(
                f"""
                SELECT p.product_id, pd.name, p.image, p.price, p.model, pd.description
                FROM {p}product p
                LEFT JOIN {p}product_description pd ON p.product_id = pd.product_id
                LEFT JOIN {p}product_to_store p2s ON p.product_id = p2s.product_id
                LEFT JOIN {p}product_to_category p2c ON p.product_id = p2c.product_id
                WHERE p2c.category_id = %s AND pd.language_id = %s
                  AND p2s.store_id = %s AND p.status = 1
                  AND (p.date_available IS NULL OR p.date_available <= NOW())
                ORDER BY p.sort_order, LCASE(pd.name)
                """,
                (category_id, self._config.language_id, self._config.store_id),
            )
            rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def fetch_categories(
    parent_id: int = 0,
    config: OpenCartDbConfig | None = None,
) -> list[dict[str, Any]]:
    """Удобная функция: один запрос категорий без ручного управления пулом.

    Создаёт пул, запрашивает категории, закрывает пул. Для частых вызовов
    предпочтительнее держать экземпляр OpenCartDb в async with.

    Args:
        parent_id: ID родительской категории (0 — корневые).
        config: Конфиг БД (по умолчанию из env).

    Returns:
        Список словарей category_id, name, sort_order.
    """
    cfg = config or get_opencart_db_config()
    async with OpenCartDb(cfg) as db:
        return await db.fetch_categories(parent_id=parent_id)


async def fetch_products_by_category(
    category_id: int,
    config: OpenCartDbConfig | None = None,
) -> list[dict[str, Any]]:
    """Удобная функция: один запрос товаров по категории без ручного управления пулом.

    Args:
        category_id: ID категории в OpenCart.
        config: Конфиг БД (по умолчанию из env).

    Returns:
        Список словарей product_id, name, image, price, model, description.
    """
    cfg = config or get_opencart_db_config()
    async with OpenCartDb(cfg) as db:
        return await db.fetch_products_by_category(category_id)
