from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger

from ..database.db import Database
from ..database.models import Order, OrderStatus, StatsSummary
from ..keyboards.kb import (
    TEXTS as KB_TEXTS,
)
from ..keyboards.kb import (
    build_admin_main_keyboard,
    build_admin_more_keyboard,
    build_admin_order_details_keyboard,
    build_admin_orders_keyboard,
    build_admin_status_change_keyboard,
    build_main_menu_keyboard,
)

TEXTS: dict[str, str] = {
    "not_admin": "⛔ У вас нет доступа к админ-панели.",
    "entry": "⚙️ Админ-панель floraldetails demo.\n\nВыберите раздел:",
    "orders_header": "📦 Последние заказы:",
    "order_details": (
        "📦 Заказ #{display_number}\n"
        "Статус: {status}\n"
        "📆 Дата и время заказа: {order_created_at}\n"
        "💳 Оплата: {payment_info}\n\n"
        "👤 Получатель: {customer_name}\n"
        "📞 Телефон: {phone}\n"
        "📍 Адрес доставки: {delivery_address}\n"
        "🚚 Город доставки: {delivery_city}\n"
        "💰 Стоимость доставки: {delivery_cost} ₽\n"
        "📅 Желаемые дата и время: {desired_datetime}\n"
        "✏️ Комментарий: {comment}\n\n"
        "🛍 Товары:\n{items}\n\n"
        "💵 Сумма заказа: {total} ₽\n"
        "ID пользователя (заказчика): {user_id}"
    ),
    "order_item": "• {title} — {price} ₽ × {qty} = {line_total} ₽",
    "new_order_notification_title": "🆕 Новый заказ #{display_number}",
    "status_changed": "✅ Статус заказа обновлён: {status}",
    "status_notification": ("🔔 Обновление статуса заказа #{display_number}\n\n" "Новый статус: {status}"),
    "stats": ("📊 Статистика магазина\n\n" "Пользователей: {users}\n" "Заказов: {orders}\n" "Выручка: {revenue} ₽"),
    "broadcast_prompt": (
        "📣 Рассылка\n\n"
        "Отправьте сообщение для рассылки: фото с подписью или только текст.\n"
        "Получат все пользователи бота. Для отмены нажмите кнопку ниже."
    ),
    "broadcast_no_content": "⚠️ Отправьте фото с подписью или текстовое сообщение.",
    "broadcast_preview": "👆 Так будет выглядеть рассылка. Отправить?",
    "broadcast_done": "✅ Рассылка отправлена {count} пользователям.",
    "broadcast_cancelled": "❌ Рассылка отменена.",
    "new_order_title": "🆕 Новый заказ\n\n",
    "order_cancelled_by_user_title": "❌ Пользователь отменил заказ\n\n",
}

router = Router(name="admin")


class BroadcastForm(StatesGroup):
    """Состояния FSM для создания рассылки админом."""

    content = State()


