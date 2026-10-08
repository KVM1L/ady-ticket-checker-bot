import json
import logging
import os
import time

import psutil
from patchright.sync_api import BrowserContext, Page, sync_playwright

from .config import Config, Station

log = logging.getLogger(__name__)

HOME_URL = "https://ticket.ady.az/"


def open_context(playwright, config: Config) -> BrowserContext:
    # get_trip_dates is gated by Cloudflare Turnstile, which withholds its
    # token from anything that looks automated. What gets a token: patchright
    # (Playwright with the automation leaks patched out), real Chrome, a
    # visible window (headless never gets past Cloudflare), and no spoofed
    # user agent or viewport - one that disagrees with the real browser is
    # itself a bot signal.
    kwargs = dict(
        headless=config.headless,
        no_viewport=True,
        args=["--window-size=1280,900"],
    )
    if config.browser_channel:
        kwargs["channel"] = config.browser_channel
    return playwright.chromium.launch_persistent_context(config.browser_profile_dir, **kwargs)


def _is_main_chrome_for(proc, profile_dir: str) -> bool:
    """True for a browser (not renderer/GPU/etc. helper) process started
    with --user-data-dir pointing at profile_dir."""
    args = proc.cmdline()
    if any(arg.startswith("--type=") for arg in args):
        return False
    for arg in args:
        if arg.startswith("--user-data-dir="):
            path = arg.split("=", 1)[1].strip('"')
            return os.path.normcase(os.path.abspath(path)) == profile_dir
    return False


def _kill_leftover_chrome(profile_dir: str) -> None:
    """Chrome allows one browser per profile, so a leftover still holding ours
    (one that hung and outlived close(), or was orphaned by a crashed driver)
    makes every relaunch fail with "Opening in existing browser session".
    Kill it along with its helper processes. A Chrome with another Python
    process up its parent chain belongs to another running copy of the bot
    (say, a manual --once) and is left alone."""
    profile_dir = os.path.normcase(os.path.abspath(profile_dir))
    me = os.getpid()
    for proc in psutil.process_iter(["name"]):
        try:
            if "chrom" not in (proc.info["name"] or "").lower() or not _is_main_chrome_for(proc, profile_dir):
                continue
            owner = next((p for p in proc.parents() if p.pid == me or "python" in p.name().lower()), None)
            if owner is not None and owner.pid != me:
                log.error(
                    "Chrome pid %d holds the browser profile for another running bot (python pid %d), not killing it",
                    proc.pid, owner.pid,
                )
                continue
            log.warning("Killing leftover Chrome pid %d still holding the browser profile", proc.pid)
            tree = proc.children(recursive=True) + [proc]
            for p in tree:
                try:
                    p.kill()
                except psutil.NoSuchProcess:
                    pass
            psutil.wait_procs(tree, timeout=10)
        except psutil.Error:
            continue


def _is_response_for(response, from_id: int, to_id: int) -> bool:
    """Match not just the URL but the actual request payload - a stale
    get_trip_dates response for a *different* route (e.g. one still
    in flight from the previous fetch on this same page) would otherwise
    be indistinguishable from the one we're waiting for."""
    if "ticket-api/get_trip_dates" not in response.url:
        return False
    try:
        payload = json.loads(response.request.post_data or "")
    except (ValueError, TypeError):
        return False
    return payload.get("from_station") == from_id and payload.get("to_station") == to_id


def _load_route(page: Page, origin: Station, destination: Station):
    """(Re)load the home page and fill in the Haradan/Haraya pickers for
    origin -> destination, without touching the "Axtar" (search) button.
    Returns the get_trip_dates response the site's calendar widget fires."""
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
    return resp_info.value


def _swap_route(page: Page, origin: Station, destination: Station):
    """Flip the already-filled form from destination -> origin to
    origin -> destination with the site's own swap button (the arrows
    between Haradan and Haraya). The calendar widget then fetches
    get_trip_dates for the new direction, with a fresh Turnstile token."""
    log.debug("Swapping direction to %s -> %s", origin.name, destination.name)
    with page.expect_response(
        lambda r: _is_response_for(r, origin.id, destination.id), timeout=20000
    ) as resp_info:
        page.locator("button.search__changer").click(timeout=15000)
    return resp_info.value


