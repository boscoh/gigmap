# The naming schema

This document records the naming rules of this codebase, layer by layer, and
the places where a name still lies. It covers the Python identifiers, the
files on disk, and the HTTP API. `docs/domain.md` is about DNS names, not the
model, so it is not the reference for these words.

## The vocabulary

| Word | Means | Example |
| --- | --- | --- |
| gig | one row that the page draws: one performance, or one day of a run | `sources.Gig` |
| source | one module that reads one site and implements `GigSource` | `gigmap/beat.py` |
| member | one source inside a group page | `SOURCE_INFO["gigs"].parts` |
| venue | the place of a gig, with its address and its coordinates | `sources.Venue` |
| day | one date, or the day file of a range | `data/days/`, `Hours.days` |
| run | a window of dates with opening hours | `sources.Schedule` |
| page parse | the range-free value that the app caches from one detail page | `beat.GigFacts` |
| miss | a geocode answer that found no place | `stores.py` `MISS_PART` |

A gig is not the event of the source. `fringe.py` states the rule: one gig is
one performance, and every performance of an event carries the same
`detail_url`. `artscentre.is_gig` states it in code: a row of the source is
not a gig until a check passes.

## Python

| Kind | Rule | Where |
| --- | --- | --- |
| module | one site, lowercase, no `_` | `artscentre.py`, `whatson.py` |
| constant | `UPPER_SNAKE`, ending in its unit or its kind | `VENUE_CONCURRENCY`, `RETRY_DELAY_SECONDS`, `DATE_RE`, `DAYS_VERSION` |
| type | a noun of the domain, one type for one concept | `Gig`, `Venue`, `Schedule`, `Ticket` |
| protocol | the interface takes the `Source` word, the class takes its own | `GigSource`, `CachedGigSource`, `CachedSource` |
| function | starts with its verb: `parse_*`, `fit_*`, `extract_*`, `read_*` | `parse_gig_detail`, `fit_dates`, `extract_schedule` |
| noun helper | a projection returns a noun phrase | `events_of`, `ticket_of`, `days_of_range` |
| module state | `_UPPER` for a container, `_lower` for a scalar a method rebinds with `global` | `_INFLIGHT`, `_current_target` |
| abbreviation | `url`, `html`, `kv`, `id`, `cf` | `url_key`, `log_kv` |

A function that reads the network starts with `read_` or `_html`. A function
that only parses the text it was handed starts with `parse_`.

## The disk

Every cache file is `<source>_<vN>_<key>.json`, with a lowercase source and a
lowercase version. The key keeps the case and the encoding of the source: a
site slug, a vendor id, or a quoted title and address.

| Directory | Holds | Written by |
| --- | --- | --- |
| `data/days/gigmap_<source>_<vN>_<start>_<end>.json` | the final `list[Gig]` of a range | `DataStores.days` |
| `data/events/<source>_<vN>_<key>.json` | a page parse: `Schedule`, `GigFacts`, `WhatsonEvent` | `stores.event_cache` |
| `data/venues/<source>_<vN>_<key>.json` | one venue with its coordinates | `DataStores.venues` |
| `data/venues/<source>_miss_<vN>_<key>.json` | a geocode answer with no place | `DataStores.misses` |

One directory holds one record kind. The cleanup uses the same grammar as the
writer, so a name that the writer can make, the cleanup can read. A temporary
file of a save that did not finish is removed at the next cleanup
(`stores.remove_temps`).

## The API

The path names the page that the reader asked for, and the body names the row.
The stream is `/api/<page>/events`, and every record in it holds the key
`gig`.

| Key | Type | When |
| --- | --- | --- |
| `gig` | object, the whole `Gig` | each row line |
| `venue` | object or `null` | each row line |
| `error` | string | only when the venue read failed |
| `total` | int | the first count, each correction, and the heartbeat `0` |
| `source` | string, the member | a failed line, and each `done.failed` entry |
| `reason` | string | a failed line, and each `done.failed` entry |
| `done` | `true` | the last line |
| `failed` | `list` of `{source, reason}` | the last line only |

A key has one type. `failed` was a string on the failed line and a list on the
done line, so the failed line now carries `reason` and `failed` stays the done
list. An internal error reads `internal error`, and the real text stays in the
log.

Headers: `X-Accel-Buffering: no` and `Cache-Control: no-store, no-transform`
on the stream, and `Cache-Control: no-cache` on a page. The media type is
`application/x-ndjson`. The app sends no `x-gig-total` header.

Query parameters are `start_date` and `end_date`, as `YYYY-MM-DD`.

## Where `event` is allowed

Allowed:

* the page or the run of the source, and the cache of its parse:
  `WhatsonEvent`, `GigFacts`, `fit_event`, `data/events`, `EVENTS`,
  `event_cache`
* the words of the site itself, in a quote: `whatsOnEvent`, `"Free Events"`,
  `Event Starts`

Not allowed: the response layer. `Gig`, `GigSource`, `candidates`, `expand`
and the key `gig` all name the row, so `/api/<page>/events` is the last place
where `event` names something that the client receives.

## Open items

| Item | Why it is not done |
| --- | --- |
| `EVENTS` → a page-parse name, and `data/events` → `data/pages` | about 40 sites, and a move of the directory with the files it holds |
| `SourceInfo.name` | four referents: the domain, the plural label, the short key, and the prose of a group. A change reaches the page JSON. The group loop in `server.py` overrides `parts[].name` with the short key, which is why the value differs from `SOURCE_INFO[key].name` |
| `server.event_line` → `gig_line` | the function emits a `Gig`. It is a rename of 6 sites |
| `eventSource` in the page | it is an `AbortController`, not a browser `EventSource` |

Left alone on purpose: `Gig` and `GigSource`, the `parse_*` family, `miss`,
`noun`, `hours` against `time`, and `_INFLIGHT`.
