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
