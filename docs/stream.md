# The event stream

## The purpose

One stream serves one page load. Each line holds one whole gig: the row, the
start time, and the venue. A line leaves the server only when its time and
its venue are ready, so the page adds the time, the sort key, and the map
marker at the same moment. One gig never shows a time with no place, or a
place with no time.

## The reads before this change

| Read | The answer | The cost |
| --- | --- | --- |
| `/api/<source>/gigs` | the gig list | one day file |
| `/api/<source>/times` | `{"index", "time"}` for each gig | one detail read for each gig |
| `/api/<source>/venues` | `{"venue"}` or `{"venue_url", "error"}` for each distinct venue | one venue read and one geocode for each new venue |

The page joins the three answers. The times stream and the venue stream run
side by side, and they finish at different times. So a row can hold a time
and no marker, or a marker and no time. The page holds two run ids, two
`AbortController` objects, and two progress pairs for the same list.

## The new read

`GET /api/<source>/events?start_date=<date>&end_date=<date>`

- The answer is NDJSON, one line for each gig of the range.
- The line holds the whole gig, so the page needs no other read.
- The media type is `application/x-ndjson`.
- The header `X-Accel-Buffering: no` makes nginx pass each line at once.
- A bad date answers 400. A failed gig list read answers 502.
- Zero gigs answer an empty stream. The page shows its empty state when the
  stream closes.

One read serves the page. The page cannot count the rows before the stream
ends, and each row appears when its line arrives. On a warm venue cache that
is about as fast as a plain list read. On a cold cache the rows appear at
the geocode pace.

The route reads the gig list before it returns the stream. Starlette sends
the response headers before the first line, so a fault inside the stream
cannot become a 502. The read in the route keeps the 502, and the day file
on disk keeps the source cost at zero.

## The line

One line holds one gig, with all of its data:

```json
{"index": 3, "gig": {"artist": "Abby Hayes", "detail_url": "https://...", "venue": "The Tote", "venue_url": "https://...", "time": "7:30pm", "ticket": {...}, ...}, "venue": {"venue_url": "https://...", "name": "The Tote", "address": "...", "latitude": -37.8, "longitude": 144.9}}
```

| Field | The value |
| --- | --- |
| `index` | the position of the gig in the list of the source. Always present |
| `gig` | the whole `Gig`, with the start time that the detail read found |
| `venue` | the whole `Venue` object, or null |
| `error` | the reason for a failed venue read. Absent when the read works |

The `gig` part holds a field `venue`, which is the venue name text of the
gig list. The top-level `venue` is the address data. The two names are
different, and the page uses both.

One line for each gig, always. A failed read does not remove the line, and a
gig with no `venue_url` still gets a line.

## The server

`event_stream` in `gigmap/server.py` calls the source one time for the gig
list. Then it starts two sets of reads:

- One detail read for each gig that has no `time` and has a `detail_url`.
- One venue read for each distinct `venue_url`.

A venue of many gigs costs one read. A time that the gig list already holds
costs no read. The pool size is `DETAIL_CONCURRENCY` (5) for the detail reads
and `VENUE_CONCURRENCY` (5) for the venue reads. The geocoder holds its own
rate limit, at `GEOCODE_SECONDS` (1.1) for each new venue.

The reads run in parallel. Every detail read and every venue read starts
before the first line leaves. There is no barrier between the two sets, and
no barrier between the gigs. A slow gig never holds a fast gig.

Each gig has its own line, and the line leaves the stream at the moment the
two values of that gig exist. So a gig with a warm venue leaves as soon as
its detail page arrives, while a gig with a new venue waits one extra
1.1 seconds for the geocode. The page can show each gig as it arrives.

The order is the completion order, not the source order. The `index` field
gives the source position, and the page needs no other order: it sorts by
date and time, and a row with no time goes last.

A client disconnect cancels every read. The `finally` block cancels the tasks
and awaits them, so a stopped stream frees every read.

The algorithm:

```python
gigs = await source.gigs(start_date, end_date)   # in the route, so a fault is 502

times = {}    # index -> Future[str | None]
venues = {}   # venue_url -> Future[Venue | Exception]

for index, gig in enumerate(gigs):
    times[index] = start_detail_read(gig)      # no read when gig.time is set
    if gig.venue_url:
        venues.setdefault(gig.venue_url, start_venue_read(gig.venue_url))

async def one(index: int, gig: Gig) -> str:
    time_text = await times[index]
    venue, error = await venues.get(gig.venue_url, DONE_NONE)
    return the_line(index, gig, time_text, venue, error)
for done in asyncio.as_completed([one(i, gig) for i, gig in enumerate(gigs)]):
    yield await done
```

The time read function is the present `gig_time`. The error rules are the
present `venue_failure` rules, moved onto the line.

`asyncio.as_completed` gives one line for each gig, in the order that the
gigs finish. A gig never waits for another gig. One gig waits for its own
time read and its own venue read, and nothing else.

One serialization stays, and it comes from the origin, not from this design:
`geocode.geocode` holds a lock and one request each 1.1 seconds, because the
Nominatim policy allows no more. Every venue with a cache entry skips that
lock, so a warm day has no serialization at all.

## The client

`loadTimes` and `mapAllVenues` become one function, `loadEvents`. The client
keeps `readNdjsonLines` and `parseLine`. `fetchGigs` goes: one request
serves one load.

