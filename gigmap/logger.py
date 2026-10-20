"""Logging setup for the gigmap server.

A plain text handler is installed on the root logger. Library loggers get a
level, and any handlers they installed (for example by uvicorn) are dropped so
their records propagate to the root handler instead.

Application code logs through ``log_kv``, which prints an event name and then
its fields as a block of key/value lines. Keys are left-justified, so every
value starts in the same column and a block is quick to scan.

Every HTTP read calls ``log_request``, so the beat, Fringe, and geocode
request lines have the same fields and the same block form.
"""

import logging
from collections.abc import Callable
from typing import Any

# Levels for app and third-party loggers. Each source logs its own request
# lines, so the httpx request line stays off.
LOGGER_LEVELS: dict[str, int] = {
    "__main__": logging.INFO,
    "gigmap": logging.INFO,
    "uvicorn": logging.INFO,
    "uvicorn.error": logging.INFO,
    "uvicorn.server": logging.INFO,
    "uvicorn.access": logging.WARNING,
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    "h11": logging.WARNING,
}

# Width of the key column in ``log_kv`` blocks: keys are left-justified to
# this width, so every value starts in the same column.
KEY_WIDTH = 10


class BlockFormatter(logging.Formatter):
    """Format a record, and align the later lines under its message."""

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        clone = logging.makeLogRecord(record.__dict__)
        clone.msg = ""
        clone.args = ()
        clone.exc_info = None
        clone.exc_text = None
        clone.stack_info = None
        prefix = super().format(clone)
        indent = " " * len(prefix)
        lines = message.splitlines() or [""]
        block = [prefix + lines[0]]
        block += [indent + line for line in lines[1:]]
        if record.exc_info:
            trace = self.formatException(record.exc_info)
            block += [indent + line for line in trace.splitlines()]
        if record.stack_info:
            block += [indent + line for line in record.stack_info.splitlines()]
        return "\n".join(block)


def _kv_line(key: str, value: Any) -> str:
    """Return one ``key  value`` line, with the key in the key column."""
    return f"{key.ljust(KEY_WIDTH)}  {value}".rstrip()


def format_kv(event: str, fields: dict[str, Any]) -> str:
    """Return an event and its fields as a block of aligned key/value lines.

    The event name comes first. Each field then gets its own line, with the key
    left-justified in a fixed column and the value after it. Example::

        beat response
          status     503
          url        https://beat.com.au/gig-guide/

    :param event: Short event name, such as ``"beat response"``.
    :param fields: Fields, printed in the given order, one per line.
    :return: Text for the log handler.
    """
    lines = [event]
    lines += [_kv_line(key, value) for key, value in fields.items()]
    return "\n".join(lines)


def format_request(method: str, url: str, **fields: Any) -> str:
    """Return an HTTP request as an aligned ``METHOD  URL`` line plus fields.

    The method sits in the key column and the URL is its value, so the URL and
    every field value start in the same column. Example::

        GET       https://api.example.com/items
        status    200
        reason    OK

    :param method: HTTP method, such as ``"GET"``.
    :param url: Request URL.
    :param fields: Further values, printed in the given order, one per line.
    :return: Text for the log handler.
    """
    rows = {method: url, **fields}
    return "\n".join(_kv_line(key, value) for key, value in rows.items())


def log_kv(emit: Callable[..., None], event: str, /, **fields: Any) -> None:
    """Log one event, with each field on its own aligned key/value line.

    Pass a bound logger method so the level comes from the caller::

        log_kv(LOGGER.info, "beat response", status=status, url=url)

    :param emit: Bound logger method, such as ``LOGGER.warning``.
    :param event: Short event name.
    :param fields: Field values, printed in the given order, one per line.
    """
    emit(format_kv(event, fields))


def log_request(
    emit: Callable[..., None], method: str, url: str, /, **fields: Any
) -> None:
    """Log one HTTP request, with its status and other fields aligned below.

    :param emit: Bound logger method, such as ``LOGGER.info``.
    :param method: HTTP method, such as ``"POST"``.
    :param url: Request URL.
    :param fields: Further values, printed in the given order, one per line.
    """
    emit(format_request(method, url, **fields))


def setup_logging(level: int | str = logging.INFO) -> None:
    """Install plain text logging on the root logger and set library levels.

    Call as early as possible, before the server serves a request.

    :param level: Root log level, as a number or a name like ``"INFO"``.
    """
    if isinstance(level, str):
        level = getattr(logging, level.upper())

    root_handler = logging.StreamHandler()
    root_handler.setLevel(level)
    root_handler.setFormatter(
        BlockFormatter(
            "[%(asctime)s] %(levelname)-8s %(message)s",
            datefmt="%X",
        )
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    for handler in root_logger.handlers[:]:
        handler.close()
        root_logger.removeHandler(handler)
    root_logger.addHandler(root_handler)

    for name, logger_level in LOGGER_LEVELS.items():
        logger = logging.getLogger(name)
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
        logger.setLevel(logger_level)
        # uvicorn sets propagate=False, which would black-hole its records once
        # the handlers above are removed.
        logger.propagate = True
