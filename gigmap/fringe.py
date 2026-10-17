"""Read the Melbourne Fringe what's-on API.

This module holds every rule about the site: the API addresses, the date
filter, the request headers, the JSON fields, and the reads. It is the only
module that reads melbournefringe.com.au. The server holds the cache and the
HTTP routes.

The site

The site is Next.js over Umbraco behind Cloudflare. The event grid does not
come from the page markup. It comes from a JSON API on the `api.` host.

The date filter

The API filters by date on the server. The key `dates` holds one entry for
each day in the form YYYY-MM-DD. The API answers the events whose run covers
one of those days, so one request covers a range. The keys `startDate` and
`endDate` are not part of the filter. The site never sends them, and a
request with them answers no event.

The reads

The module makes three reads.

* `POST /api/WhatsOn/DailyDiary` with `{"day": "YYYY-MM-DD"}` answers one row
  for each performance on one day. The row holds the event, the venue, the
  accessibility list, and the local time of the performance. This read gives
  the gig list.
* `GET /umbraco/delivery/api/v2/content` with `whatsOnEvent` answers the event
  list of the whole festival. The row holds the genre and the ticket price,
  and it holds no time. This read adds the genre and the price to a gig.
* `GET /umbraco/delivery/api/v2/content` with `whatsOnVenue` answers the venue
  list. The row holds the page address, the street address, and the
  coordinates. The site sends the `bravo` key from the browser, so the reads
  are public.

The event list holds about 1900 rows for 1.4 MB, so the read carries a
projection of four fields and not the whole row. The whole row is 20 MB.
The row id of the event list is the event id of a diary row, so the two
reads join on the id.

The price

The event row holds `price`, `maxPrice`, `preview`, `previewPrice`, and
`priceRangeOverride`. The module forms the same text that the site shows:
a range, one price, or the word `Free`. The read checks that text against
the `priceRange` of the what's-on API, and it agrees with that text for
about 99 events of every 100. Two cases can differ. The site can take a
cheap ticket tier that the event row does not hold. An event can declare no
price and still sell a ticket.

The exact price of one performance is at
`GET /api/Event/Performance?EventId=<id>&PerformanceId=<id>`. A diary row
holds both ids. The row holds one `priceTypes` entry for each ticket band,
with a `fullPrice` and a list of `concessions`. The module does not read
that address, because one date range needs about 190 reads.


The ticket link

The site has no address for one performance. The book button of every event
card sends the reader to the anchor `#sessions-book-now`, where the sessions
list shows each performance. That anchor is the value of `ticket_url`.

A free gig holds no link. Every other gig holds the link, even when the
price is not known, so the reader can open the sessions list.

The ticket state

Each performance row of the diary holds a `status`. The value is
`Available`, `SellingFast`, `AlmostGone`, or `SoldOut`. The site marks a
free unticketed event with `SoldOut` as well. That event holds no ticket
to sell out, so the state belongs to a gig with a booking link only. A
free gig then reads `Free`.

The shapes

One gig is one performance, not one event. The site has no page for one
performance, so every performance of an event carries the same `detail_url`.
The time comes from the DailyDiary row, so `gigs` fills the `time` field
itself. `gig_detail` leaves the time empty, because the server must keep the
time of the list.

The times

The API writes the time of a performance with a trailing `Z`. The value is
Melbourne local time, not UTC. The module reads the hour and the minute from
the text and does not convert it. A wrong conversion gives a gig on the
wrong day.

The `On Demand` group does not belong to the requested day. The group holds
the digital events of the whole festival, and each row carries the release
date. The module drops a row with `onDemand` true, so a digital event does
not appear on every day.

The tests

`uv run pytest tests/test_fringe.py` runs the tests. The tests use a fake
transport, so they do not touch the live site.
"""

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from datetime import date, timedelta
from typing import Any, NamedTuple, TypeVar
from urllib.parse import urlsplit

import httpx

