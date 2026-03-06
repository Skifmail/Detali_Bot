from __future__ import annotations

import json
import os
import random
import sqlite3
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from loguru import logger

from .models import (
    CartItem,
    Category,
    Order,
    OrderItem,
    OrderStatus,
    OrderSummary,
    Product,
    SavedRecipient,
    StatsSummary,
    User,
)

DB_FILENAME = "bot.sqlite3"
FOUR_DIGIT_ORDER_NUMBER_MIN = 1000
FOUR_DIGIT_ORDER_NUMBER_MAX = 9999
RECENT_ORDERS_LIMIT = 5


@dataclass(slots=True)
class DatabaseConfig:
    """Конфигурация подключения к базе данных SQLite.

    Args:
        db_path (Path): Путь к файлу базы данных.
    """

    db_path: Path


class Database:
    """Высокоуровневый интерфейс работы с SQLite и доменными моделями.

    Args:
        config (DatabaseConfig): Конфигурация подключения к базе данных.
    """

    def __init__(self, config: DatabaseConfig) -> None:
        self._config = config
        self._ensure_parent_dir()
        self._initialize_schema()
        self._seed_if_empty()

    def _ensure_parent_dir(self) -> None:
        """Гарантирует существование директории для файла БД.

        Returns:
            None: Ничего не возвращает.
        """

        db_dir = self._config.db_path.parent
        db_dir.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Контекстный менеджер для подключения к БД.

        Returns:
            Generator[sqlite3.Connection, None, None]: Генератор с подключением.
        """

        conn = sqlite3.connect(self._config.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            logger.exception("Ошибка при работе с БД")
            raise
        finally:
            conn.close()

    def _initialize_schema(self) -> None:
        """Создаёт таблицы в базе данных, если они ещё не существуют.

        Returns:
            None: Ничего не возвращает.
        """

        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tg_id INTEGER NOT NULL UNIQUE,
                    first_name TEXT,
                    last_name TEXT,
                    phone TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    slug TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    price INTEGER NOT NULL,
                    image_url TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    FOREIGN KEY (category_id) REFERENCES categories (id)
                );

                CREATE TABLE IF NOT EXISTS cart_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    product_id INTEGER NOT NULL,
                    quantity INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users (id),
                    FOREIGN KEY (product_id) REFERENCES products (id)
                );

                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    total_amount INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    delivery_address TEXT NOT NULL,
                    customer_name TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    comment TEXT,
                    external_payment_id TEXT,
                    display_order_number INTEGER NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users (id)
                );

                CREATE TABLE IF NOT EXISTS order_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id INTEGER NOT NULL,
                    product_id INTEGER NOT NULL,
                    quantity INTEGER NOT NULL,
                    unit_price INTEGER NOT NULL,
                    FOREIGN KEY (order_id) REFERENCES orders (id),
                    FOREIGN KEY (product_id) REFERENCES products (id)
                );

                CREATE TABLE IF NOT EXISTS saved_recipients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    address TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users (id)
                );
                """,
            )
        self._migrate_orders_delivery()

    def _migrate_orders_delivery(self) -> None:
        """Добавляет колонки доставки в таблицу orders при их отсутствии.

        Returns:
            None: Ничего не возвращает.
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(orders);")
            columns = [row[1] for row in cursor.fetchall()]
            if "delivery_city" not in columns:
                cursor.execute(
                    "ALTER TABLE orders ADD COLUMN delivery_city TEXT;",
                )
            if "delivery_cost" not in columns:
                cursor.execute(
                    "ALTER TABLE orders ADD COLUMN delivery_cost INTEGER DEFAULT 0;",
                )
            if "desired_delivery_datetime" not in columns:
                cursor.execute(
                    "ALTER TABLE orders ADD COLUMN desired_delivery_datetime TEXT;",
                )
            if "payment_method" not in columns:
                cursor.execute(
                    "ALTER TABLE orders ADD COLUMN payment_method TEXT;",
                )
            if "opencart_order_id" not in columns:
                cursor.execute(
                    "ALTER TABLE orders ADD COLUMN opencart_order_id INTEGER;",
                )
            if "email" not in columns:
                cursor.execute("ALTER TABLE orders ADD COLUMN email TEXT;")
        self._migrate_products_opencart_id()
        self._migrate_categories_opencart_id()
        self._migrate_admin_order_notifications()
        self._migrate_bot_settings()
        self._migrate_bot_admins()

    def _migrate_bot_settings(self) -> None:
        """Создаёт таблицу настроек бота (ключ-значение), в т.ч. контакт админа для клиентов."""
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL DEFAULT ''
                );
                """,
            )

    def _migrate_bot_admins(self) -> None:
        """Создаёт таблицу администраторов бота (добавленных через интерфейс; из env не хранятся)."""
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_admins (
                    user_id INTEGER PRIMARY KEY
                );
                """,
            )

    def _migrate_products_opencart_id(self) -> None:
        """Добавляет колонку opencart_product_id в таблицу products при её отсутствии.

        Используется для маппинга локального товара бота на товар в OpenCart
        при создании заказа в магазине.
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(products);")
            columns = [row[1] for row in cursor.fetchall()]
            if "opencart_product_id" not in columns:
                cursor.execute(
                    "ALTER TABLE products ADD COLUMN opencart_product_id INTEGER;",
                )

    def _migrate_categories_opencart_id(self) -> None:
        """Добавляет колонку opencart_category_id в categories при её отсутствии.

        Нужна для синхронизации каталога из БД OpenCart (маппинг OC → наш id).
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(categories);")
            columns = [row[1] for row in cursor.fetchall()]
            if "opencart_category_id" not in columns:
                cursor.execute(
                    "ALTER TABLE categories ADD COLUMN opencart_category_id INTEGER;",
                )
                cursor.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_categories_opencart_id
                    ON categories(opencart_category_id)
                    WHERE opencart_category_id IS NOT NULL;
                    """,
                )

    def _migrate_admin_order_notifications(self) -> None:
        """Создаёт таблицу сообщений уведомлений админам о заказах (для последующего редактирования при оплате)."""
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS admin_order_notifications (
                    order_id INTEGER NOT NULL,
                    admin_tg_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    has_photo INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (order_id, admin_tg_id),
                    FOREIGN KEY (order_id) REFERENCES orders (id)
                );
                """,
            )

    def save_admin_order_notification(
        self,
        order_id: int,
        admin_tg_id: int,
        chat_id: int,
        message_id: int,
        has_photo: bool,
    ) -> None:
        """Сохраняет идентификатор сообщения уведомления админу о заказе.

        Args:
            order_id (int): Идентификатор заказа.
            admin_tg_id (int): Telegram ID администратора.
            chat_id (int): ID чата (обычно совпадает с admin_tg_id).
            message_id (int): ID отправленного сообщения.
            has_photo (bool): True, если сообщение с фото (редактируется caption).
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO admin_order_notifications
                (order_id, admin_tg_id, chat_id, message_id, has_photo)
                VALUES (?, ?, ?, ?, ?);
                """,
                (order_id, admin_tg_id, chat_id, message_id, 1 if has_photo else 0),
            )

    def get_admin_order_notifications(self, order_id: int) -> list[tuple[int, int, bool]]:
        """Возвращает список сохранённых уведомлений админам по заказу.

        Args:
            order_id (int): Идентификатор заказа.

        Returns:
            List[tuple[int, int, bool]]: Список (chat_id, message_id, has_photo) на каждого админа.
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT chat_id, message_id, has_photo
                FROM admin_order_notifications
                WHERE order_id = ?;
                """,
                (order_id,),
            )
            rows = cursor.fetchall()
        return [(int(r["chat_id"]), int(r["message_id"]), bool(r["has_photo"])) for r in rows]

    ADMIN_CONTACT_KEY = "admin_contact"
    ADMIN_CONTACTS_KEY = "admin_contacts"
    ADMIN_CONTACTS_MAX = 5

    def get_setting(self, key: str) -> str | None:
        """Возвращает значение настройки по ключу.

        Args:
            key: Ключ настройки.

        Returns:
            Значение или None, если ключ отсутствует.
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT value FROM bot_settings WHERE key = ?;",
                (key,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return str(row["value"])

    def set_setting(self, key: str, value: str) -> None:
        """Сохраняет значение настройки.

        Args:
            key: Ключ настройки.
            value: Строковое значение.
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?);",
                (key, value),
            )

    def get_admin_contacts(self) -> list[str]:
        """Возвращает список контактов админов для связи с клиентами (до 5).

        Если задан старый ключ admin_contact — возвращает его как один элемент.
        """
        raw = self.get_setting(self.ADMIN_CONTACTS_KEY)
        if raw is not None and raw.strip():
            try:
                lst = json.loads(raw)
                if isinstance(lst, list):
                    return [str(x).strip() for x in lst if str(x).strip()][: self.ADMIN_CONTACTS_MAX]
            except (json.JSONDecodeError, TypeError):
                pass
        legacy = self.get_setting(self.ADMIN_CONTACT_KEY)
        if legacy and legacy.strip():
            return [legacy.strip()]
        return []

    def set_admin_contacts(self, contacts: list[str]) -> None:
        """Сохраняет список контактов админов (до 5). Очищает старый admin_contact."""
        trimmed = [str(c).strip() for c in contacts if str(c).strip()][: self.ADMIN_CONTACTS_MAX]
        self.set_setting(self.ADMIN_CONTACTS_KEY, json.dumps(trimmed, ensure_ascii=False))
        self.set_setting(self.ADMIN_CONTACT_KEY, "")

    def list_bot_admin_ids(self) -> list[int]:
        """Возвращает список Telegram ID администраторов, добавленных через бота (из таблицы bot_admins).

        Админы из переменной окружения ADMIN_IDS сюда не входят — они подмешиваются при старте.
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM bot_admins ORDER BY user_id;")
            return [int(row[0]) for row in cursor.fetchall()]

    def add_bot_admin(self, user_id: int) -> bool:
        """Добавляет пользователя в список администраторов бота (таблица bot_admins).

        Returns:
            True, если запись добавлена; False, если такой user_id уже есть.
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("INSERT INTO bot_admins (user_id) VALUES (?);", (user_id,))
                return True
            except sqlite3.IntegrityError:
                return False

    def remove_bot_admin(self, user_id: int) -> bool:
        """Удаляет пользователя из списка администраторов бота (только из таблицы bot_admins).

        Админов из ADMIN_IDS через этот метод удалить нельзя.

        Returns:
            True, если запись удалена; False, если такой user_id не был в bot_admins.
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM bot_admins WHERE user_id = ?;", (user_id,))
            return cursor.rowcount > 0

    def _seed_if_empty(self) -> None:
        """Заполняет БД тестовыми данными, если она пуста.

        Не выполняется, если задан OPENCART_DB_NAME — каталог будет загружен
        из OpenCart при старте бота.
        """
        if os.getenv("OPENCART_DB_NAME"):
            return

        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM categories;")
            categories_count = int(cursor.fetchone()[0])
            if categories_count > 0:
                return

            logger.info("Выполняется начальное заполнение БД тестовыми данными")

            categories = [
                ("bouquets", "🌸 Букеты"),
                ("compositions", "💐 Композиции"),
                ("decor", "🕯️ Декор"),
            ]
            cursor.executemany(
                "INSERT INTO categories (slug, title) VALUES (?, ?);",
                categories,
            )

            # Товары в боте не создаются при инициализации: каталог заполняется
            # только из БД сайта (OpenCart) — через синхронизацию или API.

            logger.info("Начальное заполнение БД завершено успешно")

    def seed_demo_catalog_if_empty(self) -> None:
        """Если нет ни одного товара, добавляет демо-товар для тестирования.

        Если категорий нет — создаёт одну категорию «Демо» и товар в ней.
        Если категории есть (например 3 тестовые), но товаров 0 — добавляет
        один демо-товар в первую по id категорию.
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM products WHERE is_active = 1;")
            if int(cursor.fetchone()[0]) > 0:
                return

            cursor.execute("SELECT id FROM categories ORDER BY id ASC LIMIT 1;")
            row = cursor.fetchone()
            if row is None:
                cursor.execute(
                    "INSERT INTO categories (slug, title) VALUES (?, ?);",
                    ("demo", "Демо"),
                )
                category_id = cursor.lastrowid
                logger.info("Добавлена демо-категория «Демо»")
            else:
                category_id = int(row["id"])

            cursor.execute(
                """
                INSERT INTO products (
                    category_id, title, description, price, image_url, is_active
                ) VALUES (?, ?, ?, ?, ?, 1);
                """,
                (
                    category_id,
                    "Демо-товар",
                    "Тестовый товар для проверки каталога и оформления заказа.",
                    100,
                    "https://via.placeholder.com/300?text=Demo",
                ),
            )
        logger.info("Добавлен демо-товар в каталог")

    # Пользователи

    def get_or_create_user(
        self,
        tg_id: int,
        first_name: str | None,
        last_name: str | None,
    ) -> User:
        """Возвращает существующего или создаёт нового пользователя по Telegram ID.

        Args:
            tg_id (int): Telegram ID пользователя.
            first_name (Optional[str]): Имя пользователя.
            last_name (Optional[str]): Фамилия пользователя.

        Returns:
            User: Объект пользователя из базы данных.
        """

        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM users WHERE tg_id = ?;",
                (tg_id,),
            )
            row = cursor.fetchone()
            if row is not None:
                return self._row_to_user(row)

            created_at = datetime.utcnow().isoformat()
            cursor.execute(
                """
                INSERT INTO users (tg_id, first_name, last_name, phone, created_at)
                VALUES (?, ?, ?, ?, ?);
                """,
                (tg_id, first_name, last_name, None, created_at),
            )
            last_id = cursor.lastrowid
            assert last_id is not None
            user_id = int(last_id)
            cursor.execute(
                "SELECT * FROM users WHERE id = ?;",
                (user_id,),
            )
            inserted_row = cursor.fetchone()

        assert inserted_row is not None
        logger.info("Создан новый пользователь tg_id={tg_id}", tg_id=tg_id)
        return self._row_to_user(inserted_row)

    def get_user(self, user_id: int) -> User | None:
        """Возвращает пользователя по идентификатору в БД.

        Args:
            user_id (int): Идентификатор пользователя (users.id).

        Returns:
            Optional[User]: Объект пользователя или None.
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE id = ?;", (user_id,))
            row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_user(row)

    def update_user_contact(
        self,
        user_id: int,
        customer_name: str,
        phone: str,
    ) -> None:
        """Обновляет контактную информацию пользователя.

        Args:
            user_id (int): Идентификатор пользователя в БД.
            customer_name (str): Имя клиента.
            phone (str): Номер телефона клиента.

        Returns:
            None: Ничего не возвращает.
        """

        first_name, _, last_name = customer_name.partition(" ")

        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE users
                SET first_name = ?, last_name = ?, phone = ?
                WHERE id = ?;
                """,
                (first_name or None, last_name or None, phone, user_id),
            )

    def list_users(self, limit: int = 100, offset: int = 0) -> list[User]:
        """Возвращает список пользователей для админки (последние сначала).

        Args:
            limit (int): Максимальное количество записей.
            offset (int): Смещение для постраничного вывода.

        Returns:
            list[User]: Список пользователей.
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM users
                ORDER BY id DESC
                LIMIT ? OFFSET ?;
                """,
                (limit, offset),
            )
            rows = cursor.fetchall()
        return [self._row_to_user(row) for row in rows]

    def count_users(self) -> int:
        """Возвращает общее количество пользователей в БД.

        Returns:
            int: Число пользователей.
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) AS cnt FROM users;")
            row = cursor.fetchone()
        return int(row["cnt"])

    def get_all_user_tg_ids(self) -> list[int]:
        """Возвращает Telegram ID всех пользователей.

        Returns:
            List[int]: Список Telegram ID пользователей.
        """

        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT tg_id FROM users;")
            rows = cursor.fetchall()
        return [int(row["tg_id"]) for row in rows]

    def get_user_tg_id(self, user_id: int) -> int | None:
        """Возвращает Telegram ID пользователя по идентификатору в БД.

        Args:
            user_id (int): Идентификатор пользователя в таблице users.

        Returns:
            Optional[int]: Telegram ID пользователя или None, если пользователь не найден.
        """

        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT tg_id FROM users WHERE id = ?;",
                (user_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return int(row["tg_id"])

    # Сохранённые получатели

    def add_saved_recipient(
        self,
        user_id: int,
        name: str,
        phone: str,
        address: str,
    ) -> SavedRecipient:
        """Добавляет сохранённого получателя для пользователя.

        Args:
            user_id (int): Идентификатор пользователя-заказчика.
            name (str): Имя получателя.
            phone (str): Телефон получателя.
            address (str): Адрес доставки.

        Returns:
            SavedRecipient: Созданная запись получателя.
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            now = datetime.utcnow().isoformat()
            cursor.execute(
                """
                INSERT INTO saved_recipients (user_id, name, phone, address, created_at)
                VALUES (?, ?, ?, ?, ?);
                """,
                (user_id, name, phone, address, now),
            )
            last_id = cursor.lastrowid
            assert last_id is not None
            row_id = int(last_id)
            cursor.execute(
                "SELECT * FROM saved_recipients WHERE id = ?;",
                (row_id,),
            )
            row = cursor.fetchone()
        assert row is not None
        return self._row_to_saved_recipient(row)

    def list_saved_recipients(self, user_id: int) -> list[SavedRecipient]:
        """Возвращает список сохранённых получателей пользователя.

        Args:
            user_id (int): Идентификатор пользователя-заказчика.

        Returns:
            List[SavedRecipient]: Список получателей, от новых к старым.
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM saved_recipients
                WHERE user_id = ?
                ORDER BY created_at DESC;
                """,
                (user_id,),
            )
            rows = cursor.fetchall()
        return [self._row_to_saved_recipient(r) for r in rows]

    def get_saved_recipient(
        self,
        recipient_id: int,
        user_id: int,
    ) -> SavedRecipient | None:
        """Возвращает сохранённого получателя по id, если он принадлежит пользователю.

        Args:
            recipient_id (int): Идентификатор записи получателя.
            user_id (int): Идентификатор пользователя (проверка владельца).

        Returns:
            Optional[SavedRecipient]: Получатель или None.
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM saved_recipients
                WHERE id = ? AND user_id = ?;
                """,
                (recipient_id, user_id),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_saved_recipient(row)

    def _row_to_saved_recipient(self, row: sqlite3.Row) -> SavedRecipient:
        """Преобразует строку БД в модель SavedRecipient."""
        return SavedRecipient(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            name=str(row["name"]),
            phone=str(row["phone"]),
            address=str(row["address"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    # Категории и товары

    def list_categories(self) -> list[Category]:
        """Возвращает категории, в которых есть хотя бы один активный товар.

        Категории без товаров (или только с неактивными) в каталоге не показываются.

        Returns:
            List[Category]: Список категорий с товарами.
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT DISTINCT c.id, c.slug, c.title
                FROM categories c
                INNER JOIN products p ON p.category_id = c.id AND p.is_active = 1
                    AND p.opencart_product_id IS NOT NULL
                ORDER BY c.id ASC;
                """,
            )
            rows = cursor.fetchall()
        return [
            Category(
                id=int(row["id"]),
                slug=str(row["slug"]),
                title=str(row["title"]),
            )
            for row in rows
        ]

    def list_products_by_category(
        self,
        category_id: int,
        limit: int,
        offset: int,
    ) -> list[Product]:
        """Возвращает товары категории с пагинацией.

        Args:
            category_id (int): Идентификатор категории.
            limit (int): Максимальное количество товаров.
            offset (int): Смещение выборки.

        Returns:
            List[Product]: Список товаров категории.
        """

        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT *
                FROM products
                WHERE category_id = ? AND is_active = 1
                ORDER BY id DESC
                LIMIT ? OFFSET ?;
                """,
                (category_id, limit, offset),
            )
            rows = cursor.fetchall()
        return [self._row_to_product(row) for row in rows]

    def count_products_in_category(self, category_id: int) -> int:
        """Возвращает количество активных товаров в категории.

        Args:
            category_id (int): Идентификатор категории.

        Returns:
            int: Количество активных товаров.
        """

        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM products
                WHERE category_id = ? AND is_active = 1
                    AND opencart_product_id IS NOT NULL;
                """,
                (category_id,),
            )
            row = cursor.fetchone()
        return int(row[0]) if row is not None else 0

    def get_or_create_category_by_opencart_id(self, opencart_category_id: int, title: str) -> int:
        """Возвращает id категории по opencart_category_id; создаёт при отсутствии.

        Используется при синхронизации каталога из OpenCart.

        Args:
            opencart_category_id (int): ID категории в OpenCart.
            title (str): Название категории.

        Returns:
            int: Локальный id категории в SQLite.
        """
        slug = f"oc_{opencart_category_id}"
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM categories WHERE opencart_category_id = ?;",
                (opencart_category_id,),
            )
            row = cursor.fetchone()
            if row is not None:
                return int(row["id"])
            cursor.execute(
                """
                INSERT INTO categories (opencart_category_id, slug, title)
                VALUES (?, ?, ?);
                """,
                (opencart_category_id, slug, title.strip()),
            )
            return cursor.lastrowid  # type: ignore[return-value]

    def deactivate_all_products_for_sync(self) -> None:
        """Ставит is_active=0 всем товарам перед синхронизацией из OpenCart.

        После синхронизации обновлённые товары получат is_active=1.
        Товары, отсутствующие в OpenCart, останутся неактивными.
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE products SET is_active = 0;")

    def upsert_product_from_opencart(
        self,
        opencart_product_id: int,
        category_id: int,
        title: str,
        description: str,
        price: int,
        image_url: str,
    ) -> None:
        """Вставляет или обновляет товар по opencart_product_id (sync из OpenCart).

        Args:
            opencart_product_id (int): ID товара в OpenCart.
            category_id (int): Локальный id категории в SQLite.
            title (str): Название.
            description (str): Описание.
            price (int): Цена в рублях.
            image_url (str): URL изображения.
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM products WHERE opencart_product_id = ?;",
                (opencart_product_id,),
            )
            row = cursor.fetchone()
            if row is not None:
                cursor.execute(
                    """
                    UPDATE products
                    SET category_id = ?, title = ?, description = ?, price = ?,
                        image_url = ?, is_active = 1
                    WHERE opencart_product_id = ?;
                    """,
                    (
                        category_id,
                        title.strip(),
                        (description or "").strip(),
                        price,
                        image_url,
                        opencart_product_id,
                    ),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO products (
                        category_id, title, description, price, image_url,
                        is_active, opencart_product_id
                    ) VALUES (?, ?, ?, ?, ?, 1, ?);
                    """,
                    (
                        category_id,
                        title.strip(),
                        (description or "").strip(),
                        price,
                        image_url,
                        opencart_product_id,
                    ),
                )

    def get_product(self, product_id: int) -> Product | None:
        """Возвращает товар по идентификатору.

        Args:
            product_id (int): Идентификатор товара.

        Returns:
            Optional[Product]: Объект товара или None, если он не найден.
        """

        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM products WHERE id = ? AND is_active = 1;",
                (product_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_product(row)

    def add_product(
        self,
        category_id: int,
        title: str,
        description: str,
        price: int,
        image_url: str,
    ) -> Product:
        """Создаёт новый товар в каталоге.

        Args:
            category_id (int): Идентификатор категории.
            title (str): Наименование товара (на кнопке и в карточке).
            description (str): Описание товара.
            price (int): Цена в рублях.
            image_url (str): URL изображения товара.

        Returns:
            Product: Созданный товар с присвоенным id.
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO products (category_id, title, description, price, image_url, is_active)
                VALUES (?, ?, ?, ?, ?, 1);
                """,
                (category_id, title.strip(), description.strip(), price, image_url),
            )
            last_id = cursor.lastrowid
            assert last_id is not None
            row_id = int(last_id)
            cursor.execute("SELECT * FROM products WHERE id = ?;", (row_id,))
            row = cursor.fetchone()
        assert row is not None
        return self._row_to_product(row)

    def set_product_opencart_id(
        self,
        product_id: int,
        opencart_product_id: int,
    ) -> None:
        """Устанавливает маппинг локального товара на ID товара в OpenCart.

        Args:
            product_id (int): Локальный id товара в БД бота.
            opencart_product_id (int): ID товара в каталоге OpenCart (oc_product).

        Returns:
            None: Ничего не возвращает.
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE products
                SET opencart_product_id = ?
                WHERE id = ?;
                """,
                (opencart_product_id, product_id),
            )

    # Корзина

    def get_cart(self, user_id: int) -> list[CartItem]:
        """Возвращает содержимое корзины пользователя.

        Args:
            user_id (int): Идентификатор пользователя.

        Returns:
            List[CartItem]: Список позиций в корзине.
        """

        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT
                    ci.id AS cart_id,
                    ci.user_id,
                    ci.product_id,
                    ci.quantity,
                    ci.created_at,
                    p.*
                FROM cart_items AS ci
                JOIN products AS p ON p.id = ci.product_id
                WHERE ci.user_id = ?
                ORDER BY ci.created_at ASC;
                """,
                (user_id,),
            )
            rows = cursor.fetchall()

        items: list[CartItem] = []
        for row in rows:
            # Логирование сырых данных из БД для отладки оформления заказа.
            # sqlite3.Row не является обычным dict, поэтому сначала приводим к словарю.
            row_dict = dict(row)
            raw_oc = row_dict.get("opencart_product_id", "<нет колонки>")
            logger.info(
                "get_cart: product_id={pid}, raw opencart_product_id={raw_oc}",
                pid=row_dict["product_id"],
                raw_oc=raw_oc,
            )
            product = self._row_to_product(row)
            items.append(
                CartItem(
                    id=int(row["cart_id"]),
                    user_id=int(row["user_id"]),
                    product_id=int(row["product_id"]),
                    quantity=int(row["quantity"]),
                    created_at=datetime.fromisoformat(row["created_at"]),
                    product=product,
                ),
            )
        return items

    def add_to_cart(self, user_id: int, product_id: int, delta: int) -> None:
        """Добавляет товар в корзину или изменяет его количество.

        Args:
            user_id (int): Идентификатор пользователя.
            product_id (int): Идентификатор товара.
            delta (int): Изменение количества (может быть отрицательным).

        Returns:
            None: Ничего не возвращает.
        """

        if delta == 0:
            return

        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, quantity
                FROM cart_items
                WHERE user_id = ? AND product_id = ?;
                """,
                (user_id, product_id),
            )
            row = cursor.fetchone()

            if row is None:
                if delta < 0:
                    return
                cursor.execute(
                    """
                    INSERT INTO cart_items (
                        user_id,
                        product_id,
                        quantity,
                        created_at
                    )
                    VALUES (?, ?, ?, ?);
                    """,
                    (
                        user_id,
                        product_id,
                        delta,
                        datetime.utcnow().isoformat(),
                    ),
                )
                return

            cart_id = int(row["id"])
            current_quantity = int(row["quantity"])
            new_quantity = current_quantity + delta

            if new_quantity <= 0:
                cursor.execute(
                    "DELETE FROM cart_items WHERE id = ?;",
                    (cart_id,),
                )
            else:
                cursor.execute(
                    """
                    UPDATE cart_items
                    SET quantity = ?
                    WHERE id = ?;
                    """,
                    (new_quantity, cart_id),
                )

    def clear_cart(self, user_id: int) -> None:
        """Очищает корзину пользователя.

        Args:
            user_id (int): Идентификатор пользователя.

        Returns:
            None: Ничего не возвращает.
        """

        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM cart_items WHERE user_id = ?;",
                (user_id,),
            )

    # Заказы

    def create_order_from_cart(
        self,
        user_id: int,
        customer_name: str,
        phone: str,
        delivery_address: str,
        comment: str | None,
        delivery_city: str | None = None,
        delivery_cost: int = 0,
        desired_delivery_datetime: str | None = None,
        email: str | None = None,
    ) -> Order | None:
        """Создаёт заказ из текущей корзины пользователя.

        Args:
            user_id (int): Идентификатор пользователя.
            customer_name (str): Имя получателя.
            phone (str): Номер телефона получателя.
            delivery_address (str): Адрес доставки или «Самовывоз».
            comment (Optional[str]): Комментарий к заказу.
            delivery_city (Optional[str]): Город доставки или «Самовывоз».
            delivery_cost (int): Стоимость доставки в рублях.
            desired_delivery_datetime (Optional[str]): Желаемая дата и время доставки.
            email (Optional[str]): Email получателя (для чеков и OpenCart).

        Returns:
            Optional[Order]: Созданный заказ или None, если корзина пуста.
        """

        cart_items = self.get_cart(user_id)
        if not cart_items:
            return None

        with self._connection() as conn:
            cursor = conn.cursor()
            now = datetime.utcnow()
            order_status = OrderStatus.NEW
            display_number = random.randint(
                FOUR_DIGIT_ORDER_NUMBER_MIN,
                FOUR_DIGIT_ORDER_NUMBER_MAX,
            )
            cart_total = sum(item.product.price * item.quantity for item in cart_items)
            total_amount = cart_total + delivery_cost

            cursor.execute(
                """
                INSERT INTO orders (
                    user_id,
                    status,
                    total_amount,
                    created_at,
                    updated_at,
                    delivery_address,
                    customer_name,
                    phone,
                    email,
                    comment,
                    external_payment_id,
                    display_order_number,
                    delivery_city,
                    delivery_cost,
                    desired_delivery_datetime
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    user_id,
                    order_status.value,
                    total_amount,
                    now.isoformat(),
                    now.isoformat(),
                    delivery_address,
                    customer_name,
                    phone,
                    email,
                    comment,
                    None,
                    display_number,
                    delivery_city,
                    delivery_cost,
                    desired_delivery_datetime,
                ),
            )
            last_oid = cursor.lastrowid
            assert last_oid is not None
            order_id = int(last_oid)

            for item in cart_items:
                cursor.execute(
                    """
                    INSERT INTO order_items (
                        order_id,
                        product_id,
                        quantity,
                        unit_price
                    )
                    VALUES (?, ?, ?, ?);
                    """,
                    (
                        order_id,
                        item.product_id,
                        item.quantity,
                        item.product.price,
                    ),
                )

            cursor.execute(
                "DELETE FROM cart_items WHERE user_id = ?;",
                (user_id,),
            )

        logger.info(
            "Создан заказ id={order_id} для user_id={user_id}",
            order_id=order_id,
            user_id=user_id,
        )
        return self.get_order(order_id)

    def get_order(self, order_id: int) -> Order | None:
        """Возвращает заказ с позициями по идентификатору.

        Args:
            order_id (int): Идентификатор заказа.

        Returns:
            Optional[Order]: Объект заказа или None, если не найден.
        """

        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM orders WHERE id = ?;", (order_id,))
            order_row = cursor.fetchone()
            if order_row is None:
                return None

            cursor.execute(
                """
                SELECT oi.*, p.*
                FROM order_items AS oi
                JOIN products AS p ON p.id = oi.product_id
                WHERE oi.order_id = ?
                ORDER BY oi.id ASC;
                """,
                (order_id,),
            )
            item_rows = cursor.fetchall()

        items = self._rows_to_order_items(item_rows)
        order = self._row_to_order(order_row, items)
        return order

    def list_orders_for_user(
        self,
        user_id: int,
        limit: int = RECENT_ORDERS_LIMIT,
    ) -> list[OrderSummary]:
        """Возвращает список последних заказов пользователя.

        Args:
            user_id (int): Идентификатор пользователя.
            limit (int): Максимальное количество заказов.

        Returns:
            List[OrderSummary]: Список кратких представлений заказов.
        """

        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT
                    id,
                    display_order_number,
                    status,
                    total_amount,
                    created_at
                FROM orders
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?;
                """,
                (user_id, limit),
            )
            rows = cursor.fetchall()

        summaries: list[OrderSummary] = []
        for row in rows:
            summaries.append(
                OrderSummary(
                    id=int(row["id"]),
                    display_order_number=int(row["display_order_number"]),
                    status=OrderStatus(row["status"]),
                    total_amount=int(row["total_amount"]),
                    created_at=datetime.fromisoformat(row["created_at"]),
                ),
            )
        return summaries

    def get_emails_used_by_user(self, user_id: int) -> list[str]:
        """Возвращает список уникальных email, которые пользователь указывал в заказах.

        Args:
            user_id (int): Идентификатор пользователя в БД.

        Returns:
            list[str]: Уникальные непустые email, от новых к старым (по дате заказа).
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            pragma_rows = cursor.execute("PRAGMA table_info(orders);").fetchall()
            if "email" not in [row[1] for row in pragma_rows]:
                return []
            cursor.execute(
                """
                SELECT email, MAX(created_at) AS last_used FROM orders
                WHERE user_id = ? AND email IS NOT NULL AND trim(email) != ''
                GROUP BY email ORDER BY last_used DESC;
                """,
                (user_id,),
            )
            rows = cursor.fetchall()
        return [str(r["email"]) for r in rows]

    def list_recent_orders(
        self,
        limit: int = RECENT_ORDERS_LIMIT,
    ) -> list[OrderSummary]:
        """Возвращает список последних заказов для админ-панели.

        Args:
            limit (int): Максимальное количество заказов.

        Returns:
            list[OrderSummary]: Список кратких представлений заказов.
        """

        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT
                    id,
                    display_order_number,
                    status,
                    total_amount,
                    created_at
                FROM orders
                ORDER BY created_at DESC
                LIMIT ?;
                """,
                (limit,),
            )
            rows = cursor.fetchall()

        summaries: list[OrderSummary] = []
        for row in rows:
            summaries.append(
                OrderSummary(
                    id=int(row["id"]),
                    display_order_number=int(row["display_order_number"]),
                    status=OrderStatus(row["status"]),
                    total_amount=int(row["total_amount"]),
                    created_at=datetime.fromisoformat(row["created_at"]),
                ),
            )
        return summaries

    def list_orders_page(
        self,
        *,
        limit: int = RECENT_ORDERS_LIMIT,
        offset: int = 0,
    ) -> list[OrderSummary]:
        """Возвращает страницу заказов для админ-панели.

        Args:
            limit: Максимальное количество заказов на странице.
            offset: Смещение (количество пропускаемых записей).

        Returns:
            list[OrderSummary]: Список кратких представлений заказов.
        """

        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT
                    id,
                    display_order_number,
                    status,
                    total_amount,
                    created_at
                FROM orders
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?;
                """,
                (limit, offset),
            )
            rows = cursor.fetchall()

        summaries: list[OrderSummary] = []
        for row in rows:
            summaries.append(
                OrderSummary(
                    id=int(row["id"]),
                    display_order_number=int(row["display_order_number"]),
                    status=OrderStatus(row["status"]),
                    total_amount=int(row["total_amount"]),
                    created_at=datetime.fromisoformat(row["created_at"]),
                ),
            )
        return summaries

    def list_orders_by_statuses(
        self,
        statuses: Iterable[OrderStatus],
        limit: int = RECENT_ORDERS_LIMIT,
    ) -> list[OrderSummary]:
        """Возвращает список заказов по набору статусов для админ-панели.

        Args:
            statuses: Итерация статусов заказов, которые нужно выбрать.
            limit: Максимальное количество заказов.

        Returns:
            list[OrderSummary]: Список кратких представлений заказов.
        """

        statuses_list = list(statuses)
        if not statuses_list:
            return []

        placeholders = ",".join("?" for _ in statuses_list)
        query = f"""
                SELECT
                    id,
                    display_order_number,
                    status,
                    total_amount,
                    created_at
                FROM orders
                WHERE status IN ({placeholders})
                ORDER BY created_at DESC
                LIMIT ?;
                """
        params: list[object] = [s.value for s in statuses_list]
        params.append(limit)

        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()

        summaries: list[OrderSummary] = []
        for row in rows:
            summaries.append(
                OrderSummary(
                    id=int(row["id"]),
                    display_order_number=int(row["display_order_number"]),
                    status=OrderStatus(row["status"]),
                    total_amount=int(row["total_amount"]),
                    created_at=datetime.fromisoformat(row["created_at"]),
                ),
            )
        return summaries

    def find_orders_by_display_number(
        self,
        display_order_number: int,
        limit: int = RECENT_ORDERS_LIMIT,
    ) -> list[OrderSummary]:
        """Ищет заказы по отображаемому номеру (4‑значный номер на чеке).

        Args:
            display_order_number: Отображаемый номер заказа (display_order_number).
            limit: Максимальное количество заказов.

        Returns:
            list[OrderSummary]: Список найденных заказов (возможны несколько при коллизиях номера).
        """

        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT
                    id,
                    display_order_number,
                    status,
                    total_amount,
                    created_at
                FROM orders
                WHERE display_order_number = ?
                ORDER BY created_at DESC
                LIMIT ?;
                """,
                (display_order_number, limit),
            )
            rows = cursor.fetchall()

        summaries: list[OrderSummary] = []
        for row in rows:
            summaries.append(
                OrderSummary(
                    id=int(row["id"]),
                    display_order_number=int(row["display_order_number"]),
                    status=OrderStatus(row["status"]),
                    total_amount=int(row["total_amount"]),
                    created_at=datetime.fromisoformat(row["created_at"]),
                ),
            )
        return summaries

    def find_orders_by_phone(
        self,
        phone: str,
        limit: int = RECENT_ORDERS_LIMIT,
    ) -> list[OrderSummary]:
        """Ищет заказы по номеру телефона получателя.

        Args:
            phone: Нормализованный телефон (как сохраняется в orders.phone).
            limit: Максимальное количество заказов.

        Returns:
            list[OrderSummary]: Список заказов для заданного телефона.
        """

        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT
                    id,
                    display_order_number,
                    status,
                    total_amount,
                    created_at
                FROM orders
                WHERE phone = ?
                ORDER BY created_at DESC
                LIMIT ?;
                """,
                (phone, limit),
            )
            rows = cursor.fetchall()

        summaries: list[OrderSummary] = []
        for row in rows:
            summaries.append(
                OrderSummary(
                    id=int(row["id"]),
                    display_order_number=int(row["display_order_number"]),
                    status=OrderStatus(row["status"]),
                    total_amount=int(row["total_amount"]),
                    created_at=datetime.fromisoformat(row["created_at"]),
                ),
            )
        return summaries

    def update_order_status(
        self,
        order_id: int,
        new_status: OrderStatus,
    ) -> Order | None:
        """Обновляет статус заказа.

        Args:
            order_id (int): Идентификатор заказа.
            new_status (OrderStatus): Новый статус заказа.

        Returns:
            Optional[Order]: Обновлённый заказ или None, если он не найден.
        """

        with self._connection() as conn:
            cursor = conn.cursor()
            now = datetime.utcnow().isoformat()
            cursor.execute(
                """
                UPDATE orders
                SET status = ?, updated_at = ?
                WHERE id = ?;
                """,
                (new_status.value, now, order_id),
            )
            if cursor.rowcount == 0:
                return None

        logger.info(
            "Изменён статус заказа id={order_id} на {status}",
            order_id=order_id,
            status=new_status.value,
        )
        return self.get_order(order_id)

    def update_order_payment_method(
        self,
        order_id: int,
        payment_method: str,
        new_status: OrderStatus | None = None,
    ) -> Order | None:
        """Устанавливает способ оплаты заказа и при необходимости меняет статус.

        Args:
            order_id (int): Идентификатор заказа.
            payment_method (str): Способ оплаты: 'cash' или 'yookassa'.
            new_status (Optional[OrderStatus]): Новый статус (например, AWAITING_PAYMENT).

        Returns:
            Optional[Order]: Обновлённый заказ или None.
        """

        with self._connection() as conn:
            cursor = conn.cursor()
            now = datetime.utcnow().isoformat()
            if new_status is not None:
                cursor.execute(
                    """
                    UPDATE orders
                    SET payment_method = ?, status = ?, updated_at = ?
                    WHERE id = ?;
                    """,
                    (payment_method, new_status.value, now, order_id),
                )
            else:
                cursor.execute(
                    """
                    UPDATE orders
                    SET payment_method = ?, updated_at = ?
                    WHERE id = ?;
                    """,
                    (payment_method, now, order_id),
                )
            if cursor.rowcount == 0:
                return None
        return self.get_order(order_id)

    def set_order_opencart_id(self, order_id: int, opencart_order_id: int) -> None:
        """Сохраняет ID заказа в OpenCart для связи с заказом в боте.

        Args:
            order_id (int): Локальный id заказа.
            opencart_order_id (int): ID заказа в OpenCart (api/order/add).
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE orders SET opencart_order_id = ? WHERE id = ?;",
                (opencart_order_id, order_id),
            )

    def set_order_external_payment_id(self, order_id: int, external_payment_id: str) -> None:
        """Сохраняет внешний идентификатор платежа (ЮKassa payment.id) для заказа.

        Args:
            order_id (int): Локальный id заказа.
            external_payment_id (str): Идентификатор платежа в платёжной системе.
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE orders SET external_payment_id = ? WHERE id = ?;",
                (external_payment_id, order_id),
            )

    def get_order_by_external_payment_id(self, external_payment_id: str) -> Order | None:
        """Возвращает заказ по идентификатору платежа ЮKassa.

        Args:
            external_payment_id (str): payment.id из уведомления ЮKassa.

        Returns:
            Order или None, если заказ не найден.
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM orders WHERE external_payment_id = ?;",
                (external_payment_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return self.get_order(int(row["id"]))

    # Статистика

    def get_stats(self) -> StatsSummary:
        """Возвращает агрегированную статистику по пользователям и заказам.

        Returns:
            StatsSummary: Сводная статистика.
        """

        with self._connection() as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) AS cnt FROM users;")
            users_row = cursor.fetchone()
            total_users = int(users_row["cnt"])

            cursor.execute("SELECT COUNT(*) AS cnt FROM orders;")
            orders_row = cursor.fetchone()
            total_orders = int(orders_row["cnt"])

            cursor.execute("SELECT COALESCE(SUM(total_amount), 0) AS revenue FROM orders;")
            revenue_row = cursor.fetchone()
            total_revenue = int(revenue_row["revenue"])

        return StatsSummary(
            total_users=total_users,
            total_orders=total_orders,
            total_revenue=total_revenue,
        )

    def count_orders(self) -> int:
        """Возвращает общее количество заказов."""

        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) AS cnt FROM orders;")
            row = cursor.fetchone()
        return int(row["cnt"]) if row is not None else 0

    def list_orders_between(
        self,
        from_dt: datetime,
        to_dt: datetime,
    ) -> list[Order]:
        """Возвращает заказы, созданные в указанном периоде (включительно).

        Args:
            from_dt: Начало периода.
            to_dt: Конец периода.

        Returns:
            list[Order]: Список заказов с позициями, от новых к старым.
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id FROM orders
                WHERE created_at >= ? AND created_at <= ?
                ORDER BY created_at DESC;
                """,
                (from_dt.isoformat(), to_dt.isoformat()),
            )
            rows = cursor.fetchall()
        order_ids = [int(r["id"]) for r in rows]
        result: list[Order] = []
        for oid in order_ids:
            order = self.get_order(oid)
            if order is not None:
                result.append(order)
        return result

    def get_top_products_by_sales(self, limit: int = 5) -> list[tuple[str, int]]:
        """Возвращает топ товаров по количеству проданных единиц.

        Args:
            limit: Максимальное количество позиций.

        Returns:
            list[tuple[str, int]]: Пары (название товара, суммарное количество).
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT p.title, SUM(oi.quantity) AS total_qty
                FROM order_items oi
                JOIN products p ON p.id = oi.product_id
                GROUP BY oi.product_id
                ORDER BY total_qty DESC
                LIMIT ?;
                """,
                (limit,),
            )
            rows = cursor.fetchall()
        return [(str(row["title"]), int(row["total_qty"])) for row in rows]

    def get_revenue_by_city(self) -> list[tuple[str, int]]:
        """Возвращает выручку по городам доставки.

        Returns:
            list[tuple[str, int]]: Пары (город, выручка в рублях).
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COALESCE(delivery_city, '—') AS city, SUM(total_amount) AS revenue
                FROM orders
                GROUP BY delivery_city
                ORDER BY revenue DESC;
                """,
            )
            rows = cursor.fetchall()
        return [(str(row["city"]), int(row["revenue"])) for row in rows]

    def get_orders_count_by_status(self) -> list[tuple[OrderStatus, int]]:
        """Возвращает количество заказов по каждому статусу.

        Returns:
            list[tuple[OrderStatus, int]]: Пары (статус, количество).
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT status, COUNT(*) AS cnt
                FROM orders
                GROUP BY status
                ORDER BY cnt DESC;
                """,
            )
            rows = cursor.fetchall()
        return [(OrderStatus(row["status"]), int(row["cnt"])) for row in rows]

    # Преобразование SQLite-строк в модели

    @staticmethod
    def _row_to_user(row: sqlite3.Row) -> User:
        """Преобразует строку SQLite в модель пользователя.

        Args:
            row (sqlite3.Row): Строка результата запроса.

        Returns:
            User: Модель пользователя.
        """

        return User(
            id=int(row["id"]),
            tg_id=int(row["tg_id"]),
            first_name=row["first_name"],
            last_name=row["last_name"],
            phone=row["phone"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _row_to_product(row: sqlite3.Row) -> Product:
        """Преобразует строку SQLite в модель товара.

        Args:
            row (sqlite3.Row): Строка результата запроса.

        Returns:
            Product: Модель товара.
        """

        # sqlite3.Row реализует интерфейс последовательности; для безопасного доступа к
        # дополнительным колонкам преобразуем строку к словарю по именам колонок.
        row_dict = dict(row)
        raw_oc_id = row_dict.get("opencart_product_id")
        opencart_product_id = int(raw_oc_id) if raw_oc_id is not None else None
        return Product(
            id=int(row_dict["id"]),
            category_id=int(row_dict["category_id"]),
            title=str(row_dict["title"]),
            description=str(row_dict["description"]),
            price=int(row_dict["price"]),
            image_url=str(row_dict["image_url"]),
            is_active=bool(row_dict["is_active"]),
            opencart_product_id=opencart_product_id,
        )

    @staticmethod
    def _rows_to_order_items(
        rows: Iterable[sqlite3.Row],
    ) -> list[OrderItem]:
        """Преобразует набор строк SQLite в список позиций заказа.

        Args:
            rows (Iterable[sqlite3.Row]): Набор строк результата запроса.

        Returns:
            List[OrderItem]: Список позиций заказа.
        """

        items: list[OrderItem] = []
        for row in rows:
            product = Database._row_to_product(row)
            items.append(
                OrderItem(
                    id=int(row["id"]),
                    order_id=int(row["order_id"]),
                    product_id=int(row["product_id"]),
                    quantity=int(row["quantity"]),
                    unit_price=int(row["unit_price"]),
                    product=product,
                ),
            )
        return items

    @staticmethod
    def _row_to_order(
        row: sqlite3.Row,
        items: list[OrderItem],
    ) -> Order:
        """Преобразует строку SQLite в модель заказа.

        Args:
            row (sqlite3.Row): Строка результата запроса.
            items (List[OrderItem]): Список позиций заказа.

        Returns:
            Order: Модель заказа.
        """

        # sqlite3.Row ведёт себя как последовательность; для безопасного доступа по
        # именам колонок приводим строку к словарю.
        row_dict = dict(row)
        delivery_city = row_dict.get("delivery_city")
        delivery_cost = row_dict.get("delivery_cost")
        desired_dt = row_dict.get("desired_delivery_datetime")
        pm = row_dict.get("payment_method")
        oc_oid = row_dict.get("opencart_order_id")
        opencart_order_id = int(oc_oid) if oc_oid is not None else None
        order_email = row_dict.get("email")
        return Order(
            id=int(row_dict["id"]),
            user_id=int(row_dict["user_id"]),
            status=OrderStatus(row_dict["status"]),
            total_amount=int(row_dict["total_amount"]),
            created_at=datetime.fromisoformat(row_dict["created_at"]),
            updated_at=datetime.fromisoformat(row_dict["updated_at"]),
            delivery_address=str(row_dict["delivery_address"]),
            customer_name=str(row_dict["customer_name"]),
            phone=str(row_dict["phone"]),
            email=str(order_email) if order_email else None,
            comment=row["comment"],
            external_payment_id=row["external_payment_id"],
            opencart_order_id=opencart_order_id,
            display_order_number=int(row["display_order_number"]),
            delivery_city=str(delivery_city) if delivery_city else None,
            delivery_cost=int(delivery_cost) if delivery_cost is not None else 0,
            desired_delivery_datetime=str(desired_dt) if desired_dt else None,
            payment_method=str(pm) if pm else None,
            items=items,
        )


def create_default_database() -> Database:
    """Создаёт экземпляр базы данных с конфигурацией по умолчанию.

    Returns:
        Database: Инициализированный экземпляр класса Database.
    """

    base_dir = Path(__file__).resolve().parent
    db_path = base_dir / DB_FILENAME
    logger.info("Инициализация SQLite бота: db_path={db_path}", db_path=db_path)
    config = DatabaseConfig(db_path=db_path)
    return Database(config=config)
