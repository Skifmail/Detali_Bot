from __future__ import annotations

import re
from datetime import date as date_type
from datetime import datetime as dt
from datetime import time as time_cls

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger

from ..database.db import Database
from ..database.models import Order
from ..keyboards.kb import (
    build_delivery_calendar_keyboard,
    build_delivery_choice_keyboard,
    build_delivery_time_keyboard,
    build_email_choice_keyboard,
    build_order_confirmation_keyboard,
    build_recipient_choice_keyboard,
    build_saved_recipients_keyboard,
)
from ..utils import get_db_from_callback, get_db_from_message, normalize_phone

# Варианты доставки: (slug, название, стоимость ₽, сообщение о сроках)
DELIVERY_OPTIONS: list[tuple[str, str, int, str]] = [
    (
        "kolomna",
        "Коломна",
        400,
        "⏱ Оперативность доставки по Коломне — до 2 часов.",
    ),
    (
        "voskresensk",
        "Воскресенск",
        1500,
        "⏱ Оперативность доставки в города Воскресенск, Егорьевск, Луховицы — от 2 часов.",
    ),
    (
        "egorevsk",
        "Егорьевск",
        1500,
        "⏱ Оперативность доставки в города Воскресенск, Егорьевск, Луховицы — от 2 часов.",
    ),
    (
        "lukhovitsy",
        "Луховицы",
        1200,
        "⏱ Оперативность доставки в города Воскресенск, Егорьевск, Луховицы — от 2 часов.",
    ),
    (
        "moscow",
        "Москва",
        2000,
        "⏱ Доставка по Москве осуществляется от 24 часов, убедительная просьба делать заказ заранее!\n\n"
        "*При большой загруженности московских дорог (8 баллов и выше по данным «Яндекс Пробки») "
        "время доставки может увеличиться. Наш курьер Вас обязательно проинформирует об этом!*",
    ),
    (
        "pickup",
        "Самовывоз",
        0,
        "🏪 Вы можете забрать свой заказ сами из нашего магазина. " "Предварительно оформите заказ на нашем сайте.",
    ),
]

TEXTS: dict[str, str] = {
    "ask_recipient_choice": "🧾 Оформление заказа\n\nКому доставить заказ?",
    "no_saved_recipients": "📋 У вас пока нет сохранённых получателей. Выберите «Я получатель» или «Новый получатель».",
    "ask_name": "🧾 Введите имя и фамилию получателя:",
    "ask_phone": "📞 Укажите номер телефона получателя в формате +7XXXXXXXXXX или 8XXXXXXXXXX "
    "или нажмите кнопку ниже, чтобы поделиться контактом:",
    "ask_phone_self": "📞 Укажите ваш номер телефона в формате +7XXXXXXXXXX или 8XXXXXXXXXX "
    "или нажмите кнопку ниже, чтобы поделиться контактом:",
    "ask_name_self": "🧾 Оформление заказа\n\nВведите, пожалуйста, ваше имя и фамилию:",
    "ask_address": "📍 Укажите адрес доставки (улица, дом, квартира):",
    "confirm_address": (
        "📍 Ранее вы указывали адрес доставки:\n\n"
        "Город: {city}\n"
        "{address}\n\n"
        "Оставить его для этого заказа или ввести новый?"
    ),
    "ask_delivery_datetime": "📅 Выберите дату доставки:",
    "ask_delivery_time": "🕐 Выберите желаемое время доставки (с 9:00 до 19:00) или введите вручную:",
    "ask_delivery_time_manual": (
        "✏️ Введите время доставки в формате ЧЧ:ММ\n" "Например: 14:30. Доступно с 9:00 до 19:00."
    ),
    "invalid_delivery_time": "⚠️ Неверный формат или время вне интервала 9:00–19:00. Введите ЧЧ:ММ, например 14:30.",
    "delivery_time_past": "⚠️ Выбранное время уже прошло. Выберите другое время или другую дату.",
    "delivery_today_ended": "На сегодня время доставки закончилось (приём до 19:00). Выберите другую дату.",
    "invalid_delivery_datetime": (
        "⚠️ Неверный формат. Введите дату и время в формате ДД.ММ.ГГГГ ЧЧ:ММ\n" "Например: 15.03.2025 14:00"
    ),
    "date_past_alert": "Нельзя выбрать прошедшую дату. Выберите сегодня или любой следующий день.",
    "ask_comment": "✏️ Добавьте комментарий к заказу (или отправьте «-», чтобы пропустить):",
    "ask_email": "📧 Укажите email для получения чека по оплате (например example@mail.ru):",
    "ask_email_choice": "📧 Выберите email для чека или введите другой:",
    "email_enter_new": "✏️ Ввести другой email",
    "invalid_email": "⚠️ Укажите корректный email (например name@mail.ru).",
    "invalid_phone": "⚠️ Похоже, номер телефона указан в неверном формате.\n"
    "Пожалуйста, введите номер в формате +7XXXXXXXXXX или 8XXXXXXXXXX.",
    "ask_delivery_choice": "🚚 Выберите город доставки или самовывоз:",
    "summary": (
        "🧾 Итоговый заказ\n\n"
        "{items}\n\n"
        "Доставка: {delivery_line}\n"
        "Стоимость доставки: {delivery_cost} ₽\n"
        "Желаемые дата и время: {desired_datetime}\n"
        "Получатель: {name}\n"
        "Телефон: {phone}\n"
        "Email: {email}\n"
        "Комментарий: {comment}\n\n"
        "Сумма к оплате: <b>{total} ₽</b>"
    ),
    "summary_item": "• {title} — {price} ₽ × {qty} = {line_total} ₽",
    "empty_cart": "🛒 Ваша корзина пуста, оформить заказ нельзя.\n" "Добавьте товары из каталога.",
    "cart_no_opencart_products": (
        "🛒 В корзине нет товаров из каталога сайта — заказ не попадёт в магазин. "
        "Удалите устаревшие позиции и добавьте товары из каталога."
    ),
    "cart_cleaned_no_valid": (
        "🛒 В корзине не было товаров из каталога сайта. Устаревшие позиции удалены. "
        "Добавьте товары из каталога заново."
    ),
    "cart_removed_invalid": "Из корзины убраны товары, которых нет в каталоге сайта.",
    "cancelled": "✖️ Оформление заказа отменено.",
    "created": "✅ Заказ создан. Переходим к оплате…",
}

