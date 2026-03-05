"""Создание заказа в OpenCart через API после подтверждения в боте."""

from __future__ import annotations

from typing import Any

from loguru import logger

from bot.core.opencart_client import OpenCartAPIError, OpenCartClient
from bot.core.opencart_config import get_opencart_config
from bot.database.models import Order


async def add_payment_confirmation_to_opencart(
    opencart_order_id: int,
    payment_comment: str,
) -> None:
    """Добавляет в историю заказа OpenCart запись о подтверждении оплаты.

    Если API не поддерживает api/order/history, ошибка логируется и не пробрасывается.
    Использует OPENCART_ORDER_STATUS_PAID_ID (или OPENCART_ORDER_STATUS_ID) для статуса записи.

    Args:
        opencart_order_id: ID заказа в OpenCart.
        payment_comment: Текст записи (например «Платеж номер … подтвержден»).
    """
    try:
        config = get_opencart_config()
    except RuntimeError:
        return
    status_id = getattr(config, "order_status_paid_id", config.order_status_id)
    async with OpenCartClient(config) as client:
        try:
            await client.login()
            await client.add_order_history(
                order_id=opencart_order_id,
                order_status_id=status_id,
                comment=payment_comment,
                notify=False,
            )
            logger.info(
                "В OpenCart заказ order_id={} добавлена запись в историю: оплата подтверждена",
                opencart_order_id,
            )
        except OpenCartAPIError as e:
            logger.warning(
                "Не удалось добавить историю заказа в OpenCart (order_id={}): {}",
                opencart_order_id,
                e,
            )
        except Exception as e:  # noqa: BLE001
            logger.debug(
                "OpenCart api/order/history недоступен или ошибка: {}",
                e,
            )


DEFAULT_CUSTOMER_FIRST_NAME = "Клиент"


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
    if len(city) < 2:
        city = "Не указан"
    address_1 = (order.delivery_address or "").strip() or "Адрес не указан"
    if len(address_1) < 3:
        address_1 = "Адрес не указан"
    postcode = ""  # В заказе бота индекса нет; не подставляем 000000, чтобы не было «Коломна 00000»
    # OpenCart требует email клиента. Используем строго email из заказа бота.
    # Если он пустой, заказ в OpenCart не создаём и логируем проблему — это баг в цепочке сбора данных.
    email = (order.email or "").strip()
    logger.info(
        "Создание заказа в OpenCart: bot_order_id={order_id}, email_for_oc={email!r}",
        order_id=order.id,
        email=email,
    )
    if not email:
        logger.error(
            "Заказ id={} не передан в OpenCart: email пустой, хотя должен быть обязательным.",
            order.id,
        )
        return None

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
                postcode=postcode,
            )
            await client.cart_add(products_oc)
            # Адрес доставки задаём после корзины: OpenCart проверяет hasShipping() по корзине.
            await client.set_shipping_address(
                firstname=firstname,
                lastname=lastname,
                address_1=address_1,
                city=city,
                zone_id=config.default_zone_id,
                country_id=config.default_country_id,
                postcode=postcode,
            )

            payment_methods = await client.get_payment_methods()
            payment_code = _first_key(payment_methods)
            if not payment_code:
                logger.warning("OpenCart не вернул способы оплаты для заказа id={}", order.id)
                return None

            shipping_methods = await client.get_shipping_methods()
            shipping_code = _select_shipping_code(
                shipping_methods,
                delivery_city=(order.delivery_city or "").strip(),
                delivery_cost=order.delivery_cost or 0,
            )

            comment_parts: list[str] = []
            if (order.comment or "").strip():
                comment_parts.append((order.comment or "").strip())
            if order.desired_delivery_datetime and str(order.desired_delivery_datetime).strip():
                comment_parts.append(f"Дата и время доставки: {order.desired_delivery_datetime.strip()}")
            comment = "\n".join(comment_parts)

            oc_order_id = await client.add_order(
                payment_method=payment_code,
                shipping_method=shipping_code,
                comment=comment,
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


def _select_shipping_code(
    shipping_methods: dict[str, Any],
    delivery_city: str,
    delivery_cost: int,
) -> str | None:
    """Выбирает код способа доставки по городу или стоимости, чтобы заказ в OpenCart
    совпадал с выбором в боте (не «Самовывоз», если выбрана доставка по городу).

    По структуре OpenCart {ext: {quote: {key: {code, title, cost}}}} ищет совпадение
    по title (город) или cost; иначе первый не-самовывоз; иначе первый любой.

    Args:
        shipping_methods: Ответ get_shipping_methods().
        delivery_city: Город доставки из заказа бота (например «Коломна»).
        delivery_cost: Стоимость доставки в рублях (например 400).

    Returns:
        Код способа доставки (например flat.flat) или None.
    """
    if not shipping_methods:
        return None
    city_lower = (delivery_city or "").strip().lower()
    is_pickup_choice = city_lower in ("самовывоз", "pickup", "")
    candidates: list[tuple[int, str]] = (
        []
    )  # приоритет: 0=город/стоимость, 1=самовывоз при выборе самовывоза, 2=не самовывоз, 3=любой
    for ext_key, ext_val in shipping_methods.items():
        if not isinstance(ext_val, dict):
            continue
        quote = ext_val.get("quote")
        if not quote or not isinstance(quote, dict):
            continue
        for quote_key, quote_data in quote.items():
            if not isinstance(quote_data, dict):
                continue
            code = quote_data.get("code") or f"{ext_key}.{quote_key}"
            title = (quote_data.get("title") or "").strip().lower()
            cost = quote_data.get("cost")
            if cost is not None and not isinstance(cost, int | float):
                try:
                    cost = int(float(cost))
                except (TypeError, ValueError):
                    cost = 0
            elif cost is None:
                cost = 0
            is_pickup = "самовывоз" in title or quote_key == "pickup"
            if (
                is_pickup_choice
                and is_pickup
                or not is_pickup_choice
                and city_lower
                and title
                and city_lower in title
                or not is_pickup_choice
                and delivery_cost
                and cost == delivery_cost
            ):
                candidates.append((0, code))
            elif is_pickup_choice and not is_pickup:
                candidates.append((2, code))
            elif not is_pickup:
                candidates.append((1, code))
            else:
                candidates.append((3, code))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]
