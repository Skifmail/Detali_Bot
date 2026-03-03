"""Инфраструктурный слой: внешние БД, очереди, интеграции."""

from bot.infrastructure.opencart_db import (
    OpenCartDb,
    fetch_categories,
    fetch_products_by_category,
)

__all__ = [
    "OpenCartDb",
    "fetch_categories",
    "fetch_products_by_category",
]
