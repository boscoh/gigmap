"""Read the beat.com.au gig guide.

This module holds every rule about the site: the URLs, the browser client,
page markup selectors, the date and time formats, and the reads. It is the
only module that reads beat.com.au. The server holds the cache and the HTTP
routes.
"""

import asyncio
import logging
import random
import re
from datetime import datetime

from bs4 import BeautifulSoup, Tag
from curl_cffi import requests as curl_requests

from gigmap import sources
from gigmap.logger import log_request

BEAT_URL = "https://beat.com.au/gig-guide/"

# The app serves this one site, so the source carries the app details.
SOURCE = sources.SourceInfo(
    title="Melb Gig Map",
    name="beat.com.au",
    home_url=BEAT_URL,
)

VENUE_URL_HINT = "venue_url must have the form https://beat.com.au/directory/<name>/"

SPACE_RE = re.compile(r"\s+")
VENUE_URL_RE = re.compile(r"https://beat\.com\.au/directory/(?P<slug>[a-z0-9-]+)/?")
DETAIL_URL_RE = re.compile(
    r"https://beat\.com\.au/(?!directory/)(?P<slug>[a-z0-9-]+)/?"
)
# The gig detail page shows the start time, for example "6.30pm".
GIG_TIME_RE = re.compile(
    r"(?P<hour>\d{1,2})(?:[.:](?P<minute>\d{2}))?\s*(?P<meridiem>am|pm)",
    re.IGNORECASE,
)

# beat.com.au (Cloudflare) reads the TLS fingerprint. curl_cffi
# impersonates the browser, so the client does not send a user agent.
# A challenge can answer one fingerprint and pass another. A read then
# moves to the next target and keeps it for the later reads.
IMPERSONATE_TARGETS = ("chrome", "safari_ios", "firefox")

# A real browser sends one fingerprint, so the target does not change
# for each read. It changes only when the process starts or when a
# challenge rejects the current target.
_current_target = random.choice(IMPERSONATE_TARGETS)


def target_order(start: str) -> tuple[str, ...]:
    """Return every target, with `start` first."""
    index = IMPERSONATE_TARGETS.index(start)
    return IMPERSONATE_TARGETS[index:] + IMPERSONATE_TARGETS[:index]


HEADERS = {
    "Accept-Language": "en-AU,en;q=0.9",
}

# Cloudflare sits in front of the site. A 5xx is a gateway or origin fault.
# The fault is short in most cases, so the app retries the same read.
SERVER_ERROR_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 1.0

LOGGER = logging.getLogger(__name__)