PHONE_REGEX = re.compile(r"^(?:\+7|8)\d{10}$")
# Простая проверка email (буквы/цифры/точка/дефис/@, домен с точкой)
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
# ДД.ММ.ГГГГ ЧЧ:ММ или ДД.ММ.ГГГГ ЧЧ:ММ (с возможными пробелами)
DELIVERY_DATETIME_REGEX = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\s+(\d{1,2}):(\d{2})\s*$")
PICKUP_ADDRESS = "Самовывоз"

router = Router(name="order")


class OrderForm(StatesGroup):
    """Состояния FSM для пошагового оформления заказа."""

    recipient_choice = State()
    name = State()  # имя заказчика при «Я получатель» и нет в профиле
    phone = State()  # телефон заказчика в том же случае
    address_confirm = State()
    address = State()
    recipient_new_name = State()
    recipient_new_phone = State()
    delivery_choice = State()
    desired_datetime = State()
    comment = State()
    email_choice = State()
    email = State()
    confirm = State()


def _build_phone_keyboard() -> ReplyKeyboardMarkup:
    """Создаёт клавиатуру для отправки контакта пользователя.

    Returns:
        ReplyKeyboardMarkup: Клавиатура с кнопкой запроса контакта.
    """

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="📱 Отправить мой номер",
                    request_contact=True,
                ),
            ],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def _build_delivery_options_for_kb() -> list[tuple[str, str]]:
    """Строит пары (slug, текст) для клавиатуры выбора доставки."""

    return [(slug, f"{label} — {cost} ₽" if cost else label) for slug, label, cost, _ in DELIVERY_OPTIONS]


async def _build_and_show_summary(
    *,
    target: Message | CallbackQuery,
    state: FSMContext,
    db: Database,
    user_db_id: int,
    email: str,
) -> None:
    """Формирует итоговую сводку заказа и показывает её пользователю.

    Args:
        target (Message | CallbackQuery): Куда отвечать/что редактировать.
        state (FSMContext): Контекст FSM.
        db (Database): Экземпляр базы данных.
        user_db_id (int): Идентификатор пользователя в БД.
        email (str): Выбранный email для чека.
    """

    # Читаем данные оформления
    data = await state.get_data()
    name = str(data.get("name") or "")
    phone = str(data.get("phone") or "")
    address = str(data.get("address") or "")
    delivery_city = str(data.get("delivery_city", "") or "")
    delivery_cost = int(data.get("delivery_cost", 0))
    comment = data.get("comment")

    # Корзина
    cart_items = db.get_cart(user_id=user_db_id)
    if not cart_items:
        msg = target if isinstance(target, Message) else target.message
        if msg is not None:
            await msg.answer(TEXTS["empty_cart"])
        await state.clear()
        return

    cart_total = sum(item.product.price * item.quantity for item in cart_items)
    total = cart_total + delivery_cost
    items_lines: list[str] = []
    for item in cart_items:
        line_total = item.product.price * item.quantity
        items_lines.append(
            TEXTS["summary_item"].format(
                title=item.product.title,
                price=item.product.price,
                qty=item.quantity,
                line_total=line_total,
            ),
        )

    city_display = delivery_city.strip() if delivery_city else None
    if not city_display:
        options_for_kb = _build_delivery_options_for_kb()
        msg = target if isinstance(target, Message) else target.message
        if msg is not None:
            await msg.answer(
                TEXTS["ask_delivery_choice"],
                reply_markup=build_delivery_choice_keyboard(options_for_kb),
            )
        await state.set_state(OrderForm.delivery_choice)
        return

    delivery_line = city_display if address == PICKUP_ADDRESS else f"{city_display} — {address}"
    desired_datetime = str(data.get("desired_delivery_datetime", "—"))
    summary_text = TEXTS["summary"].format(
        items="\n".join(items_lines),
        delivery_line=delivery_line,
        delivery_cost=delivery_cost,
        desired_datetime=desired_datetime,
        name=name,
        phone=phone,
        email=email,
        comment=comment or "—",
        total=total,
    )

    # Явно сохраняем email в state перед показом кнопки «Подтвердить», чтобы при подтверждении он точно был в data.
    await state.update_data(email=email)
    await state.set_state(OrderForm.confirm)
    kb = build_order_confirmation_keyboard()

    if isinstance(target, Message):
        await target.answer(summary_text, reply_markup=kb, parse_mode="HTML")
    else:
        msg = target.message
        if msg is None:
            return
        try:
            await msg.edit_text(summary_text, reply_markup=kb, parse_mode="HTML")
        except TelegramBadRequest:
            await msg.answer(summary_text, reply_markup=kb, parse_mode="HTML")


def _parse_delivery_datetime(text: str) -> str | None:
    """Парсит строку даты/времени в формате ДД.ММ.ГГГГ ЧЧ:ММ.

    Args:
        text (str): Строка от пользователя.

    Returns:
        Optional[str]: Нормализованная строка «ДД.ММ.ГГГГ ЧЧ:ММ» или None при ошибке.
    """
    text = (text or "").strip()
    m = DELIVERY_DATETIME_REGEX.match(text)
    if not m:
        return None
    day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    hour, minute = int(m.group(4)), int(m.group(5))
    try:
        _ = dt(year=year, month=month, day=day, hour=hour, minute=minute)
    except ValueError:
        return None
    return f"{day:02d}.{month:02d}.{year:04d} {hour:02d}:{minute:02d}"


async def _ask_delivery_datetime(message: Message, state: FSMContext) -> None:
    """Запрашивает желаемую дату доставки календарём (прошедшие даты недоступны).

    Args:
        message (Message): Сообщение для ответа.
        state (FSMContext): Контекст FSM.

    Returns:
        None: Ничего не возвращает.
    """
    today = date_type.today()
    await message.answer(
        TEXTS["ask_delivery_datetime"],
        reply_markup=build_delivery_calendar_keyboard(year=today.year, month=today.month),
    )
    await state.set_state(OrderForm.desired_datetime)


