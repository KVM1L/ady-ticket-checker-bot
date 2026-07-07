import logging

import requests

from .subscribers import load_subscribers, save_subscribers

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def send_telegram_message(token: str, chat_id: str, text: str) -> bool:
    """Returns False if the chat is gone for good (bot blocked/kicked) and the
    caller should stop sending to it, True otherwise (sent, or a transient error)."""
    url = TELEGRAM_API.format(token=token)
    resp = requests.post(
        url,
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
        timeout=15,
    )
    if resp.ok:
        return True
    log.error("Telegram send to %s failed: %s %s", chat_id, resp.status_code, resp.text)
    return resp.status_code != 403


def broadcast_message(token: str, subscribers_file: str, text: str) -> int:
    """Sends text to every subscribed chat. Returns how many it was sent to."""
    subscribers = load_subscribers(subscribers_file)
    if not subscribers:
        log.info("No subscribers yet - nothing to send.")
        return 0

    stale = set()
    sent = 0
    for chat_id in subscribers:
        if send_telegram_message(token, chat_id, text):
            sent += 1
        else:
            stale.add(chat_id)

    if stale:
        save_subscribers(subscribers_file, subscribers - stale)
        log.info("Removed %d stale subscriber(s)", len(stale))

    return sent
