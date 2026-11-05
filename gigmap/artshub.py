"""Read the ArtsHub event feed.

This module holds every rule about artshub.com.au: the feed address, the
location filter, the XML fields, and the reads. It is the only module that
reads artshub.com.au. The server holds the cache and the HTTP routes.

The site

The site is WordPress behind Cloudflare. The event search page at
`/event/search/` answers a Cloudflare challenge to a client that is not a
browser, so the page that the reader sees is not open to this module. Every
other path answers a normal request. The site has no page that lists every
event, so the module reads the feed.

The feed

`/events/feed/` answers the event list as RSS. The item of one event holds
the fields that this app needs: the start and the end of the run, the venue,
the street address, the location tags, the art forms, and the
classifications. The feed answers 200 items on a page, so the module reads
the pages until a short page.

The date filter

The feed has no filter on the date of the run. A query key that the site does
not know changes nothing and answers no error, so the module reads the list
of the location and keeps the events whose run covers one day of the
requested range. The list of one city holds about 70 events, so a range
costs one read.

The venue

The site has no page for one venue. The event carries the name and the
street address as text, and the map needs coordinates. The module builds the
venue address from those two texts, and `venue` answers from the address
without a read. The server looks the address up on Nominatim for every
source, and the cache keeps the result.

The ticket

The event page holds no price field and no booking link, so a gig of this
source holds no ticket. A few descriptions hold a price in the text, and the
module does not read the text of a description.

The schedule

The event page holds a long text in `div.the-content` and an `Event Starts`
date. `gigs` reads the page of each event, 5 at a time, and passes the text to
`extract_schedule`. `fit_dates` then checks the chosen dates against the days of
the event. A gig that is closed on all chosen dates leaves the list. For an open
gig, `time` is the opening time of the first open date, for example `1:00pm`.
A gig with no readable page or no schedule stays, with no time.

The tests

`uv run pytest tests/test_artshub.py` runs the tests. The tests use a saved
feed page, so they do not touch the live site.
"""

import asyncio
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime
from urllib.parse import quote, unquote, urlsplit

from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests

from gigmap import sources
from gigmap.logger import log_request
from gigmap.schedule import extract_schedule, fit_dates

SITE_URL = "https://www.artshub.com.au"
SITE_HOST = urlsplit(SITE_URL).netloc
FEED_URL = f"{SITE_URL}/events/feed/"
VENUE_PREFIX = f"{SITE_URL}/venue/"

# The app serves this one site, so the source carries the app details.
SOURCE = sources.SourceInfo(
    title="Melb Art Map",
    heading="Melb Art Map",
    name="artshub.com.au",
    home_url=f"{SITE_URL}/event/search/?location=melbourne",
    noun="exhibits",
)

# The feed keeps one tag for each city. The read asks for the tag and keeps
# the tag again, because the feed ignores a query key that it does not know
# without an error. The tag also holds a trace of the other cities.
LOCATION = "melbourne"

VENUE_URL_HINT = (
    "venue_url must have the form "
    "https://www.artshub.com.au/venue/<name>/<address>/"
)
DETAIL_URL_RE = re.compile(
    r"https://www\.artshub\.com\.au/event/(?P<slug>[^/?#]+)/?"
)
VENUE_PATH_PREFIX = "/venue/"

# The feed writes a few street addresses that Nominatim cannot read, for
# example `Corner Walker and, Robinson St`. The table gives a street that
# Nominatim finds. The pin of such a venue is at the street, not the door.
VENUE_ADDRESSES = {
    "Walker Street Gallery and Arts Centre": "13 Walker St, Dandenong VIC 3175",
    "Potter Museum of Art": "Masson Rd, Parkville VIC 3052",
}

# The feed answers 200 items on a page. A loop that reads more pages than
# this cannot be right, so it stops.
PAGE_SIZE = 200
MAX_PAGES = 20
# The event pages that one gig list reads at the same time.
PAGE_CONCURRENCY = 5

# The site is behind Cloudflare. The feed and the event page answer a normal
# request, so one browser fingerprint is enough. A challenge would answer a
# 403, and the read reports it as a source error.
IMPERSONATE_TARGET = "chrome"
HEADERS = {
    "Accept-Language": "en-AU,en;q=0.9",
}

# Cloudflare or the origin can answer a 5xx. The fault is short in most
# cases, so the module retries the same read.
SERVER_ERROR_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 1.0
REQUEST_TIMEOUT_SECONDS = 30.0

STAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
EVENT_STARTS_RE = re.compile(r"Event Starts\s*:?\s*([A-Z][a-z]{2} \d{1,2}, \d{4})")

LOGGER = logging.getLogger(__name__)


