import json
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


def _is_response_for(response, from_id: int, to_id: int) -> bool:
    """Match not just the URL but the actual request payload - a stale
    get_trip_dates response for a *different* route (e.g. one still
    in flight from the previous fetch_trip_dates call on this same page)
    would otherwise be indistinguishable from the one we're waiting for."""
    if "ticket-api/get_trip_dates" not in response.url:
        return False
    try:
        payload = json.loads(response.request.post_data or "")
    except (ValueError, TypeError):
        return False
    return payload.get("from_station") == from_id and payload.get("to_station") == to_id


def fetch_trip_dates(page: Page, origin: Station, destination: Station) -> list[dict]:
    """Fill in the Haradan/Haraya pickers for origin -> destination and read the
    prices/dates the site's own calendar widget fetches, without touching the
    "Axtar" (search) button.

    Returns a list of {"trip_date": "DD-MM-YYYY", "min_amount": "88.49"} dicts,
    or [] if the route currently has no bookable dates.
    """
    log.debug("Navigating to %s for %s -> %s", HOME_URL, origin.name, destination.name)
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
    log.debug("Form ready, selecting origin %s", origin.name)

    origin_group.locator("input").click(timeout=15000)
    origin_group.locator(f"button:has-text('{origin.name}')").first.click(timeout=15000)

    log.debug("Selecting destination %s", destination.name)
    destination_group.locator("input").click(timeout=15000)
    with page.expect_response(
        lambda r: _is_response_for(r, origin.id, destination.id), timeout=20000
    ) as resp_info:
        destination_group.locator(f"button:has-text('{destination.name}')").first.click(timeout=15000)

    response = resp_info.value
    payload = response.json()

    try:
        confirmed = json.loads(response.request.post_data or "{}")
    except (ValueError, TypeError):
        confirmed = {}
    log.info(
        "get_trip_dates confirmed request: intended %s(%s) -> %s(%s), actually sent from_station=%s to_station=%s",
        origin.name, origin.id, destination.name, destination.id,
        confirmed.get("from_station"), confirmed.get("to_station"),
    )
    # Full raw body, so a mismatch can be diagnosed from facts instead of
    # guesswork if this route ever again shows dates that don't match reality.
    log.info("get_trip_dates raw response body for %s -> %s: %s", origin.name, destination.name, payload)

    if payload.get("error"):
        log.debug("get_trip_dates: no data for %s -> %s", origin.name, destination.name)
        return []

    # The response bundles BOTH directions for calendar-widget convenience:
    # key "1" is the way we actually asked for (from_station -> to_station,
    # matching the "way": 1 we send), key "2" (when present) is the reverse
    # direction's data. Only "1" belongs to this call - including "2" here
    # would silently mix the other direction's dates/prices into this route.
    data = payload.get("data") or {}
    dates = data.get("1", [])
    log.debug("get_trip_dates: %d date(s) for %s -> %s", len(dates), origin.name, destination.name)
    return dates


def run_check(config: Config, routes) -> dict:
    """Runs fetch_trip_dates for every (origin, destination) pair in routes.
    Returns {(origin.name, destination.name): [ {trip_date, min_amount}, ... ]}.
    """
    log.info("Starting check for %d route(s)", len(routes))
    started = time.monotonic()
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
                        log.info(
                            "%s -> %s: %d bookable date(s)", origin.name, destination.name, len(dates),
                        )
                        break
                    except Exception:
                        log.warning(
                            "Attempt %d failed fetching %s -> %s", attempt + 1, origin.name, destination.name,
                            exc_info=True,
                        )
                        if attempt < attempts - 1:
                            time.sleep(10)
                if dates is None:
                    log.error("Giving up on %s -> %s this cycle after %d attempt(s)", origin.name, destination.name, attempts)
                results[(origin.name, destination.name)] = dates
        finally:
            context.close()
    log.info("Check finished in %.1fs", time.monotonic() - started)
    return results
