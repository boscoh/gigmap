# Server cache and cost control

**Status:** Draft. T2, T4 and T5 are not built. The page cleanup and the page
trim are built and uncommitted; **D17 reverts both.**
**Owner:** boscoh

## Summary

- **Goal:** hold the cost of one server request near zero when user traffic grows.
- **Approach:** keep the day-file cache and drop the Ticketmaster page cache (D17). Close two gaps: a refresh stampede and an unguarded rate limiter. Make a cut stream visible (D11-D13). Bound the memory (D15-D16).
- **Scope:** `read_list`, `validate_dates`, the stream queue, the delay in `_get`, and the stream end, in `gigmap/server.py` and `gigmap/index.html`.
- **Biggest risk:** a cold day file makes a visitor wait for two API calls. The single flight works inside one process only.
- **Ask:** build in wave order.

## Decisions

| Id  | Decision                                                                 | Reason                                                                  |
| --- | ------------------------------------------------------------------------ | ----------------------------------------------------------------------- |
| D1  | **Close the two gaps: a refresh stampede and an unguarded rate limiter** | No range-sharing gap exists: the UI asks for single days only           |
| D2  | **One shared `asyncio.Task` per (source, range)**                        | The task is the flight itself, and it removes its own key               |
| D7  | **`threading.Lock` in `_get` around the delay and the call**             | `candidates` runs in `asyncio.to_thread`, and `_last_call` has no lock  |
| D8  | **The single flight stays inside the process**                           | `deploy.sh` starts one uvicorn process, with no worker count            |
| D9  | **No change to `DAYS_VERSION` or `DAYS_CACHE_SECONDS`**                  | The day-file shape and the stale window stay the same                   |
| D10 | **`expand` and `venue` keep no cache**                                   | Both make no HTTP call, so a cache adds no value                        |
| D11 | **Drop the `wait_for_a_list` gate.** No route answers 502                | A 502 shows the page nothing; `test_source.py` asserts 502 today        |
| D12 | **The stream ends with `{"done": true, "failed": [...]}`**               | Send it after the loop, not in `finally`, which runs on a cancel        |
| D13 | **`Cache-Control: no-store, no-transform` on the stream**                | Headers go out first; `no-transform` stops an edge buffering it         |
| D15 | **A range must not span more than 7 days**                               | It bounds the flight keys, the lists and the day files                  |
| D16 | **The stream queue holds at most 64 lines**                              | A stalled reader pauses the pumps, not the memory                       |
| D17 | **Drop the Ticketmaster page cache**                                      | The day file is the parsed cache; a page adds a 6 h stale floor         |
| D18 | **A heartbeat line `{"total": 0}` each 25 s of silence**                 | nginx cuts a silent gap at 60 s, and the retry would repeat the cut     |
| D19 | **`pump` saves the day file once `expand` finishes**                     | A cut after that point leaves a warm file, so the retry skips the fetch |
| D20 | **The sentinel goes in with `put_nowait`, dropped on `QueueFull`**       | `await put` in `finally` blocks a cancelled pump on a full queue        |

Rejected options, one line each:

- A whole-response cache in nginx: it fights a streamed NDJSON body, and `deployvm` writes the nginx config.
- A cache by day for every source: the UI already asks for single days, so the key is already a day.
- A cache of the parsed Ticketmaster event in `data/events/`: the API answer is already parsed for free.
- A lock table for each range: a task per key is the flight, and it removes itself.
- Keeping the Ticketmaster page cache: the day file is the parsed cache (D17). The old D5, D6 and D14 went with it; the numbers stay.

## Scope

In:

- One shared in-flight task per key in `read_list`.
- The delay lock in `_get`.
- A 7-day range cap and a 64-line stream queue.
- The stream end: `failed`, `done`, `code`, `no-transform`, heartbeat.
- Three new test files: single flight, Ticketmaster, stream.

Out:

- A cache shared across processes or machines.
- Any change to the day-file shape.
- A prewarm of any source.
- A change to the geocoder or the venue cache.
- A full-page reload on a cut stream.

## Findings

Facts about today:

