"""The disk stores of the app and the cached source.

`CachedSource` builds the three stores from the data directory, so the
caller does not pass them.
"""

from collections.abc import Callable
from pathlib import Path

from pydantic import TypeAdapter

from gigmap import cache, geocode, sources


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
        self._days = cache.JsonCache(
            data_dir,
            "days",
            TypeAdapter(list[sources.Gig]),
            filename=lambda key: f"gigmap_{key}.json",
            max_age=cache.MIN_AGE_SECONDS,
        )
        self._gigs = cache.JsonCache(data_dir, "gigs", TypeAdapter(sources.GigDetail))
        self._venues = cache.JsonCache(data_dir, "venues", TypeAdapter(sources.Venue))

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
        key = sources.url_key(venue_url)
        hit = self._venues.load(key)
        if hit is not None:
            return hit
        venue = await geocode.enrich_venue(await self._source.venue(venue_url))
        if venue.latitude is not None and venue.longitude is not None:
            self._venues.save(key, venue)
        return venue
