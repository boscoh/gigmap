"""The What's On source for art and exhibitions.

It is the same site and the same parse as `whatson`. Only the subcategory
filter is different.
"""

from gigmap import sources, whatson

SUBCATEGORIES = ("art", "exhibition")

SOURCE = sources.SourceInfo(
    title="Melb Art Map",
    heading="Melb Art Map",
    name="whatsonart",
    home_url=whatson.SEARCH_URL,
)


class WhatsonArtSource(whatson.WhatsonSource):
    """Read art and exhibitions from whatson.melbourne.vic.gov.au."""

    subcategories = SUBCATEGORIES