def text(value: object) -> str | None:
    """Return one stripped string, or None for a missing value."""
    return value.strip() if isinstance(value, str) and value.strip() else None


def stamp_day(value: object) -> str | None:
    """Return the day of one feed stamp, or None for a damaged stamp.

    The stamp has the form `2026-11-14 19:30:00`. The module drops the
    time of day. The site gives an event a start and an end time, but the
    two are not the time of the show. A time on the map would be a guess.
    """
    match = STAMP_RE.search(value) if isinstance(value, str) else None
    return match.group() if match else None


def split_names(value: object) -> list[str]:
    """Return the names of one comma separated feed field."""
    if not isinstance(value, str):
        return []
    return [name.strip() for name in value.split(",") if name.strip()]


def parse_event_page(html: str) -> tuple[str, date | None]:
    """Return the body text and the `Event Starts` date of one event page.

    A missing part gives `""` or None.
    """
    soup = BeautifulSoup(html, "html.parser")
    body = soup.select_one("div.the-content")
    match = EVENT_STARTS_RE.search(soup.get_text(" ", strip=True))
    start = None
    if match:
        try:
            start = datetime.strptime(match.group(1), "%b %d, %Y").date()
        except ValueError:
            pass
    return (body.get_text(" ", strip=True) if body else ""), start


def venue_url(name: str | None, address: str | None) -> str | None:
    """Return the venue address of one event, or None for no venue.

    The site has no page for a venue, so the address holds the name and the
    street address. The cache names the file of a venue after the last part
    of the url, so the street address is the last part. Two venues at one
    address share a file, and the map shows them at one place all the same.
    """
    if not name and not address:
        return None
    safe_name = quote(name or "", safe="")
    safe_address = quote(address or "", safe="")
    return f"{VENUE_PREFIX}{safe_name}/{safe_address}/"


