"""Read the Arts Centre Melbourne event calendar.

This module holds every rule about artscentremelbourne.com.au: the API
address, the request body, the row fields, and the reads. It is the only
module that reads artscentremelbourne.com.au. The server holds the cache and
the HTTP routes.

The site

The site is Sitecore behind Imperva. The calendar page at
`/whats-on/event-calendar` is a React shell with no event in the HTML, and it
answers an Imperva challenge to a client that is not a browser. The React
bundle asks one JSON endpoint for the events, and that endpoint answers a
normal client. This module reads the endpoint.

The read

The endpoint is `POST /api/acm/events/GetFilters`. The name says filter, but
the body is a date window and the answer is the event list. The body holds
`StartDate` and `EndDate` as `YYYY-MM-DD`, the two ends included, and
`ItemsPerPage` as 1000. The answer holds the event rows as a list.

The site writes the name of each field in one of two spellings. A request
that asks for JSON holds `events` and `title`; a request that asks for
nothing in particular holds `Events` and `Title`. The module reads both, so
a change of the header does not stop it.

The window

The endpoint keeps at most 180 days from `StartDate`. A longer request stops
at that day with no error, so a long range needs more than one read. The
module reads a range in windows of 170 days, which is under the limit of the
site.

The page key of the read does not step through the window. It steps by seven
days from `StartDate`, so page 2 repeats part of page 1. The module never
sends a page above 1 and reads the window again instead.

The rows

The endpoint answers one row for each event and each day of its run. A row is
not a performance: a day with three shows holds one row, and the row holds
the first time of the day. The same event on the next day is another row. The
module maps one row to one gig, so a run of four nights is four gigs.

A row holds the title, the relative url of the event, the venue name, the
image, the categories, the label, and the booking url. It holds no price, so
the ticket of a row has no price.

Exhibitions and guided tours hold a row for each open day, and they are most
of the calendar. The module does not read them, because they are not the gigs
that this app shows. `EXCLUDED_CATEGORIES` holds the rule.

The venue

The calendar row holds the venue name and no venue url. The site has a page
for each of its spaces, and the page holds the street address. The page is
behind Imperva and is slow, so the module does not read it. The module holds
the address of the main spaces in `VENUE_ADDRESSES` and builds a venue
address from the name. `venue` answers from that table without a read.

A space that is not in the table holds no address, and the geocoder reads
the name of the space. A festival that holds its shows in more than one
space holds the name `Various`. That row keeps the name as text and holds
no venue address, because a pin of it would be a false pin.

The ticket

A row holds the booking url of the event and a label such as `New Show!`. A
row holds no sold-out state and no price; that data sits on the event page
only, and the page costs one slow read for each event. The module builds the
ticket from the row and does not read the event page.

The tests

`uv run pytest tests/test_artscentre.py` runs the tests. The tests call the
parse functions with rows of their own, so they do not touch the live site.
"""

import asyncio
import datetime
import logging
import re
import time
from urllib.parse import quote, unquote, urlsplit

from curl_cffi import requests as curl_requests

from gigmap import sources
from gigmap.logger import log_request

SITE_URL = "https://www.artscentremelbourne.com.au"
SITE_HOST = urlsplit(SITE_URL).netloc
EVENTS_URL = f"{SITE_URL}/api/acm/events/GetFilters"
VENUE_PREFIX = f"{SITE_URL}/venue/"

SOURCE = sources.SourceInfo(
    title="Melb Arts Centre Gig Map",
    name="artscentremelbourne.com.au",
    home_url=f"{SITE_URL}/whats-on/event-calendar",
)

# The endpoint reads a window and answers the rows of that window. 170 days
# stays under the limit of 180 days of the site.
WINDOW_DAYS = 170
ROWS_PER_PAGE = 1000

# A row of these categories holds one entry for each open day, so a long
# exhibition fills the calendar. The app shows gigs, so the module skips
# them. The rule is here, and one edit restores the rows.
EXCLUDED_CATEGORIES = frozenset({"Exhibition", "Guided Tour"})

