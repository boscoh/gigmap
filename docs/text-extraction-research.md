# Extract date ranges, opening hours and days of week from text (Python)

Researched on the web and tested locally in a venv.

Summary: no single module does both. Use one tool for date ranges and one for opening hours.

## Opening hours from text

| Package | Last release | Result |
|---|---|---|
| `parse-opening-hours` (import `opening_hours`) | 2021 | Works. See notes below. |
| `opening-hours-py` | 2026 | Rust parser for OpenStreetMap (OSM) syntax only. It does not parse prose. |
| `atlus` | 2026 | Converts address, phone and hours strings to OSM format. Not tested. |

`parse-opening-hours` notes:

- It parsed `Mon- Fri 9:00am - 5:30pm` and `Mon-Fri 9am-5pm, Sat 10am-2pm`.
- It returns one `{day, opens, closes}` entry per day.
- It fails on 24-hour times such as `10:00-17:00` with `TypeError: unknown type (AM, PM or 24H)`.
- Fix: pass `assume_type=TimeType.AM` (from `opening_hours.models.time import TimeType`).
- With the fix, `Open Tuesday to Sunday 10:00-17:00` gave 6 entries.
- Not tested: `M – F 10-7 SA 9-6 S 11-5` and `Tues./Thurs. 3-6 pm`.

```python
from opening_hours import OpeningHours
from opening_hours.models.time import TimeType

OpeningHours.parse("Tue-Sun 10:00-17:00", assume_type=TimeType.AM).json()
```

## Date ranges from text

| Package | Last release | Result |
|---|---|---|
| `chrono-python` (import `chrono_python`) | 2026 | Found `3 March to 28 April 2025` as one range. It does not parse `Tue-Sun 10am-5pm`. |
| `timefhuman` | 2026 | Wrong on a mixed sentence: bad dates and a swapped start and end. Avoid. |
| `DateRangeParser` | 2019 | Handles `27th-29th June 2010` and `30 May to 9th Aug`. Not maintained. Not tested. |
| `dateparser` | 2026 | Good for single dates. It does not extract ranges from prose. |
| `metadate`, `date-wrangler`, `dateperiodparser` | 2026 | Return ranges. Not tested. |
| `recognizers-text-suite` | 2019 | Last release `1.0.2a2`. Not tested. Not recommended. |

Test sentence: `Exhibition runs 3 March to 28 April 2025, open Tue-Sun 10am-5pm.`

`chrono_python.parse(text)` returns results with `.text`, `.start` and `.end` on range results.
A plain `ParsedResult` has no `.end`.

## Recommendation

1. Use `chrono_python` for date ranges.
2. Use `parse-opening-hours` for hours, with `assume_type=TimeType.AM`.
3. Split the text first. Pass the parts that name days and times to the hours parser.
4. For very messy text, use an LLM that returns JSON. Example: `nrimsky/openinghours`.

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
