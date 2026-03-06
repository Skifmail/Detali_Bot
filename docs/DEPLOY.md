# Деплой бота на боевой сервер

Чеклист перед и после выката.

---

## 1. До деплоя

### 1.1. IP сервера и доступ к MySQL

- Узнать **внешний IP** боевого сервера (например `curl -s ifconfig.me` или в панели VPS).
- Передать админу БД (или в панель Beget): **добавить этот IP в разрешённые хосты MySQL** для пользователя `dai10v_detal`. Иначе будет `Access denied for user 'dai10v_detal'@'...'`.
- Убедиться, что пароль БД в `.env` совпадает с тем, что выдал админ.

### 1.2. Переменные окружения

- На сервере должен быть файл **`.env`** (в корне проекта или в `bot/`). Скопировать с `bot/.env.example`, заполнить:
  - **BOT_TOKEN** — токен от @BotFather.
  - **ADMIN_IDS** — Telegram ID через запятую (хотя бы один «главный» админ). Остальных админов можно добавлять и удалять в боте: Ещё → Администраторы.
  - **OPENCART_*** — API и БД (хост `dai10v.beget.tech`, логин/пароль/база от админа).
  - **BOT_OFFER_URL**, **BOT_PRIVACY_POLICY_URL** (опционально) — ссылки на оферту и политику конфиденциальности; при задании в экране подтверждения заказа появятся кнопки для перехода к документам (см. `docs/LEGAL_COMPLIANCE.md`).
  - **BOT_LOG_PATH** (опционально) — путь к файлу логов (например `/var/log/detali-bot/bot.log`). Если задан, логи дополнительно пишутся в файл с ротацией (1 MB, хранится 3 файла). Иначе логи только в stdout (терминал / journald).
- На **боевом** сервере **не** задавать `SKIP_OPENCART_SYNC=1` (или удалить эту строку), иначе каталог не будет подтягиваться из OpenCart.

### 1.3. Зависимости и версия Python

- Python **3.12+**.
- Установка: `uv sync` (или `pip install -r requirements.txt` из каталога с `requirements.txt`).

---

## 2. Запуск

Из **корня** репозитория:

```bash
uv run python -m bot.main
```

или из каталога `bot/`:

```bash
cd bot && python -m bot.main
```

При запуске из корня подхватится `bot/.env`, если он есть.

### 2.1. Постоянная работа (systemd)

- Запускать бота как долгоживущий процесс (systemd unit), а не разовым cron.
- Готовый unit: `deploy/detali-bot.service`. На сервере:
  1. Подставить в unit путь к проекту и пользователю (по умолчанию `/root/Detali_Bot`, пользователь `root`).
  2. Убедиться, что в проекте есть виртуальное окружение: из корня проекта выполнить `uv sync` (появится `.venv/`). В unit используется `.venv/bin/python`.
  3. Скопировать unit и включить сервис:
     ```bash
     sudo cp deploy/detali-bot.service /etc/systemd/system/
     sudo systemctl daemon-reload
     sudo systemctl enable detali-bot
     sudo systemctl start detali-bot
     sudo systemctl status detali-bot
     ```
- В unit должна быть секция `[Install]` с `WantedBy=multi-user.target`, и в `[Service]` — строка `ExecStart=...`. Файл должен начинаться с `[Unit]` (без пустой строки или лишнего текста в начале).

### 2.2. Docker

- Сборка: из корня проекта `docker build -t detali-bot .`
- Запуск с подмонтированным каталогом для БД и `.env`:
  - Volume для SQLite: `-v /path/on/host/bot-data:/app/bot/database`
  - Файл с секретами: `--env-file /path/to/.env` или volume с `.env` в `/app`/`/app/bot`
- Команда в образе: `python -m bot.main`.

---

## 3. Обновление бота на сервере

После изменений в коде (локально или через репозиторий):

1. На сервере перейти в каталог проекта и подтянуть изменения:
   ```bash
   cd /root/Detali_Bot
   git pull
   ```
   (Если код заливается без git — скопировать изменённые файлы на сервер вручную.)

2. При необходимости обновить зависимости:
   ```bash
   uv sync
   ```

3. Перезапустить сервис:
   ```bash
   sudo systemctl restart detali-bot
   sudo systemctl status detali-bot
   ```

БД SQLite и `bot/.env` при этом не трогаются — данные и настройки сохраняются.

---

## 3.1. Очистка тестовых заказов

Чтобы удалить из БД **все заказы** (тестовые и реальные; пользователи и каталог не трогаются):

1. Остановить бота (чтобы БД не была занята):
   ```bash
   sudo systemctl stop detali-bot
   ```
2. Выполнить скрипт из корня проекта (сначала можно проверить: `--dry-run` — только показать, сколько записей будет удалено):
   ```bash
   cd /root/Detali_Bot
   uv run python scripts/clear_test_orders.py --dry-run   # проверка
   uv run python scripts/clear_test_orders.py             # реальное удаление
   ```
3. Запустить бота:
   ```bash
   sudo systemctl start detali-bot
   ```

**Вариант вручную через sqlite3:**  
`sqlite3 /root/Detali_Bot/bot/database/bot.sqlite3 "DELETE FROM order_items; DELETE FROM admin_order_notifications; DELETE FROM orders;"`  
(бот перед этим тоже лучше остановить.)

---

## 4. После деплоя

- Открыть бота в Telegram, отправить `/start`.
- Зайти в каталог — товары должны подтянуться из OpenCart (если MySQL доступен и `SKIP_OPENCART_SYNC` не установлен).
- Проверить оформление заказа и, при необходимости, оплату (ЮKassa и т.д.).

---

## 5. Важно

- Файлы **`.env`** и **`bot/.env`** в `.gitignore` — в репозиторий не попадают. На сервере их создают вручную из `bot/.env.example`.
- SQLite (`bot/database/bot.sqlite3`) хранит каталог, заказы, пользователей. При деплое в Docker нужен volume, чтобы данные не терялись при пересоздании контейнера.
