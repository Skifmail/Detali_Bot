from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class CartAddCallback(CallbackData, prefix="cart_add"):  # type: ignore[misc, call-arg]
    """Колбэк-данные для кнопки добавления товара в корзину.

    Args:
        product_id (int): Идентификатор товара из каталога.
    """

    product_id: int


class CartItemCallback(CallbackData, prefix="cart_item"):  # type: ignore[misc, call-arg]
    """Колбэк-данные для управления конкретной позицией в корзине.

    Args:
        cart_item_id (int): Идентификатор позиции корзины.
        action (str): Действие над позицией: 'inc', 'dec' или 'remove'.
    """

    cart_item_id: int
    action: str
