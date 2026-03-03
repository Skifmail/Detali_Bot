from __future__ import annotations

import calendar as cal
from collections.abc import Iterable
from datetime import date, datetime
from datetime import time as time_type

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from ..database.models import (
    Category,
    OrderStatus,
    OrderSummary,
    Product,
    SavedRecipient,
)

TEXTS: dict[str, str] = {
    "menu_catalog": "🌸 Каталог",
    "menu_cart": "🛒 Корзина",
    "menu_account": "👤 Кабинет",
    "menu_admin": "⚙️ Админ",
    "menu_orders": "📦 Заказы",
    "menu_stats": "📊 Статистика",
    "menu_broadcast": "📣 Рассылка",
    "menu_more": "Ещё",
    "menu_sync_catalog": "🔄 Обновить каталог",
    "menu_users": "👥 Пользователи",
    "back": "← Назад",
    "start_over": "🏠 Главное меню",
    "cart_checkout": "🧾 Оформить заказ",
}


def build_main_menu_keyboard(is_admin: bool = False) -> ReplyKeyboardMarkup:
    """Создаёт клавиатуру главного меню.

    Для администратора: только Каталог и Админ-панель (без Корзины и Кабинета).
    У обычных пользователей: Каталог, Корзина, Кабинет.

    Args:
        is_admin (bool): Является ли текущий пользователь администратором. По умолчанию False.

    Returns:
        ReplyKeyboardMarkup: Клавиатура главного меню.
    """
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text=TEXTS["menu_catalog"]))
    if not is_admin:
        builder.row(KeyboardButton(text=TEXTS["menu_cart"]))
        builder.row(KeyboardButton(text=TEXTS["menu_account"]))
    if is_admin:
        builder.row(KeyboardButton(text=TEXTS["menu_orders"]))
        builder.row(KeyboardButton(text=TEXTS["menu_stats"]))
        builder.row(KeyboardButton(text=TEXTS["menu_broadcast"]))
        builder.row(KeyboardButton(text=TEXTS["menu_more"]))
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def build_back_to_main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Создаёт клавиатуру с кнопкой возврата в главное меню.

    Returns:
        ReplyKeyboardMarkup: Клавиатура с кнопкой главного меню.
    """

    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text=TEXTS["start_over"]))
    return builder.as_markup(resize_keyboard=True)


def build_admin_more_reply_keyboard() -> ReplyKeyboardMarkup:
    """Создаёт нижнюю (reply) клавиатуру меню «Ещё»: дополнительные действия и Назад.

    Returns:
        ReplyKeyboardMarkup: Клавиатура с кнопками Обновить каталог, Пользователи, Назад.
    """
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text=TEXTS["menu_sync_catalog"]))
    builder.row(KeyboardButton(text=TEXTS["menu_users"]))
    builder.row(KeyboardButton(text=TEXTS["back"]))
    return builder.as_markup(resize_keyboard=True)


def build_categories_keyboard(categories: Iterable[Category]) -> InlineKeyboardMarkup:
    """Создаёт инлайн-клавиатуру выбора категории каталога.

    Args:
        categories (Iterable[Category]): Коллекция категорий каталога.

    Returns:
        InlineKeyboardMarkup: Инлайн-клавиатура с категориями и кнопкой назад.
    """

    builder = InlineKeyboardBuilder()
    for category in categories:
        builder.button(
            text=category.title,
            callback_data=f"category:{category.id}",
        )
    builder.button(text=TEXTS["back"], callback_data="nav:back_main")
    builder.adjust(2)
    return builder.as_markup()


def build_product_preview_keyboard(product_id: int) -> InlineKeyboardMarkup:
    """Клавиатура под превью товара: «Подробнее» и «В корзину».

    Args:
        product_id (int): Идентификатор товара.

    Returns:
        InlineKeyboardMarkup: Инлайн-клавиатура.
    """

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="Подробнее",
            callback_data=f"product:{product_id}",
        ),
        InlineKeyboardButton(
            text="🛒 В корзину",
            callback_data=f"cart:add:{product_id}",
        ),
    )
    return builder.as_markup()


def build_products_pagination_only_keyboard(
    category_id: int,
    page: int,
    page_size: int,
    total_count: int,
) -> InlineKeyboardMarkup:
    """Клавиатура только навигации по страницам (без кнопок товаров).

    Args:
        category_id (int): Идентификатор категории.
        page (int): Номер текущей страницы (0-индекс).
        page_size (int): Размер страницы.
        total_count (int): Всего товаров в категории.

    Returns:
        InlineKeyboardMarkup: Инлайн-клавиатура Назад / Вперёд / К категориям.
    """

    builder = InlineKeyboardBuilder()
    max_page = max((total_count - 1) // page_size, 0)
    has_prev = page > 0
    has_next = page < max_page

    nav_buttons: list[InlineKeyboardButton] = []
    if has_prev:
        nav_buttons.append(
            InlineKeyboardButton(
                text="◀️ Назад",
                callback_data=f"page:{category_id}:{page-1}",
            ),
        )
    if has_next:
        nav_buttons.append(
            InlineKeyboardButton(
                text="▶️ Вперёд",
                callback_data=f"page:{category_id}:{page+1}",
            ),
        )

    if nav_buttons:
        builder.row(*nav_buttons)

    builder.row(
        InlineKeyboardButton(
            text=TEXTS["back"],
            callback_data="nav:back_categories",
        ),
    )
    return builder.as_markup()


def build_products_grid_page_keyboard(
    products: Iterable[Product],
    category_id: int,
    page: int,
    page_size: int,
    total_count: int,
) -> InlineKeyboardMarkup:
    """Клавиатура страницы каталога: кнопки 1., 2., 3. (по фото), Назад/Далее, «К категориям».

    Args:
        products (Iterable[Product]): Товары текущей страницы (до 3 шт).
        category_id (int): Идентификатор категории.
        page (int): Номер страницы (0-индекс).
        page_size (int): Размер страницы.
        total_count (int): Всего товаров.

    Returns:
        InlineKeyboardMarkup: Инлайн-клавиатура.
    """
    builder = InlineKeyboardBuilder()
    for idx, p in enumerate(products, start=1):
        label = f"{idx}. {p.title}"
        if len(label) > 60:
            label = label[:57] + "…"
        builder.button(
            text=label,
            callback_data=f"product:{p.id}",
        )
    builder.adjust(1)
    max_page = max((total_count - 1) // page_size, 0)
    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(
            InlineKeyboardButton(
                text="◀️ Назад",
                callback_data=f"page:{category_id}:{page-1}",
            ),
        )
    nav_row.append(
        InlineKeyboardButton(
            text=f"Стр. {page+1}/{max_page+1}",
            callback_data="noop",
        ),
    )
    if page < max_page:
        nav_row.append(
            InlineKeyboardButton(
                text="Далее ▶️",
                callback_data=f"page:{category_id}:{page+1}",
            ),
        )
    builder.row(*nav_row)
    builder.row(
        InlineKeyboardButton(
            text="← К категориям",
            callback_data="nav:back_categories",
        ),
    )
    return builder.as_markup()


def build_product_actions_keyboard(product_id: int) -> InlineKeyboardMarkup:
    """Создаёт инлайн-клавиатуру действий для карточки товара.

    Args:
        product_id (int): Идентификатор товара.

    Returns:
        InlineKeyboardMarkup: Инлайн-клавиатура с действиями для товара.
    """

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🛒 В корзину",
            callback_data=f"cart:add:{product_id}",
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=TEXTS["back"],
            callback_data="nav:back_products",
        ),
    )
    return builder.as_markup()


def build_cart_keyboard(
    has_items: bool,
    can_checkout: bool,
) -> InlineKeyboardMarkup:
    """Создаёт инлайн-клавиатуру для управления корзиной.

    Args:
        has_items (bool): Есть ли позиции в корзине.
        can_checkout (bool): Можно ли переходить к оформлению заказа.

    Returns:
        InlineKeyboardMarkup: Инлайн-клавиатура для корзины.
    """

    builder = InlineKeyboardBuilder()
    if has_items and can_checkout:
        builder.row(
            InlineKeyboardButton(
                text=TEXTS["cart_checkout"],
                callback_data="cart:checkout",
            ),
        )
    builder.row(
        InlineKeyboardButton(
            text=TEXTS["back"],
            callback_data="nav:back_main",
        ),
    )
    return builder.as_markup()


def build_cart_item_controls_keyboard(
    cart_item_id: int,
) -> InlineKeyboardMarkup:
    """Создаёт инлайн-клавиатуру управления отдельной позицией в корзине.

    Args:
        cart_item_id (int): Идентификатор позиции корзины.

    Returns:
        InlineKeyboardMarkup: Инлайн-клавиатура с кнопками изменения количества.
    """

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="➖",
            callback_data=f"cart:item:{cart_item_id}:dec",
        ),
        InlineKeyboardButton(
            text="➕",
            callback_data=f"cart:item:{cart_item_id}:inc",
        ),
        InlineKeyboardButton(
            text="❌ Удалить",
            callback_data=f"cart:item:{cart_item_id}:remove",
        ),
    )
    return builder.as_markup()


def build_recipient_choice_keyboard(has_saved_recipients: bool) -> InlineKeyboardMarkup:
    """Создаёт клавиатуру выбора получателя заказа.

    Args:
        has_saved_recipients (bool): Есть ли у пользователя сохранённые получатели.

    Returns:
        InlineKeyboardMarkup: Инлайн-клавиатура: Я получатель, при наличии — из сохранённых, новый, отмена.
    """
    builder = InlineKeyboardBuilder()
    builder.button(
        text="👤 Я получатель",
        callback_data="order:recipient_self",
    )
    if has_saved_recipients:
        builder.button(
            text="📋 Выбрать из сохранённых",
            callback_data="order:recipient_saved",
        )
    builder.button(
        text="➕ Новый получатель",
        callback_data="order:recipient_new",
    )
    builder.button(text="✖️ Отмена", callback_data="order:cancel")
    builder.adjust(1)
    return builder.as_markup()


def build_saved_recipients_keyboard(
    recipients: Iterable[SavedRecipient],
) -> InlineKeyboardMarkup:
    """Создаёт инлайн-клавиатуру со списком сохранённых получателей.

    Args:
        recipients (Iterable[SavedRecipient]): Список сохранённых получателей.

    Returns:
        InlineKeyboardMarkup: Кнопки по одному на получателя и «Назад».
    """
    builder = InlineKeyboardBuilder()
    for r in recipients:
        short_phone = r.phone[-4:] if len(r.phone) >= 4 else r.phone
        builder.button(
            text=f"{r.name}, …{short_phone}",
            callback_data=f"order:recipient:{r.id}",
        )
    builder.button(text=TEXTS["back"], callback_data="order:recipient_back")
    builder.adjust(1)
    return builder.as_markup()


def build_delivery_choice_keyboard(
    options: Iterable[tuple[str, str]],
) -> InlineKeyboardMarkup:
    """Создаёт инлайн-клавиатуру выбора города доставки или самовывоза.

    Args:
        options (Iterable[tuple[str, str]]): Пары (slug для callback_data, текст кнопки).

    Returns:
        InlineKeyboardMarkup: Кнопки по одной на вариант доставки.
    """
    builder = InlineKeyboardBuilder()
    for slug, label in options:
        builder.button(
            text=label,
            callback_data=f"order:delivery:{slug}",
        )
    builder.adjust(1)
    return builder.as_markup()


MONTH_NAMES: tuple[str, ...] = (
    "",
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
)


def build_delivery_calendar_keyboard(
    year: int,
    month: int,
) -> InlineKeyboardMarkup:
    """Создаёт инлайн-клавиатуру календаря на месяц. Даты в прошлом — кнопка «—», callback order:date_past.

    Args:
        year (int): Год.
        month (int): Месяц (1–12).

    Returns:
        InlineKeyboardMarkup: Календарь с навигацией по месяцам и выбором дня.
    """
    builder = InlineKeyboardBuilder()
    today = date.today()
    # Навигация: [◀] Месяц Год [▶]
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1
    can_go_prev = (prev_year, prev_month) >= (today.year, today.month)
    builder.row(
        InlineKeyboardButton(
            text="◀️" if can_go_prev else "·",
            callback_data=(f"order:date_month:{prev_year}-{prev_month:02d}" if can_go_prev else "noop"),
        ),
        InlineKeyboardButton(
            text=f"{MONTH_NAMES[month]} {year}",
            callback_data="noop",
        ),
        InlineKeyboardButton(
            text="▶️",
            callback_data=f"order:date_month:{next_year}-{next_month:02d}",
        ),
    )
    # Заголовок дней недели
    builder.row(
        *[InlineKeyboardButton(text=w, callback_data="noop") for w in ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")],
    )
    # Календарь месяца: понедельник = 0
    month_days = cal.monthcalendar(year, month)
    for week in month_days:
        row_buttons: list[InlineKeyboardButton] = []
        for _weekday, day in enumerate(week):
            if day == 0:
                row_buttons.append(InlineKeyboardButton(text=" ", callback_data="noop"))
            else:
                d = date(year, month, day)
                if d < today:
                    row_buttons.append(
                        InlineKeyboardButton(text="—", callback_data="order:date_past"),
                    )
                else:
                    row_buttons.append(
                        InlineKeyboardButton(
                            text=str(day),
                            callback_data=f"order:date:{year}-{month:02d}-{day:02d}",
                        ),
                    )
        builder.row(*row_buttons)
    return builder.as_markup()


def build_delivery_time_keyboard(
    selected_date: date | None = None,
) -> InlineKeyboardMarkup:
    """Создаёт инлайн-клавиатуру выбора времени доставки по 30 минут с 9:00 до 19:00.

    Для выбранной даты «сегодня» показываются только слоты, которые ещё не наступили.
    Если на сегодня все слоты прошли (например, после 19:00), кнопок времени не будет.

    Args:
        selected_date (Optional[date]): Выбранная дата доставки; если совпадает с сегодняшним
            днём, в клавиатуру попадают только будущие слоты.

    Returns:
        InlineKeyboardMarkup: Кнопки с временем и «Ввести вручную».
    """
    builder = InlineKeyboardBuilder()
    now = datetime.now().time()
    today = date.today()

    for hour in range(9, 20):
        for minute in (0, 30):
            if hour == 19 and minute == 30:
                break
            if selected_date == today:
                slot_time = time_type(hour, minute)
                if slot_time <= now:
                    continue
            label = f"{hour:02d}:{minute:02d}"
            builder.button(
                text=label,
                callback_data=f"order:time:{label}",
            )
    builder.adjust(5)
    builder.row(
        InlineKeyboardButton(
            text="✏️ Ввести время вручную",
            callback_data="order:time_manual",
        ),
    )
    return builder.as_markup()


def build_order_confirmation_keyboard() -> InlineKeyboardMarkup:
    """Создаёт клавиатуру подтверждения или отмены оформления заказа.

    Returns:
        InlineKeyboardMarkup: Инлайн-клавиатура с подтверждением и отменой.
    """

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ Подтвердить",
            callback_data="order:confirm",
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="✖️ Отмена",
            callback_data="order:cancel",
        ),
    )
    return builder.as_markup()


def build_email_choice_keyboard(
    suggested_emails: list[str],
    enter_new_text: str = "✏️ Ввести другой email",
) -> InlineKeyboardMarkup:
    """Создаёт инлайн-клавиатуру выбора email из ранее использованных или ввода нового.

    Args:
        suggested_emails (list[str]): Список email для кнопок.
        enter_new_text (str): Текст кнопки «ввести другой email».

    Returns:
        InlineKeyboardMarkup: Клавиатура с кнопками email и «ввести другой».
    """
    builder = InlineKeyboardBuilder()
    for idx, email in enumerate(suggested_emails):
        builder.row(
            InlineKeyboardButton(
                text=email,
                callback_data=f"order:email_idx:{idx}",
            ),
        )
    builder.row(
        InlineKeyboardButton(text=enter_new_text, callback_data="order:email_new"),
    )
    return builder.as_markup()


def build_payment_method_keyboard(order_id: int) -> InlineKeyboardMarkup:
    """Создаёт клавиатуру выбора способа оплаты заказа.

    Args:
        order_id (int): Идентификатор заказа.

    Returns:
        InlineKeyboardMarkup: Инлайн-клавиатура: Юкасса, Наличный расчёт.
    """

    builder = InlineKeyboardBuilder()
    builder.button(
        text="💳 ЮКassa (картой онлайн)",
        callback_data=f"payment:method:yookassa:{order_id}",
    )
    builder.button(
        text="💵 Наличный расчёт",
        callback_data=f"payment:method:cash:{order_id}",
    )
    builder.adjust(1)
    return builder.as_markup()


def build_payment_keyboard(amount: int, order_id: int) -> InlineKeyboardMarkup:
    """Создаёт клавиатуру для мока оплаты заказа (ЮКassa).

    Args:
        amount (int): Сумма заказа в рублях.
        order_id (int): Идентификатор заказа.

    Returns:
        InlineKeyboardMarkup: Инлайн-клавиатура с кнопкой оплаты.
    """

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=f"Оплатить {amount} ₽",
            callback_data=f"payment:pay:{order_id}",
        ),
    )
    return builder.as_markup()


def build_account_orders_keyboard(
    orders: Iterable[OrderSummary],
) -> InlineKeyboardMarkup:
    """Создаёт клавиатуру списка заказов в личном кабинете (переход к деталям заказа).

    Args:
        orders (Iterable[OrderSummary]): Коллекция кратких заказов.

    Returns:
        InlineKeyboardMarkup: Инлайн-клавиатура с кнопками заказов.
    """

    builder = InlineKeyboardBuilder()
    for order in orders:
        builder.button(
            text=f"#{order.display_order_number} — {order.total_amount} ₽ · {order.status.human_readable}",
            callback_data=f"account:order:{order.id}",
        )
    builder.button(
        text=TEXTS["back"],
        callback_data="nav:back_main",
    )
    builder.adjust(1)
    return builder.as_markup()


def build_account_order_detail_keyboard(
    order_id: int,
    can_cancel: bool,
) -> InlineKeyboardMarkup:
    """Создаёт клавиатуру для детального просмотра заказа пользователем.

    Args:
        order_id (int): Идентификатор заказа.
        can_cancel (bool): Можно ли отменить заказ (статус Новый / Ожидает оплаты).

    Returns:
        InlineKeyboardMarkup: Кнопки «Повторить», при необходимости «Отменить», «Назад».
    """

    builder = InlineKeyboardBuilder()
    builder.button(
        text="🔁 Повторить заказ",
        callback_data=f"account:repeat:{order_id}",
    )
    if can_cancel:
        builder.button(
            text="❌ Отменить заказ",
            callback_data=f"account:cancel:{order_id}",
        )
    builder.button(text=TEXTS["back"], callback_data="account:back_orders")
    builder.adjust(1)
    return builder.as_markup()


def build_admin_main_keyboard() -> InlineKeyboardMarkup:
    """Создаёт основное меню админ-панели (используется при «Назад» из подразделов).

    Returns:
        InlineKeyboardMarkup: Инлайн-клавиатура главного меню администратора.
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="📦 Заказы",
            callback_data="admin:orders",
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="📊 Статистика",
            callback_data="admin:stats",
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="📣 Рассылка",
            callback_data="admin:broadcast",
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=TEXTS["back"],
            callback_data="nav:back_main",
        ),
    )
    return builder.as_markup()


