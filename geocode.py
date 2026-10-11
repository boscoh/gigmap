"""Look up an address on Nominatim and build the map links.

Nominatim allows one request per second, needs an application user agent,
and needs cached results. This module holds the only code that talks to
nominatim.openstreetmap.org.

See https://operations.osmfoundation.org/policies/nominatim/
"""

import asyncio
import time
from urllib.parse import urlencode

import httpx

import sources

GEOCODE_SECONDS = 1.1
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_HEADERS = {"User-Agent": "gigmap/0.1 (personal Melbourne gig guide)"}

_geocode_lock = asyncio.Lock()
_last_geocode = 0.0


def map_urls(
    latitude: float, longitude: float, delta: float = 0.004
) -> tuple[str, str]:
    """Return the OpenStreetMap embed url and page url of one point."""
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


async def geocode(query: str) -> dict | None:
    """Ask Nominatim for one query. None when the lookup fails."""
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
    """Try the address, then the venue name, and return the first place."""
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


async def enrich_venue(venue: sources.Venue) -> sources.Venue:
    """Add the geocode and the map links to one venue."""
    place = await geocode_venue(venue.name, venue.address)
    if place is None:
        venue.latitude = None
        venue.longitude = None
        venue.display_name = None
        venue.map_embed_url = None
        venue.map_url = None
    else:
        venue.latitude = place["latitude"]
        venue.longitude = place["longitude"]
        venue.display_name = place["display_name"]
        venue.map_embed_url, venue.map_url = map_urls(
            place["latitude"], place["longitude"]
        )
    return venue
