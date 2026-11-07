"""Read the City of Melbourne What's On event list.

This module holds every rule about whatson.melbourne.vic.gov.au: the search
address, the category filter, the card fields, the event page fields, and the
reads. It is the only module that reads whatson.melbourne.vic.gov.au. The
server holds the cache and the HTTP routes.

The site

The site is a Rails/Turbo app behind CloudFront. It answers a plain HTTP
request with no challenge, so one browser fingerprint is enough. `robots.txt`
allows `/search`, and both the search results and the event pages are
server-rendered HTML, so this module needs no JavaScript.

The search page is the list

`/search` answers the event list. The primary category is `c=things_to_do`,
and the three subcategories are `scs[]=theatre` for the checkbox "Theatre
and shows", `scs[]=music` for "Music, concerts, and gigs", and
`scs[]=comedy` for "Comedy". The three values are the whole category
filter of this source. A date query uses
`dkw=specific_dates` and `date_range` as `YYYY-MM-DD to YYYY-MM-DD`, with a
literal ` to ` separator and both ends included. The module sends the range
with `params`, so the square brackets of `scs[]` are encoded. `q=` is required
and empty.

A page holds 20 cards. A `page` above the last page answers 200 with zero
cards, so the module stops the loop on fewer than 20 cards.

The date filter

The search page filters on the run of an event, not on its sessions. A query
for a range returns events whose run window overlaps the range, even when no
session falls in those days. The card holds a `<time>` of the next session,
and that day is often outside the queried range. So the search page yields
candidates only, and the module does not trust its date. It reads each event
page to find the real sessions.

The session table

`table#date-times-table` holds the sessions, one row for each session day. A
row can hold two `li` entries, for example a matinee and an evening show, and
the module keeps the first. Each `li` holds a `span[aria-hidden=true]` and a
`span.sr-only`; the `sr-only` text is the clean time, so the module reads the
start and the end from it, for example `7:30pm - 9:45pm`.

The year problem

The session day text has no year, so the module takes the year from the JSON-LD
run window. It tries the year of `startDate` and the year after it, and keeps
the day that falls inside `[startDate, endDate]`. The site publishes only
some sessions of a long run; that is the site data, not a parse loss.

The JSON-LD block

Each event page holds a `<script type="application/ld+json">` block with
`"@type": "Event"` and the fields `name`, `description`, `image`, `startDate`,
`endDate`, `eventStatus`, and a `location` with a name and a street address.
The `image` is missing on some events, and the card image fills in then.

The venue

The site has no page for a venue. The event page holds the venue name and the
street address as text, and no coordinates. The module builds a synthetic
venue url that holds both, and `venue()` answers from the url with no network
read. The server geocodes the address on Nominatim.

The ticket

`div.price-and-bookings` holds the price and the booking link. A price table
holds rows such as `Adult / general $79`, and the module reads the first row
for the price. A free event holds `<p>This is a free event</p>` instead. A
booking link has the text `Book online`, and its `href` is the ticketing site.
When the page holds no price and no booking, the gig holds no ticket.

The 403 hazard

After about 45 reads in two minutes the site answers `403 Forbidden` for a
short time, and recovery is automatic. The module keeps the detail reads at
concurrency 3 or less and retries a 403 once after a short sleep. It does not
rotate the fingerprint, because the site has no challenge.

The tests

`uv run pytest tests/test_whatson.py` runs the tests. The tests call the parse
functions with saved HTML, so they do not touch the live site.
"""

import asyncio
import datetime
import json
import logging
import re
import time
from urllib.parse import quote, unquote, urlsplit

from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests
from pydantic import BaseModel, TypeAdapter

from gigmap import sources, stores
from gigmap.logger import log_request
from gigmap.schedule import clock_text, span_text

SITE_URL = "https://whatson.melbourne.vic.gov.au"
SITE_HOST = urlsplit(SITE_URL).netloc
SEARCH_URL = f"{SITE_URL}/search"
VENUE_PREFIX = f"{SITE_URL}/venue/"

# The app serves this one site, so the source carries the app details.
SOURCE = sources.SourceInfo(
    title="Melb What's On Gig Map",
    name="whatson",
    home_url=SEARCH_URL,
)

