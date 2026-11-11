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


async def geocode(query: str, *, postcode: str | None = None) -> dict | None:
    """Ask Nominatim for one query. None when the lookup fails.

    Nominatim can rank a street of the same name in another suburb first.
    So with a postcode, only a result whose postcode is near it is kept.
    """
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
                        "limit": 5,
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
    if postcode is not None:
        results = [
            r for r in results if near_postcode(r.get("display_name", ""), postcode)
        ]
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
    A room or level prefix, as in `Rm 5 Level 8/37 Swanston St`, hides the
    street number. So only the text after the last `/` stays.
    """
    street, comma, rest = address.partition(",")
    address = street.rsplit("/", 1)[-1].strip() + comma + rest
    if "australia" in address.lower():
        return address
    return f"{address}, Australia"

THE_RE = re.compile(r"^the\s+", re.IGNORECASE)
BRACKET_RE = re.compile(r"\([^)]*\)")
STATE_RE = re.compile(r"\b(VIC|Victoria)\b", re.IGNORECASE)
POSTCODE_RE = re.compile(r"\b\d{4}\b")
# A place on the border of two suburbs carries the postcode of the other
# one, so a postcode within this distance of the address still counts.
POSTCODE_RANGE = 5


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


def postcode(address: str | None) -> str | None:
    """Return the postcode of one address, or None.

    The last group of four digits, because a street number can hold four
    digits too: `1234 High St, Kew 3101` must keep `3101`.
    """
    found = POSTCODE_RE.findall(address or "")
    return found[-1] if found else None


def near_postcode(display_name: str, wanted: str) -> bool:
    """True when the postcode of a display name is close to the address one.

    A place on the border of two suburbs carries the postcode of the
    other one: the address says `3004` and the place says `3006`. So a
    small difference still counts, and a distant suburb does not.
    """
    found = POSTCODE_RE.findall(display_name)
    if not found:
        return False
    return abs(int(found[-1]) - int(wanted)) <= POSTCODE_RANGE


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
    """Try the address, then the name, and return the first place.

    The address names one building, and a name can match a namesake across
    the city: `Playhouse` finds a hall in Kew, and `Atrium` a hall in
    Hawthorn, while the street finds the venue. So the name answers only
    when the address finds nothing, and only when its answer names the
    suburb, because a bare name matches any place of that name in the
    country. A venue with no street relies on its name alone.
    """
    suburb = locality(address)
    street_postcode = postcode(address)
    queries = []
    if address:
        # Only the street answer must carry the postcode of the address.
        queries.append((address_query(address), None, street_postcode))
    if name and (suburb or not address):
        queries.append((name_query(name, address), suburb, None))
    if suburb:
        # ponytail: the suburb centre, not the venue. Better than no marker.
        queries.append((f"{suburb}, Victoria, Australia", None, None))
    for query, wanted, wanted_postcode in queries:
        place = await geocode(query, postcode=wanted_postcode)
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
