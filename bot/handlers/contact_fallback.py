"""Обработчик для клиентов: при любом текстовом сообщении или ответе — подсказка и контакты магазина."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

from ..utils import is_admin

TEXTS: dict[str, str] = {
    "contact_prompt": ("Чтобы связаться с нами, напишите или позвоните по этим контактам:\n\n{contacts}"),
    "no_contacts": ("Телефоны поддержки:\n\n" "+7 (916) 005-06-08\n" "+7 (916) 876-30-45"),
}

router = Router(name="contact_fallback")


@router.message(F.text)
async def handle_client_text_fallback(message: Message) -> None:
    """Отвечает клиенту (не админу) на любое текстовое сообщение подсказкой и контактами магазина.

    Срабатывает как fallback: когда клиент пишет что-то словами или отвечает на сообщение
    админа в боте — отправляется сообщение «напишите по этим контактам» и список контактов
    из настроек (admin_contacts). Для админов ничего не делаем.

    Args:
        message (Message): Входящее текстовое сообщение.

    Returns:
        None: Ничего не возвращает.
    """
    if message.from_user is None or is_admin(message.from_user.id, message.bot):
        return
    if message.text and message.text.strip().startswith("/"):
        return

    db = getattr(message.bot, "db", None)
    if db is None:
        await message.answer(TEXTS["no_contacts"])
        return

    contacts = db.get_admin_contacts()
    if contacts:
        text = TEXTS["contact_prompt"].format(contacts="\n".join(contacts))
    else:
        text = TEXTS["no_contacts"]

    await message.answer(text)