def _build_broadcast_confirm_keyboard() -> InlineKeyboardMarkup:
    """Собирает клавиатуру подтверждения или отмены рассылки."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ Отправить рассылку",
            callback_data="admin:broadcast_confirm",
        ),
        InlineKeyboardButton(
            text="❌ Отмена",
            callback_data="admin:broadcast_cancel",
        ),
    )
    return builder.as_markup()


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


def _is_admin(message: Message) -> bool:
    """Проверяет, является ли пользователь администратором.

    Args:
        message (Message): Сообщение пользователя.

    Returns:
        bool: True, если пользователь — администратор.
    """

    admin_ids: set[int] = getattr(message.bot, "admin_ids", set())
    from_user = message.from_user
    return from_user is not None and from_user.id in admin_ids


def _is_admin_callback(callback: CallbackQuery) -> bool:
    """Проверяет, является ли пользователь админом для callback-запроса.

    Args:
        callback (CallbackQuery): Callback-запрос.

    Returns:
        bool: True, если пользователь — администратор.
    """

    admin_ids: set[int] = getattr(callback.bot, "admin_ids", set())
    return callback.from_user.id in admin_ids


def _format_order_details(order: Order) -> str:
    """Формирует полное описание заказа для админ-панели и уведомлений.

    Args:
        order (Order): Полный заказ.

    Returns:
        str: Текст со всеми данными заказа.
    """

    lines: list[str] = []
    for item in order.items:
        line_total = item.unit_price * item.quantity
        lines.append(
            TEXTS["order_item"].format(
                title=item.product.title,
                price=item.unit_price,
                qty=item.quantity,
                line_total=line_total,
            ),
        )
    desired_datetime = order.desired_delivery_datetime or "—"
    comment = order.comment or "—"
    delivery_city = order.delivery_city or "—"
    payment_info = _format_payment_info(order)
    order_created_at = order.created_at.strftime("%d.%m.%Y %H:%M")
    return TEXTS["order_details"].format(
        display_number=order.display_order_number,
        status=order.status.human_readable,
        order_created_at=order_created_at,
        payment_info=payment_info,
        user_id=order.user_id,
        customer_name=order.customer_name,
        phone=order.phone,
        delivery_address=order.delivery_address,
        delivery_city=delivery_city,
        delivery_cost=order.delivery_cost,
        desired_datetime=desired_datetime,
        comment=comment,
        items="\n".join(lines) if lines else "—",
        total=order.total_amount,
    )


def _format_payment_info(order: Order) -> str:
    """Формирует строку о способе оплаты и статусе для заказа.

    Args:
        order (Order): Заказ.

    Returns:
        str: Текст вида «Наличными при получении» или «ЮКassa. Статус: …».
    """

    pm = order.payment_method or ""
    if pm == "cash":
        return "Наличными при получении"
    if pm == "yookassa":
        return f"ЮКassa. Статус: {order.status.human_readable}"
    return "—"


def _order_product_media(order: Order) -> list[InputMediaPhoto]:
    """Собирает список фото товаров заказа для отправки медиа-группой.

    Args:
        order (Order): Заказ с позициями.

    Returns:
        List[InputMediaPhoto]: До 10 фото (лимит Telegram).
    """

    media: list[InputMediaPhoto] = []
    for item in order.items:
        url = getattr(item.product, "image_url", None)
        if url and isinstance(url, str) and url.strip():
            media.append(InputMediaPhoto(media=url.strip()))
            if len(media) >= 10:
                break
    return media


def _first_order_photo_url(order: Order) -> str | None:
    """Возвращает URL первого фото товара в заказе для поста с подписью.

    Args:
        order (Order): Заказ с позициями.

    Returns:
        Optional[str]: URL изображения или None.
    """
    media_list = _order_product_media(order)
    if not media_list:
        return None
    url = getattr(media_list[0], "media", None)
    return str(url) if url else None


@router.message(Command("admin"))
@router.message(F.text == KB_TEXTS["menu_admin"])
async def handle_admin_entry(message: Message) -> None:
    """Точка входа в админ-панель (команда /admin или кнопка «Админ», если осталась).

    Args:
        message (Message): Сообщение пользователя.

    Returns:
        None: Ничего не возвращает.
    """
    if not _is_admin(message):
        await message.answer(TEXTS["not_admin"])
        return

    await message.answer(
        TEXTS["entry"],
        reply_markup=build_main_menu_keyboard(is_admin=True),
    )


@router.message(F.text == KB_TEXTS["menu_orders"])
async def handle_admin_orders_message(message: Message) -> None:
    """Показ списка заказов по кнопке «Заказы» в меню админа."""
    if not _is_admin(message):
        return
    db = _get_db_from_message(message)
    orders = db.list_recent_orders()
    if not orders:
        await message.answer(
            TEXTS["orders_header"],
            reply_markup=build_admin_orders_keyboard(orders=[]),
        )
        return
    await message.answer(
        TEXTS["orders_header"],
        reply_markup=build_admin_orders_keyboard(orders=orders),
    )


@router.message(F.text == KB_TEXTS["menu_stats"])
async def handle_admin_stats_message(message: Message) -> None:
    """Показ статистики по кнопке «Статистика» в меню админа."""
    if not _is_admin(message):
        return
    db = _get_db_from_message(message)
    stats: StatsSummary = db.get_stats()
    text = TEXTS["stats"].format(
        users=stats.total_users,
        orders=stats.total_orders,
        revenue=stats.total_revenue,
    )
    await message.answer(text=text)


@router.message(F.text == KB_TEXTS["menu_broadcast"])
async def handle_admin_broadcast_message(
    message: Message,
    state: FSMContext,
) -> None:
    """Старт рассылки по кнопке «Рассылка» в меню админа."""
    if not _is_admin(message):
        return
    await state.set_state(BroadcastForm.content)
    await message.answer(
        TEXTS["broadcast_prompt"],
        reply_markup=_build_broadcast_cancel_keyboard(),
    )


@router.message(F.text == KB_TEXTS["menu_more"])
async def handle_admin_more_message(message: Message) -> None:
    """Показ дополнительного меню (Добавить товар и т.д.) по кнопке «Ещё»."""
    if not _is_admin(message):
        return
    await message.answer(
        "Дополнительные действия:",
        reply_markup=build_admin_more_keyboard(),
    )


@router.callback_query(F.data == "nav:back_main")
async def handle_nav_back_main(callback: CallbackQuery) -> None:
    """Возврат в главное меню по кнопке «Назад» (из каталога или из «Ещё»)."""
    await callback.answer()
    if callback.message is None:
        return
    is_admin = callback.from_user.id in getattr(callback.bot, "admin_ids", set())
    await callback.bot.send_message(
        chat_id=callback.message.chat.id,
        text="🏠 Главное меню.",
        reply_markup=build_main_menu_keyboard(is_admin=is_admin),
    )


@router.callback_query(F.data == "admin:back")
@router.callback_query(F.data == "admin:orders")
async def handle_admin_back_or_orders(callback: CallbackQuery) -> None:
    """Возврат в главное меню админки или показ списка заказов (редактирование сообщения).

    Args:
        callback (CallbackQuery): Callback-запрос администратора.

    Returns:
        None: Ничего не возвращает.
    """

    await callback.answer()
    if not _is_admin_callback(callback) or callback.message is None:
        return

    if callback.data == "admin:back":
        main_kb = build_admin_main_keyboard()
        try:
            await callback.message.edit_text(
                text=TEXTS["entry"],
                reply_markup=main_kb,
            )
        except TelegramBadRequest as e:
            if "message is not modified" in str(e).lower():
                pass
            else:
                try:
                    await callback.message.edit_caption(
                        caption=TEXTS["entry"],
                        reply_markup=main_kb,
                    )
                except TelegramBadRequest:
                    try:
                        await callback.message.delete()
                    except TelegramBadRequest:
                        pass
                    await callback.bot.send_message(
                        chat_id=callback.message.chat.id,
                        text=TEXTS["entry"],
                        reply_markup=main_kb,
                    )
        return

    db = _get_db_from_callback(callback)
    orders = db.list_recent_orders()
    if not orders:
        try:
            await callback.message.edit_text(
                text="Пока нет заказов.",
                reply_markup=build_admin_orders_keyboard(orders=[]),
            )
        except TelegramBadRequest:
            await callback.message.edit_text("Пока нет заказов.")
        return

    try:
        await callback.message.edit_text(
            text=TEXTS["orders_header"],
            reply_markup=build_admin_orders_keyboard(orders=orders),
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise


@router.callback_query(F.data.startswith("admin:order:cancel:"))
async def handle_admin_order_cancel(callback: CallbackQuery) -> None:
    """Отменяет заказ по нажатию кнопки в админ-панели и обновляет сообщение.

    Args:
        callback (CallbackQuery): Callback-запрос администратора.

    Returns:
        None: Ничего не возвращает.
    """

    await callback.answer()
    if not _is_admin_callback(callback) or callback.message is None:
        return

    _, _, _, order_id_str = callback.data.split(":", maxsplit=3)
    order_id = int(order_id_str)

    db = _get_db_from_callback(callback)
    updated = db.update_order_status(
        order_id=order_id,
        new_status=OrderStatus.CANCELLED,
    )
    if updated is None:
        await callback.answer("Не удалось отменить заказ.", show_alert=True)
        return

    details_text = _format_order_details(updated)
    keyboard = build_admin_order_details_keyboard(
        order_id=updated.id,
        current_status=updated.status,
    )
    try:
        await callback.message.edit_text(
            text=details_text,
            reply_markup=keyboard,
        )
    except TelegramBadRequest:
        try:
            await callback.message.edit_caption(
                caption=details_text,
                reply_markup=keyboard,
            )
        except TelegramBadRequest:
            pass

    user_tg_id = db.get_user_tg_id(user_id=updated.user_id)
    if user_tg_id is not None:
        notification_text = TEXTS["status_notification"].format(
            display_number=updated.display_order_number,
            status=OrderStatus.CANCELLED.human_readable,
        )
        try:
            await callback.bot.send_message(chat_id=user_tg_id, text=notification_text)
        except Exception as e:
            logger.warning(
                "Не удалось отправить уведомление об отмене заказа пользователю tg_id={tg_id}: {err}",
                tg_id=user_tg_id,
                err=e,
            )
    else:
        logger.warning(
            "Не удалось найти tg_id пользователя для заказа id={order_id}",
            order_id=order_id,
        )


@router.callback_query(F.data.startswith("admin:order:"))
async def handle_admin_order_details(callback: CallbackQuery) -> None:
    """Показывает подробности выбранного заказа одним сообщением: фото + подпись (как пост).

    Args:
        callback (CallbackQuery): Callback-запрос администратора.

    Returns:
        None: Ничего не возвращает.
    """
    await callback.answer()
    if not _is_admin_callback(callback) or callback.message is None:
        return

    db = _get_db_from_callback(callback)
    _, _, order_id_str = callback.data.split(":", maxsplit=2)
    order_id = int(order_id_str)
    order = db.get_order(order_id=order_id)
    if order is None:
        await callback.answer("Не удалось найти указанный заказ.", show_alert=True)
        return

    details_text = _format_order_details(order)
    keyboard = build_admin_order_details_keyboard(
        order_id=order.id,
        current_status=order.status,
    )
    chat_id = callback.message.chat.id
    photo_url = _first_order_photo_url(order)

    edited = False
    try:
        await callback.message.edit_text(
            text=details_text,
            reply_markup=keyboard,
        )
        edited = True
    except TelegramBadRequest:
        try:
            await callback.message.edit_caption(
                caption=details_text,
                reply_markup=keyboard,
            )
            edited = True
        except TelegramBadRequest:
            pass

    if not edited:
        try:
            await callback.message.delete()
        except TelegramBadRequest:
            pass
        if photo_url:
            try:
                await callback.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo_url,
                    caption=details_text,
                    reply_markup=keyboard,
                )
            except Exception as e:
                logger.warning(
                    "Не удалось отправить фото заказа id={order_id}, отправляю текст: {err}",
                    order_id=order.id,
                    err=e,
                )
                await callback.bot.send_message(
                    chat_id=chat_id,
                    text=details_text,
                    reply_markup=keyboard,
                )
        else:
            await callback.bot.send_message(
                chat_id=chat_id,
                text=details_text,
                reply_markup=keyboard,
            )


@router.callback_query(F.data.startswith("admin:status:set:"))
async def handle_admin_status_set(callback: CallbackQuery) -> None:
    """Устанавливает новый статус заказа и уведомляет клиента.

    Args:
        callback (CallbackQuery): Callback-запрос администратора.

    Returns:
        None: Ничего не возвращает.
    """

    await callback.answer()
    if not _is_admin_callback(callback) or callback.message is None:
        return

    db = _get_db_from_callback(callback)
    # admin:status:set:3:courier -> order_id=3, status_value=courier
    parts = callback.data.split(":", maxsplit=4)
    if len(parts) < 5:
        await callback.answer("Неверный формат callback.", show_alert=True)
        return
    order_id = int(parts[3])
    new_status = OrderStatus(parts[4])

    updated = db.update_order_status(order_id=order_id, new_status=new_status)
    if updated is None:
        await callback.answer("Не удалось обновить статус заказа.", show_alert=True)
        return

    details_text = _format_order_details(updated)
    keyboard = build_admin_order_details_keyboard(
        order_id=updated.id,
        current_status=updated.status,
    )
    try:
        await callback.message.edit_text(
            text=details_text,
            reply_markup=keyboard,
        )
    except TelegramBadRequest:
        try:
            await callback.message.edit_caption(
                caption=details_text,
                reply_markup=keyboard,
            )
        except TelegramBadRequest:
            pass

    user_tg_id = db.get_user_tg_id(user_id=updated.user_id)
    if user_tg_id is None:
        logger.warning(
            "Не удалось найти tg_id пользователя для заказа id={order_id}",
            order_id=order_id,
        )
        return

    notification_text = TEXTS["status_notification"].format(
        display_number=updated.display_order_number,
        status=new_status.human_readable,
    )
    await callback.bot.send_message(chat_id=user_tg_id, text=notification_text)


@router.callback_query(F.data.startswith("admin:status:"))
async def handle_admin_status_menu(callback: CallbackQuery) -> None:
    """Открывает меню смены статуса заказа (callback admin:status:order_id).

    Регистрируется после handle_admin_status_set, чтобы admin:status:set:...
    обрабатывался только установкой статуса.
    """
    await callback.answer()
    if not _is_admin_callback(callback) or callback.message is None:
        return

    db = _get_db_from_callback(callback)
    _, _, order_id_str = callback.data.split(":", maxsplit=2)
    order_id = int(order_id_str)
    order: Order | None = db.get_order(order_id=order_id)
    if order is None:
        await callback.answer("Не удалось найти указанный заказ.", show_alert=True)
        return

    status_keyboard = build_admin_status_change_keyboard(
        order_id=order.id,
        current_status=order.status,
    )
    try:
        await callback.message.edit_text(
            text="Выберите новый статус заказа:",
            reply_markup=status_keyboard,
        )
    except TelegramBadRequest:
        try:
            await callback.message.edit_caption(
                caption="Выберите новый статус заказа:",
                reply_markup=status_keyboard,
            )
        except TelegramBadRequest:
            try:
                await callback.message.delete()
            except TelegramBadRequest:
                pass
            await callback.bot.send_message(
                chat_id=callback.message.chat.id,
                text="Выберите новый статус заказа:",
                reply_markup=status_keyboard,
            )


@router.callback_query(F.data == "admin:stats")
async def handle_admin_stats(callback: CallbackQuery) -> None:
    """Отображает сводную статистику (редактирует сообщение).

    Args:
        callback (CallbackQuery): Callback-запрос администратора.

    Returns:
        None: Ничего не возвращает.
    """

    await callback.answer()
    if not _is_admin_callback(callback) or callback.message is None:
        return

    db = _get_db_from_callback(callback)
    stats: StatsSummary = db.get_stats()
    text = TEXTS["stats"].format(
        users=stats.total_users,
        orders=stats.total_orders,
        revenue=stats.total_revenue,
    )
    try:
        await callback.message.edit_text(text=text)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise


def _build_broadcast_cancel_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура с одной кнопкой «Отмена» для шага ввода рассылки."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="❌ Отмена",
            callback_data="admin:broadcast_cancel",
        ),
    )
    return builder.as_markup()


@router.callback_query(F.data == "admin:broadcast")
async def handle_admin_broadcast(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Запрашивает контент рассылки: админ отправит фото с подписью или текст.

    Args:
        callback (CallbackQuery): Callback-запрос администратора.
        state (FSMContext): Контекст FSM.

    Returns:
        None: Ничего не возвращает.
    """
    await callback.answer()
    if not _is_admin_callback(callback) or callback.message is None:
        return

    await state.set_state(BroadcastForm.content)
    await callback.message.answer(
        TEXTS["broadcast_prompt"],
        reply_markup=_build_broadcast_cancel_keyboard(),
    )


