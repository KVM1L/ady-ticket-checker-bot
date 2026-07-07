import logging
import time

from playwright.sync_api import BrowserContext, Page, sync_playwright

from .config import Config, Station

log = logging.getLogger(__name__)

HOME_URL = "https://ticket.ady.az/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def open_context(playwright, config: Config) -> BrowserContext:
    kwargs = dict(
        headless=config.headless,
        user_agent=USER_AGENT,
        viewport={"width": 1280, "height": 800},
    )
    if config.browser_channel:
        kwargs["channel"] = config.browser_channel
    return playwright.chromium.launch_persistent_context(config.browser_profile_dir, **kwargs)


def fetch_trip_dates(page: Page, origin: Station, destination: Station) -> list[dict]:
    """Fill in the Haradan/Haraya pickers for origin -> destination and read the
    prices/dates the site's own calendar widget fetches, without touching the
    "Axtar" (search) button.

    Returns a list of {"trip_date": "DD-MM-YYYY", "min_amount": "88.49"} dicts,
    or [] if the route currently has no bookable dates.
    """
    page.goto(HOME_URL, wait_until="load", timeout=60000)

    # The fixed header and the full-page loading overlay both sit on top of
    # the form and intercept Playwright's clicks after it auto-scrolls the
    # target into view, even once the form itself is visible and usable.
    # Neither is something we ever need to click, so just make them
    # click-through for the rest of this page's lifetime.
    page.add_style_tag(content=".header, .full-page-loader { pointer-events: none !important; }")

    # NB: the site's CSS class names are swapped relative to the field labels -
    # ".form-group--to" is the "Haradan" (origin) field and ".form-group--from"
    # is the "Haraya" (destination) field. Confirmed by inspecting the live DOM.
    origin_group = page.locator(".form-group--to")
    destination_group = page.locator(".form-group--from")

    # Cloudflare occasionally shows a "you are in line" waiting-room
    # interstitial instead of the real page - it self-refreshes every few
    # seconds until let through. Wait for the real form to actually show up
    # instead of a fixed sleep, generously covering the site's own quoted
    # wait estimate (it's usually a couple of minutes).
    origin_group.locator("input").wait_for(state="visible", timeout=180000)

    origin_group.locator("input").click(timeout=15000)
    origin_group.locator(f"button:has-text('{origin.name}')").first.click(timeout=15000)

    destination_group.locator("input").click(timeout=15000)
    with page.expect_response(
        lambda r: "ticket-api/get_trip_dates" in r.url, timeout=20000
    ) as resp_info:
        destination_group.locator(f"button:has-text('{destination.name}')").first.click(timeout=15000)

    response = resp_info.value
    payload = response.json()

    if payload.get("error"):
        return []

    data = payload.get("data") or {}
    dates = []
    for entries in data.values():
        dates.extend(entries)
    return dates


def run_check(config: Config, routes) -> dict:
    """Runs fetch_trip_dates for every (origin, destination) pair in routes.
    Returns {(origin.name, destination.name): [ {trip_date, min_amount}, ... ]}.
    """
    results = {}
    with sync_playwright() as playwright:
        context = open_context(playwright, config)
        try:
            page = context.new_page()
            for origin, destination in routes:
                dates = None
                attempts = 2
                for attempt in range(attempts):
                    try:
                        dates = fetch_trip_dates(page, origin, destination)
                        break
                    except Exception:
                        log.warning(
                            "Attempt %d failed fetching %s -> %s", attempt + 1, origin.name, destination.name,
                            exc_info=True,
                        )
                        if attempt < attempts - 1:
                            time.sleep(10)
                results[(origin.name, destination.name)] = dates
        finally:
            context.close()
    return results
