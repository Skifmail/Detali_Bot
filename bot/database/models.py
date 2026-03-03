from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class OrderStatus(StrEnum):
    """Перечисление возможных статусов заказа.

    Args:
        StrEnum: Базовый класс строкового перечисления.
    """

    NEW = "new"
    AWAITING_PAYMENT = "awaiting_payment"
    PAID = "paid"
    PROCESSING = "processing"
    ASSEMBLING = "assembling"
    COURIER = "courier"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"

    @property
    def human_readable(self) -> str:
        """Возвращает человекочитаемое название статуса.

        Returns:
            str: Локализованное человекочитаемое название статуса.
        """

        mapping: dict[OrderStatus, str] = {
            OrderStatus.NEW: "🆕 Новый",
            OrderStatus.AWAITING_PAYMENT: "⏳ Ожидает оплаты",
            OrderStatus.PAID: "✅ Оплачен",
            OrderStatus.PROCESSING: "📥 В обработке",
            OrderStatus.ASSEMBLING: "🧺 Собирается",
            OrderStatus.COURIER: "🚚 Передан курьеру",
            OrderStatus.DELIVERED: "✅ Доставлен",
            OrderStatus.CANCELLED: "❌ Отменён",
        }
        return mapping[self]


class User(BaseModel):
    """Модель пользователя бота.

    Args:
        BaseModel: Базовый класс Pydantic.
    """

    id: int = Field(description="Идентификатор пользователя в БД")
    tg_id: int = Field(description="Telegram ID пользователя")
    first_name: str | None = Field(
        default=None,
        description="Имя пользователя из Telegram",
    )
    last_name: str | None = Field(
        default=None,
        description="Фамилия пользователя из Telegram",
    )
    phone: str | None = Field(
        default=None,
        description="Номер телефона пользователя",
    )
    created_at: datetime = Field(description="Дата и время регистрации в боте")


class Category(BaseModel):
    """Модель категории товаров.

    Args:
        BaseModel: Базовый класс Pydantic.
    """

    id: int = Field(description="Идентификатор категории")
    slug: str = Field(description="Системный код категории")
    title: str = Field(description="Отображаемое название категории")


class Product(BaseModel):
    """Модель товара каталога.

    Args:
        BaseModel: Базовый класс Pydantic.
    """

    id: int = Field(description="Идентификатор товара")
    category_id: int = Field(description="Идентификатор категории товара")
    title: str = Field(description="Название товара")
    description: str = Field(description="Описание товара")
    price: int = Field(description="Цена товара в рублях")
    image_url: str = Field(description="Прямая ссылка на изображение товара")
    is_active: bool = Field(description="Флаг активности товара")
    opencart_product_id: int | None = Field(
        default=None,
        description="ID товара в OpenCart (oc_product.product_id). Заполняется при загрузке каталога с сайта.",
    )


class CartItem(BaseModel):
    """Модель позиции в корзине пользователя.

    Args:
        BaseModel: Базовый класс Pydantic.
    """

    id: int = Field(description="Идентификатор позиции корзины")
    user_id: int = Field(description="Идентификатор пользователя")
    product_id: int = Field(description="Идентификатор товара")
    quantity: int = Field(description="Количество единиц товара")
    created_at: datetime = Field(description="Время добавления в корзину")
    product: Product = Field(description="Информация о товаре")


class OrderItem(BaseModel):
    """Модель позиции внутри заказа.

    Args:
        BaseModel: Базовый класс Pydantic.
    """

    id: int = Field(description="Идентификатор позиции заказа")
    order_id: int = Field(description="Идентификатор заказа")
    product_id: int = Field(description="Идентификатор товара")
    quantity: int = Field(description="Количество товара в позиции")
    unit_price: int = Field(description="Цена единицы товара в рублях")
    product: Product = Field(description="Информация о товаре")


class Order(BaseModel):
    """Модель заказа с позициями.

    Args:
        BaseModel: Базовый класс Pydantic.
    """

    id: int = Field(description="Идентификатор заказа")
    user_id: int = Field(description="Идентификатор пользователя, оформившего заказ")
    status: OrderStatus = Field(description="Текущий статус заказа")
    total_amount: int = Field(description="Итоговая сумма заказа в рублях")
    created_at: datetime = Field(description="Дата и время создания заказа")
    updated_at: datetime = Field(description="Дата и время последнего обновления")
    delivery_address: str = Field(description="Адрес доставки заказа")
    customer_name: str = Field(description="Имя получателя заказа")
    phone: str = Field(description="Телефон получателя заказа")
    email: str | None = Field(
        default=None,
        description="Email получателя (для чеков ЮKassa и заказа в OpenCart)",
    )
    comment: str | None = Field(
        default=None,
        description="Комментарий к заказу от клиента",
    )
    external_payment_id: str | None = Field(
        default=None,
        description="Внешний идентификатор платежа (если есть)",
    )
    opencart_order_id: int | None = Field(
        default=None,
        description="ID заказа в OpenCart (если создан через API)",
    )
    display_order_number: int = Field(
        description="Отображаемый 4-значный номер заказа",
    )
    delivery_city: str | None = Field(
        default=None,
        description="Город доставки или «Самовывоз»",
    )
    delivery_cost: int = Field(
        default=0,
        description="Стоимость доставки в рублях",
    )
    desired_delivery_datetime: str | None = Field(
        default=None,
        description="Желаемая дата и время доставки (текст от заказчика)",
    )
    payment_method: str | None = Field(
        default=None,
        description="Способ оплаты: cash (наличные) или yookassa",
    )
    items: list[OrderItem] = Field(
        default_factory=list,
        description="Позиции заказа",
    )


class SavedRecipient(BaseModel):
    """Сохранённый получатель доставки для повторного выбора при заказе.

    Args:
        BaseModel: Базовый класс Pydantic.
    """

    id: int = Field(description="Идентификатор записи")
    user_id: int = Field(description="Идентификатор пользователя-заказчика")
    name: str = Field(description="Имя получателя")
    phone: str = Field(description="Телефон получателя")
    address: str = Field(description="Адрес доставки")
    created_at: datetime = Field(description="Дата и время добавления")


class OrderSummary(BaseModel):
    """Краткое представление заказа для списков и истории.

    Args:
        BaseModel: Базовый класс Pydantic.
    """

    id: int = Field(description="Идентификатор заказа")
    display_order_number: int = Field(
        description="Отображаемый 4-значный номер заказа",
    )
    status: OrderStatus = Field(description="Текущий статус заказа")
    total_amount: int = Field(description="Итоговая сумма заказа в рублях")
    created_at: datetime = Field(description="Дата и время создания заказа")


class StatsSummary(BaseModel):
    """Сводная статистика для админ-панели.

    Args:
        BaseModel: Базовый класс Pydantic.
    """

    total_users: int = Field(description="Количество зарегистрированных пользователей")
    total_orders: int = Field(description="Общее количество заказов")
    total_revenue: int = Field(description="Суммарная выручка по всем заказам")
