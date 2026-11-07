"""Read Melbourne events from the Ticketmaster Discovery API v2.

The key is in `.env` as TICKETMASTER_API_KEY. The quota is 5000 calls a day
and 5 calls a second. This client waits `DELAY` seconds between live calls,
caches each answer on disk, and stops on HTTP 429.
"""

import asyncio
import hashlib
import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv

from gigmap import sources

URL = "https://app.ticketmaster.com/discovery/v2/events.json"
# A radius around the city, not the Victoria market, so Ballarat and Geelong stay out.
# `city=Melbourne` alone returns Florida.
MELBOURNE_LATLONG = "-37.8136,144.9631"
MELBOURNE_RADIUS_KM = 30
DELAY = 1.0
PAGE_SIZE = 100
MAX_WINDOW = 1000  # API limit: size * page must stay below this
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "ticketmaster"
CACHE_SECONDS = 6 * 3600

_last_call = 0.0


def _get(params: dict) -> dict:
    """Return one answer. Read the cache first. Never print the key."""
    global _last_call
    name = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
    path = CACHE_DIR / f"{name}.json"
    try:
        if time.time() - path.stat().st_mtime < CACHE_SECONDS:
            return json.loads(path.read_text())
    except (OSError, ValueError):
        pass  # no cache file, or a bad one: fetch again
    load_dotenv()
    wait = DELAY - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    response = httpx.get(
        URL, params={**params, "apikey": os.environ["TICKETMASTER_API_KEY"]}, timeout=30
    )
    _last_call = time.monotonic()
    if response.status_code == 429:
        raise RuntimeError("Ticketmaster rate limit reached. Stop and retry later.")
    response.raise_for_status()
    available = response.headers.get("Rate-Limit-Available")
    if available:
        print(f"ticketmaster quota left: {available}")
    data = response.json()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return data


def melbourne_events(
    start: str, end: str, max_pages: int = 10, segment: str = "Music"
) -> list[dict]:
    """Return the events of the market between two UTC times.

    Use the format `2026-11-07T00:00:00Z`. Keep the range short, because
    the API does not page past `MAX_WINDOW` results.
    """
    params = {
        "latlong": MELBOURNE_LATLONG,
        "radius": MELBOURNE_RADIUS_KM,
        "unit": "km",
        "countryCode": "AU",
        "segmentName": segment,
        "startDateTime": start,
        "endDateTime": end,
        "size": PAGE_SIZE,
        "sort": "date,asc",
    }
    events: list[dict] = []
    for page in range(max_pages):
        if PAGE_SIZE * page >= MAX_WINDOW:
            break
        data = _get({**params, "page": page})
        events += data.get("_embedded", {}).get("events", [])
        if page + 1 >= data["page"]["totalPages"]:
            break
    return events


# --- The gig source of the app ---

ZONE = ZoneInfo("Australia/Melbourne")
VENUE_PREFIX = "https://www.ticketmaster.com.au/venue/"
SOURCE = sources.SourceInfo(
    title="Melb Ticketmaster Gig Map",
    name="ticketmaster",
    home_url="https://www.ticketmaster.com.au/",
)
# The API status code of an event, as a ticket group and a label.
STATUS = {
    "onsale": (sources.TicketGroup.ON_SALE, "On sale"),
    "offsale": (sources.TicketGroup.UNAVAILABLE, "Off sale"),
    "cancelled": (sources.TicketGroup.UNAVAILABLE, "Cancelled"),
    "postponed": (sources.TicketGroup.UNAVAILABLE, "Postponed"),
    "rescheduled": (sources.TicketGroup.UNAVAILABLE, "Rescheduled"),
}


def utc_edge(day: str, days_after: int = 0) -> str:
    """Return the UTC time of the start of one Melbourne day."""
    start = datetime.fromisoformat(day).replace(tzinfo=ZONE) + timedelta(days=days_after)
    return start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def time_text(value: str | None) -> str | None:
    """Return `7:30pm` for `19:30:00`."""
    if not value:
        return None
    hour, minute = int(value[:2]), value[3:5]
    return f"{hour % 12 or 12}:{minute}{'am' if hour < 12 else 'pm'}"


