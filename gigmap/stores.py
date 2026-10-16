"""The disk stores of the app and the cached source.

`JsonCache` holds the rules of one cache kind: the subdirectory, the value
type, the file name, and the freshness rule. `DataStores` builds the three
JSON caches from one data directory and holds the cleanup of that directory,
so no caller passes a subdirectory. The cleanup removes the files that the
app cannot use again. `CachedSource` reads through those caches.

The cleanup specification:

The data
    The server writes one file for each cache:

    * `data/days/gigmap_<source>_<version>_<start>_<end>.json` - the parsed
      gig list for one date range
    * `data/gigs/<source>_<slug>.json` - the start time of one gig
    * `data/venues/<source>_<slug>.json` - the name, the address, and the
      coordinates of one venue

    `data/gigs/` grows fastest. A request for a date range reads one detail
    page for each gig, and each answer becomes one file.

The namespace

The app serves two sites from one server, and the two sites answer for the
same day range with different gigs. Each site has a path, and every file
name starts with the name of that path. So the answer of one site never
becomes the answer of another site.

A file of the first layout has no source part in the name. The app never
reads it again, and the cleanup removes it on the usual rules.

The version

A day file holds the answer of the source at one time. A change to what a
gig holds, or to how the source builds a gig, makes that answer wrong, and
the app must not serve it. `DAYS_VERSION` names the shape, and every day
file carries it. Raise the number with such a change. The app then ignores
each file of the older number, and the cleanup removes it.

The retention rules
    * A day file: remove when the range end date is before today. The
      calendar blocks a past day, so the app cannot ask for the file again.
      The rule uses the date in the file name. A file with an unknown name
      stays.
    * A gig file: remove when the file is older than `--gig-days` (30). A
      new lookup costs one request.
    * A venue file: keep. The Nominatim policy needs the cache. The server
      must not ask again for a venue with coordinates.

The growth
    * A day file is about 9 KB. One year is 365 files, about 3 MB.
    * A gig file is about 4 KB. One year is 10,000 gigs, about 40 MB.
    * A venue file is about 4 KB. One year is 500 venues, about 2 MB.

The command
    The command is `uv run gigmap clean`. `gigmap.server` holds the command
    and the schedule.

    * `--data-dir` - `./data` - directory for the saved files
    * `--gig-days` - `30` - keep a gig time file for this many days
    * `--dry-run` - off - print the plan and remove nothing
    * `--quiet` - off - print nothing

The schedule
    The server runs the cleanup in the FastAPI lifespan. The loop runs one
    time at startup, then each 24 hours. `CLEANUP_INTERVAL_SECONDS` sets the
    period. The loop uses the server's local date. The server is UTC, so the
    app command pins the timezone with `TZ=Australia/Melbourne`.

The tests
    `uv run pytest` runs `tests/test_cleanup.py`. The tests cover the date
    rule, the age rule, the dry run, the report, the loop, and the lifespan.

Later work
    The gig file holds no event date:

        { "time": "7:30pm", "detail_url": "https://beat.com.au/abby-hayes/" }

    A `date` field makes the gig rule exact. The server has the gig in hand
    at `gig_time`, so the change is small. The rule then becomes: remove
    when the event date is before today.
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

# The shape of a day file. Raise this number when the source changes what
# a gig holds, so the app never serves an answer of the older code. The
# app then ignores each day file of the older number, and the cleanup
# removes it on the usual rules.
DAYS_VERSION = "v2"

# The day files are named gigmap_<source>_<version>_<start>_<end>.json.
# A file of an older layout has no source part or no version part, and the
# cleanup still removes it.
DAY_FILE_RE = re.compile(
    r"gigmap_(?:(?P<source>[a-z0-9]+)_)?(?:(?P<version>v[0-9]+)_)?"
    r"(?P<start>\d{4}-\d{2}-\d{2})_(?P<end>\d{4}-\d{2}-\d{2})\.json"
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
    ) -> M | None:
        """Load one value, or call `origin`, and store it."""
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

    def __init__(self, data_dir: Callable[[], Path], namespace: str = "") -> None:
        # Two sources can answer for the same day range, and the two answers
        # do not agree. The namespace keeps the files of one source apart
        # from the files of another source.
        prefix = f"{namespace}_" if namespace else ""
        self.days = JsonCache(
            data_dir,
            DAYS_SUBDIR,
            TypeAdapter(list[sources.Gig]),
            filename=lambda key: (
                f"gigmap_{prefix}{DAYS_VERSION}_{key}.json"
            ),
            max_age=DAYS_CACHE_SECONDS,
        )
        self.gigs = JsonCache(
            data_dir,
            GIGS_SUBDIR,
            TypeAdapter(sources.GigDetail),
            filename=lambda key: f"{prefix}{key}.json",
        )
        self.venues = JsonCache(
            data_dir,
            VENUES_SUBDIR,
            TypeAdapter(sources.Venue),
            filename=lambda key: f"{prefix}{key}.json",
        )

    def clean(self, now: float, gig_days: int, dry_run: bool) -> CleanReport:
        """Remove the day files of a past range and the old gig time files.

        The algorithm:

            today = date of now
            for path in data/days/gigmap_*.json:
                end = the date from the file name
                if end < today and age(path) >= DAYS_CACHE_SECONDS:
                    remove(path)

            cutoff = now - gig_days * 86400
            for path in data/gigs/*.json:
                if mtime(path) < cutoff and age(path) >= DAYS_CACHE_SECONDS:
                    remove(path)

            # data/venues/*.json is never touched

        The safety rules:

        1. Keep each file younger than `DAYS_CACHE_SECONDS` (3 hours). A
           request can hold the file.
        2. Match the name pattern only.
        3. Do not remove a directory.
        4. Do not remove a venue file.
        5. A failed removal must not stop the run.
        6. `--dry-run` removes nothing.

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
    ) -> None:
        self._source = source
        self._stores = stores

    async def page(self, start_date: str, end_date: str) -> str:
        return await self._source.page(start_date, end_date)

    async def gigs(self, start_date: str, end_date: str) -> list[sources.Gig]:
        gigs = await self._stores.days.fetch(
            f"{start_date}_{end_date}",
            lambda: self._source.gigs(start_date, end_date),
        )
        if gigs is None:
            raise sources.SourceError("the source gave no gig list")
        return gigs

    async def gig_detail(self, detail_url: str) -> sources.GigDetail | None:
        return await self._stores.gigs.fetch(
            sources.url_key(detail_url),
            lambda: self._source.gig_detail(detail_url),
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