def build_admin_more_keyboard() -> InlineKeyboardMarkup:
    """Создаёт инлайн-клавиатуру для кнопки «Ещё» в меню админа.

    Returns:
        InlineKeyboardMarkup: Кнопки «Обновить каталог», «Пользователи» и «Назад».
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🔄 Обновить каталог",
            callback_data="admin:sync_catalog",
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="👥 Пользователи",
            callback_data="admin:users",
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=TEXTS["back"],
            callback_data="nav:back_main",
        ),
    )
    return builder.as_markup()


def build_admin_orders_keyboard(
    orders: Iterable[OrderSummary],
) -> InlineKeyboardMarkup:
    """Создаёт клавиатуру списка заказов в админ-панели.

    Args:
        orders (Iterable[OrderSummary]): Коллекция заказов.

    Returns:
        InlineKeyboardMarkup: Инлайн-клавиатура с выбором заказа.
    """

    builder = InlineKeyboardBuilder()
    for order in orders:
        dt_str = order.created_at.strftime("%d.%m.%y %H:%M")
        builder.button(
            text=f"#{order.display_order_number} — {order.status.human_readable} · {dt_str}",
            callback_data=f"admin:order:{order.id}",
        )
    builder.button(
        text=TEXTS["back"],
        callback_data="admin:back",
    )
    builder.adjust(1)
    return builder.as_markup()


def build_admin_order_details_keyboard(
    order_id: int,
    current_status: OrderStatus | None = None,
) -> InlineKeyboardMarkup:
    """Создаёт клавиатуру для детального просмотра заказа в админке.

    Args:
        order_id (int): Идентификатор заказа.
        current_status (Optional[OrderStatus]): Текущий статус; если не отменён — показывается кнопка отмены.

    Returns:
        InlineKeyboardMarkup: Инлайн-клавиатура с действиями по заказу.
    """

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🔄 Сменить статус",
            callback_data=f"admin:status:{order_id}",
        ),
    )
    if current_status is not None and current_status != OrderStatus.CANCELLED:
        builder.row(
            InlineKeyboardButton(
                text="❌ Отменить заказ",
                callback_data=f"admin:order:cancel:{order_id}",
            ),
        )
    builder.row(
        InlineKeyboardButton(
            text=TEXTS["back"],
            callback_data="admin:back",
        ),
    )
    return builder.as_markup()


def build_admin_status_change_keyboard(
    order_id: int,
    current_status: OrderStatus,
) -> InlineKeyboardMarkup:
    """Создаёт клавиатуру выбора нового статуса заказа.

    Args:
        order_id (int): Идентификатор заказа.
        current_status (OrderStatus): Текущий статус заказа.

    Returns:
        InlineKeyboardMarkup: Инлайн-клавиатура со статусами.
    """

    builder = InlineKeyboardBuilder()
    for status in OrderStatus:
        prefix = "✅ " if status == current_status else ""
        builder.button(
            text=f"{prefix}{status.human_readable}",
            callback_data=f"admin:status:set:{order_id}:{status.value}",
        )
    builder.button(
        text=TEXTS["back"],
        callback_data="admin:back",
    )
    builder.adjust(1)
    return builder.as_markup()
