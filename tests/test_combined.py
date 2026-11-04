"""Tests for the merge of several gig sources."""

import asyncio

import pytest

from gigmap import combined, server, sources


class Member:
    """A member that answers one gig, or fails."""

    def __init__(self, host: str, fail: bool = False) -> None:
        self.host = host
        self.fail = fail

    async def gigs(self, start_date: str, end_date: str) -> list[sources.Gig]:
        if self.fail:
            raise sources.SourceError("blocked")
        return [
            sources.Gig(
                artist=self.host,
                detail_url=f"https://{self.host}/act/",
                venue_url=f"https://{self.host}/venue/",
            )
        ]

    async def gig_detail(self, detail_url: str) -> sources.GigDetail | None:
        return sources.GigDetail(detail_url=detail_url, time=self.host)

    async def venue(self, venue_url: str) -> sources.Venue:
        return sources.Venue(venue_url=venue_url, name=self.host)


def merged(**members: Member) -> combined.CombinedSource:
    return combined.CombinedSource(dict(members))  # type: ignore[arg-type]


def test_the_gigs_of_every_member_join() -> None:
    source = merged(a=Member("a.test"), b=Member("b.test"))

    gigs = asyncio.run(source.gigs("2026-10-01", "2026-10-01"))

    assert [gig.artist for gig in gigs] == ["a.test", "b.test"]


def test_a_read_goes_to_the_member_that_owns_the_url() -> None:
    source = merged(a=Member("a.test"), b=Member("b.test"))

    async def run() -> tuple[object, object]:
        await source.gigs("2026-10-01", "2026-10-01")
        detail = await source.gig_detail("https://b.test/act/")
        venue = await source.venue("https://a.test/venue/")
        return detail, venue

    detail, venue = asyncio.run(run())

    assert detail == sources.GigDetail(detail_url="https://b.test/act/", time="b.test")
    assert venue == sources.Venue(venue_url="https://a.test/venue/", name="a.test")


def test_a_failed_member_is_left_out() -> None:
    source = merged(a=Member("a.test", fail=True), b=Member("b.test"))

    gigs = asyncio.run(source.gigs("2026-10-01", "2026-10-01"))

    assert [gig.artist for gig in gigs] == ["b.test"]


def test_the_read_fails_when_every_member_fails() -> None:
    source = merged(a=Member("a.test", fail=True), b=Member("b.test", fail=True))

    with pytest.raises(sources.SourceError):
        asyncio.run(source.gigs("2026-10-01", "2026-10-01"))


def test_an_unknown_venue_is_a_url_error() -> None:
    source = merged(a=Member("a.test"))

    with pytest.raises(sources.SourceUrlError):
        asyncio.run(source.venue("https://nowhere.test/venue/"))


def test_the_all_page_and_reads_are_served() -> None:
    assert server.page_source_name("/all/") == "all"
    assert server.api_source_name("/api/all/events") == "all"


def test_the_all_page_lists_the_short_names() -> None:
    parts = server.SOURCE_INFO["all"].parts
    assert [part.name for part in parts] == ["artscentre", "artshub", "beat"]
    assert parts[2].home_url == server.SOURCE_INFO["beat"].home_url
    assert server.SOURCE_INFO["beat"].parts == []
