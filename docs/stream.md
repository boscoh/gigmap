# The event stream

## The purpose

One stream serves one page load. The total leaves first. Then each gig
leaves the moment its own reads end. The page adds one row, one marker and
one count for each line.

A gig line leaves only when its time and its venue are ready. One gig never
shows a time with no place, or a place with no time.

## The read

`GET /api/<source>/events?start_date=<date>&end_date=<date>`

| Item     | Value                                              |
| -------- | -------------------------------------------------- |
| Answer   | NDJSON, `application/x-ndjson`                     |
| Header   | `X-Accel-Buffering: no`, so nginx passes each line |
| Bad date | 400, and the source costs no read                  |
| No gigs  | an empty stream. The page shows its empty state    |
| No list  | 502 with the first `SourceError`                   |

The route waits for a warm day file or a candidate list before it returns
the stream. The headers leave before line one, so a later fault cannot
become a 502. This wait keeps the 502.

`/api/<source>/events` always merges a set of members. A page of one source
has one member. `GROUPS` names the members of a group.

## The reads of a source

A source splits a list read into two steps.

| Step                                | Work                                            |
| ----------------------------------- | ----------------------------------------------- |
| `candidates(start_date, end_date)`  | the reads before any per-event read             |
| `expand(gig, start_date, end_date)` | one event read. It gives zero, one or many gigs |

- Each origin has one pool at module scope. Every request of the app shares
  it (D8). artshub uses `PAGE_CONCURRENCY` (5). whatson uses
  `DETAIL_CONCURRENCY` (3). beat uses `DETAIL_CONCURRENCY` (5). fringe uses
  `REQUEST_CONCURRENCY` (8). The server venue pool is 5, and the server holds
  no detail pool.
- On a `SourceError`, `expand` logs it. artshub and beat keep the gig, and
  beat leaves its time empty. whatson gives `[]`.
- Every source holds `candidates`, `expand` and `venue`, as `sources.GigSource`
  states. artshub, beat and whatson read the event in `expand`. Beat gives
  `[gig]` always, with the time of the detail page. artscentre and fringe
  give `[gig]` at once, because their list row holds the time.

## The caches

| Cache          | Content                                        | Rule                                                                                      |
| -------------- | ---------------------------------------------- | ----------------------------------------------------------------------------------------- |
| `data/days/`   | the final `list[Gig]` of a range               | `DAYS_CACHE_SECONDS` (3 hours). `DAYS_VERSION` in `gigmap/stores.py` names the shape      |
| `data/venues/` | each venue with its coordinates                | kept while `VENUES_VERSION` matches. Saved only with coordinates                          |
| `data/events/` | the range-free parse of one event page, by url | `EVENTS_CACHE_SECONDS` (7 days). `EVENTS_VERSION` names the parse. Bump it with a change  |

- `data/events/` serves the detail read of `expand` in beat (the time), artshub
  (the `Schedule`) and whatson (the `WhatsonEvent`, shared with whatsonart). A
  second range with the same events makes no detail read.
- Warm: `CachedSource` loads the day file and skips `expand`.
- Cold: the member streams `candidates` and `expand`. Then it saves the gigs
  in candidate order.
- A cancel saves no day file. An `expand` error that is not a `SourceError`
  also saves none.
- A venue with no coordinates costs one lookup for each `MISS_CACHE_SECONDS`
  (7 days), because the miss cache holds that answer.
- The geocoder holds its own rate limit: one request each `GEOCODE_SECONDS`
  (1.1). A venue with a cache entry skips it.
- An old venue file can hold `display_name`, `map_embed_url` and `map_url`.
  The field model drops them on load.

## The stream

Each member runs as one task. The route sends each queue item as it arrives.
A slow member holds no line of a fast member.

1. The first line is `{"total": n}`. `n` is the candidate count. It leaves
   before the first `expand` starts.
2. An `expand` that gives `k` gigs sends `{"total": k - 1}` when `k != 1`.
   A dropped event sends `{"total": -1}`.
3. One gig line leaves for each gig, in completion order.
4. A member that fails after its first total sends `{"total": -r}`. `r` is
   its running total minus its sent rows.
5. A warm day file sends `{"total": len(gigs)}` and no correction.

The sum of all `total` lines equals the row count at the end.

- A gig line waits for its own `expand` and its own venue read.
- The venue dict stays, so a venue of many gigs costs one read.
- Pools: `VENUE_CONCURRENCY` (5) for venue reads.
- A slow event holds no other event.
- A failed member is logged and left out.
- A client disconnect cancels every member, `expand`, detail and venue task.
- There is no dedupe. Two sites that list one show give two gigs.

## The line

```json
{"index": 3, "gig": {"artist": "Abby Hayes", "venue": "The Tote", "time": "7:30pm", ...}, "venue": {"venue_url": "https://...", "name": "The Tote", "latitude": -37.8, "longitude": 144.9}}
```

| Field   | Value                                                          |
| ------- | -------------------------------------------------------------- |
| `index` | the emit count of the member. The page ignores it              |
| `gig`   | the whole `Gig`, with its start time and its `hours` text      |
| `venue` | the whole `Venue` object, or null                              |
| `error` | the reason for a failed venue read. Absent when the read works |

The `gig` part holds a field `venue`, which is the venue name text. The
top-level `venue` is the address data.

`time` is the start time of a session, so the page sorts a row by it.
`hours` is the text that the table shows for a run, with the days that it
applies to, for example `Wed - Fri 11am - 5pm`. It is null for a source
that has no opening hours, and the table shows `time` then.

## The page

`loadEvents` reads the stream. `readNdjsonLines` and `parseLine` split it.

- One `AbortController` and one run id cover the stream.
- A `total` line adds to `total.value`, negative or positive.
- A gig line pushes `update.gig` into `gigs.value`, calls `addMarker` when
  `update.venue` is not null, and logs `update.error` when it is present.
- The first line turns `loading` false.
- `addMarker` keeps one marker for each `venue_url`.
- The summary shows `3 / 40 gigs` while the stream runs and `40 gigs` after.
- `venueInfo` and `venueErrors` serve a click on a row with no request.

## Latency

| The case                      | The time                                            |
| ----------------------------- | --------------------------------------------------- |
| the total                     | one candidate list read, or none on a warm day      |
| the first row                 | one `expand` read, or the venue read alone          |
| a warm venue cache            | no geocode, about 1 to 2 seconds                    |
| a cold venue cache, 13 venues | 13 x 1.1 seconds, about 14 seconds for the last row |
| a venue with no coordinates   | no cache entry, so 1.1 seconds on each request      |

Only the geocoder can change the pace of the last row. A cold artshub page
shows rows at the pace of its event pages.

## The failures

| The failure                    | The result                                                                         |
| ------------------------------ | ---------------------------------------------------------------------------------- |
| every candidate read fails     | 502 with the first `SourceError`                                                   |
| one candidate read fails       | a log line. The member sends no line                                               |
| a member fails after its total | a log line, `{"total": -r}`, the member stops                                      |
| `expand` gives a `SourceError` | artshub and beat keep the gig. beat leaves the time empty. whatson drops the event |
| `expand` raises another error  | a log line, `{"total": -1}`, no day file save                                      |
| an `expand` read fails         | `gig.time` is null                                                                 |
| a venue read fails             | `venue` is null, and `error` holds the text                                        |

A `SourceError` text goes to the page. Any other venue error becomes the
text "the venue lookup failed".
