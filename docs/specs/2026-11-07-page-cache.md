# An event cache keyed by url

**Status:** Done
**Owner:** boscoh

## Summary

- **Goal:** two date ranges that overlap share the event page reads again.
- **Approach:** cache the range-free parse of each event page, keyed by source and url, in `data/events/`. The date fit still runs for each range.
- **Scope:** beat, artshub and whatson `expand`. whatson splits `parse_event` into a read and a fit.
- **Biggest risk:** a parser fix does not reach a cached event until `EVENTS_VERSION` goes up.
- **Ask:** approve decisions D1-D10, then build in the order of the waves.

## Decisions

| Id  | Decision                                                                         | Reason                                                       |
| --- | -------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| D1  | **Each source owns one `JsonCache` at module level, beside its pool**            | No factory, protocol or wrapper change                       |
| D2  | A source imports `stores`. `stores` never imports a source                       | `stores` imports no source module, so no cycle exists        |
| D3  | **Cache the parse, not the HTML**                                                | Small files. The parse runs once for each page               |
| D4  | Value: beat a time `str`, artshub a `sources.Schedule`, whatson a `WhatsonEvent` | Each is the last range-free step of its parse                |
| D5  | **`EVENTS_CACHE_SECONDS` = 7 days for all three.** Needs confirmation            | whatson sessions and prices change. One number               |
| D6  | **Bump `EVENTS_VERSION` with any change to a cached parse**                      | A cached value does not see a parser fix                     |
| D7  | The check sits at the call site in `expand`, not in `_html` / `_text`            | `_html` / `_text` also read the list page and the venue page |
| D8  | Only a read that returns text is parsed and saved                                | Each `_html` / `_text` returns text only on a 200            |
| D9  | `clean()` removes an event file by age or by an old version                      | `STALE_SUBDIRS` would remove the live tree each day          |
| D10 | fringe and artscentre stay out of scope                                          | Their payload is a day or a window, not a page               |

Rejected: cache the HTML. Simpler, and a parser fix needs no re-read, but every range re-parses and files are 30-150 KB.
Rejected: (a) the factory takes a `data_dir`. It changes `SOURCE_FACTORIES` and breaks an outside source.
Rejected: (c) `CachedSource` wraps the fetch. It needs an optional fetch method, which this repo bans.

## Scope

| In                                                             | Out                                              |
| -------------------------------------------------------------- | ------------------------------------------------ |
| `stores.event_cache`, `EVENTS_VERSION`, `EVENTS_CACHE_SECONDS` | A cache of a list page or a venue page           |
| The cache check in beat, artshub and whatson `expand`          | fringe `_diary` and artscentre windows           |
| The whatson split: `read_event` and `fit_event`                | A fix of the whatson `image` bug (see Risks)     |
| The event loop and the `events` count in `clean()`             | A config knob or a per-source life               |
| The move of `data_dir` and `DATA_DIR_ENV` into `stores`        | A change to the day file shape or `DAYS_VERSION` |
| `tests/conftest.py`, which points the data dir at a tmp dir    | -                                                |

## Findings

