import datetime
import logging

import requests

from . import admin
from .config import Config, ROUTES
from .filters import Filter
from .subscribers import load_offset, load_subscribers, save_offset, save_subscribers

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
LONG_POLL_SECONDS = 25

# Stable labels identifying each direction - also used as the values stored
# in a subscriber's `directions` filter (see filters.Filter / checker.RouteSnapshot.label).
DIRECTION_LABELS = [f"{o.display} → {d.display}" for o, d in ROUTES]

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

def _main_panel(entry: dict) -> dict:
    return {
        "inline_keyboard": [
            [{"text": "🧭 Направления", "callback_data": "filter:menu:directions"}],
            [{"text": "📅 Установить фильтр по дате", "callback_data": "filter:ask:date"}],
            [{"text": "💰 Установить фильтр по цене (не более)", "callback_data": "filter:ask:price"}],
            [{"text": "♻️ Сбросить все фильтры", "callback_data": "filter:reset"}],
            [{"text": "❌ Закрыть", "callback_data": "filter:close"}],
        ]
    }


def _directions_panel(entry: dict) -> dict:
    active = entry.get("directions")
    buttons = []
    for i, label in enumerate(DIRECTION_LABELS):
        is_on = active is None or label in active
        mark = "✅" if is_on else "⬜"
        buttons.append([{"text": f"{mark} {label}", "callback_data": f"filter:dir:{i}"}])
    buttons.append([{"text": "⬅️ Назад", "callback_data": "filter:menu:main"}])
    return {"inline_keyboard": buttons}


def _call(token: str, method: str, **params) -> dict:
    url = TELEGRAM_API.format(token=token, method=method)
    resp = requests.post(url, json=params, timeout=LONG_POLL_SECONDS + 10)
    resp.raise_for_status()
    return resp.json()


def _handle_admin_callback(config: Config, token: str, chat_id: str, message: dict, data: str) -> None:
    message_id = message.get("message_id")
    if message_id is None:
        return

    if data == "admin:users":
        _call(
            token, "editMessageText", chat_id=chat_id, message_id=message_id,
            text=admin.format_users_list(config.subscribers_file),
            reply_markup=admin.ADMIN_PANEL,
        )
    elif data == "admin:logs":
        _call(
            token, "editMessageText", chat_id=chat_id, message_id=message_id,
            text=admin.format_logs_excerpt(config.log_file),
            reply_markup=admin.ADMIN_PANEL,
        )
    elif data == "admin:close":
        _call(token, "editMessageReplyMarkup", chat_id=chat_id, message_id=message_id, reply_markup={"inline_keyboard": []})


def _handle_callback(config: Config, callback_query: dict, subscribers: dict) -> bool:
    token = config.telegram_bot_token
    callback_id = callback_query["id"]
    message = callback_query.get("message") or {}
    chat_id = message.get("chat", {}).get("id")
    data = callback_query.get("data", "")
    changed = False
    answer_text = ""

    if chat_id is not None:
        chat_id = str(chat_id)

        if data.startswith("admin:"):
            if admin.is_admin(chat_id, config):
                _handle_admin_callback(config, token, chat_id, message, data)
            _call(token, "answerCallbackQuery", callback_query_id=callback_id)
            return False

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
        elif data == "filter:close":
            message_id = message.get("message_id")
            if message_id is not None:
                _call(
                    token, "editMessageReplyMarkup", chat_id=chat_id, message_id=message_id,
                    reply_markup={"inline_keyboard": []},
                )
        elif data == "filter:menu:directions":
            message_id = message.get("message_id")
            if message_id is not None:
                filt = Filter.from_dict(entry)
                _call(
                    token, "editMessageText", chat_id=chat_id, message_id=message_id,
                    text=f"Текущий фильтр:\n{filt.describe()}",
                    reply_markup=_directions_panel(entry),
                )
        elif data == "filter:menu:main":
            message_id = message.get("message_id")
            if message_id is not None:
                filt = Filter.from_dict(entry)
                _call(
                    token, "editMessageText", chat_id=chat_id, message_id=message_id,
                    text=f"Текущий фильтр:\n{filt.describe()}",
                    reply_markup=_main_panel(entry),
                )
        elif data.startswith("filter:dir:"):
            idx = int(data.rsplit(":", 1)[1])
            label = DIRECTION_LABELS[idx]
            active = set(entry["directions"]) if entry.get("directions") is not None else set(DIRECTION_LABELS)

            if label in active and len(active) == 1:
                answer_text = "Нельзя отключить все направления сразу."
            else:
                active.symmetric_difference_update({label})
                if active == set(DIRECTION_LABELS):
                    entry.pop("directions", None)
                else:
                    entry["directions"] = sorted(active)
                changed = True
                message_id = message.get("message_id")
                if message_id is not None:
                    filt = Filter.from_dict(entry)
                    _call(
                        token, "editMessageText", chat_id=chat_id, message_id=message_id,
                        text=f"Текущий фильтр:\n{filt.describe()}",
                        reply_markup=_directions_panel(entry),
                    )

    _call(token, "answerCallbackQuery", callback_query_id=callback_id, text=answer_text)
    return changed


def _handle_pending_reply(token: str, chat_id: str, entry: dict, text: str) -> None:
    pending = entry.pop("pending", None)
    text = text.strip()
    ok = True

    if pending == "price":
        if text.lower() in RESET_WORDS:
            entry.pop("max_price", None)
        else:
            try:
                value = float(text.replace(",", "."))
                if value <= 0:
                    raise ValueError
                entry["max_price"] = value
            except ValueError:
                entry["pending"] = "price"
                ok = False
                reply = "Не понял цену. Введите число, например 150 (или 0, чтобы снять ограничение)."

    elif pending == "date":
        if text.lower() in RESET_WORDS:
            entry.pop("date_from", None)
            entry.pop("date_to", None)
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
            except ValueError:
                entry["pending"] = "date"
                ok = False
                reply = (
                    "Не понял даты. Формат: ДД-ММ-ГГГГ (одна дата) или ДД-ММ-ГГГГ ДД-ММ-ГГГГ (диапазон), "
                    "например: 01-07-2026 или 01-07-2026 15-08-2026 (или 0, чтобы снять ограничение)."
                )
    else:
        return

    # On success, show the same "current filter" summary + panel used
    # everywhere else, so the subscriber can keep adjusting right away.
    if ok:
        filt = Filter.from_dict(entry)
        reply = f"Текущий фильтр:\n{filt.describe()}"
    kwargs = {"reply_markup": _main_panel(entry)} if ok else {}
    _call(token, "sendMessage", chat_id=chat_id, text=reply, **kwargs)


def poll_updates_once(config: Config) -> None:
    """Blocks up to LONG_POLL_SECONDS waiting for new Telegram updates, then
    processes any commands / button presses / filter-value replies found.
    """
    token = config.telegram_bot_token
    subscribers_file = config.subscribers_file
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
            if _handle_callback(config, update["callback_query"], subscribers):
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
                text=f"Текущий фильтр:\n{filt.describe()}",
                reply_markup=_main_panel(entry),
            )
        elif command == "/help":
            _call(token, "sendMessage", chat_id=chat_id, text=HELP_TEXT)
        elif command == "/admin":
            if not admin.is_admin(chat_id, config):
                _call(token, "sendMessage", chat_id=chat_id, text="⛔ Эта команда доступна только администратору.")
                continue
            _call(token, "sendMessage", chat_id=chat_id, text="Админ-панель:", reply_markup=admin.ADMIN_PANEL)

    if changed:
        save_subscribers(subscribers_file, subscribers)
    save_offset(subscribers_file, next_offset)
