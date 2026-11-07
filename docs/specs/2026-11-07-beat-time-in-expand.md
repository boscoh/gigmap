# Beat reads its time in `expand`

**Status:** Done
**Owner:** boscoh

## Summary

- **Goal:** the per-gig detail read stops being a server concern.
- **Approach:** beat gains `candidates` and `expand`. `expand` reads the detail page and fills `time`.
- **Removed:** `gig_time`, `detail_read`, `DETAIL_POOL`, `GigDetail`, `gig_detail` and the `data/gigs/` cache.
- **Biggest risk:** overlapping date ranges re-read beat detail pages, because the per-URL time cache is gone.
- **Ask:** approve decisions D1-D6, then build in the order of work.

## Decisions

| Id  | Decision                                                            | Reason                                                   |
| --- | ------------------------------------------------------------------- | -------------------------------------------------------- |
| D1  | Beat reads the time in its own `expand`                             | Same flow as artshub and whatson                         |
| D2  | Beat owns `DETAIL_POOL`, `DETAIL_CONCURRENCY = 5`, at module level  | Keeps the beat.com.au cap at 5, shared across the app    |
| D3  | **`expand` always returns `[gig]`, never `[]`, never raises**       | A raise or `[]` drops the gig and sends a total of −1    |
| D4  | **Bump `DAYS_VERSION` from `v10` to `v11`**                         | Beat `v10` day files hold `time=None`                    |
| D5  | Delete `GigDetail`, `gig_detail` and the `data/gigs/` cache         | Only beat used them. Four sources stub them out          |
| D6  | Accept a missing time cached for 3 hours after a failed detail read | Simplest. Add a no-save flag only if it happens          |

Rejected: **B**, where beat reads each detail page in `gigs`. It blocks the total-first stream until every page is read.
Rejected: **C**, document only. It keeps four stub methods in the protocol.

## Scope

| In                                                    | Out                                                   |
| ----------------------------------------------------- | ----------------------------------------------------- |
| Beat `candidates` and `expand`                        | A shared pool or rate-limit abstraction               |
| Deletion of the server detail path and the gig cache  | Config knobs for pool sizes                           |
| Deletion of the `gig_detail` stubs in 4 sources       | A per-URL time cache inside beat (add only if needed) |
| The `DAYS_VERSION` bump, doc and test updates         | -                                                     |
|                                                       | A `date` field in gig files (that code is deleted)    |

Late change, after wave 4: the `getattr` lookup also went. `expand` is now a
member of `GigSource`, and artscentre and fringe hold an identity `expand`. The
server types its members as `stores.CachedSource`.
## The protocol

| Layer | Changes | Detail |
| --- | --- | --- |
| `GigSource` in `gigmap/sources.py` | Yes, a removal | `gig_detail` and `GigDetail` go. The contract is `candidates`, `expand` and `venue` |
| `ExpandingSource` | Deleted | No code reads it. The rule for `candidates` and `expand` lives in `docs/stream.md` and in the `GigSource` docstring |
| The stream | No | The line shape stays `{"index", "gig", "venue", "error"}`. A line waits for one read fewer |
| `data/days/` file | Yes, in effect | `time` comes from `expand`, so `DAYS_VERSION` is `v11` |
| A source outside this repo | Breaks | A source that implements `gig_detail` fails to satisfy the protocol. It must delete the method. A source with no `expand` fails too |

A new source must fill `time` in its own read. The server no longer reads a detail page.
The README promise stays true: a source replaces another with no server change.

## Where the time came from before this change

| Source     | Where the time comes from                                    | Reads per gig           |
| ---------- | ------------------------------------------------------------ | ----------------------- |
| beat       | the server calls `gig_detail`, then `parse_gig_detail`       | 1 detail page           |
| artshub    | `expand` reads the event page, under `PAGE_POOL`             | 1 event page            |
| whatson    | `expand` reads the event page, under `DETAIL_POOL`           | 1 page per event        |
| artscentre | the calendar row (`time_text(row["eventTimeValue"])`)        | 0                       |
| fringe     | the DailyDiary row (`local_time_text`)                       | 0                       |

`server.gig_time` returned early when `gig.time` was set, so only beat reached
`gig_detail`.

Artscentre and fringe needed no per-event read. Their `gig_detail` stubs made
no request, so the deletion changed nothing at runtime. Their lines then waited
only for the venue read.

## Design

### Beat `expand`

- `candidates` is `gigs`.
- If `gig.time` is set, or `detail_url` fails `DETAIL_URL_RE`, return `[gig]`.
- Read the page inside `async with DETAIL_POOL`.
- On a `SourceError`, return `[gig]` with no time.
- On success, return `[gig.model_copy(update={"time": parse_gig_detail(html)})]`.
- `parse_gig_detail` returns `str | None`, not a `GigDetail`.

