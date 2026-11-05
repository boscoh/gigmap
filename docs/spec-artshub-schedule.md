# The ArtsHub schedule

> **Status: superseded.** This spec records the first design. It put the
> schedule in `GigDetail` and showed it in the stream line. The final design
> filters in `ArtshubSource.gigs` (`gigmap/artshub.py`). `fit_dates` in
> `gigmap/schedule.py` drops closed gigs and sets `time` to the opening time.
> `GigDetail`, `gig_detail` and `event_line` are unchanged. `DAYS_VERSION` is
> `v10`. See "The schedule" in `docs/sources.md`. The sections on the types,
> the stream line, the steps and the cache below are out of date. The
> algorithm and the test table of `extract_schedule` are still correct.

## The purpose

An ArtsHub event page holds a long text. The text states the date range, the
days of the week, and the opening hours. This change reads that text and puts
the result in `GigDetail`. The event stream line then shows the run of the
event. One pure function with the standard library does the work, so a test
needs no network. Source plan: `.pi/plans/2026-11-04-artshub-schedule/plan.md`.

## The result

Input text (from an event page, `div.the-content`):

```text
Bayside Gallery is open Wednesday to Friday 11am – 5pm and Saturday and Sunday 1pm – 5pm.
```

Output of `extract_schedule`:

```python
Schedule(
    date_start=None,
    date_end=None,
    days=[2, 3, 4, 5, 6],
    hours=[
        Hours(days=[2, 3, 4], opens="11:00", closes="17:00"),
        Hours(days=[5, 6], opens="13:00", closes="17:00"),
    ],
)
```

The stream line (`event_line` in `gigmap/server.py`) copies `date_start`,
`date_end`, `days` and `hours` into `gig`. A text range replaces `date` and
`end_date` when the two differ. This is display only.

## The types

All types are in `gigmap/sources.py`. `Hours` and `Schedule` are new.
`GigDetail` exists and gets a base class.

| Type | Field | Value |
| --- | --- | --- |
| `Hours` (new) | `days` | `list[int]`, 0 = Monday to 6 = Sunday, sorted |
| | `opens` | `"HH:MM"`, 24 hour |
| | `closes` | `"HH:MM"`, 24 hour |
| `Schedule` (new) | `date_start` | `str \| None`, ISO date |
| | `date_end` | `str \| None`, ISO date |
| | `days` | `list[int]`, sorted union of the days of `hours` |
| | `hours` | `list[Hours]` |
| `GigDetail` (changed) | base class | `Schedule` |
| | `detail_url` | `str` (no change) |
| | `time` | `str \| None` (no change) |

Lists use `Field(default_factory=list)`. `Gig` does not change.

## The functions

| Function | File | Signature | Rule |
| --- | --- | --- | --- |
| `extract_schedule` (new) | `gigmap/schedule.py` | `(text: str, reference: date \| None = None) -> Schedule` | Standard library only. No I/O. Never raises. Unclear text gives `Schedule()`. `reference` gives the year of a date with no year. `None` means today. |
| `parse_event_page` (new) | `gigmap/artshub.py` | `(html: str) -> tuple[str, date \| None]` | Returns the text of `div.the-content` and the date of `Event Starts`. Uses `bs4`. Missing parts give `""` and `None`. |
| `ArtshubSource.gig_detail` (changed) | `gigmap/artshub.py` | `async (self, detail_url: str) -> sources.GigDetail \| None` | Same URL check (`DETAIL_URL_RE`). Fetch with `self._text(detail_url)`. On `SourceError`: log, return `None`. Else `GigDetail(detail_url=..., **extract_schedule(text, reference).model_dump())`. |
| `gig_time` (changed) | `gigmap/server.py` | `async (...) -> sources.GigDetail \| None` | When `gig.time` is set: return `GigDetail(detail_url=..., time=gig.time)`, no read. When `gig.detail_url` is empty: return `None`. Else return the detail. A rename to `gig_detail_of` is allowed. |
| `detail_read` (changed) | `gigmap/server.py` | `async (...) -> sources.GigDetail \| None` | Same as today. Returns `None` on any error. |
| `event_line` (changed) | `gigmap/server.py` | `(index, gig, detail: sources.GigDetail \| None, venue) -> str` | See the rules below. |

`event_line` rules:

- Set `data["time"]` from `detail.time`, or `None`.
- Copy `date_start`, `date_end`, `days`, `hours` into `data`.
- When `detail.date_start` exists and differs from `gig.date`, set `data["date"]`.
- When `detail.date_end` exists and differs from `gig.end_date`, set `data["end_date"]`.
- When `date_end == date_start`, keep `data["end_date"]` as `None`.

## The algorithm

`extract_schedule` follows these stages.