| Fact                                                                                   | Where                                                         |
| -------------------------------------------------------------------------------------- | ------------------------------------------------------------- |
| A day file key is the exact range pair, life 3 hours                                   | `gigmap/stores.py:87`, `:343`                                 |
| The calendar sets both ends, so each pick is a new key                                 | `gigmap/index.html:1028-1029`                                 |
| `SOURCE_FACTORIES` holds zero-argument callables                                       | `gigmap/server.py:45`                                         |
| `DataStores` is built for each member, in `source_dependency`                          | `gigmap/server.py:227-230`                                    |
| The data dir comes from `GIGMAP_DATA_DIR`, else `ROOT_DIR / "data"`                    | `gigmap/server.py:36`, `:242-245`                             |
| `stores` imports only `geocode` and `sources`                                          | `gigmap/stores.py:83`                                         |
| `JsonCache` holds a `max_age` rule. `load` gives None for a miss                       | `gigmap/stores.py:160-219`                                    |
| `STALE_SUBDIRS` removes a whole tree on each run                                       | `gigmap/stores.py:93`, `:290-294`                             |
| beat `expand` reads under `DETAIL_POOL`, then `parse_gig_detail` gives the time        | `gigmap/beat.py:339-343`                                      |
| `parse_gig_detail(html)` returns `str \| None`, with no range                          | `gigmap/beat.py:111`                                          |
| artshub `expand`: `parse_event_page`, `extract_schedule`, then `fit_dates`             | `gigmap/artshub.py:400-408`                                   |
| `extract_schedule` takes no range and never raises                                     | `gigmap/schedule.py:145-160`                                  |
| `Schedule` is a pydantic model                                                         | `gigmap/sources.py:115`                                       |
| whatson `expand` reads under `DETAIL_POOL`, then `parse_event(page, card, start, end)` | `gigmap/whatson.py:610-615`                                   |
| `parse_event` mixes the range into `parse_sessions`                                    | `gigmap/whatson.py:445`, `:477`                               |
| `session_dates` takes the year from `start_date`. The day text has no year             | `gigmap/whatson.py:296-323`                                   |
| `parse_event` returns `[]` when the page has no JSON-LD                                | `gigmap/whatson.py:463-464`                                   |
| `image` is always None: it tests `isinstance(data, str)` on a dict                     | `gigmap/whatson.py:467`                                       |
| `WhatsonArtSource` subclasses `WhatsonSource`, so it shares `expand`                   | `gigmap/whatson_art.py:20`                                    |
| Each `_html` / `_text` returns text only on a 200                                      | `beat.py:280-291`, `artshub.py:333-343`, `whatson.py:569-570` |
| The list reads also use `_html` / `_text`                                              | `beat.py:322`, `artshub.py:371`, `whatson.py:589`             |
| `url_key` gives the last path part of a url                                            | `gigmap/sources.py:184-187`                                   |
| Tests call `parse_sessions` and `parse_event` directly                                 | `tests/test_whatson.py:173-212`                               |

## Design

### How a source gets its cache

- `stores.event_cache(source, adapter)` returns a `JsonCache` in `data/events/`, life `EVENTS_CACHE_SECONDS`.
- The file name is `<source>_<EVENTS_VERSION>_<url_key>.json`. The cleanup matches the same rule.
- Its root is `stores.data_dir`, moved from `server.py`. It reads `GIGMAP_DATA_DIR` on each call.
- Each source builds its cache at module level, for example `EVENTS = stores.event_cache("beat", TypeAdapter(str))`.
- **Cost:** global state. `tests/conftest.py` sets `GIGMAP_DATA_DIR` to `tmp_path` for every test.

### The read

```python
key = sources.url_key(url)
time = EVENTS.load(key)
if time is None:
    async with DETAIL_POOL:
        html = await self._html(url)
    time = parse_gig_detail(html) or ""
    EVENTS.save(key, time)
return [gig.model_copy(update={"time": time or None})]
```

- A hit takes no pool slot and makes no request.
- A `SourceError` raises before the save. A miss, a 403 or a 5xx saves nothing.
- **Never cached:** `beat._page`, artshub `_feed_page`, whatson search pages, every venue read.

### The value of each source

| Source  | Cached value              | Then, for each range                                |
| ------- | ------------------------- | --------------------------------------------------- |
| beat    | `str`. `""` means no time | `time or None`                                      |
| artshub | `sources.Schedule`        | `fit_dates(schedule, start, end)`                   |
| whatson | `WhatsonEvent`            | `fit_event(event, card, start, end)` gives the gigs |

- beat stores `""`, because `JsonCache.load` gives None for a miss.
- artshub caches an empty `Schedule` too. It is a real answer.
- whatson and whatsonart both use the source name `"whatson"`. One parse serves both.

### The whatson split

- `WhatsonEvent` is a pydantic model in `whatson.py`: `venue_name`, `street`, `locality`, `postal`, `run_start`, `run_end`, `image`, `ticket`, `sessions`.
- `sessions` holds `(day_text, first_time)` pairs, not dates. The year needs the range.
- `read_event(page) -> WhatsonEvent | None`. None when the page has no JSON-LD. None is not saved.
- `fit_event(event, card, start, end) -> list[Gig]` runs `session_dates` and the run check.
- `parse_event(page, card, start, end)` stays as `fit_event(read_event(page), ...)`, so its tests stay.
- `parse_sessions` loses its range and gives the raw pairs. Its tests change.

