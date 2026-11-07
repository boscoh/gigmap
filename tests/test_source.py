"""Tests for the agnostic source protocol and the server routes."""

import asyncio
import json
import logging
import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from curl_cffi import requests as curl_requests
from fastapi import HTTPException
from fastapi.testclient import TestClient

from gigmap import beat, geocode, server, sources, stores


VENUE_URL = "https://beat.com.au/directory/venue/"
SECOND_VENUE_URL = "https://beat.com.au/directory/hall/"
PARAMS = {"start_date": "2026-10-01", "end_date": "2026-10-02"}

class FakeSource:
    """A gig source that answers with saved data."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.gig_error: Exception | None = None
        self.venue_error: Exception | None = None
        self.gig_venue_urls: list[str] = [VENUE_URL]
        # A test holds the read of one venue at this url until it opens
        # the gate, so a second gig can leave its line first.
        self.slow_venue_url: str | None = None
        self.venue_gate: asyncio.Event | None = None

    async def candidates(self, start_date: str, end_date: str) -> list[sources.Gig]:
        self.calls.append(("candidates", start_date, end_date))
        if self.gig_error is not None:
            raise self.gig_error
        return [
            sources.Gig(
                artist="Act",
                detail_url="https://beat.com.au/act/",
                venue="Venue",
                venue_url=venue_url,
                time="8:30pm",
            )
            for venue_url in self.gig_venue_urls
        ]

    async def expand(
        self, gig: sources.Gig, start_date: str, end_date: str
    ) -> list[sources.Gig]:
        self.calls.append(("expand", start_date, end_date))
        return [gig]

    async def venue(self, venue_url: str) -> sources.Venue:
        self.calls.append(("venue", venue_url))
        if venue_url == self.slow_venue_url:
            if self.venue_gate is not None:
                await self.venue_gate.wait()
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
    server.app.dependency_overrides[server.source_dependency] = lambda: {"beat": source}
    try:
        yield TestClient(server.app), fake
    finally:
        server.app.dependency_overrides.clear()


def test_url_key_uses_the_last_path_segment() -> None:
    assert sources.url_key("https://beat.com.au/directory/venue/") == "venue"
    assert sources.url_key("https://beat.com.au/act") == "act"
    assert sources.url_key("https://beat.com.au/") == "page"


def test_a_beat_ticket_holds_the_price_and_the_link() -> None:
    ticket = beat.parse_ticket("$27.78", "https://example.test/buy")

    assert ticket is not None
    assert ticket.state is None
    assert ticket.label == "$27.78"
    assert ticket.group == sources.TicketGroup.ON_SALE
    assert ticket.price == "$27.78"
    assert ticket.price_min == 27.78
    assert ticket.url == "https://example.test/buy"


def test_a_beat_price_range_holds_both_numbers() -> None:
    ticket = beat.parse_ticket("$21.00 - 40.00", None)

    assert ticket is not None
    assert ticket.label == "$21.00 - 40.00"
    assert ticket.price_min == 21.0
    assert ticket.price_max == 40.0


def test_a_beat_ticket_without_a_price_reads_tickets() -> None:
    ticket = beat.parse_ticket(None, "https://example.test/buy")

    assert ticket is not None
    assert ticket.label == "Tickets"
    assert ticket.price_min is None


def test_a_beat_card_without_a_price_or_link_holds_no_ticket() -> None:
    assert beat.parse_ticket(None, None) is None


def test_a_free_beat_ticket_sorts_as_zero() -> None:
    ticket = beat.parse_ticket("Free", None)

    assert ticket is not None
    assert ticket.group == sources.TicketGroup.FREE
    assert ticket.price_min == 0.0


def test_a_free_beat_ticket_keeps_the_link() -> None:
    ticket = beat.parse_ticket(None, "https://example.test/free", free=True)

    assert ticket is not None
    assert ticket.group == sources.TicketGroup.FREE
    assert ticket.label == "Free"
    assert ticket.price == "Free"
    assert ticket.price_min == 0.0
    assert ticket.url == "https://example.test/free"


def test_a_beat_free_category_holds_a_free_ticket() -> None:
    html = """
    <div class="events_wrapper">
      <div class="gig-card">
        <div class="gig-title"><a href="https://beat.com.au/act/">Act</a></div>
        <div class="gig-image"><img src="https://beat.com.au/act.jpg" alt="Act"></div>
        <div class="gig-category"><span class="badge">Free Events</span></div>
      </div>
    </div>
    """

    gigs = beat.parse_gigs(html)

    assert len(gigs) == 1
    assert gigs[0].ticket is not None
    assert gigs[0].ticket.group == sources.TicketGroup.FREE
    assert gigs[0].ticket.label == "Free"
    assert gigs[0].ticket.price_min == 0.0
    assert gigs[0].ticket.url is None


def test_the_root_page_answers_the_all_source() -> None:
    assert server.page_source_name("/") == "gigs"
    assert server.page_source_name("/nowhere/") == "gigs"


def test_a_page_prefix_answers_that_source() -> None:
    assert server.page_source_name("/fringe/") == "fringe"
    assert server.page_source_name("/fringe") == "fringe"


def test_an_api_path_answers_its_source() -> None:
    assert server.api_source_name("/api/beat/events") == "beat"
    assert server.api_source_name("/api/fringe/events") == "fringe"
    assert server.api_source_name("/api/fringe") == "fringe"


def test_an_unknown_api_source_is_not_a_source() -> None:
    for path in ("/api/events", "/api/nothing/events", "/events"):
        with pytest.raises(HTTPException):
            server.api_source_name(path)


def test_the_api_prefix_of_a_source() -> None:
    assert server.api_prefix("beat") == "/api/beat"
    assert server.api_prefix("fringe") == "/api/fringe"

def test_each_site_serves_its_own_page(
    client: tuple[TestClient, FakeSource],
) -> None:
    http, _ = client

    root = http.get("/")
    all_page = http.get("/gigs/")
    beat_page = http.get("/beat/")
    fringe = http.get("/fringe/")

    assert root.status_code == 200
    # The root is the landing page. It holds a link for each page below
    # and no source, so it carries no API base.
    assert 'href="gigs/"' in root.text
    assert 'href="whatson/"' in root.text
    assert 'href="whatsonart/"' in root.text
    assert 'href="art/"' in root.text
    assert 'href="artshub/"' in root.text
    assert 'window.API_BASE' not in root.text
    assert all_page.status_code == 200
    assert '<title>Melb Gig Map</title>' in all_page.text
    assert 'window.API_BASE = "/api/gigs";' in all_page.text
    assert beat_page.status_code == 200
    assert '<title>Melb Gig Map</title>' in beat_page.text
    assert 'window.API_BASE = "/api/beat";' in beat_page.text
    assert fringe.status_code == 200
    assert '<title>Melb Fringe Gig Map</title>' in fringe.text
    assert 'window.API_BASE = "/api/fringe";' in fringe.text



def test_an_incoming_request_is_logged(
    client: tuple[TestClient, FakeSource], caplog: pytest.LogCaptureFixture
) -> None:
    http, _ = client
    caplog.set_level(logging.INFO, logger="gigmap.server")

    response = http.get("/")

    assert response.status_code == 200
    assert "incoming" in caplog.text
    assert "GET" in caplog.text
    assert "status" in caplog.text

def test_a_read_answers_on_the_fringe_path(
    client: tuple[TestClient, FakeSource],
) -> None:
    http, fake = client

    response = http.get(
        "/api/fringe/events",
        params={"start_date": "2026-10-01", "end_date": "2026-10-02"},
    )

    assert response.status_code == 200
    assert fake.calls[0] == ("candidates", "2026-10-01", "2026-10-02")


def test_each_source_page_answers_its_own_source(
    client: tuple[TestClient, FakeSource],
) -> None:
    """`/gigs/` answers the gig map, so the root page answers no source."""
    http, _ = client

    all_page = http.get("/gigs/")
    beat_page = http.get("/beat/")

    assert 'window.API_BASE = "/api/gigs";' in all_page.text
    assert 'window.API_BASE = "/api/beat";' in beat_page.text


def test_the_event_line_holds_every_gig_field(
    client: tuple[TestClient, FakeSource], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The line holds the whole gig, so the page needs no list read."""
    http, fake = client
    monkeypatch.setattr(geocode, "geocode_venue", _no_place)

    line = _lines(http.get("/api/beat/events", params=PARAMS))[0]

    assert line["gig"] == {
        "artist": "Act",
        "detail_url": "https://beat.com.au/act/",
        "date": None,
        "end_date": None,
        "venue": "Venue",
        "venue_url": VENUE_URL,
        "genres": [],
        "categories": [],
        "ticket": None,
        "image_url": None,
        "image_alt": None,
        "time": "8:30pm",
    }
    assert ("candidates", "2026-10-01", "2026-10-02") in fake.calls