| Stage | Action |
| --- | --- |
| 1. Normalise | No-break space to space. En dash and em dash to `-`. Collapse white space. |
| 2. Split | Split sentences at `. ` before a capital letter, at a newline, and at `;`. `6.30 pm` stays whole. |
| 3. Drop | Drop a sentence with a one-off cue (any case): `official opening`, `opening night`, `opening event`, `launch`, `preview`. |
| 4. Hours | Scan each kept sentence left to right. Find day groups and time ranges. Pair each time range with the day group just before it. |
| 5. Dates | Find the first date range in the kept text. Give no range for a false date or for an end before the start. |
| 6. Days | `days` is the sorted union of the days of `hours`. |

Rules of stage 4:

| Item | Rule |
| --- | --- |
| Day group | A day name or short name (`Mon`, `Tues`, `Thurs`, optional dot). A range (`Wednesday to Friday`, `Mon-Fri`, may wrap: `Sunday to Thursday`). A list joined by `and`, `&` or `,`. `daily` or `every day` gives days 0 to 6. |
| Dated day | A day name followed by an optional comma, then a day number and a month, or a month name (`Saturday 21 November`). It is not a weekday. Skip it. |
| Time range | `10am - 4pm`, `1 to 4 pm`, `6.30 pm to 9.30 pm`, `10:00-17:00`, `10-5pm`. Separator: `-` or `to`. Minute separator: `:` or `.`. |
| Pairing | Day groups joined only by `and`, `,` or `&` form one group. A time range with no day group gives no entry. After a pair, the day group is empty. |
| Meridiem | First time has no `am` or `pm`: take the one of the second time. If the start is then later than or equal to the end, the start is `am` (subtract 12). No meridiem on both: read both as 24 hour. |
| Bad value | Skip an entry with an hour above 23 or a minute above 59. Merge equal entries. Sort the days of an entry. |

Rules of stage 5:

- Forms: `Month D to D`, `Month D to Month D`, `D Month to D Month`, `D-D Month`.
- Each form has an optional `from`, an optional ordinal suffix (`st nd rd th`), and an optional year.
- A range needs a separator (`to`, `-`, `until`). A single date is not a range.
- The year is the stated year, or `reference.year`.
- Build dates with `date(...)` in `try/except ValueError`.

## The steps

Run all checks from `/Users/boscoh/p/gigmap`. The test command is
`uv run pytest tests/ -q`. Every step ends with this command green.

### Step 1: The shared types

- **Goal:** Add `Hours` and `Schedule`. Make `GigDetail` inherit `Schedule`.
- **Files:** change `gigmap/sources.py`.
- **Depends on:** none.
- **Do:**
  - Add `Hours` and `Schedule` as in "The types". Import `Field` from `pydantic`.
  - Change `class GigDetail(BaseModel)` to `class GigDetail(Schedule)`.
- **Done when:**
  - `uv run python -c "from gigmap.sources import Hours, Schedule, GigDetail; assert GigDetail(detail_url='x').hours == []"` exits 0.
  - `uv run pytest tests/ -q` passes.
- **Prompt:** "Do Step 1 of docs/spec-artshub-schedule.md"

### Step 2: The extract function

- **Goal:** Write `extract_schedule` and its 22-row test table.
- **Files:** add `gigmap/schedule.py`, add `tests/test_schedule.py`.
- **Depends on:** Step 1.
- **Do:**
  - Write `extract_schedule` as in "The algorithm". Standard library and `gigmap.sources` only.
  - Write one `pytest.mark.parametrize` table with the 22 rows of "The tests". Match the style of `tests/test_artshub.py`.
  - Add a `ponytail:` comment at each place in "The limits".
- **Done when:**
  - `uv run pytest tests/test_schedule.py -q` shows 22 passed.
  - `uv run pytest tests/ -q` passes.
  - `rg "^(import|from)" gigmap/schedule.py` shows only standard library modules and `gigmap.sources` (or `.sources`).
- **Prompt:** "Do Step 2 of docs/spec-artshub-schedule.md"

### Step 3: The ArtsHub page read

- **Goal:** Make `ArtshubSource.gig_detail` fetch the page and return the schedule.
- **Files:** change `gigmap/artshub.py`, change `tests/test_artshub.py`.
- **Depends on:** Step 1, Step 2.
- **Do:**
  - Add `parse_event_page(html)`. Read `div.the-content` and the `Event Starts` date (for example `Oct 23, 2026 00:00`).
  - Change `gig_detail` as in "The functions". Log a `SourceError` and return `None`.
  - Update the module docstring and the `gig_detail` docstring. The text now says that the read makes one request and adds the schedule. `time` stays `None`.
  - Add two tests: a saved HTML snippet gives the body text and `date(2026, 10, 23)`. A page with no `div.the-content` gives `("", None)`. Add one test of `gig_detail` that fakes `_text` and fails on any network call.
- **Done when:**
  - `uv run pytest tests/test_artshub.py -q` passes.
  - `uv run pytest tests/ -q` passes.
- **Prompt:** "Do Step 3 of docs/spec-artshub-schedule.md"

### Step 4: The stream line

