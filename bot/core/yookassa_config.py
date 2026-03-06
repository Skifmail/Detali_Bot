"""Конфигурация ЮKassa для приёма платежей.

Переменные окружения:
    YOOKASSA_SHOP_ID — идентификатор магазина (shopId из личного кабинета ЮKassa).
    YOOKASSA_SECRET_KEY — секретный ключ (для API).
    YOOKASSA_RETURN_URL — URL, на который вернуть пользователя после оплаты (опционально).
    YOOKASSA_WEBHOOK_PORT — порт для приёма webhook от ЮKassa (по умолчанию 8080).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class YooKassaConfig:
    """Параметры подключения к ЮKassa.

    Attributes:
        shop_id: Идентификатор магазина (shopId).
        secret_key: Секретный ключ API.
        return_url: URL возврата после оплаты (опционально).
        webhook_port: Порт для HTTP-сервера webhook.
    """

    shop_id: str
    secret_key: str
    return_url: str | None
    webhook_port: int


def get_yookassa_config() -> YooKassaConfig | None:
    """Читает конфигурацию ЮKassa из переменных окружения.

    Если YOOKASSA_SHOP_ID или YOOKASSA_SECRET_KEY не заданы,
    возвращает None (реальная оплата отключена, используется мок).

    Returns:
        YooKassaConfig или None, если конфиг неполный.
    """
    shop_id = (os.getenv("YOOKASSA_SHOP_ID") or "").strip()
    secret_key = (os.getenv("YOOKASSA_SECRET_KEY") or "").strip()
    if not shop_id or not secret_key:
        return None
    return_url = (os.getenv("YOOKASSA_RETURN_URL") or "").strip() or None
    port_str = (os.getenv("YOOKASSA_WEBHOOK_PORT") or "8080").strip()
    try:
        webhook_port = int(port_str)
    except ValueError:
        webhook_port = 8080
    return YooKassaConfig(
        shop_id=shop_id,
        secret_key=secret_key,
        return_url=return_url,
        webhook_port=webhook_port,
    )
