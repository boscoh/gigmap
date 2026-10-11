# Data cleanup

The server writes files to `data/` and does not remove them. This document
specifies the cleanup.

## The data

| Directory | File | Content |
| --- | --- | --- |
| `data/days/` | `gigmap_<start>_<end>.json` | the parsed gig list for one date range |
| `data/gigs/` | `<slug>.json` | the start time of one gig |
| `data/venues/` | `<slug>.json` | the name, the address, and the coordinates of one venue |

`data/gigs/` grows fastest. A request for a date range reads one detail page for
each gig, and each answer becomes a file.

## The retention rules

| Class | Rule | Reason |
| --- | --- | --- |
| day file | remove when the range end date is before today | the calendar blocks a past day, so the app cannot ask for the file again |
| gig file | remove when the file is older than `--gig-days` (30) | a new lookup costs one request |
| venue file | keep | the Nominatim policy needs the cache. The server must not ask again for a venue with coordinates |

The day rule uses the date in the file name. A file with an unknown name stays.

## The safety rules

1. Keep each file younger than `CACHE_SECONDS` (600 seconds). A request can hold
   the file.
2. Match the name pattern only.
3. Do not remove a directory.
4. Do not remove a venue file.
5. A failed removal must not stop the run.
6. `--dry-run` removes nothing.

## The command

```bash
uv run gigmap clean --dry-run      # print the plan
uv run gigmap clean                # remove
uv run gigmap clean --gig-days 14  # a shorter gig retention
uv run gigmap clean --quiet        # print nothing
```

| Option | Default | Purpose |
| --- | --- | --- |
| `--data-dir` | `./data` | directory for the saved files |
| `--gig-days` | `30` | keep a gig time file for this many days |
| `--dry-run` | off | print the plan and remove nothing |
| `--quiet` | off | print nothing |

## The output

```text
removed 12 day files and 40 gig files, 1.1 MB
kept 3 files younger than 600 seconds
```

With `--dry-run`, the first words are `would remove`.

## The schedule

The server runs the cleanup in the FastAPI lifespan. The loop runs one time at
startup, then each 24 hours. `CLEANUP_INTERVAL_SECONDS` sets the period.

The loop uses the server's local date. The server is UTC, so pin the timezone
in the app command:

```bash
TZ=Australia/Melbourne uv run --no-sync gigmap serve --host 0.0.0.0 --port 8000
```

deployvm writes that pair into the supervisord `environment=` line.

cron is the alternative. It runs while the app is stopped:

```bash
0 4 * * * cd ~/p/gigmap && uv run gigmap clean --quiet
```

## The algorithm

```python
today = date of now
for path in data/days/gigmap_*.json:
    end = the date from the file name
    if end < today and age(path) >= 600:
        remove(path)

cutoff = now - gig_days * 86400
for path in data/gigs/*.json:
    if mtime(path) < cutoff and age(path) >= 600:
        remove(path)

# data/venues/*.json is never touched
```

## The growth

| Item | One file | One year |
| --- | --- | --- |
| day file | about 9 KB | 365 files, about 3 MB |
| gig file | about 4 KB | 10,000 gigs, about 40 MB |
| venue file | about 4 KB | 500 venues, about 2 MB |

## The tests

`uv run pytest` runs `tests/test_cleanup.py`. The tests cover the date rule, the
age rule, the dry run, the report, the loop, and the lifespan.

## Later work

The gig file holds no event date:

```json
{"time": "7:30pm", "detail_url": "https://beat.com.au/abby-hayes/"}
```

A `date` field makes the gig rule exact. The server has the gig in hand at
`gig_time`, so the change is small. The rule then becomes: remove when the event
date is before today.
