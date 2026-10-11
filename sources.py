"""The data interface between the server and a gig source.

The server holds this protocol only. It does not know the site. `beat.py`
holds the beat.com.au implementation. `stores.py` wraps a source with the
disk caches. A test uses a fake implementation.

The data classes below are the return types of the protocol. `sources.py`
declares them, so the server and every source agree on one shape.
"""

from typing import Protocol
from urllib.parse import urlparse

from pydantic import BaseModel, Field


class SourceError(Exception):
    """The source cannot answer a request."""


class SourceUrlError(SourceError):
    """The source does not accept the url."""


class Gig(BaseModel):
    """One gig from a gig guide page.

    A scalar field is None when the site does not show the value. A list
    field is empty when the site does not show the value. The `time` field
    comes from the gig detail page, not from the gig guide page.
    """

    artist: str | None = None
    detail_url: str | None = None
    date: str | None = None
    end_date: str | None = None
    venue: str | None = None
    venue_url: str | None = None
    genres: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    price: str | None = None
    ticket_url: str | None = None
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


def url_key(url: str) -> str:
    """A short cache name for a page url."""
    name = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    return name or "page"
