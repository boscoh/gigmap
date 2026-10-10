import asyncio
import json
import os
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated, NamedTuple, TypeVar
from urllib.parse import urlencode

import httpx
import uvicorn
from bs4 import BeautifulSoup
from cyclopts import App, Parameter
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR_ENV = "GIGMAP_DATA_DIR"
CACHE_SECONDS = 600
def env_int(name: str, default: int) -> int:
    """Read a non-negative integer from the environment."""
    try:
        value = int(os.environ[name])
    except (KeyError, ValueError):
        return default
    return max(0, value)


# A gig time file older than this is removed by the cleanup.
GIG_TTL_DAYS = env_int("GIGMAP_GIG_TTL_DAYS", 30)
# The cleanup runs at startup, then once each day.
CLEANUP_INTERVAL_SECONDS = 24 * 3600
# The detail pages are fetched with a small pool of connections.
DETAIL_CONCURRENCY = 5
# A venue without coordinates is looked up again after this long.
VENUE_RETRY_SECONDS = 30 * 24 * 3600
GEOCODE_SECONDS = 1.1

BEAT_URL = "https://beat.com.au/gig-guide/"
BEAT_ORIGIN = "https://beat.com.au/"

HEAD_RE = re.compile(r"<head[^>]*>", re.IGNORECASE)
BASE_RE = re.compile(r"<base\b", re.IGNORECASE)
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
SPACE_RE = re.compile(r"\s+")
VENUE_URL_RE = re.compile(r"https://beat\.com\.au/directory/(?P<slug>[a-z0-9-]+)/?")
DETAIL_URL_RE = re.compile(
    r"https://beat\.com\.au/(?!directory/)(?P<slug>[a-z0-9-]+)/?"
)
# The gig detail page shows the start time, for example "6.30pm".
GIG_TIME_RE = re.compile(
    r"(?P<hour>\d{1,2})(?:[.:](?P<minute>\d{2}))?\s*(?P<meridiem>am|pm)",
    re.IGNORECASE,
)
SAVED_FILE_RE = re.compile(
    r"gigmap_(?P<start>\d{4}-\d{2}-\d{2})_(?P<end>\d{4}-\d{2}-\d{2})\.json"
)

# beat.com.au (Cloudflare) answers a browser user agent.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-AU,en;q=0.9",
}

# Nominatim allows one request per second, needs an application user agent,
# and needs cached results.
# See https://operations.osmfoundation.org/policies/nominatim/
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_HEADERS = {"User-Agent": "gigmap/0.1 (personal Melbourne gig guide)"}

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


app = FastAPI(title="Gig Guide", lifespan=lifespan)


def data_dir() -> Path:
    directory = Path(os.environ.get(DATA_DIR_ENV, BASE_DIR / "data")).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def days_dir() -> Path:
    directory = data_dir() / "days"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def saved_json(start_date: str, end_date: str) -> Path:
    return days_dir() / f"gigmap_{start_date}_{end_date}.json"


def venue_file(slug: str) -> Path:
    directory = data_dir() / "venues"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{slug}.json"


def gig_detail_dir() -> Path:
    directory = data_dir() / "gigs"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def gig_detail_file(slug: str) -> Path:
    return gig_detail_dir() / f"{slug}.json"


def page_is_fresh(path: Path, max_age: int = CACHE_SECONDS) -> bool:
    if not path.exists():
        return False
    return time.time() - path.stat().st_mtime < max_age


T = TypeVar("T")


def read_saved_json(path: Path, kind: type[T]) -> T | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, kind) else None


def rewrite_for_frame(html: str) -> str:
    if BASE_RE.search(html):
        return html
    base_tag = f'<base href="{BEAT_ORIGIN}">'
    if not HEAD_RE.search(html):
        return base_tag + html
    return HEAD_RE.sub(lambda match: match.group(0) + base_tag, html, count=1)


