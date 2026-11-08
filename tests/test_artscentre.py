"""Tests for the Arts Centre Melbourne source.

The tests check the answer parse, the date windows, and the venue address.
They do not touch the live site.
"""

import asyncio

import pytest

from gigmap import artscentre, sources


def row(**fields: object) -> dict:
    """Return one calendar row with the given fields replaced."""
    base = {
        "title": "Brahms Festival",
        "url": "/whats-on/2026/seasons/mso/brahms-festival",
        "bookNowUrl": "https://premier.ticketek.com.au/shows/Show.aspx?sh=X",
        "bookNowText": "Book tickets",
        "bookNowVisible": True,
        "label": "",
        "venue": "Hamer Hall",
        "category": "Classical Music",
        "categories": ["Classical Music"],
        "eventDateValue": "2026-11-14T00:00:00",
        "eventTimeValue": "2026-11-14T19:30:00+11:00",
        "desktopImage": "/-/media/acm/one.jpg",
    }
    base.update(fields)
    return base


def answer(*rows: dict) -> dict:
    """Return one endpoint answer with the given rows."""
    return {"events": list(rows), "totalResults": len(rows)}


def pascal(obj: dict) -> dict:
    """Return one object with an upper first letter on each key."""
    return {key[:1].upper() + key[1:]: value for key, value in obj.items()}

def test_a_summer_stamp_reads_the_local_time() -> None:
    assert artscentre.time_text("2026-11-14T19:30:00+11:00") == "7:30pm"

def test_the_winter_offset_keeps_the_local_hour() -> None:
    """The site writes the offset of the season, and the hour is local."""
    assert artscentre.time_text("2026-06-14T19:30:00+10:00") == "7:30pm"


def test_the_clock_edges() -> None:
    assert artscentre.time_text("2026-11-14T00:05:00+11:00") == "12:05am"
    assert artscentre.time_text("2026-11-14T12:00:00+11:00") == "12:00pm"
    assert artscentre.time_text("2026-11-14T09:05:00+11:00") == "9:05am"


def test_a_stamp_with_no_time_of_day_holds_no_time() -> None:
    assert artscentre.time_text("2026-11-14T00:00:00+11:00") is None


def test_a_damaged_stamp_holds_nothing() -> None:
    assert artscentre.time_text(None) is None
    assert artscentre.time_text("") is None
    assert artscentre.time_text("14/11/2026") is None


def test_the_day_comes_from_the_value_field() -> None:
    """`eventDate` is M/D/YYYY, so the module reads `eventDateValue`."""
    assert artscentre.split_day("2026-11-08T00:00:00") == "2026-11-08"
    assert artscentre.split_day(None) is None


def test_a_relative_path_becomes_a_full_url() -> None:
    site = "https://www.artscentremelbourne.com.au"
    assert artscentre.absolute_url("/whats-on/x") == f"{site}/whats-on/x"
    assert artscentre.absolute_url("whats-on/x") == f"{site}/whats-on/x"
    assert artscentre.absolute_url("https://img.test/a.jpg") == "https://img.test/a.jpg"
    assert artscentre.absolute_url(None) is None


def test_a_row_becomes_a_gig() -> None:
    gig = artscentre.parse_row(row())
    assert gig is not None
    assert gig.artist == "Brahms Festival"
    assert gig.date == "2026-11-14"
    assert gig.time == "7:30pm"
    assert gig.venue == "Hamer Hall"
    assert gig.genres == ["Classical Music"]
    assert gig.categories == ["Classical Music"]
    assert gig.image_url is not None
    assert gig.image_url.endswith("/-/media/acm/one.jpg")
    assert gig.detail_url is not None
    assert gig.detail_url.endswith("/whats-on/2026/seasons/mso/brahms-festival")



def test_the_two_key_spellings_read_the_same() -> None:
    """The `Accept` header picks `events` or `Events`, and both must work."""
    camel = artscentre.parse_answer(answer(row()), "2026-11-14", "2026-11-14")
    upper = artscentre.parse_answer(
        pascal(answer(pascal(row()))), "2026-11-14", "2026-11-14"
    )
    assert upper == camel
    assert camel[0].artist == "Brahms Festival"
    assert camel[0].time == "7:30pm"
    assert camel[0].ticket is not None

def test_a_row_holds_one_day_of_the_run() -> None:
    """The endpoint answers one row for each day, and each row is one gig."""
    gigs = artscentre.parse_answer(
        answer(
            row(eventDateValue="2026-11-14T00:00:00"),
            row(eventDateValue="2026-11-15T00:00:00"),
        ),
        "2026-11-14",
        "2026-11-15",
    )
    assert [gig.date for gig in gigs] == ["2026-11-14", "2026-11-15"]


