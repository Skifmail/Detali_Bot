from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, User
from loguru import logger

from ..database.db import Database
from ..database.models import CartItem
from ..keyboards.kb import TEXTS as KB_TEXTS
from ..keyboards.kb import (
    build_cart_item_controls_keyboard,
    build_cart_keyboard,
    build_main_menu_keyboard,
)

TEXTS: dict[str, str] = {
    "empty_cart": "🛒 Ваша корзина пока пуста.\n\nДобавьте товары из каталога.",
    "cart_header": "🛒 Корзина\n\n{lines}\n\nИтого: <b>{total} ₽</b>",
    "cart_line": "• {title} — {price} ₽ × {qty} = {line_total} ₽",
    "cart_updated": "✅ Корзина обновлена.",
}

router = Router(name="cart")


def _get_db_from_message(message: Message) -> Database:
    """Возвращает экземпляр базы данных из контекста бота по сообщению.

    Args:
        message (Message): Сообщение Telegram.

    Returns:
        Database: Экземпляр базы данных.
    """

    db: Database = message.bot.db
    return db


def _get_db_from_callback(callback: CallbackQuery) -> Database:
    """Возвращает экземпляр базы данных из контекста бота по callback-запросу.

    Args:
        callback (CallbackQuery): Callback-запрос Telegram.

    Returns:
        Database: Экземпляр базы данных.
    """

    db: Database = callback.bot.db
    return db


def _render_cart_lines(items: list[CartItem]) -> tuple[str, int]:
    """Строит текстовое представление корзины и считает итоговую сумму.

    Args:
        items (List[CartItem]): Позиции корзины.

    Returns:
        tuple[str, int]: Строка с описанием корзины и общая сумма.
    """

    lines: list[str] = []
    total = 0
    for item in items:
        line_total = item.product.price * item.quantity
        total += line_total
        lines.append(
            TEXTS["cart_line"].format(
                title=item.product.title,
                price=item.product.price,
                qty=item.quantity,
                line_total=line_total,
            ),
        )
    return "\n".join(lines), total


async def _send_cart_to_chat(
    bot: object,
    chat_id: int,
    from_user: User,
    state: FSMContext | None = None,
) -> None:
    """Отправляет содержимое корзины в указанный чат и при state сохраняет id сообщений.

    Args:
        bot (object): Экземпляр бота (должен иметь атрибут db).
        chat_id (int): ID чата.
        from_user (User): Пользователь Telegram, чью корзину показывать.
        state (FSMContext | None): Контекст FSM для сохранения id сообщений.

    Returns:
        None: Ничего не возвращает.
    """
    db: Database = bot.db
    current_user = db.get_or_create_user(
        tg_id=from_user.id,
        first_name=from_user.first_name,
        last_name=from_user.last_name,
    )
    items = db.get_cart(user_id=current_user.id)
    if not items:
        await bot.send_message(chat_id=chat_id, text=TEXTS["empty_cart"])
        return

    lines_text, total = _render_cart_lines(items)
    text = TEXTS["cart_header"].format(lines=lines_text, total=total)
    keyboard = build_cart_keyboard(
        has_items=True,
        can_checkout=True,
    )
    sent = await bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=keyboard,
    )
    message_ids: list[int] = [sent.message_id]

    for item in items:
        item_text = f"{item.product.title} — {item.product.price} ₽ × {item.quantity}"
        msg = await bot.send_message(
            chat_id=chat_id,
            text=item_text,
            reply_markup=build_cart_item_controls_keyboard(cart_item_id=item.id),
        )
        message_ids.append(msg.message_id)

    if state is not None:
        await state.update_data(
            cart_message_ids=message_ids,
            cart_chat_id=chat_id,
        )


async def _show_cart(
    message: Message,
    from_user: User | None = None,
    state: FSMContext | None = None,
) -> None:
    """Отображает текущее состояние корзины пользователя.

    При переданном state сохраняет id отправленных сообщений для последующего удаления при очистке корзины.

    Args:
        message (Message): Сообщение, в чат которого отправлять ответ (может быть от бота).
        from_user (Optional[User]): Telegram User, чью корзину показывать. Если не передан,
            берётся message.from_user (для обычных сообщений от пользователя).
        state (FSMContext | None): Контекст FSM для сохранения id сообщений корзины.

    Returns:
        None: Ничего не возвращает.
    """
    user_to_show = from_user if from_user is not None else message.from_user
    assert user_to_show is not None
    await _send_cart_to_chat(
        message.bot,
        message.chat.id,
        user_to_show,
        state=state,
    )


@router.message(Command("cart"))
@router.message(F.text == KB_TEXTS["menu_cart"])
async def handle_cart_entry(message: Message, state: FSMContext) -> None:
    """Точка входа в корзину по команде или кнопке меню.

    Администраторам корзина недоступна — показывается главное меню.
    """
    admin_ids: set[int] = getattr(message.bot, "admin_ids", set())
    if message.from_user and message.from_user.id in admin_ids:
        await message.answer(
            "Корзина и оформление заказов доступны только покупателям. Используйте админ-панель.",
            reply_markup=build_main_menu_keyboard(is_admin=True),
        )
        return
    await _show_cart(message, state=state)


