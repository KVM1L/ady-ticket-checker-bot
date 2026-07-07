import datetime
import logging

from .browser import run_check
from .config import Config, ROUTES
from .state import load_seen, save_seen

log = logging.getLogger(__name__)


def route_key(origin, destination) -> str:
    return f"{origin.name}->{destination.name}"


def _parse_date(trip_date: str) -> datetime.date:
    return datetime.datetime.strptime(trip_date, "%d-%m-%Y").date()


def _format_table(current: dict, markers: dict) -> str:
    if not current:
        return "нет билетов"
    rows = []
    for trip_date in sorted(current, key=_parse_date):
        marker = markers.get(trip_date, "")
        rows.append(f"{trip_date}   {current[trip_date]:>7} AZN {marker}".rstrip())
    return "\n".join(rows)


def check_for_new_tickets(config: Config) -> str | None:
    """Runs one full poll cycle. Returns a single Telegram-ready message with
    a combined table for both directions if anything changed within the
    lookahead window (new date, price change, or sold out) - otherwise None,
    so a poll with no changes stays silent.
    """
    results = run_check(config, ROUTES)
    seen = load_seen(config.state_file)
    today = datetime.date.today()
    horizon = today + datetime.timedelta(days=config.lookahead_days)

    sections = []
    any_change = False

    for origin, destination in ROUTES:
        key = route_key(origin, destination)
        route_label = f"{origin.display} → {destination.display}"
        dates = results.get((origin.name, destination.name))

        if dates is None:
            sections.append(f"<b>{route_label}</b>\n<pre>не удалось получить данные</pre>")
            continue  # fetch failed this cycle; leave state untouched, retry next time

        previous = seen.get(key, {})
        if not isinstance(previous, dict):
            previous = {}  # legacy list-based state from before price/sold-out tracking

        current = {}
        for entry in dates:
            trip_date = entry["trip_date"]
            try:
                parsed = _parse_date(trip_date)
            except ValueError:
                continue
            if parsed < today or parsed > horizon:
                continue  # outside the window we care about - don't track it either way
            current[trip_date] = entry.get("min_amount")

        markers = {}
        for trip_date, price in current.items():
            if trip_date not in previous:
                markers[trip_date] = "🆕"
                any_change = True
            elif previous[trip_date] != price:
                markers[trip_date] = f"💰 (было {previous[trip_date]})"
                any_change = True

        sold_out = []
        for trip_date, price in previous.items():
            if trip_date not in current:
                sold_out.append(f"❌ {trip_date} ({price} AZN)")
                any_change = True

        section = f"<b>{route_label}</b>\n<pre>{_format_table(current, markers)}</pre>"
        if sold_out:
            section += "\n" + "\n".join(sold_out)
        sections.append(section)
        seen[key] = current

    save_seen(config.state_file, seen)

    if not any_change:
        return None

    blocks = ["🚆 <b>Bakı ⇄ Tbilisi — билеты на ближайшие 2 месяца</b>"]
    blocks.extend(sections)
    return "\n\n".join(blocks)