def _lines(response: Any) -> list[dict[str, Any]]:
    """Return the gig lines of an NDJSON answer, without the total lines."""
    lines = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    return [line for line in lines if "total" not in line]


def test_the_removed_reads_answer_404(
    client: tuple[TestClient, FakeSource],
) -> None:
    """One read serves the page, so the old reads go."""
    http, _ = client

    for path in (
        "/api/beat/gigs",
        "/api/beat/times",
        "/api/beat/venue",
        "/api/beat/venues",
    ):
        response = http.get(path, params=PARAMS)
        assert response.status_code == 404, path


def test_the_event_stream_answers_one_line_for_each_gig(
    client: tuple[TestClient, FakeSource], monkeypatch: pytest.MonkeyPatch
) -> None:
    http, fake = client
    monkeypatch.setattr(geocode, "geocode_venue", _no_place)
    fake.gig_venue_urls = [VENUE_URL, SECOND_VENUE_URL]

    response = http.get("/api/beat/events", params=PARAMS)

    assert response.status_code == 200
    lines = _lines(response)
    assert sorted(line["index"] for line in lines) == [0, 1]
    assert {line["gig"]["time"] for line in lines} == {"8:30pm"}
    assert {line["venue"]["venue_url"] for line in lines} == {
        VENUE_URL,
        SECOND_VENUE_URL,
    }