- One `AbortController` and one run id cover the stream. The run id is the
  only guard, because `load` calls `loadEvents` and nothing else.
- Each line holds the whole gig, so the page pushes `update.gig` into
  `gigs.value`. The page needs no index and no list read.
- The first line turns `loading` false, so the table shows each row as it
  arrives. The empty state shows when the stream closes with no line.
- For each line: call `addMarker(update.venue)` when the venue is not null,
  log `update.error` when it is present, call `nextPaint()`, and fit the map
  at 5 moves a second.
- `addMarker` holds one map from `venue_url` to the marker, so five lines of
  one venue add one marker. `markerCount` uses the size of that map.
- The summary counts what it has: `gigs.value.length` gigs and
  `markerCount` venues. The page knows no total before the stream ends, so
  the `n/m` form goes.
- `venueInfo` still holds each venue, so a click on a row costs no request.
- `venueErrors` holds the reason for a failed venue read, so the click shows the
  reason and makes no request.

## Latency

| The case | The time |
| --- | --- |
| the first row | one detail read, about 0.2 to 1 second |
| a warm venue cache | no geocode, so the time reads set the pace, about 1 to 2 seconds |
| a cold venue cache, 13 venues | 13 x 1.1 seconds, about 14 seconds for the last row |
| a venue with no coordinates | no cache entry, so 1.1 seconds on each request |

The geocode sets the pace for the last row, and only the geocoder can change
that. The reads still overlap, so each line leaves as soon as its own venue
resolves. A row whose venue is warm, and a row whose venue is new, both
appear at their own moment, and no row waits for the last one. The table is
empty until the first row, which costs one detail read.

The present page shows the times in a fast burst, then the markers. This
change puts the time and the marker of one gig in the same moment, and pays
with a slower spread of rows on a cold cache.

## The failures

| The failure | The result |
| --- | --- |
| a detail read fails | `gig.time` is null, and `venue` is the usual object |
| a venue read fails | `venue` is null, and `error` holds the text. The page stores the error, and a click shows it with no request |
| the gig list read fails | 502, and no stream |

A `SourceError` text goes to the page. Any other error becomes the text "the
venue lookup failed". This rule is the present rule.

## The cache

The caches do not change:

- `data/days/` holds the gig list with no time. `DAYS_VERSION` stays `v7`.
- `data/gigs/` holds the start time of each gig.
- `data/venues/` holds each venue with its coordinates.

The venue rule stays: the app saves a venue only when it has coordinates. A
venue with no coordinates costs one lookup on each request.

An old venue file still holds `display_name`, `map_embed_url`, and `map_url`.
The field model drops them on load, so the file works and the stream stays
lean. The app never rewrites a venue that it holds, so the extra bytes stay
on disk until someone removes the file.

## The changes

| File | The change |
| --- | --- |
| `gigmap/server.py` | add `event_stream`, `event_lines`, `event_line`. Remove `gig_list`, `gig_times`, `venue_stream`, `venue_answer`, `venue_failure`, and the `/venue` route |
| `gigmap/sources.py`, `gigmap/geocode.py` | remove `Venue.display_name`, `map_embed_url`, `map_url`, and `geocode.map_urls`. The page reads none of them |
| `gigmap/index.html` | replace `loadTimes` and `mapAllVenues` with `loadEvents`. Remove `stopTimes`, `stopVenues`, `fetchGigs`, `fetchVenue`, the `/venue` fallback, and `loadRun` |
| `tests/test_source.py` | add the tests below. Remove the tests of the two old routes |

`/api/<source>/gigs` goes too. The page never calls it, and the event stream
already reads the day cache. The tests use `/events` for the range checks.

`/api/<source>/venue` goes too. It was a retry for a failed venue read, and
a retry is not a goal. The page stores the error, so a click can show it.

A deleted route answers 404. The server reads `index.html` on each request, so
a reload gets the new page. A browser that holds the old page shows a 404 for
`/times` or `/venue` until a reload.

## The tests

- One line for each gig, and each line holds the whole gig.
- One venue read for a venue of many gigs.
- No detail read when the gig list holds the time.
- One line for a failed detail read, with a null `time`.
- One line for a failed venue read, with the error text.
- The line holds the whole `gig`, the whole `venue`, and the resolved time.
- The header `X-Accel-Buffering: no`.
- A bad date answers 400, and the source costs no read.
- A line leaves the stream only after the venue read of its gig finishes.
- A slow venue does not hold the line of a gig with a fast venue. The test
  blocks one venue read, then reads the lines of the other gigs first.
- The removed reads answer 404: `/gigs`, `/times`, `/venue`, and `/venues`.

## The alternatives

- **A list line first.** The stream could send the whole list as its first
  line, then thin gig lines. The page would know the total at once and draw
  every row before the first geocode. The cost is a second line shape, and
  about the same bytes: on the local 17-gig day, option 2 is 12.2 KB and the
  list-line form is 12.3 KB, because the venue object dominates both.
- **A venue line and a gig line.** The stream could send a venue line at
  once and a gig line later. That is the old behaviour in one connection,
  and it does not meet the goal.

## Later work

- A short negative cache for a venue with no coordinates. Then a cold start
  does not pay 1.1 seconds for the same broken venue on each request.
- A `date` field in the gig file, so the cleanup rule is exact. See
  `gigmap/stores.py`.
