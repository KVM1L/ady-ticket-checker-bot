import datetime
import logging
from dataclasses import dataclass, field

from .browser import run_check
from .config import Config, ROUTES
from .state import load_seen, save_seen

log = logging.getLogger(__name__)


@dataclass
class RouteSnapshot:
    origin_display: str
    destination_display: str
    current: dict = field(default_factory=dict)  # trip_date "DD-MM-YYYY" -> price string, within lookahead window
    markers: dict = field(default_factory=dict)  # trip_date -> "🆕" or "💰 (было X)"
    sold_out: list = field(default_factory=list)  # [(trip_date, last_price), ...]
    fetch_failed: bool = False

    @property
    def label(self) -> str:
        """Stable, human-readable identifier for this route - used both for
        display and as the value stored in a subscriber's direction filter."""
        return f"{self.origin_display} → {self.destination_display}"


def route_key(origin, destination) -> str:
    return f"{origin.name}->{destination.name}"


def _parse_date(trip_date: str) -> datetime.date:
    return datetime.datetime.strptime(trip_date, "%d-%m-%Y").date()


def check_for_new_tickets(config: Config) -> list[RouteSnapshot]:
    """Runs one full poll cycle and returns a snapshot per route: the current
    bookable dates within the lookahead window, which of them are new/changed
    since the last cycle, and which previously-seen dates disappeared (sold
    out). Callers apply their own per-subscriber filtering on top of this.
    """
    results = run_check(config, ROUTES)
    seen = load_seen(config.state_file)
    today = datetime.date.today()
    horizon = today + datetime.timedelta(days=config.lookahead_days)

    snapshots = []
    for origin, destination in ROUTES:
        key = route_key(origin, destination)
        dates = results.get((origin.name, destination.name))

        if dates is None:
            snapshots.append(RouteSnapshot(origin.display, destination.display, fetch_failed=True))
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
            elif previous[trip_date] != price:
                markers[trip_date] = f"💰 (было {previous[trip_date]})"

        sold_out = [
            (trip_date, price) for trip_date, price in previous.items() if trip_date not in current
        ]

        snapshots.append(RouteSnapshot(origin.display, destination.display, current, markers, sold_out))
        seen[key] = current

    save_seen(config.state_file, seen)
    return snapshots
