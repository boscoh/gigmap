"""Tests for the merge of the member streams on the `all` page."""

import asyncio
import json
from typing import Any, cast

import pytest

from gigmap import server, sources


class Member:
    """A member that answers one gig, waits for a gate, or fails."""

    def __init__(self, host: str, fail: bool = False) -> None:
        self.host = host
        self.fail = fail
        self.gate: asyncio.Event | None = None

    async def gigs(self, start_date: str, end_date: str) -> list[sources.Gig]:
        if self.gate is not None:
            await self.gate.wait()
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
        return sources.GigDetail(detail_url=detail_url, time="8pm")

    async def venue(self, venue_url: str) -> sources.Venue:
        return sources.Venue(venue_url=venue_url, name=self.host)


async def stream(members: dict[str, Member]) -> list[dict[str, Any]]:
    response = await server.event_stream(
        cast(Any, members), "2026-10-01", "2026-10-01"
    )
    body = cast(Any, response.body_iterator)
    return [json.loads(line) async for line in body]


def artists(lines: list[dict[str, Any]]) -> list[str]:
    return [line["gig"]["artist"] for line in lines if "gig" in line]


def test_the_lines_of_every_member_join() -> None:
    lines = asyncio.run(stream({"a": Member("a.test"), "b": Member("b.test")}))

    assert sorted(artists(lines)) == ["a.test", "b.test"]
    assert [line for line in lines if "total" in line] == [{"total": 1}] * 2


def test_a_slow_member_holds_no_line_of_a_fast_member() -> None:
    slow = Member("slow.test")
    slow.gate = asyncio.Event()

    async def run() -> list[dict[str, Any]]:
        members = {"slow": slow, "fast": Member("fast.test")}
        response = await server.event_stream(
            cast(Any, members), "2026-10-01", "2026-10-01"
        )
        body = cast(Any, response.body_iterator)
        # The list of `slow` stays shut, so only `fast` can answer.
        first = [
            json.loads(await asyncio.wait_for(body.__anext__(), timeout=5))
            for _ in range(2)
        ]
        slow.gate.set()  # type: ignore[union-attr]
        return first + [json.loads(line) async for line in body]

    lines = asyncio.run(run())

    assert artists(lines) == ["fast.test", "slow.test"]


def test_a_failed_member_is_left_out() -> None:
    lines = asyncio.run(stream({"a": Member("a.test", fail=True), "b": Member("b.test")}))

    assert artists(lines) == ["b.test"]


def test_the_read_fails_when_every_member_fails() -> None:
    members = {"a": Member("a.test", fail=True), "b": Member("b.test", fail=True)}

    with pytest.raises(sources.SourceError):
        asyncio.run(stream(members))


def test_the_all_page_and_reads_are_served() -> None:
    assert server.page_source_name("/all/") == "all"
    assert server.api_source_name("/api/all/events") == "all"


def test_the_all_page_lists_the_short_names() -> None:
    parts = server.SOURCE_INFO["all"].parts
    assert [part.name for part in parts] == ["artscentre", "beats", "whatson"]
    assert parts[1].home_url == server.SOURCE_INFO["beat"].home_url
    assert parts[2].home_url == server.SOURCE_INFO["whatson"].home_url
    assert server.SOURCE_INFO["beat"].parts == []