from gigmap import sources

SITE_URL = "https://www.melbournefringe.com.au"
SITE_HOST = urlsplit(SITE_URL).netloc
API_URL = "https://api.melbournefringe.com.au"
DIARY_URL = f"{API_URL}/api/WhatsOn/DailyDiary"
CONTENT_URL = f"{API_URL}/umbraco/delivery/api/v2/content"

# The browser sends this key, so the delivery API is public.
CONTENT_API_KEY = "bravo"
VENUE_CONTENT_TYPE = "whatsOnVenue"
EVENT_CONTENT_TYPE = "whatsOnEvent"
# The delivery API answers a projection, so one read of the event list
# carries the few fields that this module needs and not the whole row.
EVENT_FIELDS = (
    "properties[price,maxPrice,preview,previewPrice,priceRangeOverride,genre]"
)
# The site sends this anchor from every event card. The sessions list below
# the anchor is the only booking path that the site offers.
BOOKING_ANCHOR = "#sessions-book-now"
# The price text of a free event. A free event needs no booking link.
FREE_PRICE = "Free"

# The app serves this one site, so the source carries the app details.
SOURCE = sources.SourceInfo(
    title="Melb Fringe Gig Map",
    name="melbournefringe.com.au",
    home_url=f"{SITE_URL}/whats-on/events/",
)

VENUE_URL_HINT = (
    "venue_url must have the form "
    "https://www.melbournefringe.com.au/whats-on/venues/<name>/"
)

# Cloudflare answers status 403 (error 1010) to the default "Python-urllib"
# agent. The read needs a browser agent. The API needs no key and no cookie.
HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "en-AU,en;q=0.9",
    "Origin": SITE_URL,
    "Referer": f"{SITE_URL}/whats-on/events/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
}

# The site allows a date filter of at most 366 days.
MAX_RANGE_DAYS = 366
# One delivery API page. The venue list holds about 240 rows, and the
# event list of every festival holds about 1900.
VENUE_PAGE_SIZE = 100
EVENT_PAGE_SIZE = 1000

# The app makes one request for each day of a range, and the requests run
# together. The limit is small, because the site is a small origin.
REQUEST_CONCURRENCY = 8
# Cloudflare or the origin can answer a 5xx. The fault is short in most
# cases, so the module retries the same read.
SERVER_ERROR_ATTEMPTS = 2
RETRY_DELAY_SECONDS = 1.0
REQUEST_TIMEOUT_SECONDS = 30.0

DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
TIME_RE = re.compile(r"T(?P<hour>\d{2}):(?P<minute>\d{2})")
TIME_ORDER_RE = re.compile(
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})(?P<meridiem>am|pm)"
)
# A slug can hold a non-ASCII character, for example "obang-오방".
EVENT_PATH_RE = re.compile(r"/whats-on/events/(?P<slug>[^/?#]+)/?$")
VENUE_PATH_RE = re.compile(r"/whats-on/venues/(?P<slug>[^/?#]+)/?$")

LOGGER = logging.getLogger(__name__)

T = TypeVar("T")


def text(value: object) -> str | None:
    """Return one stripped string, or None for a missing value."""
    return value.strip() if isinstance(value, str) and value.strip() else None


def digit_value(value: str) -> int:
    """Return the number of one digit group. A damaged group answers 0."""
    try:
        return int(value)
    except ValueError:
        return 0


