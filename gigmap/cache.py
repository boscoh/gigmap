"""Save and read the JSON data files through one small abstraction.

One `CacheEntry` describes a cache kind as data: the subdirectory, the value
type, the file name, the freshness rule, the validity rule, the retention
rule. One `JsonCache` does the shared work: build the
path, validate on read, write atomically, and evict the old files.

`stores.py` declares one `CacheEntry` for each data kind.
"""

import os
import re
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, Protocol, TypeVar

from pydantic import TypeAdapter

M = TypeVar("M")

MIN_AGE_SECONDS = 600.0
"""A younger file can be in use by a request. The cleanup keeps it."""


class Retention(Protocol):
    """Select the files that a cleanup can remove."""

    # pi-lens-ignore: no-ellipsis-body
    def select(
        self, path: Path, now: float, days: int | None, today: str | None
    ) -> bool:
        """Return True when the file is a removal candidate."""
        ...


@dataclass(frozen=True)
class OlderThan:
    """Select a file when its age is at least `days`."""

    def select(
        self, path: Path, now: float, days: int | None, today: str | None
    ) -> bool:
        if days is None:
            return False
        try:
            age = now - path.stat().st_mtime
        except OSError:
            return False
        return age >= days * 86400


@dataclass(frozen=True)
class PastRange:
    """Select a file when its name holds a date range that ended before today."""

    name_re: re.Pattern[str]
    end_group: str = "end"

    def select(
        self, path: Path, now: float, days: int | None, today: str | None
    ) -> bool:
        match = self.name_re.fullmatch(path.name)
        if match is None or today is None:
            return False
        return match.group(self.end_group) < today


def default_filename(key: str) -> str:
    """Return the file name of one key."""
    return f"{key}.json"


@dataclass(frozen=True)
class CacheEntry(Generic[M]):
    """The rules of one cache kind."""

    subdir: str
    adapter: TypeAdapter[M]
    filename: Callable[[str], str] = default_filename
    max_age: float | None = None
    valid: Callable[[M], bool] | None = None
    retention: Retention | None = None


@dataclass
class EvictReport:
    """The count and the size of the files that a cleanup removed."""

    removed: int = 0
    size: int = 0
    skipped: int = 0


class JsonCache(Generic[M]):
    """One JSON file cache on disk."""

    def __init__(self, root: Callable[[], Path], entry: CacheEntry[M]) -> None:
        self._root = root
        self.entry = entry

    def directory(self) -> Path:
        return self._root() / self.entry.subdir

    def path(self, key: str) -> Path:
        return self.directory() / self.entry.filename(key)

    def load(self, key: str, *, now: float | None = None) -> M | None:
        """Read one fresh value. None when absent, damaged, or stale."""
        path = self.path(key)
        try:
            value = self.entry.adapter.validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if self._is_fresh(value, path, time.time() if now is None else now):
            return value
        return None

    def save(self, key: str, value: M) -> None:
        """Write one value through a temporary file."""
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self.entry.adapter.dump_json(value, indent=2)
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
        """Load one value, or call `origin`, and store it when valid.

        A value that fails the validity rule is returned but not stored.
        """
        if not refresh:
            hit = self.load(key)
            if hit is not None:
                return hit
        value = await origin()
        if value is not None:
            if self.entry.valid is None or self.entry.valid(value):
                self.save(key, value)
        return value

    def evict(
        self,
        now: float,
        *,
        days: int | None = None,
        today: str | None = None,
        dry_run: bool = False,
    ) -> EvictReport:
        """Remove the old files. Keep a young file for a request in flight."""
        report = EvictReport()
        retention = self.entry.retention
        if retention is None:
            return report
        for path in sorted(self.directory().glob("*.json")):
            if not retention.select(path, now, days, today):
                continue
            if not self._removable(path, now):
                report.skipped += 1
                continue
            try:
                size = path.stat().st_size
                if not dry_run:
                    path.unlink(missing_ok=True)
            except OSError:
                continue
            report.removed += 1
            report.size += size
        return report

    def _is_fresh(self, value: M, path: Path, now: float) -> bool:
        valid = self.entry.valid
        if valid is not None and not valid(value):
            return False
        if self.entry.max_age is None:
            return True
        try:
            return now - path.stat().st_mtime < self.entry.max_age
        except OSError:
            return False

    @staticmethod
    def _removable(path: Path, now: float) -> bool:
        try:
            return now - path.stat().st_mtime >= MIN_AGE_SECONDS
        except OSError:
            return False