- **Goal:** Pass `GigDetail` through `gig_time`, `detail_read` and `event_line`.
- **Files:** change `gigmap/server.py`, change `tests/test_source.py`.
- **Depends on:** Step 1.
- **Do:**
  - Change `gig_time` and `detail_read` to return `GigDetail | None`.
  - Change `event_line` as in "The functions". Update the callers in `event_lines`.
  - Fix any existing test that passes a time text to `event_line`.
  - Add two tests near `test_the_event_line_holds_every_gig_field`: a `GigDetail` with a schedule copies the four fields and replaces `date` and `end_date`. `None` keeps the gig dates and holds `time` as `None`.
- **Done when:**
  - `uv run pytest tests/test_source.py -q` passes.
  - `uv run pytest tests/ -q` passes.
  - `rg "time_text" gigmap/server.py` shows no match.
- **Prompt:** "Do Step 4 of docs/spec-artshub-schedule.md"

### Step 5: The docs

- **Goal:** Describe the schedule in the source docs.
- **Files:** change `docs/sources.md`.
- **Depends on:** Step 1.
- **Do:**
  - In the ArtsHub section, add the schedule fields and the page read. Keep the true statement that the feed holds no time (`time=None`).
  - Add the deploy note from "The cache".
- **Done when:**
  - `rg "artshub_\*.json" docs/sources.md` shows one match.
  - `uv run pytest tests/ -q` passes.
- **Prompt:** "Do Step 5 of docs/spec-artshub-schedule.md"

### Step 6: The final check

- **Goal:** Prove all checks of "The checks" and run one live read.
- **Files:** none (no change; report only).
- **Depends on:** Steps 1 to 5.
- **Do:**
  - Run `uv run pytest tests/ -q`.
  - Run `git diff --stat -- pyproject.toml gigmap/stores.py` and `git diff -- gigmap/sources.py`. Confirm the anti-criteria.
  - Start the server. Read `/api/artshub/events?start_date=<date>&end_date=<date>`. Confirm that a line holds `hours` for at least one gig.
- **Done when:**
  - The test run passes.
  - `pyproject.toml` and `gigmap/stores.py` show no diff, and `class Gig` shows no diff.
  - The live line holds the four new fields.
- **Prompt:** "Do Step 6 of docs/spec-artshub-schedule.md"

## The parallel plan

| Wave | Steps | Files touched | Why |
| --- | --- | --- | --- |
| 1 | Step 1 | `gigmap/sources.py` | All steps need the types. |
| 2 | Step 2, Step 4, Step 5 | Step 2: `gigmap/schedule.py`, `tests/test_schedule.py`. Step 4: `gigmap/server.py`, `tests/test_source.py`. Step 5: `docs/sources.md` | They need only the types. No file is in two steps. |
| 3 | Step 3 | `gigmap/artshub.py`, `tests/test_artshub.py` | It calls `extract_schedule` from Step 2. |
| 4 | Step 6 | none | It checks the whole result. |

No two steps in the same wave touch the same file.

## The tests

File: `tests/test_schedule.py`. Days: 0 = Monday. `H(days, opens, closes)` is one
`Hours` entry. `-` means an empty `Schedule`.

| # | Input text | Expected |
| --- | --- | --- |
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

Test style: plain functions, one `parametrize` table, no network. Write `[0..6]` as the full list in code.

## The cache

- `CachedSource.gig_detail` keeps each detail in `data/gigs/artshub_<slug>.json` for 30 days.
- `DAYS_VERSION` (in `gigmap/stores.py`, now `v9`) stays `v9`, because `Gig` does not change.
- Old detail files hold no schedule, so they would hide the new fields for 30 days.
- Deploy step, one time: delete the old files with `rm -f data/gigs/artshub_*.json`.
- A failed read (`None`) is not cached.

## The limits

| Limit | Handling |
| --- | --- |
| A day list with no times gives no hours | Accepted. Add `# ponytail:` in `extract_schedule`. |
| A date range over a year end with no stated year gives no range | Accepted. Add `# ponytail:` at the date stage. |
| Forms such as `M – F 10-7 SA 9-6` give no result | Accepted. Add `# ponytail:` at the day group scan. |
| A regex can miss a form that ArtsHub uses | The result is empty, never wrong. Add one test row for each new form. |
| Cloudflare can limit about 70 extra reads on a cold cache | `DETAIL_CONCURRENCY` is 5. A 403 gives `SourceError`, so `gig_detail` returns `None`. The gig line still leaves. |
| The text range replaces a correct feed range | Accepted. Display only. Revisit if wrong ranges appear. |
| A page layout change removes `div.the-content` | `parse_event_page` gives `("", None)`. The `parse_event_page` test shows the fault. |
| `Opening Wednesday 11 November` and the run starts then | A single date is not a range. The feed range stays. |

## The checks

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
- [ ] ISC-17: A failed page read gives `None` and the gig line still leaves.

### Anti-criteria

- [ ] ISC-A-1: No new dependency in `pyproject.toml`.
- [ ] ISC-A-2: `Gig` and `DAYS_VERSION` do not change.
- [ ] ISC-A-3: No test touches the network.
