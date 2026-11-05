"""Tests for the key/value log form."""

import io
import logging
import re

from rich.console import Console
from rich.highlighter import NullHighlighter
from rich.text import Text

from gigmap.logger import (
    LEVEL_SHORT,
    RELOAD_MESSAGE,
    ReloadFilter,
    ShortLevelHandler,
    format_kv,
    format_request,
    log_incoming,
    log_kv,
    log_request,
)


def plain(markup: str) -> list[str]:
    """Return the visible lines of a markup block."""
    return Text.from_markup(markup).plain.splitlines()


def test_format_kv_aligns_the_values() -> None:
    lines = plain(format_kv("beat response", {"status": 503, "url": "https://x"}))

    assert lines[0] == "beat response"
    assert lines[1].split() == ["status", "503"]
    assert lines[2].split() == ["url", "https://x"]
    assert lines[1].index("503") == lines[2].index("https://x")


def test_the_keys_are_muted_grey() -> None:
    block = format_kv("beat response", {"status": 503})

    assert "[grey62]status    [/grey62]  503" in block
    assert "[grey62]beat" not in block


def test_a_bracket_in_a_value_is_escaped() -> None:
    lines = plain(format_kv("beat response", {"url": "https://x/[a]"}))

    assert lines[1].endswith("https://x/[a]")


def test_format_request_aligns_the_fields() -> None:
    lines = plain(
        format_request(
            "outgoing",
            "GET",
            "https://api.example.com/items",
            status=200,
            seconds="0.025",
        )
    )

    assert lines[0] == "outgoing"
    assert lines[1].split() == ["GET", "https://api.example.com/items"]
    assert lines[2].split() == ["status", "200", "0.025s"]
    assert lines[1].index("https") == lines[2].index("200")


def test_log_kv_emits_the_block_as_markup() -> None:
    messages: list[tuple[str, dict]] = []

    log_kv(
        lambda message, **kwargs: messages.append((message, kwargs["extra"])),
        "beat response",
        status=503,
    )

    assert messages == [
        (format_kv("beat response", {"status": 503}), {"markup": True})
    ]


def test_log_request_emits_the_request_block() -> None:
    messages: list[tuple[str, dict]] = []

    log_request(
        lambda message, **kwargs: messages.append((message, kwargs["extra"])),
        "GET",
        "https://x",
        status=200,
        seconds="0.025",
    )

    assert messages == [
        (
            format_request(
                "outgoing", "GET", "https://x", status=200, seconds="0.025"
            ),
            {"markup": True},
        )
    ]


def test_log_incoming_emits_the_request_block() -> None:
    messages: list[tuple[str, dict]] = []

    log_incoming(
        lambda message, **kwargs: messages.append((message, kwargs["extra"])),
        "GET",
        "/api/beat/events",
        status=200,
        client="1.2.3.4",
    )

    assert messages == [
        (
            format_request(
                "incoming",
                "GET",
                "/api/beat/events",
                status=200,
                client="1.2.3.4",
            ),
            {"markup": True},
        )
    ]


def test_the_key_column_is_ten_wide() -> None:
    lines = plain(format_kv("beat response", {"status": 503, "url": "https://x"}))

    assert lines[1] == "status      503"
    assert lines[2] == "url         https://x"


def test_the_level_name_has_three_letters() -> None:
    handler = ShortLevelHandler()
    record = logging.LogRecord("x", logging.WARNING, __file__, 1, "m", (), None)

    assert handler.get_level_text(record).plain == "WRN"
    assert LEVEL_SHORT["INFO"] == "INF"






def test_the_time_shows_on_every_line() -> None:
    buffer = io.StringIO()
    handler = ShortLevelHandler(
        console=Console(file=buffer, force_terminal=False, width=60),
        log_time_format="%H:%M",
        show_path=False,
        highlighter=NullHighlighter(),
        keywords=[],
        omit_repeated_times=False,
    )
    record = logging.LogRecord(
        "gigmap", logging.INFO, __file__, 1, "beat response", (), None
    )

    handler.emit(record)
    handler.emit(record)

    heads = [line for line in buffer.getvalue().splitlines() if "INF" in line]
    assert len(heads) == 2
    assert all(re.match(r"\d{2}:\d{2} INF beat response", line) for line in heads)


def test_a_dev_reload_is_not_a_warning() -> None:
    record = logging.LogRecord(
        "uvicorn.error",
        logging.WARNING,
        __file__,
        1,
        RELOAD_MESSAGE,
        ("WatchFiles", "gigmap/schedule.py"),
        None,
    )

    ReloadFilter().filter(record)

    assert record.levelno == logging.DEBUG
    assert record.levelname == "DEBUG"


def test_another_uvicorn_warning_stays_a_warning() -> None:
    record = logging.LogRecord(
        "uvicorn.error", logging.WARNING, __file__, 1, "boom", (), None
    )

    ReloadFilter().filter(record)

    assert record.levelno == logging.WARNING
