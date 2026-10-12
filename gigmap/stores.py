"""The disk stores of the app and the cached source.

`JsonCache` holds the rules of one cache kind: the subdirectory, the value
type, the file name, and the freshness rule. `DataStores` builds the three
JSON caches from one data directory and holds the cleanup of that directory,
so no caller passes a subdirectory. The cleanup removes the files that the
app cannot use again. `CachedSource` reads through those caches.
"""

import os
import re
import tempfile
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Generic, NamedTuple, TypeVar

from pydantic import TypeAdapter

from gigmap import geocode, sources

# The day files stay fresh for this time. The cleanup keeps the files
# younger than this value, so both rules must use the same number.
DAYS_CACHE_SECONDS = 3 * 3600

DAYS_SUBDIR = "days"
GIGS_SUBDIR = "gigs"
VENUES_SUBDIR = "venues"

# The day files are named gigmap_<start>_<end>.json.
DAY_FILE_RE = re.compile(
    r"gigmap_(?P<start>\d{4}-\d{2}-\d{2})_(?P<end>\d{4}-\d{2}-\d{2})\.json"
)

M = TypeVar("M")


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


def default_filename(key: str) -> str:
    """Return the file name of one key."""
    return f"{key}.json"


class JsonCache(Generic[M]):
    """One JSON file cache on disk."""

    def __init__(
        self,
        root: Callable[[], Path],
        subdir: str,
        adapter: TypeAdapter[M],
        *,
        filename: Callable[[str], str] = default_filename,
        max_age: float | None = None,
    ) -> None:
        self._root = root
        self._subdir = subdir
        self._adapter = adapter
        self._filename = filename
        self._max_age = max_age

    def directory(self) -> Path:
        return self._root() / self._subdir

    def path(self, key: str) -> Path:
        return self.directory() / self._filename(key)

    def load(self, key: str) -> M | None:
        """Read one fresh value. None when absent, damaged, or stale."""
        path = self.path(key)
        try:
            value = self._adapter.validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if self._is_fresh(path):
            return value
        return None

    def save(self, key: str, value: M) -> None:
        """Write one value through a temporary file."""
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self._adapter.dump_json(value, indent=2)
        handle, name = tempfile.mkstemp(
            dir=path.parent, prefix=f"{path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(handle, "wb") as out:
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
            os.replace(name, path)
        except BaseException:
            Path(name).unlink(missing_ok=True)
            raise

    async def fetch(
        self,
        key: str,
        origin: Callable[[], Awaitable[M | None]],
        *,
        refresh: bool = False,
    ) -> M | None:
        """Load one value, or call `origin`, and store it."""
        if not refresh:
            hit = self.load(key)
            if hit is not None:
                return hit
        value = await origin()
        if value is not None:
            self.save(key, value)
        return value

    def _is_fresh(self, path: Path) -> bool:
        if self._max_age is None:
            return True
        try:
            return time.time() - path.stat().st_mtime < self._max_age
        except OSError:
            return False


class DataStores:
    """The three JSON caches of one data directory, and its cleanup."""

    def __init__(self, data_dir: Callable[[], Path]) -> None:
        self.days = JsonCache(
            data_dir,
            DAYS_SUBDIR,
            TypeAdapter(list[sources.Gig]),
            filename=lambda key: f"gigmap_{key}.json",
            max_age=DAYS_CACHE_SECONDS,
        )
        self.gigs = JsonCache(data_dir, GIGS_SUBDIR, TypeAdapter(sources.GigDetail))
        self.venues = JsonCache(data_dir, VENUES_SUBDIR, TypeAdapter(sources.Venue))

    def clean(self, now: float, gig_days: int, dry_run: bool) -> CleanReport:
        """Remove the day files of a past range and the old gig time files.

        A venue file stays. The Nominatim policy needs the cache.
        """
        today = datetime.fromtimestamp(now).date().isoformat()
        day_count = 0
        gig_count = 0
        size = 0
        skipped = 0

        for path in sorted(self.days.directory().glob("*.json")):
            match = DAY_FILE_RE.fullmatch(path.name)
            if match is None or match.group("end") >= today:
                continue
            if not old_enough(path, now, DAYS_CACHE_SECONDS):
                skipped += 1
                continue
            size += remove_file(path, dry_run)
            day_count += 1

        for path in sorted(self.gigs.directory().glob("*.json")):
            if not old_enough(path, now, gig_days * 86400):
                continue
            if not old_enough(path, now, DAYS_CACHE_SECONDS):
                skipped += 1
                continue
            size += remove_file(path, dry_run)
            gig_count += 1

        return CleanReport(days=day_count, gigs=gig_count, size=size, skipped=skipped)


class CachedSource:
    """Cache the reads of one source on disk. Implement `GigSource`."""

    def __init__(
        self,
        source: sources.GigSource,
        stores: DataStores,
        refresh: bool = False,
    ) -> None:
        self._source = source
        self._stores = stores
        self._refresh = refresh

    async def page(self, start_date: str, end_date: str) -> str:
        return await self._source.page(start_date, end_date)

    async def gigs(self, start_date: str, end_date: str) -> list[sources.Gig]:
        gigs = await self._stores.days.fetch(
            f"{start_date}_{end_date}",
            lambda: self._source.gigs(start_date, end_date),
            refresh=self._refresh,
        )
        if gigs is None:
            raise sources.SourceError("the source gave no gig list")
        return gigs

    async def gig_detail(self, detail_url: str) -> sources.GigDetail | None:
        return await self._stores.gigs.fetch(
            sources.url_key(detail_url),
            lambda: self._source.gig_detail(detail_url),
            refresh=self._refresh,
        )

    async def venue(self, venue_url: str) -> sources.Venue:
        """Read the venue, add the geocode, and cache it when complete."""
        key = sources.url_key(venue_url)
        hit = self._stores.venues.load(key)
        if hit is not None:
            return hit
        venue = await geocode.enrich_venue(await self._source.venue(venue_url))
        if venue.latitude is not None and venue.longitude is not None:
            self._stores.venues.save(key, venue)
        return venue
