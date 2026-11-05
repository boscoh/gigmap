"""Read the run of an event from its page text: date range, days and hours.

One pure function, standard library only, no I/O. Unclear text gives an empty
Schedule. A form we miss gives an empty result, never a wrong one.
"""

import re
from datetime import date, timedelta

from .sources import Hours, Schedule

DAY = r"(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?"
MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
ORD = r"(?:st|nd|rd|th)?"
SEP = r"\s*(?:-|to|until)\s*"
TIME = r"(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?"

TOKEN = re.compile(
    rf"""\b(?P<dated>{DAY},?\s+(?:\d{{1,2}}{ORD}\s+)?{MONTH}\b)
    |\b{TIME}\s*(?:-|to)\s*{TIME}\b
    |\b(?P<range>{DAY}\s*(?:-|to)\s*{DAY})\b
    |\b(?:(?:every|each)\s+)?(?P<day>{DAY})\b
    |\b(?P<daily>daily|every day)\b""",
    re.I | re.X,
)
JOIN = re.compile(r"\s*(?:,|and|&|,\s*and)?\s*", re.I)
ONE_OFF = re.compile(r"official opening|opening night|opening event|launch|preview", re.I)
SENTENCE_END = re.compile(r"(?<=\.)\s+(?=[A-Z])|\n|;")

YEAR = r"(?:,?\s*(\d{4}))?"
MONTH_FIRST = re.compile(
    rf"\b({MONTH})\s+(\d{{1,2}}){ORD}{YEAR}{SEP}(?:({MONTH})\s+)?(\d{{1,2}}){ORD}{YEAR}\b", re.I
)
DAY_FIRST = re.compile(
    rf"\b(\d{{1,2}}){ORD}(?:\s+({MONTH}){YEAR})?{SEP}(\d{{1,2}}){ORD}\s+({MONTH}){YEAR}\b", re.I
)


def _day(word: str) -> int:
    return ["mon", "tue", "wed", "thu", "fri", "sat", "sun"].index(word.lower()[:3])


def _month(word: str) -> int:
    return ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"].index(
        word.lower()[:3]
    ) + 1


def _clock(h: str, m: str | None, meridiem: str | None) -> tuple[int, int] | None:
    """Return (hour, minute) in 24 hour form, or None for a bad value."""
    hour, minute = int(h), int(m or 0)
    if hour > 23 or minute > 59:
        return None
    if meridiem:
        hour = hour % 12 + (12 if meridiem.lower() == "pm" else 0)
    return hour, minute


def _pair(match: re.Match[str]) -> tuple[str, str] | None:
    """Return the opening and closing time of one time token, or None."""
    h1, m1, a1, h2, m2, a2 = match.groups()[1:7]  # after the `dated` group
    if not (a1 or a2 or (m1 and m2)):
        return None  # "24 to 28" is a date, not a time
    inherited = a1 is None and a2 is not None
    start = _clock(h1, m1, a1 or a2)
    end = _clock(h2, m2, a2 or a1)
    if not start or not end:
        return None
    if inherited and start >= end and start[0] >= 12:
        start = (start[0] - 12, start[1])
    return "%02d:%02d" % start, "%02d:%02d" % end


def _hours(match: re.Match[str], days: list[int]) -> Hours | None:
    pair = _pair(match)
    return Hours(days=sorted(days), opens=pair[0], closes=pair[1]) if pair else None


