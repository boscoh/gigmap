# ArtsHub schedule extraction

**Date:** 2026-11-04
**Status:** Draft (design approved in conversation; no code written)
**Directory:** /Users/boscoh/p/gigmap

## Intent

Read the free text of one ArtsHub event page. Extract the date range, the
days of the week, and the opening hours. Put the result in `GigDetail`, and
show it in the event stream line. The logic is one pure standard-library
function, so a test needs no network.

## Facts that drive the design

- The feed `<description>` is one short sentence. It holds no hours. The
  long text is on the event page, in `div.the-content`.
- The page also holds `Event Starts` (for example `Oct 23, 2026 00:00`).
- Today `ArtshubSource.gig_detail()` makes no request. The server reads only
  `GigDetail.time` (`gig_time` in `gigmap/server.py`).
- `CachedSource.gig_detail` caches each detail in `data/gigs/artshub_<slug>.json`
  for 30 days. A failed read (None) is not cached.
- Real pages hold one-off sessions that are not opening hours. Examples:
  `official opening on Saturday 14 November, 1 to 4 pm`. The module must
  not report these.

## Decisions (agreed with the user)

| Topic | Decision |
|---|---|
| Method | Regular expressions and the standard library. No new dependency. This departs from `docs/text-extraction-research.md`: both libraries fail the rule "add a dependency only if the simple method fails", and neither knows a one-off session. |
| Text source | `gig_detail()` fetches the event page (one read for each event; cached 30 days). |
| Date range | Text range replaces `Gig.date` and `Gig.end_date` in the stream line when the two differ. This is display only. The day file and the feed date filter do not change. |
| Fields | `GigDetail` gets `date_start`, `date_end`, `days`, `hours`. `Gig` does not change. |
| Cache version | `DAYS_VERSION` stays `v9` (`Gig` does not change). Old files `data/gigs/artshub_*.json` hold no schedule. The deploy step deletes them once. |
| Effort | MVP. Tests: thorough. Docs: inline. |

## Scope

### In scope
- New `gigmap/schedule.py` with `extract_schedule`.
- New types `Hours` and `Schedule` in `gigmap/sources.py`. `GigDetail` inherits `Schedule`.
- `gigmap/artshub.py`: `parse_event_page`, and a real `gig_detail`.
- `gigmap/server.py`: `gig_time` and `detail_read` return `GigDetail | None`. `event_line` copies the fields and replaces the dates.
- Tests, and a short update to the `artshub.py` docstring and `docs/sources.md`.

### Out of scope
- LLM extraction, a page (HTML/JS) change, other sources, and a `Gig` field.
- A day list with no times. A date range that crosses a year end with no stated year. Forms such as `M – F 10-7 SA 9-6`. The result for these is empty. Mark each limit with a `ponytail:` comment.

## Types (in `gigmap/sources.py`)

```python
class Hours(BaseModel):
    """The opening hours of a group of days."""
    days: list[int]        # 0 = Monday ... 6 = Sunday, sorted
    opens: str             # "HH:MM", 24 hour
    closes: str            # "HH:MM", 24 hour


class Schedule(BaseModel):
    """The run of an event, as its text states it."""
    date_start: str | None = None   # ISO date
    date_end: str | None = None     # ISO date
    days: list[int] = Field(default_factory=list)    # union of the days of `hours`, sorted
    hours: list[Hours] = Field(default_factory=list)


class GigDetail(Schedule):
    detail_url: str
    time: str | None = None
```

## Function signatures

```python
# gigmap/schedule.py  (standard library only; no I/O)
def extract_schedule(text: str, reference: date | None = None) -> Schedule: ...
# `reference` gives the year of a date that states no year. None = today.
# The function never raises. Unclear text gives Schedule().

# gigmap/artshub.py
def parse_event_page(html: str) -> tuple[str, date | None]: ...
# Returns the text of `div.the-content` and the date of `Event Starts`.
# Uses bs4 (already a dependency). Missing parts give "" and None.

async def ArtshubSource.gig_detail(self, detail_url: str) -> sources.GigDetail | None: ...
# URL check as today. Fetch with self._text(detail_url). On SourceError: log, return None.
# Else: GigDetail(detail_url=..., **extract_schedule(text, reference).model_dump())

# gigmap/server.py
async def gig_time(...) -> sources.GigDetail | None   # rename allowed: gig_detail_of
async def detail_read(...) -> sources.GigDetail | None
def event_line(index, gig, detail: sources.GigDetail | None, venue) -> str
```

`gig_time` rule: when `gig.time` is set, return `GigDetail(detail_url=..., time=gig.time)` and make no read
(same as today). When `gig.detail_url` is empty, return None.

`event_line` rule: set `data["time"]` from `detail.time`. Copy `date_start`, `date_end`, `days`, `hours`
into the line (inside `gig`). When `detail.date_start` exists and differs from `gig.date`, set
`data["date"]`. When `detail.date_end` exists and differs from `gig.end_date`, set `data["end_date"]`.
Keep `data["end_date"]` as None when `date_end == date_start`.

