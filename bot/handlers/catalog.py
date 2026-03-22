from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Final

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InputMediaPhoto, Message
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
# Лимит подписи к фото в Telegram
CAPTION_MAX_LENGTH: Final[int] = 1024

router = Router(name="catalog")


@dataclass(frozen=True)
class ProductsPagePayload:
    """Данные для отрисовки страницы товаров категории (без отправки в чат).

    Attributes:
        photos: Список пар (URL превью, подпись).
        choose_text: Текст сообщения с кнопками выбора товара.
        keyboard: Inline-клавиатура страницы.
    """

    photos: list[tuple[str, str]]
    choose_text: str
    keyboard: InlineKeyboardMarkup


def _build_products_page_payload(
    db: Database,
    category_id: int,
    page: int,
) -> ProductsPagePayload | None:
    """Собирает текст, клавиатуру и превью для страницы категории без I/O.

    Args:
        db (Database): База данных бота.
        category_id (int): Идентификатор категории.
        page (int): Номер страницы (с нуля).

    Returns:
        ProductsPagePayload | None: Данные страницы или None, если товаров в категории нет.
    """

    total_count = db.count_products_in_category(category_id=category_id)
    if total_count == 0:
        return None

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

    photos_with_captions: list[tuple[str, str]] = []
    for idx, p in enumerate(products_list, start=1):
        photo_url = (p.image_url or "").strip()
        if not photo_url:
            continue
        title_clean = _clean_title(p.title)
        photos_with_captions.append(
            (
                photo_url,
                TEXTS["product_preview_caption"].format(
                    title=f"{idx}. {title_clean}",
                    price=p.price,
                ),
            ),
        )
    items_lines = [f"{i}. {_clean_title(p.title)} — {p.price} ₽" for i, p in enumerate(products_list, start=1)]
    items_block = "\n".join(items_lines)
    choose_text = TEXTS["catalog_choose_product_no_photos"].format(items=items_block)
    if photos_with_captions:
        choose_text = TEXTS["catalog_choose_product"] + "\n\n" + items_block

    return ProductsPagePayload(
        photos=photos_with_captions,
        choose_text=choose_text,
        keyboard=keyboard,
    )


async def _edit_catalog_text_message(
    bot: Bot,
    chat_id: int,
    message_id: int,
    text: str,
    keyboard: InlineKeyboardMarkup,
    *,
    parse_mode: str | None = None,
) -> bool:
    """Редактирует текст или подпись сообщения каталога (одно сообщение с клавиатурой).

    Args:
        bot (Bot): Экземпляр бота.
        chat_id (int): ID чата.
        message_id (int): ID сообщения.
        text (str): Новый текст или подпись.
        keyboard (InlineKeyboardMarkup): Клавиатура.
        parse_mode (str | None): Режим разбора (например HTML) или None.

    Returns:
        bool: True, если редактирование выполнено успешно.
    """

    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=keyboard,
            parse_mode=parse_mode,
        )
        return True
    except TelegramBadRequest:
        try:
            await bot.edit_message_caption(
                chat_id=chat_id,
                message_id=message_id,
                caption=text,
                reply_markup=keyboard,
                parse_mode=parse_mode,
            )
            return True
        except TelegramBadRequest:
            return False


def _strip_html(html_text: str, max_length: int = CAPTION_MAX_LENGTH) -> str:
    """Убирает HTML-теги и лишние пробелы из описания товара для отображения в боте.

    Args:
        html_text: Строка с HTML (описание из OpenCart/сайта).
        max_length: Максимальная длина результата (подпись Telegram — 1024).

    Returns:
        Очищенный текст без тегов.
    """
    if not html_text or not html_text.strip():
        return ""
    # Сначала декодируем HTML-сущности (&nbsp;, &amp;, &lt;p&gt; и т.д.),
    # чтобы любые закодированные теги тоже можно было корректно удалить.
    text = html.unescape(html_text.strip())
    # <br>, <br/>, <p> — в перенос строки
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>\s*", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<p(?:\s[^>]*)?>", "", text, flags=re.IGNORECASE)
    # Удаляем все оставшиеся теги
    text = re.sub(r"<[^>]+>", "", text)
    # Декодируем HTML-сущности (&nbsp;, &amp; и т.д.)
    text = html.unescape(text)
    # Схлопываем множественные пробелы и пустые строки
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    if len(text) > max_length:
        text = text[: max_length - 3].rstrip() + "..."
    return text


