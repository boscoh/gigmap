# The total first, then each event as it arrives

**Status:** Done. All three waves landed.

**Owner:** boscoh

## Summary

- Goal: send the total before the per-event reads. Send each event when its read ends.
- Approach: split a list read into `candidates` and one `expand` per candidate. The stream runs `expand`.
- Total: the candidate count. `{"total": d}` lines correct it, so it ends exact.
- Scope: artshub, whatson, whatsonart. Beat, artscentre and fringe already comply.
- **Risk:** a cold day file is saved only at the end of a whole stream.
- The ask: done. The owner confirmed D1 and D4. All three waves landed.

## Decisions

| Id  | Decision                                                          | Reason                            |
| --- | ----------------------------------------------------------------- | --------------------------------- |
| D1  | First total = candidate count. Corrections follow. **Confirmed.** | Known before any per-event read   |
| D2  | A correction is a `{"total": d}` line. `d` can be negative.       | The page adds every `total` line  |
| D3  | New `candidates` and `expand`. No `expand` means identity.        | Four sources need no new code     |
| D4  | Day file = final list. Warm skips `expand`. **Confirmed.**        | A warm page reads no event page   |
| D5  | Keep the 502. Wait for a candidate list or a warm day file.       | The headers leave before line one |
| D6  | Per-member `total` lines. The page adds them into one total.      | No page change                    |
| D7  | A gig line still waits for its own venue read.                    | The rule of `docs/stream.md`      |

Rejected options for the total:

- Wait for the final count: breaks requirement 1.
- Candidate count, no correction: whatson shows `50 / 20`. Artshub never reaches its total.
- Count events, not gigs: the page counts rows, and `SourceInfo.noun` names rows.
- An `X-Gig-Total` header: a group has no total before the first list.

## Scope

| In                                                  | Out                                  |
| --------------------------------------------------- | ------------------------------------ |
| `GigSource`: `candidates`, `expand`                 | Stream the fringe per-day reads (Q2) |
| `CachedSource`: the cold and warm paths             | Stream the artscentre window reads   |
| `server.py`: an incremental `event_lines`           | A logic change in `index.html`       |
| artshub `_fit`, whatson `_event_gigs` to `expand`   | A dedupe across members              |
| whatsonart, by inheritance                          | A new cache directory                |
| Fix the stale docs and comment (see Contradictions) |                                      |

## Findings

### The list read of each source

| Source       | The list read today                              | Per-event reads |
| ------------ | ------------------------------------------------ | --------------- |
| `artshub`    | feed pages, then `gather` of `_fit` on each item | yes             |
| `whatson`    | `_candidates`, then `gather` of `_event_gigs`    | yes             |
| `whatsonart` | subclass of `WhatsonSource`                      | yes             |
| `fringe`     | `_diary` per day, `_event_index`, `_venue_index` | no              |
| `artscentre` | `_read_window` per `WINDOW_DAYS` (170), in order | no              |
| `beat`       | one `page` read, then `parse_gigs`               | no              |

### The stream today

- `wait_for_a_list` waits for the first member list that works.
- `pump` sends `{"total": len(gigs)}`, then the lines of `event_lines`.
- `event_lines` takes a whole list and yields in `as_completed` order.
- artshub and whatson put the time in the gig. Their `gig_detail` makes no request.
- artshub `_fit` keeps a gig when its page read fails. It drops a closed exhibit.
- whatson `_event_gigs` drops a failed event. One event can give many session gigs.
- `loadEvents` adds each `update.total`. `gigsText` shows `n / total` while `streaming`.
- The page never reads `index`.
- `CachedSource.gigs` caches the final list in `data/days/` for `DAYS_CACHE_SECONDS` (3 hours).

### Contradictions, not resolved

- `docs/stream.md` says `DAYS_VERSION` stays `v7`. `gigmap/stores.py` holds `v10`.
- An `index.html` comment says "The response header holds the count". The header is gone.

## Design

### The source protocol

- `candidates(start_date, end_date) -> list[Gig]`: the reads before any per-event read.
- `expand(gig, start_date, end_date) -> list[Gig]`: one event read. It gives zero, one or many gigs.
- `expand` owns its pool: artshub `PAGE_CONCURRENCY` (5), whatson `DETAIL_CONCURRENCY` (3).
- All `expand` reads run at the same time, and the pool queue keeps the candidate order.
- On a `SourceError`, `expand` logs it. artshub keeps the gig. whatson gives `[]`.
- `gigs` stays, as `candidates` plus a `gather` of `expand`.
- No `expand`: `candidates` is `gigs`, and `expand` gives `[gig]`. The server uses `getattr`.

### The total

- A member sends `{"total": len(candidates)}` before its first `expand` starts.
- An `expand` that gives `k` gigs sends `{"total": k - 1}` when `k != 1`.
- `k = 0` covers a dropped event: a closed exhibit, a whatson `SourceError`, or another error. Each sends `{"total": -1}`.
- An `expand` error affects that event only. The member continues the other events.
- A warm day file sends `{"total": len(gigs)}` and no correction.
- Each member keeps a running total: the sum of its `total` lines.
- A member that fails after its first total sends `{"total": -r}`. `r` is its running total minus its sent rows.
- At the end of a whole stream, the sum of `total` lines equals the row count.
- The page adds each `total` line, negative or positive, with no change (`total.value += update.total`).

### The stream