### Stream

- A gig line waits for its own `expand` and its own venue read. Nothing else.
- A gig with no time leaves as soon as its venue is ready.
- Beat `expand` gives exactly one gig, so beat sends no total correction.

### The pool of a source

- The server `DETAIL_POOL` goes, so the cap of a per-event read moves to the source.
- A source that reads one page for each gig owns a pool. artshub `PAGE_POOL` (5). whatson `DETAIL_POOL` (3). beat gains `DETAIL_POOL` (5).
- artscentre and fringe need no pool. Their list rows carry the time.
- `docs/stream.md` states this rule, so a new source cannot read per event with no cap.

### Cache

| Case        | Today                          | After                             |
| ----------- | ------------------------------ | --------------------------------- |
| warm day    | N gig-file loads               | 0 reads. The time is in the day file |
| cold day    | N detail reads (or gig cache)  | N detail reads under the beat pool |
| failed read | not cached                     | **cached with no time for 3 h**   |

The cleanup removes an old `data/gigs/` tree at startup, and after that each
day. `STALE_SUBDIRS` in `gigmap/stores.py` names the directory.

## Changes

| Area                 | Change                                                                                                                          |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `gigmap/beat.py`     | Add `DETAIL_CONCURRENCY`, `DETAIL_POOL`, `candidates` and `expand`. Delete `gig_detail`. Change `parse_gig_detail` to return `str \| None` |
| `gigmap/server.py`   | Delete `GIG_TTL_DAYS`, `DETAIL_CONCURRENCY`, `DETAIL_POOL`, `gig_time` and `detail_read`                                        |
| `gigmap/server.py`   | `event_line` drops `time_text`. `event_lines.one()` awaits only the venue. Update the docstrings                                |
| `gigmap/sources.py`  | Delete `GigDetail` and `GigSource.gig_detail`                                                                                    |
| `gigmap/stores.py`   | Delete `GIGS_SUBDIR`, `self.gigs`, `CachedSource.gig_detail`, the gig loop in `clean()` and the gig-file docstrings              |
| `gigmap/stores.py`   | Drop `CleanReport.gigs` and `--gig-days`. Bump `DAYS_VERSION` to `v11`                                                           |
| `gigmap/stores.py`   | Add `STALE_SUBDIRS` (`gigs`), `remove_tree` and the `stale` field of `CleanReport`. `clean()` removes the tree. The startup loop and `gigmap clean` both call it |
| `gigmap/server.py`   | `run_cleanup` logs `stale=report.stale` |
| `gigmap/artshub.py` | Delete the `gig_detail` stub. No other change |
| `gigmap/whatson.py` | Delete the `gig_detail` stub. No other change |
| `gigmap/artscentre.py` | Delete the `gig_detail` stub. It makes no request, so the runtime does not change |
| `gigmap/fringe.py` | Delete the `gig_detail` stub and the note about it at the top of the module |
| `docs/stream.md` | Replace "detail read" with `expand`. Remove the `data/gigs/` row and the detail pool from the pool line. Add the rule: a source with a per-event read owns its pool |

## Tests

| Area                  | Cases                                                                                                       |
| --------------------- | ----------------------------------------------------------------------------------------------------------- |
| beat `expand` (new)   | Fills the time from the fixture. Returns `[gig]` with no time on a `SourceError`. Holds `DETAIL_POOL`       |
| beat concurrency      | The `expand` test holds `beat.DETAIL_POOL`. `test_two_streams_share_one_pool` goes, because no pool serves two streams |
| `test_source.py`      | Fakes drop `gig_detail` and list `time="8:30pm"` directly. Delete the listed-time, failed-detail and gig-cache tests |
| `test_merge.py`       | Drop the `gig_detail` fakes                                                                                 |
| `test_fringe.py`      | Delete the two `gig_detail` tests                                                                           |
| `test_cleanup.py`     | Delete the gig-file cases. Keep the day and venue cases. Add two: the stale `gigs` tree goes, and a `--dry-run` keeps it |
| `test_whatson.py`     | Check `test_the_detail_read_checks_the_url`. Keep it if it tests `expand`                                   |

## Risks and open questions

- **Risk:** more origin load on overlapping ranges. The cap of 5 and the 3 h day file limit it.
- **Risk:** a missed `gig_detail` caller breaks. Run `cymbal_refs gig_detail` first.
- **Risk:** an `expand` that raises drops a gig without a message. The new `expand` test covers it.
- **Risk:** `DAYS_VERSION` is shared, so the bump makes every source's day files cold once. The fringe re-read is the heaviest (many diary reads, capped by `REQUEST_POOL` at 8). Accepted: a per-source version is not worth it.
- **Risk:** the old `data/gigs/*.json` files have no reader. The cleanup removes the tree. A `--dry-run` reports it and keeps it.

