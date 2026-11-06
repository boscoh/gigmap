"""Logging setup for the gigmap server.

Rich formatting is installed on the root logger. Library loggers get a level,
and any handlers they installed (for example by uvicorn) are dropped so their
records propagate to the root Rich handler instead.

Application code logs through ``log_kv``, which prints an event name and then
its fields as a block of key/value lines. Keys are left-justified, so every
value starts in the same column and a block is quick to scan.

Every outgoing HTTP read calls ``log_request``, and the server calls
``log_incoming`` for each incoming request. Both use the same block form,
with a direction line first. The sources log their own request lines, so the
client library lines stay off and no filter rewrites them.

Colors are the same for every block: the event name is plain, keys are muted
grey, and values have no color. The Rich handler does not highlight numbers,
URLs, or strings on its own.
"""

import logging
from collections.abc import Callable
from typing import Any

from rich.console import Console
from rich.highlighter import NullHighlighter
from rich.logging import RichHandler
from rich.markup import escape
from rich.style import Style
from rich.text import Text
from rich.theme import Theme

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
    # The reload watcher logs one line for each batch of raw file changes,
    # before it keeps only the .py files.
    "watchfiles": logging.WARNING,
}

# Width of the key column in ``log_kv`` blocks: keys are left-justified to
# this width, so every value starts in the same column.
KEY_WIDTH = 10

# Rich style of the keys in ``log_kv`` blocks: a fixed muted grey, so it looks
# the same in every terminal theme.
KEY_STYLE = "grey62"



def _kv_line(key: str, value: Any) -> str:
    """Return one ``key  value`` line, with the key in the key column."""
    label = f"[{KEY_STYLE}]{escape(key).ljust(KEY_WIDTH)}[/{KEY_STYLE}]"
    return f"{label}  {escape(str(value))}".rstrip()


def format_kv(event: str, fields: dict[str, Any]) -> str:
    """Return an event and its fields as a block of aligned key/value lines.

    The event name comes first. Each field then gets its own line, with the key
    left-justified in a fixed column and the value after it. The handler aligns
    the later lines under the event name. Example::

        beat response
        status     503
        url        https://beat.com.au/gig-guide/

    :param event: Short event name, such as ``"beat response"``.
    :param fields: Fields, printed in the given order, one per line.
    :return: Rich markup text for the log handler.
    """
    lines = [escape(event)]
    lines += [_kv_line(key, value) for key, value in fields.items()]
    return "\n".join(lines)


def format_request(direction: str, method: str, url: str, **fields: Any) -> str:
    """Return an HTTP call as a direction line and an aligned method block.

    The direction line comes first, so incoming and outgoing calls are easy
    to tell apart. The method sits in the key column and the URL is its
    value. The ``status`` and the ``seconds`` of the call share one line.
    Example::

        outgoing
        GET       https://api.example.com/items
        status    200 0.025s

    :param direction: ``"incoming"`` or ``"outgoing"``.
    :param method: HTTP method, such as ``"GET"``.
    :param url: Request URL.
    :param fields: Further values, printed in the given order, one per line.
        A ``status`` holds the answer code, and a ``seconds`` the time the
        call took. An ``error`` holds the failure of a call that got no
        answer. An ``attempt`` holds the try number of a failed call.
    :return: Rich markup text for the log handler.
    """
    status = fields.pop("status", None)
    seconds = fields.pop("seconds", None)
    rows: dict[str, Any] = {method: url}
    if status is not None:
        rows["status"] = f"{status} {seconds}s" if seconds is not None else str(status)
    rows.update(fields)
    block = [escape(direction)]
    block += [_kv_line(key, value) for key, value in rows.items()]
    return "\n".join(block)


def log_kv(emit: Callable[..., None], event: str, /, **fields: Any) -> None:
    """Log one event, with each field on its own aligned key/value line.

    Pass a bound logger method so the level comes from the caller::

        log_kv(LOGGER.info, "beat response", status=status, url=url)

    :param emit: Bound logger method, such as ``LOGGER.warning``.
    :param event: Short event name.
    :param fields: Field values, printed in the given order, one per line.
    """
    emit(format_kv(event, fields), extra={"markup": True})


def log_request(
    emit: Callable[..., None], method: str, url: str, /, **fields: Any
) -> None:
    """Log one HTTP request, with its status and other fields aligned below.

    :param emit: Bound logger method, such as ``LOGGER.info``.
    :param method: HTTP method, such as ``"POST"``.
    :param url: Request URL.
    :param fields: Further values, printed in the given order, one per line.
    """
    emit(format_request("outgoing", method, url, **fields), extra={"markup": True})


