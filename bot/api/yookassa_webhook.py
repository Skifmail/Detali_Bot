"""HTTP-эндпоинт для приёма webhook уведомлений ЮKassa о статусе платежа."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, Request, Response
from loguru import logger

from ..database.models import OrderStatus

# Тексты для сообщения клиенту после успешной оплаты (дублируем из payment.py)
PICKUP_ADDRESS_DISPLAY = "ул. Октябрьской революции, 215, г. Коломна"
SUCCESS_TEMPLATE = (
    "✅ Оплата прошла успешно!\n\n"
    "Номер заказа: <b>#{display_number}</b>\n"
    "Сумма: <b>{total} ₽</b>\n"
    "Дата и время доставки: {desired_datetime}\n"
    "Статус: ✅ Оплачен."
)
SUCCESS_PICKUP_APPEND = "\n\n📍 Забрать заказ можно по адресу:\n{address}"


def create_yookassa_webhook_app(bot: Any, db: Any) -> FastAPI:
    """Создаёт FastAPI-приложение для приёма webhook ЮKassa.

    Args:
        bot: Экземпляр aiogram Bot (для отправки сообщения клиенту и админам).
        db: Экземпляр Database бота.

    Returns:
        FastAPI: Приложение с маршрутом POST /webhook/yookassa.
    """
    app = FastAPI(title="YooKassa Webhook")
    app.state.bot = bot
    app.state.db = db

    @app.post("/webhook/yookassa")
    async def yookassa_webhook(request: Request) -> Response:
        """Принимает уведомление ЮKassa, отвечает 200 и обрабатывает в фоне."""
        try:
            body = await request.json()
        except Exception as e:
            logger.warning("ЮKassa webhook: невалидный JSON: {}", e)
            return Response(status_code=400)
        event = body.get("event")
        if event != "payment.succeeded":
            return Response(status_code=200)
        obj = body.get("object") or {}
        payment_id = obj.get("id")
        if not payment_id:
            return Response(status_code=200)
        # Ответить 200 сразу, обработку — в фоне.
        asyncio.create_task(_process_payment_succeeded(app.state.db, app.state.bot, payment_id))
        return Response(status_code=200)

    return app


async def _process_payment_succeeded(db: Any, bot: Any, payment_id: str) -> None:
    """Обновляет заказ после успешной оплаты, создаёт заказ в OpenCart, уведомляет админа и клиента."""
    order = db.get_order_by_external_payment_id(payment_id)
    if order is None:
        logger.warning("ЮKassa webhook: заказ не найден по payment_id={}", payment_id)
        return
    if order.status == OrderStatus.PAID:
        logger.debug("ЮKassa webhook: заказ order_id={} уже оплачен, пропуск", order.id)
        return
    updated = db.update_order_status(order.id, OrderStatus.PAID)
    if updated is None:
        return
    from ..services.opencart_order import add_payment_confirmation_to_opencart, create_order_in_opencart

    oc_order_id = await create_order_in_opencart(updated)
    if oc_order_id is not None:
        db.set_order_opencart_id(updated.id, oc_order_id)
        payment_comment = f'Платеж номер "{payment_id}" подтвержден'
        await add_payment_confirmation_to_opencart(oc_order_id, payment_comment)
    from ..handlers.admin import notify_admins_new_order

    await notify_admins_new_order(bot=bot, order=updated)
    # Сообщение клиенту
    chat_id = db.get_user_tg_id(updated.user_id)
    if chat_id is not None:
        desired_datetime = (updated.desired_delivery_datetime or "").strip() or "—"
        text = SUCCESS_TEMPLATE.format(
            display_number=updated.display_order_number,
            total=updated.total_amount,
            desired_datetime=desired_datetime,
        )
        if updated.delivery_address == "Самовывоз":
            text += SUCCESS_PICKUP_APPEND.format(address=PICKUP_ADDRESS_DISPLAY)
        try:
            await bot.send_message(chat_id=chat_id, text=text)
        except Exception as e:
            logger.warning("Не удалось отправить клиенту сообщение об оплате: {}", e)