@router.callback_query(F.data.startswith("cart:add:"))
async def handle_add_to_cart(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Обрабатывает добавление товара в корзину: удаляет карточку товара и показывает корзину.

    Администраторам добавление в корзину недоступно.
    """
    admin_ids: set[int] = getattr(callback.bot, "admin_ids", set())
    if callback.from_user and callback.from_user.id in admin_ids:
        await callback.answer("Корзина недоступна для администраторов.", show_alert=True)
        return
    await callback.answer("✅ Товар добавлен в корзину.")
    db = _get_db_from_callback(callback)
    from_user = callback.from_user
    if from_user is None:
        return
    _, _, product_id_str = callback.data.split(":", maxsplit=2)
    product_id = int(product_id_str)

    current_user = db.get_or_create_user(
        tg_id=from_user.id,
        first_name=from_user.first_name,
        last_name=from_user.last_name,
    )
    db.add_to_cart(user_id=current_user.id, product_id=product_id, delta=1)

    if callback.message is None:
        return
    items = db.get_cart(user_id=current_user.id)
    if not items:
        return
    lines_text, total = _render_cart_lines(items)
    text = TEXTS["cart_header"].format(lines=lines_text, total=total)
    keyboard = build_cart_keyboard(has_items=True, can_checkout=True)
    try:
        await callback.message.edit_text(text, reply_markup=keyboard)
    except TelegramBadRequest:
        try:
            await callback.message.edit_caption(caption=text, reply_markup=keyboard)
        except TelegramBadRequest:
            await callback.message.delete()
            await _send_cart_to_chat(
                callback.bot,
                callback.message.chat.id,
                from_user,
                state=state,
            )
            return
    message_ids: list[int] = [callback.message.message_id]
    for item in items:
        item_text = f"{item.product.title} — {item.product.price} ₽ × {item.quantity}"
        msg = await callback.bot.send_message(
            chat_id=callback.message.chat.id,
            text=item_text,
            reply_markup=build_cart_item_controls_keyboard(cart_item_id=item.id),
        )
        message_ids.append(msg.message_id)
    await state.update_data(
        cart_message_ids=message_ids,
        cart_chat_id=callback.message.chat.id,
    )


@router.callback_query(F.data.startswith("cart:item:"))
async def handle_cart_item_change(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Обрабатывает изменение количества или удаление позиции корзины.

    При опустошении корзины удаляет все сообщения с содержимым корзины.

    Args:
        callback (CallbackQuery): Callback-запрос пользователя.
        state (FSMContext): Контекст FSM с id сообщений корзины.

    Returns:
        None: Ничего не возвращает.
    """
    db = _get_db_from_callback(callback)
    from_user = callback.from_user
    if from_user is None:
        await callback.answer()
        return
    current_user = db.get_or_create_user(
        tg_id=from_user.id,
        first_name=from_user.first_name,
        last_name=from_user.last_name,
    )

    _, _, cart_item_id_str, action = callback.data.split(":", maxsplit=3)
    cart_item_id = int(cart_item_id_str)

    items = db.get_cart(user_id=current_user.id)
    target = next((item for item in items if item.id == cart_item_id), None)
    if target is None:
        logger.warning(
            "Попытка изменить несуществующую позицию корзины id={cart_item_id}",
            cart_item_id=cart_item_id,
        )
        await callback.answer()
        return

    if action == "inc":
        db.add_to_cart(
            user_id=current_user.id,
            product_id=target.product_id,
            delta=1,
        )
    elif action == "dec":
        db.add_to_cart(
            user_id=current_user.id,
            product_id=target.product_id,
            delta=-1,
        )
    elif action == "remove":
        db.add_to_cart(
            user_id=current_user.id,
            product_id=target.product_id,
            delta=-target.quantity,
        )

    items_after = db.get_cart(user_id=current_user.id)
    data = await state.get_data()
    message_ids: list[int] = data.get("cart_message_ids") or []
    chat_id = data.get("cart_chat_id")
    if callback.message is not None:
        chat_id = chat_id or callback.message.chat.id

    if not items_after:
        if message_ids and chat_id is not None:
            for mid in message_ids:
                try:
                    await callback.bot.delete_message(chat_id=chat_id, message_id=mid)
                except TelegramBadRequest:
                    pass
        await state.clear()
    else:
        lines_text, total = _render_cart_lines(items_after)
        header_text = TEXTS["cart_header"].format(lines=lines_text, total=total)
        header_kb = build_cart_keyboard(has_items=True, can_checkout=True)
        if message_ids and chat_id is not None:
            header_msg_id: int | None = message_ids[0]
            try:
                await callback.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=header_msg_id,
                    text=header_text,
                    reply_markup=header_kb,
                )
            except TelegramBadRequest:
                try:
                    await callback.bot.edit_message_caption(
                        chat_id=chat_id,
                        message_id=header_msg_id,
                        caption=header_text,
                        reply_markup=header_kb,
                    )
                except TelegramBadRequest:
                    header_msg_id = None
            for mid in message_ids[1:]:
                try:
                    await callback.bot.delete_message(chat_id=chat_id, message_id=mid)
                except TelegramBadRequest:
                    pass
            new_ids: list[int] = [header_msg_id] if header_msg_id is not None else []
            if callback.message is not None and header_msg_id is not None:
                for item in items_after:
                    item_text = f"{item.product.title} — {item.product.price} ₽ × {item.quantity}"
                    msg = await callback.bot.send_message(
                        chat_id=callback.message.chat.id,
                        text=item_text,
                        reply_markup=build_cart_item_controls_keyboard(cart_item_id=item.id),
                    )
                    new_ids.append(msg.message_id)
                await state.update_data(
                    cart_message_ids=new_ids,
                    cart_chat_id=callback.message.chat.id,
                )
            elif callback.message is not None:
                await _send_cart_to_chat(
                    callback.bot,
                    callback.message.chat.id,
                    from_user,
                    state=state,
                )
        else:
            if callback.message is not None:
                await _send_cart_to_chat(
                    callback.bot,
                    callback.message.chat.id,
                    from_user,
                    state=state,
                )
    await callback.answer(TEXTS["cart_updated"])