@router.message(BroadcastForm.content, F.photo)
async def handle_broadcast_content_photo(message: Message, state: FSMContext) -> None:
    """Принимает фото с подписью (или без) для рассылки."""
    if not _is_admin(message):
        return
    photo = message.photo[-1]
    caption = message.caption or ""
    await state.update_data(
        broadcast_photo_file_id=photo.file_id,
        broadcast_text=caption,
    )
    await message.answer(
        TEXTS["broadcast_preview"],
        reply_markup=_build_broadcast_confirm_keyboard(),
    )


@router.message(BroadcastForm.content, F.text)
async def handle_broadcast_content_text(message: Message, state: FSMContext) -> None:
    """Принимает текстовое сообщение для рассылки."""
    if not _is_admin(message):
        return
    text = (message.text or "").strip()
    if not text:
        await message.answer(TEXTS["broadcast_no_content"])
        return
    await state.update_data(
        broadcast_photo_file_id=None,
        broadcast_text=text,
    )
    await message.answer(
        TEXTS["broadcast_preview"],
        reply_markup=_build_broadcast_confirm_keyboard(),
    )


@router.message(BroadcastForm.content)
async def handle_broadcast_content_other(message: Message) -> None:
    """Отклоняет неподходящий контент рассылки."""
    if not _is_admin(message):
        return
    await message.answer(TEXTS["broadcast_no_content"])