def venue_link(venue: dict) -> str | None:
    """Return the venue address. It holds the coordinates, so no lookup runs."""
    place = venue.get("location") or {}
    if not venue.get("id") or not place.get("latitude"):
        return None
    query = {
        "name": venue.get("name", ""),
        "address": ", ".join(
            part
            for part in (
                (venue.get("address") or {}).get("line1"),
                (venue.get("city") or {}).get("name"),
            )
            if part
        ),
        "lat": place["latitude"],
        "lng": place["longitude"],
    }
    return f"{VENUE_PREFIX}{venue['id']}?{urlencode(query)}"


def parse_event(event: dict) -> sources.Gig:
    """Return one gig of one API event."""
    start = event["dates"]["start"]
    venues = (event.get("_embedded") or {}).get("venues") or [{}]
    venue = venues[0]
    klass = (event.get("classifications") or [{}])[0]
    genre = (klass.get("genre") or {}).get("name")
    segment = (klass.get("segment") or {}).get("name")
    code = event["dates"].get("status", {}).get("code", "")
    group, label = STATUS.get(code, (sources.TicketGroup.UNKNOWN, code or "Unknown"))
    prices = event.get("priceRanges") or [{}]
    low, high = prices[0].get("min"), prices[0].get("max")
    images = event.get("images") or []
    image = max(images, key=lambda i: i.get("width", 0), default={})
    return sources.Gig(
        artist=event["name"],
        detail_url=event.get("url"),
        date=start.get("localDate"),
        venue=venue.get("name"),
        venue_url=venue_link(venue),
        genres=[genre] if genre and genre != "Undefined" else [],
        categories=[segment] if segment else [],
        ticket=sources.Ticket(
            state=code or None,
            label=label,
            group=group,
            price=f"${low:g} - ${high:g}" if low is not None and high is not None else None,
            price_min=low,
            price_max=high,
            url=event.get("url"),
        ),
        image_url=image.get("url"),
        time=None if start.get("timeTBA") or start.get("noSpecificTime") else time_text(start.get("localTime")),
    )


class TicketmasterSource:
    """The `sources.GigSource` of the Discovery API. It holds no pool."""

    async def __aenter__(self) -> "TicketmasterSource":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def candidates(self, start_date: str, end_date: str) -> list[sources.Gig]:
        # The client blocks (it sleeps between calls), so it runs in a thread.
        try:
            events = await asyncio.to_thread(
                melbourne_events, utc_edge(start_date), utc_edge(end_date, 1)
            )
        except (httpx.HTTPError, RuntimeError, KeyError) as error:
            raise sources.SourceError(f"Ticketmaster failed: {error}") from error
        return [parse_event(event) for event in events]

    async def expand(
        self, gig: sources.Gig, start_date: str, end_date: str
    ) -> list[sources.Gig]:
        return [gig]

    async def venue(self, venue_url: str) -> sources.Venue:
        parts = urlsplit(venue_url)
        query = dict(parse_qsl(parts.query))
        if not venue_url.startswith(VENUE_PREFIX) or "lat" not in query:
            raise sources.SourceUrlError(
                f"venue_url must have the form {VENUE_PREFIX}<id>?lat=..&lng=.."
            )
        return sources.Venue(
            venue_url=venue_url,
            name=query.get("name"),
            address=query.get("address"),
            latitude=float(query["lat"]),
            longitude=float(query["lng"]),
        )


if __name__ == "__main__":
    for event in melbourne_events("2026-11-07T00:00:00Z", "2026-11-08T00:00:00Z", 1):
        venue = event["_embedded"]["venues"][0]["name"]
        print(event["dates"]["start"].get("localDate"), event["name"], "@", venue)
