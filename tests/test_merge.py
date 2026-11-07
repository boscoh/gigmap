"""Tests for the merge of the member streams on the `gig` page."""

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

    async def candidates(self, start_date: str, end_date: str) -> list[sources.Gig]:
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise sources.SourceError("blocked")
        return [
            sources.Gig(
                artist=self.host,
                detail_url=f"https://{self.host}/act/",
                time="8pm",
                venue_url=f"https://{self.host}/venue/",
            )
        ]

    def cached_gigs(self, start_date: str, end_date: str) -> list[sources.Gig] | None:
        """This member keeps no day file."""
        return None

    def save_gigs(
        self, start_date: str, end_date: str, gigs: list[sources.Gig]
    ) -> None:
        """This member keeps no day file."""

    async def expand(
        self, gig: sources.Gig, start_date: str, end_date: str
    ) -> list[sources.Gig]:
        """This member keeps no event detail."""
        return [gig]

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
    assert server.page_source_name("/gigs/") == "gigs"
    assert server.api_source_name("/api/gigs/events") == "gigs"


def test_the_all_page_lists_the_short_names() -> None:
    parts = server.SOURCE_INFO["gigs"].parts
    names = [part.name for part in parts]
    assert names == ["artscentre", "beats", "whatson", "ticketmaster"]
    assert parts[1].home_url == server.SOURCE_INFO["beat"].home_url
    assert parts[2].home_url == server.SOURCE_INFO["whatson"].home_url
    assert server.SOURCE_INFO["beat"].parts == []


def test_the_art_page_merges_artshub_and_whatsonart() -> None:
    assert server.page_source_name("/art/") == "art"
    assert server.api_source_name("/api/art/events") == "art"
    parts = server.SOURCE_INFO["art"].parts
    assert [part.name for part in parts] == ["artshub", "whatsonart"]
    assert server.GROUPS["art"] == ("artshub", "whatsonart")
    for name in ("art", "artshub", "whatsonart"):
        assert server.SOURCE_INFO[name].heading == "Melb Art Map"
    assert server.SOURCE_INFO["beat"].heading == "Melb Gig Map"


def test_the_art_page_counts_exhibits() -> None:
    for name in ("art", "artshub", "whatsonart"):
        assert server.SOURCE_INFO[name].noun == "exhibits"

    for name in ("gigs", "artscentre", "beat", "fringe", "whatson"):
        assert server.SOURCE_INFO[name].noun == "gigs"


class Expander(Member):
    """A member whose `expand` gives `sessions[name]` gigs for one candidate."""

    def __init__(self, host: str, sessions: dict[str, int]) -> None:
        super().__init__(host)
        self.sessions = sessions
        self.expanded: list[str] = []
        self.gates: dict[str, asyncio.Event] = {}

    async def candidates(self, start_date: str, end_date: str) -> list[sources.Gig]:
        return [
            sources.Gig(artist=name, detail_url=f"https://{self.host}/{name}/", time="8pm")
            for name in self.sessions
        ]

    async def expand(self, gig: sources.Gig, start_date: str, end_date: str) -> list[sources.Gig]:
        name = gig.artist or ""
        self.expanded.append(name)
        if name in self.gates:
            await self.gates[name].wait()
        if self.sessions[name] < 0:
            raise RuntimeError("boom")
        return [
            gig.model_copy(update={"artist": f"{name}{i}"})
            for i in range(self.sessions[name])
        ]


def test_the_totals_sum_to_the_rows() -> None:
    member = Expander("x.test", {"drop": 0, "keep": 1, "three": 3, "bad": -1})
    lines = asyncio.run(stream({"x": member}))

    assert lines[0] == {"total": 4}
    assert sum(line["total"] for line in lines if "total" in line) == len(artists(lines)) == 4


def test_a_blocked_expand_holds_no_other_line() -> None:
    member = Expander("x.test", {"slow": 1, "fast": 1})
    member.gates["slow"] = asyncio.Event()

    async def run() -> list[dict[str, Any]]:
        response = await server.event_stream(cast(Any, {"x": member}), "2026-10-01", "2026-10-01")
        body = cast(Any, response.body_iterator)
        first = json.loads(await asyncio.wait_for(body.__anext__(), 5))
        gig = json.loads(await asyncio.wait_for(body.__anext__(), 5))
        member.gates["slow"].set()
        return [first, gig, *[json.loads(line) async for line in body]]

    lines = asyncio.run(run())

    assert lines[0] == {"total": 2}
    assert lines[1]["gig"]["artist"] == "fast0"
    assert sorted(artists(lines)) == ["fast0", "slow0"]


def test_the_cold_stream_saves_and_the_warm_one_skips_expand(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gigmap import stores

    monkeypatch.setenv(server.DATA_DIR_ENV, str(tmp_path))
    raw = Expander("x.test", {"a": 2, "b": 0})
    member = stores.CachedSource(cast(Any, raw), stores.DataStores(server.data_dir))

    cold = asyncio.run(stream({"x": cast(Any, member)}))
    raw.expanded.clear()
    warm = asyncio.run(stream({"x": cast(Any, member)}))

    assert sorted(artists(cold)) == sorted(artists(warm)) == ["a0", "a1"]
    assert raw.expanded == []
    assert warm[0] == {"total": 2}


def test_a_cancelled_stream_saves_nothing(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gigmap import stores

    monkeypatch.setenv(server.DATA_DIR_ENV, str(tmp_path))
    raw = Expander("x.test", {"a": 1, "b": 1})
    raw.gates["b"] = asyncio.Event()
    member = stores.CachedSource(cast(Any, raw), stores.DataStores(server.data_dir))

    async def run() -> None:
        response = await server.event_stream(cast(Any, {"x": member}), "2026-10-01", "2026-10-01")
        body = cast(Any, response.body_iterator)
        await body.__anext__()
        await body.__anext__()
        await body.aclose()

    asyncio.run(run())

    assert not list(tmp_path.glob("days/*.json"))


def test_one_failed_list_leaves_the_other_member_streaming() -> None:
    lines = asyncio.run(stream({"a": Member("a.test", fail=True), "b": Member("b.test")}))

    assert artists(lines) == ["b.test"]
