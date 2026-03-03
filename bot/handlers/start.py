from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

from ..keyboards.kb import TEXTS as KB_TEXTS
from ..keyboards.kb import build_main_menu_keyboard
from ..utils import is_admin

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
}

router = Router(name="start")


@router.message(F.text.in_({"/start", KB_TEXTS["start_over"]}))
async def handle_start_and_main_menu(message: Message) -> None:
    """Обрабатывает /start и кнопку возврата в главное меню.

    Args:
        message (Message): Входящее сообщение пользователя.

    Returns:
        None: Ничего не возвращает.
    """

    from_user = message.from_user
    is_admin_flag = bool(from_user and is_admin(from_user.id, message.bot))
    keyboard = build_main_menu_keyboard(is_admin=is_admin_flag)

    text = (message.text or "").strip()
    if text == "/start":
        welcome = TEXTS["welcome_admin"] if is_admin_flag else TEXTS["welcome_user"]
        await message.answer(welcome, reply_markup=keyboard)
        return

    await message.answer(
        TEXTS["back_to_main"],
        reply_markup=keyboard,
    )
