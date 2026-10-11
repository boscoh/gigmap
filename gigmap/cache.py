"""Save and read the JSON data files through one small abstraction.

One `CacheEntry` describes a cache kind as data: the subdirectory, the value
type, the file name, the freshness rule, and the validity rule. One
`JsonCache` does the shared work: build the path, validate on read, and write
atomically.

`stores.py` declares one `CacheEntry` for each data kind.
"""

import os
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, TypeVar

from pydantic import TypeAdapter

M = TypeVar("M")

MIN_AGE_SECONDS = 600.0
"""A younger file can be in use by a request. The cleanup keeps it."""


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
