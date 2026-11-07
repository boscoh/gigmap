"""Tests for the City of Melbourne What's On source.

The tests check the request params, the card parse, the session parse across a
year, the time text, the ticket, the venue address, a wrong url, and the date
range filter. They call the parse functions with saved HTML, so they do not
touch the live site.
"""

import asyncio

import pytest

from bs4 import BeautifulSoup

from gigmap import sources, whatson

SEARCH_PAGE = """
<div data-listing-type="event" data-track-listing="...">
  <a class="main-link" data-gtm-variable-label="The Vaudeville Revue"
     href="/things-to-do/the-vaudeville-revue">The Vaudeville Revue</a>
  <img class="page_image" src="/rails/active_storage/vaudeville.jpg"
       alt="A cabaret stage">
  <p class="summary">A cabaret revue.</p>
  <ul class="tag-list">
    <li><a class="link--tag">Theatre</a></li>
    <li><a class="link--tag">Comedy</a></li>
  </ul>
  <time datetime="2026-11-22">22 Nov</time>
</div>
<div data-listing-type="event" data-track-listing="...">
  <a class="main-link" data-gtm-variable-label="Free Jazz in the Park"
     href="/things-to-do/free-jazz">Free Jazz in the Park</a>
  <img class="page_image" src="/rails/active_storage/jazz.jpg" alt="A saxophone">
  <ul class="tag-list">
    <li><a class="link--tag">Music</a></li>
    <li><a class="link--tag">Free</a></li>
  </ul>
  <time datetime="2026-11-07">7 Nov</time>
</div>
"""

TICKETED_EVENT = """
<script type="application/ld+json">
{"@type": "Event",
 "name": "The Vaudeville Revue",
 "image": "https://whatson.melbourne.vic.gov.au/rails/active_storage/vaudeville.jpg",
 "startDate": "2026-08-07",
 "endDate": "2027-01-02",
 "eventStatus": "scheduled",
 "location": {"name": "The Butterfly Club",
              "address": {"streetAddress": "191 Collins St",
                          "addressLocality": "Melbourne",
                          "postalCode": "3000"}}}
</script>
<table id="date-times-table">
  <tr class="">
    <th>Sat 7 Nov</th>
    <td><ul><li>
      <span aria-hidden="true">7.30pm - 9.45pm</span>
      <span class="sr-only">7:30pm - 9:45pm</span>
    </li></ul></td>
  </tr>
  <tr class="">
    <th>Sun 8 Nov</th>
    <td><ul><li>
      <span aria-hidden="true">1.00pm - 2.30pm</span>
      <span class="sr-only">1:00pm - 2:30pm</span>
    </li><li>
      <span aria-hidden="true">7.30pm - 9.45pm</span>
      <span class="sr-only">7:30pm - 9:45pm</span>
    </li></ul></td>
  </tr>
  <tr class="">
    <th>Sat 2 Jan</th>
    <td><ul><li>
      <span aria-hidden="true">7.30pm - 9.45pm</span>
      <span class="sr-only">7:30pm - 9:45pm</span>
    </li></ul></td>
  </tr>
</table>
<div class="price-and-bookings">
  <table class="price-table">
    <tr><td>Adult / general $79</td></tr>
    <tr><td>Senior $90.73</td></tr>
  </table>
  <a href="https://tickets.example.com/vaudeville?utm_source=whatson">Book online</a>
</div>
"""

FREE_EVENT = """
<script type="application/ld+json">
{"@type": "Event",
 "name": "Free Jazz in the Park",
 "startDate": "2026-11-07",
 "endDate": "2026-11-08",
 "eventStatus": "scheduled",
 "location": {"name": "Fed Square",
              "address": {"streetAddress": "Swanston St",
                          "addressLocality": "Melbourne",
                          "postalCode": "3000"}}}
</script>
<table id="date-times-table">
  <tr class="">
    <th>Sat 7 Nov</th>
    <td><ul><li>
      <span aria-hidden="true">2.00pm - 4.00pm</span>
      <span class="sr-only">2:00pm - 4:00pm</span>
    </li></ul></td>
  </tr>
</table>
<div class="price-and-bookings">
  <p>This is a free event</p>
  <p>Booking not required</p>
</div>
"""


def card(title: str) -> sources.Gig:
    """Return the card gig of one title from the saved search page."""
    for gig in whatson.parse_cards(SEARCH_PAGE):
        if gig.artist == title:
            return gig
    raise AssertionError(f"no card for {title}")


def test_the_request_holds_the_three_categories_and_the_date() -> None:
    params = whatson.search_params("2026-11-06", "2026-11-08")
    pairs = dict(params)
    assert pairs["c"] == "things_to_do"
    assert pairs["dkw"] == "specific_dates"
    assert pairs["date_range"] == "2026-11-06 to 2026-11-08"
    assert pairs["q"] == ""
    # Each subcategory is its own key, so the params hold the key three
    # times. A dict of the params keeps one only, and the site would
    # answer one category.
    assert [value for key, value in params if key == "scs[]"] == [
        "theatre",
        "music",
        "comedy",
    ]


