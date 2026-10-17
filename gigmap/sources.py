"""The data interface between the server and a gig source.

The server holds this protocol only. It does not know the site. `beat.py`
holds the beat.com.au implementation. `stores.py` wraps a source with the
disk caches. A test uses a fake implementation.

The data classes below are the return types of the protocol. `sources.py`
declares them, so the server and every source agree on one shape.
"""

from enum import Enum
from typing import Protocol
from urllib.parse import urlparse

from pydantic import BaseModel, Field


class SourceInfo(BaseModel):
    """The app details of one source.

    The app serves one source, so the source carries the page title and
    the attribution that the app shows.
    """

    title: str
    name: str
    home_url: str


class SourceError(Exception):
    """The source cannot answer a request."""


class SourceUrlError(SourceError):
    """The source does not accept the url."""


class TicketGroup(str, Enum):
    """The portable urgency of one ticket.

    A source owns its `state` words and its `label` text. The group is the
    small set that every source maps into, so a consumer can style or filter
    a ticket without knowledge of the site.
    """

    ON_SALE = "on_sale"
    LIMITED = "limited"
    UNAVAILABLE = "unavailable"
    FREE = "free"
    UNKNOWN = "unknown"


class Ticket(BaseModel):
    """The ticket data of one gig, as its source reports it.

    The `state` field holds the source word, for example `SellingFast`. A
    source owns its set of words, so a second site can use another set. The
    `label` field holds the text that the source shows for that state, for
    example `Selling fast`. The `group` field holds the shared value that a
    consumer uses for style and for filter.

    The `url` field is None when the source offers no booking path, for
    example a free Fringe event. The `price` field holds the display text.
    The `price_min` and `price_max` fields hold the numbers for a sort, and
    are None when the text holds no number.
    """

    state: str | None = None
    label: str
    group: TicketGroup = TicketGroup.UNKNOWN
    price: str | None = None
    price_min: float | None = None
    price_max: float | None = None
    url: str | None = None


class Gig(BaseModel):
    """One gig from a gig guide page.

    A scalar field is None when the site does not show the value. A list
    field is empty when the site does not show the value. The `time` field
    comes from the gig detail page, not from the gig guide page.
    The `ticket` field holds the ticket data of one performance. It is None
    when the site reports no ticket, no price, and no state. A source owns
    the `state` words and the `label` text of its ticket; the `group` field
    is the shared value of every source.
    """

    artist: str | None = None
    detail_url: str | None = None
    date: str | None = None
    end_date: str | None = None
    venue: str | None = None
    venue_url: str | None = None
    genres: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    ticket: Ticket | None = None
    image_url: str | None = None
    image_alt: str | None = None
    time: str | None = None


class GigDetail(BaseModel):
    """The detail of one gig. That is the start time."""

    detail_url: str
    time: str | None = None


class Venue(BaseModel):
    """The address data of one venue, with the geocode and map data.

    The `latitude`, `longitude`, `display_name`, `map_embed_url`, and
    `map_url` fields are None until the server looks the venue up.
    """

    venue_url: str
    name: str | None = None
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    display_name: str | None = None
    map_embed_url: str | None = None
    map_url: str | None = None


class GigSource(Protocol):
    """The data that the server reads from a gig source."""

    # pi-lens-ignore: no-ellipsis-body
    async def page(self, start_date: str, end_date: str) -> str:
        """Return the raw page of a date range."""
        ...

    # pi-lens-ignore: no-ellipsis-body
    async def gigs(self, start_date: str, end_date: str) -> list[Gig]:
        """Return the gigs of a date range."""
        ...

    # pi-lens-ignore: no-ellipsis-body
    async def gig_detail(self, detail_url: str) -> GigDetail | None:
        """Return the detail of one gig. None when the read fails."""
        ...

    # pi-lens-ignore: no-ellipsis-body
    async def venue(self, venue_url: str) -> Venue:
        """Return the address data of one venue."""
        ...


class SourcePool(Protocol):
    """A gig source that owns a connection pool.

    The server opens one pool for each request and closes it at the end.
    `CachedSource` is a wrapper, not a pool, so it does not hold this
    shape.
    """

    # pi-lens-ignore: no-ellipsis-body
    async def __aenter__(self) -> GigSource:
        """Open the pool."""
        ...

    # pi-lens-ignore: no-ellipsis-body
    async def __aexit__(self, *_args: object) -> None:
        """Close the pool."""
        ...


def url_key(url: str) -> str:
    """A short cache name for a page url."""
    name = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    return name or "page"
