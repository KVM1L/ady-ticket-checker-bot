import datetime
from dataclasses import dataclass


@dataclass
class Filter:
    max_price: float | None = None
    date_from: datetime.date | None = None
    date_to: datetime.date | None = None
    directions: set | None = None  # None = all directions; else set of RouteSnapshot.label values

    def matches(self, trip_date: datetime.date, price: float) -> bool:
        if self.max_price is not None and price > self.max_price:
            return False
        if self.date_from is not None and trip_date < self.date_from:
            return False
        if self.date_to is not None and trip_date > self.date_to:
            return False
        return True

    def allows_direction(self, label: str) -> bool:
        return self.directions is None or label in self.directions

    def is_empty(self) -> bool:
        return (
            self.max_price is None
            and self.date_from is None
            and self.date_to is None
            and self.directions is None
        )

    def describe(self) -> str:
        if self.is_empty():
            return "без фильтров (показываю все билеты)"
        parts = []
        if self.directions is not None:
            parts.append("направление: " + ", ".join(sorted(self.directions)))
        if self.max_price is not None:
            parts.append(f"цена ≤ {self.max_price:g} AZN")
        if self.date_from and self.date_from == self.date_to:
            parts.append(f"дата {self.date_from.strftime('%d-%m-%Y')}")
        elif self.date_from or self.date_to:
            frm = self.date_from.strftime("%d-%m-%Y") if self.date_from else "…"
            to = self.date_to.strftime("%d-%m-%Y") if self.date_to else "…"
            parts.append(f"даты {frm} – {to}")
        return "\n".join(parts)

    def to_dict(self) -> dict:
        d = {}
        if self.directions is not None:
            d["directions"] = sorted(self.directions)
        if self.max_price is not None:
            d["max_price"] = self.max_price
        if self.date_from is not None:
            d["date_from"] = self.date_from.strftime("%d-%m-%Y")
        if self.date_to is not None:
            d["date_to"] = self.date_to.strftime("%d-%m-%Y")
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Filter":
        def parse_date(s):
            return datetime.datetime.strptime(s, "%d-%m-%Y").date() if s else None

        directions = d.get("directions")
        return cls(
            max_price=d.get("max_price"),
            date_from=parse_date(d.get("date_from")),
            date_to=parse_date(d.get("date_to")),
            directions=set(directions) if directions is not None else None,
        )
