"""Tests for the agnostic source protocol and the server routes."""

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gigmap import beat, geocode, server, sources, stores


class FakeSource:
    """A gig source that answers with saved data."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.gig_error: Exception | None = None
        self.venue_error: Exception | None = None

    async def page(self, start_date: str, end_date: str) -> str:
        self.calls.append(("page", start_date, end_date))
        return "<html>page</html>"

    async def gigs(self, start_date: str, end_date: str) -> list[sources.Gig]:
        self.calls.append(("gigs", start_date, end_date))
        if self.gig_error is not None:
            raise self.gig_error
        return [sources.Gig(artist="Act", detail_url="https://beat.com.au/act/")]

    async def gig_detail(self, detail_url: str) -> sources.GigDetail | None:
        self.calls.append(("gig_detail", detail_url))
        return sources.GigDetail(detail_url=detail_url, time="8:30pm")

    async def venue(self, venue_url: str) -> sources.Venue:
        self.calls.append(("venue", venue_url))
        if self.venue_error is not None:
            raise self.venue_error
        return sources.Venue(name="Venue", address="1 St", venue_url=venue_url)


def _the_beat_source_is_a_gig_source() -> sources.GigSource:
    return beat.BeatSource()


@pytest.fixture
def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, FakeSource]]:
    monkeypatch.setenv(server.DATA_DIR_ENV, str(tmp_path))
    fake = FakeSource()
    source: sources.GigSource = stores.CachedSource(
        fake, stores.DataStores(server.data_dir)
    )
    server.app.dependency_overrides[server.source_dependency] = lambda: source
    try:
        yield TestClient(server.app), fake
    finally:
        server.app.dependency_overrides.clear()


def test_url_key_uses_the_last_path_segment() -> None:
    assert sources.url_key("https://beat.com.au/directory/venue/") == "venue"
    assert sources.url_key("https://beat.com.au/act") == "act"
    assert sources.url_key("https://beat.com.au/") == "page"


def test_page_route_uses_the_source(client: tuple[TestClient, FakeSource]) -> None:
    http, fake = client

    response = http.get(
        "/api/page",
        params={"start_date": "2026-10-01", "end_date": "2026-10-02"},
    )

    assert response.status_code == 200
    assert response.text == "<html>page</html>"
    assert fake.calls == [("page", "2026-10-01", "2026-10-02")]


def test_gig_list_route_uses_the_source(client: tuple[TestClient, FakeSource]) -> None:
    http, fake = client

    response = http.get(
        "/api/gigs",
        params={"start_date": "2026-10-01", "end_date": "2026-10-02"},
    )

    assert response.status_code == 200
    assert response.json() == [
        {
            "artist": "Act",
            "detail_url": "https://beat.com.au/act/",
            "date": None,
            "end_date": None,
            "venue": None,
            "venue_url": None,
            "genres": [],
            "categories": [],
            "price": None,
            "ticket_url": None,
            "image_url": None,
            "image_alt": None,
            "time": None,
        }
    ]
    assert fake.calls == [("gigs", "2026-10-01", "2026-10-02")]


def test_times_route_streams_the_detail_time(
    client: tuple[TestClient, FakeSource],
) -> None:
    http, fake = client

    response = http.get(
        "/api/times",
        params={"start_date": "2026-10-01", "end_date": "2026-10-02"},
    )

    assert response.status_code == 200
    assert response.json() == {"index": 0, "time": "8:30pm"}
    assert ("gig_detail", "https://beat.com.au/act/") in fake.calls


def test_venue_route_uses_the_source(
    client: tuple[TestClient, FakeSource], monkeypatch: pytest.MonkeyPatch
) -> None:
    http, _ = client
    monkeypatch.setattr(geocode, "geocode_venue", _no_place)

    response = http.get(
        "/api/venue",
        params={"venue_url": "https://beat.com.au/directory/venue/"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Venue"
    assert data["latitude"] is None


def test_a_bad_url_answers_400(client: tuple[TestClient, FakeSource]) -> None:
    http, fake = client
    fake.venue_error = sources.SourceUrlError("bad url")

    response = http.get("/api/venue", params={"venue_url": "https://x.test/y/"})

    assert response.status_code == 400
    assert response.json() == {"detail": "bad url"}


def test_a_source_error_answers_502(client: tuple[TestClient, FakeSource]) -> None:
    http, fake = client
    fake.gig_error = sources.SourceError("cannot reach beat.com.au")

    response = http.get(
        "/api/gigs",
        params={"start_date": "2026-10-01", "end_date": "2026-10-02"},
    )

    assert response.status_code == 502
    assert response.json() == {"detail": "cannot reach beat.com.au"}


async def _no_place(name: str | None, address: str | None) -> dict | None:
    return None


def test_the_wrapper_caches_the_gig_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(server.DATA_DIR_ENV, str(tmp_path))
    fake = FakeSource()
    source = stores.CachedSource(fake, stores.DataStores(server.data_dir))

    async def run() -> None:
        first = await source.gigs("2026-10-01", "2026-10-02")
        second = await source.gigs("2026-10-01", "2026-10-02")
        assert first == second
        assert await source.gig_detail("https://beat.com.au/act/") is not None
        assert await source.gig_detail("https://beat.com.au/act/") is not None

    asyncio.run(run())

    assert fake.calls == [
        ("gigs", "2026-10-01", "2026-10-02"),
        ("gig_detail", "https://beat.com.au/act/"),
    ]


def test_the_wrapper_refetches_on_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(server.DATA_DIR_ENV, str(tmp_path))
    fake = FakeSource()
    source = stores.CachedSource(fake, stores.DataStores(server.data_dir), refresh=True)

    async def run() -> None:
        await source.gigs("2026-10-01", "2026-10-02")
        await source.gigs("2026-10-01", "2026-10-02")

    asyncio.run(run())

    assert fake.calls.count(("gigs", "2026-10-01", "2026-10-02")) == 2


def test_the_venue_is_cached_only_with_coordinates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(server.DATA_DIR_ENV, str(tmp_path))
    fake = FakeSource()
    source = stores.CachedSource(fake, stores.DataStores(server.data_dir))

    async def found(name: str | None, address: str | None) -> dict:
        return {"latitude": -37.8, "longitude": 144.9, "display_name": "Here"}

    monkeypatch.setattr(geocode, "geocode_venue", found)

    async def run() -> None:
        first = await source.venue("https://beat.com.au/directory/venue/")
        second = await source.venue("https://beat.com.au/directory/venue/")
        assert first.latitude == -37.8
        assert second.latitude == -37.8

    asyncio.run(run())

    assert fake.calls.count(("venue", "https://beat.com.au/directory/venue/")) == 1


def test_a_venue_without_coordinates_is_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(server.DATA_DIR_ENV, str(tmp_path))
    fake = FakeSource()
    source = stores.CachedSource(fake, stores.DataStores(server.data_dir))

    async def missing(name: str | None, address: str | None) -> None:
        return None

    monkeypatch.setattr(geocode, "geocode_venue", missing)

    async def run() -> None:
        await source.venue("https://beat.com.au/directory/venue/")
        await source.venue("https://beat.com.au/directory/venue/")

    asyncio.run(run())

    assert fake.calls.count(("venue", "https://beat.com.au/directory/venue/")) == 2
