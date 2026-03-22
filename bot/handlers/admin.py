from __future__ import annotations

import asyncio
import csv
import io
import re
from datetime import UTC, datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger

from ..core.admin_ui import delete_tracked_admin_messages, track_admin_messages
from ..database.db import RECENT_ORDERS_LIMIT, Database
from ..database.models import Order, OrderStatus, User
from ..keyboards.kb import TEXTS as KB_TEXTS
from ..keyboards.kb import (
    build_admin_admins_keyboard,
    build_admin_main_keyboard,
    build_admin_more_keyboard,
    build_admin_more_reply_keyboard,
    build_admin_order_details_keyboard,
    build_admin_orders_keyboard,
    build_admin_orders_reply_keyboard,
    build_admin_remove_admins_keyboard,
    build_admin_status_change_keyboard,
    build_export_orders_period_keyboard,
    build_main_menu_keyboard,
)
from ..services.bot_status import build_bot_status_html
from ..services.catalog_sync import sync_catalog_from_opencart
from ..utils import get_db_from_callback, get_db_from_message, is_admin, normalize_phone

TEXTS: dict[str, str] = {
    "not_admin": "⛔ У вас нет доступа к админ-панели.",
    "entry": "⚙️ Админ-панель floraldetails demo.\n\nВыберите раздел:",
    "orders_header": "📦 Последние заказы:",
    "order_details": (
        "<b>📦 Заказ</b> #{display_number}\n"
        "<b>Статус</b> {status}\n"
        "<b>📆 Дата, время</b> {order_created_at}\n"
        "<b>💳 Оплата</b> {payment_info}\n"
        "─────────────────────\n"
        "<b>👤 Получатель</b> {customer_name}\n"
        "<b>📞 Телефон</b> {phone}\n"
        "<b>📧 Email</b> {email}\n"
        "─────────────────────\n"
        "<b>🚚 Доставка</b>\n"
        "Город: {delivery_city}\n"
        "Адрес: {delivery_address}\n"
        "📅 Желаемые дата и время: {desired_datetime}\n"
        "─────────────────────\n"
        "<b>✏️ Комментарий</b> {comment}\n"
        "─────────────────────\n"
        "<b>🛍 Товары</b>\n"
        "{items}\n"
        "💰 Доставка: {delivery_cost} ₽\n"
        "─────────────────────\n"
        "<b>💵 Итого</b> {total} ₽\n"
        "─────────────────────\n"
        "ID пользователя: {user_id}"
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
    "sync_catalog_start": "🔄 Обновляю каталог из OpenCart…",
    "sync_catalog_ok": "✅ Каталог обновлён из OpenCart.\n\n{summary}",
    "sync_catalog_fail": "❌ Не удалось обновить каталог: {error}",
    "users_header": "👥 Пользователи бота (всего {total}):",
    "users_line": "• id={id} tg={tg_id} | {name} | тел. {phone}",
    "users_empty": "👥 Пользователей пока нет.",
    "users_more": "\n\n… показаны последние {limit} из {total}.",
    "admins_header": "👤 Администраторы бота\n\n{body}\n\n",
    "admins_list_env": "SuperAdmin: {ids}",
    "admins_list_db": "Добавлены через бота: {ids}",
    "admins_list_all": "Всего: {ids}",
    "admins_add_prompt": (
        "Введите Telegram ID пользователя (целое число). " "Узнать ID: @userinfobot или бот Get My ID."
    ),
    "admins_add_ok": "✅ Пользователь {user_id} добавлен в список администраторов.",
    "admins_add_already": "Пользователь {user_id} уже является администратором.",
    "admins_remove_ok": "✅ Пользователь {user_id} удалён из списка администраторов (из бота).",
    "admins_remove_only_db": (
        "Удалить можно только тех, кто добавлен через бота. SuperAdmin (это Вы) нельзя удалить через бота"
    ),
    "admins_remove_empty": "Нет администраторов, добавленных через бота. Удалять нечего.",
    "bot_status_error": "❌ Не удалось получить статус бота. Подробности в логах сервера.",
    "orders_search_prompt": "🔍 Введите номер заказа (4 цифры) или телефон (формат +7XXXXXXXXXX или 8XXXXXXXXXX):",
    "orders_search_not_found": "По запросу <code>{query}</code> заказы не найдены.",
    "orders_search_results": "📦 Найденные заказы по запросу: <code>{query}</code>",
    "orders_filter_new": "📦 Заказы — Новые:",
    "orders_filter_delivery": "📦 Заказы — В доставке:",
    "orders_filter_paid": "📦 Заказы — Оплаченные:",
    "export_orders_prompt": "📥 Выберите период для выгрузки заказов в CSV:",
    "export_orders_empty": "В выбранном периоде заказов нет.",
    "export_orders_done": "✅ Файл с заказами отправлен ({count} заказов).",
    "order_message_prompt": "✉️ Введите сообщение для клиента (по заказу #{display_number}):",
    "order_message_sent": "✅ Сообщение отправлено клиенту.",
    "order_message_no_user": "Не удалось отправить: клиент не найден (нет Telegram).",
    "contact_edit_prompt": (
        "📞 До 5 контактов для связи с клиентами. "
        "Отправьте контакт (добавить), «удалить N», «очистить» или «готово»."
    ),
    "contact_edit_list": "Текущие контакты ({count}/5):\n{list}",
    "contact_edit_empty": "Нет контактов.",
    "contact_edit_added": "✅ Контакт добавлен.",
    "contact_edit_removed": "✅ Контакт удалён.",
    "contact_edit_cleared": "✅ Все контакты очищены.",
    "contact_edit_done": "✅ Готово.",
    "contact_edit_cancelled": "Отменено.",
    "contact_edit_full": "Достигнут лимит (5 контактов). Удалите один: «удалить N».",
    "contact_edit_bad_delete": "Укажите номер контакта от 1 до {max}: «удалить 1».",
    "client_message_header": (
        "📦 Сообщение по заказу #{display_number}\n\n{admin_message}\n\n" "📞 Связаться с нами:\n{admin_contacts}"
    ),
    "client_message_no_contact": (
        "📦 Сообщение по заказу #{display_number}\n\n{admin_message}\n\n"
        "📞 Телефоны поддержки:\n\n"
        "+7 (916) 005-06-08\n"
        "+7 (916) 876-30-45"
    ),
}

router = Router(name="admin")


async def _show_admin_orders_screen(message: Message, *, user_id: int) -> None:
    """Показывает экран управления заказами (reply + список с инлайн-кнопками), заменяя предыдущий слот.

    Args:
        message (Message): Чат и контекст отправки (в т.ч. сообщение бота из callback).
        user_id (int): Telegram ID администратора.

    Returns:
        None: Ничего не возвращает.
    """

    if not is_admin(user_id, message.bot):
        return
    bot = message.bot
    chat_id = message.chat.id
    await delete_tracked_admin_messages(bot, chat_id)
    db = get_db_from_message(message)
    total = db.count_orders()
    page = 0
    orders = db.list_orders_page(limit=RECENT_ORDERS_LIMIT, offset=page * RECENT_ORDERS_LIMIT)
    reply_kb = build_admin_orders_reply_keyboard()
    m1 = await message.answer(
        "Управление заказами:",
        reply_markup=reply_kb,
    )
    ids: list[int] = [m1.message_id]
    if orders:
        m2 = await message.answer(
            TEXTS["orders_header"],
            reply_markup=build_admin_orders_keyboard(
                orders=orders,
                page=page,
                page_size=RECENT_ORDERS_LIMIT,
                total_count=total,
            ),
        )
    else:
        m2 = await message.answer(TEXTS["orders_header"] + "\n\nПока нет заказов.")
    ids.append(m2.message_id)
    track_admin_messages(bot, chat_id, ids)


async def _show_export_orders_prompt(message: Message, *, user_id: int) -> None:
    """Показывает запрос периода выгрузки CSV (один экран в слоте админки).

    Args:
        message (Message): Сообщение-триггер.
        user_id (int): Telegram ID администратора.

    Returns:
        None: Ничего не возвращает.
    """

    if not is_admin(user_id, message.bot):
        return
    bot = message.bot
    chat_id = message.chat.id
    await delete_tracked_admin_messages(bot, chat_id)
    msg = await message.answer(
        TEXTS["export_orders_prompt"],
        reply_markup=build_export_orders_period_keyboard(),
    )
    track_admin_messages(bot, chat_id, [msg.message_id])


async def _show_main_menu_after_back(message: Message, *, user_id: int) -> None:
    """Возвращает в главное меню админа, заменяя отслеживаемые сообщения одним ответом.

    Args:
        message (Message): Сообщение пользователя.
        user_id (int): Telegram ID пользователя.

    Returns:
        None: Ничего не возвращает.
    """

    is_admin_flag = is_admin(user_id, message.bot)
    bot = message.bot
    chat_id = message.chat.id
    await delete_tracked_admin_messages(bot, chat_id)
    msg = await message.answer(
        "🏠 Главное меню.",
        reply_markup=build_main_menu_keyboard(is_admin=is_admin_flag),
    )
    track_admin_messages(bot, chat_id, [msg.message_id])


def _format_catalog_summary(summary: list[tuple[str, int]]) -> str:
    """Форматирует сводку синхронизации каталога: категория → количество товаров.

    Args:
        summary: Список пар (название категории, количество товаров).

    Returns:
        Строка с построчным перечислением для отправки в чат.
    """
    if not summary:
        return "Нет категорий с товарами."
    lines: list[str] = []
    for name, count in summary:
        if count == 1:
            word = "товар"
        elif 2 <= count <= 4:
            word = "товара"
        else:
            word = "товаров"
        lines.append(f"• {name}: {count} {word}")
    return "\n".join(lines)


class BroadcastForm(StatesGroup):
    """Состояния FSM для создания рассылки админом."""

    content = State()


class AdminOrderSearchForm(StatesGroup):
    """Состояния FSM для поиска заказа администратором."""

    query = State()


class AdminOrderMessageForm(StatesGroup):
    """Состояния FSM для отправки сообщения клиенту по заказу."""

    message = State()


class AdminContactForm(StatesGroup):
    """Состояния FSM для изменения контакта админа для связи с клиентами."""

    contact = State()


class AdminAdminsForm(StatesGroup):
    """Состояния FSM для добавления администратора по Telegram ID."""

    add_id = State()


@router.message(F.text == KB_TEXTS["admin_orders_search"])
async def handle_admin_orders_search_start(
    message: Message,
    state: FSMContext,
) -> None:
    """Запускает сценарий поиска заказа по номеру или телефону из reply-клавиатуры."""

    if message.from_user is None or not _is_superadmin(message.from_user.id, message.bot):
        return

    await delete_tracked_admin_messages(message.bot, message.chat.id)
    await state.set_state(AdminOrderSearchForm.query)
    msg = await message.answer(TEXTS["orders_search_prompt"])
    track_admin_messages(message.bot, message.chat.id, [msg.message_id])


def _refresh_bot_admin_ids(bot: Bot, db: Database) -> None:
    """Обновляет bot.admin_ids: объединение админов из env и из таблицы bot_admins."""
    env_ids: set[int] = getattr(bot, "_admin_ids_from_env", set())
    db_ids = set(db.list_bot_admin_ids())
    bot.admin_ids = env_ids | db_ids


def _is_superadmin(user_id: int, bot: Bot) -> bool:
    """Проверяет, является ли пользователь супер-админом (задан в ADMIN_IDS)."""
    env_ids: set[int] = getattr(bot, "_admin_ids_from_env", set())
    return user_id in env_ids


def _format_admins_message(bot: Bot, db: Database) -> str:
    """Формирует текст списка администраторов для раздела «Администраторы»."""
    env_ids = sorted(getattr(bot, "_admin_ids_from_env", set()))
    db_ids = db.list_bot_admin_ids()
    all_ids = sorted(set(env_ids) | set(db_ids))
    if not all_ids:
        body = "Список пуст. Добавьте админов через кнопку ниже."
    else:
        parts = []
        if env_ids:
            parts.append(TEXTS["admins_list_env"].format(ids=", ".join(str(x) for x in env_ids)))
        if db_ids:
            parts.append(TEXTS["admins_list_db"].format(ids=", ".join(str(x) for x in db_ids)))
        parts.append(TEXTS["admins_list_all"].format(ids=", ".join(str(x) for x in all_ids)))
        body = "\n".join(parts)
    return TEXTS["admins_header"].format(body=body)


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
        email=order.email or "—",
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
        str: Текст вида «Наличными при получении», «ЮКassa. Статус: …» или «ЮКassa (тест)».
    """
    pm = (order.payment_method or "").strip()
    if pm == "cash":
        return "Наличными при получении"
    if pm == "yookassa":
        return f"ЮКassa. Статус: {order.status.human_readable}"
    # Заказ оплачен, но способ не был сохранён (демо-оплата или старая запись без метода)
    if order.status == OrderStatus.PAID:
        return "ЮКassa (тест / демо)"
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
    if message.from_user is None or not is_admin(message.from_user.id, message.bot):
        await message.answer(TEXTS["not_admin"])
        return

    bot = message.bot
    chat_id = message.chat.id
    await delete_tracked_admin_messages(bot, chat_id)
    msg = await message.answer(
        TEXTS["entry"],
        reply_markup=build_main_menu_keyboard(is_admin=True),
    )
    track_admin_messages(bot, chat_id, [msg.message_id])


@router.message(F.text == KB_TEXTS["menu_orders"])
async def handle_admin_orders_message(message: Message) -> None:
    """Показ списка заказов по кнопке «Заказы» в меню админа."""
    if message.from_user is None or not is_admin(message.from_user.id, message.bot):
        return
    await _show_admin_orders_screen(message, user_id=message.from_user.id)


@router.message(F.text == KB_TEXTS["menu_stats"])
async def handle_admin_stats_message(message: Message) -> None:
    """Показ статистики по кнопке «Статистика» в меню админа."""
    if message.from_user is None or not is_admin(message.from_user.id, message.bot):
        return
    bot = message.bot
    chat_id = message.chat.id
    await delete_tracked_admin_messages(bot, chat_id)
    db = get_db_from_message(message)
    text = _format_stats_detailed(db)
    msg = await message.answer(text=text, parse_mode="HTML")
    track_admin_messages(bot, chat_id, [msg.message_id])


@router.message(F.text == KB_TEXTS["menu_broadcast"])
async def handle_admin_broadcast_message(
    message: Message,
    state: FSMContext,
) -> None:
    """Старт рассылки по кнопке «Рассылка» в меню админа."""
    if message.from_user is None or not is_admin(message.from_user.id, message.bot):
        return
    bot = message.bot
    chat_id = message.chat.id
    await delete_tracked_admin_messages(bot, chat_id)
    await state.set_state(BroadcastForm.content)
    msg = await message.answer(
        TEXTS["broadcast_prompt"],
        reply_markup=_build_broadcast_cancel_keyboard(),
    )
    track_admin_messages(bot, chat_id, [msg.message_id])


@router.message(F.text == KB_TEXTS["menu_more"])
async def handle_admin_more_message(message: Message) -> None:
    """Меняет нижнее меню на дополнительные действия по кнопке «Ещё»."""
    if message.from_user is None or not is_admin(message.from_user.id, message.bot):
        return
    bot = message.bot
    chat_id = message.chat.id
    await delete_tracked_admin_messages(bot, chat_id)
    msg = await message.answer(
        "Дополнительные действия:",
        reply_markup=build_admin_more_reply_keyboard(),
    )
    track_admin_messages(bot, chat_id, [msg.message_id])


@router.message(F.text == KB_TEXTS["menu_admin_contact"])
async def handle_admin_contact_message(
    message: Message,
    state: FSMContext,
) -> None:
    """Запуск редактирования контактов для клиентов по кнопке «Контакт для клиентов»."""
    if message.from_user is None or not is_admin(message.from_user.id, message.bot):
        return
    db = get_db_from_message(message)
    contacts = db.get_admin_contacts()
    await state.set_state(AdminContactForm.contact)
    bot = message.bot
    chat_id = message.chat.id
    await delete_tracked_admin_messages(bot, chat_id)
    m1 = await message.answer(_format_admin_contacts_list(contacts))
    m2 = await message.answer(TEXTS["contact_edit_prompt"])
    track_admin_messages(bot, chat_id, [m1.message_id, m2.message_id])


@router.message(F.text == KB_TEXTS["menu_sync_catalog"])
async def handle_admin_sync_catalog_message(message: Message) -> None:
    """Запускает синхронизацию каталога по кнопке «Обновить каталог» в нижнем меню."""
    if message.from_user is None or not is_admin(message.from_user.id, message.bot):
        return
    db = get_db_from_message(message)
    bot = message.bot
    chat_id = message.chat.id
    await delete_tracked_admin_messages(bot, chat_id)
    m1 = await message.answer(TEXTS["sync_catalog_start"])
    try:
        summary = await sync_catalog_from_opencart(db)
        summary_text = _format_catalog_summary(summary)
        m2 = await message.answer(TEXTS["sync_catalog_ok"].format(summary=summary_text))
        track_admin_messages(bot, chat_id, [m1.message_id, m2.message_id])
    except Exception as e:
        logger.exception("Ошибка синхронизации каталога по запросу админа")
        m2 = await message.answer(
            TEXTS["sync_catalog_fail"].format(error=str(e)),
        )
        track_admin_messages(bot, chat_id, [m1.message_id, m2.message_id])


@router.message(F.text == KB_TEXTS["menu_users"])
async def handle_admin_users_message(message: Message) -> None:
    """Показывает список пользователей по кнопке «Пользователи» в нижнем меню."""
    if message.from_user is None or not is_admin(message.from_user.id, message.bot):
        return
    bot = message.bot
    chat_id = message.chat.id
    await delete_tracked_admin_messages(bot, chat_id)
    db = get_db_from_message(message)
    total = db.count_users()
    limit = 50
    users = db.list_users(limit=limit)
    text = _format_users_message(users, total, limit)
    msg = await message.answer(text)
    track_admin_messages(bot, chat_id, [msg.message_id])


@router.message(F.text == KB_TEXTS["menu_bot_status"])
async def handle_admin_bot_status_message(message: Message) -> None:
    """Показывает PID, uptime, systemd и хвост лога по кнопке «Статус бота».

    Args:
        message (Message): Входящее сообщение от администратора.

    Returns:
        None: Ничего не возвращает.
    """
    if message.from_user is None or not is_admin(message.from_user.id, message.bot):
        return
    bot = message.bot
    chat_id = message.chat.id
    await delete_tracked_admin_messages(bot, chat_id)
    try:
        html = await build_bot_status_html()
        msg = await message.answer(html, parse_mode="HTML")
        track_admin_messages(bot, chat_id, [msg.message_id])
    except Exception:
        logger.exception("Ошибка при формировании статуса бота для админа")
        msg = await message.answer(TEXTS["bot_status_error"])
        track_admin_messages(bot, chat_id, [msg.message_id])


@router.message(F.text == KB_TEXTS["menu_admins"])
async def handle_admin_admins_message(message: Message) -> None:
    """Показывает список администраторов и кнопки добавить/удалить."""
    if message.from_user is None or not is_admin(message.from_user.id, message.bot):
        return
    bot = message.bot
    chat_id = message.chat.id
    await delete_tracked_admin_messages(bot, chat_id)
    db = get_db_from_message(message)
    text = _format_admins_message(message.bot, db)
    can_manage = _is_superadmin(message.from_user.id, message.bot)
    msg = await message.answer(
        text,
        reply_markup=build_admin_admins_keyboard(can_manage=can_manage),
    )
    track_admin_messages(bot, chat_id, [msg.message_id])


@router.message(F.text == KB_TEXTS["back"])
async def handle_admin_back_reply(message: Message) -> None:
    """Возврат в главное меню по кнопке «Назад» в нижнем меню (доп. действия)."""
    if message.from_user is None:
        return
    await _show_main_menu_after_back(message, user_id=message.from_user.id)


@router.message(F.text == KB_TEXTS["admin_orders_export"])
async def handle_admin_export_orders_start(message: Message) -> None:
    """Запрос периода выгрузки заказов в CSV."""
    if message.from_user is None or not is_admin(message.from_user.id, message.bot):
        return
    await _show_export_orders_prompt(message, user_id=message.from_user.id)


@router.callback_query(F.data.startswith("admin:export_orders:"))
async def handle_admin_export_orders_period(callback: CallbackQuery) -> None:
    """Формирует CSV заказов за выбранный период и отправляет файлом."""
    await callback.answer()
    if callback.message is None or callback.from_user is None:
        return
    if not _is_superadmin(callback.from_user.id, callback.bot):
        await callback.answer("Только главный администратор может управлять списком админов.", show_alert=True)
        return
    period = (callback.data or "").split(":")[-1]
    now = datetime.now(UTC)
    if period == "today":
        from_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        to_dt = now
        label = "today"
    elif period == "week":
        from_dt = now - timedelta(days=7)
        to_dt = now
        label = "week"
    elif period == "month":
        from_dt = now - timedelta(days=30)
        to_dt = now
        label = "month"
    else:
        await callback.message.answer("Неизвестный период.")
        return
    db = get_db_from_callback(callback)
    orders = db.list_orders_between(from_dt=from_dt, to_dt=to_dt)
    if not orders:
        await callback.message.answer(TEXTS["export_orders_empty"])
        return
    csv_bytes = _orders_to_csv(orders)
    date_str = now.strftime("%Y-%m-%d")
    filename = f"orders_{label}_{date_str}.csv"
    doc = BufferedInputFile(csv_bytes, filename=filename)
    await callback.message.answer_document(document=doc)
    await callback.message.answer(TEXTS["export_orders_done"].format(count=len(orders)))


@router.callback_query(F.data.startswith("admin:order_message:"))
async def handle_admin_order_message_start(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Запускает сценарий отправки сообщения клиенту по заказу."""
    await callback.answer()
    if callback.message is None or callback.from_user is None:
        return
    if not _is_superadmin(callback.from_user.id, callback.bot):
        await callback.answer("Только главный администратор может управлять списком админов.", show_alert=True)
        return
    prefix = "admin:order_message:"
    raw = callback.data or ""
    if not raw.startswith(prefix):
        return
    try:
        order_id = int(raw[len(prefix) :].strip())
    except ValueError:
        return
    db = get_db_from_callback(callback)
    order = db.get_order(order_id=order_id)
    if order is None:
        await callback.message.answer("Заказ не найден.")
        return
    await state.set_state(AdminOrderMessageForm.message)
    prompt_msg = await callback.message.answer(
        TEXTS["order_message_prompt"].format(display_number=order.display_order_number),
    )
    await state.update_data(
        admin_order_message_order_id=order_id,
        admin_order_message_prompt_message_id=prompt_msg.message_id,
    )


@router.message(AdminOrderMessageForm.message)
async def handle_admin_order_message_text(
    message: Message,
    state: FSMContext,
) -> None:
    """Отправляет введённое сообщение клиенту по заказу с инфой о заказе и контактом админа."""
    if message.from_user is None or not _is_superadmin(message.from_user.id, message.bot):
        await state.clear()
        return
    text = (message.text or "").strip()
    if not text:
        await message.answer(TEXTS["order_message_prompt"].format(display_number="…"))
        return
    data = await state.get_data()
    order_id = data.get("admin_order_message_order_id")
    await state.clear()
    if order_id is None:
        await message.answer("Сессия сброшена. Выберите заказ и нажмите «Написать клиенту» снова.")
        return
    db = get_db_from_message(message)
    order = db.get_order(order_id=order_id)
    if order is None:
        await message.answer("Заказ не найден.")
        return
    user_tg_id = db.get_user_tg_id(user_id=order.user_id)
    if user_tg_id is None:
        await message.answer(TEXTS["order_message_no_user"])
        return
    admin_contacts = db.get_admin_contacts()
    if admin_contacts:
        contacts_block = "\n".join(admin_contacts)
        body = TEXTS["client_message_header"].format(
            display_number=order.display_order_number,
            admin_message=text,
            admin_contacts=contacts_block,
        )
    else:
        body = TEXTS["client_message_no_contact"].format(
            display_number=order.display_order_number,
            admin_message=text,
        )
    try:
        await message.bot.send_message(chat_id=user_tg_id, text=body)
    except Exception as e:
        logger.warning(
            "Не удалось отправить сообщение клиенту tg_id={tg_id}: {err}",
            tg_id=user_tg_id,
            err=e,
        )
        await message.answer("Не удалось доставить сообщение клиенту.")
        return

    confirm_msg = await message.answer(TEXTS["order_message_sent"])
    prompt_message_id = data.get("admin_order_message_prompt_message_id")
    chat_id = message.chat.id
    await asyncio.sleep(3)
    for mid in (prompt_message_id, message.message_id, confirm_msg.message_id):
        if mid is None:
            continue
        try:
            await message.bot.delete_message(chat_id=chat_id, message_id=mid)
        except TelegramBadRequest as e:
            logger.debug(
                "Не удалось удалить сообщение в чате админа (message_id={mid}): {err}",
                mid=mid,
                err=e,
            )


def _format_admin_contacts_list(contacts: list[str]) -> str:
    """Форматирует нумерованный список контактов для сообщения админу."""
    if not contacts:
        return TEXTS["contact_edit_empty"]
    lines = [f"{i}. {c}" for i, c in enumerate(contacts, 1)]
    return TEXTS["contact_edit_list"].format(
        count=len(contacts),
        list="\n".join(lines),
    )


@router.callback_query(F.data == "admin:contact_edit")
async def handle_admin_contact_edit_start(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Запрашивает у админа контакты для связи с клиентами (до 5)."""
    await callback.answer()
    if callback.message is None or callback.from_user is None:
        return
    if not _is_superadmin(callback.from_user.id, callback.bot):
        await callback.answer("Только главный администратор может управлять списком админов.", show_alert=True)
        return
    db = get_db_from_callback(callback)
    contacts = db.get_admin_contacts()
    await state.set_state(AdminContactForm.contact)
    await callback.message.answer(_format_admin_contacts_list(contacts))
    await callback.message.answer(TEXTS["contact_edit_prompt"])


@router.message(AdminContactForm.contact)
async def handle_admin_contact_edit_text(
    message: Message,
    state: FSMContext,
) -> None:
    """Обрабатывает ввод: добавление контакта, удаление по номеру, очистка или выход."""
    if message.from_user is None or not is_admin(message.from_user.id, message.bot):
        await state.clear()
        return
    raw = (message.text or "").strip()
    db = get_db_from_message(message)
    contacts = db.get_admin_contacts()

    if raw.lower() in ("готово", "отмена"):
        await state.clear()
        await message.answer(
            TEXTS["contact_edit_done"] if raw.lower() == "готово" else TEXTS["contact_edit_cancelled"],
        )
        return

    if raw.lower() == "очистить":
        db.set_admin_contacts([])
        await state.clear()
        await message.answer(TEXTS["contact_edit_cleared"])
        return

    delete_prefix = "удалить "
    if raw.lower().startswith(delete_prefix):
        if not contacts:
            await message.answer(TEXTS["contact_edit_empty"])
            await message.answer(TEXTS["contact_edit_prompt"])
            return
        num_str = raw[len(delete_prefix) :].strip()
        try:
            idx = int(num_str)
        except ValueError:
            idx = 0
        if 1 <= idx <= len(contacts):
            contacts.pop(idx - 1)
            db.set_admin_contacts(contacts)
            await message.answer(TEXTS["contact_edit_removed"])
            await message.answer(_format_admin_contacts_list(db.get_admin_contacts()))
            await message.answer(TEXTS["contact_edit_prompt"])
        else:
            await message.answer(
                TEXTS["contact_edit_bad_delete"].format(max=len(contacts) or 1),
            )
            await message.answer(TEXTS["contact_edit_prompt"])
        return

    if not raw:
        await message.answer(_format_admin_contacts_list(contacts))
        await message.answer(TEXTS["contact_edit_prompt"])
        return

    if len(contacts) >= Database.ADMIN_CONTACTS_MAX:
        await message.answer(TEXTS["contact_edit_full"])
        await message.answer(TEXTS["contact_edit_prompt"])
        return

    contacts.append(raw)
    db.set_admin_contacts(contacts)
    await message.answer(TEXTS["contact_edit_added"])
    await message.answer(_format_admin_contacts_list(db.get_admin_contacts()))
    await message.answer(TEXTS["contact_edit_prompt"])


def _format_users_message(users: list[User], total: int, limit: int) -> str:
    """Формирует текст сообщения со списком пользователей (до 4096 символов)."""
    if not users:
        return TEXTS["users_empty"]
    lines: list[str] = []
    for u in users:
        name = " ".join(filter(None, [u.first_name, u.last_name])).strip() or "—"
        phone = u.phone or "—"
        lines.append(TEXTS["users_line"].format(id=u.id, tg_id=u.tg_id, name=name, phone=phone))
    text = TEXTS["users_header"].format(total=total) + "\n\n" + "\n".join(lines)
    if total > limit:
        text += TEXTS["users_more"].format(limit=limit, total=total)
    if len(text) > 4000:
        text = text[:3997] + "\n…"
    return text


def _orders_to_csv(orders: list[Order]) -> bytes:
    """Формирует CSV с заказами в кодировке UTF-8 с BOM для Excel.

    Args:
        orders: Список заказов с позициями.

    Returns:
        bytes: Содержимое CSV-файла.
    """
    buffer = io.StringIO()
    buffer.write("\ufeff")  # BOM для Excel
    writer = csv.writer(buffer, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(
        [
            "Номер заказа",
            "Дата",
            "Статус",
            "Получатель",
            "Телефон",
            "Email",
            "Город",
            "Адрес",
            "Желаемая дата доставки",
            "Комментарий",
            "Товары",
            "Доставка (₽)",
            "Итого (₽)",
            "Способ оплаты",
        ]
    )
    for order in orders:
        items_str = "; ".join(f"{item.product.title} × {item.quantity}" for item in order.items)
        payment = (order.payment_method or "—").strip()
        if payment == "cash":
            payment = "Наличные"
        elif payment == "yookassa":
            payment = "ЮКassa"
        writer.writerow(
            [
                order.display_order_number,
                order.created_at.strftime("%d.%m.%Y %H:%M"),
                order.status.human_readable,
                order.customer_name,
                order.phone,
                order.email or "—",
                order.delivery_city or "—",
                order.delivery_address,
                order.desired_delivery_datetime or "—",
                (order.comment or "—").replace("\n", " "),
                items_str,
                order.delivery_cost,
                order.total_amount,
                payment,
            ]
        )
    return buffer.getvalue().encode("utf-8")


def _format_stats_detailed(db: Database) -> str:
    """Формирует расширенный текст статистики: базовая + топ товаров, выручка по городам, заказы по статусам."""
    stats = db.get_stats()
    text = TEXTS["stats"].format(
        users=stats.total_users,
        orders=stats.total_orders,
        revenue=stats.total_revenue,
    )
    top = db.get_top_products_by_sales(limit=5)
    if top:
        text += "\n\n<b>Топ-5 товаров по продажам:</b>\n"
        for i, (title, qty) in enumerate(top, 1):
            short = (title[:50] + "…") if len(title) > 50 else title
            text += f"{i}. {short} — {qty} шт.\n"
    by_city = db.get_revenue_by_city()
    if by_city:
        text += "\n<b>Выручка по городам:</b>\n"
        for city, revenue in by_city:
            text += f"• {city}: {revenue} ₽\n"
    by_status = db.get_orders_count_by_status()
    if by_status:
        text += "\n<b>Заказы по статусам:</b>\n"
        for status, cnt in by_status:
            text += f"• {status.human_readable}: {cnt}\n"
    return text


@router.callback_query(F.data == "admin:users")
async def handle_admin_users(callback: CallbackQuery) -> None:
    """Показывает список пользователей бота по кнопке «Пользователи» в меню «Ещё»."""
    await callback.answer()
    if callback.message is None or callback.from_user is None:
        return
    if not _is_superadmin(callback.from_user.id, callback.bot):
        await callback.answer("Только главный администратор может управлять списком админов.", show_alert=True)
        return
    db = get_db_from_callback(callback)
    total = db.count_users()
    limit = 50
    users = db.list_users(limit=limit)
    text = _format_users_message(users, total, limit)
    keyboard = InlineKeyboardBuilder()
    keyboard.row(
        InlineKeyboardButton(
            text=KB_TEXTS["back"],
            callback_data="admin:back_more",
        ),
    )
    try:
        await callback.message.edit_text(
            text=text,
            reply_markup=keyboard.as_markup(),
        )
    except TelegramBadRequest:
        await callback.message.answer(
            text=text,
            reply_markup=keyboard.as_markup(),
        )


@router.callback_query(F.data == "admin:back_more")
@router.callback_query(F.data == "admin:more")
async def handle_admin_back_more(callback: CallbackQuery) -> None:
    """Возврат в меню «Дополнительные действия» (Ещё)."""
    await callback.answer()
    if callback.message is None or callback.from_user is None:
        return
    if not _is_superadmin(callback.from_user.id, callback.bot):
        await callback.answer("Только главный администратор может управлять списком админов.", show_alert=True)
        return
    try:
        await callback.message.edit_text(
            text="Дополнительные действия:",
            reply_markup=build_admin_more_keyboard(),
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data == "admin:admins")
async def handle_admin_admins_callback(callback: CallbackQuery) -> None:
    """Показывает список администраторов по кнопке «Администраторы» в меню «Ещё»."""
    await callback.answer()
    if callback.message is None or callback.from_user is None:
        return
    if not is_admin(callback.from_user.id, callback.bot):
        return
    db = get_db_from_callback(callback)
    text = _format_admins_message(callback.bot, db)
    can_manage = _is_superadmin(callback.from_user.id, callback.bot)
    try:
        await callback.message.edit_text(
            text=text,
            reply_markup=build_admin_admins_keyboard(can_manage=can_manage),
        )
    except TelegramBadRequest:
        await callback.message.answer(
            text=text,
            reply_markup=build_admin_admins_keyboard(can_manage=can_manage),
        )


@router.callback_query(F.data == "admin:admin_add")
async def handle_admin_admin_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Запускает сценарий добавления администратора по Telegram ID."""
    await callback.answer()
    if callback.message is None or callback.from_user is None:
        return
    if not is_admin(callback.from_user.id, callback.bot):
        return
    await state.set_state(AdminAdminsForm.add_id)
    await callback.message.answer(TEXTS["admins_add_prompt"])


@router.message(AdminAdminsForm.add_id, F.text)
async def handle_admin_admin_add_id(message: Message, state: FSMContext) -> None:
    """Обрабатывает введённый Telegram ID и добавляет пользователя в список админов."""
    if message.from_user is None or not is_admin(message.from_user.id, message.bot):
        await state.clear()
        return
    text = (message.text or "").strip()
    await state.clear()
    if not text or not text.isdigit():
        await message.answer("Введите одно целое число (Telegram ID).")
        return
    user_id = int(text)
    if user_id <= 0:
        await message.answer("Telegram ID должен быть положительным числом.")
        return
    db = get_db_from_message(message)
    added = db.add_bot_admin(user_id)
    _refresh_bot_admin_ids(message.bot, db)
    if added:
        await message.answer(TEXTS["admins_add_ok"].format(user_id=user_id))
    else:
        await message.answer(TEXTS["admins_add_already"].format(user_id=user_id))


@router.callback_query(F.data == "admin:admin_remove")
async def handle_admin_admin_remove_list(callback: CallbackQuery) -> None:
    """Показывает список админов из БД для удаления (админов из env удалить нельзя)."""
    await callback.answer()
    if callback.message is None or callback.from_user is None:
        return
    if not is_admin(callback.from_user.id, callback.bot):
        return
    db = get_db_from_callback(callback)
    db_ids = db.list_bot_admin_ids()
    if not db_ids:
        try:
            await callback.message.edit_text(
                text=TEXTS["admins_remove_empty"],
                reply_markup=InlineKeyboardBuilder()
                .row(
                    InlineKeyboardButton(
                        text=KB_TEXTS["back"],
                        callback_data="admin:admins",
                    ),
                )
                .as_markup(),
            )
        except TelegramBadRequest:
            await callback.message.answer(
                TEXTS["admins_remove_empty"],
                reply_markup=build_admin_admins_keyboard(),
            )
        return
    text = TEXTS["admins_remove_only_db"] + "\n\nВыберите, кого удалить:"
    try:
        await callback.message.edit_text(
            text=text,
            reply_markup=build_admin_remove_admins_keyboard(db_ids),
        )
    except TelegramBadRequest:
        await callback.message.answer(
            text=text,
            reply_markup=build_admin_remove_admins_keyboard(db_ids),
        )


@router.callback_query(F.data.startswith("admin:admin_remove:"))
async def handle_admin_admin_remove_do(callback: CallbackQuery) -> None:
    """Удаляет выбранного администратора из таблицы bot_admins и обновляет bot.admin_ids."""
    await callback.answer()
    if callback.message is None or callback.from_user is None:
        return
    if not is_admin(callback.from_user.id, callback.bot):
        return
    prefix = "admin:admin_remove:"
    raw = (callback.data or "").strip()
    if not raw.startswith(prefix):
        return
    try:
        user_id = int(raw[len(prefix) :].strip())
    except ValueError:
        return
    db = get_db_from_callback(callback)
    removed = db.remove_bot_admin(user_id)
    _refresh_bot_admin_ids(callback.bot, db)
    if removed:
        await callback.message.answer(TEXTS["admins_remove_ok"].format(user_id=user_id))
    text = _format_admins_message(callback.bot, db)
    try:
        await callback.message.edit_text(
            text=text,
            reply_markup=build_admin_admins_keyboard(),
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data == "admin:sync_catalog")
async def handle_admin_sync_catalog(callback: CallbackQuery) -> None:
    """Запускает синхронизацию каталога из OpenCart по кнопке в админке.

    Тестовый товар и старые записи деактивируются; активными остаются
    только товары, подтянутые из БД сайта.
    """
    await callback.answer()
    if callback.message is None or callback.from_user is None:
        return
    if not is_admin(callback.from_user.id, callback.bot):
        return
    db = get_db_from_callback(callback)
    await callback.message.answer(TEXTS["sync_catalog_start"])
    try:
        summary = await sync_catalog_from_opencart(db)
        summary_text = _format_catalog_summary(summary)
        await callback.message.answer(TEXTS["sync_catalog_ok"].format(summary=summary_text))
    except Exception as e:
        logger.exception("Ошибка синхронизации каталога по запросу админа")
        await callback.message.answer(
            TEXTS["sync_catalog_fail"].format(error=str(e)),
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
async def handle_admin_back(callback: CallbackQuery) -> None:
    """Возврат в главное меню админки (редактирование сообщения)."""

    await callback.answer()
    if callback.message is None or callback.from_user is None:
        return
    if not is_admin(callback.from_user.id, callback.bot):
        return

    main_kb = build_admin_main_keyboard()
    try:
        await callback.message.edit_text(
            text=TEXTS["entry"],
            reply_markup=main_kb,
        )
    except TelegramBadRequest as err:
        logger.debug("Не удалось обновить главное меню админа: {err}", err=err)
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


@router.callback_query(F.data == "admin:orders")
async def handle_admin_orders_callback(callback: CallbackQuery) -> None:
    """Показ списка заказов из сообщения админ-панели (редактирование сообщения)."""

    await callback.answer()
    if callback.message is None or callback.from_user is None:
        return
    if not is_admin(callback.from_user.id, callback.bot):
        return

    await _show_admin_orders_screen(callback.message, user_id=callback.from_user.id)


@router.callback_query(F.data == "admin:orders_search")
async def handle_admin_orders_search_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Запрашивает у администратора критерий поиска заказа (номер или телефон)."""

    await callback.answer()
    if callback.message is None or callback.from_user is None:
        return
    if not is_admin(callback.from_user.id, callback.bot):
        return

    # Поддержка старого сценария через инлайн-кнопку (если где-то осталась)
    await state.set_state(AdminOrderSearchForm.query)
    try:
        await callback.message.edit_text(
            text=TEXTS["orders_search_prompt"],
            reply_markup=None,
        )
    except TelegramBadRequest:
        await callback.message.answer(TEXTS["orders_search_prompt"])


async def _send_filtered_orders(
    message: Message,
    *,
    statuses: list[OrderStatus],
    header_text: str,
    user_id: int | None = None,
) -> None:
    """Отправляет администратору список заказов по указанным статусам (один экран в слоте админки).

    Args:
        message (Message): Чат для ответа.
        statuses (list[OrderStatus]): Фильтр по статусам.
        header_text (str): Заголовок блока.
        user_id (int | None): Telegram ID администратора; если None — из message.from_user.

    Returns:
        None: Ничего не возвращает.
    """

    uid = user_id if user_id is not None else (message.from_user.id if message.from_user else None)
    if uid is None or not is_admin(uid, message.bot):
        return

    bot = message.bot
    chat_id = message.chat.id
    await delete_tracked_admin_messages(bot, chat_id)

    db = get_db_from_message(message)
    summaries = db.list_orders_by_statuses(statuses=statuses)
    text = header_text
    if not summaries:
        text += "\n\nПока нет заказов с таким статусом."

    msg = await message.answer(
        text=text,
        reply_markup=build_admin_orders_keyboard(
            orders=summaries,
            page=0,
            page_size=len(summaries) or 1,
            total_count=len(summaries),
        ),
    )
    track_admin_messages(bot, chat_id, [msg.message_id])


@router.message(AdminOrderSearchForm.query)
async def handle_admin_orders_search_query(
    message: Message,
    state: FSMContext,
) -> None:
    """Обрабатывает ввод строки поиска или кнопки клавиатуры в разделе заказов."""

    if message.from_user is None or not is_admin(message.from_user.id, message.bot):
        await state.clear()
        return

    uid = message.from_user.id
    t = (message.text or "").strip()

    # Кнопки нижней клавиатуры раздела «Заказы»: выход из режима поиска (раньше снова показывали только промпт).
    if t == KB_TEXTS["admin_orders_new"]:
        await state.clear()
        await _send_filtered_orders(
            message,
            statuses=[
                OrderStatus.NEW,
                OrderStatus.AWAITING_PAYMENT,
                OrderStatus.PROCESSING,
                OrderStatus.ASSEMBLING,
            ],
            header_text=TEXTS["orders_filter_new"],
            user_id=uid,
        )
        return
    if t == KB_TEXTS["admin_orders_delivery"]:
        await state.clear()
        await _send_filtered_orders(
            message,
            statuses=[OrderStatus.COURIER],
            header_text=TEXTS["orders_filter_delivery"],
            user_id=uid,
        )
        return
    if t == KB_TEXTS["admin_orders_paid"]:
        await state.clear()
        await _send_filtered_orders(
            message,
            statuses=[
                OrderStatus.PAID,
                OrderStatus.DELIVERED,
            ],
            header_text=TEXTS["orders_filter_paid"],
            user_id=uid,
        )
        return
    if t == KB_TEXTS["menu_orders"]:
        await state.clear()
        await _show_admin_orders_screen(message, user_id=uid)
        return
    if t == KB_TEXTS["admin_orders_export"]:
        await state.clear()
        await _show_export_orders_prompt(message, user_id=uid)
        return
    if t == KB_TEXTS["admin_orders_search"]:
        await state.set_state(AdminOrderSearchForm.query)
        await delete_tracked_admin_messages(message.bot, message.chat.id)
        msg = await message.answer(TEXTS["orders_search_prompt"])
        track_admin_messages(message.bot, message.chat.id, [msg.message_id])
        return
    if t == KB_TEXTS["back"]:
        await state.clear()
        await _show_main_menu_after_back(message, user_id=uid)
        return

    raw_query = t
    if not raw_query:
        await delete_tracked_admin_messages(message.bot, message.chat.id)
        msg = await message.answer(TEXTS["orders_search_prompt"])
        track_admin_messages(message.bot, message.chat.id, [msg.message_id])
        return

    db = get_db_from_message(message)

    # Пытаемся определить, ищет ли админ по телефону или по номеру заказа.
    digits_only = re.sub(r"\D", "", raw_query)

    # Телефон: 10+ цифр после нормализации.
    phone_normalized = normalize_phone(raw_query)
    if phone_normalized is not None and len(digits_only) >= 10:
        summaries = db.find_orders_by_phone(phone=phone_normalized)
    else:
        # Номер заказа: 3–6 цифр (отображаемый 4‑значный номер).
        if not digits_only or not (3 <= len(digits_only) <= 6):
            await delete_tracked_admin_messages(message.bot, message.chat.id)
            msg = await message.answer(TEXTS["orders_search_prompt"])
            track_admin_messages(message.bot, message.chat.id, [msg.message_id])
            return
        try:
            display_number = int(digits_only)
        except ValueError:
            await delete_tracked_admin_messages(message.bot, message.chat.id)
            msg = await message.answer(TEXTS["orders_search_prompt"])
            track_admin_messages(message.bot, message.chat.id, [msg.message_id])
            return
        summaries = db.find_orders_by_display_number(display_order_number=display_number)

    await state.clear()

    bot = message.bot
    chat_id = message.chat.id
    await delete_tracked_admin_messages(bot, chat_id)

    if not summaries:
        msg = await message.answer(
            TEXTS["orders_search_not_found"].format(query=raw_query),
            parse_mode="HTML",
        )
        track_admin_messages(bot, chat_id, [msg.message_id])
        return

    msg = await message.answer(
        TEXTS["orders_search_results"].format(query=raw_query),
        reply_markup=build_admin_orders_keyboard(
            orders=summaries,
            page=0,
            page_size=len(summaries) or 1,
            total_count=len(summaries),
        ),
        parse_mode="HTML",
    )
    track_admin_messages(bot, chat_id, [msg.message_id])


@router.message(F.text == KB_TEXTS["admin_orders_new"])
async def handle_admin_orders_filter_new(message: Message) -> None:
    """Фильтр заказов: новые и в работе."""

    await _send_filtered_orders(
        message,
        statuses=[
            OrderStatus.NEW,
            OrderStatus.AWAITING_PAYMENT,
            OrderStatus.PROCESSING,
            OrderStatus.ASSEMBLING,
        ],
        header_text=TEXTS["orders_filter_new"],
    )


@router.message(F.text == KB_TEXTS["admin_orders_delivery"])
async def handle_admin_orders_filter_delivery(message: Message) -> None:
    """Фильтр заказов: в доставке (у курьера)."""

    await _send_filtered_orders(
        message,
        statuses=[OrderStatus.COURIER],
        header_text=TEXTS["orders_filter_delivery"],
    )


@router.message(F.text == KB_TEXTS["admin_orders_paid"])
async def handle_admin_orders_filter_paid(message: Message) -> None:
    """Фильтр заказов: оплаченные и доставленные."""

    await _send_filtered_orders(
        message,
        statuses=[
            OrderStatus.PAID,
            OrderStatus.DELIVERED,
        ],
        header_text=TEXTS["orders_filter_paid"],
    )


@router.callback_query(F.data.startswith("admin:orders_page:"))
async def handle_admin_orders_page(callback: CallbackQuery) -> None:
    """Обрабатывает нажатие кнопок пагинации списка заказов."""

    await callback.answer()
    if callback.message is None or callback.from_user is None:
        return
    if not is_admin(callback.from_user.id, callback.bot):
        return

    prefix = "admin:orders_page:"
    raw = callback.data or ""
    if not raw.startswith(prefix):
        return
    try:
        page = int(raw[len(prefix) :])
    except ValueError:
        return
    if page < 0:
        page = 0

    db = get_db_from_callback(callback)
    total = db.count_orders()
    max_page = max((total - 1) // RECENT_ORDERS_LIMIT, 0) if total > 0 else 0
    if page > max_page:
        page = max_page

    offset = page * RECENT_ORDERS_LIMIT
    orders = db.list_orders_page(limit=RECENT_ORDERS_LIMIT, offset=offset)

    text = TEXTS["orders_header"]
    if not orders:
        text += "\n\nПока нет заказов."

    try:
        await callback.message.edit_text(
            text=text,
            reply_markup=build_admin_orders_keyboard(
                orders=orders,
                page=page,
                page_size=RECENT_ORDERS_LIMIT,
                total_count=total,
            ),
        )
    except TelegramBadRequest as err:
        if "message is not modified" not in str(err).lower():
            logger.debug("Ошибка при переключении страницы заказов: {err}", err=err)


@router.callback_query(F.data.startswith("admin:order:cancel:"))
async def handle_admin_order_cancel(callback: CallbackQuery) -> None:
    """Отменяет заказ по нажатию кнопки в админ-панели и обновляет сообщение.

    Args:
        callback (CallbackQuery): Callback-запрос администратора.

    Returns:
        None: Ничего не возвращает.
    """

    await callback.answer()
    if callback.message is None or callback.from_user is None:
        return
    if not is_admin(callback.from_user.id, callback.bot):
        return

    _, _, _, order_id_str = callback.data.split(":", maxsplit=3)
    order_id = int(order_id_str)

    db = get_db_from_callback(callback)
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
    if callback.message is None or callback.from_user is None:
        return
    if not is_admin(callback.from_user.id, callback.bot):
        return

    db = get_db_from_callback(callback)
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
    if callback.message is None or callback.from_user is None:
        return
    if not is_admin(callback.from_user.id, callback.bot):
        return

    db = get_db_from_callback(callback)
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
    if callback.message is None or callback.from_user is None:
        return
    if not is_admin(callback.from_user.id, callback.bot):
        return

    db = get_db_from_callback(callback)
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
    if callback.message is None or callback.from_user is None:
        return
    if not is_admin(callback.from_user.id, callback.bot):
        return

    db = get_db_from_callback(callback)
    text = _format_stats_detailed(db)
    try:
        await callback.message.edit_text(text=text, parse_mode="HTML")
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
    """Запрашивает контент рассылки: админ отправит фото с подписью или текст."""
    await callback.answer()
    if callback.message is None or callback.from_user is None:
        return
    if not is_admin(callback.from_user.id, callback.bot):
        return

    await state.set_state(BroadcastForm.content)
    await callback.message.answer(
        TEXTS["broadcast_prompt"],
        reply_markup=_build_broadcast_cancel_keyboard(),
    )


@router.message(BroadcastForm.content, F.photo)
async def handle_broadcast_content_photo(message: Message, state: FSMContext) -> None:
    """Принимает фото с подписью (или без) для рассылки."""
    if message.from_user is None or not is_admin(message.from_user.id, message.bot):
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
    if message.from_user is None or not is_admin(message.from_user.id, message.bot):
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
    if message.from_user is None or not is_admin(message.from_user.id, message.bot):
        return
    await message.answer(TEXTS["broadcast_no_content"])


@router.callback_query(F.data == "admin:broadcast_confirm")
async def handle_broadcast_confirm(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Отправляет рассылку всем пользователям по сохранённому контенту."""
    await callback.answer()
    if callback.message is None or callback.from_user is None:
        return
    if not is_admin(callback.from_user.id, callback.bot):
        return

    data = await state.get_data()
    photo_file_id = data.get("broadcast_photo_file_id")
    text = (data.get("broadcast_text") or "").strip()
    if not text and not photo_file_id:
        await callback.message.answer(TEXTS["broadcast_no_content"])
        await state.clear()
        return

    db = get_db_from_callback(callback)
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
            # Лимитируем скорость отправки (~20 сообщений/сек).
            await asyncio.sleep(0.05)
        except TelegramRetryAfter as err:
            logger.warning(
                "TelegramRetryAfter при рассылке, пауза {retry}s",
                retry=err.retry_after,
            )
            await asyncio.sleep(err.retry_after)
        except TelegramForbiddenError:
            logger.warning(
                "Пользователь tg_id={tg_id} заблокировал бота, пропускаем",
                tg_id=tg_id,
            )
        except Exception as err:
            logger.exception(
                "Не удалось отправить рассылку пользователю tg_id={tg_id}: {err}",
                tg_id=tg_id,
                err=err,
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
    base_photo = _first_order_photo_url(order)
    keyboard = build_admin_order_details_keyboard(
        order_id=order.id,
        current_status=order.status,
    )

    for admin_tg_id in admin_ids:
        try:
            sent = None
            has_photo = False
            if base_photo:
                try:
                    sent = await bot.send_photo(
                        chat_id=admin_tg_id,
                        photo=base_photo,
                        caption=full_text,
                        reply_markup=keyboard,
                    )
                    has_photo = True
                except TelegramBadRequest as err:
                    logger.warning(
                        "Не удалось отправить фото заказа id={order_id} админу tg_id={tg_id}: {err}",
                        order_id=order.id,
                        tg_id=admin_tg_id,
                        err=err,
                    )
            if sent is None:
                sent = await bot.send_message(
                    chat_id=admin_tg_id,
                    text=full_text,
                    reply_markup=keyboard,
                )
            if db is not None and sent is not None:
                db.save_admin_order_notification(
                    order_id=order.id,
                    admin_tg_id=admin_tg_id,
                    chat_id=admin_tg_id,
                    message_id=sent.message_id,
                    has_photo=has_photo,
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
