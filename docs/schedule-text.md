# Read a date range, weekdays and hours from page text

A guide to the reader in `gigmap/schedule.py`. An event page states its run in
prose: a date range, the days of the week and the hours. The reader returns
four fields: `date_start`, `date_end`, `days` (0 = Monday to 6 = Sunday) and
`hours` (one entry for each day group, with `opens` and `closes` in 24 hour
form).

The ground truth is `docs/schedule-truth.md`. It holds 6 live event texts, the
expected answer, and the mismatch count.

## The rule

**An empty answer is better than a wrong one.** A reader that guesses "open
today" on a one-off show puts a gig in the wrong day. A reader that returns
nothing costs one missing time. So the reader returns nothing on doubt, and it
never raises on odd text.

Two corollaries:

- A weekday alone is not a schedule. Most texts name a weekday inside a
  one-off session ("opening reception on Saturday 14 November, 2pm-4pm").
- A list of dates with times is not a schedule. It has the same shape as a
  weekly schedule, so the reader cannot tell the two apart.

## How to read the text

1. **Clean the text.** Replace the non-breaking space and the en dash and em
   dash with a plain space and a hyphen.
2. **Split into sentences.** Collapse the spaces. Then split on a full stop
   with a space and a capital letter, on a newline, and on `;`.
3. **Drop the one-off sentences.** Remove a whole sentence that holds
   `official opening`, `opening night`, `opening event`, `launch` or
   `preview`.
4. **Scan the sentences for tokens**, in this order of priority: a dated day
   (`Wednesday 14 November`), a time pair (`11am - 4pm`), a day range
   (`Wednesday to Saturday`), a single day (`Wednesday`), and `daily`.
5. **Join a day group to a time.** Two tokens join only when a plain
   separator sits between them: a comma, `and`, `&`. Any other word starts a
   new group.
6. **Read the dates from the whole text.** Take the first date range, in the
   month-first or day-first form, and use the reference date for a missing
   year.

The token patterns and the design are in `docs/specs/2026-11-05-artshub-schedule.md`.

## The forms it reads

| Form | Example text | Result |
| --- | --- | --- |
| days, then hours | `Monday to Friday, 10am - 4pm` | days 0-4, 10:00-16:00 |
| two groups in one sentence | `Wednesday to Friday 11am - 5pm and Saturday and Sunday 1pm - 5pm` | days 2-4 at 11:00, days 5-6 at 13:00 |
| hours, then days | `Open 11am - 3pm, Wednesday to Saturday` | days 2-5, 11:00-15:00 |
| one day, `every` before it | `Dates Every Wednesday 14 October- 18 November \| 11:15am- 12:15pm` | day 2, 11:15-12:15, range set |
| a day range that wraps the week | `Sunday to Thursday, 6.30pm to 9.30pm` | days 0-3 and 6, 18:30-21:30 |
| `daily` or `every day` | `Open daily 10am-5pm` | all 7 days |
| a short time range | `Monday to Friday 10-5pm` | 10:00-17:00 |
| a 24 hour time | `Open Tuesday to Sunday 10:00-17:00` | 10:00-17:00 |
| a date range, day first | `3 March to 28 April 2025` | 2025-03-03 to 2025-04-28 |
| a date range, month first | `March 3 to April 28 2025` | 2025-03-03 to 2025-04-28 |
| a range with one month | `from January 24 to 28` | 24 to 28 January, reference year 2027 |
| a missing year | `9 September – 14 November 2026` | 2026 on both ends |
| a hyphen or `to` or `until` | `14 October- 18 November` | one range |

## The traps

- **A time pair needs a mark of a clock.** `24 to 28` in `January 24 to 28,
  2027` is a date. The reader accepts a pair only when a time holds `am` or
  `pm`, or when both sides hold minutes. This keeps a date out of the hours.
- **The meridiem carries across.** In `10-5pm` the start has no `pm`. The
  reader gives the mark of the end to the start.
- **A guessed meridiem can invert the pair.** In `Saturday 10-2pm` the start
  reads as 22:00. When the start is later than the end, and its hour is 12 or
  more, the reader takes 12 hours off the start, and gets 10:00 to 14:00.
- **Midnight and noon.** `hour % 12` gives 12am = 0 and 12pm = 12. A minute
  over 59 or an hour over 23 gives no time.
- **A span of 24 hours gives no clock.** `10am - 10am` has one start and one
  end, so the run never closes inside the day. `Schedule` drops such an entry,
  and the gig shows no time. The rule sits on the type, so a table of dates
  gives the same answer as prose.
- **`every` binds to the next day.** `Every Wednesday 14 October` states a
  day and then a start date. The date must not reset the day group.
- **A one-off sentence holds a real weekday.** `Saturday 14 November, 1 to
  4 pm` is a single session. Drop the sentence before the scan.
- **A reversed range is no range.** `from 28 April to 3 March 2025` and `30
  February to 2 March 2026` give nothing. The reader needs `end >= start` and
  a real calendar date.
