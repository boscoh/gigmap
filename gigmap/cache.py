"""Save and read the JSON data files through one small abstraction.

One `JsonCache` holds the rules of one cache kind: the subdirectory, the
value type, the file name, and the freshness rule. It builds the path,
validates on read, and writes atomically.

`stores.py` creates one `JsonCache` for each data kind.
"""

import os
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Generic, TypeVar

from pydantic import TypeAdapter

M = TypeVar("M")


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