| Fact                                                                      | Where                              |
| ------------------------------------------------------------------------- | ---------------------------------- |
| `merged_lines` and `event_lines` use an unbounded queue                   | `gigmap/server.py`                 |
| `pump` saves after the venue lines, so a cut after `expand` saves nothing | `gigmap/server.py:pump`            |
| `finally: await queue.put(None)` blocks when the queue is full            | the `finally` of `pump`            |
| The merge loop body is `queue.get` plus `yield`; only a cancel raises     | `gigmap/server.py:merged_lines`    |
| The page hides the stream error once rows exist, and clears the counter   | `gigmap/index.html`                |
| `validate_dates` checks the form and the order, not the length            | `gigmap/server.py:validate_dates`  |
| `wait_for_a_list` answers 502 when every list read fails                  | `gigmap/server.py`                 |
| A day file key is the exact range pair, life 3 hours                      | `DataStores`, `DAYS_CACHE_SECONDS` |
| `_is_fresh` returns true on the mtime alone                               | `gigmap/stores.py:JsonCache`       |
| `JsonCache.save` is atomic: temp file, fsync, `os.replace`                | `gigmap/stores.py:JsonCache.save`  |
| A day file is saved only after a clean, uncancelled run                   | `gigmap/server.py:pump`            |
| A day file at 2 hours is still fresh, so `read_list` cannot refresh       | `_is_fresh`, `DAYS_CACHE_SECONDS`  |
| The UI builds today, tomorrow, and +2 to +6. Each range is one day        | `gigmap/index.html:buildRanges`    |
| The page cache key is a sha256 of the query, page number included         | `gigmap/ticketmaster.py:_get`      |
| `startDateTime` and `endDateTime` are inside the key                      | `gigmap/ticketmaster.py:_get`      |
| The page cache holds 6 hours, by mtime                                    | `CACHE_SECONDS`                    |
| One page was 189 KB; trimmed and minified it is 6 KB                      | `_trim_page`, `_get`               |
| The page cache path is a hardcoded `<repo>/data/ticketmaster`             | `CACHE_DIR`                        |
| A page write is not atomic, unlike `JsonCache.save`                       | `gigmap/ticketmaster.py`           |
| `clean()` also walks `ticketmaster/`                                      | `gigmap/stores.py:clean`           |
| The cleanup runs at startup, then each 24 hours                           | `cleanup_loop`                     |
| `_last_call` is a module global, with no lock, used in a thread           | `gigmap/ticketmaster.py:_get`      |
| HTTP 429 raises. There is no retry and no backoff                         | `gigmap/ticketmaster.py:_get`      |
| `CachedSource` has no `close()`, and no name attribute                    | `gigmap/stores.py:CachedSource`    |
| `CachedSource` keeps no page in memory, so a fetch is one read            | `gigmap/stores.py:CachedSource`    |
| One call gives up to 100 events. `expand` and `venue` make no call        | `PAGE_SIZE`, `TicketmasterSource`  |
| `serve` starts one uvicorn process                                        | `gigmap/server.py:serve`           |
| No test covers the Ticketmaster module                                    | `tests/`                           |
| A test points `GIGMAP_DATA_DIR` at a tmp dir                              | `tests/conftest.py`                |

Correction: the earlier finding "two users with different ranges never share
a result" is wrong for this app. `buildRanges` gives seven single-day ranges,
so the day key is already the natural key.

## Design

### Single flight (D2)

- `_INFLIGHT`: one module-level `dict[tuple[str, str, str], asyncio.Task]` in `gigmap/server.py`.
- `fetch_once(name, member, start_date, end_date)` puts the `candidates` task in the dict under its key.
- Every caller awaits the same task through `asyncio.shield`, so a disconnect of one waiter keeps the fetch alive.
- A `done` callback removes the key, so the dict holds in-flight keys only. It cannot grow and needs no sweep.
- `read_list` calls `fetch_once` only after `cached_gigs` misses.
- Two waiters both reach `pump` and both save. The save is atomic, so the second write repeats the same bytes.

### Memory bounds (D15, D16, D20)

- `validate_dates` rejects a range longer than 7 days with a 400. This bounds the flight keys, the member lists and the day files.
- `merged_lines` uses `asyncio.Queue(maxsize=64)`, so a stalled reader pauses the pumps instead of growing the queue.
- Each pump puts its `None` sentinel with `put_nowait` and drops it on `QueueFull`. A cancelled pump never blocks on a full queue.
- `event_lines` makes one task per gig; the 7-day cap bounds the count.

### No page cache (D17)

- The day file is the parse cache. A page cache adds a second TTL and a 6-hour stale floor for no gain.
- Drop `CACHE_DIR`, `_trim_page`, the minified write and the `clean()` page rule. Revert the built ones.
- `_get` keeps a page in memory for the span of one `candidates` call. `_INFLIGHT` stops a burst from repeating it.