# The street address of each space. The calendar names the space and no
# more, and the map needs a street. The site page of a space holds the same
# address, and this table saves one slow read for each venue.
VENUE_ADDRESSES = {
    "Hamer Hall": "100 St Kilda Rd, Melbourne VIC 3004",
    "Hamer Hall and Theatres Building": "100 St Kilda Rd, Melbourne VIC 3004",
    "Ian Potter State Theatre": "100 St Kilda Rd, Melbourne VIC 3004",
    "Playhouse": "100 St Kilda Rd, Melbourne VIC 3004",
    "Fairfax Studio": "100 St Kilda Rd, Melbourne VIC 3004",
    "The Show Room": "100 St Kilda Rd, Melbourne VIC 3004",
    "Sidney Myer Music Bowl": "21 Linlithgow Ave, Melbourne VIC 3000",
    "Australian Music Vault": "100 St Kilda Rd, Melbourne VIC 3004",
}

# A festival that holds its shows in more than one space holds the name
# `Various`. It is not a place, so a map pin of it would be a false pin.
NO_VENUE_NAMES = frozenset({"Various"})

VENUE_URL_HINT = (
    "venue_url must have the form "
    "https://www.artscentremelbourne.com.au/venue/<name>/"
)
VENUE_PATH_PREFIX = "/venue/"

# The site answers a normal client, so one browser fingerprint is enough.
# The HTML pages sit behind Imperva and answer a challenge, and this module
# does not read them.
IMPERSONATE_TARGET = "chrome"
HEADERS = {
    "Accept-Language": "en-AU,en;q=0.9",
    "Accept": "application/json",
    "Content-Type": "application/json",
}

# A wide window can hold a lot of rows and the site is slow to answer. A
# retry covers a timeout, which the site shows now and then.
REQUEST_ATTEMPTS = 2
RETRY_DELAY_SECONDS = 3.0
REQUEST_TIMEOUT_SECONDS = 120.0

DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
STAMP_RE = re.compile(r"(?P<day>\d{4}-\d{2}-\d{2})T(?P<hour>\d{2}):(?P<minute>\d{2})")

LOGGER = logging.getLogger(__name__)


def text(value: object) -> str | None:
    """Return one stripped string, or None for a missing value."""
    return value.strip() if isinstance(value, str) and value.strip() else None


def split_day(value: object) -> str | None:
    """Return the day of one row stamp.

    The stamp has the form `2026-11-08T00:00:00`.
    """
    if not isinstance(value, str):
        return None
    match = DAY_RE.match(value)
    return match.group() if match is not None else None


def time_text(value: object) -> str | None:
    """Return the display time of one row stamp.

    The stamp has the form `2026-11-08T12:30:00+11:00`. The site writes the
    time of the place as the first time of the day, with the offset of the
    season, so the hour of the stamp is the hour to show. A stamp at 00:00
    marks an event with no time of day, so the time is None.
    """
    if not isinstance(value, str):
        return None
    match = STAMP_RE.search(value)
    if match is None:
        return None
    hour = int(match.group("hour"))
    minute = match.group("minute")
    if hour == 0 and minute == "00":
        return None
    meridiem = "am" if hour < 12 else "pm"
    return f"{hour % 12 or 12}:{minute}{meridiem}"


def absolute_url(value: object) -> str | None:
    """Return the full url of one row path."""
    path = text(value)
    if path is None:
        return None
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return f"{SITE_URL}{path if path.startswith('/') else '/' + path}"


def venue_url(name: str | None) -> str | None:
    """Return the venue address of one venue name, or None for no venue.

    The site has no one address for a venue that the calendar names, so the
    address holds the name and `venue` reads the table of the module. The
    cache names the file of a venue after the last part of the url, so the
    name is the last part.
    """
    if not name:
        return None
    return f"{VENUE_PREFIX}{quote(name, safe='')}/"