def number(value: object) -> float | None:
    """Return one float, or None for a missing or damaged value."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def name_key(value: object) -> str:
    """Return the comparison key of one venue name."""
    return value.strip().lower() if isinstance(value, str) else ""


async def gather_limited(
    calls: Sequence[Callable[[], Awaitable[T]]], limit: int
) -> list[T]:
    """Run the calls together, with at most `limit` in flight."""
    semaphore = asyncio.Semaphore(limit)

    async def run(call: Callable[[], Awaitable[T]]) -> T:
        async with semaphore:
            return await call()

    return list(await asyncio.gather(*(run(call) for call in calls)))


def parse_day(value: str) -> date:
    """Return one date. A damaged value stops the read."""
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise sources.SourceError(f"{value} is not a date") from error


def days_of_range(start_date: str, end_date: str) -> list[str]:
    """Return every day of a range, as YYYY-MM-DD."""
    start = parse_day(start_date)
    end = parse_day(end_date)
    if end < start:
        raise sources.SourceError("end_date is before start_date")
    count = (end - start).days + 1
    if count > MAX_RANGE_DAYS:
        raise sources.SourceError(
            f"the range holds more than {MAX_RANGE_DAYS} days"
        )
    return [(start + timedelta(days=step)).isoformat() for step in range(count)]


def local_time_text(stamp: object) -> str | None:
    """Return the local time of one timestamp, for example "7:00pm".

    The API writes a trailing `Z`, and the value is local time all the same.
    The read takes the hour and the minute from the text and converts
    nothing.
    """
    if not isinstance(stamp, str):
        return None
    match = TIME_RE.search(stamp)
    if match is None:
        return None
    hour = digit_value(match.group("hour"))
    meridiem = "am" if hour < 12 else "pm"
    return f"{hour % 12 or 12}:{match.group('minute')}{meridiem}"


def time_order(time_text: str | None) -> int:
    """Return the minute of the day of one time text. A missing time is last."""
    match = TIME_ORDER_RE.fullmatch(time_text or "")
    if match is None:
        return 24 * 60
    hour = digit_value(match.group("hour")) % 12
    if match.group("meridiem") == "pm":
        hour += 12
    return hour * 60 + digit_value(match.group("minute"))


def site_path(url: str) -> str | None:
    """Return the path of one site url, or None for another site."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or parts.netloc != SITE_HOST:
        return None
    return parts.path


def event_page_url(route: object) -> str | None:
    """Return the page address of one event route, without the tracking query."""
    if not isinstance(route, str):
        return None
    path = urlsplit(route).path
    if EVENT_PATH_RE.search(path) is None:
        return None
    return SITE_URL + path


class EventFacts(NamedTuple):
    """The genre and the ticket price of one event.

    The diary row of a performance holds neither, so the gig list reads
    the event list one time for the whole process.
    """

    genres: list[str]
    price: str | None


def price_text(properties: dict[str, Any]) -> str | None:
    """Return the ticket price of one event as the site shows it.

    The site shows a range with two prices, one price, or the word `Free`.
    The read matches that form.

    A preview performance costs less than the run price, so the read takes
    the cheaper price as the low value. The site rounds a price to a whole
    dollar with the rule of the .NET runtime, that is to the nearest even
    number, so 24.50 becomes 24 and 27.50 becomes 28.

    The site takes the cheapest real ticket of every performance. The event
    row does not hold every one of those prices, so the read is a close
    indication and not a promise.
    """
    override = text(properties.get("priceRangeOverride"))
    if override is not None:
        return override
    low = number(properties.get("price"))
    if low is None:
        return None
    high = number(properties.get("maxPrice"))
    if high is None:
        high = low
    if properties.get("preview"):
        preview = number(properties.get("previewPrice"))
        if preview is not None:
            low = min(low, preview)
    low, high = round(low), round(high)
    if low == 0 and high == 0:
        return FREE_PRICE
    if low == high:
        return f"${low:.2f}"
    return f"${low:.2f} - ${high:.2f}"


def parse_event_facts(row: dict[str, Any]) -> EventFacts:
    """Return the genre and the price of one event list row."""
    properties = row.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    genres = []
    rows = properties.get("genre")
    if isinstance(rows, list):
        for genre in rows:
            name = text(genre.get("name")) if isinstance(genre, dict) else None
            if name is not None:
                genres.append(name)
    return EventFacts(genres=genres, price=price_text(properties))


