"""Tests for the key/value log form."""

import logging

from gigmap.logger import (
    BlockFormatter,
    format_kv,
    format_request,
    log_request,
)


def test_format_kv_aligns_the_values() -> None:
    lines = format_kv("beat response", {"status": 503, "url": "https://x"}).splitlines()

    assert lines[0] == "beat response"
    assert lines[1].startswith("status")
    assert lines[1].endswith("503")
    assert lines[2].startswith("url")
    assert lines[2].endswith("https://x")
    assert lines[1].index("503") == lines[2].index("https://x")


def test_format_request_aligns_the_fields() -> None:
    lines = format_request(
        "GET",
        "https://api.example.com/items",
        status=200,
        reason="OK",
    ).splitlines()

    assert lines[0].startswith("GET")
    assert lines[0].endswith("https://api.example.com/items")
    assert lines[1].startswith("status")
    assert lines[2].startswith("reason")
    assert lines[2].endswith("OK")


def test_log_request_emits_the_request_block() -> None:
    messages: list[str] = []

    log_request(messages.append, "GET", "https://x", status=200, reason="OK")

    assert messages == [format_request("GET", "https://x", status=200, reason="OK")]


def test_the_formatter_indents_later_lines() -> None:
    message = format_kv("cleanup failed", {"reason": "disk is busy"})
    record = logging.LogRecord(
        name="gigmap.server",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )

    lines = BlockFormatter("[%(levelname)s] %(message)s").format(record).splitlines()

    assert lines[0] == "[INFO] cleanup failed"
    assert lines[1] == " " * len("[INFO] ") + message.splitlines()[1]
