"""Tests for the warm load that follows a venue version change."""

import asyncio
from datetime import date, timedelta

import pytest

from gigmap import server, stores


def write_venue(name: str) -> None:
    """Write one venue file of the given name into the data dir."""
    root = stores.data_dir() / stores.VENUES_SUBDIR
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text("{}")


def test_an_empty_cache_is_not_stale() -> None:
    """A fresh instance has nothing to refresh."""
    assert not server.venue_cache_stale()


def test_a_cache_of_an_older_version_is_stale() -> None:
    """The cleanup drops the old files, so the pins must be read again."""
    write_venue("beat_v0_deadbeef.json")
    assert server.venue_cache_stale()


def test_a_cache_of_the_current_version_is_not_stale() -> None:
    write_venue(f"beat_{stores.VENUES_VERSION}_deadbeef.json")
    assert not server.venue_cache_stale()


def test_the_ranges_cover_the_next_two_weeks() -> None:
    today = date.today()
    end = today + timedelta(days=server.WARM_DAYS - 1)

    assert server.warm_ranges() == [
        (today.isoformat(), (today + timedelta(days=6)).isoformat()),
        ((today + timedelta(days=7)).isoformat(), end.isoformat()),
    ]


def test_a_warm_does_not_start_for_a_current_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The task returns at once, so this test reads no network."""
    calls = []

    async def fake_warm(name: str, start: str, end: str) -> int:
        calls.append(name)
        return 0

    monkeypatch.setattr(server, "warm_source", fake_warm)
    asyncio.run(server.warm_loop(False))

    assert calls == []


def test_a_stale_cache_warms_each_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    async def fake_warm(name: str, start: str, end: str) -> int:
        calls.append(name)
        return 0

    monkeypatch.setattr(server, "warm_source", fake_warm)
    asyncio.run(server.warm_loop(True))

    assert {name for name in calls} == set(server.SOURCE_FACTORIES)
    assert len(calls) == len(server.SOURCE_FACTORIES) * len(server.warm_ranges())


def test_one_failed_source_does_not_stop_the_warm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    async def fake_warm(name: str, start: str, end: str) -> int:
        calls.append(name)
        if name == "beat":
            raise RuntimeError("the site is down")
        return 0

    monkeypatch.setattr(server, "warm_source", fake_warm)
    asyncio.run(server.warm_loop(True))

    assert "whatson" in calls
