"""Сборка текста отчёта о состоянии процесса бота для админов (без SSH)."""

from __future__ import annotations

import asyncio
import os
import subprocess
from html import escape
from pathlib import Path

from loguru import logger

from ..core.runtime_info import format_uptime_human, get_bot_started_at

# Запас под заголовок и теги HTML; лимит Telegram — 4096.
_MAX_LOG_CHARS: int = 2600


def _tail_text_file(path: Path, max_lines: int) -> str:
    """Читает последние строки текстового файла.

    Args:
        path (Path): Путь к файлу.
        max_lines (int): Максимум строк с конца.

    Returns:
        str: Текст или пустая строка при ошибке.
    """

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.debug("Не удалось прочитать лог-файл {path}: {err}", path=path, err=e)
        return ""
    lines = content.splitlines()
    return "\n".join(lines[-max_lines:])


def _truncate_chars(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return "…\n" + text[-max_chars:]


async def _run_subprocess(
    cmd: list[str],
    *,
    timeout_sec: float = 12.0,
) -> tuple[int, str]:
    """Запускает команду вне event loop (блокирующий subprocess).

    Args:
        cmd (list[str]): Аргументы команды (без shell).
        timeout_sec (float): Таймаут в секундах.

    Returns:
        tuple[int, str]: Код возврата и объединённый stdout+stderr.
    """

    def _sync() -> tuple[int, str]:
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
        except OSError as e:
            return 127, str(e)
        except subprocess.TimeoutExpired:
            return 124, "timeout"
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, out.strip()

    return await asyncio.to_thread(_sync)


async def build_bot_status_html() -> str:
    """Формирует HTML-сообщение со статусом процесса и хвостом лога.

    Использует BOT_LOG_PATH (если задан), иначе пытается journalctl для unit из
    BOT_SYSTEMD_UNIT (по умолчанию detali-bot.service). Состояние systemd
    опрашивается через systemctl, если доступно.

    Args:
        Нет.

    Returns:
        str: HTML-текст для отправки в Telegram (parse_mode HTML).
    """

    pid = os.getpid()
    started = get_bot_started_at()
    started_s = started.strftime("%Y-%m-%d %H:%M:%S UTC") if started else "—"
    uptime = format_uptime_human()

    unit = (os.getenv("BOT_SYSTEMD_UNIT") or "detali-bot.service").strip() or "detali-bot.service"

    systemd_lines: list[str] = []
    code, out = await _run_subprocess(["systemctl", "is-active", unit])
    systemd_lines.append(f"is-active: {out or '(пусто)'} (код {code})")

    code_show, out_show = await _run_subprocess(
        ["systemctl", "show", unit, "-p", "ActiveState", "-p", "SubState"],
    )
    if out_show:
        systemd_lines.append(f"show:\n{out_show} (код {code_show})")

    log_path_raw = (os.getenv("BOT_LOG_PATH") or "").strip()
    log_tail = ""
    log_source = ""

    if log_path_raw:
        p = Path(log_path_raw)
        if p.is_file():
            log_tail = _tail_text_file(p, 45)
            log_source = f"файл {log_path_raw}"
        else:
            log_source = f"файл {log_path_raw} (не найден)"
    else:
        log_source = "BOT_LOG_PATH не задан"

    if not log_tail:
        jcode, jout = await _run_subprocess(
            ["journalctl", "-u", unit, "-n", "35", "--no-pager", "-o", "short"],
        )
        if jcode == 0 and jout:
            log_tail = jout
            log_source = f"journalctl -u {unit}"
        elif not log_tail:
            log_tail = jout or "(journalctl недоступен или пусто)"

    log_tail = _truncate_chars(log_tail, _MAX_LOG_CHARS)

    systemd_block = escape("\n".join(systemd_lines))
    log_block = escape(log_tail) if log_tail else "(нет данных)"

    return (
        "<b>📊 Статус процесса бота</b>\n"
        f"PID: <code>{pid}</code>\n"
        f"Старт: <code>{escape(started_s)}</code>\n"
        f"Uptime: <code>{escape(uptime)}</code>\n\n"
        f"<b>systemd ({escape(unit)})</b>\n<pre>{systemd_block}</pre>\n\n"
        f"<b>Лог ({escape(log_source)})</b>\n<pre>{log_block}</pre>"
    )