### Delay (D7)

- One module-level `threading.Lock`. `_get` holds it across the sleep, the call, and the `_last_call` write.

### Cost

- Seven ranges, at most two pages each, and a fetch only on a cold day file. The 5000 call limit has room.
- The page cache saved about two thirds of these calls. That saving does not pay for a second TTL and a 6-hour stale floor.

### Stream failures (D11, D12)

- `event_stream` returns the stream at once. The `wait_for_a_list` gate goes, so the route never answers 502.
- Keep the `SourceError` handler for the other routes; the stream no longer raises it.
- `tests/test_source.py` `test_a_source_error_answers_502` asserts the old 502. It must expect 200 plus a `done` line.

### The end line (D12)

- When a member fails, `pump` sends `{"source": name, "failed": reason, "code": code}` and the `{"total": -k}` fix. The fix exists today.
- A code is `source_error` for a `SourceError`, or `internal` for any other error.
- `merged_lines` collects the failed lines and yields `{"done": true, "failed": [...]}` after the loop.
- No `except Exception` wraps the merge loop: its body is `queue.get` plus `yield`, and only a cancel raises there.
- Never yield inside a `finally` block. A yield during a cancel raises `RuntimeError: async generator ignored GeneratorExit`. The existing `finally` already handles a disconnect.

### A cut stream (D18, D19)

- A total failure is a log line plus a `done` line with every source in `failed`.
- The page tests `done` before `failed`, because both shapes carry the key. The `gig` handling guards against a `failed` line, which has no `gig` key.
- A `sawDone` flag tells a clean end from a cut one. The cut note shows even when rows exist, because today the page hides the error once one row renders.
- If no `done` line arrives, the page shows "the list was cut off" and a retry button. Disable the button while a run is active.
- nginx cuts a silent gap at 60 s (`proxy_read_timeout`). On HTTP/2 the cut is an `RST_STREAM`. `sawDone` is the only reliable test on both.
- The merge loop waits on the queue with a 25 s timeout and yields `{"total": 0}` on each timeout. The page adds a zero total with no change.
- `pump` saves the day file once `expand` finishes, before the venue lines. A cut after that point leaves a warm file, so the retry skips the fetch.
- A failed member still saves no day file.

## Changes

| Area                           | Change                                                                     |
| ------------------------------ | -------------------------------------------------------------------------- |
| `gigmap/server.py`             | Shared in-flight task per key, and the `_INFLIGHT` dict                    |
| `gigmap/server.py`             | `validate_dates` rejects a range longer than 7 days                        |
| `gigmap/server.py`             | `asyncio.Queue(maxsize=64)` in `merged_lines`, and a `put_nowait` sentinel |
| `gigmap/server.py`             | Drop `wait_for_a_list`; add `failed`, `done`, `code`; add `no-transform`   |
| `gigmap/server.py`             | A 25 s heartbeat line, and the `pump` save before the venue lines          |
| `gigmap/ticketmaster.py`       | Drop `CACHE_DIR`, `_trim_page`, and the page write                         |
| `gigmap/ticketmaster.py`       | The `threading.Lock` around the delay and the call                         |
| `gigmap/stores.py`             | Drop `TICKETMASTER_SUBDIR` and the `clean()` page rule                     |
| `gigmap/index.html`            | Read `failed` and `done`; a note per source; a cut-off retry; `sawDone`    |
| `tests/test_cleanup.py`        | Revert the `ticketmaster` case                                             |
| `tests/test_ticketmaster.py`   | New: the delay serialization, and no page file                             |
| `tests/test_single_flight.py`  | New: two cold requests make one `candidates` call                          |
| `tests/test_source.py`         | The 502 case becomes a 200 case with a `done` line                         |
| `tests/test_stream_failure.py` | New: the failure cases of the stream                                       |

## Tests