- **An end with no start is no range.** `Screening continues until Saturday 21
  November` gives no dates. One date is not a range.
- **The year comes from the reference date.** Pass the `Event Starts` date of
  the page as `reference`, not `date.today()`.
- **Never raise.** One `try` block covers the whole read, and a bad value
  gives an empty `Schedule`.

## The forms it does not read

| Form | Example text | Why it stays empty |
| --- | --- | --- |
| a table of dates, not prose | `Hours \| Date \| Times \| Thu 5 Nov \| 10am - 6pm` | the shape is the same as a list of one-off sessions |
| a list of one-off shows | `Shows: Thursday 19 November 7.00pm (preview)` | each date names one show, not a weekly day |
| a time, then `on` and a day | `Open 10am-5pm on Wednesday` | `on` is not a join word |
| an end with no start | `Screening continues until Saturday 21 November` | one date is not a range |
| a vague run | `open 364 days a year` | it states no day and no time |
| a short day word | `M – F 10-7 SA 9-6` | the pattern needs three letters of the day |
| a day list with no hours | `Open Monday and Tuesday` | the days supply the hours, so no hours means no days |

A structured table is the job of `whatson.parse_sessions`, which reads the
`Hours` table of the page as data. Use a parser for prose only.

## History of the research

The research started on 2026-11-05, before the reader existed. The aim was to
find a package for the job:

1. **Test the packages on real page text.** Test on sentences such as
   `Exhibition runs 3 March to 28 April 2025, open Tue-Sun 10am-5pm.`
2. **Find no single package for both jobs.** The date packages read a range
   but not the hours. The hours packages read the hours but not the range.
3. **Reject the dependency.** Both candidates break the project rule "add a
   dependency only if the simple method fails". Neither knows a one-off
   session, so both would add a wrong day on an opening night.
4. **Write the reader instead.** `gigmap/schedule.py` uses the standard
   library and the `re` module. It holds the 26 cases in
   `tests/test_schedule.py`, and it gives 6 of 6 correct rows on the truth
   table.

The last step moved 2 of the 6 rows from wrong to correct: a time that comes
before its days, and a single day with `every` before it. The table records
this count in "Mismatch against `extract_schedule`".

## The packages looked at

The project uses neither of the two candidates. The table stays as a record of
the research.

| Package | Last release | Result |
| --- | --- | --- |
| `parse-opening-hours` (import `opening_hours`) | 2021 | Reads `Mon-Fri 9am-5pm`. Returns one entry each day. Fails on `10:00-17:00` until you pass `assume_type=TimeType.AM`. |
| `chrono-python` (import `chrono_python`) | 2026 | Reads `3 March to 28 April 2025` as one range. Does not read `Tue-Sun 10am-5pm`. |
| `opening-hours-py` | 2026 | A Rust parser for OpenStreetMap syntax only. It does not read prose. |
| `atlus` | 2026 | Converts address, phone and hours text to OpenStreetMap form. Not tested. |
| `timefhuman` | 2026 | Wrong on a mixed sentence: bad dates, and a start and end that swap. Avoid. |
| `DateRangeParser` | 2019 | Reads `27th-29th June 2010`. Not maintained. Not tested. |
| `dateparser` | 2026 | Good for one date. Does not find a range in prose. |
| `metadate`, `date-wrangler`, `dateperiodparser` | 2026 | Return ranges. Not tested. |
| `recognizers-text-suite` | 2019 | Last release `1.0.2a2`. Not tested. |

```python
from opening_hours import OpeningHours
from opening_hours.models.time import TimeType

OpeningHours.parse("Tue-Sun 10:00-17:00", assume_type=TimeType.AM).json()
```

## Where to look

- `docs/schedule-truth.md` — the 6 live texts, the expected answer, the
  mismatch count, and the forms that stay empty.
- `docs/schedule-truth.json` — the same rows for a machine, with the
  `reference` date of each page.
- `docs/specs/2026-11-05-artshub-schedule.md` — the design: the types, the
  algorithm, and the steps.
- `gigmap/schedule.py` — the reader. `extract_schedule` reads the text, and
  `fit_dates` tests the schedule against the chosen dates.
- `tests/test_schedule.py` — 26 cases for `extract_schedule` and 10 for
  `fit_dates`.
- `docs/sources.md`, section "The schedule" — how the server uses the fields.

## Sources

- https://github.com/MoralCode/parse-opening-hours
- https://pypi.org/project/opening_hours_py/
- https://whubsch.github.io/atlus/atlus.html
- https://pypi.org/project/chrono-python/
- https://github.com/alvinwan/timefhuman
- https://pypi.org/project/DateRangeParser/
- https://dateparser.readthedocs.io/en/latest/
- https://pypi.org/project/metadate/
- https://pypi.org/project/date-wrangler/
- https://pypi.org/project/dateperiodparser/
- https://blog.placelab.com/extract-hours-operation-text-no-time/
- https://github.com/nrimsky/openinghours/blob/code-model/prompt.py
