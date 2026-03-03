from __future__ import annotations

from types import SimpleNamespace
from typing import Final

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InputMediaPhoto, Message
from loguru import logger

from ..database.db import Database
from ..database.models import Product
from ..keyboards.kb import (
    TEXTS as KB_TEXTS,
)
from ..keyboards.kb import (
    build_categories_keyboard,
    build_product_actions_keyboard,
    build_products_grid_page_keyboard,
)

TEXTS: dict[str, str] = {
    "catalog_welcome": "🌸 Каталог флористики и декора\n\nВыберите категорию:",
    "catalog_choose_product": "Выберите товар (номера соответствуют фото выше):",
    "catalog_choose_product_no_photos": "Товары:\n{items}\n\nВыберите товар:",
    "no_products": "В этой категории пока нет активных товаров.",
    "product_preview_caption": "<b>{title}</b>\n{price} ₽",
    "product_card": "<b>{title}</b>\n\n{description}\n\nЦена: <b>{price} ₽</b>",
}

PAGE_SIZE: Final[int] = 3

router = Router(name="catalog")


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


async def _show_categories(message: Message) -> None:
    """Отображает список категорий каталога.

    Args:
        message (Message): Входящее сообщение пользователя.

    Returns:
        None: Ничего не возвращает.
    """

    db = _get_db_from_message(message)
    categories = db.list_categories()
    keyboard = build_categories_keyboard(categories=categories)
    await message.answer(
        TEXTS["catalog_welcome"],
        reply_markup=keyboard,
    )


async def _show_products_page(
    message: Message,
    category_id: int,
    page: int,
    *,
    state: FSMContext | None = None,
) -> None:
    """Страница каталога: до 3 фото в медиа-группе и клавиатура (Назад/Далее, К категориям).

    Args:
        message (Message): Куда отправлять (message.chat.id, message.bot).
        category_id (int): Идентификатор категории.
        page (int): Номер страницы (0-индекс).
        state (FSMContext | None): Для сохранения id сообщений при возврате/пагинации.

    Returns:
        None: Ничего не возвращает.
    """
    chat_id = message.chat.id
    bot = message.bot
    db = _get_db_from_message(message)
    total_count = db.count_products_in_category(category_id=category_id)

    if total_count == 0:
        keyboard = build_categories_keyboard(categories=db.list_categories())
        await bot.send_message(
            chat_id=chat_id,
            text=TEXTS["no_products"],
            reply_markup=keyboard,
        )
        return

    offset = page * PAGE_SIZE
    products = db.list_products_by_category(
        category_id=category_id,
        limit=PAGE_SIZE,
        offset=offset,
    )
    products_list = list(products)
    keyboard = build_products_grid_page_keyboard(
        products_list,
        category_id=category_id,
        page=page,
        page_size=PAGE_SIZE,
        total_count=total_count,
    )

    # Фото товаров берутся с сайта (image_url при загрузке каталога из OpenCart).
    photos_with_captions = [
        (
            p.image_url.strip(),
            TEXTS["product_preview_caption"].format(
                title=f"{idx}. {p.title}",
                price=p.price,
            ),
        )
        for idx, p in enumerate(products_list, start=1)
        if p.image_url and p.image_url.strip()
    ]
    message_ids: list[int] = []
    items_lines = [f"{i}. {p.title} — {p.price} ₽" for i, p in enumerate(products_list, start=1)]
    choose_text = TEXTS["catalog_choose_product_no_photos"].format(items="\n".join(items_lines))
    if photos_with_captions:
        media = [InputMediaPhoto(type="photo", media=ph, caption=cap) for ph, cap in photos_with_captions]
        try:
            sent = await bot.send_media_group(chat_id=chat_id, media=media)
            message_ids = [m.message_id for m in sent]
            choose_text = TEXTS["catalog_choose_product"]
        except TelegramBadRequest as e:
            logger.warning(
                "Не удалось отправить медиа-группу категории category_id={}: {}",
                category_id,
                e,
            )

    text_msg = await bot.send_message(
        chat_id=chat_id,
        text=choose_text,
        reply_markup=keyboard,
    )
    message_ids.append(text_msg.message_id)

    if state is not None:
        await state.update_data(
            catalog_message_ids=message_ids,
            catalog_chat_id=chat_id,
            catalog_category_id=category_id,
            catalog_page=page,
        )