## Algorithm of `extract_schedule`

1. Normalise: no-break space to space; en dash and em dash to `-`; collapse white space.
2. Split into sentences: at `. ` before a capital letter, at a newline, and at `;`.
   `6.30 pm` has no space after the dot, so it stays whole.
3. Drop a sentence that has a one-off cue (case-insensitive):
   `official opening`, `opening night`, `opening event`, `launch`, `preview`.
4. Hours. Scan each kept sentence from left to right. Find day groups and time ranges.
   - Day group: a day name or short name (`Mon`, `Tues`, `Thurs`, with an optional dot); a range
     (`Wednesday to Friday`, `Mon-Fri`, `Wednesday – Saturday`, a range may wrap: `Sunday to Thursday`);
     a list joined by `and`, `&` or `,`; or `daily` / `every day` (days 0 to 6).
   - Dated day: a day name followed by an optional comma and then a day number and a month name,
     or a month name (`Saturday 21 November`, `Friday, 6 November`). It is NOT a weekday.
     Skip it.
   - Time range: `10am - 4pm`, `1 to 4 pm`, `6.30 pm to 9.30 pm`, `10:00-17:00`, `10-5pm`.
     A separator is `-` or `to`. A minute separator is `:` or `.`.
   - Pairing: a time range pairs with the day group just before it. Day groups that follow each other
     with only `and`, `,` or `&` between them belong to one group. A time range with no day group
     gives no entry. After a pair, the day group is empty.
   - Meridiem: when the first time has no `am` or `pm`, take the one of the second time. If the start is
     then later than or equal to the end, the start is `am` (subtract 12). When neither time has a
     meridiem, read both as 24 hour.
   - Skip an entry with an hour above 23 or a minute above 59. Merge equal entries. Sort the days of an entry.
5. Dates. Find the first range in the whole kept text. Forms: `Month D to D`, `Month D to Month D`,
   `D Month to D Month`, `D-D Month`, each with an optional `from`, an optional ordinal suffix
   (`st nd rd th`), and an optional year. A range needs a separator (`to`, `-`, `until`). A single date is
   not a range. The year is the stated year, or `reference.year`. Give no range when the date is not real
   (use `date(...)` inside `try/except ValueError`) or when the end is before the start.
6. `days` = sorted union of the days of `hours`.

## Test cases (data table; `tests/test_schedule.py`)

Test style: plain functions, one `pytest.mark.parametrize` table, no network
(match `tests/test_artshub.py`). Days: 0 = Monday. `H(days, opens, closes)` means one `Hours` entry.
`-` in the last column means an empty `Schedule`.

| # | Input text | Expected |
|---|---|---|
| 1 | `Free Admission Monday to Friday, 10am – 4pm (closed public holidays)` | hours `[H([0,1,2,3,4],"10:00","16:00")]` |
| 2 | `Bayside Gallery is open Wednesday to Friday 11am – 5pm and Saturday and Sunday 1pm – 5pm.` | hours `[H([2,3,4],"11:00","17:00"), H([5,6],"13:00","17:00")]`; days `[2,3,4,5,6]` |
| 3 | `Learn stand-up comedy in Melbourne over 5-evenings from Sunday to Thursday, 6.30 pm to 9.30 pm, from January 24 to 28, 2027.` | hours `[H([0,1,2,3,6],"18:30","21:30")]`; dates `2027-01-24` to `2027-01-28` |
| 4 | `Screening continues until Saturday 21 November Wednesday – Saturday, 11am – 4pm` | hours `[H([2,3,4,5],"11:00","16:00")]`; no dates |
| 5 | `Please join us for the official opening on Saturday 14 November, 1 to 4 pm .` | - |
| 6 | `Join us at the opening event for Digital Fantasy on Sat 7 November 6pm – 8pm, click here for more details. Free to attend, all welcome.` | - |
| 7 | `Shows: Thursday 19 November 7.00pm (preview) Friday 20 November 6.00pm (opening night) Saturday 21 November 2.00pm (matinee) Saturday 21 November 7.00pm (closing night)` | - |
| 8 | `Drop in from 1.30pm to view the exhibition and experience Alice’s performance from 2pm. The gallery will stay open until 3.30pm.` | - |
| 9 | `Drop-in creative workshops for children will run in the adjacent workshop space from 12:30pm-2:30pm.` | - (no day) |
| 10 | `on Friday, 6 November 2026, from 6:00 pm to 8:00 pm at The Frame Shop, 131 Puckle Street,` | - (dated day) |
| 11 | `Exhibition runs 3 March to 28 April 2025, open Tue-Sun 10am-5pm.` | hours `[H([1,2,3,4,5,6],"10:00","17:00")]`; dates `2025-03-03` to `2025-04-28` |
| 12 | `Open Tuesday to Sunday 10:00-17:00` | hours `[H([1,2,3,4,5,6],"10:00","17:00")]` (24 hour) |
| 13 | `Open daily 10am-5pm` | hours `[H([0..6],"10:00","17:00")]` |
| 14 | `Mon-Fri 9am-5pm, Sat 10am-2pm` | hours `[H([0..4],"09:00","17:00"), H([5],"10:00","14:00")]` |
| 15 | `Monday to Friday 10-5pm` | hours `[H([0..4],"10:00","17:00")]` (start read as am) |
| 16 | `Saturday 1 to 4 pm` | hours `[H([5],"13:00","16:00")]` |
| 17 | `from January 24 to 28` with `reference=date(2027,1,1)` | dates `2027-01-24` to `2027-01-28` |
| 18 | `from 28 April to 3 March 2025` | no dates (end before start) |
| 19 | `30 February to 2 March 2026` | no dates (not a real date) |
| 20 | `Monday 25am-30pm` | - (bad time) |
| 21 | `A night of music.` | - |
| 22 | `` (empty string) | - |

