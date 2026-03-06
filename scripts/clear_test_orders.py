#!/usr/bin/env python3
"""Удаляет все заказы из БД бота (order_items, admin_order_notifications, orders).

Использование: из корня проекта
  uv run python scripts/clear_test_orders.py           # реальное удаление
  uv run python scripts/clear_test_orders.py --dry-run # только показать, что будет удалено

Перед запуском желательно остановить бота: sudo systemctl stop detali-bot
После — запустить: sudo systemctl start detali-bot
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Очистка тестовых заказов из БД бота.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Не удалять, только показать количество записей, которые будут удалены.",
    )
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent.parent
    db_path = base_dir / "bot" / "database" / "bot.sqlite3"
    if not db_path.is_file():
        print(f"Файл БД не найден: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        if args.dry_run:
            cursor.execute("SELECT COUNT(*) FROM order_items;")
            n_items = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM admin_order_notifications;")
            n_notif = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM orders;")
            n_orders = cursor.fetchone()[0]
            print(
                f"Режим dry-run. Будет удалено: заказов={n_orders}, "
                f"позиций заказов={n_items}, уведомлений админам={n_notif}."
            )
            print("Для реального удаления запустите без --dry-run.")
        else:
            cursor.execute("DELETE FROM order_items;")
            cursor.execute("DELETE FROM admin_order_notifications;")
            cursor.execute("DELETE FROM orders;")
            conn.commit()
            print("Удалены все заказы, позиции заказов и уведомления админам о заказах.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
