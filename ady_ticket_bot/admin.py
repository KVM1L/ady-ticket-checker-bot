import html
import logging
import os

import requests

from .config import Config
from .filters import Filter
from .subscribers import load_subscribers

log = logging.getLogger(__name__)

LOG_EXCERPT_CHARS = 3000

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"

PUBLIC_COMMANDS = [
    {"command": "start", "description": "Подписаться на уведомления о билетах"},
    {"command": "stop", "description": "Отписаться от уведомлений"},
    {"command": "filter", "description": "Настроить фильтр по цене и датам"},
    {"command": "help", "description": "Список команд"},
]

ADMIN_PANEL = {
    "inline_keyboard": [
        [{"text": "👥 Пользователи", "callback_data": "admin:users"}],
        [{"text": "📜 Логи", "callback_data": "admin:logs"}],
        [{"text": "❌ Закрыть", "callback_data": "admin:close"}],
    ]
}


def is_admin(chat_id: str, config: Config) -> bool:
    return bool(config.admin_chat_id) and chat_id == config.admin_chat_id


def sync_command_menu(config: Config) -> None:
    """Sets the default (public) command list for everyone, and a separate
    chat-scoped list including /admin just for the admin's own chat - so
    /admin only shows up in Telegram's "/" suggestions for the admin."""
    url = TELEGRAM_API.format(token=config.telegram_bot_token, method="setMyCommands")
    try:
        requests.post(url, json={"commands": PUBLIC_COMMANDS}, timeout=15)
        if config.admin_chat_id:
            requests.post(
                url,
                json={
                    "commands": PUBLIC_COMMANDS + [{"command": "admin", "description": "Админ-панель"}],
                    "scope": {"type": "chat", "chat_id": int(config.admin_chat_id)},
                },
                timeout=15,
            )
    except Exception:
        log.exception("Failed to sync Telegram command menu")


def format_users_list(subscribers_file: str) -> str:
    subscribers = load_subscribers(subscribers_file)
    if not subscribers:
        return "Подписчиков пока нет."

    rows = []
    for chat_id, entry in sorted(subscribers.items()):
        filt = Filter.from_dict(entry).describe().replace("\n", "; ")
        rows.append(f"{chat_id:<12} {filt}")

    return f"👥 <b>Подписчики ({len(subscribers)})</b>\n<pre>{html.escape(chr(10).join(rows))}</pre>"


def format_logs_excerpt(log_file: str) -> str:
    if not os.path.exists(log_file):
        return "Файл лога пока не создан."

    with open(log_file, "r", encoding="utf-8", errors="replace") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - LOG_EXCERPT_CHARS * 4), os.SEEK_SET)  # bytes are usually >= chars, generous seek
        tail = f.read()[-LOG_EXCERPT_CHARS:]

    if not tail.strip():
        return "Лог пуст."

    return f"📜 <b>Последние строки лога</b>\n<pre>{html.escape(tail)}</pre>"
