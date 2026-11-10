"""Tests for the geocoder query rules."""

import asyncio

import pytest

from gigmap import geocode


def test_the_country_is_added_once() -> None:
    assert geocode.address_query("1 High St, Kew VIC 3101") == (
        "1 High St, Kew VIC 3101, Australia"
    )
    assert geocode.address_query("1 High St, Kew VIC, Australia") == (
        "1 High St, Kew VIC, Australia"
    )


def test_the_name_query_drops_a_leading_the() -> None:
    assert geocode.name_query("The Curtin", None) == "Curtin, Melbourne, Australia"


def test_the_name_query_drops_a_bracketed_gloss() -> None:
    """Nominatim finds no place for the long name with the gloss."""
    assert geocode.name_query(
        "ACMI (Australian Centre for the Moving Image)", "Flinders St, Melbourne 3000"
    ) == "ACMI, Melbourne, Australia"


def test_the_name_query_uses_the_suburb_of_the_address() -> None:
    """`Curtin, Melbourne` finds a street 10 km away. The suburb is the key."""
    assert geocode.name_query("The Curtin", "11 Nicholson St, Carlton 3053") == (
        "Curtin, Carlton, Australia"
    )


def test_the_locality_drops_the_state_and_the_postcode() -> None:
    assert geocode.locality("7 Prospect Hill Dr, Bundoora VIC 3083") == "Bundoora"
    assert geocode.locality("1 High St, Bundoora Victoria") == "Bundoora"
    assert geocode.locality("1 High St") is None
    assert geocode.locality(None) is None



def test_the_name_query_runs_before_the_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """The street answer of ACMI is the railway station, not the museum."""
    order: list[str] = []

    async def geocode_query(query: str) -> None:
        order.append(query)
        return None

    monkeypatch.setattr(geocode, "geocode", geocode_query)

    asyncio.run(geocode.geocode_venue("ACMI", "Flinders St, Melbourne 3000"))

    assert order[0] == "ACMI, Melbourne, Australia"
    assert order[1] == "Flinders St, Melbourne 3000, Australia"
    assert order[2] == "Melbourne, Victoria, Australia"


def test_the_locality_skips_a_trailing_country() -> None:
    """Art Hub ends the address with `, Australia`. It is not the suburb."""
    address = "1 Market Street, Newport VIC, Australia"
    assert geocode.locality(address) == "Newport"
    assert geocode.name_query("The Substation", address) == (
        "Substation, Newport, Australia"
    )


def test_a_name_answer_outside_the_suburb_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`Substation, Australia` finds a zone substation in Canberra."""
    canberra = {
        "latitude": -35.3152946,
        "longitude": 149.1645319,
        "display_name": "East Lake Zone Substation, Fyshwick, Canberra, ACT",
    }
    newport = {
        "latitude": -37.8442106,
        "longitude": 144.8829759,
        "display_name": "The Substation, 1 Market Street, Newport, 3015",
    }

    async def geocode_query(query: str) -> dict | None:
        # The bare name finds Canberra, and the address finds Newport.
        return canberra if query.startswith("Substation") else newport

    monkeypatch.setattr(geocode, "geocode", geocode_query)

    place = asyncio.run(
        geocode.geocode_venue("The Substation", "1 Market Street, Newport VIC, Australia")
    )

    assert place == newport
