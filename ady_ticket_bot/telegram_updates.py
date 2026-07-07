import logging

import requests

from .subscribers import load_offset, load_subscribers, save_offset, save_subscribers

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
LONG_POLL_SECONDS = 25

WELCOME_TEXT = (
    "✅ Подписка оформлена.\n"
    "Буду присылать уведомления об изменениях по билетам Bakı ⇄ Tbilisi "
    "(новые даты, изменение цены, распродажа) на ближайшие два месяца.\n\n"
    "/stop — отписаться от уведомлений"
)
GOODBYE_TEXT = "🔕 Вы отписались от уведомлений. Чтобы снова подписаться — отправьте /start."


def _call(token: str, method: str, **params) -> dict:
    url = TELEGRAM_API.format(token=token, method=method)
    resp = requests.post(url, json=params, timeout=LONG_POLL_SECONDS + 10)
    resp.raise_for_status()
    return resp.json()


def poll_updates_once(token: str, subscribers_file: str) -> None:
    """Blocks up to LONG_POLL_SECONDS waiting for new Telegram messages, then
    processes any /start or /stop commands found and returns.
    """
    offset = load_offset(subscribers_file)
    result = _call(
        token,
        "getUpdates",
        offset=offset,
        timeout=LONG_POLL_SECONDS,
        allowed_updates=["message"],
    )
    updates = result.get("result", [])
    if not updates:
        return

    subscribers = load_subscribers(subscribers_file)
    changed = False
    next_offset = offset

    for update in updates:
        next_offset = update["update_id"] + 1
        message = update.get("message") or {}
        chat_id = message.get("chat", {}).get("id")
        text = (message.get("text") or "").strip()
        if chat_id is None or not text:
            continue

        chat_id = str(chat_id)
        command = text.split()[0].split("@")[0].lower()

        if command == "/start":
            if chat_id not in subscribers:
                subscribers.add(chat_id)
                changed = True
                log.info("New subscriber: %s", chat_id)
            _call(token, "sendMessage", chat_id=chat_id, text=WELCOME_TEXT)
        elif command == "/stop":
            if chat_id in subscribers:
                subscribers.discard(chat_id)
                changed = True
                log.info("Unsubscribed: %s", chat_id)
            _call(token, "sendMessage", chat_id=chat_id, text=GOODBYE_TEXT)

    if changed:
        save_subscribers(subscribers_file, subscribers)
    save_offset(subscribers_file, next_offset)