def test_the_card_parse_reads_the_gig_fields() -> None:
    gig = card("The Vaudeville Revue")
    assert gig.detail_url == (
        "https://whatson.melbourne.vic.gov.au/things-to-do/the-vaudeville-revue"
    )
    assert gig.categories == ["Theatre", "Comedy"]
    assert gig.image_alt == "A cabaret stage"
    assert gig.date is None  # the card time is not trusted


def test_the_session_parse_reads_across_the_year() -> None:
    dates = whatson.session_dates("Sat 2 Jan", "2026-11-06", "2027-01-31")
    assert dates == ["2027-01-02"]


def test_the_session_parse_needs_a_day_in_the_range() -> None:
    assert whatson.session_dates("Sat 7 Nov", "2026-11-07", "2026-11-07") == [
        "2026-11-07"
    ]
    assert whatson.session_dates("Sat 7 Nov", "2026-11-08", "2026-11-08") == []


def test_the_time_text_strips_the_end_time() -> None:
    assert whatson.time_text("7:30pm - 9:45pm") == "7:30pm"
    assert whatson.time_text("9am - 10am") == "9am"
    assert whatson.time_text(None) is None


def test_a_ticketed_event_holds_one_gig_per_session_day() -> None:
    page = BeautifulSoup(TICKETED_EVENT, "lxml")
    sessions = whatson.parse_sessions(page)
    assert [day for day, _ in sessions][:2] == ["Sat 7 Nov", "Sun 8 Nov"]


def test_a_day_with_two_shows_keeps_the_first_time() -> None:
    page = BeautifulSoup(TICKETED_EVENT, "lxml")
    sessions = dict(whatson.parse_sessions(page))
    assert sessions["Sun 8 Nov"] == "1:00pm"


def test_the_ticketed_event_holds_the_ticket_and_the_venue() -> None:
    gigs = whatson.parse_event(
        TICKETED_EVENT, card("The Vaudeville Revue"), "2026-11-07", "2026-11-08"
    )
    assert [gig.date for gig in gigs] == ["2026-11-07", "2026-11-08"]
    gig = gigs[0]
    assert gig.venue == "The Butterfly Club"
    ticket = gig.ticket
    assert ticket is not None
    assert ticket.group is sources.TicketGroup.ON_SALE
    assert ticket.price == "Adult / general $79"
    assert ticket.price_min == 79.0
    assert ticket.price_max == 79.0
    assert ticket.url == "https://tickets.example.com/vaudeville?utm_source=whatson"
    assert gig.time == "7:30pm"


def test_a_free_event_holds_the_free_group() -> None:
    gigs = whatson.parse_event(
        FREE_EVENT, card("Free Jazz in the Park"), "2026-11-07", "2026-11-07"
    )
    assert [gig.date for gig in gigs] == ["2026-11-07"]
    ticket = gigs[0].ticket
    assert ticket is not None
    assert ticket.group is sources.TicketGroup.FREE
    assert ticket.url is None


def test_a_session_outside_the_range_is_dropped() -> None:
    gigs = whatson.parse_event(
        TICKETED_EVENT, card("The Vaudeville Revue"), "2026-11-07", "2026-11-08"
    )
    dates = {gig.date for gig in gigs}
    # The 2 Jan session of the run falls outside the range and is dropped.
    assert dates == {"2026-11-07", "2026-11-08"}


def test_the_venue_address_round_trips() -> None:
    url = whatson.venue_url(
        "The Butterfly Club", "191 Collins St", "Melbourne", "3000"
    )
    assert url is not None
    assert whatson.decode_venue(url) == (
        "The Butterfly Club",
        "191 Collins St, Melbourne 3000",
    )


def test_no_venue_holds_no_address() -> None:
    assert whatson.venue_url(None, None, None, None) is None


def test_a_foreign_address_is_not_a_venue() -> None:
    assert whatson.decode_venue("https://example.com/venue/A/B/") is None
    assert whatson.decode_venue(f"{whatson.SITE_URL}/things-to-do/x/") is None


def test_the_detail_read_checks_the_url() -> None:
    good = "https://whatson.melbourne.vic.gov.au/things-to-do/a-show"
    assert whatson.DETAIL_URL_RE.fullmatch(good) is not None
    assert whatson.DETAIL_URL_RE.fullmatch("https://example.com/a/") is None


def test_a_price_in_a_paragraph_is_read_when_the_table_is_empty() -> None:
    page = """
    <div class="price-and-bookings">
      <p>From $57 to $346</p>
      <table class="price-table"><tbody></tbody></table>
      <a href="https://tickets.example.com/x">Book online</a>
    </div>
    """
    ticket = whatson.parse_ticket(page)
    assert ticket is not None
    assert ticket.group is sources.TicketGroup.ON_SALE
    assert ticket.price == "From $57 to $346"
    assert ticket.price_min == 57.0
    assert ticket.price_max == 346.0


