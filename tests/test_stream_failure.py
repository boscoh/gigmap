"""Tests for the end of the stream: failed lines, done, heartbeat, bounds."""

import asyncio
import json
from pathlib import Path
from typing import Any, cast

import pytest

from gigmap import server, sources, stores

START = END = "2026-10-01"


class Member:
    """A member with a list of gigs, or a failure, or a gate."""

    def __init__(self, *artists: str, fail: Exception | None = None) -> None:
        self.artists = artists
        self.fail = fail
        self.gate: asyncio.Event | None = None
        self.saved: list[list[sources.Gig]] = []

    async def candidates(self, start_date: str, end_date: str) -> list[sources.Gig]:
        if self.gate is not None:
            await self.gate.wait()
        if self.fail is not None:
            raise self.fail
        return [sources.Gig(artist=a, time="8pm") for a in self.artists]

    def cached_gigs(self, start_date: str, end_date: str) -> None:
        return None

    def save_gigs(self, start_date: str, end_date: str, gigs: list[sources.Gig]) -> None:
        self.saved.append(gigs)

    async def expand(
        self, gig: sources.Gig, start_date: str, end_date: str
    ) -> list[sources.Gig]:
        return [gig]

    async def venue(self, venue_url: str) -> sources.Venue:
        return sources.Venue(venue_url=venue_url)


async def response_of(members: dict[str, Member]) -> Any:
    response = await server.event_stream(cast(Any, members), START, END)
    return response


async def lines_of(members: dict[str, Member]) -> list[dict[str, Any]]:
    response = await response_of(members)
    return [json.loads(line) async for line in response.body_iterator]


def artists(lines: list[dict[str, Any]]) -> list[str]:
    return [line["gig"]["artist"] for line in lines if "gig" in line]


def test_every_member_failing_still_answers_200_with_done() -> None:
    members = {
        "a": Member(fail=sources.SourceError("blocked")),
        "b": Member(fail=RuntimeError("secret")),
    }

    async def run() -> tuple[int, list[dict[str, Any]]]:
        response = await response_of(members)
        return response.status_code, [
            json.loads(line) async for line in response.body_iterator
        ]

    status, lines = asyncio.run(run())

    assert status == 200
    assert lines[-1]["done"] is True
    failed = {one["source"]: one for one in lines[-1]["failed"]}
    assert set(failed) == {"a", "b"}
    assert failed["a"]["reason"] == "blocked"
    assert failed["b"]["reason"] == "internal error"
    assert "secret" not in json.dumps(lines)


def test_one_failed_member_sends_its_line_then_done_after_the_other_gigs() -> None:
    lines = asyncio.run(
        lines_of({"a": Member(fail=sources.SourceError("x")), "b": Member("b.test")})
    )

    assert {"source": "a", "reason": "x"} in lines
    assert artists(lines) == ["b.test"]
    assert lines[-1] == {
        "done": True,
        "failed": [{"source": "a", "reason": "x"}],
    }


def test_a_clean_run_ends_with_done_and_no_failure() -> None:
    lines = asyncio.run(lines_of({"a": Member("a.test")}))

    assert lines[-1] == {"done": True, "failed": []}


def test_the_stream_headers_forbid_a_cache_and_a_transform() -> None:
    response = asyncio.run(response_of({"a": Member("a.test")}))

    assert response.headers["cache-control"] == "no-store, no-transform"
    assert response.headers["x-accel-buffering"] == "no"


def test_a_cut_after_expand_leaves_a_warm_day_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(server.DATA_DIR_ENV, str(tmp_path))
    store = stores.DataStores(server.data_dir, "a")
    member = stores.CachedSource(cast(Any, Member("a.test")), store)

    async def run() -> None:
        response = await server.event_stream(cast(Any, {"a": member}), START, END)
        body = cast(Any, response.body_iterator)
        await body.__anext__()  # the total line; the pump has not saved yet
        await body.__anext__()  # the first gig line comes after the save
        await body.aclose()  # the client cuts here

    asyncio.run(run())

    assert member.cached_gigs(START, END) is not None


def test_a_disconnect_with_a_full_queue_ends_every_pump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> list[asyncio.Task]:
        members = {"a": Member(*[f"a{i}" for i in range(200)])}
        response = await response_of(members)
        body = response.body_iterator
        await body.__anext__()
        await asyncio.sleep(0.2)  # the pump fills the queue and blocks
        await body.aclose()
        await asyncio.sleep(0.1)
        me = asyncio.current_task()
        return [t for t in asyncio.all_tasks() if t is not me and not t.done()]

    assert asyncio.run(run()) == []


def test_a_silent_gap_sends_the_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "HEARTBEAT_SECONDS", 0.05)
    slow = Member("slow.test")
    slow.gate = asyncio.Event()

    async def run() -> list[dict[str, Any]]:
        response = await response_of({"a": slow})
        body = response.body_iterator
        first = [json.loads(await asyncio.wait_for(body.__anext__(), 5))]
        first.append(json.loads(await asyncio.wait_for(body.__anext__(), 5)))
        slow.gate.set()  # type: ignore[union-attr]
        return first + [json.loads(line) async for line in body]

    lines = asyncio.run(run())

    assert lines[:2] == [{"total": 0}, {"total": 0}]
    assert artists(lines) == ["slow.test"]
    assert lines[-1]["done"] is True


def test_the_merge_of_two_members_keeps_both_lists_in_order() -> None:
    lines = asyncio.run(
        lines_of({"a": Member("a1", "a2", "a3"), "b": Member("b1", "b2", "b3")})
    )
    got = artists(lines)

    assert [x for x in got if x.startswith("a")] == ["a1", "a2", "a3"]
    assert [x for x in got if x.startswith("b")] == ["b1", "b2", "b3"]


def test_a_member_failing_after_its_total_corrects_the_running_total() -> None:
    class BadSave(Member):
        def save_gigs(self, start_date, end_date, gigs):  # type: ignore[no-untyped-def]
            raise RuntimeError("disk full")

    lines = asyncio.run(lines_of({"a": BadSave("a1", "a2")}))

    # The total said 2, no gig line went out, so the fix is -2.
    assert [line["total"] for line in lines if "total" in line] == [2, -2]
    assert artists(lines) == []
    assert lines[-1]["failed"][0]["reason"] == "internal error"


def test_a_stalled_reader_leaves_at_most_64_lines_queued(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[asyncio.Queue] = []
    real = asyncio.Queue

    def spy(*args: Any, **kwargs: Any) -> asyncio.Queue:
        queue = real(*args, **kwargs)
        created.append(queue)
        return queue

    monkeypatch.setattr(server.asyncio, "Queue", spy)

    async def run() -> int:
        response = await response_of({"a": Member(*[f"a{i}" for i in range(300)])})
        body = response.body_iterator
        await body.__anext__()
        await asyncio.sleep(0.3)  # the reader stalls; the pump fills the queue
        size = max(q.qsize() for q in created if q.maxsize)
        await body.aclose()
        return size

    assert asyncio.run(run()) <= 64
