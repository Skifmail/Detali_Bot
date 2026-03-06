"""Создание платежа ЮKassa и обработка уведомлений (webhook).

Использует официальный SDK yookassa (синхронный). Создание платежа
выполняется в executor, чтобы не блокировать event loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from ..database.models import Order


@dataclass(frozen=True)
class YooKassaPaymentResult:
    """Результат создания платежа ЮKassa.

    Attributes:
        payment_id: Идентификатор платежа в ЮKassa.
        confirmation_url: URL для перехода пользователя к оплате.
    """

    payment_id: str
    confirmation_url: str


def create_payment(
    order: Order,
    *,
    shop_id: str,
    secret_key: str,
    return_url: str | None = None,
) -> YooKassaPaymentResult | None:
    """Создаёт платёж в ЮKassa по заказу.

    Синхронная функция (вызывать из async через asyncio.to_thread).

    Args:
        order: Заказ из БД (с items и product).
        shop_id: Идентификатор магазина ЮKassa.
        secret_key: Секретный ключ API.
        return_url: URL возврата после оплаты (опционально).

    Returns:
        YooKassaPaymentResult с payment_id и confirmation_url или None при ошибке.
    """
    from yookassa import Configuration, Payment

    Configuration.configure(shop_id, secret_key)

    amount_value = f"{order.total_amount:.2f}"
    description = f"Заказ #{order.display_order_number}"

    payload: dict[str, Any] = {
        "amount": {"value": amount_value, "currency": "RUB"},
        "description": description,
        "capture": True,
        "confirmation": {"type": "redirect"},
        "metadata": {"order_id": str(order.id)},
    }
    if return_url:
        payload["confirmation"]["return_url"] = return_url

    # Чек 54-ФЗ: email и позиции для отправки чека клиенту.
    customer_email = (order.email or "").strip()
    if customer_email:
        receipt_items = []
        for item in order.items:
            title = (item.product.title or "Товар")[:128]
            item_value = f"{item.unit_price * item.quantity:.2f}"
            receipt_items.append(
                {
                    "description": title,
                    "quantity": f"{item.quantity}.000",
                    "amount": {"value": item_value, "currency": "RUB"},
                    "vat_code": 1,  # Без НДС (УСН и т.п.)
                }
            )
        if receipt_items:
            payload["receipt"] = {
                "customer": {"email": customer_email},
                "items": receipt_items,
            }

    idempotence_key = f"order_{order.id}_{order.display_order_number}"

    try:
        payment_response = Payment.create(payload, idempotence_key)
        pid = payment_response.id
        confirmation = payment_response.confirmation
        if not confirmation or not getattr(confirmation, "confirmation_url", None):
            logger.warning("ЮKassa не вернула confirmation_url для заказа order_id={}", order.id)
            return None
        url = confirmation.confirmation_url
        return YooKassaPaymentResult(payment_id=pid, confirmation_url=url)
    except Exception as e:
        logger.exception(
            "Ошибка создания платежа ЮKassa для заказа order_id={}: {}",
            order.id,
            e,
        )
        return None
