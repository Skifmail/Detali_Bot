from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

from ..database.models import OrderStatus
from ..keyboards.kb import TEXTS as KB_TEXTS
from ..keyboards.kb import build_main_menu_keyboard
from ..utils import get_db_from_message, is_admin

TEXTS: dict[str, str] = {
    "welcome_admin": (
        "🌸 Добро пожаловать в бот floraldetails.ru!\n\n"
        "Используйте меню: Каталог, Заказы, Статистика, Рассылка, Ещё."
    ),
    "welcome_user": (
        "🌸 Добро пожаловать в бот floraldetails.ru!\n\n"
        "Используйте меню ниже, чтобы посмотреть каталог, корзину и личный кабинет."
    ),
    "back_to_main": "🏠 Возвращаемся в главное меню.",
    "payment_return_paid": (
        "✅ Оплата получена!\n\n" "Заказ #{display_number} оплачен. Детали — в разделе «Личный кабинет» → «Мои заказы»."
    ),
    "payment_return_pending": (
        "⏳ Ожидаем подтверждение оплаты.\n\n"
        "Если вы только что оплатили заказ #{display_number}, статус обновится в течение минуты. "
        "Проверьте «Мои заказы» в личном кабинете."
    ),
}

router = Router(name="start")


@router.message(F.text.in_({"/start", KB_TEXTS["start_over"]}))
@router.message(F.text.startswith("/start "))
async def handle_start_and_main_menu(message: Message) -> None:
    """Обрабатывает /start, deep link возврата после оплаты (pay_ORDER_ID) и кнопку в главное меню.

    При переходе по ссылке «Вернуться на сайт» после оплаты ЮKassa пользователь попадает
    в бота с /start pay_ORDER_ID — показываем подтверждение оплаты и главное меню.

    Args:
        message: Входящее сообщение пользователя.

    Returns:
        None.
    """
    from_user = message.from_user
    is_admin_flag = bool(from_user and is_admin(from_user.id, message.bot))
    keyboard = build_main_menu_keyboard(is_admin=is_admin_flag)
    text = (message.text or "").strip()

    # Возврат после оплаты ЮKassa: /start pay_29
    if text.startswith("/start pay_"):
        try:
            order_id = int(text.split("_", 1)[1])
        except (IndexError, ValueError):
            order_id = 0
        if order_id and from_user:
            db = get_db_from_message(message)
            if db:
                current_user = db.get_or_create_user(
                    from_user.id,
                    from_user.first_name or None,
                    from_user.last_name or None,
                )
                order = db.get_order(order_id=order_id)
                if order and order.user_id == current_user.id:
                    if order.status == OrderStatus.PAID:
                        await message.answer(
                            TEXTS["payment_return_paid"].format(display_number=order.display_order_number),
                            reply_markup=keyboard,
                            parse_mode="HTML",
                        )
                    else:
                        await message.answer(
                            TEXTS["payment_return_pending"].format(display_number=order.display_order_number),
                            reply_markup=keyboard,
                            parse_mode="HTML",
                        )
                    return
        # неверный или чужой заказ — показываем главное меню
        await message.answer(TEXTS["back_to_main"], reply_markup=keyboard)
        return

    if text == "/start":
        welcome = TEXTS["welcome_admin"] if is_admin_flag else TEXTS["welcome_user"]
        await message.answer(welcome, reply_markup=keyboard)
        return

    await message.answer(
        TEXTS["back_to_main"],
        reply_markup=keyboard,
    )