def _clean_title(raw_title: str) -> str:
    """Очищает название товара от HTML/сущностей и лишних пробелов."""

    if not raw_title:
        return ""
    # Используем тот же HTML-стриппер, что и для описаний,
    # но с меньшим лимитом длины; он удаляет все теги и декодирует сущности.
    return _strip_html(raw_title, max_length=128)


async def _show_categories(message: Message) -> None:
    """Отображает список категорий каталога.

    Args:
        message (Message): Входящее сообщение пользователя.

    Returns:
        None: Ничего не возвращает.
    """

    db: Database = message.bot.db
    categories = db.list_categories()
    keyboard = build_categories_keyboard(categories=categories)
    await message.answer(
        TEXTS["catalog_welcome"],
        reply_markup=keyboard,
    )


async def _render_products_page(
    *,
    bot: Bot,
    chat_id: int,
    db: Database,
    category_id: int,
    page: int,
    state: FSMContext | None = None,
) -> list[int]:
    """Отрисовывает страницу товаров категории: медиа-группа и сообщение с кнопками.

    Args:
        bot (Bot): Экземпляр бота aiogram.
        chat_id (int): ID чата для отправки.
        db (Database): Экземпляр базы данных бота.
        category_id (int): Идентификатор категории.
        page (int): Номер страницы (0-индекс).
        state (FSMContext | None): Для сохранения id сообщений при возврате/пагинации.

    Returns:
        list[int]: Список отправленных message_id (сначала медиа, затем текст).
    """
    built = _build_products_page_payload(db, category_id, page)
    if built is None:
        keyboard = build_categories_keyboard(categories=db.list_categories())
        await bot.send_message(
            chat_id=chat_id,
            text=TEXTS["no_products"],
            reply_markup=keyboard,
        )
        return []

    message_ids: list[int] = []
    if built.photos:
        media = [InputMediaPhoto(type="photo", media=ph, caption=cap) for ph, cap in built.photos]
        try:
            sent = await bot.send_media_group(chat_id=chat_id, media=media)
            message_ids = [m.message_id for m in sent]
        except TelegramBadRequest as e:
            logger.warning(
                "Не удалось отправить медиа-группу категории category_id={}: {}",
                category_id,
                e,
            )

    text_msg = await bot.send_message(
        chat_id=chat_id,
        text=built.choose_text,
        reply_markup=built.keyboard,
    )
    message_ids.append(text_msg.message_id)

    if state is not None:
        await state.update_data(
            catalog_message_ids=message_ids,
            catalog_chat_id=chat_id,
            catalog_category_id=category_id,
            catalog_page=page,
        )
    return message_ids


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
    title_clean = _clean_title(product.title)
    description_clean = _strip_html(product.description, max_length=2000)
    caption = TEXTS["product_card"].format(
        title=title_clean,
        description=description_clean,
        price=product.price,
    )
    if len(caption) > CAPTION_MAX_LENGTH:
        caption = caption[: CAPTION_MAX_LENGTH - 3].rstrip() + "..."
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
    except TelegramBadRequest as e:
        logger.warning(
            "Не удалось загрузить изображение товара product_id={} ({})",
            product.id,
            e,
        )
        # В запасном варианте дополнительно убираем всю HTML-разметку из текста,
        # чтобы исключить любые проблемы с разбором сущностей Telegram
        # и не показывать пользователю «сырые» теги.
        caption_plain = _strip_html(caption, max_length=CAPTION_MAX_LENGTH)
        await callback.message.answer(
            caption_plain + "\n\n(Изображение временно недоступно.)",
            reply_markup=keyboard,
            parse_mode=None,
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
        # Возврат с карточки товара: очищаем текущие сообщения страницы и рисуем её заново.
        data = await state.get_data()
        category_id = data.get("catalog_category_id")
        page = int(data.get("catalog_page", 0))
        if category_id is None:
            return
        chat_id = data.get("catalog_chat_id") or callback.message.chat.id
        old_ids: list[int] = data.get("catalog_message_ids") or []
        # Удаляем сообщения предыдущей страницы (карточка удалится вместе с callback.message)
        for mid in old_ids:
            try:
                await callback.bot.delete_message(chat_id=chat_id, message_id=mid)
            except (TelegramBadRequest, TelegramNetworkError, TypeError):
                # Сетевые и телеграм-ошибки при удалении старых сообщений
                # не должны блокировать показ новой страницы.
                continue
        try:
            await callback.message.delete()
        except TelegramBadRequest:
            pass

        db_page: Database = callback.bot.db
        await _render_products_page(
            bot=callback.bot,
            chat_id=chat_id,
            db=db_page,
            category_id=category_id,
            page=page,
            state=state,
        )
        return

    # Возврат со страницы каталога: редактируем сообщение с клавиатурой в категории, удаляем только медиа.
    data = await state.get_data()
    existing_ids: list[int] = data.get("catalog_message_ids") or []
    chat_id = data.get("catalog_chat_id") or callback.message.chat.id
    db: Database = callback.bot.db
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
            except (TelegramBadRequest, TelegramNetworkError, TypeError):
                continue
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
    if callback.message is None:
        return
    chat_id = callback.message.chat.id
    _, category_id_str = callback.data.split(":", maxsplit=1)
    category_id = int(category_id_str)

    db: Database = callback.bot.db
    built = _build_products_page_payload(db, category_id, 0)
    if built is None:
        keyboard = build_categories_keyboard(categories=db.list_categories())
        await callback.bot.send_message(
            chat_id=chat_id,
            text=TEXTS["no_products"],
            reply_markup=keyboard,
        )
        return

    # Без превью — превращаем сообщение со списком категорий в страницу товаров (без удаления).
    if not built.photos:
        ok = await _edit_catalog_text_message(
            callback.bot,
            chat_id,
            callback.message.message_id,
            built.choose_text,
            built.keyboard,
            parse_mode=None,
        )
        if ok:
            await state.update_data(
                catalog_message_ids=[callback.message.message_id],
                catalog_chat_id=chat_id,
                catalog_category_id=category_id,
                catalog_page=0,
            )
            return
        try:
            await callback.message.delete()
        except (TelegramBadRequest, TelegramNetworkError):
            pass
        await _render_products_page(
            bot=callback.bot,
            chat_id=chat_id,
            db=db,
            category_id=category_id,
            page=0,
            state=state,
        )
        return

    try:
        await callback.message.delete()
    except (TelegramBadRequest, TelegramNetworkError):
        pass

    await _render_products_page(
        bot=callback.bot,
        chat_id=chat_id,
        db=db,
        category_id=category_id,
        page=0,
        state=state,
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
    if callback.message is None:
        return
    data = await state.get_data()
    old_ids: list[int] = data.get("catalog_message_ids") or []
    chat_id = data.get("catalog_chat_id") or callback.message.chat.id
    _, category_id_str, page_str = callback.data.split(":", maxsplit=2)
    category_id = int(category_id_str)
    page = int(page_str)

    db: Database = callback.bot.db
    built = _build_products_page_payload(db, category_id, page)
    if built is None:
        keyboard = build_categories_keyboard(categories=db.list_categories())
        await callback.bot.send_message(
            chat_id=chat_id,
            text=TEXTS["no_products"],
            reply_markup=keyboard,
        )
        return

    if not old_ids:
        await _render_products_page(
            bot=callback.bot,
            chat_id=chat_id,
            db=db,
            category_id=category_id,
            page=page,
            state=state,
        )
        return

    text_id = old_ids[-1]
    media_ids = old_ids[:-1]
    photos = built.photos

    # Только текст (нет превью и не было медиа-сообщений) — одно редактирование.
    if not photos and not media_ids:
        if await _edit_catalog_text_message(
            callback.bot,
            chat_id,
            text_id,
            built.choose_text,
            built.keyboard,
            parse_mode=None,
        ):
            await state.update_data(
                catalog_page=page,
                catalog_category_id=category_id,
                catalog_chat_id=chat_id,
            )
        return

    # Раньше были фото, на этой странице превью нет — удаляем только картинки, текст правим.
    if not photos and media_ids:
        for mid in media_ids:
            try:
                await callback.bot.delete_message(chat_id=chat_id, message_id=mid)
            except (TelegramBadRequest, TelegramNetworkError, TypeError):
                continue
        if await _edit_catalog_text_message(
            callback.bot,
            chat_id,
            text_id,
            built.choose_text,
            built.keyboard,
            parse_mode=None,
        ):
            await state.update_data(
                catalog_message_ids=[text_id],
                catalog_category_id=category_id,
                catalog_page=page,
                catalog_chat_id=chat_id,
            )
        return

    # Появились превью, а раньше их не было — порядок сообщений иначе; пересобираем страницу.
    if photos and not media_ids:
        try:
            await callback.bot.delete_message(chat_id=chat_id, message_id=text_id)
        except (TelegramBadRequest, TelegramNetworkError, TypeError):
            pass
        await _render_products_page(
            bot=callback.bot,
            chat_id=chat_id,
            db=db,
            category_id=category_id,
            page=page,
            state=state,
        )
        return

    # Совпадает число превью — правим подписи/медиа и нижнее сообщение без полного сброса.
    if len(photos) == len(media_ids):
        text_ok = await _edit_catalog_text_message(
            callback.bot,
            chat_id,
            text_id,
            built.choose_text,
            built.keyboard,
            parse_mode=None,
        )
        if not text_ok:
            for mid in old_ids:
                try:
                    await callback.bot.delete_message(chat_id=chat_id, message_id=mid)
                except (TelegramBadRequest, TelegramNetworkError, TypeError):
                    continue
            await _render_products_page(
                bot=callback.bot,
                chat_id=chat_id,
                db=db,
                category_id=category_id,
                page=page,
                state=state,
            )
            return
        for (url, cap), mid in zip(photos, media_ids, strict=True):
            try:
                await callback.bot.edit_message_media(
                    chat_id=chat_id,
                    message_id=mid,
                    media=InputMediaPhoto(
                        media=url,
                        caption=cap,
                        parse_mode="HTML",
                    ),
                )
            except TelegramBadRequest as e:
                logger.warning(
                    "Не удалось обновить превью каталога message_id={}: {}",
                    mid,
                    e,
                )
        await state.update_data(
            catalog_message_ids=media_ids + [text_id],
            catalog_category_id=category_id,
            catalog_page=page,
            catalog_chat_id=chat_id,
        )
        return

    # Разное число слотов превью — надёжный fallback: удалить и отправить заново.
    for mid in old_ids:
        try:
            await callback.bot.delete_message(chat_id=chat_id, message_id=mid)
        except (TelegramBadRequest, TelegramNetworkError, TypeError):
            continue
    await _render_products_page(
        bot=callback.bot,
        chat_id=chat_id,
        db=db,
        category_id=category_id,
        page=page,
        state=state,
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
    text_id = callback.message.message_id
    media_ids = [mid for mid in message_ids if mid != text_id]

    for mid in media_ids:
        try:
            await callback.bot.delete_message(chat_id=chat_id, message_id=mid)
        except (TelegramBadRequest, TelegramNetworkError, TypeError):
            continue
    await state.update_data(catalog_message_ids=[], catalog_chat_id=None)

    db: Database = callback.bot.db
    _, product_id_str = callback.data.split(":", maxsplit=1)
    product_id = int(product_id_str)
    product: Product | None = db.get_product(product_id=product_id)
    if product is None:
        await callback.message.answer("К сожалению, этот товар больше недоступен.")
        return

    keyboard = build_product_actions_keyboard(product_id=product.id)
    title_clean = _clean_title(product.title)
    description_clean = _strip_html(product.description, max_length=2000)
    caption = TEXTS["product_card"].format(
        title=title_clean,
        description=description_clean,
        price=product.price,
    )
    if len(caption) > CAPTION_MAX_LENGTH:
        caption = caption[: CAPTION_MAX_LENGTH - 3].rstrip() + "..."

    photo_url = product.image_url.strip() if product.image_url else None
    if photo_url:
        try:
            await callback.bot.delete_message(chat_id=chat_id, message_id=text_id)
        except (TelegramBadRequest, TelegramNetworkError, TypeError):
            pass
        try:
            await callback.bot.send_photo(
                chat_id=chat_id,
                photo=photo_url,
                caption=caption,
                reply_markup=keyboard,
            )
        except TelegramBadRequest as e:
            logger.warning(
                "Не удалось загрузить изображение товара product_id={} ({})",
                product.id,
                e,
            )
            caption_plain = _strip_html(caption, max_length=CAPTION_MAX_LENGTH)
            await callback.bot.send_message(
                chat_id=chat_id,
                text=caption_plain + "\n\n(Изображение временно недоступно.)",
                reply_markup=keyboard,
                parse_mode=None,
            )
        return

    ok = await _edit_catalog_text_message(
        callback.bot,
        chat_id,
        text_id,
        caption,
        keyboard,
        parse_mode="HTML",
    )
    if not ok:
        await _send_product_card(callback=callback, product=product)
