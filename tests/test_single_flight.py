"""Tests for the single flight of `fetch_once` and the 7-day cap."""

import asyncio
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gigmap import server, stores
from test_source import FakeSource


class SlowSource(FakeSource):
    """A source whose candidates wait for a gate."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = asyncio.Event()

    async def candidates(self, start_date, end_date):
        await self.gate.wait()
        return await super().candidates(start_date, end_date)


def _member(tmp_path: Path, fake: FakeSource) -> stores.CachedSource:
    return stores.CachedSource(fake, stores.DataStores(lambda: tmp_path))


def test_two_cold_calls_make_one_candidates_call(tmp_path: Path) -> None:
    fake = SlowSource()
    member = _member(tmp_path, fake)

    async def run() -> None:
        first = asyncio.create_task(server.read_list("beat", member, "2026-10-01", "2026-10-02"))
        second = asyncio.create_task(server.read_list("beat", member, "2026-10-01", "2026-10-02"))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert len(server._INFLIGHT) == 1
        fake.gate.set()
        assert await first == await second

    asyncio.run(run())

    assert [c for c in fake.calls if c[0] == "candidates"] == [
        ("candidates", "2026-10-01", "2026-10-02")
    ]
    assert server._INFLIGHT == {}


def test_1000_cold_ranges_leave_no_key(tmp_path: Path) -> None:
    fake = FakeSource()
    member = _member(tmp_path, fake)
    start = date(2026, 1, 1)

    async def run() -> None:
        await asyncio.gather(
            *(
                server.fetch_once(
                    "beat", member, str(start + timedelta(i)), str(start + timedelta(i))
                )
                for i in range(1000)
            )
        )
        await asyncio.sleep(0)

    asyncio.run(run())

    assert len(fake.calls) == 1000
    assert server._INFLIGHT == {}


def test_two_ranges_run_at_once(tmp_path: Path) -> None:
    fake = SlowSource()
    member = _member(tmp_path, fake)

    async def run() -> None:
        a = asyncio.create_task(server.fetch_once("beat", member, "2026-10-01", "2026-10-02"))
        b = asyncio.create_task(server.fetch_once("beat", member, "2026-10-03", "2026-10-04"))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert len(server._INFLIGHT) == 2
        fake.gate.set()
        await asyncio.gather(a, b)

    asyncio.run(run())

    assert len(fake.calls) == 2
    assert server._INFLIGHT == {}


@pytest.mark.parametrize(
    ("end_date", "status"),
    [("2026-10-07", 200), ("2026-10-08", 400)],
)
def test_a_range_over_7_days_answers_400(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, end_date: str, status: int
) -> None:
    monkeypatch.setenv(server.DATA_DIR_ENV, str(tmp_path))
    member = _member(tmp_path, FakeSource())
    server.app.dependency_overrides[server.source_dependency] = lambda: {"beat": member}
    try:
        response = TestClient(server.app).get(
            "/api/beat/events",
            params={"start_date": "2026-10-01", "end_date": end_date},
        )
    finally:
        server.app.dependency_overrides.clear()

    assert response.status_code == status