def test_the_event_stream_sends_the_count_in_a_total_line(
    client: tuple[TestClient, FakeSource], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A member sends its count first, so the page shows it before row one."""
    http, fake = client
    monkeypatch.setattr(geocode, "geocode_venue", _no_place)
    fake.gig_venue_urls = [VENUE_URL, SECOND_VENUE_URL]

    response = http.get("/api/beat/events", params=PARAMS)

    first = response.text.splitlines()[0]
    assert json.loads(first) == {"total": 2}
    assert "x-gig-total" not in response.headers


def test_the_event_stream_reads_a_shared_venue_one_time(
    client: tuple[TestClient, FakeSource], monkeypatch: pytest.MonkeyPatch
) -> None:
    http, fake = client
    monkeypatch.setattr(geocode, "geocode_venue", _no_place)
    fake.gig_venue_urls = [VENUE_URL, VENUE_URL]

    response = http.get("/api/beat/events", params=PARAMS)

    assert response.status_code == 200
    assert fake.calls.count(("venue", VENUE_URL)) == 1
    lines = _lines(response)
    assert len(lines) == 2
    assert all(line["venue"]["venue_url"] == VENUE_URL for line in lines)


def test_a_stream_tells_nginx_to_pass_the_lines_at_once(
    client: tuple[TestClient, FakeSource], monkeypatch: pytest.MonkeyPatch
) -> None:
    """nginx holds a small stream until the source closes it.

    The response says otherwise with `X-Accel-Buffering`.
    """
    http, _ = client
    monkeypatch.setattr(geocode, "geocode_venue", _no_place)

    response = http.get("/api/beat/events", params=PARAMS)

    assert response.headers.get("x-accel-buffering") == "no"


def test_the_event_stream_answers_a_failed_venue_line(
    client: tuple[TestClient, FakeSource],
) -> None:
    http, fake = client
    fake.venue_error = sources.SourceError("cannot reach beat.com.au")

    line = _lines(http.get("/api/beat/events", params=PARAMS))[0]

    assert set(line) == {"index", "gig", "venue", "error"}
    assert line["gig"]["artist"] == "Act"
    assert line["gig"]["time"] == "8:30pm"
    assert line["venue"] is None
    assert line["error"] == "cannot reach beat.com.au"


def test_the_event_line_holds_the_whole_venue(
    client: tuple[TestClient, FakeSource], monkeypatch: pytest.MonkeyPatch
) -> None:
    http, _ = client

    async def found(name: str | None, address: str | None) -> dict:
        return {"latitude": -37.8, "longitude": 144.9}

    monkeypatch.setattr(geocode, "geocode_venue", found)

    line = _lines(http.get("/api/beat/events", params=PARAMS))[0]

    assert line["index"] == 0
    assert line["gig"]["artist"] == "Act"
    assert line["venue"] == {
        "venue_url": VENUE_URL,
        "name": "Venue",
        "address": "1 St",
        "latitude": -37.8,
        "longitude": 144.9,
    }


def test_a_bad_date_costs_the_event_stream_no_read(
    client: tuple[TestClient, FakeSource],
) -> None:
    http, fake = client

    response = http.get(
        "/api/beat/events",
        params={"start_date": "2026-10-19", "end_date": "2026-10-17"},
    )

    assert response.status_code == 400
    assert fake.calls == []


def test_a_blocked_venue_holds_no_other_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A line leaves the stream while the venue of another gig reads.

    The test reads the line of a fast venue before the gate of the slow
    venue opens, so a design with one barrier per range cannot pass.
    """
    monkeypatch.setenv(server.DATA_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(geocode, "geocode_venue", _no_place)
    fake = FakeSource()
    fake.gig_venue_urls = [VENUE_URL, SECOND_VENUE_URL]
    source = stores.CachedSource(fake, stores.DataStores(server.data_dir))

    async def run() -> list[dict[str, Any]]:
        gate = asyncio.Event()
        fake.slow_venue_url = VENUE_URL
        fake.venue_gate = gate
        response = await server.event_stream(
            {"beat": source}, "2026-10-01", "2026-10-02"
        )
        lines = cast(Any, response.body_iterator)
        pending = asyncio.ensure_future(lines.__anext__())
        try:
            total = json.loads(await asyncio.wait_for(pending, timeout=5))
            assert total == {"total": 2}
            # The gate of the slow venue stays shut, so only the gig of
            # the fast venue can answer.
            pending = asyncio.ensure_future(lines.__anext__())
            fast = json.loads(await asyncio.wait_for(pending, timeout=5))
            gate.set()
            rest = [json.loads(line) async for line in lines]
        finally:
            pending.cancel()
        return [fast, *rest]

    lines = asyncio.run(run())

    assert [line["index"] for line in lines] == [0, 1]
    assert lines[0]["gig"]["venue_url"] == SECOND_VENUE_URL
    assert lines[0]["venue"]["venue_url"] == SECOND_VENUE_URL


def test_a_damaged_date_answers_400(
    client: tuple[TestClient, FakeSource],
) -> None:
    http, fake = client

    response = http.get(
        "/api/beat/events",
        params={"start_date": "17/10/2026", "end_date": "2026-10-17"},
    )

    assert response.status_code == 400
    assert fake.calls == []


def test_a_source_error_answers_502(client: tuple[TestClient, FakeSource]) -> None:
    http, fake = client
    fake.gig_error = sources.SourceError("cannot reach beat.com.au")

    response = http.get(
        "/api/beat/events",
        params={"start_date": "2026-10-01", "end_date": "2026-10-02"},
    )

    assert response.status_code == 502
    assert response.json() == {"detail": "cannot reach beat.com.au"}


async def _no_place(name: str | None, address: str | None) -> dict | None:
    return None


def test_the_venue_is_cached_only_with_coordinates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(server.DATA_DIR_ENV, str(tmp_path))
    fake = FakeSource()
    source = stores.CachedSource(fake, stores.DataStores(server.data_dir))

    async def found(name: str | None, address: str | None) -> dict:
        return {"latitude": -37.8, "longitude": 144.9}

    monkeypatch.setattr(geocode, "geocode_venue", found)

    async def run() -> None:
        first = await source.venue("https://beat.com.au/directory/venue/")
        second = await source.venue("https://beat.com.au/directory/venue/")
        assert first.latitude == -37.8
        assert second.latitude == -37.8

    asyncio.run(run())

    assert fake.calls.count(("venue", "https://beat.com.au/directory/venue/")) == 1


def test_a_venue_with_no_place_is_read_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(server.DATA_DIR_ENV, str(tmp_path))
    fake = FakeSource()
    source = stores.CachedSource(fake, stores.DataStores(server.data_dir))

    async def missing(name: str | None, address: str | None) -> None:
        return None

    monkeypatch.setattr(geocode, "geocode_venue", missing)

    async def run() -> None:
        first = await source.venue("https://beat.com.au/directory/venue/")
        second = await source.venue("https://beat.com.au/directory/venue/")
        assert first.latitude is None
        assert second.latitude is None

    asyncio.run(run())

    assert fake.calls.count(("venue", "https://beat.com.au/directory/venue/")) == 1


def test_a_venue_with_no_place_is_tried_again_after_a_week(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(server.DATA_DIR_ENV, str(tmp_path))
    data = stores.DataStores(server.data_dir)
    fake = FakeSource()
    source = stores.CachedSource(fake, data)

    async def missing(name: str | None, address: str | None) -> None:
        return None

    monkeypatch.setattr(geocode, "geocode_venue", missing)
    url = "https://beat.com.au/directory/venue/"

    async def run() -> None:
        await source.venue(url)
        # Age the miss past the wait, so the app reads the venue again.
        miss = data.misses.path(sources.url_key(url))
        old = time.time() - stores.MISS_CACHE_SECONDS - 1
        os.utime(miss, (old, old))
        await source.venue(url)

    asyncio.run(run())

    assert fake.calls.count(("venue", url)) == 2


class _FakeResponse:
    """A curl_cffi response with the fields the source reads."""

    def __init__(
        self,
        status_code: int,
        text: str = "",
        mitigated: str | None = None,
        url: str = "https://beat.com.au/gig-guide/",
        reason: str = "OK",
    ) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = {"cf-mitigated": mitigated} if mitigated else {}
        self.url = url
        self.reason = reason

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


class _FakeSequenceSession:
    """A curl_cffi session that answers a list of responses for one target."""

    def __init__(self, responses: dict[str, list[_FakeResponse]]) -> None:
        self.responses = {target: list(items) for target, items in responses.items()}
        self.targets: list[str] = []

    async def get(
        self,
        url: str,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        impersonate: str = "",
    ) -> _FakeResponse:
        self.targets.append(impersonate)
        items = self.responses[impersonate]
        return items.pop(0) if len(items) > 1 else items[0]


def _fake_source(responses: dict[str, _FakeResponse]) -> beat.BeatSource:
    source = beat.BeatSource()
    source._session = cast(Any, _FakeSession(responses))
    return source


def _fake_source_with_sequence(
    responses: dict[str, list[_FakeResponse]],
) -> beat.BeatSource:
    source = beat.BeatSource()
    source._session = cast(Any, _FakeSequenceSession(responses))
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


def test_the_beat_source_retries_a_server_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(beat, "RETRY_DELAY_SECONDS", 0)
    source = _fake_source_with_sequence(
        {"chrome": [_FakeResponse(503), _FakeResponse(200, "<html>ok</html>")]}
    )

    assert asyncio.run(source._html(beat.BEAT_URL)) == "<html>ok</html>"
    assert cast(Any, source._session).targets == ["chrome", "chrome"]


def test_the_beat_source_gives_up_after_the_server_error_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(beat, "RETRY_DELAY_SECONDS", 0)
    source = _fake_source_with_sequence({"chrome": [_FakeResponse(503)]})

    with pytest.raises(sources.SourceError, match="answered status 503"):
        asyncio.run(source._html(beat.BEAT_URL))

    targets = cast(Any, source._session).targets
    assert targets == ["chrome"] * beat.SERVER_ERROR_ATTEMPTS


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


# The beat expand reads the time from the detail page.

DETAIL_HTML = """
<div class="article-meta-item">
  <div class="article-meta-headline">Time</div>
  <div class="article-meta-content">8.30pm</div>
</div>
"""


def _bare_gig(**fields: Any) -> sources.Gig:
    return sources.Gig(artist="Act", detail_url="https://beat.com.au/act/", **fields)


def test_expand_fills_the_time_under_the_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(beat, "DETAIL_POOL", asyncio.Semaphore(1))
    source = beat.BeatSource()
    seen: list[bool] = []

    async def html(url: str, params: dict[str, str] | None = None) -> str:
        seen.append(beat.DETAIL_POOL.locked())
        return DETAIL_HTML

    source._html = html  # type: ignore[method-assign]

    result = asyncio.run(source.expand(_bare_gig(), "2026-10-01", "2026-10-02"))

    assert [gig.time for gig in result] == ["8:30pm"]
    assert seen == [True]


def test_expand_keeps_the_gig_when_the_detail_read_fails() -> None:
    source = beat.BeatSource()

    async def html(url: str, params: dict[str, str] | None = None) -> str:
        raise sources.SourceError("no detail page")

    source._html = html  # type: ignore[method-assign]
    gig = _bare_gig()

    assert asyncio.run(source.expand(gig, "2026-10-01", "2026-10-02")) == [gig]


def test_expand_keeps_the_time_of_the_list() -> None:
    """A listed time costs no read."""
    source = beat.BeatSource()
    calls: list[str] = []

    async def html(url: str, params: dict[str, str] | None = None) -> str:
        calls.append(url)
        return DETAIL_HTML

    source._html = html  # type: ignore[method-assign]
    gig = _bare_gig(time="9pm")

    assert asyncio.run(source.expand(gig, "2026-10-01", "2026-10-02")) == [gig]
    assert calls == []


def test_expand_skips_a_url_that_is_not_a_detail_page() -> None:
    source = beat.BeatSource()
    calls: list[str] = []

    async def html(url: str, params: dict[str, str] | None = None) -> str:
        calls.append(url)
        return DETAIL_HTML

    source._html = html  # type: ignore[method-assign]
    gigs = [
        sources.Gig(artist="Act", detail_url="https://example.test/act/"),
        sources.Gig(artist="Act"),
    ]

    for gig in gigs:
        assert asyncio.run(source.expand(gig, "2026-10-01", "2026-10-02")) == [gig]
    assert calls == []


def test_the_venue_read_holds_the_beat_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    """Beat has one cap of five for every read of its own site."""
    monkeypatch.setattr(beat, "DETAIL_POOL", asyncio.Semaphore(1))
    source = beat.BeatSource()
    seen: list[bool] = []

    async def html(url: str, params: dict[str, str] | None = None) -> str:
        seen.append(beat.DETAIL_POOL.locked())
        return "<html></html>"

    source._html = html  # type: ignore[method-assign]

    asyncio.run(source.venue(VENUE_URL))

    assert seen == [True]


def beat_expand(html: Any, calls: list[str]) -> list[sources.Gig]:
    source = beat.BeatSource()

    async def fake(url: str, params: dict[str, str] | None = None) -> str:
        calls.append(url)
        if isinstance(html, Exception):
            raise html
        return html

    source._html = fake  # type: ignore[method-assign]
    return asyncio.run(source.expand(_bare_gig(), "2026-10-01", "2026-10-02"))


def test_a_second_expand_makes_no_beat_read() -> None:
    calls: list[str] = []
    first = beat_expand(DETAIL_HTML, calls)
    second = beat_expand(DETAIL_HTML, calls)
    assert [g.time for g in first] == [g.time for g in second] == ["8:30pm"]
    assert len(calls) == 1


def test_a_beat_page_with_no_time_caches_an_empty_string() -> None:
    calls: list[str] = []
    assert [g.time for g in beat_expand("<html></html>", calls)] == [None]
    assert [g.time for g in beat_expand("<html></html>", calls)] == [None]
    assert len(calls) == 1
    assert beat.EVENTS.load("act") == ""


def test_a_beat_source_error_saves_no_event_file() -> None:
    beat_expand(sources.SourceError("down"), [])
    assert not beat.EVENTS.path("act").exists()
