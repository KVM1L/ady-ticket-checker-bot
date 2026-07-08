import datetime
import logging

import requests

from .filters import Filter
from .subscribers import load_offset, load_subscribers, save_offset, save_subscribers

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
LONG_POLL_SECONDS = 25

WELCOME_TEXT = (
    "✅ Подписка оформлена.\n"
    "Буду присылать уведомления об изменениях по билетам Bakı ⇄ Tbilisi "
    "(новые даты, изменение цены, распродажа) на ближайшие два месяца.\n\n"
    "/filter — настроить фильтр по цене и датам\n"
    "/stop — отписаться от уведомлений"
)
GOODBYE_TEXT = "🔕 Вы отписались от уведомлений. Чтобы снова подписаться — отправьте /start."
HELP_TEXT = (
    "/start — подписаться на уведомления\n"
    "/stop — отписаться\n"
    "/filter — настроить фильтр по цене и датам"
)

RESET_WORDS = ("0", "off", "выкл", "нет")

FILTER_PANEL = {
    "inline_keyboard": [
        [{"text": "📅 Установить фильтр по дате", "callback_data": "filter:ask:date"}],
        [{"text": "💰 Установить фильтр по цене (не более)", "callback_data": "filter:ask:price"}],
        [{"text": "♻️ Сбросить все фильтры", "callback_data": "filter:reset"}],
    ]
}


def _call(token: str, method: str, **params) -> dict:
    url = TELEGRAM_API.format(token=token, method=method)
    resp = requests.post(url, json=params, timeout=LONG_POLL_SECONDS + 10)
    resp.raise_for_status()
    return resp.json()


def _handle_callback(token: str, callback_query: dict, subscribers: dict) -> bool:
    callback_id = callback_query["id"]
    chat_id = (callback_query.get("message") or {}).get("chat", {}).get("id")
    data = callback_query.get("data", "")
    changed = False

    if chat_id is not None:
        chat_id = str(chat_id)
        entry = subscribers.setdefault(chat_id, {})

        if data == "filter:ask:price":
            entry["pending"] = "price"
            changed = True
            _call(
                token, "sendMessage", chat_id=chat_id,
                text="Введите максимальную цену в AZN (например: 150). Чтобы снять ограничение — отправьте 0.",
            )
        elif data == "filter:ask:date":
            entry["pending"] = "date"
            changed = True
            _call(
                token, "sendMessage", chat_id=chat_id,
                text=(
                    "Введите дату в формате ДД-ММ-ГГГГ (например: 01-07-2026 — только эта дата) "
                    "или диапазон ДД-ММ-ГГГГ ДД-ММ-ГГГГ (например: 01-07-2026 15-08-2026). "
                    "Чтобы снять ограничение — отправьте 0."
                ),
            )
        elif data == "filter:reset":
            entry.clear()
            changed = True
            _call(token, "sendMessage", chat_id=chat_id, text="✅ Все фильтры сброшены, буду показывать все билеты.")

    _call(token, "answerCallbackQuery", callback_query_id=callback_id)
    return changed


