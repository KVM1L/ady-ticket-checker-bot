import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@dataclass
class Station:
    name: str  # visible label as it appears in the site's dropdown, e.g. "BAKI DYV"
    id: int
    display: str = ""  # short human-friendly name used in Telegram messages

    def __post_init__(self):
        if not self.display:
            self.display = self.name


# The two ends of the route to watch. IDs come from ticket.ady.az's own
# /ticket-api/stations_in_route response and are stable identifiers.
BAKU = Station(name="BAKI DYV", id=232, display="Bakı")
TBILISI = Station(name="TBİLİSİ-SƏRN", id=170, display="Tbilisi")

ROUTES = [
    (BAKU, TBILISI),
    (TBILISI, BAKU),
]


@dataclass
class Config:
    telegram_bot_token: str = field(default_factory=lambda: os.environ["TELEGRAM_BOT_TOKEN"])
    telegram_chat_id: str = field(default_factory=lambda: os.environ["TELEGRAM_CHAT_ID"])
    poll_interval_minutes: int = field(default_factory=lambda: int(os.environ.get("POLL_INTERVAL_MINUTES", "30")))
    poll_jitter_minutes: int = field(default_factory=lambda: int(os.environ.get("POLL_JITTER_MINUTES", "5")))
    lookahead_days: int = field(default_factory=lambda: int(os.environ.get("LOOKAHEAD_DAYS", "60")))
    headless: bool = field(default_factory=lambda: os.environ.get("HEADLESS", "true").lower() != "false")
    state_file: str = field(default_factory=lambda: os.environ.get("STATE_FILE", os.path.join(BASE_DIR, "data", "state.json")))
    browser_profile_dir: str = field(default_factory=lambda: os.environ.get("BROWSER_PROFILE_DIR", os.path.join(BASE_DIR, "data", "browser_profile")))
    log_file: str = field(default_factory=lambda: os.environ.get("LOG_FILE", os.path.join(BASE_DIR, "data", "ady_ticket_bot.log")))
