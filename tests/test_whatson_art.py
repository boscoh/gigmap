from gigmap import whatson, whatson_art


def test_art_params():
    params = whatson.search_params("2026-11-05", "2026-11-05", whatson_art.SUBCATEGORIES)
    assert ("scs[]", "art") in params
    assert ("scs[]", "exhibition") in params
    assert ("scs[]", "theatre") not in params


def test_default_params():
    params = whatson.search_params("2026-11-05", "2026-11-05")
    for name in ("theatre", "music", "comedy"):
        assert ("scs[]", name) in params


def test_source_subcategories():
    assert whatson_art.WhatsonArtSource().subcategories == ("art", "exhibition")


def test_an_event_saved_by_whatson_serves_whatsonart():
    import asyncio

    from tests.test_whatson import TICKETED_EVENT, card

    calls = []

    async def text(url, *_a, **_k):
        calls.append(url)
        return TICKETED_EVENT

    gig = card("The Vaudeville Revue")
    first = whatson.WhatsonSource()
    first._text = text
    art = whatson_art.WhatsonArtSource()
    art._text = text

    async def run():
        await first.expand(gig, "2026-11-07", "2026-11-08")
        return await art.expand(gig, "2026-11-07", "2026-11-08")

    assert len(asyncio.run(run())) == 2
    assert len(calls) == 1
