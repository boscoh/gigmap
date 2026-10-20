"""Tests for the key/value log form."""

import logging

from rich.markup import render

from gigmap.logger import (
    HTTPX_REQUEST,
    HttpRequestFilter,
    format_kv,
    format_request,
)


def plain(markup: str) -> list[str]:
    """Return the rendered lines of one log message."""
    return str(render(markup)).splitlines()


def test_format_kv_aligns_the_values() -> None:
    lines = plain(format_kv("beat response", {"status": 503, "url": "https://x"}))

    assert lines[0] == "beat response"
    assert lines[1].startswith("status")
    assert lines[1].endswith("503")
    assert lines[2].startswith("url")
    assert lines[2].endswith("https://x")
    assert lines[1].index("503") == lines[2].index("https://x")


def test_format_request_aligns_the_fields() -> None:
    lines = plain(
        format_request(
            "GET",
            "https://api.example.com/items",
            status="HTTP/1.1 200 OK",
        )
    )

    assert lines[0].startswith("GET")
    assert lines[0].endswith("https://api.example.com/items")
    assert lines[1].startswith("status")
    assert lines[1].endswith("HTTP/1.1 200 OK")


def test_the_http_filter_rewrites_an_httpx_line() -> None:
    record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=HTTPX_REQUEST,
        args=("POST", "https://api.example.com/items", "HTTP/1.1", 200, "OK"),
        exc_info=None,
    )

    assert HttpRequestFilter().filter(record) is True
    lines = plain(str(record.msg))

    assert lines[0].startswith("POST")
    assert lines[0].endswith("https://api.example.com/items")
    assert any("200 OK" in line for line in lines)
