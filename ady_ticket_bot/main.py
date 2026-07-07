import argparse
import logging
import os
import random
import time

from .checker import check_for_new_tickets
from .config import Config
from .notifier import send_telegram_message


def setup_logging(log_file: str) -> None:
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler()],
    )


def run_once(config: Config) -> None:
    log = logging.getLogger(__name__)
    messages = check_for_new_tickets(config)
    if not messages:
        log.info("No new tickets found.")
        return
    for message in messages:
        send_telegram_message(config.telegram_bot_token, config.telegram_chat_id, message)
    log.info("Sent %d notification(s).", len(messages))


def main() -> None:
    parser = argparse.ArgumentParser(description="Ady.az Baku<->Tbilisi ticket watcher")
    parser.add_argument("--once", action="store_true", help="Run a single check and exit, instead of looping forever")
    args = parser.parse_args()

    config = Config()
    setup_logging(config.log_file)
    log = logging.getLogger(__name__)

    if args.once:
        run_once(config)
        return

    log.info(
        "Starting poll loop: every %d (+/- %d) minutes, lookahead %d days",
        config.poll_interval_minutes,
        config.poll_jitter_minutes,
        config.lookahead_days,
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
