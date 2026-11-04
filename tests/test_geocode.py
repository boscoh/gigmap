"""Tests for the geocoder query rules."""

from gigmap import geocode


def test_the_country_is_added_once() -> None:
    assert geocode.address_query("1 High St, Kew VIC 3101") == (
        "1 High St, Kew VIC 3101, Australia"
    )
    assert geocode.address_query("1 High St, Kew VIC, Australia") == (
        "1 High St, Kew VIC, Australia"
    )