def accessibility_names(rows: object) -> list[str]:
    """Return the names of the accessibility rows of one diary event."""
    if not isinstance(rows, list):
        return []
    names = []
    for row in rows:
        name = text(row.get("name")) if isinstance(row, dict) else None
        if name is not None:
            names.append(name)
    return names


def ticket_link(detail_url: str | None, price: str | None) -> str | None:
    """Return the booking link of one gig, or None when there is nothing to book.

    The site sends the anchor `#sessions-book-now` from every event card. The
    sessions list below that anchor is the only booking path that the site
    offers. A free event needs no link. A gig with no price still holds the
    link, so the reader can open the sessions list.
    """
    if detail_url is None or price == FREE_PRICE:
        return None
    return f"{detail_url}{BOOKING_ANCHOR}"


def parse_performance(
    row: dict[str, Any],
    day: str,
    venues: dict[str, sources.Venue],
    events: dict[str, EventFacts],
) -> sources.Gig | None:
    """Return one gig for one diary row, or None for a row that is not a gig.

    A cancelled performance is not a gig. The `On Demand` group answers for
    every day, so a row with `onDemand` true is not a gig of this day. A row
    of another day is dropped as well, so the day of a gig is exact.

    The `status` of the performance holds the ticket state of that day, for
    example `Available`, `SellingFast`, `AlmostGone`, or `SoldOut`. A free
    event holds no booking link and no ticket to sell out, so the gig keeps
    the state only with a booking link, and a free gig reads Free.
    """
    performance = row.get("performance")
    if not isinstance(performance, dict):
        return None
    if performance.get("cancelled") or performance.get("onDemand"):
        return None
    stamp = performance.get("date")
    if not isinstance(stamp, str) or not DAY_RE.fullmatch(stamp[:10]):
        return None
    if stamp[:10] != day:
        return None
    event_id = row.get("id")
    facts = events.get(event_id) if isinstance(event_id, str) else None
    venue = venues.get(name_key(row.get("venue")))
    image = row.get("image")
    image_url = image.get("imageUrl") if isinstance(image, dict) else None
    detail_url = event_page_url(row.get("route"))
    price = facts.price if facts is not None else None
    ticket_url = ticket_link(detail_url, price)
    return sources.Gig(
        artist=text(row.get("name")),
        detail_url=detail_url,
        date=stamp[:10],
        venue=text(row.get("venue")),
        venue_url=venue.venue_url if venue is not None else None,
        genres=list(facts.genres) if facts is not None else [],
        categories=accessibility_names(row.get("accessibilities")),
        price=price,
        # A gig with no booking link has no ticket, so the site state of
        # an unticketed free event does not belong to the gig.
        status=text(performance.get("status")) if ticket_url else None,
        ticket_url=ticket_url,
        image_url=text(image_url),
        time=local_time_text(stamp),
    )


def parse_diary(
    payload: dict[str, Any],
    day: str,
    venues: dict[str, sources.Venue],
    events: dict[str, EventFacts],
) -> list[sources.Gig]:
    """Return the gigs of one day payload."""
    groups = payload.get("eventGroups")
    if not isinstance(groups, list):
        return []
    gigs = []
    for group in groups:
        # The last group of a day can hold a name and no `events` key.
        rows = group.get("events") if isinstance(group, dict) else None
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            gig = parse_performance(row, day, venues, events)
            if gig is not None:
                gigs.append(gig)
    return gigs


def address_text(properties: dict[str, Any]) -> str | None:
    """Return the street address of one venue row as one line."""
    parts = [
        properties.get("address1"),
        properties.get("address2"),
        properties.get("town"),
        properties.get("postcode"),
    ]
    items = []
    for part in parts:
        cleaned = text(part)
        if cleaned is not None:
            items.append(cleaned)
    return ", ".join(items) or None