def decode_venue(url: str) -> tuple[str | None, str | None] | None:
    """Return the name and the address of one venue address.

    None when the address is not a venue address of this site.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or parts.netloc != SITE_HOST:
        return None
    if not parts.path.startswith(VENUE_PATH_PREFIX):
        return None
    segments = parts.path[len(VENUE_PATH_PREFIX) :].split("/")
    name = text(unquote(segments[0])) if segments else None
    address = text(unquote(segments[1])) if len(segments) > 1 else None
    return name, address


def covers(
    start: str,
    end: str | None,
    start_date: str,
    end_date: str,
) -> bool:
    """Return true when the run of one event covers one day of the range."""
    return start <= end_date and (end or start) >= start_date


def parse_item(item: ET.Element) -> sources.Gig | None:
    """Return one gig of a feed item, or None for an item that is not a gig.

    An item with no start date cannot fall in a date range, so it is not a
    gig of this app. The site holds a few of them, and they are usually past
    events that the site keeps.
    """
    artist = text(item.findtext("title"))
    start = stamp_day(item.findtext("startDateTime"))
    if artist is None or start is None:
        return None
    end = stamp_day(item.findtext("endDateTime"))
    name = text(item.findtext("venue"))
    address = VENUE_ADDRESSES.get(name or "") or text(item.findtext("venueAddress"))
    return sources.Gig(
        artist=artist,
        detail_url=text(item.findtext("link")),
        date=start,
        end_date=end if end and end != start else None,
        venue=name or address,
        venue_url=venue_url(name, address),
        genres=split_names(item.findtext("art_forms")),
        categories=split_names(item.findtext("classifications")),
    )


def in_location(item: ET.Element) -> bool:
    """Return true when one feed item carries the city tag."""
    names = split_names(item.findtext("locations"))
    return LOCATION in [name.lower() for name in names]


def parse_feed(
    page: str,
    start_date: str,
    end_date: str,
) -> list[sources.Gig]:
    """Return the gigs of one feed page that cover one day of the range."""
    try:
        root = ET.fromstring(page)
    except ET.ParseError as error:
        raise sources.SourceError("artshub.com.au answered damaged XML") from error
    gigs = []
    for item in root.findall("./channel/item"):
        if not in_location(item):
            continue
        gig = parse_item(item)
        if gig is None:
            continue
        if not covers(gig.date or "", gig.end_date, start_date, end_date):
            continue
        gigs.append(gig)
    return gigs


class ArtshubSource:
    """Read the data from artshub.com.au through one connection pool.

    This class is the `sources.GigSource` implementation for the live site.
    """

    def __init__(self) -> None:
        self._session: curl_requests.AsyncSession | None = None

    async def __aenter__(self) -> "ArtshubSource":
        self._session = curl_requests.AsyncSession(
            impersonate=IMPERSONATE_TARGET,
            allow_redirects=True,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        return self

    async def __aexit__(self, *_args: object) -> None:
        session = self._session
        self._session = None
        if session is not None:
            await session.close()

    async def _text(
        self,
        url: str,
        params: dict[str, str] | None = None,
        *,
        missing_ok: bool = False,
    ) -> str | None:
        """Return the page text, with a retry on a 5xx answer.

        A missing page answers None when `missing_ok` is true, because the
        feed marks the end of its pages with a 404.
        """
        session = self._session
        if session is None:
            raise RuntimeError("use ArtshubSource as an async context manager")
        status = 0
        for attempt in range(1, SERVER_ERROR_ATTEMPTS + 1):
            started = time.perf_counter()
            try:
                response = await session.get(url, params=params, headers=HEADERS)
            except curl_requests.RequestsError as error:
                log_request(
                    LOGGER.warning,
                    "GET",
                    url,
                    seconds=f"{time.perf_counter() - started:.3f}",
                    error=error,
                    attempt=attempt,
                )
                raise sources.SourceError(
                    f"cannot reach artshub.com.au: {error}"
                ) from error
            seconds = f"{time.perf_counter() - started:.3f}"
            status = response.status_code
            if status == 200:
                log_request(
                    LOGGER.info,
                    "GET",
                    response.url or url,
                    status=status,
                    seconds=seconds,
                )
                return response.text
            if status == 404 and missing_ok:
                return None
            mitigated = response.headers.get("cf-mitigated")
            log_request(
                LOGGER.warning,
                "GET",
                response.url or url,
                status=status,
                seconds=seconds,
                cf_mitigated=mitigated,
                attempt=attempt,
            )
            # A challenge belongs to the client, not to the origin. A retry
            # answers the same challenge, so the read stops.
            if mitigated is not None or status < 500:
                break
            await asyncio.sleep(RETRY_DELAY_SECONDS * attempt)
        raise sources.SourceError(f"artshub.com.au answered status {status}")

    async def _feed_page(
        self,
        page: int,
        start_date: str,
        end_date: str,
    ) -> list[sources.Gig]:
        """Return the gigs of one feed page. An empty list ends the pages."""
        params = {"location": LOCATION}
        if page > 1:
            params["paged"] = str(page)
        body = await self._text(FEED_URL, params, missing_ok=page > 1)
        if body is None:
            return []
        return parse_feed(body, start_date, end_date)

    async def _fit(
        self,
        gig: sources.Gig,
        start_date: str,
        end_date: str,
        pool: asyncio.Semaphore,
    ) -> sources.Gig | None:
        """Return the gig with its opening time, or None when it is not open.

        The event page holds the days and hours. A gig that has no readable
        page or no schedule stays in the list, with no time.
        """
        if gig.detail_url is None or DETAIL_URL_RE.fullmatch(gig.detail_url) is None:
            return gig
        try:
            async with pool:
                page = await self._text(gig.detail_url)
        except sources.SourceError as error:
            LOGGER.warning("cannot read %s: %s", gig.detail_url, error)
            return gig
        body, reference = parse_event_page(page or "")
        schedule = extract_schedule(body, reference)
        keep, time = fit_dates(schedule, start_date, end_date)
        return gig.model_copy(update={"time": time}) if keep else None

    async def gigs(self, start_date: str, end_date: str) -> list[sources.Gig]:
        """Return the gigs that are open in a date range.

        The feed holds a date range for each event. The page of each event
        holds its days and hours. A gig leaves the list when none of the
        chosen dates is one of its days. Its time is its opening time.
        """
        feed = []
        for page in range(1, MAX_PAGES + 1):
            items = await self._feed_page(page, start_date, end_date)
            feed.extend(items)
            if len(items) < PAGE_SIZE:
                break
        # ponytail: the list waits for every page read. The answer is cached
        # for the date range. Move the filter to the stream if it is slow.
        pool = asyncio.Semaphore(PAGE_CONCURRENCY)
        fitted = await asyncio.gather(
            *(self._fit(gig, start_date, end_date, pool) for gig in feed)
        )
        return [gig for gig in fitted if gig is not None]

    async def gig_detail(self, detail_url: str) -> sources.GigDetail | None:
        """Return the detail of one event.

        The time is in the gig list, so the read adds nothing and makes no
        request.
        """
        if DETAIL_URL_RE.fullmatch(detail_url) is None:
            return None
        return sources.GigDetail(detail_url=detail_url)

    async def venue(self, venue_url: str) -> sources.Venue:
        """Return the address data of one venue."""
        decoded = decode_venue(venue_url)
        if decoded is None:
            raise sources.SourceUrlError(VENUE_URL_HINT)
        name, address = decoded
        return sources.Venue(venue_url=venue_url, name=name, address=address)
