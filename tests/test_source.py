"""Tests for the agnostic source protocol and the server routes."""

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from curl_cffi import requests as curl_requests
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


@pytest.fixture(autouse=True)
def _pin_the_target(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the fingerprint rotation deterministic for the tests."""
    monkeypatch.setattr(beat, "_current_target", beat.IMPERSONATE_TARGETS[0])


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


class _FakeResponse:
    """A curl_cffi response with the fields the source reads."""

    def __init__(
        self, status_code: int, text: str = "", mitigated: str | None = None
    ) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = {"cf-mitigated": mitigated} if mitigated else {}


class _FakeSession:
    """A curl_cffi session that answers from a saved table."""

    def __init__(self, responses: dict[str, _FakeResponse]) -> None:
        self.responses = responses
        self.targets: list[str] = []

    async def get(
        self,
        url: str,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        impersonate: str = "",
    ) -> _FakeResponse:
        self.targets.append(impersonate)
        return self.responses[impersonate]


def _fake_source(responses: dict[str, _FakeResponse]) -> beat.BeatSource:
    source = beat.BeatSource()
    source._session = cast(Any, _FakeSession(responses))
    return source


def test_the_beat_source_returns_the_page_text() -> None:
    source = _fake_source({"chrome": _FakeResponse(200, "<html>ok</html>")})

    assert asyncio.run(source._html(beat.BEAT_URL)) == "<html>ok</html>"


def test_the_beat_source_retries_a_challenge_with_another_fingerprint() -> None:
    source = _fake_source(
        {
            "chrome": _FakeResponse(403, mitigated="challenge"),
            "safari_ios": _FakeResponse(200, "<html>ok</html>"),
        }
    )

    assert asyncio.run(source._html(beat.BEAT_URL)) == "<html>ok</html>"
    assert cast(Any, source._session).targets == ["chrome", "safari_ios"]


def test_the_beat_source_does_not_retry_a_plain_error() -> None:
    source = _fake_source({"chrome": _FakeResponse(404)})

    with pytest.raises(sources.SourceError, match="answered status 404"):
        asyncio.run(source._html(beat.BEAT_URL))

    assert cast(Any, source._session).targets == ["chrome"]


def test_the_beat_source_reports_a_challenge_after_every_target() -> None:
    responses = {
        target: _FakeResponse(403, mitigated="challenge")
        for target in beat.IMPERSONATE_TARGETS
    }
    source = _fake_source(responses)

    with pytest.raises(sources.SourceError, match="answered status 403"):
        asyncio.run(source._html(beat.BEAT_URL))

    assert cast(Any, source._session).targets == list(beat.IMPERSONATE_TARGETS)


def test_the_beat_source_wraps_a_connection_error() -> None:
    class Failing:
        async def get(self, *args: object, **kwargs: object) -> object:
            raise curl_requests.RequestsError("boom")

    source = beat.BeatSource()
    source._session = cast(Any, Failing())

    with pytest.raises(sources.SourceError, match="cannot reach beat.com.au"):
        asyncio.run(source._html(beat.BEAT_URL))


def test_target_order_starts_at_the_given_target() -> None:
    assert beat.target_order("safari_ios") == ("safari_ios", "firefox", "chrome")


def test_the_beat_source_keeps_the_target_that_passed() -> None:
    source = _fake_source(
        {
            "chrome": _FakeResponse(403, mitigated="challenge"),
            "safari_ios": _FakeResponse(200, "<html>ok</html>"),
        }
    )

    assert asyncio.run(source._html(beat.BEAT_URL)) == "<html>ok</html>"
    assert beat._current_target == "safari_ios"
