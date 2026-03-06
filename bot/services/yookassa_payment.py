"""Создание платежа ЮKassa и обработка уведомлений (webhook).

Создание платежа выполняется через прямой HTTP-запрос к API (без SDK для тела запроса),
чтобы контроль формата receipt.items.amount — SDK мог искажать сериализацию.
"""

from __future__ import annotations

from base64 import b64encode
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import httpx
from loguru import logger

if TYPE_CHECKING:
    from ..database.models import Order

YOOKASSA_API_URL = "https://api.yookassa.ru/v3/payments"
# Email для чека 54-ФЗ, если у заказа не указан email клиента.
RECEIPT_EMAIL_FALLBACK = "108lav@gmail.com"


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
    """Создаёт платёж в ЮKassa по заказу (с чеком 54-ФЗ при наличии email).

    Синхронная функция (вызывать из async через asyncio.to_thread).
    Запрос к API формируется вручную, чтобы amount в receipt.items уходил строго
    в формате строки «XXX.XX» (рубли), без преобразований SDK.

    Args:
        order: Заказ из БД (с items и product).
        shop_id: Идентификатор магазина ЮKassa.
        secret_key: Секретный ключ API.
        return_url: URL возврата после оплаты (опционально).

    Returns:
        YooKassaPaymentResult с payment_id и confirmation_url или None при ошибке.
    """
    amount_value = f"{order.total_amount:.2f}"
    description = f"Заказ #{order.display_order_number}"

    payload: dict[str, Any] = {
        "amount": {"value": amount_value, "currency": "RUB"},
        "description": description,
        "capture": True,
        "confirmation": {"type": "redirect"},
        "metadata": {"order_id": str(order.id), "cms_name": "yookassa_sdk_python"},
    }
    if return_url:
        payload["confirmation"]["return_url"] = return_url

    # Чек 54-ФЗ обязателен. Если у заказа нет email — подставляем RECEIPT_EMAIL_FALLBACK.
    customer_email = (order.email or "").strip() or RECEIPT_EMAIL_FALLBACK

    receipt_items: list[dict[str, Any]] = []
    for item in order.items:
        title = (item.product.title or "Товар")[:128]
        # В 54-ФЗ для сторонней кассы amount — сумма по позиции (quantity * unit_price), строка "XXX.XX".
        line_total = Decimal(str(item.quantity * item.unit_price)).quantize(Decimal("0.01"))
        value_str = f"{line_total:.2f}"
        receipt_items.append(
            {
                "description": title,
                "quantity": float(item.quantity),
                "amount": {"value": value_str, "currency": "RUB"},
                "vat_code": 1,
                "payment_mode": "full_payment",
                "payment_subject": "commodity",
            }
        )
    payload["receipt"] = {
        "customer": {"email": customer_email},
        "items": receipt_items,
        "internet": True,
    }

    idempotence_key = f"order_{order.id}_{order.display_order_number}"
    auth = b64encode(f"{shop_id}:{secret_key}".encode()).decode("ascii")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Basic {auth}",
        "Idempotence-Key": idempotence_key,
    }

    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(YOOKASSA_API_URL, json=payload, headers=headers)
        if resp.status_code != 200:
            try:
                err = resp.json()
            except Exception:  # noqa: S110
                err = resp.text
            logger.error(
                "ЮKassa API ошибка order_id={}: status={} body={}",
                order.id,
                resp.status_code,
                err,
            )
            return None
        data = resp.json()
        pid = data.get("id")
        confirmation = data.get("confirmation") or {}
        url = confirmation.get("confirmation_url")
        if not pid or not url:
            logger.warning("ЮKassa не вернула id или confirmation_url для заказа order_id={}", order.id)
            return None
        return YooKassaPaymentResult(payment_id=pid, confirmation_url=url)
    except httpx.HTTPError as e:
        logger.exception("Ошибка запроса к ЮKassa для заказа order_id={}: {}", order.id, e)
        return None
    except Exception as e:  # noqa: BLE001
        logger.exception("Ошибка создания платежа ЮKassa для заказа order_id={}: {}", order.id, e)
        return None