async def _ask_delivery_choice(message: Message, state: FSMContext) -> None:
    """Запрашивает выбор города доставки или самовывоза.

    Args:
        message (Message): Сообщение для ответа.
        state (FSMContext): Контекст FSM.

    Returns:
        None: Ничего не возвращает.
    """
    options_for_kb = _build_delivery_options_for_kb()
    await message.answer(
        TEXTS["ask_delivery_choice"],
        reply_markup=build_delivery_choice_keyboard(options_for_kb),
    )
    await state.set_state(OrderForm.delivery_choice)


async def _ask_address(
    message: Message | None,
    state: FSMContext,
    *,
    bot: Bot | None = None,
    chat_id: int | None = None,
) -> None:
    """Запускает шаг выбора или ввода адреса доставки (после выбора города).

    Использует suggested_address (адрес сохранённого получателя) или last_address
    (последний адрес из заказов) для предложения «оставить этот адрес».

    Args:
        message (Message | None): Сообщение для ответа; если None, используются bot и chat_id.
        state (FSMContext): Контекст FSM.
        bot (Bot | None): Бот для отправки (если message не передан).
        chat_id (int | None): ID чата (если message не передан).

    Returns:
        None: Ничего не возвращает.
    """

    async def _send(text: str, reply_markup: object | None = None) -> None:
        if message is not None:
            await message.answer(text, reply_markup=reply_markup)
        elif bot is not None and chat_id is not None:
            await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)

    data = await state.get_data()
    suggested_address = data.get("suggested_address") or data.get("last_address")
    delivery_city = data.get("delivery_city") or ""

    # Не предлагать «Самовывоз» как адрес при доставке в город — это адрес из прошлого заказа с самовывозом.
    if suggested_address and suggested_address.strip() != PICKUP_ADDRESS:
        builder = InlineKeyboardBuilder()
        builder.button(
            text="✅ Оставить этот адрес",
            callback_data="order:addr_use_saved",
        )
        builder.button(
            text="✏️ Ввести другой адрес",
            callback_data="order:addr_change",
        )
        builder.button(
            text="🚚 Изменить город доставки",
            callback_data="order:addr_change_city",
        )
        builder.adjust(1)
        city_display = delivery_city or "Город не выбран"
        await _send(
            TEXTS["confirm_address"].format(
                city=city_display,
                address=suggested_address,
            ),
            reply_markup=builder.as_markup(),
        )
        await state.set_state(OrderForm.address_confirm)
        return

    await _send(TEXTS["ask_address"], reply_markup=ReplyKeyboardRemove())
    await state.set_state(OrderForm.address)


def _format_cart_summary(order: Order) -> str:
    """Формирует текст сводки заказа на основе его позиций.

    Args:
        order (Order): Объект заказа с позициями.

    Returns:
        str: Готовый текст сводки.
    """

    lines: list[str] = []
    for item in order.items:
        line_total = item.unit_price * item.quantity
        lines.append(
            TEXTS["summary_item"].format(
                title=item.product.title,
                price=item.unit_price,
                qty=item.quantity,
                line_total=line_total,
            ),
        )
    return "\n".join(lines)


