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

    from test_whatson import TICKETED_EVENT, card

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

    merged = asyncio.run(run())
    assert len(calls) == 1
    assert len(merged) == 1
    run_row = merged[0]
    assert run_row.date == "2026-08-07"
    assert run_row.end_date == "2027-01-02"
    assert run_row.time == "7:30pm"
    assert run_row.hours == "Sat 7:30pm - 9:45pm, Sun 1pm - 2:30pm"


def test_a_one_day_query_gives_the_bare_span_of_the_run():
    import asyncio

    from test_whatson import TICKETED_EVENT, card

    async def text(*_a, **_k):
        return TICKETED_EVENT

    art = whatson_art.WhatsonArtSource()
    art._text = text

    rows = asyncio.run(
        art.expand(card("The Vaudeville Revue"), "2026-11-07", "2026-11-07")
    )
    run_row = rows[0]
    assert run_row.hours == "7:30pm - 9:45pm"
    assert run_row.date == "2026-08-07"


def test_run_schedule_of_one_event():
    from test_whatson import TICKETED_EVENT

    run = whatson_art.run_schedule(whatson.read_event(TICKETED_EVENT))
    assert run.days == [5, 6]
    assert [(one.days, one.opens, one.closes) for one in run.hours] == [
        ([5], "19:30", "21:45"),
        ([6], "13:00", "14:30"),
    ]