@router.callback_query(F.data == "admin:broadcast_confirm")
async def handle_broadcast_confirm(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Отправляет рассылку всем пользователям по сохранённому контенту."""
    await callback.answer()
    if not _is_admin_callback(callback) or callback.message is None:
        return

    data = await state.get_data()
    photo_file_id = data.get("broadcast_photo_file_id")
    text = (data.get("broadcast_text") or "").strip()
    if not text and not photo_file_id:
        await callback.message.answer(TEXTS["broadcast_no_content"])
        await state.clear()
        return

    db = _get_db_from_callback(callback)
    tg_ids = db.get_all_user_tg_ids()
    sent_count = 0
    for tg_id in tg_ids:
        try:
            if photo_file_id:
                await callback.bot.send_photo(
                    chat_id=tg_id,
                    photo=photo_file_id,
                    caption=text or None,
                )
            else:
                await callback.bot.send_message(chat_id=tg_id, text=text)
            sent_count += 1
        except Exception:
            logger.exception(
                "Не удалось отправить рассылку пользователю tg_id={tg_id}",
                tg_id=tg_id,
            )

    await state.clear()
    await callback.message.edit_text(
        TEXTS["broadcast_done"].format(count=sent_count),
    )


@router.callback_query(F.data == "admin:broadcast_cancel")
async def handle_broadcast_cancel(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Отменяет рассылку и возвращает в админ-меню."""
    await callback.answer()
    if callback.message is None:
        return
    await state.clear()
    await callback.message.edit_text(
        TEXTS["broadcast_cancelled"],
        reply_markup=build_admin_main_keyboard(),
    )


async def notify_admins_new_order(bot: Bot, order: Order) -> None:
    """Отправляет всем администраторам уведомление о новом заказе с полной информацией и фото товаров.

    Сохраняет message_id каждого сообщения для последующего обновления при смене статуса (например, оплате).

    Args:
        bot (Bot): Экземпляр бота.
        order (Order): Созданный заказ с позициями.

    Returns:
        None: Ничего не возвращает.
    """
    admin_ids: set[int] = getattr(bot, "admin_ids", set())
    if not admin_ids:
        return

    db: Database | None = getattr(bot, "db", None)
    full_text = TEXTS["new_order_title"] + _format_order_details(order)
    photo = _first_order_photo_url(order)

    for admin_tg_id in admin_ids:
        try:
            sent = None
            if photo:
                try:
                    sent = await bot.send_photo(
                        chat_id=admin_tg_id,
                        photo=photo,
                        caption=full_text,
                    )
                except TelegramBadRequest:
                    photo = None
            if sent is None:
                sent = await bot.send_message(chat_id=admin_tg_id, text=full_text)
            if db is not None and sent is not None:
                db.save_admin_order_notification(
                    order_id=order.id,
                    admin_tg_id=admin_tg_id,
                    chat_id=admin_tg_id,
                    message_id=sent.message_id,
                    has_photo=bool(photo),
                )
        except Exception as e:
            logger.warning(
                "Не удалось отправить уведомление о заказе админу tg_id={}: {}",
                admin_tg_id,
                e,
            )


async def update_admins_order_notification(bot: Bot, order_id: int) -> None:
    """Обновляет текст уведомлений админам о заказе (например, при смене статуса на «Оплачен»).

    Редактирует ранее отправленные сообщения с актуальными данными заказа.

    Args:
        bot (Bot): Экземпляр бота.
        order_id (int): Идентификатор заказа.

    Returns:
        None: Ничего не возвращает.
    """
    db: Database | None = getattr(bot, "db", None)
    if db is None:
        return
    order: Order | None = db.get_order(order_id=order_id)
    if order is None:
        return
    notifications = db.get_admin_order_notifications(order_id=order_id)
    if not notifications:
        return
    full_text = TEXTS["new_order_title"] + _format_order_details(order)
    for chat_id, message_id, has_photo in notifications:
        try:
            if has_photo:
                await bot.edit_message_caption(
                    chat_id=chat_id,
                    message_id=message_id,
                    caption=full_text,
                )
            else:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=full_text,
                )
        except TelegramBadRequest as e:
            logger.debug(
                "Не удалось обновить уведомление о заказе order_id={order_id} chat_id={chat_id}: {err}",
                order_id=order_id,
                chat_id=chat_id,
                err=e,
            )


async def notify_admins_order_cancelled_by_user(bot: Bot, order: Order) -> None:
    """Отправляет всем администраторам уведомление об отмене заказа пользователем.

    Args:
        bot (Bot): Экземпляр бота.
        order (Order): Отменённый заказ (уже со статусом CANCELLED).

    Returns:
        None: Ничего не возвращает.
    """
    admin_ids: set[int] = getattr(bot, "admin_ids", set())
    if not admin_ids:
        return

    full_text = TEXTS["order_cancelled_by_user_title"] + _format_order_details(order)

    for admin_tg_id in admin_ids:
        try:
            await bot.send_message(chat_id=admin_tg_id, text=full_text)
        except Exception as e:
            logger.warning(
                "Не удалось отправить уведомление об отмене заказа админу tg_id={tg_id}: {err}",
                tg_id=admin_tg_id,
                err=e,
            )
