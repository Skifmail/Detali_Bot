"""Конфигурация подключения к OpenCart (API и БД MySQL) для бота."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class OpenCartDbConfig:
    """Параметры подключения к MySQL БД OpenCart (только чтение каталога).

    Attributes:
        host: Хост MySQL (localhost при боте на том же сервере, что и сайт).
        port: Порт MySQL (обычно 3306).
        database: Имя базы данных.
        user: Пользователь MySQL (тот же, что в config.php или отдельный bot_user).
        password: Пароль пользователя.
        prefix: Префикс таблиц (oc_).
        store_id: ID магазина в OpenCart (обычно 0).
        language_id: ID языка для названий/описаний (обычно 1).
    """

    host: str
    port: int
    database: str
    user: str
    password: str
    prefix: str
    store_id: int
    language_id: int


def get_opencart_db_config() -> OpenCartDbConfig:
    """Читает конфигурацию БД OpenCart из переменных окружения.

    Ожидаемые переменные:
        OPENCART_DB_HOST, OPENCART_DB_PORT, OPENCART_DB_NAME,
        OPENCART_DB_USER, OPENCART_DB_PASSWORD, OPENCART_DB_PREFIX.
    Опционально: OPENCART_DB_STORE_ID (0), OPENCART_DB_LANGUAGE_ID (1).

    Returns:
        OpenCartDbConfig: Заполненный конфиг.

    Raises:
        RuntimeError: Если не заданы обязательные переменные.
    """
    host = (os.getenv("OPENCART_DB_HOST") or "localhost").strip()
    port_str = (os.getenv("OPENCART_DB_PORT") or "3306").strip()
    try:
        port = int(port_str)
    except ValueError:
        port = 3306
    database = (os.getenv("OPENCART_DB_NAME") or "").strip()
    user = (os.getenv("OPENCART_DB_USER") or "").strip()
    password = os.getenv("OPENCART_DB_PASSWORD", "")
    if isinstance(password, str) and password.startswith('"') and password.endswith('"'):
        password = password[1:-1]
    raw_prefix = (os.getenv("OPENCART_DB_PREFIX") or "oc_").strip()
    prefix = raw_prefix if re.match(r"^[a-zA-Z0-9_]+$", raw_prefix) else "oc_"
    store_id_str = (os.getenv("OPENCART_DB_STORE_ID") or "0").strip()
    language_id_str = (os.getenv("OPENCART_DB_LANGUAGE_ID") or "1").strip()
    try:
        store_id = int(store_id_str)
    except ValueError:
        store_id = 0
    try:
        language_id = int(language_id_str)
    except ValueError:
        language_id = 1

    if not database:
        raise RuntimeError("Не задана переменная OPENCART_DB_NAME в .env.")
    if not user:
        raise RuntimeError("Не задана переменная OPENCART_DB_USER в .env.")

    return OpenCartDbConfig(
        host=host,
        port=port,
        database=database,
        user=user,
        password=password,
        prefix=prefix,
        store_id=store_id,
        language_id=language_id,
    )


@dataclass(frozen=True)
class OpenCartConfig:
    """Параметры подключения к OpenCart REST API.

    Attributes:
        base_url: Базовый URL магазина (без/со слешем в конце — нормализуется).
        api_key: Ключ API из админки OpenCart (Система → Пользователи → API).
        api_username: Имя пользователя API (обычно «Default»).
        order_status_id: ID статуса заказа в OpenCart для заказов из бота (напр. 17).
        default_country_id: ID страны для адреса доставки (напр. 176 — Россия).
        default_zone_id: ID региона/зоны для адреса (0 если не используется).
        order_email: Email для заказов из бота (OpenCart требует email клиента).
    """

    base_url: str
    api_key: str
    api_username: str
    order_status_id: int
    default_country_id: int
    default_zone_id: int
    order_email: str

    def api_url(self, route: str) -> str:
        """Собирает URL для вызова API.

        Args:
            route: Маршрут API, например «api/login» или «api/order/add».

        Returns:
            str: Полный URL вида {base_url}index.php?route={route}.
        """
        base = self.base_url.rstrip("/")
        return f"{base}/index.php?route={route}"


def get_opencart_config() -> OpenCartConfig:
    """Читает конфигурацию OpenCart из переменных окружения.

    Ожидаемые переменные:
        OPENCART_BASE_URL или PENCART_BASE_URL — базовый URL магазина.
        OPENCART_API_KEY — ключ API.
        OPENCART_API_USERNAME — имя пользователя API (по умолчанию «Default»).
        OPENCART_ORDER_STATUS_ID — ID статуса заказа для бота (по умолчанию 17).

    Returns:
        OpenCartConfig: Заполненный конфиг.

    Raises:
        RuntimeError: Если не заданы обязательные OPENCART_BASE_URL и OPENCART_API_KEY.
    """
    base_url = (os.getenv("OPENCART_BASE_URL") or os.getenv("PENCART_BASE_URL") or "").strip()
    api_key = (os.getenv("OPENCART_API_KEY") or "").strip()
    api_username = (os.getenv("OPENCART_API_USERNAME") or "Default").strip()
    raw_status = os.getenv("OPENCART_ORDER_STATUS_ID", "17").strip()
    try:
        order_status_id = int(raw_status)
    except ValueError:
        order_status_id = 17

    if not base_url:
        raise RuntimeError(
            "Не задана переменная OPENCART_BASE_URL (или PENCART_BASE_URL). " "Укажите базовый URL магазина в .env."
        )
    if not api_key:
        raise RuntimeError("Не задана переменная OPENCART_API_KEY. " "Укажите ключ API из админки OpenCart в .env.")

    raw_country = (os.getenv("OPENCART_DEFAULT_COUNTRY_ID") or "176").strip()
    raw_zone = (os.getenv("OPENCART_DEFAULT_ZONE_ID") or "0").strip()
    try:
        default_country_id = int(raw_country)
    except ValueError:
        default_country_id = 176
    try:
        default_zone_id = int(raw_zone)
    except ValueError:
        default_zone_id = 0
    order_email = (os.getenv("OPENCART_ORDER_EMAIL") or "bot@placeholder.local").strip()

    return OpenCartConfig(
        base_url=base_url,
        api_key=api_key,
        api_username=api_username,
        order_status_id=order_status_id,
        default_country_id=default_country_id,
        default_zone_id=default_zone_id,
        order_email=order_email,
    )
