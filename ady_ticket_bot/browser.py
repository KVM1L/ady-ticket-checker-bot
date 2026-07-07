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
    try:
        # Servers on datacenter IPs can sit behind Cloudflare longer than a
        # residential IP does - wait for the page to actually go quiet instead
        # of a fixed sleep, but don't fail the whole attempt if it never does.
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(4000)

    # NB: the site's CSS class names are swapped relative to the field labels -
    # ".form-group--to" is the "Haradan" (origin) field and ".form-group--from"
    # is the "Haraya" (destination) field. Confirmed by inspecting the live DOM.
    origin_group = page.locator(".form-group--to")
    destination_group = page.locator(".form-group--from")

    origin_group.locator("input").click(timeout=45000)
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
                attempts = 3
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