def parse_site_date(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = SPACE_RE.sub(" ", text).strip().lstrip("-").strip()
    if not cleaned:
        return None
    try:
        return datetime.strptime(cleaned.title(), "%a %d %b %Y").date().isoformat()
    except ValueError:
        return None


def normalise_time(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = SPACE_RE.sub(" ", text).strip()
    match = GIG_TIME_RE.fullmatch(cleaned)
    if match is None:
        return cleaned or None
    hour = match.group("hour")
    minute = match.group("minute") or "00"
    meridiem = match.group("meridiem").lower()
    return f"{hour}:{minute}{meridiem}"


def parse_gig_detail(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    time_text = None
    for item in soup.select(".article-meta-item"):
        headline = item.select_one(".article-meta-headline")
        if headline is None or headline.get_text(strip=True).lower() != "time":
            continue
        content = item.select_one(".article-meta-content")
        if content is not None:
            time_text = content.get_text(" ", strip=True)
        break
    return {"time": normalise_time(time_text)}


def parse_gigs(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    gigs = []
    for card in soup.select(".events_wrapper > .gig-card"):
        title = card.select_one(".gig-title a")
        image = card.select_one(".gig-image img")
        if title is None or image is None:
            continue
        date = card.select_one(".gig-date")
        end_date = card.select_one(".gig-end-date")
        location = card.select_one(".gig-location a")
        price = card.select_one(".gig-price")
        ticket = card.select_one(".gig-ticket a")
        gigs.append(
            {
                "artist": title.get_text(strip=True),
                "detail_url": title.get("href"),
                "date": parse_site_date(date.get_text()) if date else None,
                "end_date": parse_site_date(end_date.get_text()) if end_date else None,
                "venue": location.get_text(strip=True) if location else None,
                "venue_url": location.get("href") if location else None,
                "genres": [badge.get_text(strip=True) for badge in card.select(".gig-genre .badge")],
                "categories": [
                    badge.get_text(strip=True) for badge in card.select(".gig-category .badge")
                ],
                "price": price.get_text(" ", strip=True) if price else None,
                "ticket_url": ticket.get("href") if ticket else None,
                "image_url": image.get("src"),
                "image_alt": image.get("alt"),
            }
        )
    return gigs


def parse_venue(html: str, venue_url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    heading = soup.select_one("h1")
    items = soup.select(".article-content .article-meta-item")
    address = None
    if items:
        text = items[0].get_text(" ", strip=True)
        address = SPACE_RE.sub(" ", text).strip() or None
    return {
        "name": heading.get_text(strip=True) if heading else None,
        "address": address,
        "venue_url": venue_url,
    }


def map_urls(latitude: float, longitude: float, delta: float = 0.004) -> tuple[str, str]:
    bbox = (
        f"{longitude - delta:.6f},{latitude - delta:.6f},"
        f"{longitude + delta:.6f},{latitude + delta:.6f}"
    )
    marker = f"{latitude:.6f},{longitude:.6f}"
    embed = "https://www.openstreetmap.org/export/embed.html?" + urlencode(
        {"bbox": bbox, "layer": "mapnik", "marker": marker}
    )
    page = (
        "https://www.openstreetmap.org/?"
        + urlencode({"mlat": latitude, "mlon": longitude})
        + f"#map=17/{latitude}/{longitude}"
    )
    return embed, page


_geocode_lock = asyncio.Lock()
_last_geocode = 0.0


async def geocode(query: str) -> dict | None:
    global _last_geocode

    async with _geocode_lock:
        delay = GEOCODE_SECONDS - (time.time() - _last_geocode)
        if delay > 0:
            await asyncio.sleep(delay)
        response = None
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(
                    NOMINATIM_URL,
                    params={
                        "q": query,
                        "format": "jsonv2",
                        "limit": 1,
                        "countrycodes": "au",
                    },
                    headers=NOMINATIM_HEADERS,
                )
        except httpx.HTTPError:
            response = None
        _last_geocode = time.time()

    if response is None or response.status_code != 200:
        return None
    try:
        results = response.json()
    except ValueError:
        return None
    if not results:
        return None
    first = results[0]
    try:
        latitude = float(first["lat"])
        longitude = float(first["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    return {
        "latitude": latitude,
        "longitude": longitude,
        "display_name": first.get("display_name"),
    }


async def geocode_venue(name: str | None, address: str | None) -> dict | None:
    queries = []
    if address:
        queries.append(f"{address}, Australia")
    if name:
        queries.append(f"{name}, Melbourne, Australia")
    for query in queries:
        place = await geocode(query)
        if place is not None:
            return place
    return None


def validate_dates(start_date: str, end_date: str) -> None:
    for value in (start_date, end_date):
        if not DATE_RE.fullmatch(value):
            raise HTTPException(
                status_code=400,
                detail="dates must have the form YYYY-MM-DD",
            )


async def fetch_remote(start_date: str, end_date: str) -> str:
    params = {
        "search_term": "",
        "venue_search": "",
        "genre": "",
        "city": "",
        "category": "",
        "start_date": start_date,
        "end_date": end_date,
    }

    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        try:
            response = await client.get(BEAT_URL, params=params, headers=HEADERS)
        except httpx.HTTPError as error:
            raise HTTPException(
                status_code=502,
                detail=f"cannot reach beat.com.au: {error}",
            ) from error

    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"beat.com.au answered status {response.status_code}",
        )

    return rewrite_for_frame(response.text)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(BASE_DIR / "index.html")


@app.get("/api/page", response_class=HTMLResponse)
async def gig_page(
    start_date: str = Query(...),
    end_date: str = Query(...),
) -> HTMLResponse:
    validate_dates(start_date, end_date)
    return HTMLResponse(content=await fetch_remote(start_date, end_date))


async def fetch_gig_detail(client: httpx.AsyncClient, detail_url: str) -> dict | None:
    try:
        response = await client.get(detail_url, headers=HEADERS)
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    data = parse_gig_detail(response.text)
    data["detail_url"] = detail_url
    return data


async def gig_time(
    client: httpx.AsyncClient,
    gig: dict,
    refresh: bool,
    semaphore: asyncio.Semaphore,
) -> str | None:
    match = DETAIL_URL_RE.fullmatch(gig.get("detail_url") or "")
    if match is None:
        return None
    cache = gig_detail_file(match.group("slug"))
    data = None if refresh else read_saved_json(cache, dict)
    if data is None:
        async with semaphore:
            data = await fetch_gig_detail(client, gig["detail_url"])
        if data is not None:
            cache.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
    return normalise_time(data.get("time")) if isinstance(data, dict) else None


async def load_gigs(start_date: str, end_date: str, refresh: bool) -> list[dict]:
    json_file = saved_json(start_date, end_date)
    if not refresh and page_is_fresh(json_file):
        gigs = read_saved_json(json_file, list)
        if gigs is not None:
            return gigs

    gigs = parse_gigs(await fetch_remote(start_date, end_date))
    json_file.write_text(
        json.dumps(gigs, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return gigs


@app.get("/api/gigs")
async def gig_list(
    start_date: str = Query(...),
    end_date: str = Query(...),
    refresh: bool = Query(False),
) -> list[dict]:
    validate_dates(start_date, end_date)

    gigs = await load_gigs(start_date, end_date, refresh)
    for gig in gigs:
        gig.setdefault("time", None)
    return gigs


@app.get("/api/times")
async def gig_times(
    start_date: str = Query(...),
    end_date: str = Query(...),
    refresh: bool = Query(False),
) -> StreamingResponse:
    """Stream one NDJSON line for each gig as its start time arrives."""
    validate_dates(start_date, end_date)
    gigs = await load_gigs(start_date, end_date, refresh)

    async def lines() -> AsyncIterator[str]:
        semaphore = asyncio.Semaphore(DETAIL_CONCURRENCY)
        queue: asyncio.Queue[tuple[int, str | None]] = asyncio.Queue()

        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:

            async def worker(index: int, gig: dict) -> None:
                try:
                    time_text = await gig_time(client, gig, refresh, semaphore)
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


def saved_venue(path: Path) -> dict | None:
    """Use the stored venue. A venue without coordinates expires."""
    saved = read_saved_json(path, dict)
    if saved is None:
        return None
    if saved.get("latitude") and saved.get("longitude"):
        return saved
    return saved if page_is_fresh(path, VENUE_RETRY_SECONDS) else None


@app.get("/api/venue")
async def venue(venue_url: str = Query(...)) -> dict:
    match = VENUE_URL_RE.fullmatch(venue_url)
    if match is None:
        raise HTTPException(
            status_code=400,
            detail="venue_url must have the form https://beat.com.au/directory/<name>/",
        )
    slug = match.group("slug")

    cache = venue_file(slug)
    saved = saved_venue(cache)
    if saved is not None:
        return saved

    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        try:
            response = await client.get(venue_url, headers=HEADERS)
        except httpx.HTTPError as error:
            raise HTTPException(
                status_code=502,
                detail=f"cannot reach beat.com.au: {error}",
            ) from error

    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"beat.com.au answered status {response.status_code}",
        )

    data = parse_venue(response.text, venue_url)
    place = await geocode_venue(data["name"], data["address"])
    if place is None:
        data.update(
            {
                "latitude": None,
                "longitude": None,
                "map_embed_url": None,
                "map_url": None,
            }
        )
    else:
        data.update(place)
        data["map_embed_url"], data["map_url"] = map_urls(
            place["latitude"], place["longitude"]
        )

    cache.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return data


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
            "server:app",
            host=host,
            port=port,
            reload=True,
            reload_dirs=[str(BASE_DIR)],
        )
    else:
        uvicorn.run("server:app", host=host, port=port)


def use_data_dir(data_dir: Path | None) -> None:
    if data_dir is not None:
        os.environ[DATA_DIR_ENV] = str(data_dir)


class CleanReport(NamedTuple):
    """The count of the files that the cleanup removed."""

    days: int
    gigs: int
    size: int
    skipped: int


def removable(path: Path, now: float) -> bool:
    """A file younger than CACHE_SECONDS may be in use by a request."""
    try:
        return now - path.stat().st_mtime >= CACHE_SECONDS
    except OSError:
        return False


def format_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} B"


def clean_data(now: float, gig_days: int, dry_run: bool) -> CleanReport:
    """Remove the day files of a past range and the old gig time files."""
    today = datetime.fromtimestamp(now).date().isoformat()
    days = gigs = size = skipped = 0

    for path in days_dir().glob("gigmap_*.json"):
        match = SAVED_FILE_RE.fullmatch(path.name)
        if match is None or match.group("end") >= today:
            continue
        if not removable(path, now):
            skipped += 1
            continue
        days += 1
        size += path.stat().st_size
        if not dry_run:
            path.unlink(missing_ok=True)

    cutoff = now - gig_days * 86400
    for path in gig_detail_dir().glob("*.json"):
        try:
            old = path.stat().st_mtime < cutoff
        except OSError:
            continue
        if not old:
            continue
        if not removable(path, now):
            skipped += 1
            continue
        gigs += 1
        size += path.stat().st_size
        if not dry_run:
            path.unlink(missing_ok=True)

    return CleanReport(days=days, gigs=gigs, size=size, skipped=skipped)


def run_cleanup(
    gig_days: int = GIG_TTL_DAYS,
    dry_run: bool = False,
    quiet: bool = False,
) -> CleanReport:
    """Remove the old data files and print the result."""
    report = clean_data(time.time(), gig_days, dry_run)
    if quiet:
        return report
    verb = "would remove" if dry_run else "removed"
    print(
        f"{verb} {report.days} day files and {report.gigs} gig files, "
        f"{format_size(report.size)}",
        flush=True,
    )
    if report.skipped:
        print(f"kept {report.skipped} files younger than {CACHE_SECONDS} seconds", flush=True)
    return report


@cli.command
def clean(
    *,
    data_dir: Path | None = None,
    gig_days: Annotated[int, Parameter(env_var="GIGMAP_GIG_TTL_DAYS")] = GIG_TTL_DAYS,
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
