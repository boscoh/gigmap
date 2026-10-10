"""Tests for the data cleanup, the loop, and the lifespan."""

import asyncio
import os
import time
from pathlib import Path

import pytest

import server

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


def test_clean_data_removes_only_the_old_files(data_dir: Path) -> None:
    stale_day = write(data_dir / "days" / "gigmap_2020-01-01_2020-01-01.json", OLD)
    fresh_day = write(data_dir / "days" / "gigmap_2020-01-02_2020-01-02.json", 0)
    future_day = write(data_dir / "days" / "gigmap_2999-01-01_2999-01-01.json", OLD)
    unknown_day = write(data_dir / "days" / "notes.json", OLD)
    old_gig = write(data_dir / "gigs" / "old.json", OLD)
    new_gig = write(data_dir / "gigs" / "new.json", 0)
    venue = write(data_dir / "venues" / "keep.json", OLD)

    report = server.clean_data(time.time(), server.GIG_TTL_DAYS, dry_run=False)

    assert not stale_day.exists()
    assert not old_gig.exists()
    for kept in (fresh_day, future_day, unknown_day, new_gig, venue):
        assert kept.exists()
    assert report.days == 1
    assert report.gigs == 1
    # The recent day file matches the past-range rule, so it counts as skipped.
    assert report.skipped == 1


def test_clean_data_dry_run_removes_nothing(data_dir: Path) -> None:
    stale_day = write(data_dir / "days" / "gigmap_2020-01-01_2020-01-01.json", OLD)
    old_gig = write(data_dir / "gigs" / "old.json", OLD)

    report = server.clean_data(time.time(), server.GIG_TTL_DAYS, dry_run=True)

    assert stale_day.exists()
    assert old_gig.exists()
    assert report.days == 1
    assert report.gigs == 1


def test_a_recent_file_is_skipped(data_dir: Path) -> None:
    fresh_day = write(data_dir / "days" / "gigmap_2020-01-01_2020-01-01.json", 0)

    report = server.clean_data(time.time(), server.GIG_TTL_DAYS, dry_run=False)

    assert fresh_day.exists()
    assert report.days == 0
    assert report.skipped == 1


def test_run_cleanup_prints_the_report(data_dir: Path, capsys: pytest.CaptureFixture) -> None:
    write(data_dir / "days" / "gigmap_2020-01-01_2020-01-01.json", OLD)

    server.run_cleanup()

    assert "removed 1 day files and 0 gig files" in capsys.readouterr().out


def test_run_cleanup_is_quiet_on_request(data_dir: Path, capsys: pytest.CaptureFixture) -> None:
    write(data_dir / "days" / "gigmap_2020-01-01_2020-01-01.json", OLD)

    server.run_cleanup(quiet=True)

    assert capsys.readouterr().out == ""


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

    async def run() -> None:
        async with server.lifespan(server.app):
            await asyncio.wait_for(started.wait(), 1)
        await asyncio.sleep(0.05)
        assert stopped.is_set()

    asyncio.run(run())


def test_a_bad_loop_iteration_does_not_stop_the_loop(monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_env_int(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIGMAP_TEST_TTL", "7")
    assert server.env_int("GIGMAP_TEST_TTL", 30) == 7
    monkeypatch.setenv("GIGMAP_TEST_TTL", "not a number")
    assert server.env_int("GIGMAP_TEST_TTL", 30) == 30
    monkeypatch.delenv("GIGMAP_TEST_TTL")
    assert server.env_int("GIGMAP_TEST_TTL", 30) == 30
