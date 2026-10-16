"""Tests for the Melbourne Fringe source.

The tests use a fake transport, so they do not touch the live site.
"""

import asyncio
import json

import httpx
import pytest

from gigmap import fringe, sources

EVENT_URL = "https://www.melbournefringe.com.au/whats-on/events/briefs-the-works/"
VENUE_URL = "https://www.melbournefringe.com.au/whats-on/venues/ellora/"


def diary_row(
    *,
    event_id: str = "e1",
    name: str = "Briefs: The Works",
    route: str = "/whats-on/events/briefs-the-works/?utm_source=test",
    venue: str = "Ellora",
    stamp: str = "2026-10-17T19:00:00Z",
    accessibilities: list[dict] | None = None,
    **performance: object,
) -> dict:
    """Return one row of a DailyDiary day payload."""
    details = {
        "id": "p1",
        "date": stamp,
        "duration": 60,
        "onDemand": False,
        "cancelled": False,
        "status": "Available",
    }
    details.update(performance)
    return {
        "id": event_id,
        "name": name,
        "route": route,
        "venue": venue,
        "town": "Melbourne",
        "unticketed": False,
        "isAdvertised": False,
        "isPastEvent": False,
        "image": {"imageUrl": "https://img.test/1.jpg", "credit": "Aitor"},
        "isCommonRoomEvent": False,
        "accessibilities": (
            accessibilities
            if accessibilities is not None
            else [{"name": "Wheelchair Accessible"}]
        ),
        "performance": details,
    }


def day(*rows: dict, name: str = "7pm") -> list[dict]:
    """Return one DailyDiary day payload group."""
    return [{"name": name, "events": list(rows)}]


def event_row(
    *,
    event_id: str = "e1",
    genre: str | None = "Cabaret",
    price: float | None = 55.0,
    max_price: float | None = 69.0,
    override: str | None = None,
) -> dict:
    """Return one row of the whatsOnEvent content list."""
    genres = [{"name": genre}] if genre is not None else []
    return {
        "id": event_id,
        "properties": {
            "genre": genres,
            "price": price,
            "maxPrice": max_price,
            "priceRangeOverride": override,
        },
    }


def venue_row(
    *, name: str = "Ellora", path: str = "/whats-on/venues/ellora/"
) -> dict:
    """Return one row of the venue list."""
    return {
        "contentType": "whatsOnVenue",
        "name": name,
        "route": {"path": path},
        "properties": {
            "address1": "1 Fitzroy St",
            "address2": None,
            "town": "St Kilda",
            "postcode": "3182",
            "latitude": "-37.86264028522469",
            "longitude": "144.97319945649303",
        },
    }


class FakeSite:
    """A fake melbournefringe.com.au that answers the three reads."""

    def __init__(self) -> None:
        self.days: dict[str, list[dict]] = {}
        self.events: list[dict] = []
        self.venues: list[dict] = []
        self.calls: list[tuple[str, str]] = []
        # The filter of each delivery API read, in order.
        self.filters: list[str] = []
        self.diary_status = 200
        self.event_status = 200
        self.venue_status = 200

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            self.calls.append((request.method, path))
            if path == "/api/WhatsOn/DailyDiary":
                if self.diary_status != 200:
                    return httpx.Response(self.diary_status)
                body = json.loads(request.content)
                groups = self.days.get(body["day"], [])
                return httpx.Response(
                    200, json={"status": 200, "result": {"eventGroups": groups}}
                )
            if path == "/umbraco/delivery/api/v2/content":
                query = dict(request.url.params)
                content_filter = query.get("filter", "")
                self.filters.append(content_filter)
                if "whatsOnEvent" in content_filter:
                    if self.event_status != 200:
                        return httpx.Response(self.event_status)
                    rows = self.events
                else:
                    if self.venue_status != 200:
                        return httpx.Response(self.venue_status)
                    rows = self.venues
                take = int(query.get("take", "100"))
                skip = int(query.get("skip", "0"))
                return httpx.Response(
                    200,
                    json={
                        "total": len(rows),
                        "items": rows[skip : skip + take],
                    },
                )
            return httpx.Response(404)

        return httpx.MockTransport(handle)

    def read_gigs(self, start_date: str, end_date: str) -> list[sources.Gig]:
        async def run() -> list[sources.Gig]:
            async with fringe.FringeSource(self.transport()) as source:
                return await source.gigs(start_date, end_date)

        return asyncio.run(run())