def _find_hours(sentences: list[str]) -> list[Hours]:
    found: list[Hours] = []
    for s in sentences:
        group: list[int] = []
        last_end = 0
        # A time that comes before its days, as in "11am - 3pm, Wednesday to Friday".
        ahead: tuple[str, str] | None = None
        ahead_end = 0
        for m in TOKEN.finditer(s):
            if m["dated"]:
                group, ahead = [], None
            elif m["range"] or m["day"] or m["daily"]:
                if m["daily"]:
                    days = list(range(7))
                elif m["day"]:
                    days = [_day(m["day"])]
                else:
                    a, b = re.split(r"\s*(?:-|to)\s*", m["range"], flags=re.I)
                    i, j = _day(a), _day(b)
                    days = list(range(i, j + 1)) if i <= j else [*range(i, 7), *range(j + 1)]
                joined = group and JOIN.fullmatch(s[last_end : m.start()])
                if ahead and not group and JOIN.fullmatch(s[ahead_end : m.start()]):
                    entry = Hours(days=days, opens=ahead[0], closes=ahead[1])
                    if entry not in found:
                        found.append(entry)
                    ahead = None
                group = group + days if joined else days
                last_end = m.end()
            elif group:
                entry = _hours(m, group)
                if entry and entry not in found:
                    found.append(entry)
                group = []
            else:
                ahead = _pair(m)
                ahead_end = m.end()
    return found


def _find_dates(text: str, year: int) -> tuple[str | None, str | None]:
    # ponytail: a range over a year end with no stated year gives no range
    best = None
    for form in (MONTH_FIRST, DAY_FIRST):
        m = form.search(text)
        if m and (best is None or m.start() < best[0].start()):
            best = (m, form)
    if best is None:
        return None, None
    m, form = best
    if form is MONTH_FIRST:
        mo1, d1, y1, mo2, d2, y2 = m.groups()
        mo2 = mo2 or mo1
    else:
        d1, mo1, y1, d2, mo2, y2 = m.groups()
        mo1 = mo1 or mo2
        y1 = y1 if m.group(2) else None
    try:
        start = date(int(y1 or y2 or year), _month(mo1), int(d1))
        end = date(int(y2 or y1 or year), _month(mo2), int(d2))
    except ValueError:
        return None, None
    if end < start:
        return None, None
    return start.isoformat(), end.isoformat()


def extract_schedule(text: str, reference: date | None = None) -> Schedule:
    """Return the date range, days and hours stated in the text."""
    try:
        text = text.replace("\xa0", " ").replace("–", "-").replace("—", "-")
        sentences = [" ".join(s.split()) for s in SENTENCE_END.split(text)]
        kept = [s for s in sentences if s and not ONE_OFF.search(s)]
        hours = _find_hours(kept)
        # ponytail: a day list with no times gives no hours
        # ponytail: forms such as "M - F 10-7 SA 9-6" are not read
        # ponytail: a table of dates such as "Thu 5 Nov 10am - 6pm" is not read;
        # it has the shape of a one-off session list, so a guess could be wrong.
        start, end = _find_dates(" ".join(kept), (reference or date.today()).year)
        days = sorted({d for h in hours for d in h.days})
        return Schedule(date_start=start, date_end=end, days=days, hours=hours)
    except Exception:  # never raise on odd text
        return Schedule()


def clock_text(hhmm: str) -> str:
    """Return "13:00" as "1:00pm", the form of the time of a gig."""
    hour, minute = int(hhmm[:2]), hhmm[3:]
    return f"{hour % 12 or 12}:{minute}{'pm' if hour >= 12 else 'am'}"


def fit_dates(schedule: Schedule, start_date: str, end_date: str) -> tuple[bool, str | None]:
    """Return whether the event runs in the chosen dates, and its time then.

    The dates are ISO strings. The event runs when one chosen date is inside
    its text range and on one of its days. The time is the opening time of
    the first such date. An event with no days, or no hours, has no time.
    """
    low, high = date.fromisoformat(start_date), date.fromisoformat(end_date)
    if schedule.date_start:
        low = max(low, date.fromisoformat(schedule.date_start))
    if schedule.date_end:
        high = min(high, date.fromisoformat(schedule.date_end))
    # Seven dates show every weekday, so a long range costs no more.
    for offset in range(min(7, (high - low).days + 1)):
        weekday = (low + timedelta(days=offset)).weekday()
        if not schedule.days or weekday in schedule.days:
            opens = next((h.opens for h in schedule.hours if weekday in h.days), None)
            return True, clock_text(opens) if opens else None
    return False, None