def parse_venue(row: dict[str, Any]) -> sources.Venue | None:
    """Return one venue of a delivery API row, or None for a damaged row."""
    name = text(row.get("name"))
    route = row.get("route")
    path = route.get("path") if isinstance(route, dict) else None
    if name is None or not isinstance(path, str):
        return None
    if VENUE_PATH_RE.search(path) is None:
        return None
    properties = row.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    return sources.Venue(
        venue_url=SITE_URL + path,
        name=name,
        address=address_text(properties),
        latitude=number(properties.get("latitude")),
        longitude=number(properties.get("longitude")),
    )



class FringeSource:
    """Read the data from the Melbourne Fringe API through one connection pool.

    This class is the `sources.GigSource` implementation for the live site.
    """

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        # The venue list and the event list are the same for every request,
        # so the source reads each one time and holds it. None means the read
        # has not run.
        self._venues: dict[str, sources.Venue] | None = None
        self._facts: dict[str, EventFacts] | None = None
        self._venue_lock = asyncio.Lock()
        self._facts_lock = asyncio.Lock()

    async def __aenter__(self) -> "FringeSource":
        self._client = httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers=HEADERS,
            follow_redirects=True,
            transport=self._transport,
        )
        return self

    async def __aexit__(self, *_args: object) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await client.aclose()

    async def _send(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Return the answer of one read, with a retry on a 5xx answer."""
        client = self._client
        if client is None:
            raise RuntimeError("use FringeSource as an async context manager")
        status = 0
        for attempt in range(1, SERVER_ERROR_ATTEMPTS + 1):
            try:
                response = await client.request(
                    method, url, params=params, json=body, headers=headers
                )
            except httpx.HTTPError as error:
                LOGGER.warning("cannot reach melbournefringe.com.au: %s", error)
                raise sources.SourceError(
                    f"cannot reach melbournefringe.com.au: {error}"
                ) from error
            if response.status_code == 200:
                return response
            status = response.status_code
            LOGGER.warning(
                "melbournefringe.com.au answered status %s for %s (attempt %d)",
                status,
                url,
                attempt,
            )
            # A 5xx is a gateway or origin fault. Retry the same read.
            if status < 500 or attempt >= SERVER_ERROR_ATTEMPTS:
                break
            await asyncio.sleep(RETRY_DELAY_SECONDS * attempt)
        raise sources.SourceError(
            f"melbournefringe.com.au answered status {status}"
        )

    async def _json(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Return the JSON of one read."""
        response = await self._send(
            method, url, params=params, body=body, headers=headers
        )
        try:
            return response.json()
        except ValueError as error:
            raise sources.SourceError(f"{url} answered damaged JSON") from error

    async def _result(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return the `result` object of one what's-on API read."""
        payload = await self._json(method, url, params=params, body=body)
        if not isinstance(payload, dict) or payload.get("isError"):
            raise sources.SourceError(f"{url} answered an error")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise sources.SourceError(f"{url} answered no result")
        return result

    async def _diary(self, day: str) -> dict[str, Any]:
        """Return the day payload of one day."""
        return await self._result("POST", DIARY_URL, body={"day": day})

    async def _content_rows(
        self,
        content_type: str,
        page_size: int,
        fields: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return every row of one delivery API content type."""
        rows: list[dict[str, Any]] = []
        skip = 0
        while True:
            params = {
                "filter": f"contentType:{content_type}",
                "take": str(page_size),
                "skip": str(skip),
            }
            if fields is not None:
                params["fields"] = fields
            payload = await self._json(
                "GET",
                CONTENT_URL,
                params=params,
                headers={"Api-Key": CONTENT_API_KEY},
            )
            if not isinstance(payload, dict):
                raise sources.SourceError("the content list answered no object")
            items = payload.get("items")
            total = payload.get("total")
            if not isinstance(items, list):
                raise sources.SourceError("the content list answered no items")
            rows.extend(row for row in items if isinstance(row, dict))
            skip += len(items)
            if not items or not isinstance(total, int) or skip >= total:
                return rows

    async def _event_index(self) -> dict[str, EventFacts]:
        """Return the genre and the price of every event, keyed by event id.

        A diary row holds neither, so the gig list reads the event list. The
        read covers the whole festival, so the source makes it one time for
        the process. A fault leaves the genre and the price empty and does
        not stop the gig list.
        """
        if self._facts is not None:
            return self._facts
        async with self._facts_lock:
            if self._facts is not None:
                return self._facts
            try:
                rows = await self._content_rows(
                    EVENT_CONTENT_TYPE, EVENT_PAGE_SIZE, EVENT_FIELDS
                )
            except sources.SourceError as error:
                LOGGER.warning(
                    "the event list failed, so a gig holds no genre or price: %s",
                    error,
                )
                return {}
            index = {}
            for row in rows:
                event_id = row.get("id")
                if isinstance(event_id, str):
                    index[event_id] = parse_event_facts(row)
            self._facts = index
            return index

    async def _venue_index(self) -> dict[str, sources.Venue]:
        """Return the venue list by page address.

        The list is the same for every request, so one read answers the
        process. A fault answers an empty list and does not hold the fault,
        because a later request can succeed.
        """
        if self._venues is not None:
            return self._venues
        async with self._venue_lock:
            if self._venues is not None:
                return self._venues
            try:
                rows = await self._content_rows(
                    VENUE_CONTENT_TYPE, VENUE_PAGE_SIZE
                )
            except sources.SourceError as error:
                LOGGER.warning(
                    "the venue list failed, so a venue has no address: %s", error
                )
                return {}
            index = {}
            for row in rows:
                venue = parse_venue(row)
                if venue is not None:
                    index[venue.venue_url] = venue
            self._venues = index
            return index

    async def page(self, start_date: str, end_date: str) -> str:
        """Return the raw day payloads of a range as one JSON text.

        The site serves one page for each day, so the raw page text of a
        range is the array of those day payloads.
        """
        days = days_of_range(start_date, end_date)
        payloads = await gather_limited(
            [lambda day=day: self._diary(day) for day in days],
            REQUEST_CONCURRENCY,
        )
        return json.dumps(payloads)

    async def gigs(self, start_date: str, end_date: str) -> list[sources.Gig]:
        """Return the gigs of a date range. One gig is one performance."""
        days = days_of_range(start_date, end_date)
        payloads = await gather_limited(
            [lambda day=day: self._diary(day) for day in days],
            REQUEST_CONCURRENCY,
        )
        events = await self._event_index()
        venues = {
            name_key(venue.name): venue
            for venue in (await self._venue_index()).values()
            if venue.name
        }
        gigs = []
        for day, payload in zip(days, payloads):
            gigs.extend(parse_diary(payload, day, venues, events))
        return sorted(
            gigs,
            key=lambda gig: (gig.date or "", time_order(gig.time), gig.artist or ""),
        )

    async def gig_detail(self, detail_url: str) -> sources.GigDetail | None:
        """Return the detail of one event.

        The site has no page for one performance, and the diary read already
        holds the time. The time stays empty, so the server keeps the time of
        the gig list.
        """
        path = site_path(detail_url)
        if path is None or EVENT_PATH_RE.search(path) is None:
            return None
        return sources.GigDetail(detail_url=detail_url)

    async def venue(self, venue_url: str) -> sources.Venue:
        """Return the address data of one venue."""
        path = site_path(venue_url)
        if path is None or VENUE_PATH_RE.search(path) is None:
            raise sources.SourceUrlError(VENUE_URL_HINT)
        venue = (await self._venue_index()).get(venue_url)
        if venue is None:
            # The venue list does not hold the url. The answer carries the url
            # alone, so the server can still answer the request.
            return sources.Venue(venue_url=venue_url)
        # The server adds the map links to the answer, so the source gives a
        # copy and keeps the held row clean.
        return venue.model_copy()