def read_venue(venue_url: str, site: FakeSite) -> sources.Venue:
    async def run() -> sources.Venue:
        async with fringe.FringeSource(site.transport()) as source:
            return await source.venue(venue_url)

    return asyncio.run(run())


def test_days_of_range_holds_each_day() -> None:
    assert fringe.days_of_range("2026-10-17", "2026-10-19") == [
        "2026-10-17",
        "2026-10-18",
        "2026-10-19",
    ]
    assert fringe.days_of_range("2026-10-17", "2026-10-17") == ["2026-10-17"]


def test_a_damaged_range_stops_the_read() -> None:
    with pytest.raises(sources.SourceError):
        fringe.days_of_range("17/10/2026", "2026-10-19")


def test_a_reversed_range_stops_the_read() -> None:
    with pytest.raises(sources.SourceError):
        fringe.days_of_range("2026-10-19", "2026-10-17")


def test_a_long_range_stops_the_read() -> None:
    with pytest.raises(sources.SourceError):
        fringe.days_of_range("2026-10-17", "2028-10-17")


def test_local_time_text_reads_the_local_hour() -> None:
    """The API writes a trailing Z, and the value is local time."""
    assert fringe.local_time_text("2026-10-17T19:00:00Z") == "7:00pm"
    assert fringe.local_time_text("2026-10-17T07:30:00Z") == "7:30am"
    assert fringe.local_time_text("2026-10-17T00:05:00Z") == "12:05am"
    assert fringe.local_time_text("2026-10-17T12:00:00Z") == "12:00pm"
    assert fringe.local_time_text(None) is None
    assert fringe.local_time_text("2026-10-17") is None


def test_time_order_puts_a_missing_time_last() -> None:
    assert fringe.time_order("7:00pm") == 19 * 60
    assert fringe.time_order("12:30am") == 30
    assert fringe.time_order("12:00pm") == 12 * 60
    assert fringe.time_order(None) == 24 * 60


def test_event_page_url_drops_the_tracking_query() -> None:
    assert fringe.event_page_url("/whats-on/events/briefs/?utm_source=x") == (
        "https://www.melbournefringe.com.au/whats-on/events/briefs/"
    )
    assert fringe.event_page_url("/whats-on/venues/ellora/") is None
    assert fringe.event_page_url(None) is None


def test_a_slug_can_hold_a_non_ascii_character() -> None:
    assert fringe.event_page_url("/whats-on/events/obang-오방/") == (
        "https://www.melbournefringe.com.au/whats-on/events/obang-오방/"
    )


def test_price_text_forms_the_text_of_the_site() -> None:
    """The site shows a range, one price, or the word Free."""
    assert fringe.price_text({"price": 55.0, "maxPrice": 69.0}) == (
        "$55.00 - $69.00"
    )
    assert fringe.price_text({"price": 55.0, "maxPrice": 55.0}) == "$55.00"
    assert fringe.price_text({"price": 15.0}) == "$15.00"
    assert fringe.price_text({"price": 0, "maxPrice": 0}) == "Free"
    assert fringe.price_text({"price": 0}) == "Free"
    assert fringe.price_text({"price": None, "maxPrice": 10.0}) is None
    assert fringe.price_text({}) is None


def test_price_text_rounds_a_price_as_the_site_rounds_it() -> None:
    """The site rounds to the nearest even dollar, as .NET does."""
    assert fringe.price_text({"price": 24.5, "maxPrice": 24.5}) == "$24.00"
    assert fringe.price_text({"price": 27.5, "maxPrice": 27.5}) == "$28.00"
    assert fringe.price_text({"price": 29.99, "maxPrice": 29.99}) == "$30.00"


def test_price_text_uses_the_preview_price_as_the_low_value() -> None:
    """A preview performance costs less than the run price."""
    run = {"price": 35.0, "maxPrice": 35.0, "preview": True}
    assert fringe.price_text(run) == "$35.00"
    assert fringe.price_text({**run, "previewPrice": 25.0}) == (
        "$25.00 - $35.00"
    )
    # A preview with no preview price keeps the run price.
    assert fringe.price_text({**run, "previewPrice": None}) == "$35.00"


