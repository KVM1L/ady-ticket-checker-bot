import datetime

from .filters import Filter


def _parse_date(trip_date: str) -> datetime.date:
    return datetime.datetime.strptime(trip_date, "%d-%m-%Y").date()


def _row(trip_date: str, price: str, marker: str = "") -> str:
    return f"{trip_date}   {price:>7} AZN {marker}".rstrip()


def build_message(snapshots: list, filt: Filter) -> str | None:
    """Renders a personalized message from this cycle's route snapshots,
    keeping only dates that match `filt`. Returns None if nothing relevant
    changed for this filter this cycle (so the caller sends nothing).
    """
    sections = []
    has_change = False

    for snap in snapshots:
        if not filt.allows_direction(snap.label):
            continue  # subscriber isn't interested in this direction at all

        if snap.fetch_failed:
            sections.append(f"<b>{snap.origin_display} → {snap.destination_display}</b>\n<pre>не удалось получить данные</pre>")
            continue

        rows = []
        for trip_date in sorted(snap.current, key=_parse_date):
            price = snap.current[trip_date]
            if not filt.matches(_parse_date(trip_date), float(price)):
                continue
            marker = snap.markers.get(trip_date, "")
            if marker:
                has_change = True
            rows.append(_row(trip_date, price, marker))

        sold_out_lines = []
        for trip_date, price in snap.sold_out:
            if not filt.matches(_parse_date(trip_date), float(price)):
                continue
            sold_out_lines.append(f"❌ {trip_date} ({price} AZN)")
            has_change = True

        table = "\n".join(rows) if rows else "нет билетов"
        section = f"<b>{snap.origin_display} → {snap.destination_display}</b>\n<pre>{table}</pre>"
        if sold_out_lines:
            section += "\n" + "\n".join(sold_out_lines)
        sections.append(section)

    if not has_change:
        return None

    blocks = ["🚆 <b>Bakı ⇄ Tbilisi — билеты на ближайшие 2 месяца</b>"]
    blocks.extend(sections)
    blocks.append(f"<i>Ваш фильтр: {filt.describe()}</i>\n/filter — изменить")
    return "\n\n".join(blocks)
