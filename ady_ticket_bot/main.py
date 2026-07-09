import argparse
import logging
import os
import random
import threading
import time

from . import admin
from .checker import check_for_new_tickets
from .config import Config
from .notifier import notify_subscribers
from .subscribers import load_subscribers, save_subscribers
from .telegram_updates import poll_updates_once


def setup_logging(log_file: str) -> None:
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler()],
    )


def seed_owner_subscription(config: Config) -> None:
    """One-time migration: if nobody has /start'd the bot yet but
    TELEGRAM_CHAT_ID is set (the pre-subscriptions way of configuring this),
    subscribe that chat automatically so notifications don't just stop."""
    if not config.telegram_chat_id:
        return
    if os.path.exists(config.subscribers_file):
        return
    save_subscribers(config.subscribers_file, {config.telegram_chat_id: {}})


def run_subscriber_listener(config: Config) -> None:
    log = logging.getLogger(__name__)
    while True:
        try:
            poll_updates_once(config)
        except Exception:
            log.exception("Subscriber listener error, retrying shortly")
            time.sleep(5)


def run_once(config: Config) -> None:
    log = logging.getLogger(__name__)
    snapshots = check_for_new_tickets(config)
    sent = notify_subscribers(config.telegram_bot_token, config.subscribers_file, snapshots)
    log.info("Notified %d subscriber(s).", sent)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ady.az Baku<->Tbilisi ticket watcher")
    parser.add_argument("--once", action="store_true", help="Run a single check and exit, instead of looping forever")
    args = parser.parse_args()

    config = Config()
    setup_logging(config.log_file)
    log = logging.getLogger(__name__)
    seed_owner_subscription(config)
    admin.sync_command_menu(config)

    if args.once:
        run_once(config)
        return

    listener = threading.Thread(target=run_subscriber_listener, args=(config,), daemon=True)
    listener.start()

    log.info(
        "Starting poll loop: every %d (+/- %d) minutes, lookahead %d days, %d subscriber(s)",
        config.poll_interval_minutes,
        config.poll_jitter_minutes,
        config.lookahead_days,
        len(load_subscribers(config.subscribers_file)),
    )
    while True:
        try:
            run_once(config)
        except Exception:
            log.exception("Poll cycle failed")

        jitter = random.uniform(-config.poll_jitter_minutes, config.poll_jitter_minutes)
        sleep_minutes = max(1.0, config.poll_interval_minutes + jitter)
        time.sleep(sleep_minutes * 60)


if __name__ == "__main__":
    main()