# The primary category and the three subcategories of the source. The
# values `theatre`, `music`, and `comedy` are the whole category filter
# of this source.
CATEGORY = "things_to_do"
SUBCATEGORIES = ("theatre", "music", "comedy")

# The site answers 20 cards on a page. A loop that reads more pages than this
# cannot be right, so it stops on a short page.
PAGE_SIZE = 20
MAX_PAGES = 30

VENUE_URL_HINT = (
    "venue_url must have the form "
    "https://whatson.melbourne.vic.gov.au/venue/<name>/<address>/"
)
DETAIL_URL_RE = re.compile(
    rf"https://{re.escape(SITE_HOST)}/things-to-do/(?P<slug>[^/?#]+)/?"
)
VENUE_PATH_PREFIX = "/venue/"

# The site answers a normal client, so one browser fingerprint is enough.
IMPERSONATE_TARGET = "chrome"
HEADERS = {
    "Accept-Language": "en-AU,en;q=0.9",
}

# The rate limit: too many reads in a short time answers 403. The detail
# reads hold the most reads, so they run at a low concurrency. A 403 is short,
# so the module retries once after a short sleep.
REQUEST_ATTEMPTS = 2
FORBIDDEN_DELAY_SECONDS = 3.0
REQUEST_TIMEOUT_SECONDS = 30.0
# The detail reads hold the most reads, so a request runs three at a time.
DETAIL_CONCURRENCY = 3
# One pool for the whole app, so every request shares the limit.
DETAIL_POOL = asyncio.Semaphore(DETAIL_CONCURRENCY)


PRICE_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
# The session day text has no year, for example `Sat 2 Jan`. The module reads
# the day and the month, and adds the year from the JSON-LD run window.
SESSION_DAY_RE = re.compile(r"(?P<day>\d{1,2})\s+(?P<month>[A-Za-z]{3})")
# The sr-only time of a session, for example `7:30pm - 9:45pm`. The module
# reads the start and then the end.
TIME_RE = re.compile(
    r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<meridiem>am|pm)",
    re.IGNORECASE,
)

LOGGER = logging.getLogger(__name__)


def text(value: object) -> str | None:
    """Return one stripped string, or None for a missing value."""
    return value.strip() if isinstance(value, str) and value.strip() else None


def absolute_url(value: object) -> str | None:
    """Return the full url of one path, or None for no path."""
    path = text(value)
    if path is None:
        return None
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return f"{SITE_URL}{path if path.startswith('/') else '/' + path}"


def search_params(
    start_date: str, end_date: str, subcategories: tuple[str, ...] = SUBCATEGORIES
) -> list[tuple[str, str]]:
    """Return the query params of one date range.

    The date filter is `dkw=specific_dates` and `date_range` as
    `YYYY-MM-DD to YYYY-MM-DD`, with a literal ` to ` and both ends included.
    `q=` is required and empty. Each subcategory is one `scs[]` key, so the
    params are a list of pairs and not a dict; a dict holds one key only,
    and a second category would replace the first.
    """
    params: list[tuple[str, str]] = [
        ("c", CATEGORY),
        ("dkw", "specific_dates"),
        ("date_range", f"{start_date} to {end_date}"),
        ("q", ""),
    ]
    params.extend(("scs[]", subcategory) for subcategory in subcategories)
    return params


def venue_url(name: str | None, street: str | None, locality: str | None, postal: str | None) -> str | None:
    """Return the synthetic venue address of one event, or None for no venue.

    The site has no page for a venue, so the address holds the name and the
    street address. The server geocodes the address on Nominatim for the
    coordinates. The cache names the file of a venue after the last part of the
    url, so the address is the last part.
    """
    name = quote(name or "", safe="")
    # The site can end the street with a comma, which gives `Street,, Suburb`.
    street = (text(street) or "").strip(" ,")
    locality = text(locality) or ""
    postal = text(postal) or ""
    rest = " ".join(part for part in (locality, postal) if part)
    address = ", ".join(part for part in (street, rest) if part)
    if not unquote(name) and not address:
        return None
    return f"{VENUE_PREFIX}{name}/{quote(address, safe='')}/"


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