async def _send_product_card(
    callback: CallbackQuery,
    product: Product,
) -> None:
    """Отправляет карточку товара с фотографией и кнопками действий.

    Args:
        callback (CallbackQuery): Callback-запрос пользователя.
        product (Product): Модель товара.

    Returns:
        None: Ничего не возвращает.
    """

    keyboard = build_product_actions_keyboard(product_id=product.id)
    caption = TEXTS["product_card"].format(
        title=product.title,
        description=product.description,
        price=product.price,
    )
    if callback.message is None:
        return

    photo_url = product.image_url.strip() if product.image_url else None
    try:
        if photo_url:
            await callback.message.answer_photo(
                photo_url,
                caption=caption,
                reply_markup=keyboard,
            )
        else:
            await callback.message.answer(
                caption,
                reply_markup=keyboard,
            )
    except TelegramBadRequest:
        logger.warning(
            "Не удалось загрузить изображение товара product_id={}",
            product.id,
        )
        await callback.message.answer(
            caption + "\n\n(Изображение временно недоступно.)",
            reply_markup=keyboard,
        )


@router.message(Command("catalog"))
@router.message(F.text == KB_TEXTS["menu_catalog"])
async def handle_catalog_entry(message: Message) -> None:
    """Точка входа в каталог по команде или кнопке меню.

    Args:
        message (Message): Сообщение пользователя.

    Returns:
        None: Ничего не возвращает.
    """

    await _show_categories(message)