Two small tests for the wiring (add to the existing files):

- `tests/test_artshub.py`: `parse_event_page` on a saved HTML snippet holds the body text and the date `2026-10-23`;
  a page with no `div.the-content` gives `("", None)`.
- `tests/test_source.py` (near `test_the_event_line_holds_every_gig_field`, line 267): `event_line` with a
  `GigDetail` that holds a schedule copies the four fields and replaces `date` and `end_date`; with `None` it
  keeps the gig dates and holds `time` None.

## Ideal State Criteria

### Core
- [ ] ISC-1: Text 2 gives two hour entries.
- [ ] ISC-2: `Wednesday to Friday` gives days 2, 3 and 4.
- [ ] ISC-3: `6.30 pm` gives `"18:30"`.
- [ ] ISC-4: `10am – 4pm` gives opens `"10:00"` and closes `"16:00"`.
- [ ] ISC-5: `from January 24 to 28, 2027` gives `2027-01-24` and `2027-01-28`.
- [ ] ISC-6: A date with no year takes the year of `reference`.
- [ ] ISC-7: `days` is the sorted union of the days of all hour entries.
- [ ] ISC-8: `schedule.py` imports only the standard library (and `sources`).
- [ ] ISC-9: `gig_detail()` fetches the page and passes the text of `div.the-content` to the module.
- [ ] ISC-10: `GigDetail` has `date_start`, `date_end`, `days` and `hours`.
- [ ] ISC-11: `event_line` copies the four fields.
- [ ] ISC-12: `event_line` replaces `date` and `end_date` with the text range when they differ.

### Edge cases
- [ ] ISC-13: Text with no date or time gives an empty `Schedule`, with no crash.
- [ ] ISC-14: Test 5 gives no hours.
- [ ] ISC-15: Test 7 gives no hours.
- [ ] ISC-16: A range that ends before it starts gives no range.
- [ ] ISC-17: A failed page read gives None and the gig line still leaves.

### Anti-criteria
- [ ] ISC-A-1: No new dependency in `pyproject.toml`.
- [ ] ISC-A-2: `Gig` and `DAYS_VERSION` do not change.
- [ ] ISC-A-3: No test touches the network.

## Todo sequence (for the worker)

1. `sources.py`: add `Hours`, `Schedule`; make `GigDetail` inherit `Schedule` (ISC-10).
2. `schedule.py` and `tests/test_schedule.py`: the function and the 22-row table (ISC-1 to 8, 13 to 16, A-1).
3. `artshub.py`: `parse_event_page`, real `gig_detail`, docstring update; tests (ISC-9, 17).
4. `server.py`: `gig_time`, `detail_read`, `event_line`; test in `tests/test_source.py` (ISC-11, 12).
5. Docs: `docs/sources.md` ArtsHub section ("the module always answers `time=None`" stays true; add the schedule), and the deploy note to delete `data/gigs/artshub_*.json` once.
6. Run `uv run pytest tests/ -q`, then one live check of `/api/artshub/events?start_date=...`.

Do the steps in this order. Run the tests after each step.

## Risks (premortem)

| Risk | Mitigation |
|---|---|
| Regex misses a form that ArtsHub uses | Accepted. The result is empty, never wrong. Add a test row for each new form. |
| Cloudflare limits the extra page reads (about 70 on a cold cache) | Pool size is 5 (`DETAIL_CONCURRENCY`). 40 pages read without error on 2026-11-04. A 403 gives `SourceError`, so `gig_detail` returns None and the gig line still leaves. A failed read is not cached. |
| Stale `data/gigs/artshub_*.json` files hide the new fields for 30 days | Deploy step: delete these files once. |
| The text range replaces a correct feed range | Accepted by the user (choice B). Display only. Revisit if wrong ranges appear. |
| A page layout change removes `div.the-content` | `parse_event_page` gives `("", None)`. The result is empty. The `parse_event_page` test shows the failure. |
| Text said something like `Opening Wednesday 11 November` and the run really starts then | The text range is not set from a single date. The feed range stays. |