def decode_venue(url: str) -> str | None:
    """Return the venue name of one venue address.

    None when the address is not a venue address of this site.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or parts.netloc != SITE_HOST:
        return None
    if not parts.path.startswith(VENUE_PATH_PREFIX):
        return None
    segments = parts.path[len(VENUE_PATH_PREFIX) :].split("/")
    return text(unquote(segments[0])) if segments else None


def is_gig(row: dict) -> bool:
    """Return true when one row is a gig that this app shows."""
    categories = row.get("categories") or []
    return not EXCLUDED_CATEGORIES.intersection(categories, {row.get("category")})


def keyed(mapping: dict) -> dict:
    """Return one answer object with a lower first letter on each key.

    The endpoint writes `Events` or `events` for the same data. The `Accept`
    header of the request picks the spelling, and the site answers a normal
    browser either way, so the module reads both.
    """
    return {key[:1].lower() + key[1:]: value for key, value in mapping.items()}


def events_of(answer: object) -> list[dict]:
    """Return the event rows of one answer."""
    rows = keyed(answer).get("events") if isinstance(answer, dict) else None
    if not isinstance(rows, list):
        raise sources.SourceError("artscentremelbourne.com.au answered no event list")
    return [row for row in rows if isinstance(row, dict)]


def ticket_of(row: dict) -> sources.Ticket | None:
    """Return the ticket data of one row, or None for no ticket data.

    The row holds a booking address, a button text, and a note such as
    `New Show!`. It holds no price and no sale state. A row with a booking
    address of `#` books in a window on the event page, so it holds no
    address that this app can open.

    The module reports a ticket in two cases only, because the badge of a
    ticket reads as a call to action. A row with no address and no note
    holds no ticket.
    """
    note = text(row.get("label"))
    action = text(row.get("bookNowText"))
    url = text(row.get("bookNowUrl"))
    if url == "#":
        url = None
    categories = row.get("categories") or []
    if "Free Event" in categories:
        return sources.Ticket(
            state="free",
            label=note or action or "Free event",
            group=sources.TicketGroup.FREE,
            url=url,
        )
    if url is None and note is None:
        return None
    return sources.Ticket(
        state="on_sale",
        label=note or action or "On sale",
        group=sources.TicketGroup.ON_SALE,
        url=url,
    )


def parse_row(row: dict) -> sources.Gig | None:
    """Return one gig of a calendar row, or None for a row that is not one.

    A row with no day cannot fall in a date range, and a row of an excluded
    category is not a gig of this app.
    """
    row = keyed(row)
    artist = text(row.get("title"))
    day = split_day(row.get("eventDateValue"))
    if artist is None or day is None or not is_gig(row):
        return None
    venue_name = text(row.get("venue"))
    categories = [name for name in row.get("categories") or [] if text(name)]
    category = text(row.get("category"))
    return sources.Gig(
        artist=artist,
        detail_url=absolute_url(row.get("url")),
        date=day,
        venue=venue_name,
        venue_url=None if venue_name in NO_VENUE_NAMES else venue_url(venue_name),
        genres=[category] if category else [],
        categories=categories,
        ticket=ticket_of(row),
        image_url=absolute_url(row.get("desktopImage")),
        time=time_text(row.get("eventTimeValue")),
    )


def parse_answer(
    answer: object,
    start_date: str,
    end_date: str,
) -> list[sources.Gig]:
    """Return the gigs of one answer that fall in the date range."""
    rows = events_of(answer)
    gigs = []
    for row in rows:
        gig = parse_row(row)
        if gig is None or not start_date <= (gig.date or "") <= end_date:
            continue
        gigs.append(gig)
    return gigs


def window_ends(start_date: str, end_date: str) -> list[tuple[str, str]]:
    """Return the date windows that cover one range.

    The endpoint keeps at most 180 days from the first day, so a long range
    needs more than one read.
    """
    try:
        start = datetime.date.fromisoformat(start_date)
        end = datetime.date.fromisoformat(end_date)
    except ValueError as error:
        raise sources.SourceError("the date range is not a pair of dates") from error
    if end < start:
        raise sources.SourceError("the date range ends before it starts")
    step = datetime.timedelta(days=WINDOW_DAYS - 1)
    windows = []
    first = start
    while first <= end:
        last = min(first + step, end)
        windows.append((first.isoformat(), last.isoformat()))
        first = last + datetime.timedelta(days=1)
    return windows


class ArtsCentreSource:
    """Read the data from artscentremelbourne.com.au through one pool.

    This class is the `sources.GigSource` implementation for the live site.
    """

    def __init__(self) -> None:
        self._session: curl_requests.AsyncSession | None = None

    async def __aenter__(self) -> "ArtsCentreSource":
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

    def _body(self, start_date: str, end_date: str) -> dict:
        """Return the request body of one date window."""
        return {
            "StartDate": start_date,
            "EndDate": end_date,
            "TileType": 2,
            "PageNum": 1,
            "SortOrder": 2,
            "ItemsPerPage": ROWS_PER_PAGE,
            "GenreId": None,
            "Filters": [],
            "PageType": 1,
        }

    async def _read_window(self, start_date: str, end_date: str) -> list[sources.Gig]:
        """Return the gigs of one date window."""
        session = self._session
        if session is None:
            raise RuntimeError("use ArtsCentreSource as an async context manager")
        status = 0
        for attempt in range(1, REQUEST_ATTEMPTS + 1):
            started = time.perf_counter()
            try:
                response = await session.post(
                    EVENTS_URL,
                    json=self._body(start_date, end_date),
                    headers=HEADERS,
                )
            except curl_requests.RequestsError as error:
                log_request(
                    LOGGER.warning,
                    "POST",
                    EVENTS_URL,
                    seconds=f"{time.perf_counter() - started:.3f}",
                    error=error,
                    attempt=attempt,
                )
                if attempt == REQUEST_ATTEMPTS:
                    raise sources.SourceError(
                        f"cannot reach artscentremelbourne.com.au: {error}"
                    ) from error
                await asyncio.sleep(RETRY_DELAY_SECONDS * attempt)
                continue
            seconds = f"{time.perf_counter() - started:.3f}"
            status = response.status_code
            # The try number matters only when the call failed.
            extra = {} if status == 200 else {"attempt": attempt}
            log_request(
                LOGGER.info if status == 200 else LOGGER.warning,
                "POST",
                EVENTS_URL,
                status=status,
                seconds=seconds,
                **extra,
            )
            if status == 200:
                try:
                    answer = response.json()
                except ValueError as error:
                    raise sources.SourceError(
                        "artscentremelbourne.com.au answered damaged JSON"
                    ) from error
                return parse_answer(answer, start_date, end_date)
            # A challenge belongs to the client and a 4xx belongs to the
            # request. A retry answers the same, so the read stops.
            if status < 500 or attempt == REQUEST_ATTEMPTS:
                break
            await asyncio.sleep(RETRY_DELAY_SECONDS * attempt)
        raise sources.SourceError(
            f"artscentremelbourne.com.au answered status {status}"
        )

    async def candidates(self, start_date: str, end_date: str) -> list[sources.Gig]:
        """Return the candidates of a date range."""
        gigs = []
        for first, last in window_ends(start_date, end_date):
            gigs.extend(await self._read_window(first, last))
        return gigs

    async def expand(
        self, gig: sources.Gig, start_date: str, end_date: str
    ) -> list[sources.Gig]:
        """Give the gig back. The list row holds its time."""
        return [gig]

    async def venue(self, venue_url: str) -> sources.Venue:
        """Return the address data of one venue."""
        name = decode_venue(venue_url)
        if name is None:
            raise sources.SourceUrlError(VENUE_URL_HINT)
        return sources.Venue(
            venue_url=venue_url,
            name=name,
            address=VENUE_ADDRESSES.get(name),
        )
