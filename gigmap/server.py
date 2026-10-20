import asyncio
import json
import os
import re
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import uvicorn
from cyclopts import App, Parameter
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)

from gigmap import beat, fringe, sources, stores

PACKAGE_DIR = Path(__file__).resolve().parent
ROOT_DIR = PACKAGE_DIR.parent
DATA_DIR_ENV = "GIGMAP_DATA_DIR"
SOURCE_TITLE_TOKEN = "__SOURCE_TITLE__"
SOURCE_INFO_TOKEN = "__SOURCE_INFO__"
SOURCE_API_BASE_TOKEN = "__API_BASE__"

# One server answers both sites. `/` is the page of the beat site, and
# each other source has a page prefix of its own name, so `/fringe` is
# the Fringe page. The reads of every source sit at `/api/<source>`.
ROOT_SOURCE_NAME = "beat"
SOURCE_FACTORIES: dict[str, Callable[[], sources.SourcePool]] = {
    "beat": beat.BeatSource,
    "fringe": fringe.FringeSource,
}
SOURCE_INFO: dict[str, sources.SourceInfo] = {
    "beat": beat.SOURCE,
    "fringe": fringe.SOURCE,
}

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
            print(f"cleanup failed: {error}", flush=True)
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    task = asyncio.create_task(cleanup_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title=SOURCE.title, lifespan=lifespan)
page_router = APIRouter()
api_router = APIRouter()


def first_segment(path: str) -> str:
    """Return the first part of one request path, in lower case."""
    return path.strip("/").split("/", 1)[0].lower()


def page_source_name(path: str) -> str:
    """Return the name of the source of one page path.

    The app serves each page at the path of its source, so `/fringe/`
    answers from the Fringe source. The root path `/` answers from the
    beat source, which is the site at the root.
    """
    name = first_segment(path)
    return name if name in SOURCE_FACTORIES else ROOT_SOURCE_NAME


def api_source_name(path: str) -> str:
    """Return the name of the source of one API path.

    The reads sit at `/api/<source>/<read>`, so `/api/fringe/events`
    answers from the Fringe source. An unknown source is a 404, so a
    wrong name never answers from another site.
    """
    parts = path.strip("/").split("/")
    if len(parts) >= 2 and parts[0].lower() == "api":
        name = parts[1].lower()
        if name in SOURCE_FACTORIES:
            return name
    raise HTTPException(status_code=404, detail="unknown source")


def api_prefix(name: str) -> str:
    """Return the API path prefix of one source."""
    return f"/api/{name}"


async def source_dependency(request: Request) -> AsyncIterator[sources.GigSource]:
    """Give one cached source for the request, then close it."""
    name = api_source_name(request.url.path)
    async with SOURCE_FACTORIES[name]() as site:
        yield stores.CachedSource(site, stores.DataStores(data_dir, name))


SourceDep = Annotated[sources.GigSource, Depends(source_dependency)]


@app.exception_handler(sources.SourceUrlError)
async def source_url_error(_: Request, error: sources.SourceUrlError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(error)})


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
    """Serve the page of one source, with its details filled in."""
    path = request.url.path
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


@api_router.get("/events")
async def event_stream(
    source: SourceDep,
    start_date: str = Query(...),
    end_date: str = Query(...),
) -> StreamingResponse:
    """Stream one line for each gig, when its time and venue are known."""
    validate_dates(start_date, end_date)
    gigs = await source.gigs(start_date, end_date)
    return StreamingResponse(
        event_lines(source, gigs),
        media_type="application/x-ndjson",
        headers=STREAM_HEADERS,
    )


@api_router.get("/venue")
async def venue(source: SourceDep, venue_url: str = Query(...)) -> sources.Venue:
    return await source.venue(venue_url)


def mount_sources(
    application: FastAPI, pages: APIRouter, reads: APIRouter
) -> None:
    """Serve the page and the reads of each source.

    `/` answers the beat page and `/fringe/` answers the Fringe page.
    The reads of a source sit at `/api/<source>/`, so the Fringe reads
    are at `/api/fringe/events`.
    """
    application.include_router(pages)
    for name in SOURCE_FACTORIES:
        application.include_router(reads, prefix=api_prefix(name))
        # The root site is the beat site, so it needs no page prefix.
        if name == ROOT_SOURCE_NAME:
            continue
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

    if reload:
        uvicorn.run(
            "gigmap.server:app",
            host=host,
            port=port,
            reload=True,
            reload_dirs=[str(ROOT_DIR)],
        )
    else:
        uvicorn.run("gigmap.server:app", host=host, port=port)


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
    """Remove the old data files and print the result."""
    report = stores.DataStores(data_dir).clean(time.time(), gig_days, dry_run)
    if quiet:
        return report
    verb = "would remove" if dry_run else "removed"
    print(
        f"{verb} {report.days} day files and {report.gigs} gig files, "
        f"{format_size(report.size)}",
        flush=True,
    )
    if report.skipped:
        print(
            f"kept {report.skipped} files younger than "
            f"{stores.DAYS_CACHE_SECONDS} seconds",
            flush=True,
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
    :param dry_run: print the plan and remove nothing
    :param quiet: print nothing
    """
    use_data_dir(data_dir)
    run_cleanup(gig_days, dry_run, quiet)


if __name__ == "__main__":
    cli()