def parse_card(card) -> sources.Gig | None:
    """Return one gig of a search card, or None for a card that is not a gig.

    The card holds the title and the url of the event, the image, the summary,
    and the tags. The date and the venue and the ticket sit on the event page,
    so this parse returns a candidate only.
    """
    link = card.select_one("a.main-link[data-gtm-variable-label]")
    if link is None:
        return None
    detail_url = absolute_url(link.get("href"))
    if detail_url is None or DETAIL_URL_RE.fullmatch(detail_url) is None:
        return None
    image = card.select_one("img.page_image")
    categories = [
        tag.get_text(strip=True) for tag in card.select("ul.tag-list a.link--tag")
    ]
    return sources.Gig(
        artist=text(link.get("data-gtm-variable-label")) or link.get_text(strip=True) or None,
        detail_url=detail_url,
        categories=[name for name in categories if name],
        image_url=absolute_url(image.get("src") if image is not None else None),
        image_alt=text(image.get("alt")) if image is not None else None,
    )


def parse_cards(page: str) -> list[sources.Gig]:
    """Return the candidate gigs of one search page. An empty list ends the pages."""
    soup = BeautifulSoup(page, "lxml")
    gigs = []
    for card in soup.select("[data-listing-type]"):
        listing_type = card.get("data-listing-type", "")
        # The site writes the value with embedded quotes, for example
        # `"event"`. The check keeps the event cards and skips the rest.
        if "event" not in str(listing_type):
            continue
        gig = parse_card(card)
        if gig is not None:
            gigs.append(gig)
    return gigs


def json_ld_data(soup) -> dict | None:
    """Return the JSON-LD Event block of one event page, or None for none."""
    for script in soup.select("script[type='application/ld+json']"):
        raw = script.string or script.get_text()
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and data.get("@type") == "Event":
            return data
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("@type") == "Event":
                    return item
    return None


def session_dates(day_text: str, start_date: str, end_date: str) -> list[str]:
    """Return the real dates of one session day.

    The day text has no year, for example `Sat 2 Jan`. The module tries the
    year of `start_date` and the year after it, and keeps the date that falls
    inside `[start_date, end_date]`.
    """
    match = SESSION_DAY_RE.search(day_text)
    if match is None:
        return []
    try:
        day = int(match.group("day"))
    except ValueError:
        return []
    month_name = match.group("month").title()
    try:
        month = datetime.datetime.strptime(month_name, "%b").month
    except ValueError:
        return []
    year = int(start_date[:4])
    dates = []
    for candidate_year in (year, year + 1):
        try:
            day_value = datetime.date(candidate_year, month, day).isoformat()
        except ValueError:
            continue
        if start_date <= day_value <= end_date:
            dates.append(day_value)
    return dates


def _clock(match: re.Match[str]) -> str | None:
    """Return the 24 hour `HH:MM` of one time match, or None for a bad value."""
    hour = int(match.group("hour")) % 12
    if match.group("meridiem").lower() == "pm":
        hour += 12
    minute = int(match.group("minute") or 0)
    if minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def time_pair(value: object) -> tuple[str | None, str | None]:
    """Return the start and the end of one session time, in `HH:MM` form.

    The sr-only text has the form `7:30pm - 9:45pm`. A text with one time has
    no end, so the end is None. A missing part is None too.
    """
    found = list(TIME_RE.finditer(value)) if isinstance(value, str) else []
    if not found:
        return None, None
    start = _clock(found[0])
    end = _clock(found[1]) if len(found) > 1 else None
    return start, end


def parse_sessions(soup) -> list[tuple[str, str | None, str | None]]:
    """Return the session days of one event page, as raw triples.

    Each result is the day text, the start and the end of the first session of
    that day, in `HH:MM` form. The day text has no year, so the range is
    applied later by `fit_event`. A day with two sessions keeps the first.
    """
    sessions: list[tuple[str, str | None, str | None]] = []
    for row in soup.select("table#date-times-table tr"):
        header = row.find("th")
        if header is None:
            continue
        # The first li of the day holds the first session.
        start: str | None = None
        end: str | None = None
        for li in row.select("td li"):
            sr = li.select_one("span.sr-only")
            start, end = time_pair(sr.get_text(strip=True) if sr else None)
            if start is not None:
                break
        sessions.append((header.get_text(" ", strip=True), start, end))
    return sessions