def test_ticket_link_answers_the_booking_anchor_of_a_paid_gig() -> None:
    assert fringe.ticket_link(EVENT_URL, "$30.00") == (
        f"{EVENT_URL}#sessions-book-now"
    )
    assert fringe.ticket_link(EVENT_URL, "$25.00 - $35.00") == (
        f"{EVENT_URL}#sessions-book-now"
    )


def test_ticket_link_is_empty_for_a_free_gig() -> None:
    """A free gig needs no booking link."""
    assert fringe.ticket_link(EVENT_URL, "Free") is None


def test_ticket_link_is_empty_without_a_price_or_a_page() -> None:
    assert fringe.ticket_link(EVENT_URL, None) is None
    assert fringe.ticket_link(None, "$30.00") is None


def test_price_text_prefers_the_override() -> None:
    assert fringe.price_text(
        {"price": 55.0, "maxPrice": 69.0, "priceRangeOverride": "By donation"}
    ) == "By donation"


def test_an_event_fact_row_holds_the_genre_and_the_price() -> None:
    facts = fringe.parse_event_facts(event_row(price=0.0, max_price=0.0))
    assert facts.genres == ["Cabaret"]
    assert facts.price == "Free"


def test_an_event_fact_row_survives_a_damaged_shape() -> None:
    facts = fringe.parse_event_facts({"id": "e1"})
    assert facts.genres == []
    assert facts.price is None


def test_a_diary_row_becomes_a_gig() -> None:
    row = diary_row()
    gig = fringe.parse_performance(
        row,
        "2026-10-17",
        {"ellora": sources.Venue(name="Ellora", venue_url=VENUE_URL)},
        {"e1": fringe.parse_event_facts(event_row())},
    )

    assert gig is not None
    assert gig.artist == "Briefs: The Works"
    assert gig.detail_url == EVENT_URL
    assert gig.date == "2026-10-17"
    assert gig.venue == "Ellora"
    assert gig.venue_url == VENUE_URL
    assert gig.genres == ["Cabaret"]
    assert gig.categories == ["Wheelchair Accessible"]
    assert gig.image_url == "https://img.test/1.jpg"
    assert gig.time == "7:00pm"
    assert gig.price == "$55.00 - $69.00"
    # A paid gig carries the booking anchor of the event page.
    assert gig.ticket_url == f"{EVENT_URL}#sessions-book-now"
    assert gig.status == "Available"


def test_a_sold_out_row_keeps_the_ticket_state() -> None:
    """The state belongs to one performance, so one day can sell out alone."""
    gig = fringe.parse_performance(
        diary_row(status="SoldOut"), "2026-10-17", {}, {}
    )
    assert gig is not None
    assert gig.status == "SoldOut"


def test_a_row_without_a_state_holds_no_status() -> None:
    gig = fringe.parse_performance(diary_row(status=None), "2026-10-17", {}, {})
    assert gig is not None
    assert gig.status is None


def test_a_gig_without_an_event_row_holds_no_genre_or_price() -> None:
    gig = fringe.parse_performance(diary_row(), "2026-10-17", {}, {})
    assert gig is not None
    assert gig.genres == []
    assert gig.price is None
    assert gig.venue_url is None
    # A gig without a price shows no price to click, so it holds no link.
    assert gig.ticket_url is None


def test_a_cancelled_or_on_demand_row_is_not_a_gig() -> None:
    """The On Demand group answers for every day of the festival."""
    day_text = "2026-10-17"
    assert fringe.parse_performance(
        diary_row(cancelled=True), day_text, {}, {}
    ) is None
    assert fringe.parse_performance(
        diary_row(onDemand=True, stamp="2026-10-14T00:00:00Z"), day_text, {}, {}
    ) is None


def test_a_row_of_another_day_is_not_a_gig() -> None:
    assert (
        fringe.parse_performance(
            diary_row(stamp="2026-10-18T19:00:00Z"), "2026-10-17", {}, {}
        )
        is None
    )


def test_a_row_without_a_performance_is_not_a_gig() -> None:
    row = diary_row()
    row["performance"] = None
    assert fringe.parse_performance(row, "2026-10-17", {}, {}) is None


def test_a_group_without_events_is_skipped() -> None:
    """The last group of a day can hold a name and no `events` key."""
    payload = {"eventGroups": [{"name": "11pm"}, *day(diary_row())]}
    gigs = fringe.parse_diary(payload, "2026-10-17", {}, {})
    assert len(gigs) == 1


