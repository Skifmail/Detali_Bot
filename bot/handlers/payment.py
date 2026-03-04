from __future__ import annotations

import asyncio

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message

from ..database.models import Order, OrderStatus
from ..keyboards.kb import build_payment_keyboard, build_payment_method_keyboard
from ..utils import get_db_from_callback, get_db_from_message

TEXTS: dict[str, str] = {
    "payment_choice": "💳 Выберите способ оплаты заказа #{display_number} ({total} ₽):",
    "payment_intro": "💳 Оплата заказа #{display_number}\n\n"
    "Сумма к оплате: <b>{total} ₽</b>\n\n"
    "Для демо используется тестовый сценарий оплаты без реального списания средств.",
    "processing": "⏳ Обрабатываем оплату…",
    "success": "✅ Оплата прошла успешно!\n\n"
    "Номер заказа: <b>#{display_number}</b>\n"
    "Сумма: <b>{total} ₽</b>\n"
    "Статус: ✅ Оплачен (для демо).",
    "cash_success": (
        "✅ Заказ принят.\n\n"
        "Номер заказа: <b>#{display_number}</b>\n"
        "Сумма к оплате: <b>{total} ₽</b>\n\n"
        "Оплата наличными при получении."
    ),
    "order_created_prefix": "✅ Заказ создан.\n\n{body}",
}

router = Router(name="payment")


async def show_payment_method_choice(
    message: Message,
    order_id: int,
    *,
    edit: bool = False,
) -> None:
    """Показывает выбор способа оплаты (ЮКassa или наличные) для заказа.

    Args:
        message (Message): Сообщение, в чат которого отправить или которое отредактировать.
        order_id (int): Идентификатор заказа.
        edit (bool): Если True, редактировать сообщение вместо отправки нового.

    Returns:
        None: Ничего не возвращает.
    """

    db = get_db_from_message(message)
    order: Order | None = db.get_order(order_id=order_id)
    if order is None:
        await message.answer("Не удалось найти заказ для оплаты.")
        return

    text = TEXTS["payment_choice"].format(
        display_number=order.display_order_number,
        total=order.total_amount,
    )
    if edit:
        full_text = TEXTS["order_created_prefix"].format(body=text)
        try:
            await message.edit_text(
                text=full_text,
                reply_markup=build_payment_method_keyboard(order_id=order.id),
                parse_mode="HTML",
            )
        except TelegramBadRequest:
            await message.answer(
                text=full_text,
                reply_markup=build_payment_method_keyboard(order_id=order.id),
                parse_mode="HTML",
            )
    else:
        await message.answer(
            text,
            reply_markup=build_payment_method_keyboard(order_id=order.id),
            parse_mode="HTML",
        )


async def _start_mock_payment(message: Message, order_id: int) -> None:
    """Запускает демонстрационный сценарий оплаты ЮКassa для указанного заказа.

    Args:
        message (Message): Сообщение, в который отправляется информация об оплате.
        order_id (int): Идентификатор заказа.

    Returns:
        None: Ничего не возвращает.
    """

    db = get_db_from_message(message)
    order: Order | None = db.get_order(order_id=order_id)
    if order is None:
        await message.answer("Не удалось найти заказ для оплаты.")
        return

    intro_text = TEXTS["payment_intro"].format(
        display_number=order.display_order_number,
        total=order.total_amount,
    )
    await message.answer(
        intro_text,
        reply_markup=build_payment_keyboard(
            amount=order.total_amount,
            order_id=order.id,
        ),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("payment:method:yookassa:"))
async def handle_payment_method_yookassa(callback: CallbackQuery) -> None:
    """Обрабатывает выбор оплаты через ЮКassa: ставит статус «ожидает оплаты», уведомляет админов, запускает оплату.

    Args:
        callback (CallbackQuery): Callback-запрос пользователя.

    Returns:
        None: Ничего не возвращает.
    """

    await callback.answer()
    if callback.message is None:
        return

    _, _, _, order_id_str = callback.data.split(":", maxsplit=3)
    order_id = int(order_id_str)

    db = get_db_from_callback(callback)
    updated = db.update_order_payment_method(
        order_id=order_id,
        payment_method="yookassa",
        new_status=OrderStatus.AWAITING_PAYMENT,
    )
    if updated is None:
        await callback.message.answer("Не удалось обновить заказ.")
        return

    from .admin import notify_admins_new_order

    await notify_admins_new_order(bot=callback.bot, order=updated)
    # Для реальной интеграции с ЮKassa вместо мокового сценария нужно
    # вызывать создание платежа в ЮKassa API и переадресовывать пользователя.
    await _start_mock_payment(message=callback.message, order_id=order_id)


@router.callback_query(F.data.startswith("payment:method:cash:"))
async def handle_payment_method_cash(callback: CallbackQuery) -> None:
    """Обрабатывает выбор оплаты наличными: фиксирует способ оплаты, уведомляет админов.

    Args:
        callback (CallbackQuery): Callback-запрос пользователя.

    Returns:
        None: Ничего не возвращает.
    """

    await callback.answer()
    if callback.message is None:
        return

    _, _, _, order_id_str = callback.data.split(":", maxsplit=3)
    order_id = int(order_id_str)

    db = get_db_from_callback(callback)
    updated = db.update_order_payment_method(
        order_id=order_id,
        payment_method="cash",
    )
    if updated is None:
        await callback.message.answer("Не удалось обновить заказ.")
        return

    from .admin import notify_admins_new_order

    await notify_admins_new_order(bot=callback.bot, order=updated)

    text = TEXTS["cash_success"].format(
        display_number=updated.display_order_number,
        total=updated.total_amount,
    )
    await callback.message.answer(text, parse_mode="HTML")


@router.callback_query(F.data.startswith("payment:pay:"))
async def handle_mock_payment(callback: CallbackQuery) -> None:
    """Обрабатывает нажатие на кнопку оплаты и имитирует платёж.

    Args:
        callback (CallbackQuery): Callback-запрос пользователя.

    Returns:
        None: Ничего не возвращает.
    """

    await callback.answer()
    if callback.message is None:
        return

    _, _, order_id_str = callback.data.split(":", maxsplit=2)
    order_id = int(order_id_str)

    db = get_db_from_callback(callback)
    order_before: Order | None = db.get_order(order_id=order_id)
    if order_before is None:
        await callback.message.answer("Не удалось найти заказ для оплаты.")
        return

    await callback.message.answer(TEXTS["processing"])

    # Если способ оплаты не был выбран ранее (редкий кейс), фиксируем «yookassa» для отчётов.
    if not (order_before.payment_method or "").strip():
        db.update_order_payment_method(order_id=order_id, payment_method="yookassa")

    # TODO: заменить на реальный ЮKassa Payment.create; в чеке (receipt.customer.email)
    # передать order.email — чеки будут уходить на email покупателя.
    await asyncio.sleep(2)

    updated = db.update_order_status(order_id=order_id, new_status=OrderStatus.PAID)
    if updated is None:
        await callback.message.answer("Не удалось обновить статус заказа после оплаты.")
        return

    if updated.opencart_order_id is not None:
        from bot.services.opencart_order import add_payment_confirmation_to_opencart

        payment_comment = f'Платеж номер "{updated.external_payment_id or updated.display_order_number}" подтвержден'
        await add_payment_confirmation_to_opencart(updated.opencart_order_id, payment_comment)

    from .admin import update_admins_order_notification

    await update_admins_order_notification(bot=callback.bot, order_id=order_id)

    success_text = TEXTS["success"].format(
        display_number=updated.display_order_number,
        total=updated.total_amount,
    )
    await callback.message.answer(success_text)
