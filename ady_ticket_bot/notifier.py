import logging

import requests

from .filters import Filter
from .messaging import build_message
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


def notify_subscribers(token: str, subscribers_file: str, snapshots: list) -> int:
    """Builds a personalized message per subscriber (applying their price/date
    filter) from this cycle's snapshots, and sends it if there's anything
    relevant. Returns how many subscribers were actually sent a message."""
    subscribers = load_subscribers(subscribers_file)
    if not subscribers:
        log.info("No subscribers yet - nothing to send.")
        return 0

    stale = set()
    sent = 0
    for chat_id, entry in subscribers.items():
        message = build_message(snapshots, Filter.from_dict(entry))
        if not message:
            continue
        if send_telegram_message(token, chat_id, message):
            sent += 1
        else:
            stale.add(chat_id)

    if stale:
        remaining = {cid: entry for cid, entry in subscribers.items() if cid not in stale}
        save_subscribers(subscribers_file, remaining)
        log.info("Removed %d stale subscriber(s)", len(stale))

    return sent