def test_an_exhibition_is_not_a_gig() -> None:
    """An exhibition holds a row for each open day and fills the calendar."""
    gigs = artscentre.parse_answer(
        answer(
            row(title="Encore", category="Exhibition", categories=["Exhibition"]),
            row(title="A tour", category="Guided Tour", categories=["Guided Tour"]),
            row(title="A show"),
        ),
        "2026-11-14",
        "2026-11-14",
    )
    assert [gig.artist for gig in gigs] == ["A show"]


def test_the_answer_keeps_the_range() -> None:
    gigs = artscentre.parse_answer(
        answer(
            row(eventDateValue="2026-11-13T00:00:00"),
            row(eventDateValue="2026-11-14T00:00:00"),
            row(eventDateValue="2026-11-20T00:00:00"),
        ),
        "2026-11-14",
        "2026-11-16",
    )
    assert [gig.date for gig in gigs] == ["2026-11-14"]


def test_an_answer_without_a_list_stops_the_read() -> None:
    with pytest.raises(sources.SourceError):
        artscentre.parse_answer({"totalResults": 0}, "2026-11-14", "2026-11-14")


def test_the_windows_cover_the_range_once() -> None:
    assert artscentre.window_ends("2026-11-14", "2026-11-14") == [
        ("2026-11-14", "2026-11-14")
    ]
    assert artscentre.window_ends("2026-11-14", "2026-11-16") == [
        ("2026-11-14", "2026-11-16")
    ]


def test_a_long_range_needs_more_than_one_window() -> None:
    windows = artscentre.window_ends("2026-11-01", "2027-06-01")
    assert len(windows) == 2
    assert windows[0][0] == "2026-11-01"
    assert windows[-1][1] == "2027-06-01"
    # Each window stays under the limit of the site, and no day repeats.
    assert windows[0][1] < windows[1][0]
    for first, last in windows:
        span = artscentre.datetime.date.fromisoformat(last)
        span -= artscentre.datetime.date.fromisoformat(first)
        assert span.days < 180


def test_a_damaged_range_stops_the_read() -> None:
    with pytest.raises(sources.SourceError):
        artscentre.window_ends("14/11/2026", "2026-11-16")
    with pytest.raises(sources.SourceError):
        artscentre.window_ends("2026-11-16", "2026-11-14")


def test_a_booking_address_makes_a_button() -> None:
    ticket = artscentre.ticket_of(row())
    assert ticket is not None
    assert ticket.url is not None
    assert ticket.group is sources.TicketGroup.ON_SALE
    assert ticket.label == "Book tickets"


def test_a_note_makes_a_label() -> None:
    ticket = artscentre.ticket_of(row(bookNowUrl="#", label="New Show!"))
    assert ticket is not None
    assert ticket.url is None
    assert ticket.label == "New Show!"


def test_no_address_and_no_note_holds_no_ticket() -> None:
    """A badge with no action misleads, so the row holds no ticket."""
    assert artscentre.ticket_of(row(bookNowUrl="#")) is None


def test_a_free_event_holds_the_free_group() -> None:
    ticket = artscentre.ticket_of(
        row(bookNowUrl="#", categories=["Classical Music", "Free Event"])
    )
    assert ticket is not None
    assert ticket.group is sources.TicketGroup.FREE


def test_the_venue_address_round_trips() -> None:
    url = artscentre.venue_url("Sidney Myer Music Bowl")
    assert url is not None
    assert artscentre.decode_venue(url) == "Sidney Myer Music Bowl"


def test_the_cache_names_a_venue_after_its_name() -> None:
    url = artscentre.venue_url("Hamer Hall")
    assert url is not None
    assert sources.url_key(url).startswith("hamer-hall-")


def test_no_venue_holds_no_address() -> None:
    assert artscentre.venue_url(None) is None
    assert artscentre.venue_url("") is None


def test_a_festival_of_many_spaces_holds_no_venue() -> None:
    """`Various` is not a place, so a pin of it would be a false pin."""
    gig = artscentre.parse_row(row(venue="Various"))
    assert gig is not None
    assert gig.venue == "Various"
    assert gig.venue_url is None


def test_a_foreign_address_is_not_a_venue() -> None:
    assert artscentre.decode_venue("https://example.com/venue/Hamer%20Hall/") is None
    site = "https://www.artscentremelbourne.com.au"
    assert artscentre.decode_venue(f"{site}/whats-on/x") is None


def test_the_venue_read_uses_the_address_table() -> None:
    name = "Sidney Myer Music Bowl"
    url = artscentre.venue_url(name)
    assert url is not None

    async def run() -> sources.Venue:
        async with artscentre.ArtsCentreSource() as source:
            return await source.venue(url)

    venue = asyncio.run(run())
    assert venue.name == name
    assert venue.address == "21 Linlithgow Ave, Melbourne VIC 3000"
    assert venue.latitude is None
