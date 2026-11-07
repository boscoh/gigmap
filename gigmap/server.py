import asyncio
import contextlib
import json
import logging
import os
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Annotated

import uvicorn
from cyclopts import App, Parameter
from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)

from gigmap import (
    artscentre,
    artshub,
    beat,
    fringe,
    sources,
    stores,
    ticketmaster,
    whatson,
    whatson_art,
)
from gigmap.logger import log_incoming, log_kv, setup_logging
from gigmap.stores import DATA_DIR_ENV, data_dir

LOGGER = logging.getLogger(__name__)

PACKAGE_DIR = Path(__file__).resolve().parent
ROOT_DIR = PACKAGE_DIR.parent
SOURCE_TITLE_TOKEN = "__SOURCE_TITLE__"
SOURCE_INFO_TOKEN = "__SOURCE_INFO__"
SOURCE_API_BASE_TOKEN = "__API_BASE__"

# One server answers every site. Each source has a page prefix of its own
# name, so `/fringe` is the Fringe page and `/beat` is the beat page. The
# reads of every source sit at `/api/<source>`. The root path `/` answers
# the landing page, and `/gigs/` answers the map of the gig sources.
SOURCE_FACTORIES: dict[str, Callable[[], sources.SourcePool]] = {
    "artscentre": artscentre.ArtsCentreSource,
    "artshub": artshub.ArtshubSource,
    "beat": beat.BeatSource,
    "fringe": fringe.FringeSource,
    "whatson": whatson.WhatsonSource,
    "whatsonart": whatson_art.WhatsonArtSource,
    "ticketmaster": ticketmaster.TicketmasterSource,
}
# A group page merges its members into one map. `/gigs/` holds the three
# sources that give a time of day for each gig. `/art/` holds the two art
# sources. artshub and fringe are not members of `gigs`. The page of each
# member is still at its own path.
GROUPS: dict[str, tuple[str, ...]] = {
    "gigs": ("artscentre", "beat", "whatson", "ticketmaster"),
    "art": ("artshub", "whatsonart"),
}
GIG_SOURCE_NAME = "gigs"
# The root path `/` answers the landing page, and `/gigs/` answers the
# map of the gig sources.
ROOT_SOURCE_NAME = GIG_SOURCE_NAME
LANDING_FILE = ROOT_DIR / "index.html"
SOURCE_INFO: dict[str, sources.SourceInfo] = {
    "gigs": sources.SourceInfo(
        title="Melb Gig Map",
        name="gig sources",
        home_url="/gigs/",
    ),
    "art": sources.SourceInfo(
        title="Melb Art Map",
        heading="Melb Art Map",
        name="art sources",
        home_url="/art/",
        noun="exhibits",
    ),
    "artscentre": artscentre.SOURCE,
    "artshub": artshub.SOURCE,
    "beat": beat.SOURCE,
    "fringe": fringe.SOURCE,
    "whatson": whatson.SOURCE,
    "whatsonart": whatson_art.SOURCE,
    "ticketmaster": ticketmaster.SOURCE,
}
# The header of a group page links each member by its short name.
for _group, _names in GROUPS.items():
    SOURCE_INFO[_group].parts = [
        SOURCE_INFO[name].model_copy(
            update={"name": "beats" if name == "beat" else name}
        )
        for name in _names
    ]

# The document title of the server itself.
SOURCE = SOURCE_INFO[ROOT_SOURCE_NAME]

# The cleanup runs at startup, then once each day.
CLEANUP_INTERVAL_SECONDS = 24 * 3600
# The venue reads use a small pool of connections.
VENUE_CONCURRENCY = 5
# One pool for the whole app, so every request shares the limit.
VENUE_POOL = asyncio.Semaphore(VENUE_CONCURRENCY)

# The page shows a failed venue read. Any error that is not a source
# error keeps this text, so an internal message stays on the server.
VENUE_FAILURE_TEXT = "the venue lookup failed"

