"""Tests for the ArtsHub source.

The tests check the feed parse and the venue address. They do not touch the
live site.
"""

import asyncio
import xml.etree.ElementTree as ET
from datetime import date

import pytest

from gigmap import artshub, sources

ITEM = """
<item>
  <title>Kew Court House Live: THNDO</title>
  <link>https://www.artshub.com.au/event/kew-court-house-live-thndo-2877667/</link>
  <description>A night of music.</description>
  <startDateTime>2026-11-14 19:30:00</startDateTime>
  <endDateTime>2026-11-16 21:30:00</endDateTime>
  <venue>Kew Court House</venue>
  <venueAddress>188 High Street, Kew VIC, Australia</venueAddress>
  <locations>Victoria,Australia,Melbourne</locations>
  <classifications>Performances &amp; Gigs,Festivals</classifications>
  <art_forms>Performing Arts,Music</art_forms>
</item>
"""


def feed(*items: str) -> str:
    """Return one feed page with the given items."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Events</title>
{''.join(items)}
</channel></rss>"""


def item(**fields: str) -> str:
    """Return one feed item with the given fields replaced."""
    root = ET.fromstring(ITEM)
    for name, value in fields.items():
        node = root.find(name)
        if node is None:
            node = ET.SubElement(root, name)
        node.text = value
    return ET.tostring(root, encoding="unicode")


def test_stamp_reads_the_day_only() -> None:
    assert artshub.stamp_day("2026-11-14 19:30:00") == "2026-11-14"
    assert artshub.stamp_day("2026-10-23 00:00:00") == "2026-10-23"


def test_a_damaged_stamp_holds_nothing() -> None:
    assert artshub.stamp_day(None) is None
    assert artshub.stamp_day("23/10/2026") is None
    assert artshub.stamp_day("") is None


def test_a_known_venue_takes_the_table_address() -> None:
    """The feed address of this venue is one that Nominatim cannot read."""
    gig = artshub.parse_item(
        ET.fromstring(
            item(
                venue="Potter Museum of Art",
                venueAddress="Corner Swanston Street and, Masson Rd, Parkville",
            )
        )
    )
    assert gig is not None
    assert artshub.decode_venue(gig.venue_url or "") == (
        "Potter Museum of Art",
        "Masson Rd, Parkville VIC 3052",
    )


def test_item_reads_the_gig_fields() -> None:
    gig = artshub.parse_item(ET.fromstring(ITEM))
    assert gig is not None
    assert gig.artist == "Kew Court House Live: THNDO"
    assert gig.date == "2026-11-14"
    assert gig.end_date == "2026-11-16"
    assert gig.time is None  # the feed time is not the time of the show
    assert gig.venue == "Kew Court House"
    assert gig.genres == ["Performing Arts", "Music"]
    assert gig.categories == ["Performances & Gigs", "Festivals"]


def test_an_item_with_no_start_is_not_a_gig() -> None:
    """An event with no start date cannot fall in a date range."""
    root = ET.fromstring(item(startDateTime=""))
    assert artshub.parse_item(root) is None


def test_a_single_day_run_holds_no_end_date() -> None:
    root = ET.fromstring(item(endDateTime="2026-11-14 23:00:00"))
    gig = artshub.parse_item(root)
    assert gig is not None
    assert gig.end_date is None


def test_the_feed_keeps_the_city_and_the_range() -> None:
    page = feed(
        item(title="Inside"),
        item(title="Outside", startDateTime="2027-03-01 19:30:00"),
        item(
            title="Long run",
            startDateTime="2026-10-01 10:00:00",
            endDateTime="2026-12-01 17:00:00",
        ),
        item(title="Other city", locations="Victoria,Australia,Sydney"),
        item(title="No start", startDateTime=""),
    )
    gigs = artshub.parse_feed(page, "2026-11-10", "2026-11-20")
    assert [gig.artist for gig in gigs] == ["Inside", "Long run"]


def test_a_damaged_page_stops_the_read() -> None:
    with pytest.raises(sources.SourceError):
        artshub.parse_feed("<rss><channel>", "2026-11-10", "2026-11-20")


def test_the_venue_address_round_trips() -> None:
    url = artshub.venue_url("Kew Court House", "188 High Street, Kew VIC")
    assert url is not None
    decoded = artshub.decode_venue(url)
    assert decoded == ("Kew Court House", "188 High Street, Kew VIC")