def parse_site_date(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = SPACE_RE.sub(" ", text).strip().lstrip("-").strip()
    if not cleaned:
        return None
    try:
        return datetime.strptime(cleaned.title(), "%a %d %b %Y").date().isoformat()
    except ValueError:
        return None


def normalise_time(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = SPACE_RE.sub(" ", text).strip()
    match = GIG_TIME_RE.fullmatch(cleaned)
    if match is None:
        return cleaned or None
    hour = match.group("hour")
    minute = match.group("minute") or "00"
    meridiem = match.group("meridiem").lower()
    return f"{hour}:{minute}{meridiem}"


def attribute(node: object, name: str) -> str | None:
    """Return one string attribute, or None for a missing value."""
    value = node.get(name) if isinstance(node, Tag) else None
    return value if isinstance(value, str) else None


def parse_gig_detail(html: str, detail_url: str) -> sources.GigDetail:
    soup = BeautifulSoup(html, "lxml")
    time_text = None
    for item in soup.select(".article-meta-item"):
        headline = item.select_one(".article-meta-headline")
        if headline is None or headline.get_text(strip=True).lower() != "time":
            continue
        content = item.select_one(".article-meta-content")
        if content is not None:
            time_text = content.get_text(" ", strip=True)
        break
    return sources.GigDetail(detail_url=detail_url, time=normalise_time(time_text))


FREE_PRICE = "Free"
# The category that beat uses for an event with no ticket price.
FREE_CATEGORY = "Free Events"
PRICE_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def parse_ticket(
    price: str | None,
    url: str | None,
    free: bool = False,
) -> sources.Ticket | None:
    """Return the ticket of one card, or None when the card holds no ticket data.

    The beat card names no ticket state. The read reports the price and the
    link only, so the state stays None and the group is On sale. A card with
    no price and no link holds no ticket.

    A card in the `Free Events` category holds no price. The caller passes
    `free` true, and the ticket reads Free. The link stays when the card
    offers one, because a free event can still have a registration page.
    The price text can hold a range, so the low and high values come from
    the numbers in the text.
    """
    if price == FREE_PRICE or (price is None and free):
        return sources.Ticket(
            label=FREE_PRICE,
            group=sources.TicketGroup.FREE,
            price=FREE_PRICE,
            price_min=0.0,
            price_max=0.0,
            url=url,
        )
    if price is None and url is None:
        return None
    numbers = [float(value) for value in PRICE_NUMBER_RE.findall(price or "")]
    low = min(numbers) if numbers else None
    high = max(numbers) if numbers else None
    return sources.Ticket(
        label=price or "Tickets",
        group=sources.TicketGroup.ON_SALE,
        price=price,
        price_min=low,
        price_max=high,
        url=url,
    )


def parse_gigs(html: str) -> list[sources.Gig]:
    soup = BeautifulSoup(html, "lxml")
    gigs = []
    for card in soup.select(".events_wrapper > .gig-card"):
        title = card.select_one(".gig-title a")
        image = card.select_one(".gig-image img")
        if title is None or image is None:
            continue
        date = card.select_one(".gig-date")
        end_date = card.select_one(".gig-end-date")
        location = card.select_one(".gig-location a")
        price = card.select_one(".gig-price")
        ticket = card.select_one(".gig-ticket a")
        categories = [
            badge.get_text(strip=True) for badge in card.select(".gig-category .badge")
        ]
        price_text = price.get_text(" ", strip=True) if price else None
        ticket_url = attribute(ticket, "href") if ticket else None
        free = price_text is None and FREE_CATEGORY in categories
        gigs.append(
            sources.Gig(
                artist=title.get_text(strip=True),
                detail_url=attribute(title, "href"),
                date=parse_site_date(date.get_text()) if date else None,
                end_date=parse_site_date(end_date.get_text()) if end_date else None,
                venue=location.get_text(strip=True) if location else None,
                venue_url=attribute(location, "href") if location else None,
                genres=[
                    badge.get_text(strip=True)
                    for badge in card.select(".gig-genre .badge")
                ],
                categories=categories,
                ticket=parse_ticket(price_text, ticket_url, free),
                image_url=attribute(image, "src"),
                image_alt=attribute(image, "alt"),
            )
        )
    return gigs


def parse_venue(html: str, venue_url: str) -> sources.Venue:
    soup = BeautifulSoup(html, "lxml")
    heading = soup.select_one("h1")
    items = soup.select(".article-content .article-meta-item")
    address = None
    if items:
        text = items[0].get_text(" ", strip=True)
        address = SPACE_RE.sub(" ", text).strip() or None
    return sources.Venue(
        venue_url=venue_url,
        name=heading.get_text(strip=True) if heading else None,
        address=address,
    )


class BeatSource:
    """Read the data from beat.com.au through one connection pool.

    This class is the `sources.GigSource` implementation for the live site.
    """

    def __init__(self) -> None:
        self._session: curl_requests.AsyncSession | None = None

    async def __aenter__(self) -> "BeatSource":
        self._session = curl_requests.AsyncSession(
            impersonate=_current_target,
            allow_redirects=True,
            timeout=30.0,
        )
        return self

    async def __aexit__(self, *_args: object) -> None:
        session = self._session
        self._session = None
        if session is not None:
            await session.close()

    async def _html(self, url: str, params: dict[str, str] | None = None) -> str:
        """Return the page text, with a retry on a 5xx answer."""
        global _current_target
        session = self._session
        if session is None:
            raise RuntimeError("use BeatSource as an async context manager")
        status = 0
        for target in target_order(_current_target):
            attempts = 0
            while True:
                attempts += 1
                try:
                    response = await session.get(
                        url, params=params, headers=HEADERS, impersonate=target
                    )
                except curl_requests.RequestsError as error:
                    log_request(LOGGER.warning, "GET", url, error=error)
                    raise sources.SourceError(
                        f"cannot reach beat.com.au: {error}"
                    ) from error
                status = response.status_code
                if status == 200:
                    log_request(
                        LOGGER.info,
                        "GET",
                        response.url or url,
                        status=status,
                        reason=response.reason,
                        attempt=attempts,
                    )
                    # Keep the target that passed, so the later reads start there.
                    _current_target = target
                    return response.text
                mitigated = response.headers.get("cf-mitigated")
                log_request(
                    LOGGER.warning,
                    "GET",
                    response.url or url,
                    status=status,
                    reason=response.reason,
                    cf_mitigated=mitigated,
                    cf_ray=response.headers.get("cf-ray"),
                    attempt=attempts,
                )
                # A challenge belongs to one fingerprint. Try the next target.
                if mitigated is not None:
                    break
                # A 5xx is a gateway or origin fault. Retry the same target.
                if status < 500 or attempts >= SERVER_ERROR_ATTEMPTS:
                    raise sources.SourceError(f"beat.com.au answered status {status}")
                await asyncio.sleep(RETRY_DELAY_SECONDS * attempts)
        raise sources.SourceError(f"beat.com.au answered status {status}")

    async def page(self, start_date: str, end_date: str) -> str:
        """Return the gig guide page of a date range."""
        params = {
            "search_term": "",
            "venue_search": "",
            "genre": "",
            "city": "",
            "category": "",
            "start_date": start_date,
            "end_date": end_date,
        }
        return await self._html(BEAT_URL, params=params)

    async def gigs(self, start_date: str, end_date: str) -> list[sources.Gig]:
        """Return the gigs of a date range."""
        return parse_gigs(await self.page(start_date, end_date))

    async def gig_detail(self, detail_url: str) -> sources.GigDetail | None:
        """Return the detail of one gig. None when the read fails."""
        if DETAIL_URL_RE.fullmatch(detail_url) is None:
            return None
        try:
            html = await self._html(detail_url)
        except sources.SourceError:
            return None
        return parse_gig_detail(html, detail_url)

    async def venue(self, venue_url: str) -> sources.Venue:
        """Return the address data of one venue."""
        if VENUE_URL_RE.fullmatch(venue_url) is None:
            raise sources.SourceUrlError(VENUE_URL_HINT)
        return parse_venue(await self._html(venue_url), venue_url)
