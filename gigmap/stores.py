"""The disk stores of the app and the cached source.

`CachedSource` builds the three stores from the data directory, so the
caller does not pass them. The cleanup removes the files that the
app cannot use again.
"""

import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from pydantic import TypeAdapter

from gigmap import cache, geocode, sources

# The day files stay fresh for this time. The cleanup keeps the files
# younger than this value, so both rules must use the same number.
DAYS_CACHE_SECONDS = 600.0

DAYS_SUBDIR = "days"
GIGS_SUBDIR = "gigs"
VENUES_SUBDIR = "venues"

# The day files are named gigmap_<start>_<end>.json.
DAY_FILE_RE = re.compile(
    r"gigmap_(?P<start>\d{4}-\d{2}-\d{2})_(?P<end>\d{4}-\d{2}-\d{2})\.json"
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
        self._days = cache.JsonCache(
            data_dir,
            DAYS_SUBDIR,
            TypeAdapter(list[sources.Gig]),
            filename=lambda key: f"gigmap_{key}.json",
            max_age=DAYS_CACHE_SECONDS,
        )
        self._gigs = cache.JsonCache(data_dir, GIGS_SUBDIR, TypeAdapter(sources.GigDetail))
        self._venues = cache.JsonCache(data_dir, VENUES_SUBDIR, TypeAdapter(sources.Venue))

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


class CleanReport(NamedTuple):
    """The count of the files that the cleanup removed."""

    days: int
    gigs: int
    size: int
    skipped: int


def old_enough(path: Path, now: float, seconds: float) -> bool:
    """Return True when the file age is at least `seconds`."""
    try:
        return now - path.stat().st_mtime >= seconds
    except OSError:
        return False


def remove_file(path: Path, dry_run: bool) -> int:
    """Remove one file and return its size. Return 0 when the removal fails."""
    try:
        size = path.stat().st_size
        if not dry_run:
            path.unlink(missing_ok=True)
    except OSError:
        return 0
    return size


def clean_data(
    directory: Path, now: float, gig_days: int, dry_run: bool
) -> CleanReport:
    """Remove the day files of a past range and the old gig time files."""
    today = datetime.fromtimestamp(now).date().isoformat()
    day_count = 0
    gig_count = 0
    size = 0
    skipped = 0

    for path in sorted((directory / DAYS_SUBDIR).glob("*.json")):
        match = DAY_FILE_RE.fullmatch(path.name)
        if match is None or match.group("end") >= today:
            continue
        if not old_enough(path, now, DAYS_CACHE_SECONDS):
            skipped += 1
            continue
        size += remove_file(path, dry_run)
        day_count += 1

    for path in sorted((directory / GIGS_SUBDIR).glob("*.json")):
        if not old_enough(path, now, gig_days * 86400):
            continue
        if not old_enough(path, now, DAYS_CACHE_SECONDS):
            skipped += 1
            continue
        size += remove_file(path, dry_run)
        gig_count += 1

    return CleanReport(days=day_count, gigs=gig_count, size=size, skipped=skipped)