def _parse_trip_dates(response, origin: Station, destination: Station) -> list[dict]:
    """Returns a list of {"trip_date": "DD-MM-YYYY", "min_amount": "88.49"} dicts,
    or [] if the route currently has no bookable dates."""
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

    # A rejected captcha comes back as HTTP 422 {"error": true, "message":
    # "ReCaptcha validation failed"} - the same "error" flag as a route that
    # genuinely has no dates. Treating it as "no dates" would announce every
    # known date as sold out and wipe the state, so it must be a fetch failure.
    message = str(payload.get("message") or "")
    if not response.ok or "captcha" in message.lower():
        raise RuntimeError(
            f"get_trip_dates rejected for {origin.name} -> {destination.name}: HTTP {response.status} {message!r}"
        )

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


class TicketSite:
    """A Chrome window kept open on ticket.ady.az across poll cycles.

    A full page load is the slow, Cloudflare-visible part of a check. Once
    both stations are picked, the site's swap button flips the direction and
    the calendar widget re-fetches get_trip_dates for it, so the page is only
    reloaded every config.page_reload_minutes (or after any failure) and every
    other fetch is a swap. Not thread-safe: use it from one thread only.
    """

    def __init__(self, config: Config):
        self.config = config
        self._playwright = None
        self._context = None
        self._page = None
        self._loaded_at = None  # time.monotonic() of the last full page load
        self._direction = None  # (origin, destination) the form currently shows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def fetch_trip_dates(self, origin: Station, destination: Station) -> list[dict]:
        if self._page is not None and not self._is_alive():
            self.close()  # relaunched and logged in below, within this same attempt
        try:
            if self._page_is_fresh() and self._direction == (destination, origin):
                response = _swap_route(self._page, origin, destination)
            else:
                if self._page is None:
                    self._open()
                log.info("Loading %s for %s -> %s", HOME_URL, origin.name, destination.name)
                response = _load_route(self._page, origin, destination)
                self._loaded_at = time.monotonic()
            self._direction = (origin, destination)
            return _parse_trip_dates(response, origin, destination)
        except Exception:
            # The form may be left half-flipped, or the browser may have
            # crashed - either way the next attempt starts from a fresh browser.
            self.close()
            raise

    def _is_alive(self) -> bool:
        """Chrome sits idle between polls and can die meanwhile (crash, OOM
        kill, window closed by hand) or hang. A dead browser, a crashed tab
        and a hung renderer all fail this probe, so the browser gets relaunched
        up front instead of costing a failed attempt first."""
        try:
            self._page.locator("button.search__changer").wait_for(state="attached", timeout=10000)
            return True
        except Exception as e:
            reason = (str(e).splitlines() or [type(e).__name__])[0]
            log.warning("Browser is dead or unresponsive, relaunching: %s", reason)
            return False

    def _page_is_fresh(self) -> bool:
        return (
            self._loaded_at is not None
            and time.monotonic() - self._loaded_at < self.config.page_reload_minutes * 60
        )

    def _open(self) -> None:
        log.info("Launching browser")
        _kill_leftover_chrome(self.config.browser_profile_dir)
        self._playwright = sync_playwright().start()
        self._context = open_context(self._playwright, self.config)
        self._page = self._context.new_page()

    def close(self) -> None:
        # No context.close(): if the Playwright driver itself has died it
        # waits forever for a "closed" event nobody will send. Stopping the
        # driver closes Chrome just as cleanly when the driver is alive, and
        # returns at once when it isn't.
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                log.debug("Error stopping playwright", exc_info=True)
        self._playwright = self._context = self._page = None
        self._loaded_at = self._direction = None


def run_check(site: TicketSite, routes) -> dict:
    """Runs site.fetch_trip_dates for every (origin, destination) pair in routes.
    Returns {(origin.name, destination.name): [ {trip_date, min_amount}, ... ]}.
    """
    log.info("Starting check for %d route(s)", len(routes))
    started = time.monotonic()
    results = {}
    for origin, destination in routes:
        dates = None
        attempts = 2
        for attempt in range(attempts):
            try:
                dates = site.fetch_trip_dates(origin, destination)
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
    log.info("Check finished in %.1fs", time.monotonic() - started)
    return results
