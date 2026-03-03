"""Создание заказа в OpenCart через API после подтверждения в боте."""

from __future__ import annotations

from typing import Any

from loguru import logger

from bot.core.opencart_client import OpenCartAPIError, OpenCartClient
from bot.core.opencart_config import get_opencart_config
from bot.database.models import Order

DEFAULT_CUSTOMER_FIRST_NAME = "Клиент"
FALLBACK_EMAIL_DOMAIN = "example.com"


def _split_name(full_name: str) -> tuple[str, str]:
    """Делит «Имя Фамилия» на firstname и lastname (для OpenCart).

    Args:
        full_name (str): Полное имя пользователя.

    Returns:
        tuple[str, str]: Пара (firstname, lastname). При пустом имени используется
            значение по умолчанию для firstname и пустая фамилия.
    """

    parts = (full_name or "").strip().split(None, 1)
    if not parts:
        return (DEFAULT_CUSTOMER_FIRST_NAME, "")
    if len(parts) == 1:
        return (parts[0], "")
    return (parts[0], parts[1])


async def create_order_in_opencart(order: Order) -> int | None:
    """Создаёт заказ в OpenCart по данным заказа из бота.

    Использует API: логин, клиент, адреса, корзина, способы оплаты/доставки,
    order/add. Товары без opencart_product_id пропускаются; если таких все —
    заказ в OpenCart не создаётся.

    Args:
        order: Заказ из БД бота (с items и product.opencart_product_id).

    Returns:
        ID заказа в OpenCart или None при ошибке / отсутствии товаров для OC.
    """
    try:
        config = get_opencart_config()
    except RuntimeError as e:
        logger.warning("OpenCart API не настроен: {}", e)
        return None

    products_oc: list[dict[str, int]] = []
    for item in order.items:
        pid = item.product.opencart_product_id
        if pid is not None:
            products_oc.append({"product_id": pid, "quantity": item.quantity})
    if not products_oc:
        logger.warning(
            "Заказ id={} не передан в OpenCart: нет товаров с opencart_product_id",
            order.id,
        )
        return None

    firstname, lastname = _split_name(order.customer_name)
    city = (order.delivery_city or "Не указан").strip()
    address_1 = (order.delivery_address or "").strip() or "—"
    email = (order.email or "").strip() or config.order_email
    if not email:
        email = f"bot-order-{order.id}@{FALLBACK_EMAIL_DOMAIN}"

    async with OpenCartClient(config) as client:
        try:
            await client.login()
            await client.set_customer(
                customer_id=0,
                firstname=firstname,
                lastname=lastname,
                email=email,
                telephone=order.phone or "",
            )
            await client.set_payment_address(
                firstname=firstname,
                lastname=lastname,
                address_1=address_1,
                city=city,
                zone_id=config.default_zone_id,
                country_id=config.default_country_id,
            )
            await client.set_shipping_address(
                firstname=firstname,
                lastname=lastname,
                address_1=address_1,
                city=city,
                zone_id=config.default_zone_id,
                country_id=config.default_country_id,
            )
            await client.cart_add(products_oc)

            payment_methods = await client.get_payment_methods()
            payment_code = _first_key(payment_methods)
            if not payment_code:
                logger.warning("OpenCart не вернул способы оплаты для заказа id={}", order.id)
                return None

            shipping_methods = await client.get_shipping_methods()
            shipping_code = _first_shipping_code(shipping_methods)

            oc_order_id = await client.add_order(
                payment_method=payment_code,
                shipping_method=shipping_code,
                comment=(order.comment or "").strip(),
            )
            logger.info(
                "Заказ бота id={} создан в OpenCart как order_id={}",
                order.id,
                oc_order_id,
            )
            return oc_order_id
        except OpenCartAPIError as e:
            logger.error(
                "Ошибка OpenCart API при создании заказа id={}: {}",
                order.id,
                e,
            )
            return None
        except Exception as e:  # noqa: BLE001
            # Логируем полную трассировку и тип исключения для точной диагностики.
            logger.exception(
                "Неожиданная ошибка при создании заказа id={} в OpenCart " "(type={exc_type}): {error}",
                order.id,
                exc_type=type(e).__name__,
                error=e,
            )
            return None


def _first_key(d: dict[str, Any]) -> str | None:
    """Возвращает первый ключ словаря (код способа оплаты в OpenCart).

    В текущей реализации берётся первый доступный способ оплаты без приоритизации.
    Для более сложной логики можно добавить выбор по коду/настройкам.
    """
    if not d:
        return None
    return next(iter(d.keys()))


def _first_shipping_code(shipping_methods: dict[str, Any]) -> str | None:
    """По структуре {ext: {quote: {code: ...}}} возвращает код вида ext.code.

    Сейчас выбирается первый попавшийся способ доставки. При необходимости
    можно добавить приоритизацию по названию или коду модуля.
    """
    if not shipping_methods:
        return None
    for ext_key, ext_val in shipping_methods.items():
        quote = ext_val.get("quote") if isinstance(ext_val, dict) else None
        if quote and isinstance(quote, dict):
            for quote_key in quote:
                return f"{ext_key}.{quote_key}"
    return None