# nginx holds a small stream in its buffers until the source closes it.
# This header makes nginx pass each line to the page at once.
# no-transform stops an edge from buffering or recoding the lines.
STREAM_HEADERS = {
    "X-Accel-Buffering": "no",
    "Cache-Control": "no-store, no-transform",
}
# nginx cuts a silent gap at 60 s, so a quiet stream sends a heartbeat.
HEARTBEAT_SECONDS = 25
# A stalled reader pauses the pumps at this many queued lines.
STREAM_QUEUE_LINES = 64

# The page holds the whole app, so a browser keeps no copy. Without this
# header a browser can show the app of an older deploy, and a new field of
# the gig never appears.
PAGE_HEADERS = {"Cache-Control": "no-cache"}

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
MAX_RANGE_DAYS = 7

# The flights in progress, by (source, start, end). A done callback removes
# each key, so the dict holds in-flight keys only.
_INFLIGHT: dict[tuple[str, str, str], asyncio.Task] = {}


async def cleanup_loop() -> None:
    """Remove the old data files at startup, then once each day."""
    while True:
        try:
            run_cleanup()
        except Exception as error:
            log_kv(LOGGER.warning, "cleanup failed", error=error)
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    task = asyncio.create_task(cleanup_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title=SOURCE.title, lifespan=lifespan)