### The cleanup

- `clean()` removes an event file when its age is at least `EVENTS_CACHE_SECONDS`, or its version part is not `EVENTS_VERSION`.
- `CleanReport` gains `events`. `run_cleanup` logs it.

### An outside source

- **The promise holds.** `GigSource`, `SourcePool` and `SOURCE_FACTORIES` do not change.
- An outside source works with no cache. It may call `stores.event_cache` to gain one.

## Changes

| Area                | Change                                                                                     |
| ------------------- | ------------------------------------------------------------------------------------------ |
| `gigmap/stores.py`  | Add `EVENTS_SUBDIR`, `EVENTS_VERSION = "v1"`, `EVENTS_CACHE_SECONDS`, `event_cache`        |
| `gigmap/stores.py`  | Add `DATA_DIR_ENV` and `data_dir`, moved from `server.py`                                  |
| `gigmap/stores.py`  | `clean()` gains the event loop. `CleanReport` gains `events`. Docstring: the file and rule |
| `gigmap/server.py`  | Delete `data_dir` and `DATA_DIR_ENV`. Use `stores.data_dir`. `run_cleanup` logs `events`   |
| `gigmap/beat.py`    | Add `EVENTS`. `expand` caches the time string                                              |
| `gigmap/artshub.py` | Add `EVENTS`. `expand` caches the `Schedule`, then runs `fit_dates`                        |
| `gigmap/whatson.py` | Add `WhatsonEvent`, `read_event`, `fit_event`, `EVENTS`. `parse_sessions` drops its range  |
| `gigmap/whatson.py` | `parse_event` calls `read_event` then `fit_event`. `expand` caches the `WhatsonEvent`      |
| `tests/conftest.py` | New. An autouse fixture sets `GIGMAP_DATA_DIR` to `tmp_path`                               |
| `docs/stream.md`    | Add the `data/events/` row to the cache table. Name the three reads that use it            |

## Tests

| Area                    | Cases                                                                                    |
| ----------------------- | ---------------------------------------------------------------------------------------- |
| `test_source.py` (beat) | A second `expand` makes no `_html` call. A page with no time caches `""` and gives None  |
| `test_source.py` (beat) | A `SourceError` saves no file                                                            |
| `test_artshub.py`       | A second `expand` makes no `_text` call. Two ranges fit one cached `Schedule` apart      |
| `test_artshub.py`       | The feed read is never saved                                                             |
| `test_whatson.py`       | `read_event` gives the raw pairs. `fit_event` gives the same gigs as `parse_event` today |
| `test_whatson.py`       | A second `expand` makes no read. A page with no JSON-LD saves nothing                    |
| `test_whatson.py`       | `parse_sessions` tests use the raw pairs                                                 |
| `test_whatson_art.py`   | An event saved by whatson serves whatsonart                                              |
| `test_cleanup.py`       | An old event file goes. A fresh one stays. An old version goes. `--dry-run` keeps all    |
| All                     | No test writes into `./data`                                                             |

## Risks and open questions

- **Risk: a parser fix misses the cache.** Bump `EVENTS_VERSION` with the fix. State the rule beside the constant.
- **Risk: stale facts.** beat `time`, artshub schedule, whatson sessions and `ticket` show late, up to 7 days.
- **Risk:** artshub `extract_schedule` falls back to `date.today()` for the year (`schedule.py:156`). A cached value keeps the year of the read day.
- **Risk:** a test writes into the real `data/`. The conftest fixture prevents it.
- **Risk:** two slugs collide in one source. Each detail regex ends in one slug, and the source name splits the sites.
- **Found:** `image` at `whatson.py:467` is always None. `WhatsonEvent` keeps today's value. A fix is a separate change.

Unverified: a `WhatsonEvent` round-trips `ticket` through JSON with no loss.

| Question                     | Assumed default                    | Owner  |
| ---------------------------- | ---------------------------------- | ------ |
| Life of an event file?       | 7 days for all (D5). Brief said 30 | boscoh |
| A shorter life for beat?     | No. beat time is the most stable   | boscoh |
| Fix the whatson `image` bug? | Later, in its own change           | boscoh |
| Cache fringe per day later?  | No, until the load is measured     | boscoh |

