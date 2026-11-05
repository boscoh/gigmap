import asyncio
import json
import logging
import os
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
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

from gigmap import artscentre, artshub, beat, fringe, sources, stores, whatson, whatson_art
from gigmap.logger import log_incoming, log_kv, setup_logging

LOGGER = logging.getLogger(__name__)

PACKAGE_DIR = Path(__file__).resolve().parent
ROOT_DIR = PACKAGE_DIR.parent
DATA_DIR_ENV = "GIGMAP_DATA_DIR"
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
}
# A group page merges its members into one map. `/gigs/` holds the three
# sources that give a time of day for each gig. `/art/` holds the two art
# sources. artshub and fringe are not members of `gigs`. The page of each
# member is still at its own path.
GROUPS: dict[str, tuple[str, ...]] = {
    "gigs": ("artscentre", "beat", "whatson"),
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

# A gig time file older than this is removed by the cleanup.
GIG_TTL_DAYS = 30
# The cleanup runs at startup, then once each day.
CLEANUP_INTERVAL_SECONDS = 24 * 3600
# The detail pages are fetched with a small pool of connections.
DETAIL_CONCURRENCY = 5
# The venue reads use the same size of pool.
VENUE_CONCURRENCY = 5

# The page shows a failed venue read. Any error that is not a source
# error keeps this text, so an internal message stays on the server.
VENUE_FAILURE_TEXT = "the venue lookup failed"

# nginx holds a small stream in its buffers until the source closes it.
# This header makes nginx pass each line to the page at once.
STREAM_HEADERS = {"X-Accel-Buffering": "no"}

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


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


async def source_dependency(request: Request) -> AsyncIterator[dict[str, sources.GigSource]]:
    """Give the cached members for the request, then close them.

    A group page (`gig`, `art`) opens every member of its group. Each member
    keeps its own cache, so a group page shares the files of the other pages.
    """
    name = api_source_name(request.url.path)
    names = GROUPS.get(name, (name,))
    async with AsyncExitStack() as stack:
        members: dict[str, sources.GigSource] = {}
        for member in names:
            site = await stack.enter_async_context(SOURCE_FACTORIES[member]())
            members[member] = stores.CachedSource(
                site, stores.DataStores(data_dir, member)
            )
        yield members


MembersDep = Annotated[dict[str, sources.GigSource], Depends(source_dependency)]


@app.exception_handler(sources.SourceError)
async def source_error(_: Request, error: sources.SourceError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": str(error)})


def data_dir() -> Path:
    directory = Path(os.environ.get(DATA_DIR_ENV, ROOT_DIR / "data")).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


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


@page_router.get("/")
async def index(request: Request) -> HTMLResponse:
    """Serve the landing page, or the page of one source."""
    path = request.url.path
    if not path.strip("/"):
        return HTMLResponse(LANDING_FILE.read_text(encoding="utf-8"))
    name = page_source_name(path)
    info = SOURCE_INFO[name]
    html = (PACKAGE_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace(SOURCE_TITLE_TOKEN, info.title)
    html = html.replace(SOURCE_INFO_TOKEN, info.model_dump_json())
    return HTMLResponse(
        html.replace(SOURCE_API_BASE_TOKEN, api_prefix(name))
    )


async def gig_time(
    source: sources.GigSource,
    gig: sources.Gig,
    semaphore: asyncio.Semaphore,
) -> str | None:
    # A source can hold the time in the gig list. A read then costs one
    # request and adds nothing, so the time of the list stays.
    if gig.time:
        return gig.time
    detail_url = gig.detail_url or ""
    if not detail_url:
        return None
    async with semaphore:
        data = await source.gig_detail(detail_url)
    return data.time if data is not None else None


async def detail_read(
    source: sources.GigSource,
    gig: sources.Gig,
    semaphore: asyncio.Semaphore,
) -> str | None:
    """Return the start time of one gig, and None when the read fails."""
    try:
        return await gig_time(source, gig, semaphore)
    except Exception:
        return None


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
    time_text: str | None,
    venue: sources.Venue | Exception | None,
) -> str:
    """Return the one stream line of a gig, with all of its data."""
    data = gig.model_dump()
    # The line holds the time that the detail read found, so the page
    # needs no second line for it.
    data["time"] = time_text
    line: dict[str, object] = {"index": index, "gig": data, "venue": None}
    if isinstance(venue, Exception):
        line["error"] = venue_failure_text(venue)
    elif venue is not None:
        line["venue"] = venue.model_dump()
    return json.dumps(line) + "\n"


async def event_lines(
    source: sources.GigSource,
    gigs: list[sources.Gig],
) -> AsyncIterator[str]:
    """Yield one line for each gig, as its time and venue arrive.

    Every read starts before the first line leaves. A gig waits for its
    own two reads only, so a slow venue holds no other gig's line.
    """
    detail_pool = asyncio.Semaphore(DETAIL_CONCURRENCY)
    venue_pool = asyncio.Semaphore(VENUE_CONCURRENCY)
    # One detail read for each gig, so a time in the list costs no read.
    times = {
        index: asyncio.create_task(detail_read(source, gig, detail_pool))
        for index, gig in enumerate(gigs)
    }
    # One venue read for each url, so a venue of many gigs costs one read.
    venues: dict[str, asyncio.Task[sources.Venue | Exception]] = {}
    for gig in gigs:
        url = gig.venue_url
        if url and url not in venues:
            venues[url] = asyncio.create_task(
                venue_read(source, url, venue_pool)
            )

    async def one(index: int, gig: sources.Gig) -> str:
        time_text = await times[index]
        venue: sources.Venue | Exception | None = None
        if gig.venue_url:
            venue = await venues[gig.venue_url]
        return event_line(index, gig, time_text, venue)

    tasks = [
        asyncio.create_task(one(index, gig))
        for index, gig in enumerate(gigs)
    ]
    reads = [*times.values(), *venues.values()]
    try:
        for done in asyncio.as_completed(tasks):
            yield await done
    finally:
        # A client disconnect cancels every read.
        for task in [*tasks, *reads]:
            task.cancel()
        await asyncio.gather(*tasks, *reads, return_exceptions=True)


async def wait_for_a_list(lists: dict[str, asyncio.Task[list[sources.Gig]]]) -> None:
    """Return when one member has its gig list. Raise when every member fails.

    The route needs one list before it sends the headers, so a total
    failure still answers 502. A later failure is a log line only.
    """
    pending = set(lists.values())
    while pending:
        done, pending = await asyncio.wait(
            pending, return_when=asyncio.FIRST_COMPLETED
        )
        if any(task.exception() is None for task in done):
            return
    for name, task in lists.items():
        log_kv(LOGGER.warning, "source failed", source=name, error=task.exception())
    error = next(iter(lists.values())).exception()
    if isinstance(error, sources.SourceError):
        raise error
    raise sources.SourceError("no source gave a gig list")


async def merged_lines(
    members: dict[str, sources.GigSource],
    lists: dict[str, asyncio.Task[list[sources.Gig]]],
) -> AsyncIterator[str]:
    """Yield the lines of every member, as each line arrives.

    Each member has its own list read and its own pools, so a slow member
    holds no line of a fast member. A member that fails is logged and left
    out. The first line of a member is its total, `{"total": n}`.
    """
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def pump(name: str, member: sources.GigSource) -> None:
        try:
            gigs = await lists[name]
            await queue.put(json.dumps({"total": len(gigs)}) + "\n")
            async for line in event_lines(member, gigs):
                await queue.put(line)
        except Exception as error:
            log_kv(LOGGER.warning, "source failed", source=name, error=error)
        finally:
            await queue.put(None)

    tasks = [asyncio.create_task(pump(name, member)) for name, member in members.items()]
    try:
        for _ in tasks:
            while (line := await queue.get()) is not None:
                yield line
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
        name: asyncio.create_task(member.gigs(start_date, end_date))
        for name, member in members.items()
    }
    await wait_for_a_list(lists)
    return StreamingResponse(
        merged_lines(members, lists),
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
    gig_days: int = GIG_TTL_DAYS,
    dry_run: bool = False,
    quiet: bool = False,
) -> stores.CleanReport:
    """Remove the old data files and log the result."""
    report = stores.DataStores(data_dir).clean(time.time(), gig_days, dry_run)
    if quiet:
        return report
    verb = "would remove" if dry_run else "removed"
    log_kv(
        LOGGER.info,
        verb,
        days=report.days,
        gigs=report.gigs,
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
    gig_days: int = GIG_TTL_DAYS,
    dry_run: bool = False,
    quiet: bool = False,
) -> None:
    """Remove the data files that the app cannot use again.

    :param data_dir: directory for the saved files
    :param gig_days: keep a gig time file for this many days
    :param dry_run: log the plan and remove nothing
    :param quiet: log nothing
    """
    use_data_dir(data_dir)
    setup_logging()
    run_cleanup(gig_days, dry_run, quiet)


if __name__ == "__main__":
    cli()