- `event_lines` takes an async source of gigs. It starts the reads of each gig on arrival.
- The venue dict stays, so a venue of many gigs costs one read.
- A gig line leaves when its own detail and venue reads end (D7).
- `expand` tasks finish in `as_completed` order. A slow event holds no other event.
- `index` becomes the emit count of the member. The page ignores it.
- A client disconnect cancels every `expand`, detail and venue task.

### The day cache

- Warm: `CachedSource` loads the day file. The member streams it as today.
- Cold: the member streams `candidates` and `expand`, then saves the gigs in candidate order.
- No save on a cancel, or on an `expand` error that is not a `SourceError`.
- A `SourceError` in `expand` follows the source rule, so the list still saves.
- The file still holds `list[Gig]`. `DAYS_VERSION` needs no raise.

### The failures

| The failure                    | The result                                       |
| ------------------------------ | ------------------------------------------------ |
| every candidate read fails     | 502 with the first `SourceError`, as today       |
| one candidate read fails       | a log line. The member sends no line             |
| a member fails after its total | a log line, `{"total": -r}`, the member stops    |
| `expand` gives a `SourceError` | artshub keeps the gig. whatson drops the event   |
| `expand` raises another error  | a log line, `{"total": -1}`, no day file save    |
| a detail or venue read fails   | as today: null `time`, or null `venue` + `error` |

## Changes

| Area                | Change                                                               |
| ------------------- | -------------------------------------------------------------------- |
| `gigmap/sources.py` | add `ExpandingSource` with `candidates` and `expand`. Done in wave 1 |
| `gigmap/stores.py`  | `CachedSource`: `cached_gigs`, `candidates`, `expand`, `save_gigs`   |
| `gigmap/server.py`  | `pump`: the warm and cold paths, the correction lines                |
| `gigmap/server.py`  | `event_lines`: take an async source of gigs                          |
| `gigmap/server.py`  | `wait_for_a_list`: wait for a warm file or a candidate list          |
| `gigmap/artshub.py` | `_fit` to `expand`. Remove the `ponytail:` comment                   |
| `gigmap/whatson.py` | `_candidates` to `candidates`, `_event_gigs` to `expand`             |
| `gigmap/index.html` | fix the stale `total` comment. No logic change                       |
| `docs/stream.md`    | the total rule, the correction line, `DAYS_VERSION`                  |

## Tests

| Area         | Cases                                                                |
| ------------ | -------------------------------------------------------------------- |
| the total    | `{"total": n}` leaves before the first `expand` starts               |
| the total    | sum of `total` = rows: artshub drop, whatson 3 sessions, a failure   |
| the order    | a blocked `expand` holds no line of another event                    |
| the order    | a gig line leaves before the last `expand` ends                      |
| the cache    | a cold stream saves the day file. A second request calls no `expand` |
| the cache    | a cancelled stream saves no day file                                 |
| the failures | all candidate reads fail: 502. One fails: the other member streams   |
| the identity | beat, artscentre and fringe stream as today                          |
| keep         | `tests/test_source.py` and `tests/test_merge.py` still pass          |

## Safety and open questions

- A cold artshub page shows rows at the pace of the event pages, not after all of them.
- Two cold requests for one range both run `expand`. Today both run `gigs`, so the cost is equal.

| Question                                                                       | Assumed default                            | Owner  |
| ------------------------------------------------------------------------------ | ------------------------------------------ | ------ |
| Q1: does "immediately" include the venue read?                                 | yes (D7). Confirmed                        | boscoh |
| Q2: stream fringe per day, with a per-day total?                               | no. Not per event. Confirmed               | boscoh |
| Q3: is a total that moves up and down clear to a reader?                       | yes. It ends exact. confirmed              | boscoh |
| Q4: describe the streaming, reading and updating strategy in one readable doc? | yes. Grow `docs/stream.md` into it         | boscoh |
| Q5: does the stream fetch the events in order, or at the same time?            | at the same time. Each pool caps the reads | boscoh |

Assumption: the whatson search gives each event card once. `_candidates` dedupes by url.

## Order of work

Three waves. Tasks of one wave share no file, so agents can run them at the same time. Each task works from this spec alone. The `Design` section is the contract between tasks.

**Wave 1 — one agent, the protocol.**

Add `candidates` and `expand` to `GigSource` in `gigmap/sources.py`, and the identity rule in the server. No behaviour change. The suite stays green.

**Wave 2 — four agents, after wave 1. No two tasks share a file.**

| Task    | Files                                                                                              |
| ------- | -------------------------------------------------------------------------------------------------- |
| stream  | `gigmap/server.py`, `gigmap/stores.py`, `tests/test_source.py`, `tests/test_merge.py`              |
| artshub | `gigmap/artshub.py`, `tests/test_artshub.py`                                                       |
| whatson | `gigmap/whatson.py`, `gigmap/whatson_art.py`, `tests/test_whatson.py`, `tests/test_whatson_art.py` |
| docs    | `gigmap/index.html`, `docs/stream.md`                                                              |

The work of a task is its rows of the Changes table. The task owns every file in its row.

- A task merges only when its own tests pass.
- The stream task writes the stream rows of the tests table. The source tasks write their own unit tests.
- The docs task shares no file with the others, so it may start before wave 1.

**Wave 3 — one agent, after wave 2.**

Run the full suite. Check that every row of the tests table has a test.
