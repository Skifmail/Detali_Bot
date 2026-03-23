from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, User
from loguru import logger

from ..callback_data import CartAddCallback, CartItemCallback
from ..database.models import CartItem
from ..keyboards.kb import TEXTS as KB_TEXTS
from ..keyboards.kb import (
    build_cart_item_controls_keyboard,
    build_cart_keyboard,
    build_main_menu_keyboard,
)
from ..utils import get_db, get_db_from_callback, is_admin

TEXTS: dict[str, str] = {
    "empty_cart": "🛒 Ваша корзина пока пуста.\n\nДобавьте товары из каталога.",
    "cart_header": "🛒 Корзина\n\n{lines}\n\nИтого: <b>{total} ₽</b>",
    "cart_line": "• {title} — {price} ₽ × {qty} = {line_total} ₽",
    "cart_updated": "✅ Корзина обновлена.",
}

router = Router(name="cart")


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
    bot: Bot,
    chat_id: int,
    from_user: User,
    state: FSMContext | None = None,
) -> None:
    """Отправляет содержимое корзины в указанный чат и при state сохраняет id сообщений.

    Args:
        bot (Bot): Экземпляр бота aiogram.
        chat_id (int): Идентификатор чата.
        from_user (User): Пользователь Telegram, чью корзину нужно показать.
        state (FSMContext | None): Контекст FSM для сохранения id сообщений.

    Returns:
        None: Ничего не возвращает.
    """
    db = get_db(bot)
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


async def _update_cart_messages(
    callback: CallbackQuery,
    state: FSMContext,
    from_user: User,
    items_after: list[CartItem],
) -> None:
    """Обновляет сообщения корзины в чате после изменения позиций.

    Args:
        callback (CallbackQuery): Колбэк с действием по позиции корзины.
        state (FSMContext): Состояние FSM с сохранёнными id сообщений корзины.
        from_user (User): Пользователь, чью корзину показываем.
        items_after (list[CartItem]): Актуальный список позиций корзины.

    Returns:
        None: Ничего не возвращает.
    """
    data = await state.get_data()
    message_ids: list[int] = data.get("cart_message_ids") or []
    chat_id = data.get("cart_chat_id")
    if callback.message is not None:
        chat_id = chat_id or callback.message.chat.id

    if not items_after:
        if message_ids and chat_id is not None:
            header_mid = message_ids[0]
            for mid in message_ids[1:]:
                try:
                    await callback.bot.delete_message(chat_id=chat_id, message_id=mid)
                except TelegramBadRequest:
                    continue
            try:
                await callback.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=header_mid,
                    text=TEXTS["empty_cart"],
                )
            except TelegramBadRequest:
                try:
                    await callback.bot.delete_message(chat_id=chat_id, message_id=header_mid)
                except TelegramBadRequest:
                    pass
                if callback.message is not None:
                    await callback.message.answer(TEXTS["empty_cart"])
        elif callback.message is not None:
            await callback.message.answer(TEXTS["empty_cart"])
        await state.clear()
        await callback.answer(TEXTS["cart_updated"])
        return

    if callback.message is None or chat_id is None:
        if callback.message is not None:
            await _send_cart_to_chat(
                callback.bot,
                callback.message.chat.id,
                from_user,
                state=state,
            )
        await callback.answer(TEXTS["cart_updated"])
        return

    lines_text, total = _render_cart_lines(items_after)
    header_text = TEXTS["cart_header"].format(lines=lines_text, total=total)
    header_kb = build_cart_keyboard(has_items=True, can_checkout=True)

    header_msg_id: int | None = message_ids[0] if message_ids else None
    line_msg_ids: list[int] = message_ids[1:] if len(message_ids) > 1 else []

    if header_msg_id is not None:
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

    # Та же число строк — правим текст и кнопки на месте, без удаления строк.
    if (
        header_msg_id is not None
        and callback.message is not None
        and len(items_after) == len(line_msg_ids)
        and line_msg_ids
    ):
        for item, mid in zip(items_after, line_msg_ids, strict=True):
            item_text = f"{item.product.title} — {item.product.price} ₽ × {item.quantity}"
            try:
                await callback.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=mid,
                    text=item_text,
                    reply_markup=build_cart_item_controls_keyboard(cart_item_id=item.id),
                )
            except TelegramBadRequest:
                await _send_cart_to_chat(
                    callback.bot,
                    callback.message.chat.id,
                    from_user,
                    state=state,
                )
                await callback.answer(TEXTS["cart_updated"])
                return
        await state.update_data(
            cart_message_ids=[header_msg_id, *line_msg_ids],
            cart_chat_id=callback.message.chat.id,
        )
        await callback.answer(TEXTS["cart_updated"])
        return

    if message_ids and chat_id is not None:
        for mid in message_ids[1:]:
            try:
                await callback.bot.delete_message(chat_id=chat_id, message_id=mid)
            except TelegramBadRequest:
                continue

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

    await callback.answer(TEXTS["cart_updated"])


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
    if user_to_show is None:
        return
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
    from_user = message.from_user
    if from_user is not None and is_admin(from_user.id, message.bot):
        await message.answer(
            "Корзина и оформление заказов доступны только покупателям. Используйте админ-панель.",
            reply_markup=build_main_menu_keyboard(is_admin=True),
        )
        return

    data = await state.get_data()
    old_ids: list[int] = data.get("cart_message_ids") or []
    old_chat: int | None = data.get("cart_chat_id")
    if old_ids and old_chat is not None:
        for mid in old_ids:
            try:
                await message.bot.delete_message(chat_id=old_chat, message_id=mid)
            except TelegramBadRequest:
                continue
        await state.update_data(cart_message_ids=[], cart_chat_id=None)

    await _show_cart(message, state=state)


@router.callback_query(CartAddCallback.filter())
async def handle_add_to_cart(
    callback: CallbackQuery,
    callback_data: CartAddCallback,
    state: FSMContext,
) -> None:
    """Обрабатывает добавление товара в корзину: удаляет карточку товара и показывает корзину.

    Администраторам добавление в корзину недоступно.
    """
    from_user = callback.from_user
    if from_user is not None and is_admin(from_user.id, callback.bot):
        await callback.answer("Корзина недоступна для администраторов.", show_alert=True)
        return
    if from_user is None:
        await callback.answer()
        return

    db = get_db_from_callback(callback)
    product_id = callback_data.product_id
    product = db.get_product(product_id=product_id)
    if product is None:
        # Товар больше неактивен или не найден — не даём добавить «битую» позицию.
        await callback.answer("Этот товар больше недоступен в каталоге.", show_alert=True)
        return

    current_user = db.get_or_create_user(
        tg_id=from_user.id,
        first_name=from_user.first_name,
        last_name=from_user.last_name,
    )
    db.add_to_cart(user_id=current_user.id, product_id=product_id, delta=1)
    await callback.answer("✅ Товар добавлен в корзину.", show_alert=False)


@router.callback_query(CartItemCallback.filter())
async def handle_cart_item_change(
    callback: CallbackQuery,
    callback_data: CartItemCallback,
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
    db = get_db_from_callback(callback)
    from_user = callback.from_user
    if from_user is None:
        await callback.answer()
        return

    current_user = db.get_or_create_user(
        tg_id=from_user.id,
        first_name=from_user.first_name,
        last_name=from_user.last_name,
    )

    cart_item_id = callback_data.cart_item_id
    action = callback_data.action

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
    await _update_cart_messages(callback=callback, state=state, from_user=from_user, items_after=items_after)
