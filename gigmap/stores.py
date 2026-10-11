"""The disk stores of the app and the cached source.

One `CacheEntry` describes each cache kind. `CachedSource` builds the three
stores from the data directory, so the caller does not pass them.
"""

from collections.abc import Callable
from pathlib import Path

from pydantic import TypeAdapter

from gigmap import cache, geocode, sources


def has_coordinates(venue: sources.Venue) -> bool:
    """Return True when the venue holds a geocode."""
    return venue.latitude is not None and venue.longitude is not None


DAYS = cache.CacheEntry(
    subdir="days",
    adapter=TypeAdapter(list[sources.Gig]),
    filename=lambda key: f"gigmap_{key}.json",
    max_age=cache.MIN_AGE_SECONDS,
)

GIGS = cache.CacheEntry(
    subdir="gigs",
    adapter=TypeAdapter(sources.GigDetail),
)

VENUES = cache.CacheEntry(
    subdir="venues",
    adapter=TypeAdapter(sources.Venue),
    valid=has_coordinates,
)


class CachedSource:
    """Cache the reads of one source on disk. Implement `GigSource`."""

    def __init__(
        self,
        source: sources.GigSource,
        data_dir: Callable[[], Path],
        refresh: bool = False,
    ) -> None:
        self._source = source
        self._refresh = refresh
        self._days = cache.JsonCache(data_dir, DAYS)
        self._gigs = cache.JsonCache(data_dir, GIGS)
        self._venues = cache.JsonCache(data_dir, VENUES)

    async def page(self, start_date: str, end_date: str) -> str:
        return await self._source.page(start_date, end_date)

    async def gigs(self, start_date: str, end_date: str) -> list[sources.Gig]:
        gigs = await self._days.fetch(
            f"{start_date}_{end_date}",
            lambda: self._source.gigs(start_date, end_date),
            refresh=self._refresh,
        )
        if gigs is None:
            raise sources.SourceError("the source gave no gig list")
        return gigs

    async def gig_detail(self, detail_url: str) -> sources.GigDetail | None:
        return await self._gigs.fetch(
            sources.url_key(detail_url),
            lambda: self._source.gig_detail(detail_url),
            refresh=self._refresh,
        )

    async def venue(self, venue_url: str) -> sources.Venue:
        """Read the venue, add the geocode, and cache it when complete."""

        async def read() -> sources.Venue:
            return await geocode.enrich_venue(await self._source.venue(venue_url))

        venue = await self._venues.fetch(sources.url_key(venue_url), read)
        if venue is None:
            raise sources.SourceError("the source gave no venue")
        return venue