def log_incoming(
    emit: Callable[..., None], method: str, url: str, /, **fields: Any
) -> None:
    """Log one incoming HTTP request, with its status and other fields below.

    :param emit: Bound logger method, such as ``LOGGER.info``.
    :param method: HTTP method, such as ``"GET"``.
    :param url: Request path, with its query string.
    :param fields: Further values, printed in the given order, one per line.
    """
    emit(format_request("incoming", method, url, **fields), extra={"markup": True})

# The level column holds three letters, so a block line is short.
LEVEL_SHORT = {
    "DEBUG": "DBG",
    "INFO": "INF",
    "WARNING": "WRN",
    "ERROR": "ERR",
    "CRITICAL": "CRT",
}

# Column of the event line: a ``%H:%M`` time and a space take 6, and the three
# letter level and a space take 4 more.
MESSAGE_COL = 10

# Offset of the value in a block line: the key column and the two spaces
# after the key.
VALUE_PAD = KEY_WIDTH + 2

# Column of the values in a block, where a long value wraps.
VALUE_COL = MESSAGE_COL + VALUE_PAD



class ShortLevelHandler(RichHandler):
    """Show a three letter level name, in the colour of the full name."""

    def get_level_text(self, record: logging.LogRecord) -> Text:
        style = f"logging.level.{record.levelname.lower()}"
        name = LEVEL_SHORT.get(record.levelname, record.levelname)
        return Text.styled(name.ljust(3), style)

    def emit(self, record: logging.LogRecord) -> None:
        """Print a block, with a long value wrapped at the value column."""
        message = record.getMessage()
        # Only our own blocks have a key column, so any other message, and any
        # message with a traceback, goes through the log renderer.
        if record.exc_info or "\n" not in message or not getattr(record, "markup", False):
            super().emit(record)
            return
        lines = message.splitlines()
        head = logging.makeLogRecord(record.__dict__)
        head.msg, head.args = lines[0], ()
        super().emit(head)
        # The log renderer puts every line of a message at the message column,
        # so the field lines are printed here, where a long value can wrap
        # under its own first character.
        markup = getattr(record, "markup", False)
        room = self.console.width - VALUE_COL
        for line in lines[1:]:
            text = Text.from_markup(line) if markup else Text(line)
            key, value = text[:VALUE_PAD], text[VALUE_PAD:]
            parts = value.wrap(self.console, max(room, 8), overflow="fold") or [Text()]
            self._print(key, parts[0])
            for part in parts[1:]:
                self._print(Text(" " * VALUE_PAD), part)

    def _print(self, key: Text, value: Text) -> None:
        """Print one field line at the message column."""
        pad = Text(" " * MESSAGE_COL)
        self.console.print(pad + key + value, soft_wrap=True)


# uvicorn reports each reload of the dev server as a warning. A reload is
# routine, so this filter moves the record to DEBUG.
RELOAD_MESSAGE = "%s detected changes in %s. Reloading..."


class ReloadFilter(logging.Filter):
    """Move the uvicorn reload line from WARNING to DEBUG."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.msg == RELOAD_MESSAGE:
            record.levelno = logging.DEBUG
            record.levelname = "DEBUG"
        return True

def setup_logging(level: int | str = logging.INFO) -> None:
    """Install Rich logging on the root logger and set library levels.

    Call as early as possible, before the server serves a request.

    :param level: Root log level, as a number or a name like ``"INFO"``.
    """
    if isinstance(level, str):
        level = getattr(logging, level.upper())

    root_handler = ShortLevelHandler(
        # The time is muted grey, has no brackets, and no seconds, so the
        # block starts near the left edge.
        console=Console(
            stderr=True,
            theme=Theme(
                {
                    "log.time": Style(color=KEY_STYLE, dim=True),
                    # ANSI yellow is dull in many themes, so a warning gets a
                    # bright orange, with the same weight as the error level.
                    "logging.level.warning": Style(color="color(214)", bold=True),
                }
            ),
        ),
        log_time_format="%H:%M",
        # The time always shows, because a minute covers many lines.
        omit_repeated_times=False,
        # Drop the path column so ``log_kv`` lines keep their alignment.
        show_path=False,
        # No automatic highlighting: it colors numbers, URLs, and strings by
        # their shape. Only the markup from ``format_kv`` sets colors.
        highlighter=NullHighlighter(),
        # An empty list turns off the default keyword highlight, which would
        # print GET, POST, and the other HTTP methods in bold yellow.
        keywords=[],
    )
    root_handler.setLevel(level)

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

    # A dev reload is a routine event, not a warning.
    uvicorn_logger = logging.getLogger("uvicorn.error")
    uvicorn_logger.filters = [
        item for item in uvicorn_logger.filters if not isinstance(item, ReloadFilter)
    ]
    uvicorn_logger.addFilter(ReloadFilter())
