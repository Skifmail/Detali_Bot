from __future__ import annotations

from collections.abc import Iterable

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from ..database.models import Order, OrderStatus, OrderSummary
from ..keyboards.kb import (
    TEXTS as KB_TEXTS,
)
from ..keyboards.kb import (
    build_account_order_detail_keyboard,
    build_account_orders_keyboard,
    build_main_menu_keyboard,
)
from ..utils import get_db_from_callback, get_db_from_message, is_admin
from .cart import _show_cart

TEXTS: dict[str, str] = {
    "profile": (
        "👤 Личный кабинет\n\n"
        "Имя: {name}\n"
        "Телефон: {phone}\n"
        "Количество заказов: {orders_count}\n"
        "Сумма всех покупок: {total_spent} ₽"
    ),
    "no_orders": "Пока у вас нет оформленных заказов.",
    "orders_header": "📦 Последние заказы:",
    "repeat_done": "✅ Заказ скопирован в корзину. Откроем её для оформления.",
    "order_detail": (
        "📦 Заказ #{display_number}\n"
        "Статус: {status}\n"
        "Оплата: {payment_info}\n"
        "Получатель: {customer_name}\n"
        "Адрес доставки: {delivery_address}\n"
        "Дата и время доставки: {desired_datetime}\n\n"
        "{items_block}\n"
        "{table_footer}"
    ),
    "order_item": "• {title} — {price} ₽ × {qty} = {line_total} ₽",
    "order_table_footer": (
        "────────────────────\n"
        "Сумма товаров: {items_total} ₽\n"
        "Доставка: {delivery} ₽\n"
        "────────────────────\n"
        "Итого: {total} ₽"
    ),
    "cancel_done": "Заказ #{display_number} отменён.",
    "cancel_forbidden": "Этот заказ нельзя отменить.",
    "cancel_not_yours": "Заказ не найден или вам недоступен.",
}

router = Router(name="account")


def _aggregate_orders(orders: Iterable[OrderSummary]) -> tuple[int, int]:
    """Считает количество заказов и общую сумму по ним.

    Args:
        orders (Iterable[OrderSummary]): Коллекция кратких заказов.

    Returns:
        tuple[int, int]: Пара (количество заказов, суммарная выручка).
    """

    count = 0
    total = 0
    for order in orders:
        count += 1
        total += order.total_amount
    return count, total


def _format_payment_info_user(order: Order) -> str:
    """Строка способа оплаты для отображения пользователю."""
    pm = order.payment_method or ""
    if pm == "cash":
        return "Наличными при получении"
    if pm == "yookassa":
        return "ЮКassa"
    return "—"