def test_a_slash_in_the_name_stays_in_one_part() -> None:
    url = artshub.venue_url("Smith / Jones", "1 A Street")
    assert url is not None
    assert artshub.decode_venue(url) == ("Smith / Jones", "1 A Street")


def test_the_cache_names_a_venue_after_its_address() -> None:
    """Two venues at one address share a file, so the address is the key."""
    one = artshub.venue_url("Bunjil Place", "2 Patrick Northeast Drive")
    two = artshub.venue_url("Bunjil Place Gallery", "2 Patrick Northeast Drive")
    assert one and two
    assert sources.url_key(one) == sources.url_key(two)


def test_no_venue_holds_no_address() -> None:
    assert artshub.venue_url(None, None) is None
    assert artshub.venue_url("", "") is None


def test_a_foreign_address_is_not_a_venue() -> None:
    assert artshub.decode_venue("https://example.com/venue/A/B/") is None
    assert artshub.decode_venue("https://www.artshub.com.au/event/x/") is None


PAGE = """
<html><body><span>Event Starts</span> Oct 23, 2026 00:00
<div class="the-content"><p>Open Tuesday to Sunday 10am - 5pm.</p></div>
</body></html>
"""


def test_the_event_page_gives_the_body_and_the_start() -> None:
    body, start = artshub.parse_event_page(PAGE)
    assert body == "Open Tuesday to Sunday 10am - 5pm."
    assert start == date(2026, 10, 23)


def test_a_page_with_no_body_gives_nothing() -> None:
    assert artshub.parse_event_page("<html></html>") == ("", None)


GIG_URL = "https://www.artshub.com.au/event/x-1/"


def gigs_on(start: str, end: str, page: str | None) -> list[sources.Gig]:
    """Return the gigs of one fake event, as the source filters them."""

    async def run() -> list[sources.Gig]:
        source = artshub.ArtshubSource()  # no session: a network call raises

        async def feed(*_a: object, **_k: object) -> list[sources.Gig]:
            return [sources.Gig(artist="X", detail_url=GIG_URL)]

        async def text(*_a: object, **_k: object) -> str:
            if page is None:
                raise sources.SourceError("down")
            return page

        source._feed_page = feed  # type: ignore[method-assign]
        source._text = text  # type: ignore[method-assign]
        return await source.gigs(start, end)

    return asyncio.run(run())


# PAGE says "Open Tuesday to Sunday 10am - 5pm". 2026-11-02 is a Monday.
@pytest.mark.parametrize(
    "start, end, times",
    [
        ("2026-11-05", "2026-11-05", ["10:00am"]),  # Thursday: open
        ("2026-11-02", "2026-11-02", []),  # Monday: closed
        ("2026-11-02", "2026-11-03", ["10:00am"]),  # Monday and Tuesday
    ],
)
def test_a_gig_keeps_only_the_days_that_it_is_open(start, end, times) -> None:
    assert [g.time for g in gigs_on(start, end, PAGE)] == times


def test_a_gig_with_no_readable_page_stays_with_no_time() -> None:
    assert [g.time for g in gigs_on("2026-11-02", "2026-11-02", None)] == [None]


def test_a_gig_with_no_schedule_stays_with_no_time() -> None:
    page = "<div class='the-content'>A night of music.</div>"
    assert [g.time for g in gigs_on("2026-11-02", "2026-11-02", page)] == [None]


def expand(page: str | None, start: str = "2026-11-02") -> list[sources.Gig]:
    """Return `expand` of one fake gig, with the event page faked."""

    async def run() -> list[sources.Gig]:
        source = artshub.ArtshubSource()

        async def text(*_a: object, **_k: object) -> str:
            if page is None:
                raise sources.SourceError("down")
            return page

        source._text = text  # type: ignore[method-assign]
        gig = sources.Gig(artist="X", detail_url=GIG_URL)
        return await source.expand(gig, start, start)

    return asyncio.run(run())


def test_expand_gives_nothing_for_a_closed_exhibit() -> None:
    assert expand(PAGE, "2026-11-02") == []  # Monday


def test_expand_keeps_the_gig_when_the_page_read_fails() -> None:
    assert [g.time for g in expand(None)] == [None]


def test_expand_sets_the_time_from_the_schedule() -> None:
    assert [g.time for g in expand(PAGE, "2026-11-05")] == ["10:00am"]