def price_of(display: str | None) -> tuple[str | None, float | None, float | None]:
    """Return the display text and the price range of one price text.

    The numbers in the text are the low and the high of the range.
    """
    display = text(display)
    numbers = [float(value) for value in PRICE_NUMBER_RE.findall(display or "")]
    low = min(numbers) if numbers else None
    high = max(numbers) if numbers else None
    return display, low, high

def booking_link(box) -> str | None:
    """Return the booking url of one price box, or None for no booking link.

    The box holds a link with the text `Book online`. A free event holds no
    such link on some pages, so the text of the link is the check and not its
    position in the box.
    """
    for link in box.select("a"):
        if "book online" in link.get_text(" ", strip=True).lower():
            return absolute_url(link.get("href"))
    return None


def parse_ticket(page: str) -> sources.Ticket | None:
    """Return the ticket of one event page, or None for no ticket data.

    A free event holds `<p>This is a free event</p>` with no price. An event
    with a price table holds the first price row; an event with an empty
    price table holds a price as a `<p>` such as `From $57 to $346`. A booking
    link has the text `Book online`. The page is used only for the ticket, so
    the caller passes the raw HTML text and this function parses it.
    """
    soup = BeautifulSoup(page, "lxml")
    box = soup.select_one("div.price-and-bookings")
    if box is None:
        return None
    paragraphs = box.select("p")
    booking_url = booking_link(box)
    if any("free event" in p.get_text(" ", strip=True).lower() for p in paragraphs):
        return sources.Ticket(
            label="Free event",
            group=sources.TicketGroup.FREE,
            url=booking_url,
        )
    price_table = box.select_one("table.price-table")
    rows = price_table.select("tr") if price_table is not None else []
    display: str | None = None
    if rows:
        display = text(rows[0].get_text(" ", strip=True))
    else:
        for p in paragraphs:
            candidate = text(p.get_text(" ", strip=True))
            if candidate and any(c in candidate for c in "$€£"):
                display = candidate
                break
    if display is not None:
        _, low, high = price_of(display)
        return sources.Ticket(
            label=display,
            group=sources.TicketGroup.ON_SALE,
            price=display,
            price_min=low,
            price_max=high,
            url=booking_url,
        )
    if booking_url is None:
        return None
    return sources.Ticket(
        label="Book online",
        group=sources.TicketGroup.ON_SALE,
        url=booking_url,
    )


class WhatsonEvent(BaseModel):
    """The range-free parse of one event page."""

    venue_name: str | None
    street: str | None
    locality: str | None
    postal: str | None
    run_start: str
    run_end: str
    image: str | None
    ticket: sources.Ticket | None
    sessions: list[tuple[str, str | None, str | None]]


# The range-free parse of each event page, keyed by url. whatsonart shares it.
EVENTS = stores.event_cache("whatson", TypeAdapter(WhatsonEvent))


def read_event(page: str) -> WhatsonEvent | None:
    """Return the range-free parse of one event page, or None with no JSON-LD."""
    soup = BeautifulSoup(page, "lxml")
    data = json_ld_data(soup)
    if data is None:
        return None
    venue_name = None
    address = None
    location = data.get("location")
    if isinstance(location, dict):
        venue_name = text(location.get("name"))
        addr = location.get("address")
        if isinstance(addr, dict):
            address = addr
    run_start = text(data.get("startDate")) or ""
    street = locality = postal = None
    if address is not None:
        street = text(address.get("streetAddress"))
        locality = text(address.get("addressLocality"))
        postal = text(address.get("postalCode"))
    return WhatsonEvent(
        venue_name=venue_name,
        street=street,
        locality=locality,
        postal=postal,
        run_start=run_start,
        run_end=text(data.get("endDate")) or run_start,
        image=data.get("image") if isinstance(data, str) else None,
        ticket=parse_ticket(page),
        sessions=parse_sessions(soup),
    )