@router.callback_query(F.data == "account:back_orders")
async def handle_account_back_orders(callback: CallbackQuery) -> None:
    """Возврат к списку заказов (редактирование сообщения)."""
    await callback.answer()
    if callback.message is None or callback.from_user is None:
        return

    db = get_db_from_callback(callback)
    from_user = callback.from_user
    current_user = db.get_or_create_user(
        tg_id=from_user.id,
        first_name=from_user.first_name,
        last_name=from_user.last_name,
    )
    orders = list(db.list_orders_for_user(user_id=current_user.id))
    if not orders:
        await callback.message.edit_text(TEXTS["no_orders"])
        return
    try:
        await callback.message.edit_text(
            text=TEXTS["orders_header"],
            reply_markup=build_account_orders_keyboard(orders=orders),
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            # Логируем и пробрасываем дальше для отладки.
            from loguru import logger

            logger.debug("Ошибка редактирования списка заказов аккаунта: {err}", err=e)
            raise


@router.callback_query(F.data.startswith("account:order:"))
async def handle_account_order_detail(callback: CallbackQuery) -> None:
    """Показывает детали заказа и кнопки «Повторить» / «Отменить»."""
    await callback.answer()
    if callback.message is None or callback.from_user is None:
        return

    _, _, order_id_str = callback.data.split(":", maxsplit=2)
    order_id = int(order_id_str)

    db = get_db_from_callback(callback)
    from_user = callback.from_user
    current_user = db.get_or_create_user(
        tg_id=from_user.id,
        first_name=from_user.first_name,
        last_name=from_user.last_name,
    )
    order: Order | None = db.get_order(order_id=order_id)
    if order is None or order.user_id != current_user.id:
        await callback.answer("Заказ не найден.", show_alert=True)
        return

    lines = [
        TEXTS["order_item"].format(
            title=item.product.title,
            price=item.unit_price,
            qty=item.quantity,
            line_total=item.unit_price * item.quantity,
        )
        for item in order.items
    ]
    items_total = sum(item.unit_price * item.quantity for item in order.items)
    items_block = "\n".join(lines) if lines else "—"
    table_footer = TEXTS["order_table_footer"].format(
        items_total=items_total,
        delivery=order.delivery_cost,
        total=order.total_amount,
    )
    payment_info = _format_payment_info_user(order)
    desired_datetime = (order.desired_delivery_datetime or "").strip() or "—"
    text = TEXTS["order_detail"].format(
        display_number=order.display_order_number,
        status=order.status.human_readable,
        payment_info=payment_info,
        customer_name=(order.customer_name or "").strip() or "—",
        delivery_address=(order.delivery_address or "").strip() or "—",
        desired_datetime=desired_datetime,
        items_block=items_block,
        table_footer=table_footer,
    )
    can_cancel = order.status in (OrderStatus.NEW, OrderStatus.AWAITING_PAYMENT)
    try:
        await callback.message.edit_text(
            text=text,
            reply_markup=build_account_order_detail_keyboard(
                order_id=order.id,
                can_cancel=can_cancel,
            ),
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            from loguru import logger

            logger.debug("Ошибка редактирования деталей заказа в аккаунте: {err}", err=e)
            raise


@router.callback_query(F.data.startswith("account:cancel:"))
async def handle_account_cancel_order(callback: CallbackQuery) -> None:
    """Отмена заказа пользователем (только для статусов Новый / Ожидает оплаты)."""
    await callback.answer()
    if callback.message is None or callback.from_user is None:
        return

    _, _, order_id_str = callback.data.split(":", maxsplit=2)
    order_id = int(order_id_str)

    db = get_db_from_callback(callback)
    from_user = callback.from_user
    current_user = db.get_or_create_user(
        tg_id=from_user.id,
        first_name=from_user.first_name,
        last_name=from_user.last_name,
    )
    order: Order | None = db.get_order(order_id=order_id)
    if order is None or order.user_id != current_user.id:
        await callback.answer(TEXTS["cancel_not_yours"], show_alert=True)
        return
    if order.status not in (OrderStatus.NEW, OrderStatus.AWAITING_PAYMENT):
        await callback.answer(TEXTS["cancel_forbidden"], show_alert=True)
        return

    updated = db.update_order_status(
        order_id=order_id,
        new_status=OrderStatus.CANCELLED,
    )
    if updated is None:
        await callback.answer("Не удалось отменить заказ.", show_alert=True)
        return

    from .admin import notify_admins_order_cancelled_by_user

    await notify_admins_order_cancelled_by_user(bot=callback.bot, order=updated)

    try:
        await callback.message.edit_text(
            text=TEXTS["cancel_done"].format(display_number=updated.display_order_number),
            reply_markup=build_account_order_detail_keyboard(
                order_id=updated.id,
                can_cancel=False,
            ),
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            from loguru import logger

            logger.debug("Ошибка редактирования отменённого заказа в аккаунте: {err}", err=e)
            raise


@router.message(Command("account"))
@router.message(F.text == KB_TEXTS["menu_account"])
async def handle_account_entry(message: Message) -> None:
    """Отображает личный кабинет пользователя.

    Администраторам кабинет недоступен — показывается главное меню.
    """
    from_user = message.from_user
    if from_user is not None and is_admin(from_user.id, message.bot):
        await message.answer(
            "Личный кабинет доступен только покупателям. Используйте админ-панель.",
            reply_markup=build_main_menu_keyboard(is_admin=True),
        )
        return

    db = get_db_from_message(message)
    if from_user is None:
        return

    current_user = db.get_or_create_user(
        tg_id=from_user.id,
        first_name=from_user.first_name,
        last_name=from_user.last_name,
    )
    orders = list(db.list_orders_for_user(user_id=current_user.id))
    orders_count, total_spent = _aggregate_orders(orders)

    full_name_parts = [part for part in [current_user.first_name, current_user.last_name] if part]
    full_name = " ".join(full_name_parts) if full_name_parts else "Не указано"

    profile_text = TEXTS["profile"].format(
        name=full_name,
        phone=current_user.phone or "Не указан",
        orders_count=orders_count,
        total_spent=total_spent,
    )
    if not orders:
        await message.answer(profile_text + "\n\n" + TEXTS["no_orders"])
        return

    await message.answer(
        profile_text + "\n\n" + TEXTS["orders_header"],
        reply_markup=build_account_orders_keyboard(orders=orders),
    )


@router.callback_query(F.data.startswith("account:repeat:"))
async def handle_repeat_order(callback: CallbackQuery) -> None:
    """Наполняет корзину товарами из выбранного заказа и открывает корзину.

    Args:
        callback (CallbackQuery): Callback-запрос пользователя.

    Returns:
        None: Ничего не возвращает.
    """
    await callback.answer()
    if callback.message is None or callback.from_user is None:
        return

    db = get_db_from_callback(callback)
    from_user = callback.from_user
    current_user = db.get_or_create_user(
        tg_id=from_user.id,
        first_name=from_user.first_name,
        last_name=from_user.last_name,
    )

    _, _, order_id_str = callback.data.split(":", maxsplit=2)
    order_id = int(order_id_str)
    order: Order | None = db.get_order(order_id=order_id)
    if order is None:
        await callback.message.answer("Не удалось найти выбранный заказ.")
        return

    db.clear_cart(user_id=current_user.id)
    for item in order.items:
        db.add_to_cart(
            user_id=current_user.id,
            product_id=item.product_id,
            delta=item.quantity,
        )

    try:
        await callback.message.edit_text(TEXTS["repeat_done"], reply_markup=None)
    except TelegramBadRequest:
        await callback.message.answer(TEXTS["repeat_done"])
    await _show_cart(callback.message, from_user=callback.from_user)