def test_the_venue_read_uses_the_address() -> None:
    url = whatson.venue_url(
        "The Butterfly Club", "191 Collins St", "Melbourne", "3000"
    )
    assert url is not None

    async def run() -> sources.Venue:
        async with whatson.WhatsonSource() as source:
            return await source.venue(url)

    venue = asyncio.run(run())
    assert venue.name == "The Butterfly Club"
    assert venue.address == "191 Collins St, Melbourne 3000"
    assert venue.latitude is None


def test_a_wrong_venue_url_stops_the_read() -> None:
    async def run() -> None:
        async with whatson.WhatsonSource() as source:
            await source.venue("https://example.com/venue/A/")

    with pytest.raises(sources.SourceUrlError):
        asyncio.run(run())


def stub_source(pages: dict[str, str]) -> whatson.WhatsonSource:
    """Return a source whose page reads come from `pages`; a missing url fails."""
    source = whatson.WhatsonSource()

    async def fake_text(url: str, params=None) -> str:
        if url not in pages:
            raise sources.SourceError("boom")
        return pages[url]

    source._text = fake_text  # type: ignore[method-assign]
    return source


def test_expand_gives_one_gig_per_session() -> None:
    url = "https://whatson.melbourne.vic.gov.au/things-to-do/the-vaudeville-revue"
    source = stub_source({url: TICKETED_EVENT})
    gigs = asyncio.run(
        source.expand(
            sources.Gig(artist="The Vaudeville Revue", detail_url=url),
            "2026-11-07",
            "2027-01-02",
        )
    )
    assert [gig.date for gig in gigs] == ["2026-11-07", "2026-11-08", "2027-01-02"]


def test_a_failed_event_page_drops_that_event_only() -> None:
    base = "https://whatson.melbourne.vic.gov.au/things-to-do/"
    good = sources.Gig(artist="Good", detail_url=base + "good")
    bad = sources.Gig(artist="Bad", detail_url=base + "bad")
    source = stub_source({base + "good": TICKETED_EVENT})

    async def run() -> list[list[sources.Gig]]:
        return await asyncio.gather(
            *(source.expand(g, "2026-11-07", "2026-11-08") for g in (bad, good))
        )

    failed, ok = asyncio.run(run())
    assert failed == []
    assert len(ok) == 2


def expand_ranges(
    source: whatson.WhatsonSource,
    page: str,
    title: str,
    ranges: list[tuple[str, str]],
    calls: list[str],
) -> list[list[sources.Gig]]:
    async def text(url: str, *_a: object, **_k: object) -> str:
        calls.append(url)
        return page

    source._text = text  # type: ignore[method-assign]
    gig = card(title)

    async def run() -> list[list[sources.Gig]]:
        return [await source.expand(gig, s, e) for s, e in ranges]

    return asyncio.run(run())


def test_read_event_gives_the_raw_session_pairs() -> None:
    event = whatson.read_event(TICKETED_EVENT)
    assert event is not None
    assert event.sessions[:2] == [("Sat 7 Nov", "7:30pm"), ("Sun 8 Nov", "1:00pm")]
    assert event.venue_name == "The Butterfly Club"
    assert whatson.read_event("<html></html>") is None


def test_fit_event_matches_parse_event() -> None:
    event = whatson.read_event(TICKETED_EVENT)
    assert event is not None
    the_card = card("The Vaudeville Revue")
    fitted = whatson.fit_event(event, the_card, "2026-11-07", "2026-11-08")
    parsed = whatson.parse_event(TICKETED_EVENT, the_card, "2026-11-07", "2026-11-08")
    assert fitted == parsed
    assert len(fitted) == 2


def test_overlapping_ranges_share_one_whatson_read() -> None:
    calls: list[str] = []
    both, second = expand_ranges(
        whatson.WhatsonSource(),
        TICKETED_EVENT,
        "The Vaudeville Revue",
        [("2026-11-07", "2026-11-08"), ("2026-11-08", "2026-11-08")],
        calls,
    )
    assert len(calls) == 1
    assert [g.date for g in both] == ["2026-11-07", "2026-11-08"]
    assert [g.date for g in second] == ["2026-11-08"]
    assert both[0].ticket is not None and both[0].ticket == second[0].ticket


def test_a_page_with_no_json_ld_saves_nothing() -> None:
    calls: list[str] = []
    (gigs,) = expand_ranges(
        whatson.WhatsonSource(),
        "<html></html>",
        "The Vaudeville Revue",
        [("2026-11-07", "2026-11-08")],
        calls,
    )
    assert gigs == []
    assert not list(whatson.EVENTS.directory().glob("*.json"))
