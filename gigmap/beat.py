"""Read the beat.com.au gig guide.

This module holds every rule about the site: the URLs, the user agent, the
page markup selectors, the date and time formats, and the reads. It is the
only module that reads beat.com.au. The server holds the cache and the HTTP
routes.
"""

import re
from datetime import datetime

import httpx
from bs4 import BeautifulSoup, Tag

from gigmap import sources

BEAT_URL = "https://beat.com.au/gig-guide/"
BEAT_ORIGIN = "https://beat.com.au/"

# The app serves this one site, so the source carries the app details.
SOURCE = sources.SourceInfo(
    title="Melb Gig Map",
    name="beat magazine",
    home_url=BEAT_URL,
)

VENUE_URL_HINT = "venue_url must have the form https://beat.com.au/directory/<name>/"

HEAD_RE = re.compile(r"<head[^>]*>", re.IGNORECASE)
BASE_RE = re.compile(r"<base\b", re.IGNORECASE)
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

# beat.com.au (Cloudflare) answers a browser user agent.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-AU,en;q=0.9",
}


def rewrite_for_frame(html: str) -> str:
    """Add a base tag, so the relative links work in the page."""
    if BASE_RE.search(html):
        return html
    base_tag = f'<base href="{BEAT_ORIGIN}">'
    if not HEAD_RE.search(html):
        return base_tag + html
    return HEAD_RE.sub(lambda match: match.group(0) + base_tag, html, count=1)


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
                categories=[
                    badge.get_text(strip=True)
                    for badge in card.select(".gig-category .badge")
                ],
                price=price.get_text(" ", strip=True) if price else None,
                ticket_url=attribute(ticket, "href") if ticket else None,
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
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "BeatSource":
        self._client = httpx.AsyncClient(follow_redirects=True, timeout=30.0)
        return self

    async def __aexit__(self, *_args: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _html(self, url: str, params: dict[str, str] | None = None) -> str:
        client = self._client
        if client is None:
            raise RuntimeError("use BeatSource as an async context manager")
        try:
            response = await client.get(url, params=params, headers=HEADERS)
        except httpx.HTTPError as error:
            raise sources.SourceError(f"cannot reach beat.com.au: {error}") from error
        if response.status_code != 200:
            raise sources.SourceError(
                f"beat.com.au answered status {response.status_code}"
            )
        return response.text

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
        return rewrite_for_frame(await self._html(BEAT_URL, params=params))

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