def _handle_pending_reply(token: str, chat_id: str, entry: dict, text: str) -> None:
    pending = entry.pop("pending", None)
    text = text.strip()

    if pending == "price":
        if text.lower() in RESET_WORDS:
            entry.pop("max_price", None)
            reply = "✅ Ограничение по цене снято."
        else:
            try:
                value = float(text.replace(",", "."))
                if value <= 0:
                    raise ValueError
                entry["max_price"] = value
                reply = f"✅ Буду показывать билеты дешевле {value:g} AZN."
            except ValueError:
                entry["pending"] = "price"
                reply = "Не понял цену. Введите число, например 150 (или 0, чтобы снять ограничение)."
        _call(token, "sendMessage", chat_id=chat_id, text=reply)
        return

    if pending == "date":
        if text.lower() in RESET_WORDS:
            entry.pop("date_from", None)
            entry.pop("date_to", None)
            reply = "✅ Ограничение по датам снято."
        else:
            parts = text.split()
            try:
                if len(parts) == 1:
                    d_from = d_to = datetime.datetime.strptime(parts[0], "%d-%m-%Y").date()
                elif len(parts) == 2:
                    d_from = datetime.datetime.strptime(parts[0], "%d-%m-%Y").date()
                    d_to = datetime.datetime.strptime(parts[1], "%d-%m-%Y").date()
                    if d_from > d_to:
                        d_from, d_to = d_to, d_from
                else:
                    raise ValueError
                entry["date_from"] = d_from.strftime("%d-%m-%Y")
                entry["date_to"] = d_to.strftime("%d-%m-%Y")
                if d_from == d_to:
                    reply = f"✅ Буду показывать билеты только на {entry['date_from']}."
                else:
                    reply = f"✅ Буду показывать билеты с {entry['date_from']} по {entry['date_to']}."
            except ValueError:
                entry["pending"] = "date"
                reply = (
                    "Не понял даты. Формат: ДД-ММ-ГГГГ (одна дата) или ДД-ММ-ГГГГ ДД-ММ-ГГГГ (диапазон), "
                    "например: 01-07-2026 или 01-07-2026 15-08-2026 (или 0, чтобы снять ограничение)."
                )
        _call(token, "sendMessage", chat_id=chat_id, text=reply)


def poll_updates_once(token: str, subscribers_file: str) -> None:
    """Blocks up to LONG_POLL_SECONDS waiting for new Telegram updates, then
    processes any commands / button presses / filter-value replies found.
    """
    offset = load_offset(subscribers_file)
    result = _call(
        token,
        "getUpdates",
        offset=offset,
        timeout=LONG_POLL_SECONDS,
        allowed_updates=["message", "callback_query"],
    )
    updates = result.get("result", [])
    if not updates:
        return

    subscribers = load_subscribers(subscribers_file)
    changed = False
    next_offset = offset

    for update in updates:
        next_offset = update["update_id"] + 1

        if "callback_query" in update:
            if _handle_callback(token, update["callback_query"], subscribers):
                changed = True
            continue

        message = update.get("message") or {}
        chat_id = message.get("chat", {}).get("id")
        text = (message.get("text") or "").strip()
        if chat_id is None or not text:
            continue
        chat_id = str(chat_id)

        entry = subscribers.get(chat_id)
        if entry and entry.get("pending") and not text.startswith("/"):
            _handle_pending_reply(token, chat_id, entry, text)
            changed = True
            continue

        command = text.split()[0].split("@")[0].lower()

        if command == "/start":
            if chat_id not in subscribers:
                subscribers[chat_id] = {}
                changed = True
                log.info("New subscriber: %s", chat_id)
            _call(token, "sendMessage", chat_id=chat_id, text=WELCOME_TEXT)
        elif command == "/stop":
            if chat_id in subscribers:
                del subscribers[chat_id]
                changed = True
                log.info("Unsubscribed: %s", chat_id)
            _call(token, "sendMessage", chat_id=chat_id, text=GOODBYE_TEXT)
        elif command == "/filter":
            if chat_id not in subscribers:
                _call(token, "sendMessage", chat_id=chat_id, text="Сначала подпишитесь: /start")
                continue
            entry = subscribers.setdefault(chat_id, {})
            entry.pop("pending", None)
            changed = True
            filt = Filter.from_dict(entry)
            _call(
                token, "sendMessage", chat_id=chat_id,
                text=f"Текущий фильтр: {filt.describe()}",
                reply_markup=FILTER_PANEL,
            )
        elif command == "/help":
            _call(token, "sendMessage", chat_id=chat_id, text=HELP_TEXT)

    if changed:
        save_subscribers(subscribers_file, subscribers)
    save_offset(subscribers_file, next_offset)