def test_the_source_reads_the_gigs_of_a_range() -> None:
    site = FakeSite()
    site.venues = [venue_row()]
    site.events = [event_row()]
    site.days = {
        "2026-10-17": day(diary_row(stamp="2026-10-17T19:00:00Z")),
        "2026-10-18": day(
            diary_row(stamp="2026-10-18T09:00:00Z", name="Morning Show")
        ),
    }

    gigs = site.read_gigs("2026-10-17", "2026-10-18")

    assert [(gig.artist, gig.date, gig.time) for gig in gigs] == [
        ("Briefs: The Works", "2026-10-17", "7:00pm"),
        ("Morning Show", "2026-10-18", "9:00am"),
    ]
    assert gigs[0].venue_url == VENUE_URL
    assert gigs[0].genres == ["Cabaret"]
    assert ("POST", "/api/WhatsOn/DailyDiary") in site.calls
    assert ("GET", "/umbraco/delivery/api/v2/content") in site.calls
    assert site.filters == ["contentType:whatsOnEvent", "contentType:whatsOnVenue"]


def test_the_source_sorts_the_gigs_of_one_day() -> None:
    site = FakeSite()
    site.days = {
        "2026-10-17": day(
            diary_row(stamp="2026-10-17T21:00:00Z", name="Late"),
            diary_row(stamp="2026-10-17T07:00:00Z", name="Early"),
        )
    }

    gigs = site.read_gigs("2026-10-17", "2026-10-17")

    assert [gig.artist for gig in gigs] == ["Early", "Late"]


