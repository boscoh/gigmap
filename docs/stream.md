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

- `expand` owns its pool. artshub uses `PAGE_CONCURRENCY` (5). whatson uses
  `DETAIL_CONCURRENCY` (3).
- On a `SourceError`, `expand` logs it. artshub keeps the gig. whatson gives
  `[]`.
- A source with no `expand` is the identity. `candidates` is `gigs`, and
  `expand` gives `[gig]`. The server uses `getattr`.
- beat, artscentre and fringe need no `expand`.

## The caches

| Cache          | Content                          | Rule                                                                          |
| -------------- | -------------------------------- | ----------------------------------------------------------------------------- |
| `data/days/`   | the final `list[Gig]` of a range | `DAYS_CACHE_SECONDS` (3 hours). `DAYS_VERSION` is `v10` in `gigmap/stores.py` |
| `data/gigs/`   | the start time of each gig       | kept                                                                          |
| `data/venues/` | each venue with its coordinates  | saved only with coordinates                                                   |

- Warm: `CachedSource` loads the day file and skips `expand`.
- Cold: the member streams `candidates` and `expand`. Then it saves the gigs
  in candidate order.
- A cancel saves no day file. An `expand` error that is not a `SourceError`
  also saves none.
- A venue with no coordinates costs one lookup on each request.
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

- A gig line waits for its own detail read and its own venue read.
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
| `gig`   | the whole `Gig`, with its start time                           |
| `venue` | the whole `Venue` object, or null                              |
| `error` | the reason for a failed venue read. Absent when the read works |

The `gig` part holds a field `venue`, which is the venue name text. The
top-level `venue` is the address data.

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
| the first row                 | one detail read, about 0.2 to 1 second              |
| a warm venue cache            | no geocode, about 1 to 2 seconds                    |
| a cold venue cache, 13 venues | 13 x 1.1 seconds, about 14 seconds for the last row |
| a venue with no coordinates   | no cache entry, so 1.1 seconds on each request      |

Only the geocoder can change the pace of the last row. A cold artshub page
shows rows at the pace of its event pages.

## The failures

| The failure                    | The result                                     |
| ------------------------------ | ---------------------------------------------- |
| every candidate read fails     | 502 with the first `SourceError`               |
| one candidate read fails       | a log line. The member sends no line           |
| a member fails after its total | a log line, `{"total": -r}`, the member stops  |
| `expand` gives a `SourceError` | artshub keeps the gig. whatson drops the event |
| `expand` raises another error  | a log line, `{"total": -1}`, no day file save  |
| a detail read fails            | `gig.time` is null                             |
| a venue read fails             | `venue` is null, and `error` holds the text    |

A `SourceError` text goes to the page. Any other venue error becomes the
text "the venue lookup failed".