@router.callback_query(F.data == "cart:checkout")
async def handle_checkout_start(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Запускает процесс оформления заказа: удаляет сообщения корзины и показывает первый шаг — выбор получателя.

    Администраторам оформление заказа недоступно.
    """
    admin_ids: set[int] = getattr(callback.bot, "admin_ids", set())
    if callback.from_user and callback.from_user.id in admin_ids:
        await callback.answer(
            "Оформление заказов доступно только покупателям.",
            show_alert=True,
        )
        return
    await callback.answer()
    if callback.message is None:
        return

    chat_id = callback.message.chat.id
    data = await state.get_data()
    cart_message_ids: list[int] = data.get("cart_message_ids") or []
    cart_chat_id = data.get("cart_chat_id") or chat_id
    for mid in cart_message_ids:
        try:
            await callback.bot.delete_message(chat_id=cart_chat_id, message_id=mid)
        except TelegramBadRequest:
            pass
    if cart_message_ids:
        await state.update_data(cart_message_ids=[], cart_chat_id=None)

    db = get_db_from_callback(callback)
    from_user = callback.from_user
    current_user = db.get_or_create_user(
        tg_id=from_user.id,
        first_name=from_user.first_name,
        last_name=from_user.last_name,
    )
    cart_items = db.get_cart(user_id=current_user.id)
    if not cart_items:
        await callback.bot.send_message(
            chat_id=chat_id,
            text=TEXTS["empty_cart"],
        )
        return

    # Подробное логирование для отладки: что в корзине и есть ли opencart_product_id
    logger.info(
        "Оформление заказа: user_id={user_id}, позиций в корзине={count}",
        user_id=current_user.id,
        count=len(cart_items),
    )
    for idx, item in enumerate(cart_items):
        logger.info(
            "  Корзина[{idx}]: product_id={product_id}, title={title!r}, "
            "opencart_product_id={oc_id}, quantity={qty}",
            idx=idx,
            product_id=item.product.id,
            title=(item.product.title or "")[:60],
            oc_id=item.product.opencart_product_id,
            qty=item.quantity,
        )

    # Только товары с привязкой к OpenCart попадут в заказ на сайте
    valid_items = [i for i in cart_items if i.product.opencart_product_id is not None]
    if not valid_items:
        logger.warning(
            "Оформление заказа отклонено: в корзине нет ни одной позиции с opencart_product_id "
            "(user_id={user_id}, всего позиций={total})",
            user_id=current_user.id,
            total=len(cart_items),
        )
        # Удаляем устаревшие позиции из корзины, чтобы пользователь мог добавить товары заново
        for item in cart_items:
            if item.product.opencart_product_id is None:
                db.add_to_cart(
                    user_id=current_user.id,
                    product_id=item.product.id,
                    delta=-item.quantity,
                )
        await callback.bot.send_message(
            chat_id=chat_id,
            text=TEXTS["cart_cleaned_no_valid"],
        )
        return

    # Удаляем из корзины позиции без opencart_product_id, чтобы заказ создался в OpenCart
    if len(valid_items) < len(cart_items):
        for item in cart_items:
            if item.product.opencart_product_id is None:
                db.add_to_cart(
                    user_id=current_user.id,
                    product_id=item.product.id,
                    delta=-item.quantity,
                )
        await callback.bot.send_message(
            chat_id=chat_id,
            text=TEXTS["cart_removed_invalid"],
        )

    last_orders = db.list_orders_for_user(user_id=current_user.id, limit=1)
    last_address: str | None = None
    if last_orders:
        last_full_order = db.get_order(order_id=last_orders[0].id)
        if last_full_order and last_full_order.delivery_address != PICKUP_ADDRESS:
            last_address = last_full_order.delivery_address

    saved = db.list_saved_recipients(user_id=current_user.id)
    await state.update_data(
        user_db_id=current_user.id,
        last_address=last_address,
        suggested_address=last_address,
    )
    await state.set_state(OrderForm.recipient_choice)
    await callback.bot.send_message(
        chat_id=chat_id,
        text=TEXTS["ask_recipient_choice"],
        reply_markup=build_recipient_choice_keyboard(has_saved_recipients=len(saved) > 0),
    )


@router.callback_query(
    OrderForm.recipient_choice,
    F.data == "order:recipient_self",
)
async def handle_recipient_self(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Получатель — заказчик: подставляем данные из профиля или запрашиваем имя/телефон.

    Args:
        callback (CallbackQuery): Callback-запрос пользователя.
        state (FSMContext): Контекст FSM.

    Returns:
        None: Ничего не возвращает.
    """

    await callback.answer()
    if callback.message is None:
        return

    db = get_db_from_callback(callback)
    data = await state.get_data()
    user_db_id = int(data["user_db_id"])
    current_user = db.get_user(user_id=user_db_id)
    if current_user is None:
        await callback.answer(TEXTS["empty_cart"], show_alert=True)
        await state.clear()
        return

    await state.update_data(recipient_self=True)
    has_name = bool(current_user.first_name or current_user.last_name)
    has_phone = bool(current_user.phone)
    chat_id = callback.message.chat.id
    bot = callback.bot

    if has_name and has_phone:
        full_name_parts = [p for p in [current_user.first_name, current_user.last_name] if p]
        full_name = " ".join(full_name_parts)
        await state.update_data(name=full_name, phone=current_user.phone or "")
        options_for_kb = _build_delivery_options_for_kb()
        try:
            await callback.message.edit_text(
                TEXTS["ask_delivery_choice"],
                reply_markup=build_delivery_choice_keyboard(options_for_kb),
            )
        except TelegramBadRequest:
            await callback.message.answer(
                TEXTS["ask_delivery_choice"],
                reply_markup=build_delivery_choice_keyboard(options_for_kb),
            )
        await state.set_state(OrderForm.delivery_choice)
        return

    try:
        await callback.message.edit_text(TEXTS["ask_name_self"])
    except TelegramBadRequest:
        await bot.send_message(chat_id=chat_id, text=TEXTS["ask_name_self"])
    await state.set_state(OrderForm.name)


@router.callback_query(
    OrderForm.recipient_choice,
    F.data == "order:recipient_saved",
)
async def handle_recipient_saved_list(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Показывает список сохранённых получателей или сообщение, что список пуст.

    Args:
        callback (CallbackQuery): Callback-запрос пользователя.
        state (FSMContext): Контекст FSM.

    Returns:
        None: Ничего не возвращает.
    """

    await callback.answer()
    if callback.message is None:
        return

    db = get_db_from_callback(callback)
    data = await state.get_data()
    user_db_id = int(data["user_db_id"])
    saved = db.list_saved_recipients(user_id=user_db_id)

    if not saved:
        try:
            await callback.message.edit_text(
                TEXTS["no_saved_recipients"],
                reply_markup=build_recipient_choice_keyboard(has_saved_recipients=False),
            )
        except TelegramBadRequest:
            await callback.message.answer(
                TEXTS["no_saved_recipients"],
                reply_markup=build_recipient_choice_keyboard(has_saved_recipients=False),
            )
        return

    try:
        await callback.message.edit_text(
            "📋 Выберите получателя:",
            reply_markup=build_saved_recipients_keyboard(saved),
        )
    except TelegramBadRequest:
        await callback.message.answer(
            "📋 Выберите получателя:",
            reply_markup=build_saved_recipients_keyboard(saved),
        )


@router.callback_query(
    OrderForm.recipient_choice,
    F.data == "order:recipient_back",
)
async def handle_recipient_back(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Возврат от списка сохранённых получателей к выбору типа получателя.

    Args:
        callback (CallbackQuery): Callback-запрос пользователя.
        state (FSMContext): Контекст FSM.

    Returns:
        None: Ничего не возвращает.
    """

    await callback.answer()
    if callback.message is None:
        return

    db = get_db_from_callback(callback)
    data = await state.get_data()
    user_db_id = int(data["user_db_id"])
    saved = db.list_saved_recipients(user_id=user_db_id)
    try:
        await callback.message.edit_text(
            text=TEXTS["ask_recipient_choice"],
            reply_markup=build_recipient_choice_keyboard(has_saved_recipients=len(saved) > 0),
        )
    except TelegramBadRequest:
        await callback.message.answer(
            TEXTS["ask_recipient_choice"],
            reply_markup=build_recipient_choice_keyboard(has_saved_recipients=len(saved) > 0),
        )


@router.callback_query(
    OrderForm.recipient_choice,
    F.data.startswith("order:recipient:"),
)
async def handle_recipient_picked(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Обрабатывает выбор сохранённого получателя по id.

    Args:
        callback (CallbackQuery): Callback-запрос пользователя.
        state (FSMContext): Контекст FSM.

    Returns:
        None: Ничего не возвращает.
    """

    await callback.answer()
    if callback.message is None:
        return

    raw = callback.data or ""
    prefix = "order:recipient:"
    if not raw.startswith(prefix):
        return
    try:
        recipient_id = int(raw[len(prefix) :].strip())
    except ValueError:
        return

    db = get_db_from_callback(callback)
    data = await state.get_data()
    user_db_id = int(data["user_db_id"])
    recipient = db.get_saved_recipient(recipient_id=recipient_id, user_id=user_db_id)
    if recipient is None:
        await callback.answer("Получатель не найден.", show_alert=True)
        return

    await state.update_data(
        name=recipient.name,
        phone=recipient.phone,
        suggested_address=recipient.address,
    )
    options_for_kb = _build_delivery_options_for_kb()
    try:
        await callback.message.edit_text(
            TEXTS["ask_delivery_choice"],
            reply_markup=build_delivery_choice_keyboard(options_for_kb),
        )
    except TelegramBadRequest:
        await callback.message.answer(
            TEXTS["ask_delivery_choice"],
            reply_markup=build_delivery_choice_keyboard(options_for_kb),
        )
    await state.set_state(OrderForm.delivery_choice)


@router.callback_query(
    OrderForm.recipient_choice,
    F.data == "order:recipient_new",
)
async def handle_recipient_new_start(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Начало ввода данных нового получателя.

    Args:
        callback (CallbackQuery): Callback-запрос пользователя.
        state (FSMContext): Контекст FSM.

    Returns:
        None: Ничего не возвращает.
    """

    await callback.answer()
    if callback.message is None:
        return

    try:
        await callback.message.edit_text(TEXTS["ask_name"], reply_markup=None)
    except TelegramBadRequest:
        await callback.message.answer(TEXTS["ask_name"])
    await state.set_state(OrderForm.recipient_new_name)


@router.message(OrderForm.name)
async def handle_name_step(message: Message, state: FSMContext) -> None:
    """Обрабатывает ввод имени заказчика (сценарий «Я получатель», профиль без имени).

    Args:
        message (Message): Сообщение пользователя с именем.
        state (FSMContext): Контекст FSM.

    Returns:
        None: Ничего не возвращает.
    """

    await state.update_data(name=(message.text or "").strip())
    await message.answer(
        TEXTS["ask_phone_self"],
        reply_markup=_build_phone_keyboard(),
    )
    await state.set_state(OrderForm.phone)


@router.message(OrderForm.phone)
async def handle_phone_step(message: Message, state: FSMContext) -> None:
    """Обрабатывает ввод телефона заказчика (сценарий «Я получатель») и валидацию.

    Args:
        message (Message): Сообщение пользователя с телефоном.
        state (FSMContext): Контекст FSM.

    Returns:
        None: Ничего не возвращает.
    """

    if message.contact and message.contact.phone_number:
        phone_source = message.contact.phone_number
    else:
        phone_source = message.text or ""

    phone_normalized = normalize_phone(phone_source)
    if phone_normalized is None:
        await message.answer(TEXTS["invalid_phone"])
        return

    await state.update_data(phone=phone_normalized)
    await _ask_delivery_choice(message=message, state=state)


@router.message(OrderForm.recipient_new_name)
async def handle_recipient_new_name(message: Message, state: FSMContext) -> None:
    """Обрабатывает ввод имени нового получателя.

    Args:
        message (Message): Сообщение пользователя с именем.
        state (FSMContext): Контекст FSM.

    Returns:
        None: Ничего не возвращает.
    """

    await state.update_data(recipient_new_name=(message.text or "").strip())
    await message.answer(
        TEXTS["ask_phone"],
        reply_markup=_build_phone_keyboard(),
    )
    await state.set_state(OrderForm.recipient_new_phone)


@router.message(OrderForm.recipient_new_phone)
async def handle_recipient_new_phone(message: Message, state: FSMContext) -> None:
    """Обрабатывает ввод телефона нового получателя и валидацию.

    Args:
        message (Message): Сообщение пользователя с телефоном.
        state (FSMContext): Контекст FSM.

    Returns:
        None: Ничего не возвращает.
    """

    if message.contact and message.contact.phone_number:
        phone_source = message.contact.phone_number
    else:
        phone_source = message.text or ""

    phone_normalized = normalize_phone(phone_source)
    if phone_normalized is None:
        await message.answer(TEXTS["invalid_phone"])
        return

    await state.update_data(recipient_new_phone=phone_normalized)
    await _ask_delivery_choice(message=message, state=state)


@router.message(OrderForm.address)
async def handle_address_step(message: Message, state: FSMContext) -> None:
    """Обрабатывает ввод адреса доставки (после выбора города).

    Для нового получателя сохраняет его в saved_recipients и подставляет name/phone в состояние.

    Args:
        message (Message): Сообщение пользователя с адресом.
        state (FSMContext): Контекст FSM.

    Returns:
        None: Ничего не возвращает.
    """

    address = (message.text or "").strip()
    await state.update_data(address=address)

    data = await state.get_data()
    r_name = data.get("recipient_new_name")
    r_phone = data.get("recipient_new_phone")
    if r_name is not None and r_phone is not None:
        db = get_db_from_message(message)
        user_db_id = int(data["user_db_id"])
        db.add_saved_recipient(
            user_id=user_db_id,
            name=str(r_name),
            phone=str(r_phone),
            address=address,
        )
        await state.update_data(name=r_name, phone=r_phone)

    await _ask_delivery_datetime(message=message, state=state)


def _parse_manual_time(text: str) -> str | None:
    """Парсит время из строки ЧЧ:ММ или Ч:ММ. Возвращает HH:MM в интервале 9:00–19:00 или None."""
    text = (text or "").strip()
    if not text:
        return None
    parts = text.replace(".", ":").split(":")
    if len(parts) != 2:
        return None
    try:
        h, m = int(parts[0].strip()), int(parts[1].strip())
    except ValueError:
        return None
    if not (0 <= m <= 59):
        return None
    if h < 9 or h > 19:
        return None
    if h == 19 and m != 0:
        return None
    return f"{h:02d}:{m:02d}"


@router.message(OrderForm.desired_datetime)
async def handle_desired_datetime_step(message: Message, state: FSMContext) -> None:
    """Обработка ввода: ручной ввод времени (если ожидался) или подсказка использовать календарь.

    Args:
        message (Message): Сообщение пользователя.
        state (FSMContext): Контекст FSM.

    Returns:
        None: Ничего не возвращает.
    """
    data = await state.get_data()
    if data.get("order_manual_time") and data.get("order_date"):
        time_str = _parse_manual_time(message.text or "")
        if time_str is None:
            await message.answer(TEXTS["invalid_delivery_time"])
            return
        order_date = data["order_date"]
        try:
            y, m, d = map(int, order_date.split("-"))
            sel_date = date_type(y, m, d)
            desired = f"{d:02d}.{m:02d}.{y:04d} {time_str}"
        except (ValueError, IndexError):
            await message.answer(TEXTS["invalid_delivery_time"])
            return
        if sel_date == date_type.today():
            parts = time_str.split(":")
            if len(parts) == 2:
                try:
                    h, min_val = int(parts[0]), int(parts[1])
                    slot = time_cls(h, min_val)
                    if slot <= dt.now().time():
                        await message.answer(TEXTS["delivery_time_past"])
                        return
                except ValueError:
                    pass
        await state.update_data(
            desired_delivery_datetime=desired,
            order_manual_time=None,
        )
        await message.answer(TEXTS["ask_comment"])
        await state.set_state(OrderForm.comment)
        return
    await message.answer(
        "Используйте календарь выше для выбора даты, затем времени доставки.",
        reply_markup=build_delivery_calendar_keyboard(
            year=date_type.today().year,
            month=date_type.today().month,
        ),
    )


@router.callback_query(
    OrderForm.desired_datetime,
    F.data == "order:date_past",
)
async def handle_date_past(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Реакция на нажатие прошедшей даты в календаре."""
    await callback.answer(TEXTS["date_past_alert"], show_alert=True)


@router.callback_query(
    OrderForm.desired_datetime,
    F.data.startswith("order:date_month:"),
)
async def handle_date_month(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Переключение месяца в календаре доставки."""
    await callback.answer()
    if callback.message is None:
        return
    prefix = "order:date_month:"
    raw = (callback.data or "").strip()
    if not raw.startswith(prefix):
        return
    part = raw[len(prefix) :].strip()
    parts = part.split("-")
    if len(parts) != 2:
        return
    try:
        year, month = int(parts[0]), int(parts[1])
    except ValueError:
        return
    if not (1 <= month <= 12):
        return
    keyboard = build_delivery_calendar_keyboard(year=year, month=month)
    try:
        await callback.message.edit_reply_markup(reply_markup=keyboard)
    except TelegramBadRequest:
        await callback.message.edit_text(
            text=TEXTS["ask_delivery_datetime"],
            reply_markup=keyboard,
        )


@router.callback_query(
    OrderForm.desired_datetime,
    F.data.startswith("order:date:"),
)
async def handle_date_picked(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Обработка выбора даты: сохраняем в state и показываем выбор времени."""
    await callback.answer()
    if callback.message is None:
        return
    prefix = "order:date:"
    raw = (callback.data or "").strip()
    if not raw.startswith(prefix):
        return
    date_str = raw[len(prefix) :].strip()
    try:
        sel_date = date_type.fromisoformat(date_str)
    except ValueError:
        sel_date = None
    if sel_date == date_type.today() and dt.now().time() >= time_cls(19, 0):
        await callback.message.edit_text(
            text=TEXTS["delivery_today_ended"],
            reply_markup=build_delivery_calendar_keyboard(
                year=date_type.today().year,
                month=date_type.today().month,
            ),
        )
        return
    await state.update_data(order_date=date_str)
    await callback.message.edit_text(
        text=TEXTS["ask_delivery_time"],
        reply_markup=build_delivery_time_keyboard(selected_date=sel_date),
    )


@router.callback_query(
    OrderForm.desired_datetime,
    F.data == "order:time_manual",
)
async def handle_time_manual(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Включение ручного ввода времени: просим ввести ЧЧ:ММ."""
    await callback.answer()
    if callback.message is None:
        return
    await state.update_data(order_manual_time=True)
    await callback.message.edit_text(TEXTS["ask_delivery_time_manual"])


@router.callback_query(
    OrderForm.desired_datetime,
    F.data.startswith("order:time:"),
)
async def handle_time_picked(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Обработка выбора времени: формируем дату/время и переходим к комментарию."""
    await callback.answer()
    if callback.message is None:
        return
    prefix = "order:time:"
    raw = (callback.data or "").strip()
    if not raw.startswith(prefix):
        return
    time_str = raw[len(prefix) :].strip()
    data = await state.get_data()
    order_date = data.get("order_date") or ""
    if not order_date or len(order_date) != 10:
        await callback.message.answer(TEXTS["ask_delivery_datetime"])
        await callback.message.answer(
            "Используйте календарь для выбора даты.",
            reply_markup=build_delivery_calendar_keyboard(
                year=date_type.today().year,
                month=date_type.today().month,
            ),
        )
        return
    try:
        y, m, d = map(int, order_date.split("-"))
        sel_date = date_type(y, m, d)
        desired = f"{d:02d}.{m:02d}.{y:04d} {time_str}"
    except (ValueError, IndexError):
        desired = ""
        sel_date = None
    if not desired:
        await callback.answer()
        return
    if sel_date == date_type.today():
        parts = time_str.split(":")
        if len(parts) == 2:
            try:
                h, min_val = int(parts[0]), int(parts[1])
                slot = time_cls(h, min_val)
                if slot <= dt.now().time():
                    await callback.answer(TEXTS["delivery_time_past"], show_alert=True)
                    await callback.message.edit_text(
                        text=TEXTS["ask_delivery_time"],
                        reply_markup=build_delivery_time_keyboard(selected_date=sel_date),
                    )
                    return
            except ValueError:
                pass
    await state.update_data(desired_delivery_datetime=desired)
    await callback.message.edit_text(TEXTS["ask_comment"])
    await state.set_state(OrderForm.comment)


@router.callback_query(
    OrderForm.delivery_choice,
    F.data.startswith("order:delivery:"),
)
async def handle_delivery_picked(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Обрабатывает выбор города доставки или самовывоза: показывает сообщение о сроках и переходит к комментарию.

    Args:
        callback (CallbackQuery): Callback-запрос пользователя.
        state (FSMContext): Контекст FSM.

    Returns:
        None: Ничего не возвращает.
    """
    await callback.answer()
    if callback.message is None:
        return

    raw = callback.data or ""
    prefix = "order:delivery:"
    if not raw.startswith(prefix):
        return
    slug = raw[len(prefix) :].strip()

    option = next((o for o in DELIVERY_OPTIONS if o[0] == slug), None)
    if option is None:
        return

    _slug, city_name, delivery_cost, message_text = option

    try:
        await callback.message.edit_text(message_text, parse_mode="Markdown")
    except TelegramBadRequest:
        await callback.message.answer(message_text, parse_mode="Markdown")

    data = await state.get_data()
    if slug == "pickup":
        await state.update_data(
            delivery_city=city_name,
            delivery_cost=delivery_cost,
            address=PICKUP_ADDRESS,
        )
        r_name = data.get("recipient_new_name")
        r_phone = data.get("recipient_new_phone")
        if r_name is not None and r_phone is not None:
            db = get_db_from_callback(callback)
            user_db_id = int(data["user_db_id"])
            db.add_saved_recipient(
                user_id=user_db_id,
                name=str(r_name),
                phone=str(r_phone),
                address=PICKUP_ADDRESS,
            )
            await state.update_data(name=r_name, phone=r_phone)
        await _ask_delivery_datetime(message=callback.message, state=state)
    else:
        await state.update_data(
            delivery_city=city_name,
            delivery_cost=delivery_cost,
        )
        await _ask_address(message=callback.message, state=state)


@router.message(OrderForm.comment)
async def handle_comment_step(message: Message, state: FSMContext) -> None:
    """Обрабатывает ввод комментария к заказу и показывает итоговую сводку.

    Args:
        message (Message): Сообщение пользователя с комментарием.
        state (FSMContext): Контекст FSM.

    Returns:
        None: Ничего не возвращает.
    """

    comment_raw = (message.text or "").strip()
    comment: str | None = None if comment_raw in {"", "-"} else comment_raw

    data = await state.get_data()
    user_db_id = int(data["user_db_id"])

    db = get_db_from_message(message)
    cart_items = db.get_cart(user_id=user_db_id)
    if not cart_items:
        await message.answer(TEXTS["empty_cart"])
        await state.clear()
        return

    await state.update_data(comment=comment)

    suggested_emails = db.get_emails_used_by_user(user_db_id)
    if suggested_emails:
        await state.update_data(suggested_emails=suggested_emails)
        await message.answer(
            TEXTS["ask_email_choice"],
            reply_markup=build_email_choice_keyboard(
                suggested_emails,
                enter_new_text=TEXTS["email_enter_new"],
            ),
        )
        await state.set_state(OrderForm.email_choice)
    else:
        await message.answer(TEXTS["ask_email"])
        await state.set_state(OrderForm.email)


@router.callback_query(OrderForm.email_choice, F.data.startswith("order:email_idx:"))
async def handle_email_choice_selected(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Обрабатывает выбор ранее использованного email и показывает сводку заказа.

    Args:
        callback (CallbackQuery): Callback-запрос с данными order:email_idx:N.
        state (FSMContext): Контекст FSM.

    Returns:
        None: Ничего не возвращает.
    """
    await callback.answer()
    if callback.message is None:
        return

    idx_str = callback.data.split(":", 2)[-1]
    try:
        idx = int(idx_str)
    except ValueError:
        return
    data = await state.get_data()
    suggested: list[str] = data.get("suggested_emails") or []
    if idx < 0 or idx >= len(suggested):
        return
    selected_email = suggested[idx]
    await state.update_data(email=selected_email)

    user_db_id = int(data.get("user_db_id", 0))
    db = get_db_from_callback(callback)
    await _build_and_show_summary(
        target=callback,
        state=state,
        db=db,
        user_db_id=user_db_id,
        email=selected_email,
    )


@router.callback_query(OrderForm.email_choice, F.data == "order:email_new")
async def handle_email_choice_enter_new(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Переводит пользователя к вводу нового email.

    Args:
        callback (CallbackQuery): Callback-запрос.
        state (FSMContext): Контекст FSM.

    Returns:
        None: Ничего не возвращает.
    """
    await callback.answer()
    if callback.message is None:
        return
    await callback.message.answer(TEXTS["ask_email"])
    await state.set_state(OrderForm.email)


@router.message(OrderForm.email)
async def handle_email_step(message: Message, state: FSMContext) -> None:
    """Обрабатывает ввод email и показывает итоговую сводку заказа.

    Args:
        message (Message): Сообщение пользователя с email.
        state (FSMContext): Контекст FSM.

    Returns:
        None: Ничего не возвращает.
    """
    email_raw = (message.text or "").strip().lower()
    if not email_raw or not EMAIL_REGEX.match(email_raw):
        await message.answer(TEXTS["invalid_email"])
        return
    await state.update_data(email=email_raw)
    data = await state.get_data()
    user_db_id = int(data.get("user_db_id", 0))
    db = get_db_from_message(message)
    await _build_and_show_summary(
        target=message,
        state=state,
        db=db,
        user_db_id=user_db_id,
        email=email_raw,
    )


@router.callback_query(OrderForm.address_confirm, F.data == "order:addr_use_saved")
async def handle_use_saved_address(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Использует ранее сохранённый адрес доставки для текущего заказа.

    Args:
        callback (CallbackQuery): Callback-запрос пользователя.
        state (FSMContext): Контекст FSM.

    Returns:
        None: Ничего не возвращает.
    """

    await callback.answer()
    if callback.message is None:
        return

    data = await state.get_data()
    suggested_address = data.get("suggested_address") or data.get("last_address")
    if not suggested_address:
        await callback.message.answer(TEXTS["ask_address"])
        await state.set_state(OrderForm.address)
        return

    await state.update_data(address=suggested_address)

    r_name = data.get("recipient_new_name")
    r_phone = data.get("recipient_new_phone")
    if r_name is not None and r_phone is not None:
        db = get_db_from_callback(callback)
        user_db_id = int(data["user_db_id"])
        db.add_saved_recipient(
            user_id=user_db_id,
            name=str(r_name),
            phone=str(r_phone),
            address=suggested_address,
        )
        await state.update_data(name=r_name, phone=r_phone)

    try:
        today = date_type.today()
        await callback.message.edit_text(
            TEXTS["ask_delivery_datetime"],
            reply_markup=build_delivery_calendar_keyboard(year=today.year, month=today.month),
        )
    except TelegramBadRequest:
        await _ask_delivery_datetime(message=callback.message, state=state)
        return
    await state.set_state(OrderForm.desired_datetime)


@router.callback_query(OrderForm.address_confirm, F.data == "order:addr_change_city")
async def handle_change_city(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Возвращает к выбору города доставки.

    Args:
        callback (CallbackQuery): Callback-запрос пользователя.
        state (FSMContext): Контекст FSM.

    Returns:
        None: Ничего не возвращает.
    """
    await callback.answer()
    if callback.message is None:
        return
    options_for_kb = _build_delivery_options_for_kb()
    try:
        await callback.message.edit_text(
            text=TEXTS["ask_delivery_choice"],
            reply_markup=build_delivery_choice_keyboard(options_for_kb),
        )
    except TelegramBadRequest:
        await callback.message.answer(
            TEXTS["ask_delivery_choice"],
            reply_markup=build_delivery_choice_keyboard(options_for_kb),
        )
    await state.set_state(OrderForm.delivery_choice)


@router.callback_query(OrderForm.address_confirm, F.data == "order:addr_change")
async def handle_change_address(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Запрашивает у пользователя новый адрес доставки вместо сохранённого.

    Args:
        callback (CallbackQuery): Callback-запрос пользователя.
        state (FSMContext): Контекст FSM.

    Returns:
        None: Ничего не возвращает.
    """

    await callback.answer()
    if callback.message is None:
        return
    try:
        await callback.message.edit_text(
            TEXTS["ask_address"],
            reply_markup=None,
        )
    except TelegramBadRequest:
        await callback.message.answer(
            TEXTS["ask_address"],
            reply_markup=ReplyKeyboardRemove(),
        )
    await state.set_state(OrderForm.address)


@router.callback_query(F.data == "order:cancel")
async def handle_order_cancel(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Отменяет оформление заказа и очищает состояние.

    Args:
        callback (CallbackQuery): Callback-запрос пользователя.
        state (FSMContext): Контекст FSM.

    Returns:
        None: Ничего не возвращает.
    """

    await callback.answer()
    await state.clear()
    if callback.message:
        try:
            await callback.message.edit_text(text=TEXTS["cancelled"])
        except TelegramBadRequest:
            await callback.message.answer(TEXTS["cancelled"])


@router.callback_query(F.data == "order:confirm")
async def handle_order_confirm(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Создаёт заказ в БД после подтверждения пользователем.

    Args:
        callback (CallbackQuery): Callback-запрос пользователя.
        state (FSMContext): Контекст FSM.

    Returns:
        None: Ничего не возвращает.
    """

    await callback.answer()
    if callback.message is None:
        return

    data = await state.get_data()
    user_db_id = int(data["user_db_id"])
    name = str(data["name"])
    phone = str(data["phone"])
    address = str(data["address"])
    comment = data.get("comment")
    email = data.get("email")
    delivery_city = data.get("delivery_city")
    delivery_cost = int(data.get("delivery_cost", 0))
    desired_delivery_datetime = data.get("desired_delivery_datetime")

    if not delivery_city or not str(delivery_city).strip():
        options_for_kb = _build_delivery_options_for_kb()
        try:
            await callback.message.edit_text(
                TEXTS["ask_delivery_choice"],
                reply_markup=build_delivery_choice_keyboard(options_for_kb),
            )
        except TelegramBadRequest:
            await callback.message.answer(
                TEXTS["ask_delivery_choice"],
                reply_markup=build_delivery_choice_keyboard(options_for_kb),
            )
        await state.set_state(OrderForm.delivery_choice)
        return

    db = get_db_from_callback(callback)
    if not email or not str(email).strip() or not isinstance(email, str):
        # Если email по какой-то причине не в state или некорректного типа — это ошибка логики.
        logger.error(
            "Оформление заказа: email отсутствует в состоянии перед подтверждением, "
            "user_db_id={user_id}, data_keys={keys}",
            user_id=user_db_id,
            keys=sorted(data.keys()),
        )
        await callback.message.answer(
            "Не удалось зафиксировать email для заказа. Попробуйте оформить заказ заново.",
        )
        await state.clear()
        return
    email = str(email).strip()

    order = db.create_order_from_cart(
        user_id=user_db_id,
        customer_name=name,
        phone=phone,
        delivery_address=address,
        comment=comment if isinstance(comment, str) else None,
        email=email,
        delivery_city=str(delivery_city) if delivery_city else None,
        delivery_cost=delivery_cost,
        desired_delivery_datetime=(str(desired_delivery_datetime) if desired_delivery_datetime else None),
    )

    # Обновляем контакт заказчика в профиле только если получатель — он сам.
    if data.get("recipient_self"):
        db.update_user_contact(
            user_id=user_db_id,
            customer_name=name,
            phone=phone,
        )

    await state.clear()

    if order is None:
        logger.warning(
            "Не удалось создать заказ: корзина оказалась пустой user_db_id={user_id}",
            user_id=user_db_id,
        )
        await callback.answer(TEXTS["empty_cart"], show_alert=True)
        return

    # Заказ в OpenCart создаётся: при наличных — в handle_payment_method_cash после выбора способа;
    # при ЮKassa — в handle_mock_payment после успешной оплаты.
    from .payment import show_payment_method_choice

    await show_payment_method_choice(
        message=callback.message,
        order_id=order.id,
        edit=True,
    )