def test_the_event_list_answers_every_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The event list of a festival holds more rows than one page."""
    monkeypatch.setattr(fringe, "EVENT_PAGE_SIZE", 20)
    site = FakeSite()
    count = 25
    site.events = [event_row(event_id=f"e{index}") for index in range(count)]
    site.days = {
        "2026-10-17": day(diary_row(event_id=f"e{count - 1}", name="Last"))
    }

    gigs = site.read_gigs("2026-10-17", "2026-10-17")

    event_reads = [f for f in site.filters if "whatsOnEvent" in f]
    assert len(event_reads) == 2
    assert gigs[0].genres == ["Cabaret"]
    assert gigs[0].price == "$55.00 - $69.00"


def test_the_event_list_reads_one_time_for_the_process() -> None:
    site = FakeSite()
    site.events = [event_row()]
    site.days = {
        "2026-10-17": day(diary_row()),
        "2026-10-18": day(diary_row(stamp="2026-10-18T19:00:00Z")),
    }

    async def run() -> None:
        async with fringe.FringeSource(site.transport()) as source:
            await source.gigs("2026-10-17", "2026-10-18")
            await source.gigs("2026-10-17", "2026-10-18")

    asyncio.run(run())

    event_reads = [f for f in site.filters if "whatsOnEvent" in f]
    assert len(event_reads) == 1


def test_the_source_keeps_a_gig_without_a_genre_or_price_when_the_read_fails(
) -> None:
    site = FakeSite()
    site.event_status = 500
    site.days = {"2026-10-17": day(diary_row())}

    gigs = site.read_gigs("2026-10-17", "2026-10-17")

    assert len(gigs) == 1
    assert gigs[0].genres == []
    assert gigs[0].price is None
    assert gigs[0].time == "7:00pm"


def test_the_source_keeps_a_gig_without_a_venue_when_the_venue_list_fails() -> None:
    site = FakeSite()
    site.venue_status = 500
    site.days = {"2026-10-17": day(diary_row())}

    gigs = site.read_gigs("2026-10-17", "2026-10-17")

    assert len(gigs) == 1
    assert gigs[0].venue == "Ellora"
    assert gigs[0].venue_url is None


def test_a_bad_status_stops_the_gig_read() -> None:
    site = FakeSite()
    site.diary_status = 404
    site.days = {"2026-10-17": day(diary_row())}

    with pytest.raises(sources.SourceError):
        site.read_gigs("2026-10-17", "2026-10-17")


def test_the_source_reads_the_venue_list_one_time() -> None:
    site = FakeSite()
    site.venues = [venue_row()]
    site.days = {
        "2026-10-17": day(diary_row()),
        "2026-10-18": day(diary_row(stamp="2026-10-18T19:00:00Z")),
    }

    async def run() -> None:
        async with fringe.FringeSource(site.transport()) as source:
            await source.gigs("2026-10-17", "2026-10-18")
            await source.gigs("2026-10-17", "2026-10-18")

    asyncio.run(run())

    reads = [f for f in site.filters if "whatsOnVenue" in f]
    assert len(reads) == 1


def test_the_venue_read_holds_the_address_and_the_coordinates() -> None:
    site = FakeSite()
    site.venues = [venue_row()]

    venue = read_venue(VENUE_URL, site)

    assert venue.name == "Ellora"
    assert venue.address == "1 Fitzroy St, St Kilda, 3182"
    assert venue.latitude == pytest.approx(-37.86264028522469)
    assert venue.longitude == pytest.approx(144.97319945649303)


def test_the_venue_read_gives_a_copy() -> None:
    """The server adds the map links to the answer, so the row must not change."""
    site = FakeSite()
    site.venues = [venue_row()]

    async def run() -> None:
        async with fringe.FringeSource(site.transport()) as source:
            first = await source.venue(VENUE_URL)
            first.latitude = None
            second = await source.venue(VENUE_URL)
            assert second.latitude is not None

    asyncio.run(run())


def test_an_unknown_venue_answers_the_url_alone() -> None:
    site = FakeSite()

    venue = read_venue(VENUE_URL, site)

    assert venue.venue_url == VENUE_URL
    assert venue.name is None
    assert venue.latitude is None


def test_a_foreign_venue_url_is_rejected() -> None:
    site = FakeSite()
    with pytest.raises(sources.SourceUrlError):
        read_venue("https://beat.com.au/directory/venue/", site)
    with pytest.raises(sources.SourceUrlError):
        read_venue("https://www.melbournefringe.com.au/whats-on/events/x/", site)


def test_the_detail_read_leaves_the_time_empty() -> None:
    """The gig list holds the time, so the detail read must not replace it."""
    site = FakeSite()

    async def run() -> sources.GigDetail | None:
        async with fringe.FringeSource(site.transport()) as source:
            return await source.gig_detail(EVENT_URL)

    detail = asyncio.run(run())

    assert detail is not None
    assert detail.time is None
    assert site.calls == []


def test_a_foreign_detail_url_answers_none() -> None:
    site = FakeSite()

    async def run() -> sources.GigDetail | None:
        async with fringe.FringeSource(site.transport()) as source:
            return await source.gig_detail("https://beat.com.au/act/")

    assert asyncio.run(run()) is None


def test_the_page_read_holds_one_entry_for_each_day() -> None:
    site = FakeSite()
    site.days = {"2026-10-17": day(diary_row())}

    async def run() -> str:
        async with fringe.FringeSource(site.transport()) as source:
            return await source.page("2026-10-17", "2026-10-18")

    payloads = json.loads(asyncio.run(run()))

    assert len(payloads) == 2
    assert payloads[0]["eventGroups"]


def test_the_source_retries_a_server_error() -> None:
    calls = {"count": 0}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/api/WhatsOn/DailyDiary":
            return httpx.Response(200, json={"total": 0, "items": []})
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(503)
        return httpx.Response(
            200, json={"status": 200, "result": {"eventGroups": day(diary_row())}}
        )

    async def run() -> list[sources.Gig]:
        async with fringe.FringeSource(httpx.MockTransport(handle)) as source:
            return await source.gigs("2026-10-17", "2026-10-17")

    gigs = asyncio.run(run())

    assert len(gigs) == 1
    assert calls["count"] == 2


def test_the_source_does_not_retry_a_client_error() -> None:
    calls = {"count": 0}

    def handle(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(404)

    async def run() -> None:
        async with fringe.FringeSource(httpx.MockTransport(handle)) as source:
            await source.gigs("2026-10-17", "2026-10-17")

    with pytest.raises(sources.SourceError):
        asyncio.run(run())
    assert calls["count"] == 1


def test_a_damaged_answer_stops_the_read() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    async def run() -> None:
        async with fringe.FringeSource(httpx.MockTransport(handle)) as source:
            await source.gigs("2026-10-17", "2026-10-17")

    with pytest.raises(sources.SourceError):
        asyncio.run(run())


def test_the_source_needs_the_context_manager() -> None:
    async def run() -> None:
        await fringe.FringeSource().gigs("2026-10-17", "2026-10-17")

    with pytest.raises(RuntimeError):
        asyncio.run(run())