@router.callback_query(F.data.in_({"nav:back_categories", "nav:back_products"}))
async def handle_back_to_categories(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Возврат: с карточки товара — в страницу товаров; со страницы — в категории.

    Args:
        callback (CallbackQuery): Callback-запрос пользователя.
        state (FSMContext): Контекст FSM для чтения catalog_message_ids.

    Returns:
        None: Ничего не возвращает.
    """
    await callback.answer()
    if callback.message is None:
        return

    if callback.data == "nav:back_products":
        # Возврат с карточки товара: редактируем сообщение в страницу товаров и дополняем медиа-группой.
        data = await state.get_data()
        category_id = data.get("catalog_category_id")
        page = data.get("catalog_page", 0)
        if category_id is None:
            return
        db = _get_db_from_callback(callback)
        chat_id = callback.message.chat.id
        total_count = db.count_products_in_category(category_id=category_id)
        if total_count == 0:
            keyboard = build_categories_keyboard(categories=db.list_categories())
            try:
                await callback.message.edit_text(TEXTS["no_products"], reply_markup=keyboard)
            except TelegramBadRequest:
                await callback.message.edit_caption(caption=TEXTS["no_products"], reply_markup=keyboard)
            return
        offset = page * PAGE_SIZE
        products_list = list(
            db.list_products_by_category(
                category_id=category_id,
                limit=PAGE_SIZE,
                offset=offset,
            )
        )
        keyboard = build_products_grid_page_keyboard(
            products_list,
            category_id=category_id,
            page=page,
            page_size=PAGE_SIZE,
            total_count=total_count,
        )
        items_lines = [f"{i}. {p.title} — {p.price} ₽" for i, p in enumerate(products_list, start=1)]
        choose_text = TEXTS["catalog_choose_product_no_photos"].format(items="\n".join(items_lines))
        photos_with_captions = [
            (
                p.image_url.strip(),
                TEXTS["product_preview_caption"].format(
                    title=f"{idx}. {p.title}",
                    price=p.price,
                ),
            )
            for idx, p in enumerate(products_list, start=1)
            if p.image_url and p.image_url.strip()
        ]
        if photos_with_captions:
            choose_text = TEXTS["catalog_choose_product"]
        try:
            await callback.message.edit_text(choose_text, reply_markup=keyboard)
        except TelegramBadRequest:
            try:
                await callback.message.edit_caption(caption=choose_text, reply_markup=keyboard)
            except TelegramBadRequest:
                msg = SimpleNamespace(
                    chat=SimpleNamespace(id=chat_id),
                    bot=callback.bot,
                )
                await _show_products_page(
                    message=msg,
                    category_id=category_id,
                    page=page,
                    state=state,
                )
                return
        message_ids: list[int] = [callback.message.message_id]
        if photos_with_captions:
            media = [InputMediaPhoto(type="photo", media=ph, caption=cap) for ph, cap in photos_with_captions]
            try:
                sent = await callback.bot.send_media_group(chat_id=chat_id, media=media)
                message_ids = [m.message_id for m in sent] + message_ids
            except TelegramBadRequest:
                pass
        await state.update_data(
            catalog_message_ids=message_ids,
            catalog_chat_id=chat_id,
            catalog_category_id=category_id,
            catalog_page=page,
        )
        return

    # Возврат со страницы каталога: редактируем сообщение с клавиатурой в категории, удаляем только медиа.
    data = await state.get_data()
    existing_ids: list[int] = data.get("catalog_message_ids") or []
    chat_id = data.get("catalog_chat_id") or callback.message.chat.id
    db = _get_db_from_callback(callback)
    keyboard = build_categories_keyboard(categories=db.list_categories())
    # Последнее сообщение в списке — текст с клавиатурой страницы, его редактируем.
    if existing_ids:
        text_msg_id = existing_ids[-1]
        media_ids = existing_ids[:-1]
        try:
            await callback.bot.edit_message_text(
                chat_id=chat_id,
                message_id=text_msg_id,
                text=TEXTS["catalog_welcome"],
                reply_markup=keyboard,
            )
        except TelegramBadRequest:
            try:
                await callback.bot.edit_message_caption(
                    chat_id=chat_id,
                    message_id=text_msg_id,
                    caption=TEXTS["catalog_welcome"],
                    reply_markup=keyboard,
                )
            except TelegramBadRequest:
                try:
                    await callback.bot.delete_message(chat_id=chat_id, message_id=text_msg_id)
                except (TelegramBadRequest, TypeError):
                    pass
                await callback.bot.send_message(
                    chat_id=callback.message.chat.id,
                    text=TEXTS["catalog_welcome"],
                    reply_markup=keyboard,
                )
        for mid in media_ids:
            try:
                await callback.bot.delete_message(chat_id=chat_id, message_id=mid)
            except (TelegramBadRequest, TypeError):
                pass
    else:
        await callback.bot.send_message(
            chat_id=callback.message.chat.id,
            text=TEXTS["catalog_welcome"],
            reply_markup=keyboard,
        )
    await state.clear()


@router.callback_query(F.data.startswith("category:"))
async def handle_category_selected(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Обрабатывает выбор категории: редактирует сообщение в страницу товаров и отправляет медиа-группу.

    Args:
        callback (CallbackQuery): Callback-запрос пользователя.
        state (FSMContext): Контекст FSM для сохранения id сообщений страницы каталога.

    Returns:
        None: Ничего не возвращает.
    """
    await callback.answer()
    assert callback.message is not None
    chat_id = callback.message.chat.id
    _, category_id_str = callback.data.split(":", maxsplit=1)
    category_id = int(category_id_str)
    db = _get_db_from_callback(callback)
    total_count = db.count_products_in_category(category_id=category_id)
    if total_count == 0:
        keyboard = build_categories_keyboard(categories=db.list_categories())
        try:
            await callback.message.edit_text(TEXTS["no_products"], reply_markup=keyboard)
        except TelegramBadRequest:
            await callback.message.answer(
                TEXTS["no_products"],
                reply_markup=keyboard,
            )
        return
    products_list = list(
        db.list_products_by_category(
            category_id=category_id,
            limit=PAGE_SIZE,
            offset=0,
        )
    )
    keyboard = build_products_grid_page_keyboard(
        products_list,
        category_id=category_id,
        page=0,
        page_size=PAGE_SIZE,
        total_count=total_count,
    )
    items_lines = [f"{i}. {p.title} — {p.price} ₽" for i, p in enumerate(products_list, start=1)]
    choose_text = TEXTS["catalog_choose_product_no_photos"].format(items="\n".join(items_lines))
    photos_with_captions = [
        (
            p.image_url.strip(),
            TEXTS["product_preview_caption"].format(
                title=f"{idx}. {p.title}",
                price=p.price,
            ),
        )
        for idx, p in enumerate(products_list, start=1)
        if p.image_url and p.image_url.strip()
    ]
    if photos_with_captions:
        choose_text = TEXTS["catalog_choose_product"]
    try:
        await callback.message.edit_text(choose_text, reply_markup=keyboard)
    except TelegramBadRequest:
        await callback.message.answer(
            choose_text,
            reply_markup=keyboard,
        )
    message_ids: list[int] = [callback.message.message_id]
    if photos_with_captions:
        media = [InputMediaPhoto(type="photo", media=ph, caption=cap) for ph, cap in photos_with_captions]
        try:
            sent = await callback.bot.send_media_group(chat_id=chat_id, media=media)
            message_ids = [m.message_id for m in sent] + message_ids
        except TelegramBadRequest:
            pass
    await state.update_data(
        catalog_message_ids=message_ids,
        catalog_chat_id=chat_id,
        catalog_category_id=category_id,
        catalog_page=0,
    )


@router.callback_query(F.data.startswith("page:"))
async def handle_products_page(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Обрабатывает переключение страниц: редактирует сообщение с клавиатурой, обновляет медиа-группу.

    Args:
        callback (CallbackQuery): Callback-запрос пользователя.
        state (FSMContext): Контекст FSM для чтения/записи catalog_message_ids.

    Returns:
        None: Ничего не возвращает.
    """
    await callback.answer()
    assert callback.message is not None
    data = await state.get_data()
    message_ids: list[int] = data.get("catalog_message_ids") or []
    chat_id = data.get("catalog_chat_id") or callback.message.chat.id
    _, category_id_str, page_str = callback.data.split(":", maxsplit=2)
    category_id = int(category_id_str)
    page = int(page_str)
    db = _get_db_from_callback(callback)
    total_count = db.count_products_in_category(category_id=category_id)
    if total_count == 0:
        keyboard = build_categories_keyboard(categories=db.list_categories())
        if message_ids:
            text_msg_id = message_ids[-1]
            try:
                await callback.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=text_msg_id,
                    text=TEXTS["no_products"],
                    reply_markup=keyboard,
                )
            except TelegramBadRequest:
                await callback.bot.edit_message_caption(
                    chat_id=chat_id,
                    message_id=text_msg_id,
                    caption=TEXTS["no_products"],
                    reply_markup=keyboard,
                )
            for mid in message_ids[:-1]:
                try:
                    await callback.bot.delete_message(chat_id=chat_id, message_id=mid)
                except (TelegramBadRequest, TypeError):
                    pass
        await state.clear()
        return
    products_list = list(
        db.list_products_by_category(
            category_id=category_id,
            limit=PAGE_SIZE,
            offset=page * PAGE_SIZE,
        )
    )
    keyboard = build_products_grid_page_keyboard(
        products_list,
        category_id=category_id,
        page=page,
        page_size=PAGE_SIZE,
        total_count=total_count,
    )
    items_lines = [f"{i}. {p.title} — {p.price} ₽" for i, p in enumerate(products_list, start=1)]
    choose_text = TEXTS["catalog_choose_product_no_photos"].format(items="\n".join(items_lines))
    photos_with_captions = [
        (
            p.image_url.strip(),
            TEXTS["product_preview_caption"].format(
                title=f"{idx}. {p.title}",
                price=p.price,
            ),
        )
        for idx, p in enumerate(products_list, start=1)
        if p.image_url and p.image_url.strip()
    ]
    if photos_with_captions:
        choose_text = TEXTS["catalog_choose_product"]
    edit_msg_id: int | None = message_ids[-1] if message_ids else None
    media_ids = message_ids[:-1] if message_ids else []
    for mid in media_ids:
        try:
            await callback.bot.delete_message(chat_id=chat_id, message_id=mid)
        except (TelegramBadRequest, TypeError):
            pass
    if edit_msg_id is not None:
        try:
            await callback.bot.edit_message_text(
                chat_id=chat_id,
                message_id=edit_msg_id,
                text=choose_text,
                reply_markup=keyboard,
            )
        except TelegramBadRequest:
            try:
                await callback.bot.edit_message_caption(
                    chat_id=chat_id,
                    message_id=edit_msg_id,
                    caption=choose_text,
                    reply_markup=keyboard,
                )
            except TelegramBadRequest:
                await _show_products_page(
                    message=callback.message,
                    category_id=category_id,
                    page=page,
                    state=state,
                )
                return
    else:
        await _show_products_page(
            message=callback.message,
            category_id=category_id,
            page=page,
            state=state,
        )
        return
    new_message_ids: list[int] = [edit_msg_id] if edit_msg_id is not None else []
    if photos_with_captions:
        media = [InputMediaPhoto(type="photo", media=ph, caption=cap) for ph, cap in photos_with_captions]
        try:
            sent = await callback.bot.send_media_group(chat_id=chat_id, media=media)
            new_message_ids = [m.message_id for m in sent] + new_message_ids
        except TelegramBadRequest:
            pass
    await state.update_data(
        catalog_message_ids=new_message_ids,
        catalog_chat_id=chat_id,
        catalog_category_id=category_id,
        catalog_page=page,
    )


@router.callback_query(F.data == "noop")
async def handle_noop(callback: CallbackQuery) -> None:
    """Обрабатывает нажатие на кнопку-индикатор страницы (ничего не делает)."""
    await callback.answer()


@router.callback_query(F.data.startswith("product:"))
async def handle_product_selected(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Обрабатывает выбор продукта: удаляет сообщения со страницей товаров и показывает карточку.

    Args:
        callback (CallbackQuery): Callback-запрос пользователя.
        state (FSMContext): Контекст FSM для чтения catalog_message_ids.

    Returns:
        None: Ничего не возвращает.
    """
    await callback.answer()
    if callback.message is None:
        return

    data = await state.get_data()
    message_ids: list[int] = data.get("catalog_message_ids") or []
    chat_id = data.get("catalog_chat_id") or callback.message.chat.id
    for mid in message_ids:
        try:
            await callback.bot.delete_message(chat_id=chat_id, message_id=mid)
        except (TelegramBadRequest, TypeError):
            pass
    await state.update_data(catalog_message_ids=[], catalog_chat_id=None)

    db = _get_db_from_callback(callback)
    _, product_id_str = callback.data.split(":", maxsplit=1)
    product_id = int(product_id_str)
    product: Product | None = db.get_product(product_id=product_id)
    if product is None:
        await callback.message.answer("К сожалению, этот товар больше недоступен.")
        return

    await _send_product_card(callback=callback, product=product)
