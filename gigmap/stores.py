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
    * `data/venues/<source>_<slug>.json` - the name, the address, and the
      coordinates of one venue

    An old `data/gigs/` tree is removed by the cleanup. The app writes no
    gig file now, because each source reads the time of its own gigs.

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
    * A venue file: keep. The Nominatim policy needs the cache. The server
      must not ask again for a venue with coordinates.

The growth
    * A day file is about 9 KB. One year is 365 files, about 3 MB.
    * A venue file is about 4 KB. One year is 500 venues, about 2 MB.

The command
    The command is `uv run gigmap clean`. `gigmap.server` holds the command
    and the schedule.

    * `--data-dir` - `./data` - directory for the saved files
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
"""

import os
import re
import shutil
import tempfile
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Generic, NamedTuple, TypeVar

from pydantic import TypeAdapter

from gigmap import geocode, sources

# The day files stay fresh for this time. The cleanup keeps the files
# younger than this value, so both rules must use the same number.
DAYS_CACHE_SECONDS = 3 * 3600

DAYS_SUBDIR = "days"
VENUES_SUBDIR = "venues"

# A directory of an older layout. The cleanup removes the whole tree.
STALE_SUBDIRS = ("gigs",)

# The shape of a day file. Raise this number when the source changes what
# a gig holds, so the app never serves an answer of the older code. The
# app then ignores each day file of the older number, and the cleanup
# removes it on the usual rules.
DAYS_VERSION = "v11"

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
    stale: int
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


def remove_tree(path: Path, dry_run: bool) -> int:
    """Remove one directory tree and return its size. Return 0 on a failure."""
    try:
        size = sum(
            child.stat().st_size for child in path.rglob("*") if child.is_file()
        )
        if not dry_run:
            shutil.rmtree(path, ignore_errors=True)
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

    def _is_fresh(self, path: Path) -> bool:
        if self._max_age is None:
            return True
        try:
            return time.time() - path.stat().st_mtime < self._max_age
        except OSError:
            return False


class DataStores:
    """The two JSON caches of one data directory, and its cleanup."""

    def __init__(self, data_dir: Callable[[], Path], namespace: str = "") -> None:
        # Two sources can answer for the same day range, and the two answers
        # do not agree. The namespace keeps the files of one source apart
        # from the files of another source.
        prefix = f"{namespace}_" if namespace else ""
        self._root = data_dir
        self.days = JsonCache(
            data_dir,
            DAYS_SUBDIR,
            TypeAdapter(list[sources.Gig]),
            filename=lambda key: (
                f"gigmap_{prefix}{DAYS_VERSION}_{key}.json"
            ),
            max_age=DAYS_CACHE_SECONDS,
        )
        self.venues = JsonCache(
            data_dir,
            VENUES_SUBDIR,
            TypeAdapter(sources.Venue),
            filename=lambda key: f"{prefix}{key}.json",
        )

    def clean(self, now: float, dry_run: bool) -> CleanReport:
        """Remove the day files of a past range.

        The algorithm:

            today = date of now
            for path in data/days/gigmap_*.json:
                end = the date from the file name
                if end < today and age(path) >= DAYS_CACHE_SECONDS:
                    remove(path)

            # data/venues/*.json is never touched

            for name in STALE_SUBDIRS:
                remove the whole tree, because no code reads it

        The safety rules:

        1. Keep each file younger than `DAYS_CACHE_SECONDS` (3 hours). A
           request can hold the file.
        2. Match the name pattern only.
        3. A directory goes only when its name is in `STALE_SUBDIRS`.
        4. Do not remove a venue file.
        5. A failed removal must not stop the run.
        6. `--dry-run` removes nothing.

        A venue file stays. The Nominatim policy needs the cache.
        """
        today = datetime.fromtimestamp(now).date().isoformat()
        day_count = 0
        size = 0
        stale = 0
        skipped = 0

        for name in STALE_SUBDIRS:
            path = self._root() / name
            if path.is_dir():
                size += remove_tree(path, dry_run)
                stale += 1

        for path in sorted(self.days.directory().glob("*.json")):
            match = DAY_FILE_RE.fullmatch(path.name)
            if match is None or match.group("end") >= today:
                continue
            if not old_enough(path, now, DAYS_CACHE_SECONDS):
                skipped += 1
                continue
            size += remove_file(path, dry_run)
            day_count += 1

        return CleanReport(days=day_count, stale=stale, size=size, skipped=skipped)

class CachedSource:
    """Cache the reads of one source on disk. Implement `GigSource`."""

    def __init__(
        self,
        source: sources.GigSource,
        stores: DataStores,
    ) -> None:
        self._source = source
        self._stores = stores

    def cached_gigs(self, start_date: str, end_date: str) -> list[sources.Gig] | None:
        """Return the warm day file, or None."""
        return self._stores.days.load(f"{start_date}_{end_date}")

    async def candidates(self, start_date: str, end_date: str) -> list[sources.Gig]:
        return await self._source.candidates(start_date, end_date)

    async def expand(
        self, gig: sources.Gig, start_date: str, end_date: str
    ) -> list[sources.Gig]:
        return await self._source.expand(gig, start_date, end_date)

    def save_gigs(
        self, start_date: str, end_date: str, gigs: list[sources.Gig]
    ) -> None:
        self._stores.days.save(f"{start_date}_{end_date}", gigs)

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
