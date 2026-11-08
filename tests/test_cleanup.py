"""Tests for the data cleanup, the loop, and the lifespan."""

import asyncio
import logging
import os
import time
from pathlib import Path

import pytest

from gigmap import server, stores

OLD = 40 * 86400


def write(path: Path, age: float = 0) -> Path:
    """Create a file and set its age in seconds."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    stamp = time.time() - age
    os.utime(path, (stamp, stamp))
    return path


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(server.DATA_DIR_ENV, str(tmp_path))
    return tmp_path


def test_the_cleanup_removes_only_the_old_files(data_dir: Path) -> None:
    version = stores.DAYS_VERSION
    stale_day = write(
        data_dir / "days" / f"gigmap_beat_{version}_2020-01-01_2020-01-01.json", OLD
    )
    fresh_day = write(
        data_dir / "days" / f"gigmap_beat_{version}_2020-01-02_2020-01-02.json", 0
    )
    future_day = write(
        data_dir / "days" / f"gigmap_beat_{version}_2999-01-01_2999-01-01.json", OLD
    )
    unknown_day = write(data_dir / "days" / "notes.json", OLD)
    venue = write(
        data_dir / "venues" / f"beat_{stores.VENUES_VERSION}_keep.json", OLD
    )

    report = stores.DataStores(server.data_dir).clean(time.time(), dry_run=False)

    assert not stale_day.exists()
    for kept in (fresh_day, future_day, unknown_day, venue):
        assert kept.exists()
    assert report.days == 1
    # The recent day file matches the past-range rule, so it counts as skipped.
    assert report.skipped == 1


def test_the_dry_run_removes_nothing(data_dir: Path) -> None:
    stale_day = write(data_dir / "days" / "gigmap_2020-01-01_2020-01-01.json", OLD)

    report = stores.DataStores(server.data_dir).clean(time.time(), dry_run=True)

    assert stale_day.exists()
    assert report.days == 1


def test_the_cleanup_removes_a_temporary_file(data_dir: Path) -> None:
    temp = write(
        data_dir / "days" / "gigmap_2026-10-01_2026-10-01.json.abc.tmp", OLD
    )

    stores.DataStores(server.data_dir).clean(time.time(), dry_run=True)
    assert temp.exists()

    stores.DataStores(server.data_dir).clean(time.time(), dry_run=False)
    assert not temp.exists()

def test_a_recent_file_is_skipped(data_dir: Path) -> None:
    fresh_day = write(data_dir / "days" / "gigmap_2020-01-01_2020-01-01.json", 0)

    report = stores.DataStores(server.data_dir).clean(time.time(), dry_run=False)

    assert fresh_day.exists()
    assert report.days == 0
    assert report.skipped == 1


def test_the_stale_gig_tree_is_removed(data_dir: Path) -> None:
    gig = write(data_dir / "gigs" / "beat_abby-hayes.json", OLD)

    report = stores.DataStores(server.data_dir).clean(time.time(), dry_run=False)

    assert not gig.exists()
    assert report.stale == 1
    assert report.size > 0


def test_the_dry_run_keeps_the_stale_gig_tree(data_dir: Path) -> None:
    gig = write(data_dir / "gigs" / "beat_abby-hayes.json", OLD)

    report = stores.DataStores(server.data_dir).clean(time.time(), dry_run=True)

    assert gig.exists()
    assert report.stale == 1


def test_run_cleanup_logs_the_report(
    data_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    write(data_dir / "days" / "gigmap_2020-01-01_2020-01-01.json", OLD)
    caplog.set_level(logging.INFO, logger="gigmap.server")

    server.run_cleanup()

    message = caplog.messages[0]
    assert "removed" in message
    assert "days" in message
    assert "size" in message


def test_run_cleanup_is_quiet_on_request(
    data_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    write(data_dir / "days" / "gigmap_2020-01-01_2020-01-01.json", OLD)
    caplog.set_level(logging.INFO, logger="gigmap.server")

    server.run_cleanup(quiet=True)

    assert caplog.records == []


def test_the_loop_repeats(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(server, "run_cleanup", lambda *args, **kwargs: calls.append(1))
    monkeypatch.setattr(server, "CLEANUP_INTERVAL_SECONDS", 0.01)

    async def run() -> None:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(server.cleanup_loop(), 0.06)

    asyncio.run(run())

    assert len(calls) >= 2


def test_the_lifespan_starts_and_cancels_the_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def fake_loop() -> None:
        started.set()
        try:
            await asyncio.sleep(3600)
        finally:
            stopped.set()

    monkeypatch.setattr(server, "cleanup_loop", fake_loop)
    monkeypatch.setattr(server, "setup_logging", lambda: None)

    async def run() -> None:
        async with server.lifespan(server.app):
            await asyncio.wait_for(started.wait(), 1)
        await asyncio.sleep(0.05)
        assert stopped.is_set()

    asyncio.run(run())


def test_a_bad_loop_iteration_does_not_stop_the_loop(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[int] = []

    def flaky(*args: object, **kwargs: object) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("disk is busy")

    monkeypatch.setattr(server, "run_cleanup", flaky)
    monkeypatch.setattr(server, "CLEANUP_INTERVAL_SECONDS", 0.01)

    async def run() -> None:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(server.cleanup_loop(), 0.06)

    asyncio.run(run())

    assert len(calls) >= 2
    assert "cleanup failed" in caplog.text


def clean_events(dry_run: bool) -> stores.CleanReport:
    return stores.DataStores(stores.data_dir).clean(time.time(), dry_run=dry_run)


def test_the_cleanup_removes_only_the_old_event_files(data_dir: Path) -> None:
    version = stores.EVENTS_VERSION
    old = write(data_dir / "events" / f"beat_{version}_a.json", 8 * 86400)
    fresh = write(data_dir / "events" / f"beat_{version}_b.json", 0)
    older_version = write(data_dir / "events" / "beat_v0_c.json", 0)
    unknown = write(data_dir / "events" / "notes.json", 8 * 86400)

    report = clean_events(dry_run=False)

    assert report.events == 2
    assert not old.exists() and not older_version.exists()
    assert fresh.exists() and unknown.exists()


def test_the_dry_run_keeps_the_event_files(data_dir: Path) -> None:
    old = write(data_dir / "events" / f"beat_{stores.EVENTS_VERSION}_a.json", 8 * 86400)

    report = clean_events(dry_run=True)

    assert report.events == 1
    assert old.exists()


def test_a_day_file_of_an_old_version_goes(data_dir: Path) -> None:
    """A file of an older shape is never read again, so it goes."""
    old_shape = write(
        data_dir / "days" / "gigmap_beat_v10_2999-01-01_2999-01-01.json", OLD
    )
    unversioned = write(data_dir / "days" / "gigmap_2999-01-01_2999-01-01.json", OLD)

    report = stores.DataStores(server.data_dir).clean(time.time(), dry_run=False)

    assert report.days == 2
    assert not old_shape.exists() and not unversioned.exists()


def test_a_day_file_of_an_old_version_that_a_request_may_hold_stays(
    data_dir: Path,
) -> None:
    old_shape = write(
        data_dir / "days" / "gigmap_beat_v10_2020-01-01_2020-01-01.json", 0
    )

    report = stores.DataStores(server.data_dir).clean(time.time(), dry_run=False)

    assert report.days == 0
    assert report.skipped == 1
    assert old_shape.exists()


def test_a_venue_file_of_an_old_shape_goes(data_dir: Path) -> None:
    unversioned = write(data_dir / "venues" / "beat_Playhouse.json", OLD)
    old_version = write(data_dir / "venues" / "beat_v0_Playhouse.json", OLD)
    current = write(
        data_dir / "venues" / f"beat_{stores.VENUES_VERSION}_Playhouse.json", OLD
    )

    report = stores.DataStores(server.data_dir).clean(time.time(), dry_run=False)

    assert report.venues == 2
    assert not unversioned.exists() and not old_version.exists()
    assert current.exists()


def test_a_miss_file_goes_after_the_wait(data_dir: Path) -> None:
    stale = write(data_dir / "venues" / f"beat_miss_{stores.MISS_VERSION}_Playhouse.json", 8 * 86400)
    fresh = write(data_dir / "venues" / f"beat_miss_{stores.MISS_VERSION}_Hamer.json", 0)

    report = stores.DataStores(server.data_dir).clean(time.time(), dry_run=False)

    assert report.venues == 1
    assert not stale.exists()
    assert fresh.exists()


def test_a_miss_file_of_an_older_query_goes_now(data_dir: Path) -> None:
    fresh = write(data_dir / "venues" / "beat_miss_v1_Hamer.json", 0)
    bare = write(data_dir / "venues" / "beat_miss_Playhouse.json", 0)

    report = stores.DataStores(server.data_dir).clean(time.time(), dry_run=False)

    assert report.venues == 2
    assert not fresh.exists() and not bare.exists()


def test_the_dry_run_keeps_an_old_venue_file(data_dir: Path) -> None:
    old_shape = write(data_dir / "venues" / "beat_Playhouse.json", OLD)

    report = stores.DataStores(server.data_dir).clean(time.time(), dry_run=True)

    assert report.venues == 1
    assert old_shape.exists()