@app.middleware("http")
async def log_incoming_request(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Log one incoming request, with its status and time."""
    started = time.perf_counter()
    path = request.url.path
    if request.url.query:
        path = f"{path}?{request.url.query}"
    # A local client says nothing about the request, so it stays out.
    client = request.client.host if request.client else ""
    origin = {} if client in ("", "127.0.0.1", "::1") else {"client": client}
    try:
        response = await call_next(request)
    except Exception:
        log_incoming(
            LOGGER.warning,
            request.method,
            path,
            status=500,
            **origin,
            seconds=f"{time.perf_counter() - started:.3f}",
        )
        raise
    level = LOGGER.info if response.status_code < 500 else LOGGER.warning
    log_incoming(
        level,
        request.method,
        path,
        status=response.status_code,
        **origin,
        seconds=f"{time.perf_counter() - started:.3f}",
    )
    return response


page_router = APIRouter()
api_router = APIRouter()


def first_segment(path: str) -> str:
    """Return the first part of one request path, in lower case."""
    return path.strip("/").split("/", 1)[0].lower()


def page_source_name(path: str) -> str:
    """Return the name of the source of one page path.

    The app serves each page at the path of its source, so `/fringe/`
    answers from the Fringe source. `/gigs/` answers from the `gigs`
    source, which is the map of the gig sources. The root path `/` is the
    landing page, and it is not a source page.
    """
    name = first_segment(path)
    return name if name in SOURCE_INFO else ROOT_SOURCE_NAME


def api_source_name(path: str) -> str:
    """Return the name of the source of one API path.

    The reads sit at `/api/<source>/<read>`, so `/api/fringe/events`
    answers from the Fringe source. An unknown source is a 404, so a
    wrong name never answers from another site.
    """
    parts = path.strip("/").split("/")
    if len(parts) >= 2 and parts[0].lower() == "api":
        name = parts[1].lower()
        if name in SOURCE_INFO:
            return name
    raise HTTPException(status_code=404, detail="unknown source")


def api_prefix(name: str) -> str:
    """Return the API path prefix of one source."""
    return f"/api/{name}"


async def source_dependency(request: Request) -> AsyncIterator[dict[str, stores.CachedGigSource]]:
    """Give the cached members for the request, then close them.

    A group page (`gig`, `art`) opens every member of its group. Each member
    keeps its own cache, so a group page shares the files of the other pages.
    """
    name = api_source_name(request.url.path)
    names = GROUPS.get(name, (name,))
    async with AsyncExitStack() as stack:
        members: dict[str, stores.CachedGigSource] = {}
        for member in names:
            site = await stack.enter_async_context(SOURCE_FACTORIES[member]())
            members[member] = stores.CachedSource(
                site, stores.DataStores(data_dir, member)
            )
        yield members


MembersDep = Annotated[dict[str, stores.CachedGigSource], Depends(source_dependency)]


@app.exception_handler(sources.SourceError)
async def source_error(_: Request, error: sources.SourceError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": str(error)})


def validate_dates(start_date: str, end_date: str) -> None:
    for value in (start_date, end_date):
        if not DATE_RE.fullmatch(value):
            raise HTTPException(
                status_code=400,
                detail="dates must have the form YYYY-MM-DD",
            )
    # An ISO date compares as a string, so one check covers the order.
    if end_date < start_date:
        raise HTTPException(
            status_code=400,
            detail="end_date must not be before start_date",
        )
    days = (date.fromisoformat(end_date) - date.fromisoformat(start_date)).days + 1
    if days > MAX_RANGE_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"a range must not span more than {MAX_RANGE_DAYS} days",
        )


@page_router.get("/")
async def index(request: Request) -> HTMLResponse:
    """Serve the landing page, or the page of one source."""
    path = request.url.path
    if not path.strip("/"):
        return HTMLResponse(
            LANDING_FILE.read_text(encoding="utf-8"), headers=PAGE_HEADERS
        )
    name = page_source_name(path)
    info = SOURCE_INFO[name]
    html = (PACKAGE_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace(SOURCE_TITLE_TOKEN, info.title)
    html = html.replace(SOURCE_INFO_TOKEN, info.model_dump_json())
    return HTMLResponse(
        html.replace(SOURCE_API_BASE_TOKEN, api_prefix(name)), headers=PAGE_HEADERS
    )


async def venue_read(
    source: sources.GigSource,
    venue_url: str,
    semaphore: asyncio.Semaphore,
) -> sources.Venue | Exception:
    """Return one venue, or the reason that its read failed."""
    try:
        async with semaphore:
            return await source.venue(venue_url)
    except Exception as error:
        return error


def venue_failure_text(error: Exception) -> str:
    """Return the text of a failed venue read.

    The message of a source error goes to the page, so the page can show
    the reason. Any other message stays on the server.
    """
    if isinstance(error, sources.SourceError):
        return str(error)
    return VENUE_FAILURE_TEXT


def event_line(
    index: int,
    gig: sources.Gig,
    venue: sources.Venue | Exception | None,
) -> str:
    """Return the one stream line of a gig, with all of its data."""
    line: dict[str, object] = {"index": index, "gig": gig.model_dump(), "venue": None}
    if isinstance(venue, Exception):
        line["error"] = venue_failure_text(venue)
    elif venue is not None:
        line["venue"] = venue.model_dump()
    return json.dumps(line) + "\n"


async def event_lines(
    source: sources.GigSource,
    gigs: AsyncIterator[sources.Gig],
) -> AsyncIterator[str]:
    """Yield one line for each gig, as its venue arrives.

    The venue read of a gig starts when the gig arrives. A gig waits for
    its own venue read only, so a slow venue holds no other gig's line.
    """
    # One venue read for each url, so a venue of many gigs costs one read.
    venues: dict[str, asyncio.Task[sources.Venue | Exception]] = {}
    tasks: list[asyncio.Task] = []
    # Each item is a finished gig, an error of the gig stream, or None at the end.
    done: asyncio.Queue = asyncio.Queue()

    async def one(gig: sources.Gig) -> None:
        venue: sources.Venue | Exception | None = None
        if gig.venue_url:
            venue = await venues[gig.venue_url]
        await done.put((gig, venue))

    async def feed() -> None:
        try:
            async for gig in gigs:
                url = gig.venue_url
                if url and url not in venues:
                    venues[url] = asyncio.create_task(
                        venue_read(source, url, VENUE_POOL)
                    )
                tasks.append(asyncio.create_task(one(gig)))
            await asyncio.gather(*tasks)
        except Exception as error:
            await done.put(error)
        else:
            await done.put(None)

    feeder = asyncio.create_task(feed())
    count = 0
    try:
        while (item := await done.get()) is not None:
            if isinstance(item, Exception):
                raise item
            yield event_line(count, *item)
            count += 1
    finally:
        # A client disconnect cancels every read.
        reads = [feeder, *tasks, *venues.values()]
        for task in reads:
            task.cancel()
        await asyncio.gather(*reads, return_exceptions=True)


async def fetch_once(
    name: str, member: stores.CachedGigSource, start_date: str, end_date: str
) -> list[sources.Gig]:
    """Return the candidates; callers of one range share one fetch."""
    key = (name, start_date, end_date)
    task = _INFLIGHT.get(key)
    if task is None:
        task = asyncio.create_task(member.candidates(start_date, end_date))
        _INFLIGHT[key] = task
        task.add_done_callback(lambda _: _INFLIGHT.pop(key, None))
    return await asyncio.shield(task)


async def read_list(
    name: str, member: stores.CachedGigSource, start_date: str, end_date: str
) -> tuple[list[sources.Gig], bool]:
    """Return the warm day file and True, or the candidates and False."""
    hit = member.cached_gigs(start_date, end_date)
    if hit is not None:
        return hit, True
    return await fetch_once(name, member, start_date, end_date), False


async def merged_lines(
    members: dict[str, stores.CachedGigSource],
    lists: dict[str, asyncio.Task[tuple[list[sources.Gig], bool]]],
    start_date: str,
    end_date: str,
) -> AsyncIterator[str]:
    """Yield the lines of every member, as each line arrives.

    Each member has its own list read and its own pools, so a slow member
    holds no line of a fast member. A member that fails is logged, sends a
    `failed` line and is left out; the stream ends with a `done` line that
    lists the failed members. A member sends `{"total": n}` before its first event read, then a
    correction `{"total": d}` after each `expand` that does not give one gig.
    """
    queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=STREAM_QUEUE_LINES)
    failed: list[dict[str, str]] = []

    async def pump(name: str, member: stores.CachedGigSource) -> None:
        running = 0  # the sum of the total lines of this member
        sent = 0  # the gig lines of this member

        async def send_total(n: int) -> None:
            nonlocal running
            running += n
            await queue.put(json.dumps({"total": n}) + "\n")

        try:
            gigs, warm = await lists[name]
            await send_total(len(gigs))
            expanded: list[list[sources.Gig]] = [[] for _ in gigs]
            clean = True

            async def stream() -> AsyncIterator[sources.Gig]:
                nonlocal clean
                if warm:
                    for gig in gigs:
                        yield gig
                    return

                async def read(i: int, gig: sources.Gig) -> int:
                    nonlocal clean
                    try:
                        expanded[i] = await member.expand(gig, start_date, end_date)
                    except Exception as error:
                        log_kv(LOGGER.warning, "expand failed", source=name, error=error)
                        clean = False
                    return i

                reads = [asyncio.create_task(read(i, g)) for i, g in enumerate(gigs)]
                try:
                    for finished in asyncio.as_completed(reads):
                        part = expanded[await finished]
                        if len(part) != 1:
                            await send_total(len(part) - 1)
                        for gig in part:
                            yield gig
                    # Every expand is done: a cut after this leaves a warm
                    # file, so the retry skips the fetch.
                    if clean:
                        member.save_gigs(
                            start_date,
                            end_date,
                            [g for part in expanded for g in part],
                        )
                finally:
                    for task in reads:
                        task.cancel()

            async for line in event_lines(member, stream()):
                sent += 1
                await queue.put(line)
        except Exception as error:
            log_kv(LOGGER.warning, "source failed", source=name, error=error)
            code = "source_error" if isinstance(error, sources.SourceError) else "internal"
            reason = str(error) if code == "source_error" else "internal error"
            one = {"source": name, "failed": reason, "code": code}
            failed.append(one)
            await queue.put(json.dumps(one) + "\n")
            if running != sent:
                await queue.put(json.dumps({"total": sent - running}) + "\n")
        finally:
            # A cancelled pump must not wait on a full queue. The loop below
            # also stops on done tasks, so a dropped sentinel costs nothing.
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(None)

    tasks = [asyncio.create_task(pump(name, member)) for name, member in members.items()]
    try:
        while not (all(task.done() for task in tasks) and queue.empty()):
            try:
                line = await asyncio.wait_for(queue.get(), HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                yield json.dumps({"total": 0}) + "\n"
                continue
            if line is not None:
                yield line
        yield json.dumps({"done": True, "failed": failed}) + "\n"
    finally:
        # A client disconnect cancels every member.
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, *lists.values(), return_exceptions=True)


@api_router.get("/events")
async def event_stream(
    members: MembersDep,
    start_date: str = Query(...),
    end_date: str = Query(...),
) -> StreamingResponse:
    """Stream one line for each gig, when its time and venue are known."""
    validate_dates(start_date, end_date)
    lists = {
        name: asyncio.create_task(read_list(name, member, start_date, end_date))
        for name, member in members.items()
    }
    return StreamingResponse(
        merged_lines(members, lists, start_date, end_date),
        media_type="application/x-ndjson",
        headers=STREAM_HEADERS,
    )


def mount_sources(
    application: FastAPI, pages: APIRouter, reads: APIRouter
) -> None:
    """Serve the page and the reads of each source.

    `/` answers the landing page, `/gigs/` answers the map of the gig
    sources, and `/beat/` answers the beat page. The reads of a source
    sit at `/api/<source>/`, so the Fringe reads are at
    `/api/fringe/events`.
    """
    application.include_router(pages)
    for name in SOURCE_INFO:
        application.include_router(reads, prefix=api_prefix(name))
        application.include_router(pages, prefix=f"/{name}")

mount_sources(app, page_router, api_router)
cli = App(
    name="gigmap",
    help="The Melbourne gig guide server.",
)


@cli.command
def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    *,
    data_dir: Path | None = None,
    reload: Annotated[bool, Parameter(name=("-r", "--reload"))] = False,
) -> None:
    """Start the Melbourne gig guide server.

    :param host: address to listen on
    :param port: port to listen on
    :param data_dir: directory for the saved files
    :param reload: restart the server after a change to a .py file
    """
    use_data_dir(data_dir)
    setup_logging()

    if reload:
        # pi-lens-ignore: no-uvicorn-non-import-string
        uvicorn.run(
            "gigmap.server:app",
            host=host,
            port=port,
            log_config=None,
            reload=True,
            # Watch only the code. The cache writes many JSON files into
            # data/, so a watch of the root logs a change line per request.
            reload_dirs=[str(PACKAGE_DIR)],
        )
    else:
        uvicorn.run(
            "gigmap.server:app", host=host, port=port, log_config=None
        )


def use_data_dir(data_dir: Path | None) -> None:
    if data_dir is not None:
        os.environ[DATA_DIR_ENV] = str(data_dir)


def format_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} B"


def run_cleanup(
    dry_run: bool = False,
    quiet: bool = False,
) -> stores.CleanReport:
    """Remove the old data files and log the result."""
    report = stores.DataStores(data_dir).clean(time.time(), dry_run)
    if quiet:
        return report
    verb = "would remove" if dry_run else "removed"
    log_kv(
        LOGGER.info,
        verb,
        days=report.days,
        stale=report.stale,
        events=report.events,
        venues=report.venues,
        size=format_size(report.size),
    )
    if report.skipped:
        log_kv(
            LOGGER.info,
            "kept recent files",
            count=report.skipped,
            seconds=stores.DAYS_CACHE_SECONDS,
        )
    return report


@cli.command
def clean(
    *,
    data_dir: Path | None = None,
    dry_run: bool = False,
    quiet: bool = False,
) -> None:
    """Remove the data files that the app cannot use again.

    :param data_dir: directory for the saved files
    :param dry_run: log the plan and remove nothing
    :param quiet: log nothing
    """
    use_data_dir(data_dir)
    setup_logging()
    run_cleanup(dry_run, quiet)


if __name__ == "__main__":
    cli()
