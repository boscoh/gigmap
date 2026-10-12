import asyncio
import json
import os
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import uvicorn
from cyclopts import App, Parameter
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)

from gigmap import beat, sources, stores

PACKAGE_DIR = Path(__file__).resolve().parent
ROOT_DIR = PACKAGE_DIR.parent
DATA_DIR_ENV = "GIGMAP_DATA_DIR"
SOURCE_TITLE_TOKEN = "__SOURCE_TITLE__"
SOURCE_INFO_TOKEN = "__SOURCE_INFO__"

# A gig time file older than this is removed by the cleanup.
GIG_TTL_DAYS = 30
# The cleanup runs at startup, then once each day.
CLEANUP_INTERVAL_SECONDS = 24 * 3600
# The detail pages are fetched with a small pool of connections.
DETAIL_CONCURRENCY = 5

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


app = FastAPI(title=beat.SOURCE.title, lifespan=lifespan)


async def source_dependency(
    refresh: bool = Query(False),
) -> AsyncIterator[sources.GigSource]:
    """Give one cached source for the request, then close it."""
    async with beat.BeatSource() as site:
        yield stores.CachedSource(site, stores.DataStores(data_dir), refresh)


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



@app.get("/")
async def index() -> HTMLResponse:
    """Serve the page with the source details filled in."""
    html = (PACKAGE_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace(SOURCE_TITLE_TOKEN, beat.SOURCE.title)
    return HTMLResponse(html.replace(SOURCE_INFO_TOKEN, beat.SOURCE.model_dump_json()))


async def gig_time(
    source: sources.GigSource,
    gig: sources.Gig,
    semaphore: asyncio.Semaphore,
) -> str | None:
    detail_url = gig.detail_url or ""
    if not detail_url:
        return None
    async with semaphore:
        data = await source.gig_detail(detail_url)
    return data.time if data is not None else None


@app.get("/api/gigs")
async def gig_list(
    source: SourceDep,
    start_date: str = Query(...),
    end_date: str = Query(...),
) -> list[sources.Gig]:
    validate_dates(start_date, end_date)
    return await source.gigs(start_date, end_date)


@app.get("/api/times")
async def gig_times(
    source: SourceDep,
    start_date: str = Query(...),
    end_date: str = Query(...),
) -> StreamingResponse:
    """Stream one NDJSON line for each gig as its start time arrives."""
    validate_dates(start_date, end_date)
    gigs = await source.gigs(start_date, end_date)

    async def lines() -> AsyncIterator[str]:
        semaphore = asyncio.Semaphore(DETAIL_CONCURRENCY)
        queue: asyncio.Queue[tuple[int, str | None]] = asyncio.Queue()

        async def worker(index: int, gig: sources.Gig) -> None:
            try:
                time_text = await gig_time(source, gig, semaphore)
            except Exception:
                time_text = None
            await queue.put((index, time_text))

        workers = [
            asyncio.create_task(worker(index, gig))
            for index, gig in enumerate(gigs)
        ]
        try:
            for _ in workers:
                index, time_text = await queue.get()
                yield json.dumps({"index": index, "time": time_text}) + "\n"
        finally:
            for task in workers:
                task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

    return StreamingResponse(lines(), media_type="application/x-ndjson")


@app.get("/api/venue")
async def venue(source: SourceDep, venue_url: str = Query(...)) -> sources.Venue:
    return await source.venue(venue_url)


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
