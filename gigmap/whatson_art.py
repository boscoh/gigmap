"""The What's On source for art and exhibitions.

It is the same site and the same parse as `whatson`, with two differences.
The subcategory filter is art and exhibitions. The row shape is one row
per run, as in `artshub`, so the art page shows one exhibition once.
"""

from gigmap import schedule, sources, whatson
from gigmap.schedule import fit_dates

SUBCATEGORIES = ("art", "exhibition")

SOURCE = sources.SourceInfo(
    title="Melb Art Map",
    heading="Melb Art Map",
    name="whatsonart",
    home_url=whatson.SEARCH_URL,
    noun="exhibits",
)


def weekday_of(day_text: str) -> int | None:
    """Return the weekday of one session day text, or None for no day."""
    head = day_text.strip()[:3].title()
    return schedule.DAYS_TEXT.index(head) if head in schedule.DAYS_TEXT else None


def run_schedule(event: whatson.WhatsonEvent) -> sources.Schedule:
    """Return the run of one event as a Schedule, so `fit_dates` reads it.

    Each session day gives the weekday and the times of that day. Sessions
    with the same times share one Hours entry, and the day list holds every
    session weekday of the run.
    """
    groups: dict[tuple[str, str], list[int]] = {}
    for day_text, opens, closes in event.sessions:
        day = weekday_of(day_text)
        if day is None or not opens or not closes:
            continue
        groups.setdefault((opens, closes), []).append(day)
    hours = [
        sources.Hours(days=sorted(set(days)), opens=opens, closes=closes)
        for (opens, closes), days in groups.items()
    ]
    return sources.Schedule(
        date_start=event.run_start or None,
        date_end=event.run_end or None,
        days=sorted({day for entry in hours for day in entry.days}),
        hours=hours,
    )


class WhatsonArtSource(whatson.WhatsonSource):
    """Read art and exhibitions from whatson.melbourne.vic.gov.au."""

    subcategories = SUBCATEGORIES

    async def expand(
        self, gig: sources.Gig, start_date: str, end_date: str
    ) -> list[sources.Gig]:
        """Return one run row for one event, as the art page wants.

        The row holds the run of the event, not the queried window, as
        `artshub` gives, so the art page shows one exhibition once. The
        hours text comes from `fit_dates`: a one-day window gives the bare
        span, and a longer window names the days.
        """
        sessions = await super().expand(gig, start_date, end_date)
        if not sessions:
            return []
        # The base read saved the event parse, so this load makes no request.
        event = whatson.EVENTS.load(sources.url_key(gig.detail_url or ""))
        if event is None:
            return sessions
        _, time, hours = fit_dates(run_schedule(event), start_date, end_date)
        first = sessions[0]
        date = event.run_start or first.date
        return [
            first.model_copy(
                update={
                    "date": date,
                    "end_date": event.run_end if event.run_end != date else None,
                    "time": time,
                    "hours": hours,
                }
            )
        ]
