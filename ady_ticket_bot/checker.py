import datetime
import logging

from .browser import run_check
from .config import Config, ROUTES
from .state import load_seen, save_seen

log = logging.getLogger(__name__)


def route_key(origin, destination) -> str:
    return f"{origin.name}->{destination.name}"


def check_for_new_tickets(config: Config) -> list[str]:
    """Runs one full poll cycle and returns Telegram-ready message strings for
    any change - within the lookahead window - since the last cycle: a date
    newly becoming bookable, its price changing, or it disappearing (sold out).
    """
    results = run_check(config, ROUTES)
    seen = load_seen(config.state_file)
    today = datetime.date.today()
    horizon = today + datetime.timedelta(days=config.lookahead_days)

    messages = []
    for origin, destination in ROUTES:
        key = route_key(origin, destination)
        dates = results.get((origin.name, destination.name))
        if dates is None:
            continue  # fetch failed this cycle; leave state untouched, retry next time

        previous = seen.get(key, {})
        if not isinstance(previous, dict):
            previous = {}  # legacy list-based state from before price/sold-out tracking

        current = {}
        for entry in dates:
            trip_date = entry["trip_date"]
            try:
                parsed = datetime.datetime.strptime(trip_date, "%d-%m-%Y").date()
            except ValueError:
                continue
            if parsed < today or parsed > horizon:
                continue  # outside the window we care about - don't track it either way
            current[trip_date] = entry.get("min_amount")

        route_label = f"<b>{origin.display} → {destination.display}</b>"

        for trip_date, price in current.items():
            if trip_date not in previous:
                messages.append(f"🚆 {route_label}\nНайден билет на {trip_date}\nЦена: {price} AZN")
            elif previous[trip_date] != price:
                messages.append(
                    f"💰 {route_label}\nИзменилась цена на {trip_date}\n"
                    f"{previous[trip_date]} → {price} AZN"
                )

        for trip_date in previous:
            if trip_date not in current:
                messages.append(f"❌ {route_label}\nБилеты на {trip_date} распроданы (пропали из продажи)")

        seen[key] = current

    save_seen(config.state_file, seen)
    return messages
