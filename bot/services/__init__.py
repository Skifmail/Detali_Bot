"""Сервисный слой: сценарии и оркестрация."""

from bot.services.catalog_sync import sync_catalog_from_opencart

__all__ = ["sync_catalog_from_opencart"]