| Area          | Cases                                                            |
| ------------- | ---------------------------------------------------------------- |
| Delay         | Two threads call `_get`; the calls stay one `DELAY` apart        |
| Delay         | A 429 raises, and no page file is kept                           |
| Dates         | A range longer than 7 days answers 400                           |
| Single flight | Two cold calls make one `candidates` call and leave no key       |
| Single flight | 1000 cold ranges leave no key in `_INFLIGHT`                     |
| Single flight | Two calls on two ranges run at once                              |
| Stream        | Every member fails: status 200, and `done` lists each source     |
| Stream        | One member fails: its `failed` line, the other gigs, then `done` |
| Stream        | A clean run ends with `done` and an empty `failed`               |
| Stream        | A cut after `expand` leaves a warm day file                      |
| Stream        | A disconnect with a full queue ends the pumps; no task hangs     |
| Stream        | A silent gap sends a `{"total": 0}` heartbeat each 25 s          |
| Stream        | The merge of two members keeps both lists in order               |
| Stream        | A member fails after its total: the running total is corrected   |
| Stream        | The old 502 case now answers 200 and `done`                      |
| Stream        | A stalled reader leaves at most 64 lines queued                  |

## Safety and open questions

| Question                                     | Assumed default                     | Owner  |
| -------------------------------------------- | ----------------------------------- | ------ |
| Serve a stale day file while a refresh runs? | No. The flight makes the wait short | boscoh |

Risks:

- **Stale data:** up to 3 hours, the day-file TTL, plus the run time of a refresh.
- **Process-only single flight:** it stops two requests inside one process only. Use `fcntl.flock` on the day file before any second worker.
- **No page cache:** every cold day costs one or two calls at 1 s each, and the visitor waits for them. A warm day file has no wait.
- **No 5xx on a source failure (D11):** a 5xx log alert goes blind. Alert on the `source failed` log line instead.
- **An edge cache and D11:** a 200 with a `failed` line looks like a good 200 to nginx or Cloudflare. D13 keeps `no-store, no-transform`, so no cache keeps one. To send `public, s-maxage=N`, call `cached_gigs` for every member first, and send it only when all hit; a cached 200 with a `failed` entry is worse than no cache. Keep `no-store`: a warm stream costs 1-2 s.
- **Compression:** `application/x-ndjson` is outside the nginx default `gzip_types`.
  `Unverified:` the deployvm site has no `proxy_cache` and no ndjson `gzip_types`. gzip would buffer the lines and undo `X-Accel-Buffering: no`.

## Task breakdown

### Table A: tasks

| ID  | Wave | Outcome                                                               | Needs  | Risk                                          |
| --- | ---- | --------------------------------------------------------------------- | ------ | --------------------------------------------- |
| T2  | 1    | One fetch per cold range; a range is at most 7 days                   | -      | A leaked flight entry hangs the range         |
| T4  | 1    | The delay lock in `_get`, and no page cache                           | -      | One slow call stalls the other fetches        |
| T5  | 2    | A cut stream shows a note and a retry; a heartbeat stops the 60 s cut | T2, T4 | A 200 hides a source failure from a 5xx alert |

### Table B: launch

| ID  | Name       | Tier | Thinking | Worktree branch    | Commit |
| --- | ---------- | ---- | -------- | ------------------ | ------ |
| T2  | `t2-api-1` | mid  | medium   | `t2/single-flight` | yes    |
| T4  | `t4-api-1` | mid  | medium   | `t4/delay-lock`    | yes    |
| T5  | `t5-ui-1`  | mid  | medium   | `t5/stream-end`    | yes    |

### Table C: files and verify

| ID  | Allowed files                                                                                       | Verify                                                            |
| --- | --------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| T2  | `gigmap/server.py`, `tests/test_single_flight.py`, `tests/test_source.py`                           | `uv run pytest tests/test_single_flight.py tests/test_source.py`  |
| T4  | `gigmap/ticketmaster.py`, `gigmap/stores.py`, `tests/test_ticketmaster.py`, `tests/test_cleanup.py` | `uv run pytest tests/test_ticketmaster.py tests/test_cleanup.py`  |
| T5  | `gigmap/server.py`, `gigmap/index.html`, `tests/test_stream_failure.py`, `tests/test_source.py`     | `uv run pytest tests/test_stream_failure.py tests/test_source.py` |

### Launch steps

1. Run `git restore` on the uncommitted page cleanup and page trim. D17 deletes both.
2. Commit the spec, then start wave 1. T2 and T4 write no shared file, so they run in parallel worktrees.
3. Start wave 2 only after wave 1 is merged and the checks pass.

### Checks after each merge

1. Run `uv run pytest` in the parent checkout. All tests must pass.
2. Check that no task touched a file outside its allowed list.

### Merge order

1. Wave 1: merge T2 first, then T4. Run the checks after each merge.
2. Wave 2: merge T5 alone.