## Task breakdown

**The suite stays green after every wave.** Each source change keeps its old function names. Two tasks never write one file in one wave.

### A. Tasks

| ID  | Wave | Outcome                                                     | Needs      | Risk                         |
| --- | ---- | ----------------------------------------------------------- | ---------- | ---------------------------- |
| T0  | 0    | Callers of `data_dir`, `DATA_DIR_ENV`, `CleanReport` listed | -          | low                          |
| T0b | 0    | boscoh confirms D5 (7 days)                                 | -          | blocks the T1 constant       |
| T1  | 1    | `stores.event_cache`, the event cleanup, the moved data dir | T0, T0b    | **a test writes `./data`**   |
| T2  | 2    | beat `expand` caches the time                               | T1         | low                          |
| T3  | 2    | artshub `expand` caches the `Schedule`                      | T1         | the feed read gets cached    |
| T4  | 2    | whatson split and cache                                     | T1         | **a `fit_event` regression** |
| T5  | 3    | `docs/stream.md` names `data/events/`                       | T2, T3, T4 | low                          |
| T6  | 4    | The "A task is done when" checks pass, live check included  | T5         | live site down               |

### B. Launch

| ID  | Name                  | Tier | Thinking | Worktree branch          | Commit |
| --- | --------------------- | ---- | -------- | ------------------------ | ------ |
| T0  | `eventcache-research` | fast | low      | none                     | no     |
| T0b | human                 | -    | -        | none                     | no     |
| T1  | `eventcache-build`    | mid  | medium   | none (single writer)     | yes    |
| T2  | `eventcache-build-1`  | fast | low      | `T2/beat-event-cache`    | yes    |
| T3  | `eventcache-build-2`  | fast | low      | `T3/artshub-event-cache` | yes    |
| T4  | `eventcache-build-3`  | mid  | medium   | `T4/whatson-event-cache` | yes    |
| T5  | `eventcache-build`    | fast | minimal  | none                     | yes    |
| T6  | `eventcache-review`   | mid  | medium   | none                     | no     |

### C. Files and verify

| ID  | Allowed files                                                                        | Verify                                    |
| --- | ------------------------------------------------------------------------------------ | ----------------------------------------- |
| T0  | read only                                                                            | -                                         |
| T1  | `gigmap/stores.py`, `gigmap/server.py`, `tests/conftest.py`, `tests/test_cleanup.py` | `uv run pytest -q`                        |
| T2  | `gigmap/beat.py`, `tests/test_source.py` (beat part only)                            | `uv run pytest -q tests/test_source.py`   |
| T3  | `gigmap/artshub.py`, `tests/test_artshub.py`                                         | `uv run pytest -q tests/test_artshub.py`  |
| T4  | `gigmap/whatson.py`, `tests/test_whatson.py`, `tests/test_whatson_art.py`            | `uv run pytest -q tests/test_whatson*.py` |
| T5  | `docs/stream.md`                                                                     | `npx prettier --check docs/stream.md`     |
| T6  | read only                                                                            | the checks in "A task is done when"       |

### Checks after each merge

- `uv run pytest -q` in the parent checkout.
- `ls data/events 2>/dev/null` shows no file that a test wrote.

### Merge order

1. Wave 1: T1 only, in the parent checkout.
2. Wave 2: T4 first (most files), then T2, then T3. One branch at a time, checks after each.
3. Waves 3 and 4: one writer each, in the parent checkout.

## A task is done when

- Its own tests pass, and `uv run pytest -q` passes at the end of each wave.
- `grep -rn "DATA_DIR_ENV\|def data_dir" gigmap/server.py` finds no line.
- `grep -rn "event_cache" gigmap` finds uses in `stores`, `beat`, `artshub` and `whatson` only.
- `grep -rn "getattr\|hasattr" gigmap` finds nothing new.
- No test leaves a file in `./data/events/`.
- A live check: load `2026-11-14`, then `2026-11-13..14`. The second load reads no detail page of the first.
- A day file keeps its current shape, and `DAYS_VERSION` stays `v11`.
- The diff holds one new model (`WhatsonEvent`), no new pool and no config value.
