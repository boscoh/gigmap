"""Look up the coordinates of an address on Nominatim.

Nominatim allows one request per second, needs an application user agent,
and needs cached results. This module holds the only code that talks to
nominatim.openstreetmap.org.

See https://operations.osmfoundation.org/policies/nominatim/
"""

import asyncio
import logging
import re
import time

import httpx

from gigmap import sources
from gigmap.logger import log_request

GEOCODE_SECONDS = 1.1
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_HEADERS = {"User-Agent": "gigmap/0.1 (personal Melbourne gig guide)"}

LOGGER = logging.getLogger(__name__)

_geocode_lock = asyncio.Lock()
_last_geocode = 0.0


async def geocode(query: str) -> dict | None:
    """Ask Nominatim for one query. None when the lookup fails."""
    global _last_geocode

    async with _geocode_lock:
        delay = GEOCODE_SECONDS - (time.time() - _last_geocode)
        if delay > 0:
            await asyncio.sleep(delay)
        response = None
        started = time.perf_counter()
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
                level = LOGGER.info if response.status_code == 200 else LOGGER.warning
                log_request(
                    level,
                    "GET",
                    str(response.url),
                    status=response.status_code,
                    seconds=f"{time.perf_counter() - started:.3f}",
                    query=query,
                )
        except httpx.HTTPError as error:
            log_request(LOGGER.warning, "GET", NOMINATIM_URL, query=query, error=error)
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
        "display_name": first.get("display_name", ""),
    }


def address_query(address: str) -> str:
    """Add the country to an address, unless the address names it already.

    Nominatim finds nothing for `..., Australia, Australia`.
    """
    if "australia" in address.lower():
        return address
    return f"{address}, Australia"

THE_RE = re.compile(r"^the\s+", re.IGNORECASE)
BRACKET_RE = re.compile(r"\([^)]*\)")
STATE_RE = re.compile(r"\b(VIC|Victoria)\b", re.IGNORECASE)
POSTCODE_RE = re.compile(r"\b\d{4}\b")


def locality(address: str | None) -> str | None:
    """Return the suburb of one address, or None.

    The suburb sits in the last comma part, before the state and the
    postcode. The name query needs it: `Curtin, Carlton` finds the venue,
    and `Curtin, Melbourne` finds a street 10 km away.
    """
    parts = [part for part in (address or "").split(",") if part.strip()]
    # Art Hub ends the address with `, Australia` as its own part. That part
    # is the country, never the suburb.
    parts = [part for part in parts if part.strip().lower() != "australia"]
    if len(parts) < 2:
        # A street with no comma holds no separate suburb.
        return None
    suburb = POSTCODE_RE.sub("", STATE_RE.sub("", parts[-1]))
    return suburb.strip(" ,") or None


def name_query(name: str, address: str | None) -> str:
    """Return the query for one venue name, with no leading `The`.

    A bracketed gloss breaks the lookup: Nominatim finds no place for
    `ACMI (Australian Centre for the Moving Image)`, but it finds
    `ACMI`.
    """
    bare = " ".join(BRACKET_RE.sub(" ", name).split()) or name
    bare = THE_RE.sub("", bare).strip() or bare
    return f"{bare}, {locality(address) or 'Melbourne'}, Australia"


async def geocode_venue(name: str | None, address: str | None) -> dict | None:
    """Try the venue name, then the address, and return the first place.

    The name names the place itself, so it wins: `ACMI` is the museum,
    while its street answer is the railway station. A name that finds
    no place falls back to the address, which pins the street. A name
    answer counts only when it names the suburb: a bare name finds any
    place of that name in the country.
    """
    suburb = locality(address)
    queries = []
    if name:
        queries.append((name_query(name, address), suburb))
    if address:
        queries.append((address_query(address), None))
    if suburb:
        # ponytail: the suburb centre, not the venue. Better than no marker.
        queries.append((f"{suburb}, Victoria, Australia", None))
    for query, wanted in queries:
        place = await geocode(query)
        if place is None:
            continue
        if wanted and wanted.lower() not in place.get("display_name", "").lower():
            # `Substation, Australia` is a zone substation in Canberra.
            LOGGER.info("geocode mismatch: %r is not in %r", wanted, query)
            continue
        return place
    return None


async def enrich_venue(venue: sources.Venue) -> sources.Venue:
    """Add the coordinates to one venue.

    A source that gives the coordinates needs no lookup.
    """
    if venue.latitude is not None and venue.longitude is not None:
        return venue
    place = await geocode_venue(venue.name, venue.address)
    if place is None:
        venue.latitude = None
        venue.longitude = None
    else:
        venue.latitude = place["latitude"]
        venue.longitude = place["longitude"]
    return venue
