"""Ссылки на правовые документы (оферта, политика ПДн) для соответствия законодательству РФ."""

from __future__ import annotations

import os


def get_offer_url() -> str | None:
    """Возвращает URL договора-оферты из окружения.

    Returns:
        Строка URL или None, если BOT_OFFER_URL не задан.
    """
    raw = (os.getenv("BOT_OFFER_URL") or "").strip()
    return raw if raw else None


def get_privacy_policy_url() -> str | None:
    """Возвращает URL политики обработки персональных данных из окружения.

    Returns:
        Строка URL или None, если BOT_PRIVACY_POLICY_URL не задан.
    """
    raw = (os.getenv("BOT_PRIVACY_POLICY_URL") or "").strip()
    return raw if raw else None