def fit_event(
    event: WhatsonEvent, card: sources.Gig, start_date: str, end_date: str
) -> list[sources.Gig]:
    """Return one gig for each session day that falls inside the range.

    The card holds the title, the url, the categories, and the image; the
    event holds the run window, the venue, and the sessions.
    """
    venue = event.venue_name or (event.street or event.locality)
    gigs = []
    for day_text, start, end in event.sessions:
        for day_value in session_dates(day_text, start_date, end_date):
            # The search filter is coarse, so the year and the range of the run
            # are re-checked here against the real session day.
            if not (event.run_start <= day_value <= event.run_end):
                continue
            gigs.append(
                sources.Gig(
                    artist=card.artist,
                    detail_url=card.detail_url,
                    date=day_value,
                    venue=venue,
                    venue_url=venue_url(
                        event.venue_name, event.street, event.locality, event.postal
                    ),
                    categories=list(card.categories),
                    ticket=event.ticket,
                    image_url=absolute_url(event.image) or card.image_url,
                    image_alt=card.image_alt,
                    time=clock_text(start) if start else None,
                    hours=span_text(start, end),
                )
            )
    return gigs


def parse_event(page: str, card: sources.Gig, start_date: str, end_date: str) -> list[sources.Gig]:
    """Return the gigs of one event page that fall in the date range."""
    event = read_event(page)
    return [] if event is None else fit_event(event, card, start_date, end_date)


class WhatsonSource:
    """Read the data from whatson.melbourne.vic.gov.au through one pool.

    This class is the `sources.GigSource` implementation for the live site.
    """

    subcategories = SUBCATEGORIES

    def __init__(self) -> None:
        self._session: curl_requests.AsyncSession | None = None

    async def __aenter__(self) -> "WhatsonSource":
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
        params: list[tuple[str, str]] | None = None,
    ) -> str:
        """Return the page text, with a retry on a 403 answer.

        The site rate-limits: after too many reads in a short time it answers a
        403 for a short while, and recovery is automatic. The module retries
        once after a short sleep, and then reports a source error.
        """
        session = self._session
        if session is None:
            raise RuntimeError("use WhatsonSource as an async context manager")
        status = 0
        for attempt in range(1, REQUEST_ATTEMPTS + 1):
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
                    f"cannot reach whatson.melbourne.vic.gov.au: {error}"
                ) from error
            seconds = f"{time.perf_counter() - started:.3f}"
            status = response.status_code
            # The try number matters only when the call failed.
            extra = {} if status == 200 else {"attempt": attempt}
            log_request(
                LOGGER.info if status == 200 else LOGGER.warning,
                "GET",
                response.url or url,
                status=status,
                seconds=seconds,
                **extra,
            )
            if status == 200:
                return response.text
            if status == 403 and attempt < REQUEST_ATTEMPTS:
                await asyncio.sleep(FORBIDDEN_DELAY_SECONDS)
                continue
            break
        raise sources.SourceError(
            f"whatson.melbourne.vic.gov.au answered status {status}"
        )

    async def candidates(self, start_date: str, end_date: str) -> list[sources.Gig]:
        """Return the candidate cards of one range, one card for each event.

        A card of the search page holds no real session, so the cards are the
        input of `expand` and not the gigs. A url appears once.
        """
        seen: set[str] = set()
        candidates: list[sources.Gig] = []
        for page in range(1, MAX_PAGES + 1):
            params = [*search_params(start_date, end_date, self.subcategories), ("page", str(page))]
            cards = parse_cards(await self._text(SEARCH_URL, params))
            for card in cards:
                url = card.detail_url
                if url is None or url in seen:
                    continue
                seen.add(url)
                candidates.append(card)
            if len(cards) < PAGE_SIZE:
                break
        return candidates

    async def expand(
        self, gig: sources.Gig, start_date: str, end_date: str
    ) -> list[sources.Gig]:
        """Return the session gigs of one event, and none when its read fails.

        One event page that fails must not lose the other events of the range,
        so a source error drops this event only and is logged.
        """
        url = gig.detail_url or ""
        key = sources.url_key(url)
        event = EVENTS.load(key)
        if event is None:
            try:
                async with DETAIL_POOL:
                    body = await self._text(url)
            except sources.SourceError as error:
                LOGGER.warning("skip the event page %s: %s", url, error)
                return []
            event = read_event(body)
            if event is None:
                return []
            EVENTS.save(key, event)
        return fit_event(event, gig, start_date, end_date)

    async def venue(self, venue_url: str) -> sources.Venue:
        """Return the address data of one venue."""
        decoded = decode_venue(venue_url)
        if decoded is None:
            raise sources.SourceUrlError(VENUE_URL_HINT)
        name, address = decoded
        return sources.Venue(venue_url=venue_url, name=name, address=address)
