#!/usr/bin/env python3
"""Удаляет все заказы из БД бота (order_items, admin_order_notifications, orders).

Использование: из корня проекта
  uv run python scripts/clear_test_orders.py
  или
  cd /root/Detali_Bot && uv run python scripts/clear_test_orders.py

Перед запуском желательно остановить бота: sudo systemctl stop detali-bot
После — запустить: sudo systemctl start detali-bot
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def main() -> int:
    base_dir = Path(__file__).resolve().parent.parent
    db_path = base_dir / "bot" / "database" / "bot.sqlite3"
    if not db_path.is_file():
        print(f"Файл БД не найден: {db_path}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
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