Unverified: `test_whatson.py::test_the_detail_read_checks_the_url` tests `expand`, not `gig_detail`.

| Question                                        | Assumed default             | Owner  |
| ----------------------------------------------- | --------------------------- | ------ |
| Cache a missing time after a failed read?       | Yes, for 3 h (D6)           | boscoh |
| Delete old `data/gigs/` files automatically?    | Yes. The cleanup removes the tree | boscoh |
| Keep a per-URL time cache in beat?              | No, until load is measured  | boscoh |

## Task breakdown

Rule: one agent owns one file set, and two agents never edit the same file. Each task runs `uv run pytest -q` before it hands over. The suite is green after wave 1 and again after wave 3. Wave 2 breaks it on purpose, because the protocol method, the server path and the test fakes go together. Do not merge wave 2 alone.

| Wave | Task | Files | Work | Needs |
| --- | --- | --- | --- | --- |
| 0 | T0 Recon | none, read only | Run `cymbal_refs` on `gig_detail`, `GigDetail`, `gig_time`, `detail_read`, `DETAIL_POOL`, `GIG_TTL_DAYS`. Write the call sites and the test names to delete into a comment on this spec | - |
| 1 | T1 Beat reads its own time | `gigmap/beat.py`, the beat section of `tests/test_source.py` | Add `DETAIL_CONCURRENCY`, `DETAIL_POOL`, `candidates` and `expand`. `parse_gig_detail` gives `str \| None` (beat.py:105). Delete `BeatSource.gig_detail` (beat.py:322). Add the `expand` tests: the time fills, a `SourceError` gives `[gig]` with no time | T0 |
| 2a | T2 Delete the protocol method | `gigmap/sources.py`, `artshub.py`, `whatson.py`, `artscentre.py`, `fringe.py` | Delete `GigDetail` (sources.py:124) and `GigSource.gig_detail` (sources.py:154). Delete the 4 stubs: artshub:423, whatson:623, artscentre:464, fringe:832. Delete the fringe note at fringe.py:82 | T1 |
| 2b | T3 Delete the server path | `gigmap/server.py`, `gigmap/stores.py` | server: delete `GIG_TTL_DAYS` (99), `DETAIL_CONCURRENCY`/`DETAIL_POOL` (105-107), `gig_time` (285), `detail_read` (302), the `gig_days` argument of `run_cleanup` and `clean` (633, 663). `event_line` (337) drops `time_text`. `event_lines.one()` (373) awaits only the venue. stores: delete `GIGS_SUBDIR` (103), `self.gigs` (251), `CachedSource.gig_detail`, the gig loop in `clean()` (308-315), the `gigs` field of `CleanReport` (127). Bump `DAYS_VERSION` `v10` to `v11` (110) | T1 |
| 3a | T4 The tests | `tests/test_source.py` (all but the beat section), `test_merge.py`, `test_cleanup.py`, `test_fringe.py`, `test_whatson.py` | Fakes drop `gig_detail` and set `time` on the list gig. Delete the listed-time test (test_source.py:368), the failed-detail test (:381), the gig-cache test (:537), the two fringe tests (:631-651) and the gig cases of `test_cleanup.py`. Delete `test_two_streams_share_one_pool` in `test_merge.py`, because no pool serves two streams after the change | T2, T3 |
| 3b | T5 The docs | `docs/stream.md` | Replace "detail read" with `expand`. Remove the `data/gigs/` row. State that a source with a per-event read owns its pool | T2, T3 |
| 4 | T6 Integrate | none | `uv run pytest -q`. `grep -rn "gig_detail\|GigDetail\|gig_time\|detail_read" gigmap tests` gives no line. `cymbal_changed` reviews the diff | T4, T5 |

## Order of the waves

1. Wave 1 runs alone. `expand_of` used `getattr`, so the server called beat's new `expand` at once, and `gig_time` returned early because `gig.time` is set. The suite stayed green with one agent.
2. Wave 2 runs two agents in parallel: T2 owns `sources.py` and the 4 source files, T3 owns `server.py` and `stores.py`. The file sets do not touch.
3. Wave 3 runs two agents in parallel: T4 owns the test files and restores the green suite, T5 owns `docs/stream.md`.
4. Wave 4 runs one agent, after all the others.

## Two rules for the agents

- `tests/test_source.py` has two owners at two waves. T1 adds a new section at the end of the file. T4 edits the earlier tests only, and starts after T1 lands.
- T3 deletes `CachedSource.gig_detail` and T2 deletes the protocol method. Either order is fine inside wave 2, because the two file sets do not touch.

## A task is done when

- Its own tests pass. The full suite passes at the end of wave 1 and at the end of wave 3.
- No dead name stays: `grep` finds no use of the symbols the task deletes.
- The diff holds no new abstraction, pool or config value.
